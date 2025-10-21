from typing import List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from bson import ObjectId
import certifi
import os
from dotenv import load_dotenv

from app.models.accounting import (
    Account, AccountCreate, AccountUpdate,
    Journal, JournalCreate,
    Voucher, VoucherCreate, VoucherUpdate, VoucherStatus,
    JournalEntry, JournalEntryCreate, JournalEntryType,
    LedgerEntry, PostingRule, PostingRuleCreate,
    TrialBalance, TrialBalanceEntry, AccountBalance,
    AccountType
)

load_dotenv()

class AccountingRepository:
    def __init__(self):
        self.mongo_uri = os.getenv("MONGO_URI")
        self.db_name = os.getenv("DB_NAME")
        self.client = MongoClient(self.mongo_uri, tlsCAFile=certifi.where())
        self.db = self.client[self.db_name]
        
        # Collections
        self.accounts: Collection = self.db["accounts"]
        self.journals: Collection = self.db["journals"]
        self.vouchers: Collection = self.db["vouchers"]
        self.journal_entries: Collection = self.db["journal_entries"]
        self.ledger_entries: Collection = self.db["ledger_entries"]
        self.posting_rules: Collection = self.db["posting_rules"]
        
        # Create indexes
        self._create_indexes()
    
    def _create_indexes(self):
        """Create necessary indexes for performance"""
        # Accounts indexes
        self.accounts.create_index([("organization_id", ASCENDING), ("account_code", ASCENDING)], unique=True)
        self.accounts.create_index([("organization_id", ASCENDING), ("account_type", ASCENDING)])
        self.accounts.create_index([("organization_id", ASCENDING), ("is_active", ASCENDING)])
        
        # Journals indexes
        self.journals.create_index([("organization_id", ASCENDING), ("journal_code", ASCENDING)], unique=True)
        
        # Vouchers indexes
        self.vouchers.create_index([("organization_id", ASCENDING), ("voucher_number", ASCENDING)], unique=True)
        self.vouchers.create_index([("organization_id", ASCENDING), ("status", ASCENDING)])
        self.vouchers.create_index([("organization_id", ASCENDING), ("voucher_date", DESCENDING)])
        self.vouchers.create_index([("organization_id", ASCENDING), ("journal_id", ASCENDING)])
        
        # Journal entries indexes
        self.journal_entries.create_index([("organization_id", ASCENDING), ("voucher_id", ASCENDING)])
        self.journal_entries.create_index([("organization_id", ASCENDING), ("account_id", ASCENDING)])
        self.journal_entries.create_index([("organization_id", ASCENDING), ("transaction_date", DESCENDING)])
        
        # Ledger entries indexes
        self.ledger_entries.create_index([("organization_id", ASCENDING), ("account_id", ASCENDING), ("posted_at", DESCENDING)])
        self.ledger_entries.create_index([("organization_id", ASCENDING), ("voucher_id", ASCENDING)])
        self.ledger_entries.create_index([("organization_id", ASCENDING), ("transaction_date", DESCENDING)])
        
        # Posting rules indexes
        self.posting_rules.create_index([("organization_id", ASCENDING), ("event_type", ASCENDING)])
        self.posting_rules.create_index([("organization_id", ASCENDING), ("is_active", ASCENDING)])
    
    def _convert_decimals_to_float(self, data: dict) -> dict:
        """Convert Decimal values to float for MongoDB compatibility"""
        converted = {}
        for key, value in data.items():
            if isinstance(value, Decimal):
                converted[key] = float(value)
            elif isinstance(value, dict):
                converted[key] = self._convert_decimals_to_float(value)
            elif isinstance(value, list):
                converted[key] = [
                    self._convert_decimals_to_float(item) if isinstance(item, dict)
                    else float(item) if isinstance(item, Decimal)
                    else item
                    for item in value
                ]
            else:
                converted[key] = value
        return converted
    
    def _convert_objectid_to_string(self, data: dict) -> dict:
        """Convert ObjectId to string for Pydantic compatibility"""
        if data is None:
            return None
        converted = {}
        for key, value in data.items():
            if isinstance(value, ObjectId):
                converted[key] = str(value)
            elif isinstance(value, dict):
                converted[key] = self._convert_objectid_to_string(value)
            elif isinstance(value, list):
                converted[key] = [
                    self._convert_objectid_to_string(item) if isinstance(item, dict)
                    else str(item) if isinstance(item, ObjectId)
                    else item
                    for item in value
                ]
            else:
                converted[key] = value
        return converted

    # ==================== ACCOUNTS ====================
    
    async def create_account(self, organization_id: str, account_data: AccountCreate) -> Account:
        """Create a new account"""
        account_dict = account_data.dict()
        account_dict["organization_id"] = organization_id
        account_dict["created_at"] = datetime.utcnow()
        account_dict["updated_at"] = datetime.utcnow()
        
        # Convert Decimal values to float for MongoDB compatibility
        account_dict = self._convert_decimals_to_float(account_dict)
        
        result = self.accounts.insert_one(account_dict)
        account_dict["_id"] = str(result.inserted_id)
        return Account(**account_dict)
    
    async def get_account_by_id(self, organization_id: str, account_id: str) -> Optional[Account]:
        """Get account by ID"""
        account = self.accounts.find_one({
            "_id": ObjectId(account_id),
            "organization_id": organization_id
        })
        return Account(**self._convert_objectid_to_string(account)) if account else None
    
    async def get_account_by_code(self, organization_id: str, account_code: str) -> Optional[Account]:
        """Get account by code"""
        account = self.accounts.find_one({
            "account_code": account_code.upper(),
            "organization_id": organization_id
        })
        return Account(**self._convert_objectid_to_string(account)) if account else None
    
    async def get_accounts(self, organization_id: str, account_type: Optional[AccountType] = None, 
                    is_active: Optional[bool] = None) -> List[Account]:
        """Get accounts with optional filters"""
        query = {"organization_id": organization_id}
        if account_type:
            query["account_type"] = account_type
        if is_active is not None:
            query["is_active"] = is_active
        
        accounts = self.accounts.find(query).sort("account_code", ASCENDING)
        return [Account(**self._convert_objectid_to_string(account)) for account in accounts]
    
    async def update_account(self, organization_id: str, account_id: str, 
                      account_data: AccountUpdate) -> Optional[Account]:
        """Update account"""
        update_dict = {k: v for k, v in account_data.dict().items() if v is not None}
        update_dict["updated_at"] = datetime.utcnow()
        
        # Convert Decimal values to float for MongoDB compatibility
        update_dict = self._convert_decimals_to_float(update_dict)
        
        result = self.accounts.find_one_and_update(
            {"_id": ObjectId(account_id), "organization_id": organization_id},
            {"$set": update_dict},
            return_document=True
        )
        return Account(**self._convert_objectid_to_string(result)) if result else None
    
    async def update_account_balance(self, organization_id: str, account_id: str, 
                              new_balance: Decimal) -> bool:
        """Update account current balance"""
        result = self.accounts.update_one(
            {"_id": ObjectId(account_id), "organization_id": organization_id},
            {"$set": {"current_balance": float(new_balance), "updated_at": datetime.utcnow()}}
        )
        return result.modified_count > 0

    # ==================== JOURNALS ====================
    
    async def create_journal(self, organization_id: str, journal_data: JournalCreate) -> Journal:
        """Create a new journal"""
        journal_dict = journal_data.dict()
        journal_dict["organization_id"] = organization_id
        journal_dict["created_at"] = datetime.utcnow()
        journal_dict["updated_at"] = datetime.utcnow()
        
        result = self.journals.insert_one(journal_dict)
        journal_dict["_id"] = str(result.inserted_id)
        return Journal(**journal_dict)
    
    async def get_journal_by_id(self, organization_id: str, journal_id: str) -> Optional[Journal]:
        """Get journal by ID"""
        journal = self.journals.find_one({
            "_id": ObjectId(journal_id),
            "organization_id": organization_id
        })
        return Journal(**self._convert_objectid_to_string(journal)) if journal else None
    
    async def get_journal_by_code(self, organization_id: str, journal_code: str) -> Optional[Journal]:
        """Get journal by code"""
        journal = self.journals.find_one({
            "journal_code": journal_code,
            "organization_id": organization_id
        })
        return Journal(**self._convert_objectid_to_string(journal)) if journal else None
    
    async def get_journals(self, organization_id: str, is_active: Optional[bool] = None) -> List[Journal]:
        """Get journals with optional filters"""
        query = {"organization_id": organization_id}
        if is_active is not None:
            query["is_active"] = is_active
        
        journals = self.journals.find(query).sort("journal_code", ASCENDING)
        return [Journal(**self._convert_objectid_to_string(journal)) for journal in journals]

    # ==================== VOUCHERS ====================
    
    async def create_voucher(self, organization_id: str, voucher_data: VoucherCreate, 
                      user_id: str) -> Voucher:
        """Create a new voucher with journal entries"""
        # Generate voucher number
        voucher_number = await self._generate_voucher_number(organization_id, voucher_data.journal_id)
        
        # Get journal info
        journal = await self.get_journal_by_id(organization_id, voucher_data.journal_id)
        if not journal:
            raise ValueError("Journal not found")
        
        # Calculate totals
        total_debit = sum(entry.amount for entry in voucher_data.entries 
                         if entry.entry_type == JournalEntryType.DEBIT)
        total_credit = sum(entry.amount for entry in voucher_data.entries 
                          if entry.entry_type == JournalEntryType.CREDIT)
        
        # Create voucher
        voucher_dict = {
            "organization_id": organization_id,
            "voucher_number": voucher_number,
            "journal_id": voucher_data.journal_id,
            "journal_code": journal.journal_code,
            "voucher_date": voucher_data.voucher_date,
            "description": voucher_data.description,
            "reference": voucher_data.reference,
            "source_document": voucher_data.source_document,
            "source_type": voucher_data.source_type,
            "status": VoucherStatus.DRAFT,
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "currency": "EUR",
            "exchange_rate": 1.00,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = self.vouchers.insert_one(voucher_dict)
        voucher_id = str(result.inserted_id)
        voucher_dict["_id"] = voucher_id
        
        # Create journal entries
        for entry_data in voucher_data.entries:
            account = await self.get_account_by_id(organization_id, entry_data.account_id)
            if not account:
                raise ValueError(f"Account {entry_data.account_id} not found")
            
            entry_dict = {
                "organization_id": organization_id,
                "account_id": entry_data.account_id,
                "account_code": account.account_code,
                "account_name": account.account_name,
                "entry_type": entry_data.entry_type,
                "amount": float(entry_data.amount),
                "description": entry_data.description,
                "reference": entry_data.reference,
                "voucher_id": voucher_id,
                "journal_id": voucher_data.journal_id,
                "transaction_date": voucher_data.voucher_date,
                "created_at": datetime.utcnow()
            }
            self.journal_entries.insert_one(entry_dict)
        
        return Voucher(**voucher_dict)
    
    async def get_voucher_by_id(self, organization_id: str, voucher_id: str) -> Optional[Voucher]:
        """Get voucher by ID"""
        voucher = self.vouchers.find_one({
            "_id": ObjectId(voucher_id),
            "organization_id": organization_id
        })
        return Voucher(**self._convert_objectid_to_string(voucher)) if voucher else None
    
    async def get_vouchers(self, organization_id: str, status: Optional[VoucherStatus] = None,
                    journal_id: Optional[str] = None, limit: int = 100, 
                    offset: int = 0) -> List[Voucher]:
        """Get vouchers with optional filters"""
        query = {"organization_id": organization_id}
        if status:
            query["status"] = status
        if journal_id:
            query["journal_id"] = journal_id
        
        vouchers = (self.vouchers.find(query)
                   .sort("voucher_date", DESCENDING)
                   .skip(offset)
                   .limit(limit))
        return [Voucher(**self._convert_objectid_to_string(voucher)) for voucher in vouchers]
    
    async def post_voucher(self, organization_id: str, voucher_id: str, user_id: str) -> bool:
        """Post a voucher and create ledger entries"""
        voucher = await self.get_voucher_by_id(organization_id, voucher_id)
        if not voucher or voucher.status != VoucherStatus.DRAFT:
            return False
        
        # Get journal entries for this voucher
        entries = self.journal_entries.find({
            "organization_id": organization_id,
            "voucher_id": voucher_id
        })
        
        # Create ledger entries and update account balances
        for entry in entries:
            # Calculate running balance
            account_id = entry["account_id"]
            account = await self.get_account_by_id(organization_id, account_id)
            
            if entry["entry_type"] == JournalEntryType.DEBIT:
                if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
                    new_balance = account.current_balance + Decimal(str(entry["amount"]))
                else:
                    new_balance = account.current_balance - Decimal(str(entry["amount"]))
            else:  # CREDIT
                if account.account_type in [AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME]:
                    new_balance = account.current_balance + Decimal(str(entry["amount"]))
                else:
                    new_balance = account.current_balance - Decimal(str(entry["amount"]))
            
            # Create ledger entry
            ledger_entry = {
                "organization_id": organization_id,
                "account_id": account_id,
                "account_code": entry["account_code"],
                "account_name": entry["account_name"],
                "voucher_id": voucher_id,
                "voucher_number": voucher.voucher_number,
                "journal_id": entry["journal_id"],
                "journal_code": voucher.journal_code,
                "entry_type": entry["entry_type"],
                "amount": entry["amount"],
                "running_balance": float(new_balance),
                "description": entry["description"],
                "reference": entry["reference"],
                "transaction_date": entry["transaction_date"],
                "posted_at": datetime.utcnow(),
                "created_at": datetime.utcnow()
            }
            self.ledger_entries.insert_one(ledger_entry)
            
            # Update account balance
            await self.update_account_balance(organization_id, account_id, new_balance)
        
        # Update voucher status
        self.vouchers.update_one(
            {"_id": ObjectId(voucher_id), "organization_id": organization_id},
            {"$set": {
                "status": VoucherStatus.POSTED,
                "posted_by": user_id,
                "posted_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }}
        )
        
        return True
    
    async def _generate_voucher_number(self, organization_id: str, journal_id: str) -> str:
        """Generate next voucher number for journal"""
        journal = await self.get_journal_by_id(organization_id, journal_id)
        if not journal:
            raise ValueError("Journal not found")
        
        # Get last voucher number for this journal
        last_voucher = self.vouchers.find_one(
            {"organization_id": organization_id, "journal_id": journal_id},
            sort=[("voucher_number", DESCENDING)]
        )
        
        if last_voucher:
            # Extract number from voucher_number (format: JRN-001)
            try:
                last_num = int(last_voucher["voucher_number"].split("-")[-1])
                next_num = last_num + 1
            except:
                next_num = 1
        else:
            next_num = 1
        
        return f"{journal.journal_code}-{next_num:03d}"

    # ==================== JOURNAL ENTRIES ====================
    
    async def get_journal_entries(self, organization_id: str, voucher_id: Optional[str] = None,
                           account_id: Optional[str] = None) -> List[JournalEntry]:
        """Get journal entries with optional filters"""
        query = {"organization_id": organization_id}
        if voucher_id:
            query["voucher_id"] = voucher_id
        if account_id:
            query["account_id"] = account_id
        
        entries = self.journal_entries.find(query).sort("created_at", DESCENDING)
        return [JournalEntry(**self._convert_objectid_to_string(entry)) for entry in entries]

    # ==================== LEDGER ====================
    
    async def get_ledger_entries(self, organization_id: str, account_id: Optional[str] = None,
                          start_date: Optional[datetime] = None, 
                          end_date: Optional[datetime] = None) -> List[LedgerEntry]:
        """Get ledger entries with optional filters"""
        query = {"organization_id": organization_id}
        if account_id:
            query["account_id"] = account_id
        if start_date or end_date:
            date_query = {}
            if start_date:
                date_query["$gte"] = start_date
            if end_date:
                date_query["$lte"] = end_date
            query["transaction_date"] = date_query
        
        entries = self.ledger_entries.find(query).sort("posted_at", DESCENDING)
        return [LedgerEntry(**self._convert_objectid_to_string(entry)) for entry in entries]
    
    async def get_account_balance(self, organization_id: str, account_id: str, 
                           as_of_date: Optional[datetime] = None) -> Optional[AccountBalance]:
        """Get account balance as of a specific date"""
        account = await self.get_account_by_id(organization_id, account_id)
        if not account:
            return None
        
        query = {
            "organization_id": organization_id,
            "account_id": account_id
        }
        
        if as_of_date:
            query["transaction_date"] = {"$lte": as_of_date}
        
        # Aggregate debits and credits
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": None,
                "total_debits": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$entry_type", "debit"]},
                            "$amount",
                            0
                        ]
                    }
                },
                "total_credits": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$entry_type", "credit"]},
                            "$amount",
                            0
                        ]
                    }
                }
            }}
        ]
        
        result = list(self.ledger_entries.aggregate(pipeline))
        
        if result:
            total_debits = Decimal(str(result[0]["total_debits"]))
            total_credits = Decimal(str(result[0]["total_credits"]))
        else:
            total_debits = Decimal("0.00")
            total_credits = Decimal("0.00")
        
        # Calculate closing balance based on account type
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            closing_balance = account.opening_balance + total_debits - total_credits
        else:
            closing_balance = account.opening_balance + total_credits - total_debits
        
        return AccountBalance(
            account_id=account_id,
            account_code=account.account_code,
            account_name=account.account_name,
            account_type=account.account_type,
            opening_balance=account.opening_balance,
            total_debits=total_debits,
            total_credits=total_credits,
            closing_balance=closing_balance,
            as_of_date=as_of_date or datetime.utcnow()
        )

    # ==================== TRIAL BALANCE ====================
    
    async def get_trial_balance(self, organization_id: str, as_of_date: Optional[datetime] = None) -> TrialBalance:
        """Generate trial balance"""
        accounts = await self.get_accounts(organization_id, is_active=True)
        entries = []
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")
        
        for account in accounts:
            balance = await self.get_account_balance(organization_id, account.id, as_of_date)
            if balance:
                if balance.closing_balance > 0:
                    if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
                        debit_balance = balance.closing_balance
                        credit_balance = Decimal("0.00")
                        total_debits += debit_balance
                    else:
                        debit_balance = Decimal("0.00")
                        credit_balance = balance.closing_balance
                        total_credits += credit_balance
                elif balance.closing_balance < 0:
                    if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
                        debit_balance = Decimal("0.00")
                        credit_balance = abs(balance.closing_balance)
                        total_credits += credit_balance
                    else:
                        debit_balance = abs(balance.closing_balance)
                        credit_balance = Decimal("0.00")
                        total_debits += debit_balance
                else:
                    debit_balance = Decimal("0.00")
                    credit_balance = Decimal("0.00")
                
                entries.append(TrialBalanceEntry(
                    account_code=account.account_code,
                    account_name=account.account_name,
                    account_type=account.account_type,
                    debit_balance=debit_balance,
                    credit_balance=credit_balance
                ))
        
        return TrialBalance(
            organization_id=organization_id,
            period_start=datetime(as_of_date.year, 1, 1) if as_of_date else datetime(datetime.utcnow().year, 1, 1),
            period_end=as_of_date or datetime.utcnow(),
            entries=entries,
            total_debits=total_debits,
            total_credits=total_credits
        )

    # ==================== POSTING RULES ====================
    
    async def create_posting_rule(self, organization_id: str, rule_data: PostingRuleCreate) -> PostingRule:
        """Create a new posting rule"""
        rule_dict = rule_data.dict()
        rule_dict["organization_id"] = organization_id
        rule_dict["created_at"] = datetime.utcnow()
        rule_dict["updated_at"] = datetime.utcnow()
        
        result = self.posting_rules.insert_one(rule_dict)
        rule_dict["_id"] = str(result.inserted_id)
        return PostingRule(**rule_dict)
    
    async def get_posting_rules(self, organization_id: str, event_type: Optional[str] = None) -> List[PostingRule]:
        """Get posting rules with optional filters"""
        query = {"organization_id": organization_id, "is_active": True}
        if event_type:
            query["event_type"] = event_type
        
        rules = self.posting_rules.find(query).sort("priority", ASCENDING)
        return [PostingRule(**self._convert_objectid_to_string(rule)) for rule in rules]