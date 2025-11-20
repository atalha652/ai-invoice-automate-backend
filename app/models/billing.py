"""
Billing and Subscription Models for Contia365
Handles Stripe payments, subscriptions, and automated billing
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, EmailStr
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


class PaymentProvider(str, Enum):
    """Supported payment providers"""
    STRIPE = "stripe"
    REDSYS = "redsys"
    BIZUM = "bizum"


class SubscriptionTier(str, Enum):
    """Subscription pricing tiers"""
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """Subscription lifecycle status"""
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"
    SUSPENDED = "suspended"  # After 5 failed retry attempts


class PaymentStatus(str, Enum):
    """Payment transaction status"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    DISPUTED = "disputed"
    CANCELED = "canceled"


class PaymentIntentStatus(str, Enum):
    """Stripe payment intent status"""
    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    REQUIRES_CAPTURE = "requires_capture"
    CANCELED = "canceled"
    SUCCEEDED = "succeeded"


class BillingInterval(str, Enum):
    """Billing frequency"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RetryStatus(str, Enum):
    """Payment retry status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"  # All 5 retries failed


# ===== Subscription Plans =====

class SubscriptionPlan(BaseModel):
    """Subscription plan definition"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # Plan details
    name: str
    tier: SubscriptionTier
    description: Optional[str] = None

    # Pricing
    price_monthly: float
    price_yearly: float
    currency: str = "EUR"

    # Stripe integration
    stripe_price_id_monthly: Optional[str] = None
    stripe_price_id_yearly: Optional[str] = None
    stripe_product_id: Optional[str] = None

    # Features and limits
    features: List[str] = []
    max_users: int = 1
    max_invoices_per_month: int = 100
    max_storage_gb: int = 5

    # Status
    is_active: bool = True
    is_public: bool = True

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


# ===== User Subscription =====

class Subscription(BaseModel):
    """User subscription record"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # User and plan
    user_id: str
    organization_id: str
    plan_id: str
    tier: SubscriptionTier

    # Stripe integration
    stripe_subscription_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_price_id: Optional[str] = None

    # Subscription details
    status: SubscriptionStatus
    billing_interval: BillingInterval = BillingInterval.MONTHLY

    # Dates
    start_date: datetime
    current_period_start: datetime
    current_period_end: datetime
    trial_end: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    # Pricing
    amount: float
    currency: str = "EUR"
    tax_rate: float = 0.0

    # Payment tracking
    last_payment_date: Optional[datetime] = None
    next_payment_date: datetime
    failed_payment_count: int = 0

    # Retry logic for failed payments
    retry_attempt: int = 0  # Current retry attempt (0-5)
    max_retry_attempts: int = 5
    last_retry_date: Optional[datetime] = None
    next_retry_date: Optional[datetime] = None
    retry_status: RetryStatus = RetryStatus.PENDING

    # Account suspension
    is_suspended: bool = False
    suspended_at: Optional[datetime] = None
    suspension_reason: Optional[str] = None

    # Features enabled
    features_enabled: List[str] = []

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    # Notes and internal tracking
    notes: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class SubscriptionCreate(BaseModel):
    """Create subscription request"""
    plan_id: str
    billing_interval: BillingInterval = BillingInterval.MONTHLY
    payment_method_id: Optional[str] = None  # Stripe payment method
    trial_days: int = 0


class SubscriptionUpdate(BaseModel):
    """Update subscription request"""
    plan_id: Optional[str] = None
    billing_interval: Optional[BillingInterval] = None
    status: Optional[SubscriptionStatus] = None


# ===== Payment Methods =====

