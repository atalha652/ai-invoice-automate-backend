"""
Scheduled Billing Tasks
Runs monthly billing and payment retries automatically
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging

from app.repos.billing_repo import BillingRepository
from app.services.stripe_service import StripeService
from app.services.billing_automation_service import BillingAutomationService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def init_scheduled_tasks(db):
    """Initialize scheduled billing tasks"""
    billing_repo = BillingRepository(db)
    stripe_service = StripeService(billing_repo)
    billing_automation = BillingAutomationService(billing_repo, stripe_service)

    # Schedule monthly billing - runs daily at 2 AM
    scheduler.add_job(
        lambda: run_monthly_billing(billing_automation),
        trigger=CronTrigger(hour=2, minute=0),
        id="monthly_billing",
        name="Process monthly subscription billing",
        replace_existing=True,
    )

    # Schedule payment retries - runs daily at 3 AM
    scheduler.add_job(
        lambda: run_payment_retries(billing_automation),
        trigger=CronTrigger(hour=3, minute=0),
        id="payment_retries",
        name="Process failed payment retries",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduled billing tasks initialized")


def run_monthly_billing(billing_automation: BillingAutomationService):
    """Run monthly billing process"""
    try:
        logger.info("Starting monthly billing process...")
        stats = billing_automation.process_monthly_billing()
        logger.info(f"Monthly billing completed: {stats}")
    except Exception as e:
        logger.error(f"Error in monthly billing: {e}")


def run_payment_retries(billing_automation: BillingAutomationService):
    """Run payment retry process"""
    try:
        logger.info("Starting payment retry process...")
        stats = billing_automation.process_payment_retries()
        logger.info(f"Payment retries completed: {stats}")
    except Exception as e:
        logger.error(f"Error in payment retries: {e}")


def shutdown_scheduler():
    """Shutdown scheduler gracefully"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shutdown complete")
