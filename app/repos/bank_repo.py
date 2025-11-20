"""
Bank Transaction Repository
MongoDB data access layer for bank accounts, statements, and transactions
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from bson import ObjectId
import logging

from app.models.bank_transactions import (
    BankAccount,
    BankStatement,
    BankTransaction,
    PaymentInvoiceMatch,
    ReconciliationReport,
    TransactionFilter,
    TransactionStatus,
    MatchStatus,
)

logger = logging.getLogger(__name__)


class BankRepository:
    """Repository for bank-related data operations"""

    def __init__(self, db: Any):
        self.db = db
        self.bank_accounts: Collection = db["bank_accounts"]
        self.bank_statements: Collection = db["bank_statements"]
        self.bank_transactions: Collection = db["bank_transactions"]
        self.payment_matches: Collection = db["payment_invoice_matches"]
        self.reconciliation_reports: Collection = db["reconciliation_reports"]

        # Create indexes
        self._create_indexes()

    def _create_indexes(self):
        """Create database indexes for performance"""
        try:
            # Bank accounts
            self.bank_accounts.create_index(
                [("organization_id", ASCENDING), ("account_number", ASCENDING)],
                unique=True,
            )
            self.bank_accounts.create_index([("organization_id", ASCENDING)])
            self.bank_accounts.create_index([("is_active", ASCENDING)])

            # Bank statements
            self.bank_statements.create_index([("organization_id", ASCENDING)])
            self.bank_statements.create_index([("bank_account_id", ASCENDING)])
            self.bank_statements.create_index([("file_hash", ASCENDING)], unique=True)
            self.bank_statements.create_index(
                [("from_date", ASCENDING), ("to_date", ASCENDING)]
            )

            # Bank transactions
            self.bank_transactions.create_index([("organization_id", ASCENDING)])
            self.bank_transactions.create_index([("bank_account_id", ASCENDING)])
            self.bank_transactions.create_index([("statement_id", ASCENDING)])
            self.bank_transactions.create_index([("transaction_date", DESCENDING)])
            self.bank_transactions.create_index([("status", ASCENDING)])
            self.bank_transactions.create_index([("match_status", ASCENDING)])
            self.bank_transactions.create_index([("transaction_type", ASCENDING)])

            # Payment matches
            self.payment_matches.create_index([("organization_id", ASCENDING)])
            self.payment_matches.create_index([("transaction_id", ASCENDING)])
            self.payment_matches.create_index([("invoice_id", ASCENDING)])
            self.payment_matches.create_index([("match_status", ASCENDING)])

            logger.info("Bank repository indexes created successfully")
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")

    # ===== Bank Account Operations =====

    def create_bank_account(self, account: BankAccount) -> str:
        """Create a new bank account"""
        account_dict = account.dict(by_alias=True, exclude={"id"})
        result = self.bank_accounts.insert_one(account_dict)
        return str(result.inserted_id)

    def get_bank_account(self, account_id: str) -> Optional[BankAccount]:
        """Get bank account by ID"""
        doc = self.bank_accounts.find_one({"_id": ObjectId(account_id)})
        return BankAccount(**doc) if doc else None

    def get_bank_accounts_by_org(self, organization_id: str) -> List[BankAccount]:
        """Get all bank accounts for an organization"""
        docs = self.bank_accounts.find({"organization_id": organization_id})
        return [BankAccount(**doc) for doc in docs]

    def update_bank_account_balance(
        self, account_id: str, new_balance: float
    ) -> bool:
        """Update bank account current balance"""
        result = self.bank_accounts.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {"current_balance": new_balance, "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    def deactivate_bank_account(self, account_id: str) -> bool:
        """Deactivate a bank account"""
        result = self.bank_accounts.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    # ===== Bank Statement Operations =====

    def create_bank_statement(self, statement: BankStatement) -> str:
        """Create a new bank statement"""
        statement_dict = statement.dict(by_alias=True, exclude={"id"})
        result = self.bank_statements.insert_one(statement_dict)
        return str(result.inserted_id)

    def get_bank_statement(self, statement_id: str) -> Optional[BankStatement]:
        """Get bank statement by ID"""
        doc = self.bank_statements.find_one({"_id": ObjectId(statement_id)})
        return BankStatement(**doc) if doc else None

    def get_statement_by_hash(self, file_hash: str) -> Optional[BankStatement]:
        """Check if statement already imported by file hash"""
        doc = self.bank_statements.find_one({"file_hash": file_hash})
        return BankStatement(**doc) if doc else None

    def get_statements_by_account(
        self, bank_account_id: str, limit: int = 50
    ) -> List[BankStatement]:
        """Get bank statements for an account"""
        docs = (
            self.bank_statements.find({"bank_account_id": bank_account_id})
            .sort("statement_date", DESCENDING)
            .limit(limit)
        )
        return [BankStatement(**doc) for doc in docs]

    def mark_statement_processed(self, statement_id: str, processed_by: str) -> bool:
        """Mark statement as processed"""
        result = self.bank_statements.update_one(
            {"_id": ObjectId(statement_id)},
            {
                "$set": {
                    "is_processed": True,
                    "processed_at": datetime.utcnow(),
                    "processed_by": processed_by,
                }
            },
        )
        return result.modified_count > 0

    # ===== Bank Transaction Operations =====

    def create_transaction(self, transaction: BankTransaction) -> str:
        """Create a new bank transaction"""
        trans_dict = transaction.dict(by_alias=True, exclude={"id"})
        result = self.bank_transactions.insert_one(trans_dict)
        return str(result.inserted_id)

    def create_transactions_bulk(self, transactions: List[BankTransaction]) -> List[str]:
        """Bulk insert bank transactions"""
        if not transactions:
            return []

        trans_dicts = [t.dict(by_alias=True, exclude={"id"}) for t in transactions]
        result = self.bank_transactions.insert_many(trans_dicts)
        return [str(id) for id in result.inserted_ids]

    def get_transaction(self, transaction_id: str) -> Optional[BankTransaction]:
        """Get bank transaction by ID"""
        doc = self.bank_transactions.find_one({"_id": ObjectId(transaction_id)})
        return BankTransaction(**doc) if doc else None

    def get_transactions_by_statement(self, statement_id: str) -> List[BankTransaction]:
        """Get all transactions for a statement"""
        docs = self.bank_transactions.find({"statement_id": statement_id}).sort(
            "transaction_date", ASCENDING
        )
        return [BankTransaction(**doc) for doc in docs]

    def query_transactions(
        self, filters: TransactionFilter, skip: int = 0, limit: int = 100
    ) -> List[BankTransaction]:
        """Query transactions with filters"""
        query = {}

        if filters.bank_account_id:
            query["bank_account_id"] = filters.bank_account_id

        if filters.from_date:
            query.setdefault("transaction_date", {})["$gte"] = filters.from_date

        if filters.to_date:
            query.setdefault("transaction_date", {})["$lte"] = filters.to_date

        if filters.transaction_type:
            query["transaction_type"] = filters.transaction_type

        if filters.status:
            query["status"] = filters.status

        if filters.match_status:
            query["match_status"] = filters.match_status

        if filters.min_amount is not None:
            query.setdefault("amount", {})["$gte"] = filters.min_amount

        if filters.max_amount is not None:
            query.setdefault("amount", {})["$lte"] = filters.max_amount

        if filters.counterparty_name:
            query["counterparty_name"] = {"$regex": filters.counterparty_name, "$options": "i"}

        if filters.reference:
            query["reference"] = {"$regex": filters.reference, "$options": "i"}

        docs = (
            self.bank_transactions.find(query)
            .sort("transaction_date", DESCENDING)
            .skip(skip)
            .limit(limit)
        )

        return [BankTransaction(**doc) for doc in docs]

    def update_transaction_status(
        self,
        transaction_id: str,
        status: TransactionStatus,
        match_status: Optional[MatchStatus] = None,
    ) -> bool:
        """Update transaction status"""
        update_data = {"status": status, "updated_at": datetime.utcnow()}

        if match_status:
            update_data["match_status"] = match_status

        result = self.bank_transactions.update_one(
            {"_id": ObjectId(transaction_id)}, {"$set": update_data}
        )
        return result.modified_count > 0

    def match_transaction_to_invoice(
        self, transaction_id: str, invoice_id: str, voucher_id: Optional[str] = None
    ) -> bool:
        """Match transaction to invoice/voucher"""
        update_data = {
            "match_status": MatchStatus.AUTO_MATCHED,
            "matched_invoice_id": invoice_id,
            "status": TransactionStatus.MATCHED,
            "updated_at": datetime.utcnow(),
        }

        if voucher_id:
            update_data["matched_voucher_id"] = voucher_id

        result = self.bank_transactions.update_one(
            {"_id": ObjectId(transaction_id)}, {"$set": update_data}
        )
        return result.modified_count > 0

    def get_unmatched_transactions(
        self, organization_id: str, limit: int = 100
    ) -> List[BankTransaction]:
        """Get unmatched transactions for payment matching"""
        docs = (
            self.bank_transactions.find(
                {
                    "organization_id": organization_id,
                    "match_status": MatchStatus.UNMATCHED,
                    "status": TransactionStatus.PENDING,
                }
            )
            .sort("transaction_date", DESCENDING)
            .limit(limit)
        )
        return [BankTransaction(**doc) for doc in docs]

    # ===== Payment-Invoice Matching =====

    def create_payment_match(self, match: PaymentInvoiceMatch) -> str:
        """Create payment-invoice match record"""
        match_dict = match.dict(by_alias=True, exclude={"id"})
        result = self.payment_matches.insert_one(match_dict)
        return str(result.inserted_id)

    def get_matches_by_transaction(
        self, transaction_id: str
    ) -> List[PaymentInvoiceMatch]:
        """Get all matches for a transaction"""
        docs = self.payment_matches.find({"transaction_id": transaction_id})
        return [PaymentInvoiceMatch(**doc) for doc in docs]

    def get_matches_by_invoice(self, invoice_id: str) -> List[PaymentInvoiceMatch]:
        """Get all matches for an invoice"""
        docs = self.payment_matches.find({"invoice_id": invoice_id})
        return [PaymentInvoiceMatch(**doc) for doc in docs]

    # ===== Reconciliation =====

    def create_reconciliation_report(self, report: ReconciliationReport) -> str:
        """Create reconciliation report"""
        report_dict = report.dict(by_alias=True, exclude={"id"})
        result = self.reconciliation_reports.insert_one(report_dict)
        return str(result.inserted_id)

    def get_reconciliation_reports(
        self, bank_account_id: str, limit: int = 20
    ) -> List[ReconciliationReport]:
        """Get reconciliation reports for account"""
        docs = (
            self.reconciliation_reports.find({"bank_account_id": bank_account_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [ReconciliationReport(**doc) for doc in docs]

    # ===== Statistics =====

    def get_transaction_stats(
        self, bank_account_id: str, from_date: datetime, to_date: datetime
    ) -> Dict[str, Any]:
        """Get transaction statistics for a period"""
        pipeline = [
            {
                "$match": {
                    "bank_account_id": bank_account_id,
                    "transaction_date": {"$gte": from_date, "$lte": to_date},
                }
            },
            {
                "$group": {
                    "_id": "$transaction_type",
                    "total": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                    "avg": {"$avg": "$amount"},
                }
            },
        ]

        results = list(self.bank_transactions.aggregate(pipeline))

        stats = {
            "total_debits": 0.0,
            "total_credits": 0.0,
            "debit_count": 0,
            "credit_count": 0,
            "avg_debit": 0.0,
            "avg_credit": 0.0,
        }

        for result in results:
            if result["_id"] == "debit":
                stats["total_debits"] = result["total"]
                stats["debit_count"] = result["count"]
                stats["avg_debit"] = result["avg"]
            elif result["_id"] == "credit":
                stats["total_credits"] = result["total"]
                stats["credit_count"] = result["count"]
                stats["avg_credit"] = result["avg"]

        stats["net_flow"] = stats["total_credits"] - stats["total_debits"]
        stats["total_transactions"] = stats["debit_count"] + stats["credit_count"]

        return stats
