"""
Bank Transaction Models for Contia365
Supports CSV, CAMT.053 (ISO 20022), and MT940 bank statement formats
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from bson import ObjectId


class PyObjectId(str):
    """Custom ObjectId for MongoDB"""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, field=None):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return str(v)


class BankStatementFormat(str, Enum):
    """Supported bank statement formats"""
    CSV = "csv"
    CAMT053 = "camt053"  # ISO 20022 XML format
    MT940 = "mt940"  # SWIFT MT940 format
    PDF = "pdf"  # PDF bank statements


class TransactionType(str, Enum):
    """Bank transaction types"""
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionStatus(str, Enum):
    """Transaction processing status"""
    PENDING = "pending"
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    RECONCILED = "reconciled"
    DISPUTED = "disputed"


class MatchStatus(str, Enum):
    """Invoice-payment match status"""
    AUTO_MATCHED = "auto_matched"
    MANUAL_MATCHED = "manual_matched"
    UNMATCHED = "unmatched"
    PARTIALLY_MATCHED = "partially_matched"


class BankAccount(BaseModel):
    """Bank account information"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    organization_id: str
    account_name: str
    account_number: str
    iban: Optional[str] = None
    swift_bic: Optional[str] = None
    bank_name: str
    currency: str = "EUR"
    opening_balance: float = 0.0
    current_balance: float = 0.0
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class BankAccountCreate(BaseModel):
    """Create bank account request"""
    account_name: str
    account_number: str
    iban: Optional[str] = None
    swift_bic: Optional[str] = None
    bank_name: str
    currency: str = "EUR"
    opening_balance: float = 0.0


class BankTransaction(BaseModel):
    """Individual bank transaction"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    organization_id: str
    bank_account_id: str
    statement_id: Optional[str] = None

    # Transaction details
    transaction_date: datetime
    value_date: datetime
    booking_date: Optional[datetime] = None
    transaction_type: TransactionType
    amount: float
    currency: str = "EUR"

    # Transaction identifiers
    transaction_id: Optional[str] = None  # Bank's transaction ID
    reference: Optional[str] = None  # Payment reference
    end_to_end_id: Optional[str] = None  # End-to-end identification
    mandate_id: Optional[str] = None  # Direct debit mandate ID
    creditor_id: Optional[str] = None  # Creditor identification

    # Counterparty information
    counterparty_name: Optional[str] = None
    counterparty_account: Optional[str] = None
    counterparty_iban: Optional[str] = None
    counterparty_bic: Optional[str] = None

    # Description and categorization
    description: Optional[str] = None
    additional_info: Optional[str] = None
    bank_transaction_code: Optional[str] = None
    purpose_code: Optional[str] = None

    # Balance information
    balance_before: Optional[float] = None
    balance_after: Optional[float] = None

    # Matching and reconciliation
    status: TransactionStatus = TransactionStatus.PENDING
    match_status: MatchStatus = MatchStatus.UNMATCHED
    matched_invoice_id: Optional[str] = None
    matched_voucher_id: Optional[str] = None
    ledger_entry_id: Optional[str] = None
    reconciled_at: Optional[datetime] = None

    # Metadata
    raw_data: Optional[Dict[str, Any]] = None  # Store original parsed data
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    imported_by: Optional[str] = None

    @validator("amount")
    def validate_amount(cls, v):
        if v == 0:
            raise ValueError("Amount cannot be zero")
        return round(v, 2)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class BankStatement(BaseModel):
    """Bank statement containing multiple transactions"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    organization_id: str
    bank_account_id: str

    # Statement details
    statement_number: Optional[str] = None
    format: BankStatementFormat
    statement_date: datetime
    from_date: datetime
    to_date: datetime

    # Balance information
    opening_balance: float
    closing_balance: float
    total_debits: float = 0.0
    total_credits: float = 0.0
    transaction_count: int = 0

    # File information
    file_name: str
    file_path: Optional[str] = None
    file_hash: Optional[str] = None  # To prevent duplicate imports

    # Processing status
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    processed_by: Optional[str] = None

    # Metadata
    currency: str = "EUR"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    imported_by: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class BankStatementUpload(BaseModel):
    """Upload bank statement request"""
    bank_account_id: str
    format: Optional[BankStatementFormat] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


class PaymentInvoiceMatch(BaseModel):
    """Represents a match between payment and invoice"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    organization_id: str

    # Match details
    transaction_id: str
    invoice_id: Optional[str] = None
    voucher_id: Optional[str] = None

    # Match information
    match_status: MatchStatus
    match_score: float = 0.0  # Confidence score 0-100
    match_method: str  # "amount", "reference", "name", "manual", "combined"
    matched_amount: float

    # Match criteria
    criteria_matched: List[str] = []  # ["amount", "reference", "date", "name"]

    # Audit trail
    matched_by: Optional[str] = None  # User ID if manual match
    matched_at: datetime = Field(default_factory=datetime.utcnow)

    # Notes
    notes: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class ReconciliationReport(BaseModel):
    """Bank reconciliation report"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    organization_id: str
    bank_account_id: str

    # Report period
    from_date: datetime
    to_date: datetime

    # Balances
    opening_balance: float
    closing_balance: float
    book_balance: float
    bank_balance: float
    difference: float

    # Transaction summary
    total_transactions: int
    matched_transactions: int
    unmatched_transactions: int

    # Matched amounts
    matched_debits: float = 0.0
    matched_credits: float = 0.0
    unmatched_debits: float = 0.0
    unmatched_credits: float = 0.0

    # Status
    is_reconciled: bool = False
    reconciled_by: Optional[str] = None
    reconciled_at: Optional[datetime] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class TransactionFilter(BaseModel):
    """Filter criteria for querying transactions"""
    bank_account_id: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    transaction_type: Optional[TransactionType] = None
    status: Optional[TransactionStatus] = None
    match_status: Optional[MatchStatus] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    counterparty_name: Optional[str] = None
    reference: Optional[str] = None


class BankTransactionUpdate(BaseModel):
    """Update bank transaction"""
    status: Optional[TransactionStatus] = None
    match_status: Optional[MatchStatus] = None
    matched_invoice_id: Optional[str] = None
    matched_voucher_id: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None


class TransactionsToLedgerRequest(BaseModel):
    """Request to convert bank transactions to ledger entries"""
    transaction_ids: List[str] = Field(..., min_items=1, description="List of transaction IDs to convert to ledger entries")