class PaymentMethod(BaseModel):
    """Saved payment method (card, bank account, etc.)"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # User association
    user_id: str
    organization_id: str

    # Provider details
    provider: PaymentProvider
    stripe_payment_method_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None

    # Payment method details
    type: str  # "card", "sepa_debit", "bank_account"

    # Card details (if type=card)
    card_brand: Optional[str] = None  # "visa", "mastercard"
    card_last4: Optional[str] = None
    card_exp_month: Optional[int] = None
    card_exp_year: Optional[int] = None
    card_fingerprint: Optional[str] = None

    # Bank account details (if type=sepa_debit)
    bank_name: Optional[str] = None
    iban_last4: Optional[str] = None

    # Billing details
    billing_email: Optional[EmailStr] = None
    billing_name: Optional[str] = None
    billing_phone: Optional[str] = None
    billing_address: Optional[Dict[str, str]] = None

    # Status
    is_default: bool = False
    is_active: bool = True
    is_verified: bool = False

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class PaymentMethodCreate(BaseModel):
    """Add payment method request"""
    provider: PaymentProvider = PaymentProvider.STRIPE
    stripe_payment_method_id: str
    set_as_default: bool = True


# ===== Payment Transactions =====

class PaymentTransaction(BaseModel):
    """Individual payment transaction record"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # User and subscription
    user_id: str
    organization_id: str
    subscription_id: Optional[str] = None

    # Transaction identifiers
    transaction_id: str  # Our internal ID

    # Stripe integration
    stripe_payment_intent_id: Optional[str] = None
    stripe_charge_id: Optional[str] = None
    stripe_invoice_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None

    # Payment details
    provider: PaymentProvider
    payment_method_id: Optional[str] = None
    status: PaymentStatus
    intent_status: Optional[PaymentIntentStatus] = None

    # Amount details
    amount: float
    currency: str = "EUR"
    fee: float = 0.0
    net_amount: float = 0.0
    tax_amount: float = 0.0

    # Description
    description: str
    metadata: Optional[Dict[str, Any]] = None

    # Dates
    transaction_date: datetime = Field(default_factory=datetime.utcnow)
    succeeded_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None

    # Failure information
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    # Refund information
    refund_amount: float = 0.0
    refund_reason: Optional[str] = None

    # Receipt
    receipt_url: Optional[str] = None
    receipt_number: Optional[str] = None

    # Retry information (for failed payments)
    is_retry: bool = False
    retry_count: int = 0
    original_transaction_id: Optional[str] = None

    # Ledger integration
    ledger_entry_id: Optional[str] = None
    voucher_id: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("amount", "net_amount", "fee", "tax_amount", "refund_amount")
    def validate_amounts(cls, v):
        return round(v, 2)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class PaymentTransactionCreate(BaseModel):
    """Create payment transaction"""
    subscription_id: Optional[str] = None
    amount: float
    description: str
    payment_method_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ===== Billing Cycles =====

class BillingCycle(BaseModel):
    """Billing cycle record for tracking monthly charges"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # User and subscription
    user_id: str
    organization_id: str
    subscription_id: str

    # Cycle details
    cycle_start: datetime
    cycle_end: datetime
    billing_date: datetime

    # Amount details
    base_amount: float
    usage_amount: float = 0.0
    tax_amount: float = 0.0
    total_amount: float
    currency: str = "EUR"

    # Payment tracking
    payment_transaction_id: Optional[str] = None
    status: PaymentStatus = PaymentStatus.PENDING
    paid_at: Optional[datetime] = None

    # Usage metrics
    invoices_processed: int = 0
    storage_used_gb: float = 0.0

    # Stripe integration
    stripe_invoice_id: Optional[str] = None
    stripe_invoice_url: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


# ===== Payment Retry Log =====

class PaymentRetryLog(BaseModel):
    """Log of payment retry attempts"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # References
    user_id: str
    organization_id: str
    subscription_id: str
    payment_transaction_id: Optional[str] = None

    # Retry details
    retry_number: int  # 1-5
    retry_date: datetime = Field(default_factory=datetime.utcnow)
    next_retry_date: Optional[datetime] = None

    # Result
    status: PaymentStatus
    success: bool = False

    # Error details
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # Actions taken
    action_taken: Optional[str] = None  # e.g., "email_sent", "account_suspended"

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


# ===== Webhooks =====

class WebhookEvent(BaseModel):
    """Stripe webhook event log"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # Webhook details
    event_id: str  # Stripe event ID
    event_type: str  # e.g., "payment_intent.succeeded"

    # Provider
    provider: PaymentProvider = PaymentProvider.STRIPE

    # Payload
    payload: Dict[str, Any]

    # Processing status
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    processing_error: Optional[str] = None

    # Related entities
    user_id: Optional[str] = None
    subscription_id: Optional[str] = None
    payment_transaction_id: Optional[str] = None

    # Metadata
    received_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


# ===== Invoice (Billing Invoice, not customer invoice) =====

class BillingInvoice(BaseModel):
    """Generated billing invoice for user"""
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    # User details
    user_id: str
    organization_id: str
    subscription_id: str

    # Invoice details
    invoice_number: str
    invoice_date: datetime
    due_date: datetime

    # Amount details
    subtotal: float
    tax_amount: float = 0.0
    total_amount: float
    amount_paid: float = 0.0
    amount_due: float
    currency: str = "EUR"

    # Line items
    line_items: List[Dict[str, Any]] = []

    # Status
    status: PaymentStatus = PaymentStatus.PENDING
    paid_at: Optional[datetime] = None

    # Stripe integration
    stripe_invoice_id: Optional[str] = None
    stripe_invoice_url: Optional[str] = None
    stripe_invoice_pdf: Optional[str] = None

    # Payment tracking
    payment_transaction_id: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}
