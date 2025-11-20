"""
Billing Automation Service
Handles monthly billing, payment retries, and account suspension
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging

from app.models.billing import (
    Subscription,
    PaymentTransaction,
    PaymentRetryLog,
    BillingCycle,
    SubscriptionStatus,
    PaymentStatus,
    RetryStatus,
)
from app.repos.billing_repo import BillingRepository
from app.services.stripe_service import StripeService

logger = logging.getLogger(__name__)


class BillingAutomationService:
    """
    Automated billing service for monthly charges and payment retries
    """

    def __init__(self, billing_repo: BillingRepository, stripe_service: StripeService):
        self.billing_repo = billing_repo
        self.stripe_service = stripe_service

        # Configuration
        self.MAX_RETRY_ATTEMPTS = 5
        self.RETRY_INTERVAL_DAYS = 1  # Retry every day
        self.SUSPENSION_AFTER_DAYS = 5  # Suspend after 5 failed retries

    def process_monthly_billing(self) -> Dict[str, int]:
        """
        Process all subscriptions due for monthly billing

        Returns:
            Statistics dict with billing results
        """
        stats = {
            "total_processed": 0,
            "successful_charges": 0,
            "failed_charges": 0,
            "skipped": 0,
        }

        # Get subscriptions due for billing
        subscriptions = self.billing_repo.get_subscriptions_for_billing()

        logger.info(f"Processing monthly billing for {len(subscriptions)} subscriptions")

        for subscription in subscriptions:
            stats["total_processed"] += 1

            try:
                # Attempt to charge subscription
                transaction = self.charge_subscription(subscription)

                if transaction and transaction.status == PaymentStatus.SUCCEEDED:
                    stats["successful_charges"] += 1
                else:
                    stats["failed_charges"] += 1
                    # Mark subscription as past due
                    self.billing_repo.update_subscription(
                        str(subscription.id),
                        {
                            "status": SubscriptionStatus.PAST_DUE,
                            "next_retry_date": datetime.utcnow() + timedelta(days=1),
                        },
                    )

            except Exception as e:
                logger.error(
                    f"Error processing billing for subscription {subscription.id}: {e}"
                )
                stats["failed_charges"] += 1

        logger.info(f"Monthly billing completed: {stats}")
        return stats

    def charge_subscription(
        self, subscription: Subscription
    ) -> Optional[PaymentTransaction]:
        """
        Charge a subscription

        Args:
            subscription: Subscription to charge

        Returns:
            PaymentTransaction if successful, None otherwise
        """
        try:
            # Create billing cycle record
            cycle_start = subscription.current_period_start
            cycle_end = subscription.current_period_end

            billing_cycle = BillingCycle(
                user_id=subscription.user_id,
                organization_id=subscription.organization_id,
                subscription_id=str(subscription.id),
                cycle_start=cycle_start,
                cycle_end=cycle_end,
                billing_date=datetime.utcnow(),
                base_amount=subscription.amount,
                total_amount=subscription.amount,
                currency=subscription.currency,
                status=PaymentStatus.PENDING,
            )

            cycle_id = self.billing_repo.create_billing_cycle(billing_cycle)

            # Attempt payment via Stripe
            description = (
                f"Monthly subscription charge - "
                f"{subscription.tier.value} plan "
                f"({cycle_start.strftime('%Y-%m-%d')} to {cycle_end.strftime('%Y-%m-%d')})"
            )

            transaction = self.stripe_service.charge_subscription(
                subscription, description
            )

            # Update billing cycle with transaction
            if transaction:
                self.billing_repo.db["billing_cycles"].update_one(
                    {"_id": cycle_id},
                    {
                        "$set": {
                            "payment_transaction_id": str(transaction.id),
                            "status": transaction.status,
                            "paid_at": (
                                datetime.utcnow()
                                if transaction.status == PaymentStatus.SUCCEEDED
                                else None
                            ),
                        }
                    },
                )

            return transaction

        except Exception as e:
            logger.error(f"Error charging subscription {subscription.id}: {e}")
            return None

    def process_payment_retries(self) -> Dict[str, int]:
        """
        Process payment retries for failed subscriptions

        Retry logic:
        - Attempt 1: After 1 day
        - Attempt 2: After 2 days
        - Attempt 3: After 3 days
        - Attempt 4: After 4 days
        - Attempt 5: After 5 days
        - After 5 failed attempts: Suspend account

        Returns:
            Statistics dict with retry results
        """
        stats = {
            "total_processed": 0,
            "successful_retries": 0,
            "failed_retries": 0,
            "suspended_accounts": 0,
        }

        # Get subscriptions needing retry
        subscriptions = self.billing_repo.get_subscriptions_for_retry()

        logger.info(f"Processing payment retries for {len(subscriptions)} subscriptions")

        for subscription in subscriptions:
            stats["total_processed"] += 1

            try:
                # Check if we've exceeded max retries
                if subscription.retry_attempt >= self.MAX_RETRY_ATTEMPTS:
                    # Suspend account
                    self._suspend_account(subscription)
                    stats["suspended_accounts"] += 1
                    continue

                # Increment retry attempt
                current_attempt = self.billing_repo.increment_retry_attempt(
                    str(subscription.id)
                )

                logger.info(
                    f"Retry attempt {current_attempt}/{self.MAX_RETRY_ATTEMPTS} "
                    f"for subscription {subscription.id}"
                )

                # Attempt to charge
                transaction = self.charge_subscription(subscription)

                # Create retry log
                retry_log = PaymentRetryLog(
                    user_id=subscription.user_id,
                    organization_id=subscription.organization_id,
                    subscription_id=str(subscription.id),
                    payment_transaction_id=(
                        str(transaction.id) if transaction else None
                    ),
                    retry_number=current_attempt,
                    retry_date=datetime.utcnow(),
                    next_retry_date=(
                        None
                        if transaction and transaction.status == PaymentStatus.SUCCEEDED
                        else datetime.utcnow()
                        + timedelta(days=self.RETRY_INTERVAL_DAYS)
                    ),
                    status=(
                        transaction.status
                        if transaction
                        else PaymentStatus.FAILED
                    ),
                    success=(
                        transaction.status == PaymentStatus.SUCCEEDED
                        if transaction
                        else False
                    ),
                    error_message=(
                        None
                        if transaction and transaction.status == PaymentStatus.SUCCEEDED
                        else transaction.failure_message if transaction else "Payment failed"
                    ),
                    action_taken=(
                        "payment_succeeded"
                        if transaction and transaction.status == PaymentStatus.SUCCEEDED
                        else f"retry_scheduled_attempt_{current_attempt}"
                    ),
                )

                self.billing_repo.create_retry_log(retry_log)

                # Update statistics
                if transaction and transaction.status == PaymentStatus.SUCCEEDED:
                    stats["successful_retries"] += 1

                    # Reset retry counter
                    self.billing_repo.update_subscription(
                        str(subscription.id),
                        {
                            "retry_attempt": 0,
                            "retry_status": RetryStatus.COMPLETED,
                            "status": SubscriptionStatus.ACTIVE,
                            "last_payment_date": datetime.utcnow(),
                        },
                    )

                    # TODO: Send success email to user

                else:
                    stats["failed_retries"] += 1

                    # Check if this was the last attempt
                    if current_attempt >= self.MAX_RETRY_ATTEMPTS:
                        self._suspend_account(subscription)
                        stats["suspended_accounts"] += 1

            except Exception as e:
                logger.error(
                    f"Error processing retry for subscription {subscription.id}: {e}"
                )
                stats["failed_retries"] += 1

        logger.info(f"Payment retries completed: {stats}")
        return stats

    def _suspend_account(self, subscription: Subscription) -> None:
        """
        Suspend account after max retry attempts

        Args:
            subscription: Subscription to suspend
        """
        try:
            reason = (
                f"Account suspended after {self.MAX_RETRY_ATTEMPTS} failed payment attempts"
            )

            # Suspend subscription
            self.billing_repo.suspend_subscription(str(subscription.id), reason)

            # Create final retry log
            retry_log = PaymentRetryLog(
                user_id=subscription.user_id,
                organization_id=subscription.organization_id,
                subscription_id=str(subscription.id),
                retry_number=subscription.retry_attempt,
                retry_date=datetime.utcnow(),
                status=PaymentStatus.FAILED,
                success=False,
                error_message="Max retry attempts exceeded",
                action_taken="account_suspended",
            )

            self.billing_repo.create_retry_log(retry_log)

            logger.warning(
                f"Account suspended for subscription {subscription.id} after "
                f"{self.MAX_RETRY_ATTEMPTS} failed payment attempts"
            )

            # TODO: Send suspension email to user
            # TODO: Disable user access to features

        except Exception as e:
            logger.error(f"Error suspending account for subscription {subscription.id}: {e}")

    def reactivate_subscription(
        self, subscription_id: str, payment_method_id: Optional[str] = None
    ) -> bool:
        """
        Reactivate a suspended subscription

        Args:
            subscription_id: Subscription ID
            payment_method_id: New payment method ID (optional)

        Returns:
            Success boolean
        """
        try:
            subscription = self.billing_repo.get_subscription(subscription_id)

            if not subscription:
                raise ValueError("Subscription not found")

            # If new payment method provided, update it
            if payment_method_id:
                # TODO: Update payment method via Stripe
                pass

            # Attempt to charge
            transaction = self.charge_subscription(subscription)

            if transaction and transaction.status == PaymentStatus.SUCCEEDED:
                # Unsuspend subscription
                self.billing_repo.unsuspend_subscription(subscription_id)

                logger.info(f"Subscription {subscription_id} reactivated successfully")
                return True
            else:
                logger.error(f"Failed to charge subscription {subscription_id} for reactivation")
                return False

        except Exception as e:
            logger.error(f"Error reactivating subscription {subscription_id}: {e}")
            return False

    def check_subscription_features(self, user_id: str) -> bool:
        """
        Check if user's subscription allows feature access

        Args:
            user_id: User ID

        Returns:
            True if user can access features, False if suspended
        """
        subscription = self.billing_repo.get_subscription_by_user(user_id)

        if not subscription:
            return False  # No subscription = no access

        # Check if suspended
        if subscription.is_suspended:
            return False

        # Check if status allows access
        allowed_statuses = [
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.PAST_DUE,  # Allow access during grace period
        ]

        return subscription.status in allowed_statuses

    def get_subscription_status_summary(
        self, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get subscription status summary for user

        Args:
            user_id: User ID

        Returns:
            Summary dict or None
        """
        subscription = self.billing_repo.get_subscription_by_user(user_id)

        if not subscription:
            return None

        summary = {
            "status": subscription.status,
            "is_suspended": subscription.is_suspended,
            "tier": subscription.tier,
            "billing_interval": subscription.billing_interval,
            "amount": subscription.amount,
            "next_payment_date": subscription.next_payment_date,
            "failed_payment_count": subscription.failed_payment_count,
            "retry_attempt": subscription.retry_attempt,
            "can_access_features": not subscription.is_suspended,
        }

        # Add warning if in grace period
        if subscription.status == SubscriptionStatus.PAST_DUE:
            days_until_suspension = (
                self.MAX_RETRY_ATTEMPTS - subscription.retry_attempt
            )
            summary["warning"] = (
                f"Payment failed. {days_until_suspension} days until account suspension."
            )

        # Add suspension details if suspended
        if subscription.is_suspended:
            summary["suspended_at"] = subscription.suspended_at
            summary["suspension_reason"] = subscription.suspension_reason

        return summary
