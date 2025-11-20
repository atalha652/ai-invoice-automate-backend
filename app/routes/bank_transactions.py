"""
Bank Transaction API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from datetime import datetime
from pymongo import MongoClient
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
    format: BankStatementFormat = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Import bank statement file (CSV, CAMT.053, or MT940)

    Formats supported:
    - csv: Standard CSV format
    - camt053: ISO 20022 XML format
    - mt940: SWIFT MT940 format
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
