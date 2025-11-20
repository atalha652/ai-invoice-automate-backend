"""
Billing and Subscription API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from typing import List, Optional
from pymongo import MongoClient
import os
import certifi
from dotenv import load_dotenv
from datetime import datetime

from app.models.billing import (
    SubscriptionPlan,
    Subscription,
    SubscriptionCreate,
    PaymentMethod,
    PaymentMethodCreate,
    PaymentTransaction,
    BillingCycle,
    WebhookEvent,
    PaymentProvider,
    PaymentStatus,
)
from app.repos.billing_repo import BillingRepository
from app.services.stripe_service import StripeService
from app.services.billing_automation_service import BillingAutomationService
from app.routes.auth import get_current_user

# Load environment
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

# Database connection
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]

router = APIRouter(tags=["Billing & Subscriptions"])


# Helper function to convert ObjectId to string
def ensure_str(value):
    """Convert ObjectId or any value to string"""
    return str(value) if not isinstance(value, str) else value


# ===== Subscription Plans =====

@router.get("/billing/plans", response_model=List[dict])
async def list_subscription_plans():
    """Get all active subscription plans"""
    billing_repo = BillingRepository(db)
    plans = billing_repo.get_active_plans()
    return [p.dict(by_alias=True) for p in plans]


# ===== User Subscription =====

@router.post("/billing/subscribe", response_model=dict)
async def create_subscription(
    subscription_create: SubscriptionCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new subscription"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)

    # Convert ObjectId to string
    user_id = str(current_user["_id"]) if not isinstance(current_user["_id"], str) else current_user["_id"]
    org_id = current_user.get("organization_id") or current_user["_id"]
    org_id = str(org_id) if not isinstance(org_id, str) else org_id

    # Get plan
    plan = billing_repo.get_plan(subscription_create.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Create or get Stripe customer
    stripe_customer_id = stripe_service.create_or_get_customer(
        user_id=user_id,
        email=current_user["email"],
        name=current_user.get("name", ""),
        metadata={
            "user_id": user_id,
            "organization_id": org_id,
        }
    )

    # Create subscription
    subscription = stripe_service.create_subscription(
        user_id=user_id,
        organization_id=org_id,
        plan=plan,
        stripe_customer_id=stripe_customer_id,
        payment_method_id=subscription_create.payment_method_id,
        trial_days=subscription_create.trial_days,
        billing_interval=subscription_create.billing_interval,
    )

    return {
        "subscription_id": str(subscription.id),
        "status": subscription.status,
        "next_payment_date": subscription.next_payment_date.isoformat(),
        "message": "Subscription created successfully",
    }


@router.get("/billing/subscription", response_model=dict)
async def get_my_subscription(
    current_user: dict = Depends(get_current_user),
):
    """Get current user's subscription"""
    billing_repo = BillingRepository(db)
    user_id = ensure_str(current_user["_id"])
    subscription = billing_repo.get_subscription_by_user(user_id)

    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found")

    return subscription.dict(by_alias=True)


