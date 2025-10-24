"""
Accounting Service Layer
Implements business logic for double-entry bookkeeping, journal entries, and ledger operations.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, date
from decimal import Decimal
from bson import ObjectId

from app.models.accounting import (
    Account, AccountCreate, AccountUpdate, AccountType,
    Journal, JournalCreate,
    Voucher, VoucherCreate, VoucherStatus,
    JournalEntry, JournalEntryCreate, JournalEntryType,
    LedgerEntry,
    PostingRule, PostingRuleCreate,
    TrialBalance, AccountBalance
)
from app.repos.accounting_repo import AccountingRepository


class AccountingService:
    """Service layer for accounting operations with business logic."""
    
    def __init__(self):
        self.repo = AccountingRepository()
    
    # ==================== CHART OF ACCOUNTS ====================
    
    async def create_account(self, account_data: AccountCreate, organization_id: str) -> Account:
        """Create a new account with validation."""
        # Check if account code already exists
        existing = await self.repo.get_account_by_code(organization_id, account_data.account_code)
        if existing:
            raise ValueError(f"Account code {account_data.account_code} already exists")
        
        # Validate parent account exists if specified
        if account_data.parent_account_id:
            parent = await self.repo.get_account_by_id(organization_id, account_data.parent_account_id)
            if not parent:
                raise ValueError("Parent account not found")
        
        account = Account(
            **account_data.dict(),
            organization_id=organization_id,
            balance=Decimal('0.00'),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        return await self.repo.create_account(organization_id, account_data)
    
    async def get_account(self, account_id: str, organization_id: str) -> Optional[Account]:
        """Get account by ID."""
        return await self.repo.get_account_by_id(organization_id, account_id)
    
    async def get_account_by_code(self, account_code: str, organization_id: str) -> Optional[Account]:
        """Get account by code."""
        return await self.repo.get_account_by_code(organization_id, account_code)
    
    async def get_accounts(self, organization_id: str, account_type: Optional[AccountType] = None) -> List[Account]:
        """Get all accounts, optionally filtered by type."""
        return await self.repo.get_accounts(organization_id, account_type)
    
    async def update_account(self, account_id: str, account_data: AccountUpdate, organization_id: str) -> Optional[Account]:
        """Update account details."""
        # Validate account exists
        account = await self.repo.get_account_by_id(account_id, organization_id)
        if not account:
            raise ValueError("Account not found")
        
        # If updating account code, check for duplicates
        if account_data.account_code and account_data.account_code != account.account_code:
            existing = await self.repo.get_account_by_code(account_data.account_code, organization_id)
            if existing:
                raise ValueError(f"Account code {account_data.account_code} already exists")
        
        # Validate parent account if specified
        if account_data.parent_account_id:
            parent = await self.repo.get_account_by_id(account_data.parent_account_id, organization_id)
            if not parent:
                raise ValueError("Parent account not found")
        
        update_data = account_data.dict(exclude_unset=True)
        update_data['updated_at'] = datetime.utcnow()
        
        return await self.repo.update_account(organization_id, account_id, update_data)
    
    # ==================== JOURNALS ====================
    
    async def create_journal(self, journal_data: JournalCreate, organization_id: str) -> Journal:
        """Create a new journal."""
        # Check if journal code already exists
        existing = await self.repo.get_journal_by_code(journal_data.journal_code, organization_id)
        if existing:
            raise ValueError(f"Journal code {journal_data.journal_code} already exists")
        
        journal = Journal(
            **journal_data.dict(),
            organization_id=organization_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        return await self.repo.create_journal(organization_id, journal_data)
    
    async def get_journal(self, journal_id: str, organization_id: str) -> Optional[Journal]:
        """Get journal by ID."""
        return await self.repo.get_journal_by_id(organization_id, journal_id)
    
    async def get_journals(self, organization_id: str) -> List[Journal]:
        """Get all journals."""
        return await self.repo.get_journals(organization_id)
    
    # ==================== VOUCHERS & JOURNAL ENTRIES ====================
    
    async def create_voucher(self, voucher_data: VoucherCreate, organization_id: str) -> Voucher:
        """Create a voucher with journal entries (double-entry validation)."""
        # Validate journal exists
        journal = await self.repo.get_journal_by_id(voucher_data.journal_id, organization_id)
        if not journal:
            raise ValueError("Journal not found")
        
        # Validate journal entries balance (debits = credits)
        total_debits = sum(entry.debit_amount or Decimal('0') for entry in voucher_data.journal_entries)
        total_credits = sum(entry.credit_amount or Decimal('0') for entry in voucher_data.journal_entries)
        
        if total_debits != total_credits:
            raise ValueError(f"Journal entries must balance: Debits {total_debits} != Credits {total_credits}")
        
        # Validate all accounts exist
        for entry in voucher_data.journal_entries:
            account = await self.repo.get_account_by_id(entry.account_id, organization_id)
            if not account:
                raise ValueError(f"Account {entry.account_id} not found")
        
        # Create voucher with auto-generated voucher number
        return await self.repo.create_voucher(voucher_data, organization_id)
    
    async def get_voucher(self, voucher_id: str, organization_id: str) -> Optional[Voucher]:
        """Get voucher by ID."""
        return await self.repo.get_voucher_by_id(organization_id, voucher_id)
    
    async def get_vouchers(self, organization_id: str, journal_id: Optional[str] = None, 
                          status: Optional[VoucherStatus] = None,
                          date_from: Optional[date] = None, 
                          date_to: Optional[date] = None) -> List[Voucher]:
        """Get vouchers with optional filters."""
        return await self.repo.get_vouchers(organization_id, journal_id, status, date_from, date_to)
    
    async def post_voucher(self, voucher_id: str, organization_id: str) -> Voucher:
        """Post a voucher (create ledger entries and update account balances)."""
        voucher = await self.repo.get_voucher_by_id(organization_id, voucher_id)
        if not voucher:
            raise ValueError("Voucher not found")
        
        if voucher.status != VoucherStatus.DRAFT:
            raise ValueError(f"Cannot post voucher with status {voucher.status}")
        
        return await self.repo.post_voucher(voucher_id, organization_id)
    
    # ==================== LEDGER & REPORTING ====================
    
    async def get_journal_entries(self, organization_id: str, account_id: Optional[str] = None,
                                 journal_id: Optional[str] = None,
                                 date_from: Optional[date] = None,
                                 date_to: Optional[date] = None) -> List[JournalEntry]:
        """Get journal entries with optional filters."""
        return await self.repo.get_journal_entries(organization_id, account_id, journal_id, date_from, date_to)
    
    async def get_ledger_entries(self, organization_id: str, account_id: Optional[str] = None,
                                date_from: Optional[date] = None,
                                date_to: Optional[date] = None) -> List[LedgerEntry]:
        """Get ledger entries with optional filters."""
        return await self.repo.get_ledger_entries(organization_id, account_id, date_from, date_to)
    
    async def get_account_balance(self, account_id: str, organization_id: str, 
                                 as_of_date: Optional[date] = None) -> AccountBalance:
        """Get account balance as of a specific date."""
        return await self.repo.get_account_balance(account_id, organization_id, as_of_date)
    
    async def get_trial_balance(self, organization_id: str, as_of_date: Optional[date] = None) -> TrialBalance:
        """Generate trial balance report."""
        return await self.repo.get_trial_balance(organization_id, as_of_date)
    
    # ==================== POSTING RULES ====================
    
    async def create_posting_rule(self, rule_data: PostingRuleCreate, organization_id: str) -> PostingRule:
        """Create a posting rule for automated journal entries."""
        # Validate accounts exist
        for mapping in rule_data.account_mappings:
            account = await self.repo.get_account_by_id(mapping['account_id'], organization_id)
            if not account:
                raise ValueError(f"Account {mapping['account_id']} not found")
        
        rule = PostingRule(
            **rule_data.dict(),
            organization_id=organization_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        return await self.repo.create_posting_rule(organization_id, rule_data)
    
    async def get_posting_rules(self, organization_id: str, event_type: Optional[str] = None) -> List[PostingRule]:
        """Get posting rules, optionally filtered by event type."""
        return await self.repo.get_posting_rules(organization_id, event_type)
    
    async def apply_posting_rule(self, event_type: str, event_data: Dict[str, Any], 
                               organization_id: str) -> Optional[Voucher]:
        """Apply posting rules to create automated journal entries."""
        rules = await self.get_posting_rules(organization_id, event_type)
        
        if not rules:
            return None
        
        # For simplicity, apply the first matching rule
        rule = rules[0]
        
        # Create journal entries based on rule mappings
        journal_entries = []
        for mapping in rule.account_mappings:
            # Extract amount from event data based on mapping configuration
            amount_field = mapping.get('amount_field', 'amount')
            amount = Decimal(str(event_data.get(amount_field, 0)))
            
            if amount <= 0:
                continue
            
            entry_type = mapping.get('entry_type', 'debit')
            
            journal_entry = JournalEntryCreate(
                account_id=mapping['account_id'],
                description=f"Auto: {rule.description} - {event_data.get('description', '')}",
                debit_amount=amount if entry_type == 'debit' else None,
                credit_amount=amount if entry_type == 'credit' else None,
                reference=event_data.get('reference', ''),
                entry_type=JournalEntryType.DEBIT if entry_type == 'debit' else JournalEntryType.CREDIT
            )
            journal_entries.append(journal_entry)
        
        if not journal_entries:
            return None
        
        # Create voucher with auto-generated entries
        voucher_data = VoucherCreate(
            journal_id=rule.default_journal_id,
            voucher_date=datetime.utcnow().date(),
            description=f"Auto: {rule.description}",
            reference=event_data.get('reference', ''),
            journal_entries=journal_entries
        )
        
        voucher = await self.create_voucher(voucher_data, organization_id)
        
        # Auto-post if rule specifies
        if rule.auto_post:
            voucher = await self.post_voucher(str(voucher.id), organization_id)
        
        return voucher
    
    # ==================== BUSINESS LOGIC HELPERS ====================
    
    async def create_standard_chart_of_accounts(self, organization_id: str) -> List[Account]:
        """Create a standard chart of accounts for a new organization."""
        standard_accounts = [
            # Assets
            {"account_code": "1000", "account_name": "Cash", "account_type": AccountType.ASSET, "is_active": True},
            {"account_code": "1100", "account_name": "Accounts Receivable", "account_type": AccountType.ASSET, "is_active": True},
            {"account_code": "1200", "account_name": "Inventory", "account_type": AccountType.ASSET, "is_active": True},
            {"account_code": "1500", "account_name": "Equipment", "account_type": AccountType.ASSET, "is_active": True},
            
            # Liabilities
            {"account_code": "2000", "account_name": "Accounts Payable", "account_type": AccountType.LIABILITY, "is_active": True},
            {"account_code": "2100", "account_name": "Accrued Expenses", "account_type": AccountType.LIABILITY, "is_active": True},
            {"account_code": "2500", "account_name": "Long-term Debt", "account_type": AccountType.LIABILITY, "is_active": True},
            
            # Equity
            {"account_code": "3000", "account_name": "Owner's Equity", "account_type": AccountType.EQUITY, "is_active": True},
            {"account_code": "3100", "account_name": "Retained Earnings", "account_type": AccountType.EQUITY, "is_active": True},
            
            # Revenue
            {"account_code": "4000", "account_name": "Sales Revenue", "account_type": AccountType.REVENUE, "is_active": True},
            {"account_code": "4100", "account_name": "Service Revenue", "account_type": AccountType.REVENUE, "is_active": True},
            
            # Expenses
            {"account_code": "5000", "account_name": "Cost of Goods Sold", "account_type": AccountType.EXPENSE, "is_active": True},
            {"account_code": "6000", "account_name": "Operating Expenses", "account_type": AccountType.EXPENSE, "is_active": True},
            {"account_code": "6100", "account_name": "Salaries Expense", "account_type": AccountType.EXPENSE, "is_active": True},
            {"account_code": "6200", "account_name": "Rent Expense", "account_type": AccountType.EXPENSE, "is_active": True},
            {"account_code": "6300", "account_name": "Utilities Expense", "account_type": AccountType.EXPENSE, "is_active": True},
        ]
        
        created_accounts = []
        for account_data in standard_accounts:
            try:
                account_create = AccountCreate(**account_data)
                account = await self.create_account(account_create, organization_id)
                created_accounts.append(account)
            except ValueError:
                # Account might already exist, skip
                continue
        
        return created_accounts
    
    async def create_standard_journals(self, organization_id: str) -> List[Journal]:
        """Create standard journals for a new organization."""
        standard_journals = [
            {"journal_code": "GJ", "journal_name": "General Journal", "description": "General purpose journal"},
            {"journal_code": "SJ", "journal_name": "Sales Journal", "description": "Sales transactions"},
            {"journal_code": "PJ", "journal_name": "Purchase Journal", "description": "Purchase transactions"},
            {"journal_code": "CRJ", "journal_name": "Cash Receipts Journal", "description": "Cash receipts"},
            {"journal_code": "CDJ", "journal_name": "Cash Disbursements Journal", "description": "Cash payments"},
        ]
        
        created_journals = []
        for journal_data in standard_journals:
            try:
                journal_create = JournalCreate(**journal_data)
                journal = await self.create_journal(journal_create, organization_id)
                created_journals.append(journal)
            except ValueError:
                # Journal might already exist, skip
                continue
        
        return created_journals