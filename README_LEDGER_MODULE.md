# Ledger & Posting Module

## Overview
The Ledger & Posting Module provides production-ready APIs for converting approved vouchers into journal entries and maintaining double-entry accounting. This module ensures accurate financial record-keeping and provides comprehensive ledger management capabilities.

## Features Implemented

### 1. Auto Posting (`POST /accounting/ledger/post`)
- **Description**: Automatically converts approved vouchers into journal entries
- **Features**: 
  - Validates voucher approval status
  - Extracts financial data from OCR results
  - Creates double-entry journal entries
  - Posts to ledger automatically
  - Updates voucher status

**Request Example**:
```json
{
  "voucher_id": "60f7b3b3b3b3b3b3b3b3b3b3",
  "account_mappings": {
    "expense": "5000",
    "accounts_payable": "2000"
  },
  "description": "Office supplies purchase"
}
```

### 2. Manual Journal Entry (`POST /accounting/ledger/manual`)
- **Description**: Create or adjust journal entries manually
- **Features**:
  - Double-entry validation
  - Account existence validation
  - Auto-generated reference numbers
  - Draft status for review before posting

**Request Example**:
```json
{
  "transaction_date": "2024-01-15",
  "description": "Monthly rent payment",
  "entries": [
    {
      "account_code": "5200",
      "account_name": "Rent Expense",
      "entry_type": "debit",
      "amount": 2000.00,
      "description": "January rent"
    },
    {
      "account_code": "1000",
      "account_name": "Cash",
      "entry_type": "credit",
      "amount": 2000.00,
      "description": "Cash payment for rent"
    }
  ]
}
```

### 3. Ledger View (`GET /accounting/ledger/`)
- **Description**: View transactions by account/date with comprehensive filtering
- **Features**:
  - Filter by account code, account type, date range, entry type
  - Pagination support
  - Sorting by transaction date

**Query Parameters**:
- `account_code`: Filter by specific account
- `account_type`: Filter by asset, liability, equity, revenue, expense
- `start_date` & `end_date`: Date range filtering
- `entry_type`: Filter by debit or credit entries
- `limit` & `skip`: Pagination controls

### 4. Accruals & Reversals (`POST /accounting/ledger/accrual`)
- **Description**: Schedule adjustments for future periods
- **Features**:
  - Schedule accrual entries for future dates
  - Automatic reversal scheduling
  - Account validation

**Request Example**:
```json
{
  "account_code": "2100",
  "amount": 1500.00,
  "accrual_date": "2024-01-31",
  "reversal_date": "2024-02-01",
  "description": "Accrued utilities expense"
}
```

## Additional APIs

### 5. Post Journal Entry (`POST /accounting/ledger/journal-entry/{id}/post`)
- Convert draft journal entries to posted status
- Create corresponding ledger records

### 6. Trial Balance (`GET /accounting/ledger/trial-balance`)
- Generate trial balance reports as of specific dates
- Verify double-entry balance
- Filter by account type

### 7. Chart of Accounts Management
- **Create Account**: `POST /accounting/ledger/accounts`
- **Get Accounts**: `GET /accounting/ledger/accounts`

## Data Models

### Account Types
- `asset`: Assets (Cash, Accounts Receivable, Equipment)
- `liability`: Liabilities (Accounts Payable, Loans)
- `equity`: Owner's Equity, Retained Earnings
- `revenue`: Sales Revenue, Service Revenue
- `expense`: Operating Expenses, Cost of Goods Sold

### Entry Types
- `debit`: Debit entries (increases assets/expenses, decreases liabilities/equity/revenue)
- `credit`: Credit entries (decreases assets/expenses, increases liabilities/equity/revenue)

### Journal Entry Status
- `draft`: Created but not posted to ledger
- `posted`: Posted to ledger and affecting account balances
- `reversed`: Reversed entry (for corrections)

## Database Collections

