"""
Bank Transaction API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
import os
import certifi
from dotenv import load_dotenv

from app.models.bank_transactions import (
    BankAccount,
    BankAccountCreate,
    BankStatement,
    BankTransaction,
    TransactionFilter,
    BankStatementFormat,
    BankTransactionUpdate,
    TransactionsToLedgerRequest,
)
from app.repos.bank_repo import BankRepository
from app.repos.accounting_repo import AccountingRepository
from app.services.bank_parser import BankStatementParser
from app.services.payment_matching_service import PaymentMatchingService
from app.routes.auth import get_current_user

# Load environment
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

# Database connection
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]

router = APIRouter(tags=["Bank Transactions"])


# ===== Bank Account Management =====

@router.post("/bank/accounts", response_model=dict)
async def create_bank_account(
    account: BankAccountCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new bank account"""
    bank_repo = BankRepository(db)

    # Convert ObjectId to string
    org_id = current_user.get("organization_id") or current_user["_id"]
    if not isinstance(org_id, str):
        org_id = str(org_id)

    bank_account = BankAccount(
        organization_id=org_id,
        **account.dict(),
    )

    account_id = bank_repo.create_bank_account(bank_account)

    return {"id": account_id, "message": "Bank account created successfully"}


@router.get("/bank/accounts", response_model=List[dict])
async def list_bank_accounts(
    current_user: dict = Depends(get_current_user),
):
    """Get all bank accounts for organization"""
    bank_repo = BankRepository(db)
    organization_id = current_user.get("organization_id") or current_user["_id"]
    organization_id = str(organization_id) if not isinstance(organization_id, str) else organization_id
    accounts = bank_repo.get_bank_accounts_by_org(organization_id)
    return [acc.dict(by_alias=True) for acc in accounts]


