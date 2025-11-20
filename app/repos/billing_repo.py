"""
Billing Repository
MongoDB data access layer for subscriptions, payments, and billing
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from bson import ObjectId
import logging

from app.models.billing import (
    SubscriptionPlan,
    Subscription,
    PaymentMethod,
    PaymentTransaction,
    BillingCycle,
    PaymentRetryLog,
    WebhookEvent,
    BillingInvoice,
    SubscriptionStatus,
    PaymentStatus,
    RetryStatus,
)

logger = logging.getLogger(__name__)


class BillingRepository:
    """Repository for billing-related data operations"""

    def __init__(self, db: Any):
        self.db = db
        self.subscription_plans: Collection = db["subscription_plans"]
        self.subscriptions: Collection = db["subscriptions"]
        self.payment_methods: Collection = db["payment_methods"]
        self.payment_transactions: Collection = db["payment_transactions"]
        self.billing_cycles: Collection = db["billing_cycles"]
        self.payment_retry_logs: Collection = db["payment_retry_logs"]
        self.webhook_events: Collection = db["webhook_events"]
        self.billing_invoices: Collection = db["billing_invoices"]

        # Create indexes
        self._create_indexes()

    def _create_indexes(self):
        """Create database indexes for performance"""
        try:
            # Subscription plans
            self.subscription_plans.create_index([("tier", ASCENDING)])
            self.subscription_plans.create_index([("is_active", ASCENDING)])

            # Subscriptions
            self.subscriptions.create_index([("user_id", ASCENDING)])
            self.subscriptions.create_index([("organization_id", ASCENDING)])
            self.subscriptions.create_index([("stripe_subscription_id", ASCENDING)], unique=True, sparse=True)
            self.subscriptions.create_index([("status", ASCENDING)])
            self.subscriptions.create_index([("next_payment_date", ASCENDING)])
            self.subscriptions.create_index([("is_suspended", ASCENDING)])

            # Payment methods
            self.payment_methods.create_index([("user_id", ASCENDING)])
            self.payment_methods.create_index([("stripe_payment_method_id", ASCENDING)])
            self.payment_methods.create_index([("is_default", ASCENDING)])

            # Payment transactions
            self.payment_transactions.create_index([("user_id", ASCENDING)])
            self.payment_transactions.create_index([("subscription_id", ASCENDING)])
            self.payment_transactions.create_index([("transaction_id", ASCENDING)], unique=True)
            self.payment_transactions.create_index([("stripe_payment_intent_id", ASCENDING)])
            self.payment_transactions.create_index([("status", ASCENDING)])
            self.payment_transactions.create_index([("transaction_date", DESCENDING)])

            # Billing cycles
            self.billing_cycles.create_index([("subscription_id", ASCENDING)])
            self.billing_cycles.create_index([("billing_date", ASCENDING)])
            self.billing_cycles.create_index([("status", ASCENDING)])

            # Payment retry logs
            self.payment_retry_logs.create_index([("subscription_id", ASCENDING)])
            self.payment_retry_logs.create_index([("retry_date", DESCENDING)])

            # Webhook events
            self.webhook_events.create_index([("event_id", ASCENDING)], unique=True)
            self.webhook_events.create_index([("is_processed", ASCENDING)])

            logger.info("Billing repository indexes created successfully")
        except Exception as e:
            logger.error(f"Error creating billing indexes: {e}")

    # ===== Subscription Plans =====

    def create_plan(self, plan: SubscriptionPlan) -> str:
        """Create subscription plan"""
        plan_dict = plan.dict(by_alias=True, exclude={"id"})
        result = self.subscription_plans.insert_one(plan_dict)
        return str(result.inserted_id)

    def get_plan(self, plan_id: str) -> Optional[SubscriptionPlan]:
        """Get subscription plan by ID"""
        doc = self.subscription_plans.find_one({"_id": ObjectId(plan_id)})
        return SubscriptionPlan(**doc) if doc else None

    def get_active_plans(self) -> List[SubscriptionPlan]:
        """Get all active subscription plans"""
        docs = self.subscription_plans.find({"is_active": True, "is_public": True})
        return [SubscriptionPlan(**doc) for doc in docs]

    # ===== Subscriptions =====

    def create_subscription(self, subscription: Subscription) -> str:
        """Create user subscription"""
        sub_dict = subscription.dict(by_alias=True, exclude={"id"})
        result = self.subscriptions.insert_one(sub_dict)
        return str(result.inserted_id)

    def get_subscription(self, subscription_id: str) -> Optional[Subscription]:
        """Get subscription by ID"""
        doc = self.subscriptions.find_one({"_id": ObjectId(subscription_id)})
        return Subscription(**doc) if doc else None

    def get_subscription_by_user(self, user_id: str) -> Optional[Subscription]:
        """Get active subscription for user"""
        doc = self.subscriptions.find_one(
            {"user_id": user_id, "status": {"$in": [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE]}}
        )
        return Subscription(**doc) if doc else None

    def get_subscription_by_stripe_id(self, stripe_subscription_id: str) -> Optional[Subscription]:
        """Get subscription by Stripe ID"""
        doc = self.subscriptions.find_one({"stripe_subscription_id": stripe_subscription_id})
        return Subscription(**doc) if doc else None

    def update_subscription(self, subscription_id: str, updates: Dict[str, Any]) -> bool:
        """Update subscription"""
        updates["updated_at"] = datetime.utcnow()
        result = self.subscriptions.update_one(
            {"_id": ObjectId(subscription_id)},
            {"$set": updates}
        )
        return result.modified_count > 0

    def suspend_subscription(self, subscription_id: str, reason: str) -> bool:
        """Suspend subscription due to payment failure"""
        result = self.subscriptions.update_one(
            {"_id": ObjectId(subscription_id)},
            {
                "$set": {
                    "is_suspended": True,
                    "suspended_at": datetime.utcnow(),
                    "suspension_reason": reason,
                    "status": SubscriptionStatus.SUSPENDED,
                    "updated_at": datetime.utcnow(),
                }
            }
        )
        return result.modified_count > 0

    def unsuspend_subscription(self, subscription_id: str) -> bool:
        """Reactivate suspended subscription"""
        result = self.subscriptions.update_one(
            {"_id": ObjectId(subscription_id)},
            {
                "$set": {
                    "is_suspended": False,
                    "suspended_at": None,
                    "suspension_reason": None,
                    "status": SubscriptionStatus.ACTIVE,
                    "retry_attempt": 0,
                    "retry_status": RetryStatus.PENDING,
                    "updated_at": datetime.utcnow(),
                }
            }
        )
        return result.modified_count > 0

    def increment_retry_attempt(self, subscription_id: str) -> int:
        """Increment retry attempt counter and return new count"""
        now = datetime.utcnow()
        next_retry = now + timedelta(days=1)  # Retry next day

        result = self.subscriptions.find_one_and_update(
            {"_id": ObjectId(subscription_id)},
            {
                "$inc": {"retry_attempt": 1, "failed_payment_count": 1},
                "$set": {
                    "last_retry_date": now,
                    "next_retry_date": next_retry,
                    "retry_status": RetryStatus.IN_PROGRESS,
                    "updated_at": now,
                }
            },
            return_document=True
        )

        if result:
            return result.get("retry_attempt", 0)
        return 0

    def get_subscriptions_for_retry(self) -> List[Subscription]:
        """Get subscriptions that need payment retry"""
        now = datetime.utcnow()

        docs = self.subscriptions.find({
            "status": SubscriptionStatus.PAST_DUE,
            "retry_attempt": {"$lt": 5},
            "next_retry_date": {"$lte": now},
            "is_suspended": False,
        })

        return [Subscription(**doc) for doc in docs]

    def get_subscriptions_for_billing(self) -> List[Subscription]:
        """Get subscriptions due for monthly billing"""
        now = datetime.utcnow()

        docs = self.subscriptions.find({
            "status": SubscriptionStatus.ACTIVE,
            "next_payment_date": {"$lte": now},
            "is_suspended": False,
        })

        return [Subscription(**doc) for doc in docs]

    # ===== Payment Methods =====

    def create_payment_method(self, payment_method: PaymentMethod) -> str:
        """Create payment method"""
        pm_dict = payment_method.dict(by_alias=True, exclude={"id"})
        result = self.payment_methods.insert_one(pm_dict)
        return str(result.inserted_id)

    def get_payment_method(self, payment_method_id: str) -> Optional[PaymentMethod]:
        """Get payment method by ID"""
        doc = self.payment_methods.find_one({"_id": ObjectId(payment_method_id)})
        return PaymentMethod(**doc) if doc else None

    def get_default_payment_method(self, user_id: str) -> Optional[PaymentMethod]:
        """Get user's default payment method"""
        doc = self.payment_methods.find_one({"user_id": user_id, "is_default": True, "is_active": True})
        return PaymentMethod(**doc) if doc else None

    def get_payment_methods_by_user(self, user_id: str) -> List[PaymentMethod]:
        """Get all payment methods for user"""
        docs = self.payment_methods.find({"user_id": user_id, "is_active": True})
        return [PaymentMethod(**doc) for doc in docs]

    def set_default_payment_method(self, user_id: str, payment_method_id: str) -> bool:
        """Set default payment method"""
        # Unset all as default
        self.payment_methods.update_many(
            {"user_id": user_id},
            {"$set": {"is_default": False}}
        )

        # Set new default
        result = self.payment_methods.update_one(
            {"_id": ObjectId(payment_method_id), "user_id": user_id},
            {"$set": {"is_default": True, "updated_at": datetime.utcnow()}}
        )

        return result.modified_count > 0

    # ===== Payment Transactions =====

    def create_payment_transaction(self, transaction: PaymentTransaction) -> str:
        """Create payment transaction"""
        trans_dict = transaction.dict(by_alias=True, exclude={"id"})
        result = self.payment_transactions.insert_one(trans_dict)
        return str(result.inserted_id)

    def get_payment_transaction(self, transaction_id: str) -> Optional[PaymentTransaction]:
        """Get payment transaction by ID"""
        doc = self.payment_transactions.find_one({"_id": ObjectId(transaction_id)})
        return PaymentTransaction(**doc) if doc else None

    def get_transaction_by_payment_intent(self, stripe_payment_intent_id: str) -> Optional[PaymentTransaction]:
        """Get transaction by Stripe payment intent ID"""
        doc = self.payment_transactions.find_one({"stripe_payment_intent_id": stripe_payment_intent_id})
        return PaymentTransaction(**doc) if doc else None

    def update_payment_transaction(self, transaction_id: str, updates: Dict[str, Any]) -> bool:
        """Update payment transaction"""
        updates["updated_at"] = datetime.utcnow()
        result = self.payment_transactions.update_one(
            {"_id": ObjectId(transaction_id)},
            {"$set": updates}
        )
        return result.modified_count > 0

    def get_transactions_by_user(self, user_id: str, limit: int = 50) -> List[PaymentTransaction]:
        """Get payment transactions for user"""
        docs = self.payment_transactions.find({"user_id": user_id}).sort("transaction_date", DESCENDING).limit(limit)
        return [PaymentTransaction(**doc) for doc in docs]

    def get_transactions_by_subscription(self, subscription_id: str) -> List[PaymentTransaction]:
        """Get transactions for subscription"""
        docs = self.payment_transactions.find({"subscription_id": subscription_id}).sort("transaction_date", DESCENDING)
        return [PaymentTransaction(**doc) for doc in docs]

    # ===== Billing Cycles =====

    def create_billing_cycle(self, cycle: BillingCycle) -> str:
        """Create billing cycle"""
        cycle_dict = cycle.dict(by_alias=True, exclude={"id"})
        result = self.billing_cycles.insert_one(cycle_dict)
        return str(result.inserted_id)

    def get_billing_cycle(self, cycle_id: str) -> Optional[BillingCycle]:
        """Get billing cycle by ID"""
        doc = self.billing_cycles.find_one({"_id": ObjectId(cycle_id)})
        return BillingCycle(**doc) if doc else None

    def get_billing_cycles_by_subscription(self, subscription_id: str) -> List[BillingCycle]:
        """Get billing cycles for subscription"""
        docs = self.billing_cycles.find({"subscription_id": subscription_id}).sort("billing_date", DESCENDING)
        return [BillingCycle(**doc) for doc in docs]

    # ===== Payment Retry Logs =====

    def create_retry_log(self, log: PaymentRetryLog) -> str:
        """Create payment retry log"""
        log_dict = log.dict(by_alias=True, exclude={"id"})
        result = self.payment_retry_logs.insert_one(log_dict)
        return str(result.inserted_id)

    def get_retry_logs_by_subscription(self, subscription_id: str) -> List[PaymentRetryLog]:
        """Get retry logs for subscription"""
        docs = self.payment_retry_logs.find({"subscription_id": subscription_id}).sort("retry_date", DESCENDING)
        return [PaymentRetryLog(**doc) for doc in docs]

    # ===== Webhook Events =====

    def create_webhook_event(self, event: WebhookEvent) -> str:
        """Create webhook event"""
        event_dict = event.dict(by_alias=True, exclude={"id"})
        result = self.webhook_events.insert_one(event_dict)
        return str(result.inserted_id)

    def get_webhook_event(self, event_id: str) -> Optional[WebhookEvent]:
        """Get webhook event by Stripe event ID"""
        doc = self.webhook_events.find_one({"event_id": event_id})
        return WebhookEvent(**doc) if doc else None

    def mark_webhook_processed(self, webhook_id: str) -> bool:
        """Mark webhook as processed"""
        result = self.webhook_events.update_one(
            {"_id": ObjectId(webhook_id)},
            {"$set": {"is_processed": True, "processed_at": datetime.utcnow()}}
        )
        return result.modified_count > 0

    # ===== Billing Invoices =====

    def create_billing_invoice(self, invoice: BillingInvoice) -> str:
        """Create billing invoice"""
        invoice_dict = invoice.dict(by_alias=True, exclude={"id"})
        result = self.billing_invoices.insert_one(invoice_dict)
        return str(result.inserted_id)

    def get_billing_invoice(self, invoice_id: str) -> Optional[BillingInvoice]:
        """Get billing invoice by ID"""
        doc = self.billing_invoices.find_one({"_id": ObjectId(invoice_id)})
        return BillingInvoice(**doc) if doc else None

    def get_invoices_by_user(self, user_id: str) -> List[BillingInvoice]:
        """Get billing invoices for user"""
        docs = self.billing_invoices.find({"user_id": user_id}).sort("invoice_date", DESCENDING)
        return [BillingInvoice(**doc) for doc in docs]

    # ===== Statistics =====

    def get_billing_stats(self, from_date: datetime, to_date: datetime) -> Dict[str, Any]:
        """Get billing statistics for period"""
        pipeline = [
            {
                "$match": {
                    "transaction_date": {"$gte": from_date, "$lte": to_date},
                    "status": PaymentStatus.SUCCEEDED
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_revenue": {"$sum": "$amount"},
                    "total_transactions": {"$sum": 1},
                    "avg_transaction": {"$avg": "$amount"},
                }
            }
        ]

        result = list(self.payment_transactions.aggregate(pipeline))

        if result:
            return result[0]

        return {
            "total_revenue": 0.0,
            "total_transactions": 0,
            "avg_transaction": 0.0,
        }
