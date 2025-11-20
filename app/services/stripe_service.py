"""
Stripe Payment Service
Handles all Stripe payment integration including subscriptions, payments, and webhooks
"""

import os
import stripe
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import logging

from app.models.billing import (
    Subscription,
    SubscriptionPlan,
    PaymentMethod,
    PaymentTransaction,
    BillingCycle,
    PaymentStatus,
    SubscriptionStatus,
    BillingInterval,
    PaymentIntentStatus,
    PaymentProvider,
)
from app.repos.billing_repo import BillingRepository

logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


class StripeService:
    """
    Service for handling Stripe payment operations
    """

    def __init__(self, billing_repo: BillingRepository):
        self.billing_repo = billing_repo
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    # ===== Customer Management =====

    def create_or_get_customer(
        self, user_id: str, email: str, name: str, metadata: Optional[Dict] = None
    ) -> str:
        """
        Create or retrieve Stripe customer

        Returns:
            Stripe customer ID
        """
        try:
            # Check if user already has a subscription with Stripe customer
            subscription = self.billing_repo.get_subscription_by_user(user_id)

            if subscription and subscription.stripe_customer_id:
                return subscription.stripe_customer_id

            # Create new Stripe customer
            customer_data = {
                "email": email,
                "name": name,
                "metadata": metadata or {"user_id": user_id},
            }

            customer = stripe.Customer.create(**customer_data)

            logger.info(f"Created Stripe customer {customer.id} for user {user_id}")
            return customer.id

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating customer: {e}")
            raise Exception(f"Failed to create Stripe customer: {str(e)}")

    # ===== Payment Method Management =====

    def attach_payment_method(
        self,
        user_id: str,
        organization_id: str,
        stripe_customer_id: str,
        payment_method_id: str,
        set_as_default: bool = True,
    ) -> PaymentMethod:
        """
        Attach payment method to customer and save to database

        Args:
            user_id: User ID
            organization_id: Organization ID
            stripe_customer_id: Stripe customer ID
            payment_method_id: Stripe payment method ID
            set_as_default: Set as default payment method

        Returns:
            PaymentMethod object
        """
        try:
            # Attach payment method to customer in Stripe
            payment_method = stripe.PaymentMethod.attach(
                payment_method_id, customer=stripe_customer_id
            )

            # Set as default if requested
            if set_as_default:
                stripe.Customer.modify(
                    stripe_customer_id,
                    invoice_settings={"default_payment_method": payment_method_id},
                )

            # Extract payment method details
            pm_type = payment_method.type
            card_brand = None
            card_last4 = None
            card_exp_month = None
            card_exp_year = None
            iban_last4 = None

            if pm_type == "card":
                card_brand = payment_method.card.brand
                card_last4 = payment_method.card.last4
                card_exp_month = payment_method.card.exp_month
                card_exp_year = payment_method.card.exp_year
            elif pm_type == "sepa_debit":
                iban_last4 = payment_method.sepa_debit.last4

            # Create payment method record
            pm_record = PaymentMethod(
                user_id=user_id,
                organization_id=organization_id,
                provider=PaymentProvider.STRIPE,
                stripe_payment_method_id=payment_method_id,
                stripe_customer_id=stripe_customer_id,
                type=pm_type,
                card_brand=card_brand,
                card_last4=card_last4,
                card_exp_month=card_exp_month,
                card_exp_year=card_exp_year,
                iban_last4=iban_last4,
                is_default=set_as_default,
                is_active=True,
                is_verified=True,
            )

            # Save to database
            pm_id = self.billing_repo.create_payment_method(pm_record)

            # If set as default, update other payment methods
            if set_as_default:
                self.billing_repo.set_default_payment_method(user_id, pm_id)

            logger.info(
                f"Attached payment method {payment_method_id} to customer {stripe_customer_id}"
            )

            pm_record.id = pm_id
            return pm_record

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error attaching payment method: {e}")
            raise Exception(f"Failed to attach payment method: {str(e)}")

    # ===== Subscription Management =====

    def create_subscription(
        self,
        user_id: str,
        organization_id: str,
        plan: SubscriptionPlan,
        stripe_customer_id: str,
        payment_method_id: Optional[str] = None,
        trial_days: int = 0,
        billing_interval: BillingInterval = BillingInterval.MONTHLY,
    ) -> Subscription:
        """
        Create Stripe subscription

        Args:
            user_id: User ID
            organization_id: Organization ID
            plan: Subscription plan
            stripe_customer_id: Stripe customer ID
            payment_method_id: Stripe payment method ID
            trial_days: Number of trial days
            billing_interval: Billing interval (monthly/yearly)

        Returns:
            Subscription object
        """
        try:
            # Select the correct Stripe price ID
            stripe_price_id = (
                plan.stripe_price_id_yearly
                if billing_interval == BillingInterval.YEARLY
                else plan.stripe_price_id_monthly
            )

            if not stripe_price_id:
                raise ValueError(
                    f"No Stripe price ID configured for {billing_interval} billing"
                )

            # Prepare subscription data
            subscription_data = {
                "customer": stripe_customer_id,
                "items": [{"price": stripe_price_id}],
                "metadata": {
                    "user_id": user_id,
                    "organization_id": organization_id,
                    "plan_id": str(plan.id),
                },
            }

            # Add payment method if provided
            if payment_method_id:
                subscription_data["default_payment_method"] = payment_method_id

            # Add trial period if applicable
            if trial_days > 0:
                subscription_data["trial_period_days"] = trial_days

            # Create subscription in Stripe
            stripe_subscription = stripe.Subscription.create(**subscription_data)

            # Calculate dates
            now = datetime.utcnow()
            current_period_start = datetime.fromtimestamp(
                stripe_subscription.current_period_start
            )
            current_period_end = datetime.fromtimestamp(
                stripe_subscription.current_period_end
            )
            trial_end = (
                datetime.fromtimestamp(stripe_subscription.trial_end)
                if stripe_subscription.trial_end
                else None
            )

            # Determine amount
            amount = (
                plan.price_yearly
                if billing_interval == BillingInterval.YEARLY
                else plan.price_monthly
            )

            # Create subscription record
            subscription = Subscription(
                user_id=user_id,
                organization_id=organization_id,
                plan_id=str(plan.id),
                tier=plan.tier,
                stripe_subscription_id=stripe_subscription.id,
                stripe_customer_id=stripe_customer_id,
                stripe_price_id=stripe_price_id,
                status=(
                    SubscriptionStatus.TRIALING
                    if trial_days > 0
                    else SubscriptionStatus.ACTIVE
                ),
                billing_interval=billing_interval,
                start_date=now,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                trial_end=trial_end,
                next_payment_date=trial_end or current_period_end,
                amount=amount,
                currency=plan.currency,
                features_enabled=plan.features,
            )

            # Save to database
            sub_id = self.billing_repo.create_subscription(subscription)
            subscription.id = sub_id

            logger.info(
                f"Created subscription {stripe_subscription.id} for user {user_id}"
            )

            return subscription

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating subscription: {e}")
            raise Exception(f"Failed to create subscription: {str(e)}")

    def cancel_subscription(
        self, subscription_id: str, cancel_immediately: bool = False
    ) -> bool:
        """
        Cancel Stripe subscription

        Args:
            subscription_id: Our subscription ID
            cancel_immediately: If True, cancel now. If False, cancel at period end

        Returns:
            Success boolean
        """
        try:
            subscription = self.billing_repo.get_subscription(subscription_id)

            if not subscription or not subscription.stripe_subscription_id:
                raise ValueError("Subscription not found")

            # Cancel in Stripe
            if cancel_immediately:
                stripe.Subscription.delete(subscription.stripe_subscription_id)
            else:
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=True,
                )

            # Update in database
            self.billing_repo.update_subscription(
                subscription_id,
                {
                    "status": SubscriptionStatus.CANCELED,
                    "canceled_at": datetime.utcnow(),
                    "ended_at": (
                        datetime.utcnow()
                        if cancel_immediately
                        else subscription.current_period_end
                    ),
                },
            )

            logger.info(f"Canceled subscription {subscription.stripe_subscription_id}")
            return True

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error canceling subscription: {e}")
            raise Exception(f"Failed to cancel subscription: {str(e)}")

    # ===== Payment Processing =====

    def create_payment_intent(
        self,
        user_id: str,
        organization_id: str,
        amount: float,
        currency: str,
        description: str,
        stripe_customer_id: str,
        payment_method_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> PaymentTransaction:
        """
        Create Stripe payment intent

        Args:
            user_id: User ID
            organization_id: Organization ID
            amount: Payment amount
            currency: Currency code
            description: Payment description
            stripe_customer_id: Stripe customer ID
            payment_method_id: Stripe payment method ID (optional)
            subscription_id: Subscription ID (optional)
            metadata: Additional metadata

        Returns:
            PaymentTransaction object
        """
        try:
            # Convert amount to cents (Stripe uses smallest currency unit)
            amount_cents = int(amount * 100)

            # Prepare payment intent data
            intent_data = {
                "amount": amount_cents,
                "currency": currency.lower(),
                "customer": stripe_customer_id,
                "description": description,
                "metadata": metadata or {},
            }

            # Add payment method if provided
            if payment_method_id:
                intent_data["payment_method"] = payment_method_id
                intent_data["confirm"] = True  # Auto-confirm if payment method provided
                intent_data["automatic_payment_methods"] = {"enabled": True, "allow_redirects": "never"}

            # Create payment intent in Stripe
            payment_intent = stripe.PaymentIntent.create(**intent_data)

            # Create transaction record
            transaction = PaymentTransaction(
                user_id=user_id,
                organization_id=organization_id,
                subscription_id=subscription_id,
                transaction_id=f"txn_{datetime.utcnow().timestamp()}",
                stripe_payment_intent_id=payment_intent.id,
                stripe_customer_id=stripe_customer_id,
                provider=PaymentProvider.STRIPE,
                payment_method_id=payment_method_id,
                status=self._map_stripe_status_to_payment_status(payment_intent.status),
                intent_status=PaymentIntentStatus(payment_intent.status),
                amount=amount,
                currency=currency,
                net_amount=amount,
                description=description,
                metadata=metadata,
            )

            # Save to database
            trans_id = self.billing_repo.create_payment_transaction(transaction)
            transaction.id = trans_id

            logger.info(f"Created payment intent {payment_intent.id} for user {user_id}")

            return transaction

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating payment intent: {e}")
            raise Exception(f"Failed to create payment intent: {str(e)}")

    def confirm_payment_intent(self, payment_intent_id: str) -> PaymentTransaction:
        """
        Confirm a payment intent

        Args:
            payment_intent_id: Stripe payment intent ID

        Returns:
            Updated PaymentTransaction
        """
        try:
            # Confirm in Stripe
            payment_intent = stripe.PaymentIntent.confirm(payment_intent_id)

            # Update transaction in database
            transaction = self.billing_repo.get_transaction_by_payment_intent(
                payment_intent_id
            )

            if transaction:
                updates = {
                    "status": self._map_stripe_status_to_payment_status(
                        payment_intent.status
                    ),
                    "intent_status": payment_intent.status,
                }

                if payment_intent.status == "succeeded":
                    updates["succeeded_at"] = datetime.utcnow()
                    updates["receipt_url"] = getattr(
                        payment_intent.charges.data[0], "receipt_url", None
                    )

                self.billing_repo.update_payment_transaction(
                    str(transaction.id), updates
                )

                # Refresh transaction
                transaction = self.billing_repo.get_payment_transaction(
                    str(transaction.id)
                )

            return transaction

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error confirming payment intent: {e}")
            raise Exception(f"Failed to confirm payment intent: {str(e)}")

    # ===== Billing & Invoicing =====

    def charge_subscription(
        self, subscription: Subscription, description: str = "Monthly subscription charge"
    ) -> Optional[PaymentTransaction]:
        """
        Charge a subscription's default payment method

        Args:
            subscription: Subscription to charge
            description: Charge description

        Returns:
            PaymentTransaction if successful, None if failed
        """
        try:
            # Get default payment method
            payment_method = self.billing_repo.get_default_payment_method(
                subscription.user_id
            )

            if not payment_method:
                logger.error(
                    f"No payment method found for user {subscription.user_id}"
                )
                return None

            # Create payment intent
            transaction = self.create_payment_intent(
                user_id=subscription.user_id,
                organization_id=subscription.organization_id,
                amount=subscription.amount,
                currency=subscription.currency,
                description=description,
                stripe_customer_id=subscription.stripe_customer_id,
                payment_method_id=payment_method.stripe_payment_method_id,
                subscription_id=str(subscription.id),
                metadata={
                    "subscription_id": str(subscription.id),
                    "billing_period": subscription.billing_interval,
                },
            )

            # Check if payment succeeded
            if transaction.status == PaymentStatus.SUCCEEDED:
                # Update subscription
                self.billing_repo.update_subscription(
                    str(subscription.id),
                    {
                        "last_payment_date": datetime.utcnow(),
                        "next_payment_date": subscription.current_period_end
                        + timedelta(days=30 if subscription.billing_interval == BillingInterval.MONTHLY else 365),
                        "failed_payment_count": 0,
                        "retry_attempt": 0,
                        "retry_status": "pending",
                        "status": SubscriptionStatus.ACTIVE,
                    },
                )

            return transaction

        except Exception as e:
            logger.error(f"Error charging subscription: {e}")
            return None

    # ===== Webhook Processing =====

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        """
        Verify and construct webhook event from Stripe

        Args:
            payload: Raw webhook payload
            sig_header: Stripe signature header

        Returns:
            Verified event data
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )
            return event
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            raise ValueError("Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise ValueError("Invalid signature")

    # ===== Helper Methods =====

    @staticmethod
    def _map_stripe_status_to_payment_status(stripe_status: str) -> PaymentStatus:
        """Map Stripe payment intent status to our PaymentStatus"""
        mapping = {
            "succeeded": PaymentStatus.SUCCEEDED,
            "processing": PaymentStatus.PROCESSING,
            "requires_payment_method": PaymentStatus.FAILED,
            "requires_confirmation": PaymentStatus.PENDING,
            "requires_action": PaymentStatus.PENDING,
            "canceled": PaymentStatus.CANCELED,
        }
        return mapping.get(stripe_status, PaymentStatus.PENDING)