@router.post("/billing/subscription/cancel", response_model=dict)
async def cancel_subscription(
    cancel_immediately: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Cancel subscription"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)
    user_id = ensure_str(current_user["_id"])
    subscription = billing_repo.get_subscription_by_user(user_id)

    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found")

    success = stripe_service.cancel_subscription(
        str(subscription.id), cancel_immediately
    )

    return {
        "message": "Subscription canceled successfully",
        "canceled_immediately": cancel_immediately,
    }


# ===== Payment Methods =====

@router.post("/billing/payment-methods", response_model=dict)
async def add_payment_method(
    payment_method: PaymentMethodCreate,
    current_user: dict = Depends(get_current_user),
):
    """Add payment method"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)

    user_id = ensure_str(current_user["_id"])
    org_id = ensure_str(current_user.get("organization_id") or current_user["_id"])

    # Get or create Stripe customer
    subscription = billing_repo.get_subscription_by_user(user_id)

    if not subscription:
        raise HTTPException(status_code=400, detail="No active subscription")

    # Attach payment method
    pm = stripe_service.attach_payment_method(
        user_id=user_id,
        organization_id=org_id,
        stripe_customer_id=subscription.stripe_customer_id,
        payment_method_id=payment_method.stripe_payment_method_id,
        set_as_default=payment_method.set_as_default,
    )

    return {
        "payment_method_id": str(pm.id),
        "message": "Payment method added successfully",
    }


@router.get("/billing/payment-methods", response_model=List[dict])
async def list_payment_methods(
    current_user: dict = Depends(get_current_user),
):
    """Get user's payment methods"""
    billing_repo = BillingRepository(db)
    user_id = ensure_str(current_user["_id"])
    methods = billing_repo.get_payment_methods_by_user(user_id)
    return [m.dict(by_alias=True) for m in methods]


# ===== Payment History =====

@router.get("/billing/transactions", response_model=List[dict])
async def list_payment_transactions(
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Get payment transaction history"""
    billing_repo = BillingRepository(db)
    user_id = ensure_str(current_user["_id"])
    transactions = billing_repo.get_transactions_by_user(user_id, limit)
    return [t.dict(by_alias=True) for t in transactions]


@router.get("/billing/billing-cycles", response_model=List[dict])
async def list_billing_cycles(
    current_user: dict = Depends(get_current_user),
):
    """Get billing cycle history"""
    billing_repo = BillingRepository(db)
    user_id = ensure_str(current_user["_id"])
    subscription = billing_repo.get_subscription_by_user(user_id)

    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    cycles = billing_repo.get_billing_cycles_by_subscription(str(subscription.id))
    return [c.dict(by_alias=True) for c in cycles]


# ===== Subscription Status =====

@router.get("/billing/status", response_model=dict)
async def get_subscription_status(
    current_user: dict = Depends(get_current_user),
):
    """Get subscription status summary"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)
    billing_automation = BillingAutomationService(billing_repo, stripe_service)

    user_id = ensure_str(current_user["_id"])
    summary = billing_automation.get_subscription_status_summary(user_id)

    if not summary:
        raise HTTPException(status_code=404, detail="No subscription found")

    # Convert datetime objects to strings
    if "next_payment_date" in summary and summary["next_payment_date"]:
        summary["next_payment_date"] = summary["next_payment_date"].isoformat()
    if "suspended_at" in summary and summary["suspended_at"]:
        summary["suspended_at"] = summary["suspended_at"].isoformat()

    return summary


@router.get("/billing/check-access", response_model=dict)
async def check_feature_access(
    current_user: dict = Depends(get_current_user),
):
    """Check if user can access features"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)
    billing_automation = BillingAutomationService(billing_repo, stripe_service)

    user_id = ensure_str(current_user["_id"])
    can_access = billing_automation.check_subscription_features(user_id)

    return {
        "can_access_features": can_access,
        "message": (
            "Access granted" if can_access else "Access denied - subscription inactive or suspended"
        ),
    }


# ===== Stripe Webhooks =====

@router.post("/billing/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
):
    """Handle Stripe webhook events"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)

    payload = await request.body()

    try:
        # Verify webhook signature
        event = stripe_service.construct_webhook_event(payload, stripe_signature)

        # Save webhook event
        webhook_event = WebhookEvent(
            event_id=event["id"],
            event_type=event["type"],
            provider=PaymentProvider.STRIPE,
            payload=event,
        )

        webhook_id = billing_repo.create_webhook_event(webhook_event)

        # Process event
        await process_stripe_webhook(event, billing_repo, stripe_service)

        # Mark as processed
        billing_repo.mark_webhook_processed(webhook_id)

        return {"status": "success"}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def process_stripe_webhook(event: dict, billing_repo: BillingRepository, stripe_service: StripeService):
    """Process Stripe webhook events"""
    event_type = event["type"]

    if event_type == "payment_intent.succeeded":
        # Update payment transaction
        payment_intent = event["data"]["object"]
        transaction = billing_repo.get_transaction_by_payment_intent(payment_intent["id"])

        if transaction:
            billing_repo.update_payment_transaction(
                str(transaction.id),
                {
                    "status": PaymentStatus.SUCCEEDED,
                    "succeeded_at": datetime.utcnow(),
                }
            )

    elif event_type == "payment_intent.payment_failed":
        # Handle failed payment
        payment_intent = event["data"]["object"]
        transaction = billing_repo.get_transaction_by_payment_intent(payment_intent["id"])

        if transaction:
            billing_repo.update_payment_transaction(
                str(transaction.id),
                {
                    "status": PaymentStatus.FAILED,
                    "failed_at": datetime.utcnow(),
                    "failure_code": payment_intent.get("last_payment_error", {}).get("code"),
                    "failure_message": payment_intent.get("last_payment_error", {}).get("message"),
                }
            )

    elif event_type == "customer.subscription.deleted":
        # Handle subscription cancellation
        subscription_data = event["data"]["object"]
        subscription = billing_repo.get_subscription_by_stripe_id(subscription_data["id"])

        if subscription:
            from app.models.billing import SubscriptionStatus
            billing_repo.update_subscription(
                str(subscription.id),
                {
                    "status": SubscriptionStatus.CANCELED,
                    "ended_at": datetime.utcnow(),
                }
            )
