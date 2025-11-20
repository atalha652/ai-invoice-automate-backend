"""
Bank Statement Parser Service
Supports CSV, CAMT.053 (ISO 20022 XML), and MT940 (SWIFT) formats
"""

import csv
import hashlib
import logging
import zipfile
from decimal import Decimal, InvalidOperation
from datetime import datetime
from io import StringIO, BytesIO
from typing import List, Dict, Any, Optional, Tuple

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - optional dependency
    load_workbook = None

# Third-party parsers
import mt940
from pycamt.parser import Camt053Parser
from defusedxml import ElementTree as ET

from app.models.bank_transactions import (
    BankTransaction,
    BankStatement,
    BankStatementFormat,
    TransactionType,
    TransactionStatus,
    MatchStatus,
)

logger = logging.getLogger(__name__)


class BankStatementParser:
    """
    Universal bank statement parser supporting multiple formats
    """

    def __init__(self, organization_id: str, bank_account_id: str):
        self.organization_id = organization_id
        self.bank_account_id = bank_account_id

    def parse_file(
        self,
        file_content: bytes,
        file_name: str,
        format_type: BankStatementFormat,
        imported_by: Optional[str] = None,
    ) -> Tuple[BankStatement, List[BankTransaction]]:
        """
        Parse bank statement file and return statement + transactions

        Args:
            file_content: Raw file bytes
            file_name: Original filename
            format_type: Statement format (CSV, CAMT053, MT940)
            imported_by: User ID who imported

        Returns:
            Tuple of (BankStatement, List[BankTransaction])
        """
        # Calculate file hash to prevent duplicates
        file_hash = hashlib.sha256(file_content).hexdigest()

        if format_type == BankStatementFormat.CSV:
            return self._parse_csv(file_content, file_name, file_hash, imported_by)
        elif format_type == BankStatementFormat.CAMT053:
            return self._parse_camt053(file_content, file_name, file_hash, imported_by)
        elif format_type == BankStatementFormat.MT940:
            return self._parse_mt940(file_content, file_name, file_hash, imported_by)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

    def _parse_csv(
        self,
        file_content: bytes,
        file_name: str,
        file_hash: str,
        imported_by: Optional[str],
    ) -> Tuple[BankStatement, List[BankTransaction]]:
        """
        Parse CSV bank statement

        Expected CSV columns:
        - date, value_date, description, amount, currency, balance, reference,
          counterparty_name, counterparty_account
        """
        try:
            content_str = self._get_csv_string(file_content, file_name)
            # Normalize line endings and handle quoted fields properly
            content_str = content_str.replace('\r\n', '\n').replace('\r', '\n')
            content_str = self._strip_leading_metadata_lines(content_str)

            # Create CSV reader with proper configuration to handle various CSV formats
            csv_reader = csv.DictReader(
                StringIO(content_str),
                skipinitialspace=True,
                quoting=csv.QUOTE_MINIMAL
            )

            transactions = []
            opening_balance = None
            closing_balance = None
            total_debits = 0.0
            total_credits = 0.0
            min_date = None
            max_date = None
            row_errors: List[str] = []

            for row_num, row in enumerate(csv_reader, start=1):
                try:
                    normalized_row = self._normalize_row_keys(row)

                    # Parse transaction date
                    trans_date = self._parse_date(
                        self._get_first_value(normalized_row, "date", "transaction_date")
                    )
                    value_date = (
                        self._parse_date(self._get_first_value(normalized_row, "value_date"))
                        or trans_date
                    )

                    # Update date range
                    if min_date is None or trans_date < min_date:
                        min_date = trans_date
                    if max_date is None or trans_date > max_date:
                        max_date = trans_date

                    # Parse amount
                    amount, trans_type = self._extract_amount_and_type(normalized_row)

                    # Update totals
                    if trans_type == TransactionType.DEBIT:
                        total_debits += amount
                    else:
                        total_credits += amount

                    # Parse balance
                    balance = self._get_first_value(normalized_row, "balance", "balance_after")
                    if balance:
                        balance_value = self._parse_decimal(balance)
                        if opening_balance is None:
                            opening_balance = balance_value
                        closing_balance = balance_value

                    # Create transaction
                    transaction = BankTransaction(
                        organization_id=self.organization_id,
                        bank_account_id=self.bank_account_id,
                        transaction_date=trans_date,
                        value_date=value_date,
                        transaction_type=trans_type,
                        amount=amount,
                        currency=self._get_first_value(normalized_row, "currency") or "EUR",
                        reference=self._get_first_value(normalized_row, "reference"),
                        description=self._get_first_value(normalized_row, "description", "details"),
                        counterparty_name=self._get_first_value(normalized_row, "counterparty_name", "counterparty"),
                        counterparty_account=self._get_first_value(normalized_row, "counterparty_account", "counterparty_iban"),
                        balance_after=self._parse_decimal(balance) if balance else None,
                        status=TransactionStatus.PENDING,
                        match_status=MatchStatus.UNMATCHED,
                        imported_by=imported_by,
                        raw_data=self._sanitize_raw_row(row),
                    )

                    transactions.append(transaction)

                except Exception as e:
                    error_message = f"Row {row_num}: {e}"
                    row_errors.append(error_message)
                    logger.error(f"Error parsing CSV row {row_num}: {e}")
                    continue

            if not transactions:
                sample_errors = "; ".join(row_errors[:3]) if row_errors else "No parsable rows found."
                raise ValueError(f"No valid transactions were parsed from CSV. {sample_errors}")

            if row_errors:
                logger.warning(
                    "CSV parsing skipped %s rows due to validation errors. Sample: %s",
                    len(row_errors),
                    "; ".join(row_errors[:3]),
                )

            # Create statement
            statement = BankStatement(
                organization_id=self.organization_id,
                bank_account_id=self.bank_account_id,
                format=BankStatementFormat.CSV,
                file_name=file_name,
                file_hash=file_hash,
                statement_date=datetime.utcnow(),
                from_date=min_date or datetime.utcnow(),
                to_date=max_date or datetime.utcnow(),
                opening_balance=opening_balance or 0.0,
                closing_balance=closing_balance or 0.0,
                total_debits=total_debits,
                total_credits=total_credits,
                transaction_count=len(transactions),
                imported_by=imported_by,
            )

            return statement, transactions

        except Exception as e:
            logger.error(f"Error parsing CSV file: {e}")
            raise ValueError(f"Failed to parse CSV file: {str(e)}")

    def _parse_camt053(
        self,
        file_content: bytes,
        file_name: str,
        file_hash: str,
        imported_by: Optional[str],
    ) -> Tuple[BankStatement, List[BankTransaction]]:
        """
        Parse CAMT.053 (ISO 20022 XML) bank statement

        CAMT.053 is the modern XML-based standard for bank statements
        """
        try:
            # Parse XML using pycamt library
            xml_string = file_content.decode("utf-8")
            parser = Camt053Parser(xml_string)

            # Get statement info
            stmt_info = parser.get_statement_info()
            transactions_data = parser.get_transactions()

            transactions = []
            total_debits = 0.0
            total_credits = 0.0

            # Parse transactions
            for trans_data in transactions_data:
                try:
                    # Determine transaction type
                    credit_debit = trans_data.get("credit_debit_indicator", "")
                    is_credit = credit_debit == "CRDT"
                    trans_type = (
                        TransactionType.CREDIT
                        if is_credit
                        else TransactionType.DEBIT
                    )
                    amount = abs(float(trans_data.get("amount", 0)))

                    # Update totals
                    if trans_type == TransactionType.DEBIT:
                        total_debits += amount
                    else:
                        total_credits += amount

                    # Parse dates
                    booking_date = trans_data.get("booking_date")
                    value_date = trans_data.get("value_date")

                    if isinstance(booking_date, str):
                        booking_date = self._parse_date(booking_date)
                    if isinstance(value_date, str):
                        value_date = self._parse_date(value_date)

                    # Create transaction
                    transaction = BankTransaction(
                        organization_id=self.organization_id,
                        bank_account_id=self.bank_account_id,
                        transaction_date=booking_date or value_date or datetime.utcnow(),
                        value_date=value_date or booking_date or datetime.utcnow(),
                        booking_date=booking_date,
                        transaction_type=trans_type,
                        amount=amount,
                        currency=trans_data.get("currency", "EUR"),
                        transaction_id=trans_data.get("entry_reference"),
                        reference=trans_data.get("remittance_information"),
                        counterparty_name=trans_data.get("counterparty_name"),
                        counterparty_iban=trans_data.get("counterparty_account"),
                        description=trans_data.get("remittance_information"),
                        status=TransactionStatus.PENDING,
                        match_status=MatchStatus.UNMATCHED,
                        imported_by=imported_by,
                        raw_data=trans_data,
                    )

                    transactions.append(transaction)

                except Exception as e:
                    logger.error(f"Error parsing CAMT transaction: {e}")
                    continue

            # Parse statement balances
            opening_balance = 0.0
            closing_balance = 0.0
            from_date = datetime.utcnow()
            to_date = datetime.utcnow()

            if stmt_info:
                opening_balance = float(stmt_info.get("opening_balance", {}).get("amount", 0))
                closing_balance = float(stmt_info.get("closing_balance", {}).get("amount", 0))

                from_date_str = stmt_info.get("from_date")
                to_date_str = stmt_info.get("to_date")

                if from_date_str:
                    from_date = self._parse_date(from_date_str)
                if to_date_str:
                    to_date = self._parse_date(to_date_str)

            # Create statement
            statement = BankStatement(
                organization_id=self.organization_id,
                bank_account_id=self.bank_account_id,
                format=BankStatementFormat.CAMT053,
                file_name=file_name,
                file_hash=file_hash,
                statement_number=stmt_info.get("statement_id") if stmt_info else None,
                statement_date=datetime.utcnow(),
                from_date=from_date,
                to_date=to_date,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                total_debits=total_debits,
                total_credits=total_credits,
                transaction_count=len(transactions),
                imported_by=imported_by,
            )

            return statement, transactions

        except Exception as e:
            logger.error(f"Error parsing CAMT.053 file: {e}")
            raise ValueError(f"Failed to parse CAMT.053 file: {str(e)}")

    def _parse_mt940(
        self,
        file_content: bytes,
        file_name: str,
        file_hash: str,
        imported_by: Optional[str],
    ) -> Tuple[BankStatement, List[BankTransaction]]:
        """
        Parse MT940 (SWIFT) bank statement

        MT940 is the legacy text-based SWIFT standard
        """
        try:
            # Decode to string
            content_str = file_content.decode("utf-8")

            # Parse using mt940 library
            mt940_data = mt940.parse(content_str)

            transactions = []
            total_debits = 0.0
            total_credits = 0.0

            for stmt in mt940_data:
                # Extract statement information
                opening_balance = float(stmt.opening_balance.amount)
                closing_balance = float(stmt.closing_balance.amount)

                # Parse transactions
                for trans in stmt.transactions:
                    try:
                        # Determine type (D=debit, C=credit)
                        is_credit = trans.debit_credit == "C"
                        trans_type = (
                            TransactionType.CREDIT
                            if is_credit
                            else TransactionType.DEBIT
                        )
                        amount = abs(float(trans.amount))

                        # Update totals
                        if trans_type == TransactionType.DEBIT:
                            total_debits += amount
                        else:
                            total_credits += amount

                        # Create transaction
                        transaction = BankTransaction(
                            organization_id=self.organization_id,
                            bank_account_id=self.bank_account_id,
                            transaction_date=trans.booking_date or trans.value_date,
                            value_date=trans.value_date,
                            booking_date=trans.booking_date,
                            transaction_type=trans_type,
                            amount=amount,
                            currency=getattr(trans, "currency", "EUR"),
                            reference=getattr(trans, "reference", None),
                            transaction_id=getattr(trans, "transaction_id", None),
                            description=" ".join(
                                getattr(trans, "transaction_details", [])
                            ),
                            status=TransactionStatus.PENDING,
                            match_status=MatchStatus.UNMATCHED,
                            imported_by=imported_by,
                            raw_data={
                                "reference": getattr(trans, "reference", None),
                                "fund_code": getattr(trans, "fund_code", None),
                            },
                        )

                        transactions.append(transaction)

                    except Exception as e:
                        logger.error(f"Error parsing MT940 transaction: {e}")
                        continue

                # Create statement
                statement = BankStatement(
                    organization_id=self.organization_id,
                    bank_account_id=self.bank_account_id,
                    format=BankStatementFormat.MT940,
                    file_name=file_name,
                    file_hash=file_hash,
                    statement_number=getattr(stmt, "transaction_reference", None),
                    statement_date=datetime.utcnow(),
                    from_date=stmt.opening_balance.date,
                    to_date=stmt.closing_balance.date,
                    opening_balance=opening_balance,
                    closing_balance=closing_balance,
                    total_debits=total_debits,
                    total_credits=total_credits,
                    transaction_count=len(transactions),
                    imported_by=imported_by,
                )

                # Only process first statement
                break

            return statement, transactions

        except Exception as e:
            logger.error(f"Error parsing MT940 file: {e}")
            raise ValueError(f"Failed to parse MT940 file: {str(e)}")

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> datetime:
        """
        Parse date string to datetime

        Supports common formats:
        - YYYY-MM-DD
        - DD/MM/YYYY
        - DD.MM.YYYY
        - YYYYMMDD
        """
        if not date_str:
            return datetime.utcnow()

        # Try common formats
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d.%m.%Y",
            "%Y%m%d",
            "%d-%m-%Y",
            "%Y/%m/%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue

        # If all fail, return current date
        logger.warning(f"Could not parse date: {date_str}")
        return datetime.utcnow()

    @staticmethod
    def _sanitize_raw_row(row: Dict[Any, Any]) -> Dict[str, Any]:
        """Ensure raw_data keys are strings for Pydantic validation."""
        sanitized: Dict[str, Any] = {}
        for key, value in row.items():
            if key is None:
                continue
            sanitized[str(key)] = value
        return sanitized

    @staticmethod
    def _parse_decimal(value: Any) -> Optional[float]:
        """Parse numeric strings that may contain locale or currency characters."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None

            sign = 1
            if cleaned.startswith("(") and cleaned.endswith(")"):
                sign = -1
                cleaned = cleaned[1:-1]

            cleaned = (
                cleaned.replace(",", "")
                .replace(" ", "")
                .replace("€", "")
                .replace("$", "")
                .replace("+", "")
            )

            suffixes = {"CR": 1, "DR": -1}
            for suffix, suffix_sign in suffixes.items():
                if cleaned.upper().endswith(suffix):
                    sign *= suffix_sign
                    cleaned = cleaned[:-len(suffix)]
                    break

            try:
                return float(Decimal(cleaned)) * sign
            except (InvalidOperation, ValueError):
                return None

        return None

    def _extract_amount_and_type(self, row: Dict[str, Any]) -> Tuple[float, TransactionType]:
        """
        Determine transaction amount and type from flexible CSV schemas.
        `row` must have lowercase, trimmed keys (use `_normalize_row_keys`).
        Raises ValueError if the amount cannot be determined or is zero.
        """
        amount_fields = [
            "amount",
            "transaction_amount",
            "value",
            "amt",
        ]
        paired_fields = [
            ("credit", "debit"),
            ("credit_amount", "debit_amount"),
            ("paid_in", "paid_out"),
            ("deposit", "withdrawal"),
            ("money_in", "money_out"),
        ]
        indicator_fields = [
            "transaction_type",
            "type",
            "credit_debit_indicator",
            "credit_debit",
            "cr_dr",
            "dr_cr",
            "direction",
            "indicator",
            "debitcredit",
        ]
        indicator_type = None
        for indicator_field in indicator_fields:
            indicator_type = self._infer_type_from_indicator(row.get(indicator_field))
            if indicator_type:
                break

        for field in amount_fields:
            parsed = self._parse_decimal(row.get(field))
            if parsed is None or parsed == 0:
                continue
            if parsed > 0:
                return parsed, TransactionType.CREDIT
            return abs(parsed), TransactionType.DEBIT

        for credit_field, debit_field in paired_fields:
            credit_value = self._parse_decimal(row.get(credit_field))
            if credit_value and credit_value > 0:
                return credit_value, TransactionType.CREDIT

            debit_value = self._parse_decimal(row.get(debit_field))
            if debit_value and debit_value > 0:
                return debit_value, TransactionType.DEBIT

        # Fallback: scan all columns containing "amount"/"amt"
        for key, value in row.items():
            if "amount" not in key and "amt" not in key:
                continue
            if any(exclusion in key for exclusion in ("opening_balance", "closing_balance", "balance")):
                continue

            parsed = self._parse_decimal(value)
            if parsed is None or parsed == 0:
                continue

            # Infer type from column name if possible
            lower_key = key.lower()
            if "credit" in lower_key or lower_key.endswith("cr"):
                return abs(parsed), TransactionType.CREDIT
            if "debit" in lower_key or lower_key.endswith("dr"):
                return abs(parsed), TransactionType.DEBIT

            if indicator_type:
                return abs(parsed), indicator_type

            # Default to credit for positive, debit for negative even though we excluded 0
            if parsed > 0:
                return parsed, TransactionType.CREDIT
            return abs(parsed), TransactionType.DEBIT

        available_fields = ", ".join(row.keys())
        raise ValueError(
            "Unable to determine transaction amount (missing amount/credit/debit values). "
            f"Available columns: {available_fields}"
        )

    @staticmethod
    def _normalize_row_keys(row: Dict[Any, Any]) -> Dict[str, Any]:
        """Return a new dict with lowercase, trimmed keys for case-insensitive lookups."""
        normalized: Dict[str, Any] = {}
        for key, value in row.items():
            if key is None:
                continue
            normalized[str(key).strip().lower()] = value
        return normalized

    @staticmethod
    def _get_first_value(row: Dict[str, Any], *keys: str) -> Optional[Any]:
        """Fetch the first available key (case-insensitive) from normalized row."""
        for key in keys:
            if key is None:
                continue
            lookup = key.strip().lower()
            if lookup in row:
                return row[lookup]
        return None

    @staticmethod
    def _infer_type_from_indicator(indicator: Optional[Any]) -> Optional[TransactionType]:
        if indicator is None:
            return None
        text = str(indicator).strip().lower()
        if not text:
            return None

        credit_tokens = {"credit", "cr", "c", "in", "+", "deposit"}
        debit_tokens = {"debit", "dr", "d", "out", "-", "withdrawal"}

        if text in credit_tokens:
            return TransactionType.CREDIT
        if text in debit_tokens:
            return TransactionType.DEBIT

        if "credit" in text:
            return TransactionType.CREDIT
        if "debit" in text:
            return TransactionType.DEBIT

        return None

    @staticmethod
    def _strip_leading_metadata_lines(content_str: str) -> str:
        """
        Remove introductory metadata lines so the CSV header starts with actual column names.
        Detects the first line that contains a delimiter and any expected column keyword.
        """
        lines = content_str.splitlines()
        header_keywords = [
            "date",
            "transaction",
            "amount",
            "debit",
            "credit",
            "balance",
            "description",
            "details",
            "reference",
        ]
        delimiters = [",", ";", "\t", "|"]

        def looks_like_header(line: str) -> bool:
            lower = line.lower()
            if not any(delim in line for delim in delimiters):
                return False
            return any(keyword in lower for keyword in header_keywords)

        for idx, line in enumerate(lines):
            if looks_like_header(line):
                stripped = "\n".join(lines[idx:])
                if idx > 0:
                    logger.info("Skipped %s metadata lines before CSV header detection", idx)
                return stripped

        return content_str

    def _get_csv_string(self, file_content: bytes, file_name: str) -> str:
        """Return text content for CSV parsing, converting Excel if necessary."""
        if self._is_excel_content(file_content, file_name):
            return self._excel_to_csv_string(file_content)

        encodings = [
            "utf-8-sig",
            "utf-8",
            "latin-1",
            "iso-8859-1",
            "cp1252",
            "windows-1252",
        ]

        for encoding in encodings:
            try:
                decoded = file_content.decode(encoding)
                logger.info("Successfully decoded CSV with %s encoding", encoding)
                return decoded
            except (UnicodeDecodeError, AttributeError):
                continue

        raise ValueError("Could not decode CSV file with any supported encoding")

    def _is_excel_content(self, file_content: bytes, file_name: Optional[str]) -> bool:
        """Detect if the uploaded file is actually an Excel workbook."""
        excel_extensions = (".xlsx", ".xlsm", ".xlsb", ".xls")
        if file_name and file_name.lower().endswith(excel_extensions):
            return True

        # Excel OpenXML files are ZIP archives that start with PK header
        try:
            if not zipfile.is_zipfile(BytesIO(file_content)):
                return False
            with zipfile.ZipFile(BytesIO(file_content)) as archive:
                names = archive.namelist()
                return "[Content_Types].xml" in names or any(
                    name.endswith((".xml", ".rels")) for name in names
                )
        except Exception:
            return False

    def _excel_to_csv_string(self, file_content: bytes) -> str:
        """Convert first worksheet of an Excel file to CSV string."""
        if load_workbook is None:
            raise ValueError(
                "Excel file detected but openpyxl is not installed. "
                "Install openpyxl or upload CSV exports instead."
            )

        try:
            workbook = load_workbook(filename=BytesIO(file_content), read_only=True, data_only=True)
        except Exception as exc:
            raise ValueError(f"Failed to read Excel file: {exc}") from exc
        worksheet = workbook.active

        output = StringIO()
        csv_writer = csv.writer(output)
        rows_written = 0

        for row in worksheet.iter_rows(values_only=True):
            sanitized_row = ["" if cell is None else cell for cell in row]
            csv_writer.writerow(sanitized_row)
            rows_written += 1

        if rows_written == 0:
            raise ValueError("Excel file does not contain any rows to import")

        return output.getvalue()

    @staticmethod
    def detect_format(file_content: bytes, file_name: str) -> BankStatementFormat:
        """
        Auto-detect bank statement format based on content

        Args:
            file_content: Raw file bytes
            file_name: Original filename

        Returns:
            Detected BankStatementFormat
        """
        # Check file extension
        file_name_lower = file_name.lower()

        if file_name_lower.endswith(".xml"):
            # Try to detect if it's CAMT.053
            try:
                content_str = file_content.decode("utf-8")
                if "camt.053" in content_str or "BkToCstmrStmt" in content_str:
                    return BankStatementFormat.CAMT053
            except:
                pass

        if file_name_lower.endswith(".csv"):
            return BankStatementFormat.CSV

        if file_name_lower.endswith((".txt", ".mt940", ".sta")):
            return BankStatementFormat.MT940

        # Content-based detection
        try:
            content_str = file_content.decode("utf-8")

            # Check for CAMT.053 XML structure
            if "<?xml" in content_str and "camt.053" in content_str:
                return BankStatementFormat.CAMT053

            # Check for MT940 structure
            if ":20:" in content_str and ":25:" in content_str:
                return BankStatementFormat.MT940

            # Default to CSV
            return BankStatementFormat.CSV

        except:
            # If all fails, assume CSV
            return BankStatementFormat.CSV