@router.get("/bank/accounts/{account_id}", response_model=dict)
async def get_bank_account(
    account_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get bank account details"""
    bank_repo = BankRepository(db)
    account = bank_repo.get_bank_account(account_id)

    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    # Verify ownership
    user_org_id = current_user.get("organization_id") or current_user["_id"]
    user_org_id = str(user_org_id) if not isinstance(user_org_id, str) else user_org_id
    if account.organization_id != user_org_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return account.dict(by_alias=True)


# ===== Statement Import =====

@router.post("/bank/import", response_model=dict)
async def import_bank_statement(
    file: UploadFile = File(...),
    bank_account_id: str = Form(...),
    format: Optional[BankStatementFormat] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Import bank statement file (CSV, CAMT.053, MT940, or PDF)

    Formats supported:
    - csv: Standard CSV format
    - camt053: ISO 20022 XML format
    - mt940: SWIFT MT940 format
    - pdf: Bank statement PDFs with tabular transaction layouts

    If the format is not provided (or is incorrect), the backend will attempt
    to auto-detect it from the file contents before parsing.
    """
    try:
        bank_repo = BankRepository(db)
        accounting_repo = AccountingRepository()

        # Verify bank account ownership
        organization_id = current_user.get("organization_id") or current_user["_id"]
        organization_id = str(organization_id) if not isinstance(organization_id, str) else organization_id
        user_id = str(current_user["_id"]) if not isinstance(current_user["_id"], str) else current_user["_id"]
        bank_account = bank_repo.get_bank_account(bank_account_id)

        if not bank_account or bank_account.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Invalid bank account")

        # Read file content
        file_content = await file.read()

        # Parse statement
        parser = BankStatementParser(organization_id, bank_account_id)
        statement, transactions = parser.parse_file(
            file_content=file_content,
            file_name=file.filename,
            format_type=format,
            imported_by=user_id,
        )

        if not transactions:
            raise HTTPException(
                status_code=400,
                detail="No transactions were parsed from the provided statement file",
            )

        # Check for duplicate import
        existing = bank_repo.get_statement_by_hash(statement.file_hash)
        if existing:
            raise HTTPException(
                status_code=400,
                detail="This statement has already been imported"
            )

        # Save statement
        statement_id = bank_repo.create_bank_statement(statement)

        # Save transactions
        for trans in transactions:
            trans.statement_id = statement_id

        transaction_ids = bank_repo.create_transactions_bulk(transactions)

        # Update bank account balance
        if statement.closing_balance:
            bank_repo.update_bank_account_balance(
                bank_account_id, statement.closing_balance
            )

        # Auto-match transactions
        matching_service = PaymentMatchingService(bank_repo, accounting_repo)
        match_stats = matching_service.match_all_unmatched_transactions(
            organization_id
        )

        return {
            "statement_id": statement_id,
            "transactions_imported": len(transaction_ids),
            "from_date": statement.from_date.isoformat(),
            "to_date": statement.to_date.isoformat(),
            "total_debits": statement.total_debits,
            "total_credits": statement.total_credits,
            "matching_stats": match_stats,
            "message": "Bank statement imported successfully",
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


# ===== Transaction Management =====

@router.get("/bank/transactions", response_model=List[dict])
async def list_transactions(
    bank_account_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    status: Optional[str] = None,
    match_status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """Get bank transactions with filters"""
    bank_repo = BankRepository(db)

    # Parse dates if provided
    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None

    filters = TransactionFilter(
        bank_account_id=bank_account_id,
        from_date=from_dt,
        to_date=to_dt,
        status=status,
        match_status=match_status,
    )

    transactions = bank_repo.query_transactions(filters, skip, limit)
    return [t.dict(by_alias=True) for t in transactions]


# ===== Convert Transactions to Ledger (must come before parameterized routes) =====

@router.post("/bank/transactions/to-ledger", response_model=dict, status_code=200)
async def convert_transactions_to_ledger(
    request: TransactionsToLedgerRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Convert bank transactions to ledger entries

    This creates journal entries and posts them to the ledger, similar to voucher OCR processing.
    Each bank transaction becomes a double-entry journal entry.

    Request body:
    {
        "transaction_ids": ["id1", "id2", ...]
    }
    """
    try:
        bank_repo = BankRepository(db)
        accounting_repo = AccountingRepository()
        user_id = str(current_user["_id"]) if not isinstance(current_user["_id"], str) else current_user["_id"]
        organization_id = current_user.get("organization_id") or current_user["_id"]
        organization_id = str(organization_id) if not isinstance(organization_id, str) else organization_id

        transaction_ids = request.transaction_ids

        if not transaction_ids:
            raise HTTPException(status_code=400, detail="No transaction IDs provided")

        results = {
            "total_transactions": len(transaction_ids),
            "successful": 0,
            "failed": 0,
            "ledger_entries_created": [],
            "errors": []
        }

        for trans_id in transaction_ids:
            try:
                # Get transaction
                transaction = bank_repo.get_transaction(trans_id)

                if not transaction:
                    results["errors"].append({
                        "transaction_id": trans_id,
                        "error": "Transaction not found"
                    })
                    results["failed"] += 1
                    continue

                # Verify ownership
                if transaction.organization_id != organization_id:
                    results["errors"].append({
                        "transaction_id": trans_id,
                        "error": "Access denied"
                    })
                    results["failed"] += 1
                    continue

                # Skip if already has ledger entry
                if transaction.ledger_entry_id:
                    results["errors"].append({
                        "transaction_id": trans_id,
                        "error": "Already has ledger entry"
                    })
                    results["failed"] += 1
                    continue

                # Create journal entries for this transaction
                from app.models.accounting import JournalEntryCreate, JournalEntryType

                # Get or create "Bank" journal
                bank_journal = accounting_repo.db["journals"].find_one({
                    "organization_id": organization_id,
                    "journal_type": "bank"
                })

                if not bank_journal:
                    # Create default bank journal
                    bank_journal_data = {
                        "organization_id": organization_id,
                        "journal_code": "BANK",
                        "journal_name": "Bank Transactions",
                        "journal_type": "bank",
                        "description": "Bank account transactions",
                        "is_active": True,
                        "created_at": datetime.utcnow()
                    }
                    result = accounting_repo.db["journals"].insert_one(bank_journal_data)
                    bank_journal = accounting_repo.db["journals"].find_one({"_id": result.inserted_id})

                journal_id = str(bank_journal["_id"])

                # Determine accounts based on transaction type
                # For credit (money in): Debit Bank Account, Credit Income/Revenue
                # For debit (money out): Debit Expense, Credit Bank Account

                # Get bank account from chart of accounts
                bank_account_obj = bank_repo.get_bank_account(transaction.bank_account_id)
                bank_account_code = None

                if bank_account_obj:
                    # Try to find matching account in chart of accounts by account number/IBAN
                    chart_account = accounting_repo.db["accounts"].find_one({
                        "organization_id": organization_id,
                        "$or": [
                            {"account_code": {"$regex": bank_account_obj.account_number, "$options": "i"}},
                            {"account_name": {"$regex": bank_account_obj.account_name, "$options": "i"}}
                        ]
                    })

                    if chart_account:
                        bank_account_code = chart_account["account_code"]
                    else:
                        # Create a new bank account in chart of accounts
                        bank_account_code = f"1020-{bank_account_obj.account_number[-4:]}"  # 1020 = Bank accounts

                        new_account = {
                            "organization_id": organization_id,
                            "account_code": bank_account_code,
                            "account_name": f"Bank - {bank_account_obj.account_name}",
                            "account_type": "ASSET",
                            "account_subtype": "CURRENT_ASSET",
                            "is_active": True,
                            "current_balance": 0.0,
                            "currency": transaction.currency,
                            "created_at": datetime.utcnow()
                        }
                        accounting_repo.db["accounts"].insert_one(new_account)

                # Prepare journal entry data
                if transaction.transaction_type == "credit":
                    # Money coming in - Debit Bank, Credit Revenue
                    # Use counterparty name or default description
                    description = transaction.description or f"Bank transfer from {transaction.counterparty_name or 'Unknown'}"

                    # Default revenue account
                    revenue_account_code = "4000"  # Revenue account

                    entries = [
                        {
                            "account_code": bank_account_code,
                            "entry_type": "DEBIT",
                            "amount": transaction.amount,
                            "description": description,
                            "reference": transaction.reference or transaction.transaction_id
                        },
                        {
                            "account_code": revenue_account_code,
                            "entry_type": "CREDIT",
                            "amount": transaction.amount,
                            "description": description,
                            "reference": transaction.reference or transaction.transaction_id
                        }
                    ]
                else:
                    # Money going out - Debit Expense, Credit Bank
                    description = transaction.description or f"Bank transfer to {transaction.counterparty_name or 'Unknown'}"

                    # Default expense account
                    expense_account_code = "5000"  # Expense account

                    entries = [
                        {
                            "account_code": expense_account_code,
                            "entry_type": "DEBIT",
                            "amount": transaction.amount,
                            "description": description,
                            "reference": transaction.reference or transaction.transaction_id
                        },
                        {
                            "account_code": bank_account_code,
                            "entry_type": "CREDIT",
                            "amount": transaction.amount,
                            "description": description,
                            "reference": transaction.reference or transaction.transaction_id
                        }
                    ]

                # Create journal entry record
                journal_entry_doc = {
                    "organization_id": organization_id,
                    "journal_id": journal_id,
                    "transaction_date": transaction.transaction_date,
                    "description": description,
                    "reference": transaction.reference or f"BANK-{trans_id[:8]}",
                    "entries": entries,
                    "total_debit": transaction.amount,
                    "total_credit": transaction.amount,
                    "status": "posted",
                    "created_by": user_id,
                    "created_at": datetime.utcnow(),
                    "posted_at": datetime.utcnow(),
                    "posted_by": user_id,
                    "source": "bank_import",
                    "source_id": trans_id
                }

                # Insert journal entry
                je_result = accounting_repo.db["journal_entries"].insert_one(journal_entry_doc)
                journal_entry_id = str(je_result.inserted_id)

                # Create ledger entries
                for entry in entries:
                    # Get account details
                    account = accounting_repo.db["accounts"].find_one({
                        "organization_id": organization_id,
                        "account_code": entry["account_code"]
                    })

                    if account:
                        # Calculate running balance
                        last_ledger = accounting_repo.db["ledger_entries"].find_one(
                            {"account_id": str(account["_id"])},
                            sort=[("created_at", -1)]
                        )

                        running_balance = last_ledger["running_balance"] if last_ledger else account.get("current_balance", 0.0)

                        if entry["entry_type"] == "DEBIT":
                            running_balance += entry["amount"]
                        else:
                            running_balance -= entry["amount"]

                        # Create ledger entry
                        ledger_entry = {
                            "organization_id": organization_id,
                            "account_id": str(account["_id"]),
                            "account_code": entry["account_code"],
                            "account_name": account["account_name"],
                            "entry_type": entry["entry_type"],
                            "amount": entry["amount"],
                            "running_balance": running_balance,
                            "transaction_date": transaction.transaction_date,
                            "description": entry["description"],
                            "reference": entry["reference"],
                            "journal_entry_id": journal_entry_id,
                            "posted_at": datetime.utcnow(),
                            "posted_by": user_id,
                            "created_at": datetime.utcnow()
                        }

                        accounting_repo.db["ledger_entries"].insert_one(ledger_entry)

                        # Update account balance
                        accounting_repo.db["accounts"].update_one(
                            {"_id": account["_id"]},
                            {"$set": {"current_balance": running_balance}}
                        )

                # Update transaction with ledger entry reference
                bank_repo.db["bank_transactions"].update_one(
                    {"_id": ObjectId(trans_id)},
                    {"$set": {
                        "ledger_entry_id": journal_entry_id,
                        "status": "reconciled",
                        "updated_at": datetime.utcnow()
                    }}
                )

                results["successful"] += 1
                results["ledger_entries_created"].append({
                    "transaction_id": trans_id,
                    "journal_entry_id": journal_entry_id,
                    "amount": transaction.amount,
                    "type": transaction.transaction_type
                })

            except Exception as e:
                results["errors"].append({
                    "transaction_id": trans_id,
                    "error": str(e)
                })
                results["failed"] += 1

        return {
            "message": "Transaction processing completed",
            "results": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error converting transactions to ledger: {str(e)}")


@router.get("/bank/transactions/{transaction_id}", response_model=dict)
async def get_transaction(
    transaction_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get transaction details"""
    bank_repo = BankRepository(db)
    transaction = bank_repo.get_transaction(transaction_id)

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return transaction.dict(by_alias=True)


@router.patch("/bank/transactions/{transaction_id}", response_model=dict)
async def update_transaction(
    transaction_id: str,
    update: BankTransactionUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update transaction"""
    bank_repo = BankRepository(db)
    success = bank_repo.update_transaction_status(
        transaction_id,
        update.status,
        update.match_status,
    )

    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {"message": "Transaction updated successfully"}


# ===== Payment Matching =====

@router.post("/bank/transactions/{transaction_id}/match", response_model=dict)
async def manual_match_transaction(
    transaction_id: str,
    invoice_id: str,
    voucher_id: Optional[str] = None,
    notes: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Manually match transaction to invoice"""
    bank_repo = BankRepository(db)
    accounting_repo = AccountingRepository()
    matching_service = PaymentMatchingService(bank_repo, accounting_repo)

    user_id = str(current_user["_id"]) if not isinstance(current_user["_id"], str) else current_user["_id"]

    match = matching_service.manual_match(
        transaction_id=transaction_id,
        invoice_id=invoice_id,
        voucher_id=voucher_id,
        user_id=user_id,
        notes=notes,
    )

    return {
        "match_id": str(match.id),
        "message": "Transaction matched successfully",
    }


@router.post("/bank/transactions/{transaction_id}/unmatch", response_model=dict)
async def unmatch_transaction(
    transaction_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Unmatch transaction from invoice"""
    bank_repo = BankRepository(db)
    accounting_repo = AccountingRepository()
    matching_service = PaymentMatchingService(bank_repo, accounting_repo)

    success = matching_service.unmatch_transaction(transaction_id)

    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {"message": "Transaction unmatched successfully"}


@router.post("/bank/match-all", response_model=dict)
async def auto_match_all_transactions(
    current_user: dict = Depends(get_current_user),
):
    """Run automatic matching for all unmatched transactions"""
    organization_id = current_user.get("organization_id") or current_user["_id"]
    organization_id = str(organization_id) if not isinstance(organization_id, str) else organization_id

    bank_repo = BankRepository(db)
    accounting_repo = AccountingRepository()
    matching_service = PaymentMatchingService(bank_repo, accounting_repo)
    stats = matching_service.match_all_unmatched_transactions(organization_id)

    return {
        "stats": stats,
        "message": "Automatic matching completed",
    }
