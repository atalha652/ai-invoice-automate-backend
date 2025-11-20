"""
Payment-Invoice Matching Service
Automatically matches bank payments with invoices using QuickBooks-style matching logic
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any
from difflib import SequenceMatcher
import logging
import re

from app.models.bank_transactions import (
    BankTransaction,
    PaymentInvoiceMatch,
    MatchStatus,
    TransactionStatus,
    TransactionType,
)
from app.repos.bank_repo import BankRepository

logger = logging.getLogger(__name__)


class PaymentMatchingService:
    """
    Service for automatically matching bank payments to invoices
    Similar to QuickBooks automated matching
    """

    def __init__(self, bank_repo: BankRepository, accounting_repo: Any):
        self.bank_repo = bank_repo
        self.accounting_repo = accounting_repo

        # Matching thresholds
        self.EXACT_MATCH_THRESHOLD = 100
        self.HIGH_CONFIDENCE_THRESHOLD = 90
        self.MEDIUM_CONFIDENCE_THRESHOLD = 70
        self.LOW_CONFIDENCE_THRESHOLD = 50

    def match_all_unmatched_transactions(self, organization_id: str) -> Dict[str, int]:
        """
        Match all unmatched transactions for an organization

        Returns:
            Dictionary with matching statistics
        """
        stats = {
            "total_processed": 0,
            "exact_matches": 0,
            "high_confidence_matches": 0,
            "medium_confidence_matches": 0,
            "unmatched": 0,
        }

        # Get all unmatched transactions
        unmatched_transactions = self.bank_repo.get_unmatched_transactions(
            organization_id
        )

        for transaction in unmatched_transactions:
            stats["total_processed"] += 1

            # Attempt to match
            match_result = self.match_transaction(transaction, organization_id)

            if match_result:
                match_score = match_result["score"]

                if match_score >= self.EXACT_MATCH_THRESHOLD:
                    stats["exact_matches"] += 1
                elif match_score >= self.HIGH_CONFIDENCE_THRESHOLD:
                    stats["high_confidence_matches"] += 1
                elif match_score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
                    stats["medium_confidence_matches"] += 1
            else:
                stats["unmatched"] += 1

        logger.info(f"Payment matching completed for org {organization_id}: {stats}")
        return stats

    def match_transaction(
        self, transaction: BankTransaction, organization_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Match a single transaction to an invoice

        Args:
            transaction: Bank transaction to match
            organization_id: Organization ID

        Returns:
            Match result dict with invoice_id, voucher_id, score, and criteria
            None if no match found
        """
        # Get candidate invoices (unpaid or partially paid)
        candidate_invoices = self._get_candidate_invoices(
            organization_id, transaction
        )

        if not candidate_invoices:
            logger.debug(
                f"No candidate invoices found for transaction {transaction.id}"
            )
            return None

        # Score each candidate
        best_match = None
        best_score = 0

        for invoice in candidate_invoices:
            match_score, criteria = self._calculate_match_score(transaction, invoice)

            if match_score > best_score and match_score >= self.LOW_CONFIDENCE_THRESHOLD:
                best_score = match_score
                best_match = {
                    "invoice_id": str(invoice.get("_id")),
                    "voucher_id": invoice.get("voucher_id"),
                    "score": match_score,
                    "criteria": criteria,
                    "invoice": invoice,
                }

        # If we have a good match, create the match record
        if best_match and best_score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            self._create_match_record(transaction, best_match)
            return best_match

        return None

    def _get_candidate_invoices(
        self, organization_id: str, transaction: BankTransaction
    ) -> List[Dict]:
        """
        Get candidate invoices for matching

        Returns invoices that:
        - Are from the same organization
        - Are unpaid or partially paid
        - Have similar amounts (+/- 5%)
        - Are dated within 90 days of the transaction
        """
        # Calculate amount range (±5%)
        amount_min = transaction.amount * 0.95
        amount_max = transaction.amount * 1.05

        # Calculate date range (90 days before transaction)
        date_min = transaction.transaction_date - timedelta(days=90)

        # Query accounting database for candidate invoices
        # This is a placeholder - actual implementation depends on your invoice schema
        query = {
            "organization_id": organization_id,
            "status": {"$in": ["unpaid", "partially_paid", "pending"]},
            "total_amount": {"$gte": amount_min, "$lte": amount_max},
            "invoice_date": {"$gte": date_min, "$lte": transaction.transaction_date},
        }

        try:
            # Try to get invoices from vouchers collection
            candidates = list(
                self.accounting_repo.db["voucher"].find(query).limit(20)
            )
            return candidates
        except Exception as e:
            logger.error(f"Error querying candidate invoices: {e}")
            return []

    def _calculate_match_score(
        self, transaction: BankTransaction, invoice: Dict
    ) -> Tuple[float, List[str]]:
        """
        Calculate matching score between transaction and invoice

        Scoring criteria:
        - Amount match: 40 points
        - Reference/invoice number: 30 points
        - Counterparty name match: 20 points
        - Date proximity: 10 points

        Returns:
            Tuple of (score, matched_criteria_list)
        """
        score = 0.0
        criteria_matched = []

        # 1. Amount matching (40 points)
        transaction_amount = transaction.amount
        invoice_amount = float(invoice.get("total_amount", 0))

        amount_diff_pct = abs(transaction_amount - invoice_amount) / invoice_amount

        if amount_diff_pct < 0.01:  # Within 1%
            score += 40
            criteria_matched.append("exact_amount")
        elif amount_diff_pct < 0.03:  # Within 3%
            score += 35
            criteria_matched.append("close_amount")
        elif amount_diff_pct < 0.05:  # Within 5%
            score += 25
            criteria_matched.append("similar_amount")

        # 2. Reference/Invoice Number matching (30 points)
        transaction_ref = (transaction.reference or "").strip().upper()
        invoice_number = str(invoice.get("invoice_number", "")).strip().upper()
        voucher_number = str(invoice.get("voucher_number", "")).strip().upper()

        if transaction_ref and (
            invoice_number in transaction_ref or voucher_number in transaction_ref
        ):
            score += 30
            criteria_matched.append("reference_match")
        elif transaction_ref and invoice_number:
            # Check for partial match
            similarity = SequenceMatcher(None, transaction_ref, invoice_number).ratio()
            if similarity > 0.7:
                score += 20
                criteria_matched.append("partial_reference_match")

        # Also check description for invoice number
        transaction_desc = (transaction.description or "").strip().upper()
        if invoice_number and invoice_number in transaction_desc:
            score += 15
            criteria_matched.append("description_match")

        # 3. Counterparty Name matching (20 points)
        counterparty_name = (transaction.counterparty_name or "").strip().upper()
        invoice_customer = str(invoice.get("customer_name", "")).strip().upper()
        invoice_supplier = str(invoice.get("supplier_name", "")).strip().upper()

        if counterparty_name and (
            counterparty_name in invoice_customer or counterparty_name in invoice_supplier
        ):
            score += 20
            criteria_matched.append("name_exact_match")
        elif counterparty_name and (invoice_customer or invoice_supplier):
            # Check for partial name match
            name_to_check = invoice_customer or invoice_supplier
            similarity = SequenceMatcher(None, counterparty_name, name_to_check).ratio()
            if similarity > 0.7:
                score += 15
                criteria_matched.append("name_partial_match")

        # 4. Date proximity (10 points)
        invoice_date = invoice.get("invoice_date") or invoice.get("voucher_date")

        if invoice_date:
            if isinstance(invoice_date, str):
                invoice_date = datetime.fromisoformat(invoice_date.replace("Z", "+00:00"))

            days_diff = abs((transaction.transaction_date - invoice_date).days)

            if days_diff <= 7:  # Within 1 week
                score += 10
                criteria_matched.append("date_exact")
            elif days_diff <= 30:  # Within 1 month
                score += 7
                criteria_matched.append("date_close")
            elif days_diff <= 60:  # Within 2 months
                score += 4
                criteria_matched.append("date_similar")

        return score, criteria_matched

    def _create_match_record(
        self, transaction: BankTransaction, match_result: Dict
    ) -> None:
        """
        Create payment-invoice match record and update transaction

        Args:
            transaction: Bank transaction
            match_result: Match result dict
        """
        try:
            # Determine match status based on score
            score = match_result["score"]

            if score >= self.EXACT_MATCH_THRESHOLD:
                match_status = MatchStatus.AUTO_MATCHED
            elif score >= self.HIGH_CONFIDENCE_THRESHOLD:
                match_status = MatchStatus.AUTO_MATCHED
            else:
                match_status = MatchStatus.PARTIALLY_MATCHED

            # Create match record
            payment_match = PaymentInvoiceMatch(
                organization_id=transaction.organization_id,
                transaction_id=str(transaction.id),
                invoice_id=match_result["invoice_id"],
                voucher_id=match_result.get("voucher_id"),
                match_status=match_status,
                match_score=score,
                match_method="automated",
                matched_amount=transaction.amount,
                criteria_matched=match_result["criteria"],
                notes=f"Auto-matched with {score}% confidence",
            )

            match_id = self.bank_repo.create_payment_match(payment_match)

            # Update transaction
            self.bank_repo.match_transaction_to_invoice(
                transaction_id=str(transaction.id),
                invoice_id=match_result["invoice_id"],
                voucher_id=match_result.get("voucher_id"),
            )

            logger.info(
                f"Created payment match {match_id} for transaction {transaction.id} "
                f"with score {score}%"
            )

        except Exception as e:
            logger.error(f"Error creating match record: {e}")

    def manual_match(
        self,
        transaction_id: str,
        invoice_id: str,
        voucher_id: Optional[str],
        user_id: str,
        notes: Optional[str] = None,
    ) -> PaymentInvoiceMatch:
        """
        Manually match transaction to invoice

        Args:
            transaction_id: Transaction ID
            invoice_id: Invoice ID
            voucher_id: Voucher ID (optional)
            user_id: User who created the match
            notes: Optional notes

        Returns:
            PaymentInvoiceMatch object
        """
        # Get transaction
        transaction = self.bank_repo.get_transaction(transaction_id)

        if not transaction:
            raise ValueError("Transaction not found")

        # Create manual match
        payment_match = PaymentInvoiceMatch(
            organization_id=transaction.organization_id,
            transaction_id=transaction_id,
            invoice_id=invoice_id,
            voucher_id=voucher_id,
            match_status=MatchStatus.MANUAL_MATCHED,
            match_score=100.0,  # Manual matches are 100% confident
            match_method="manual",
            matched_amount=transaction.amount,
            criteria_matched=["manual"],
            matched_by=user_id,
            notes=notes or "Manually matched by user",
        )

        match_id = self.bank_repo.create_payment_match(payment_match)
        payment_match.id = match_id

        # Update transaction
        self.bank_repo.match_transaction_to_invoice(
            transaction_id=transaction_id,
            invoice_id=invoice_id,
            voucher_id=voucher_id,
        )

        logger.info(f"Manual match created by user {user_id} for transaction {transaction_id}")

        return payment_match

    def unmatch_transaction(self, transaction_id: str) -> bool:
        """
        Unmatch a transaction from its invoice

        Args:
            transaction_id: Transaction ID

        Returns:
            Success boolean
        """
        try:
            # Update transaction status
            result = self.bank_repo.update_transaction_status(
                transaction_id,
                TransactionStatus.PENDING,
                MatchStatus.UNMATCHED,
            )

            logger.info(f"Unmatched transaction {transaction_id}")
            return result

        except Exception as e:
            logger.error(f"Error unmatching transaction: {e}")
            return False