The module uses the following MongoDB collections:
- `journal_entries`: Journal entry records
- `ledger`: Individual ledger line items
- `chart_of_accounts`: Chart of accounts master data
- `accruals`: Scheduled accrual entries
- `voucher`: Voucher records (existing)
- `ocr`: OCR data for vouchers (existing)

## Double-Entry Validation

The system enforces strict double-entry bookkeeping rules:
1. **Balance Validation**: Total debits must equal total credits
2. **Minimum Entries**: At least 2 entries required per journal entry
3. **Account Validation**: All accounts must exist in chart of accounts
4. **Amount Validation**: All amounts must be positive (entry type determines debit/credit)

## Sample Chart of Accounts

A sample chart of accounts is provided in `sample_chart_of_accounts.json` with standard business accounts:
- **Assets (1000-1999)**: Cash, Accounts Receivable, Inventory, Equipment
- **Liabilities (2000-2999)**: Accounts Payable, Accrued Expenses, Loans
- **Equity (3000-3999)**: Owner's Equity, Retained Earnings
- **Revenue (4000-4999)**: Sales Revenue, Service Revenue, Interest Income
- **Expenses (5000-5999)**: COGS, Operating Expenses, Depreciation

## Error Handling

The module provides comprehensive error handling:
- **404 Errors**: Voucher not found, Account not found, Journal entry not found
- **400 Errors**: Invalid voucher status, Double-entry imbalance, Invalid amounts
- **500 Errors**: Database errors, Unexpected system errors

## Security & Validation

- All monetary amounts are validated and rounded to 2 decimal places
- Account codes are validated against the chart of accounts
- User authentication required for all operations (user_id parameter)
- Voucher approval status verified before posting
- Duplicate posting prevention

## Usage Examples

### 1. Setting up Chart of Accounts
```bash
# Create basic accounts
curl -X POST "http://localhost:8000/accounting/ledger/accounts?user_id=user123" \
  -H "Content-Type: application/json" \
  -d '{
    "account_code": "1000",
    "account_name": "Cash",
    "account_type": "asset",
    "description": "Cash on hand and in bank"
  }'
```

### 2. Posting a Voucher
```bash
curl -X POST "http://localhost:8000/accounting/ledger/post?user_id=user123" \
  -H "Content-Type: application/json" \
  -d '{
    "voucher_id": "60f7b3b3b3b3b3b3b3b3b3b3",
    "account_mappings": {
      "expense": "5100",
      "accounts_payable": "2000"
    }
  }'
```

### 3. Creating Manual Entry
```bash
curl -X POST "http://localhost:8000/accounting/ledger/manual?user_id=user123" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_date": "2024-01-15",
    "description": "Office supplies purchase",
    "entries": [
      {
        "account_code": "5100",
        "account_name": "Office Supplies",
        "entry_type": "debit",
        "amount": 150.00
      },
      {
        "account_code": "1000",
        "account_name": "Cash",
        "entry_type": "credit",
        "amount": 150.00
      }
    ]
  }'
```

### 4. Viewing Ledger Entries
```bash
# Get all entries for Cash account
curl "http://localhost:8000/accounting/ledger/?account_code=1000&limit=50"

# Get entries by date range
curl "http://localhost:8000/accounting/ledger/?start_date=2024-01-01&end_date=2024-01-31"

# Get trial balance
curl "http://localhost:8000/accounting/ledger/trial-balance?as_of_date=2024-01-31"
```

## Integration with Existing System

The module integrates seamlessly with your existing voucher system:
1. **Voucher Collection**: Uses existing voucher records and status
2. **OCR Integration**: Extracts amounts from existing OCR data
3. **User System**: Uses existing user authentication
4. **MongoDB**: Uses existing database connection and collections

## Production Readiness Features

- **Comprehensive Error Handling**: Detailed error messages and proper HTTP status codes
- **Data Validation**: Strict validation of all inputs and business rules
- **Audit Trail**: Complete audit trail with timestamps and user tracking
- **Scalability**: Efficient database queries with proper indexing support
- **Documentation**: Complete API documentation with examples
- **Testing Support**: Sample data and clear testing procedures