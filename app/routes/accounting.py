"""
Accounting API Routes
Provides REST endpoints for Chart of Accounts, Journals, Vouchers, and Ledger operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import date
from bson import ObjectId

from app.models.accounting import (
    Account, AccountCreate, AccountUpdate, AccountType,
    Journal, JournalCreate,
    Voucher, VoucherCreate, VoucherStatus,
    JournalEntry, LedgerEntry,
    PostingRule, PostingRuleCreate,
    TrialBalance, AccountBalance
)
from app.services.accounting_service import AccountingService

# -------------------- Router --------------------
router = APIRouter(prefix="/accounting", tags=["accounting"])

# -------------------- Dependency Injection --------------------
def get_accounting_service() -> AccountingService:
    """Dependency to get accounting service instance."""
    return AccountingService()

# ==================== CHART OF ACCOUNTS ENDPOINTS ====================

@router.post("/accounts", response_model=Account)
async def create_account(
    account_data: AccountCreate,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create a new account in the chart of accounts."""
    try:
        return await service.create_account(account_data, organization_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/accounts", response_model=List[Account])
async def get_accounts(
    account_type: Optional[AccountType] = Query(None, description="Filter by account type"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get all accounts, optionally filtered by type."""
    return await service.get_accounts(organization_id, account_type)

@router.get("/accounts/{account_id}", response_model=Account)
async def get_account(
    account_id: str,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get a specific account by ID."""
    account = await service.get_account(account_id, organization_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account

@router.get("/accounts/code/{account_code}", response_model=Account)
async def get_account_by_code(
    account_code: str,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get a specific account by code."""
    account = await service.get_account_by_code(account_code, organization_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account

@router.put("/accounts/{account_id}", response_model=Account)
async def update_account(
    account_id: str,
    account_data: AccountUpdate,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Update an existing account."""
    try:
        account = await service.update_account(account_id, account_data, organization_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return account
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/accounts/{account_id}/balance", response_model=AccountBalance)
async def get_account_balance(
    account_id: str,
    as_of_date: Optional[date] = Query(None, description="Balance as of specific date"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get account balance as of a specific date."""
    return await service.get_account_balance(account_id, organization_id, as_of_date)

# ==================== JOURNALS ENDPOINTS ====================

@router.post("/journals", response_model=Journal)
async def create_journal(
    journal_data: JournalCreate,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create a new journal."""
    try:
        return await service.create_journal(journal_data, organization_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/journals", response_model=List[Journal])
async def get_journals(
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get all journals."""
    return await service.get_journals(organization_id)

@router.get("/journals/{journal_id}", response_model=Journal)
async def get_journal(
    journal_id: str,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get a specific journal by ID."""
    journal = await service.get_journal(journal_id, organization_id)
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")
    return journal

# ==================== VOUCHERS ENDPOINTS ====================

@router.post("/vouchers", response_model=Voucher)
async def create_voucher(
    voucher_data: VoucherCreate,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create a new voucher with journal entries."""
    try:
        return await service.create_voucher(voucher_data, organization_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/vouchers", response_model=List[Voucher])
async def get_vouchers(
    journal_id: Optional[str] = Query(None, description="Filter by journal"),
    status: Optional[VoucherStatus] = Query(None, description="Filter by status"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get vouchers with optional filters."""
    return await service.get_vouchers(organization_id, journal_id, status, date_from, date_to)

@router.get("/vouchers/{voucher_id}", response_model=Voucher)
async def get_voucher(
    voucher_id: str,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get a specific voucher by ID."""
    voucher = await service.get_voucher(voucher_id, organization_id)
    if not voucher:
        raise HTTPException(status_code=404, detail="Voucher not found")
    return voucher

@router.post("/vouchers/{voucher_id}/post", response_model=Voucher)
async def post_voucher(
    voucher_id: str,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Post a voucher (create ledger entries and update balances)."""
    try:
        voucher = await service.post_voucher(voucher_id, organization_id)
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        return voucher
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================== JOURNAL ENTRIES ENDPOINTS ====================

@router.get("/journal-entries", response_model=List[JournalEntry])
async def get_journal_entries(
    account_id: Optional[str] = Query(None, description="Filter by account"),
    journal_id: Optional[str] = Query(None, description="Filter by journal"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get journal entries with optional filters."""
    return await service.get_journal_entries(organization_id, account_id, journal_id, date_from, date_to)

# ==================== LEDGER ENDPOINTS ====================

@router.get("/ledger-entries", response_model=List[LedgerEntry])
async def get_ledger_entries(
    account_id: Optional[str] = Query(None, description="Filter by account"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get ledger entries with optional filters."""
    return await service.get_ledger_entries(organization_id, account_id, date_from, date_to)

# ==================== REPORTING ENDPOINTS ====================

@router.get("/trial-balance", response_model=TrialBalance)
async def get_trial_balance(
    as_of_date: Optional[date] = Query(None, description="Trial balance as of specific date"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Generate trial balance report."""
    return await service.get_trial_balance(organization_id, as_of_date)

# ==================== POSTING RULES ENDPOINTS ====================

@router.post("/posting-rules", response_model=PostingRule)
async def create_posting_rule(
    rule_data: PostingRuleCreate,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create a new posting rule for automation."""
    try:
        return await service.create_posting_rule(rule_data, organization_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/posting-rules", response_model=List[PostingRule])
async def get_posting_rules(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Get posting rules, optionally filtered by event type."""
    return await service.get_posting_rules(organization_id, event_type)

# ==================== SETUP ENDPOINTS ====================

@router.post("/setup/chart-of-accounts", response_model=List[Account])
async def setup_chart_of_accounts(
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create standard chart of accounts for organization."""
    return await service.create_standard_chart_of_accounts(organization_id)

@router.post("/setup/journals", response_model=List[Journal])
async def setup_journals(
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Create standard journals for organization."""
    return await service.create_standard_journals(organization_id)

@router.post("/setup/complete")
async def complete_setup(
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Complete accounting setup (create accounts and journals)."""
    try:
        accounts = await service.create_standard_chart_of_accounts(organization_id)
        journals = await service.create_standard_journals(organization_id)
        
        return {
            "message": "Accounting setup completed successfully",
            "accounts_created": len(accounts),
            "journals_created": len(journals),
            "organization_id": organization_id
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================== AUTOMATION ENDPOINTS ====================

@router.post("/automation/apply-posting-rule")
async def apply_posting_rule(
    event_type: str,
    event_data: dict,
    organization_id: str = Query("default_org", description="Organization ID"),
    service: AccountingService = Depends(get_accounting_service)
):
    """Apply posting rules based on event type and data."""
    try:
        result = await service.apply_posting_rules(event_type, event_data, organization_id)
        if result:
            return {
                "message": "Posting rule applied successfully",
                "voucher_created": True,
                "voucher_id": str(result.id) if result else None
            }
        else:
            return {
                "message": "No applicable posting rules found",
                "voucher_created": False
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))