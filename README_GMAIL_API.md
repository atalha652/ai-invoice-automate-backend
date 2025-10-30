# Gmail Purchase Emails API

A comprehensive FastAPI-based solution for fetching, parsing, and analyzing purchase emails from Gmail using the Gmail API, now with multi-user support.

## 🚀 Features

- **Multi-User Support**: Securely access multiple users' Gmail accounts using their unique user ID.
- **Purchase Email Fetching**: Automatically fetch emails from Gmail's "Purchases" category for each user.
- **Smart Email Parsing**: Extract purchase information (amount, order number, merchant, etc.).
- **Advanced Filtering**: Filter emails by sender, amount, date, merchant, and more.
- **Pagination Support**: Handle large volumes of emails with pagination.
- **Purchase Analytics**: Get summary statistics and insights for each user.
- **RESTful API**: Clean, documented API endpoints.
- **Error Handling**: Comprehensive error handling and validation.

## 📋 Prerequisites

1. **Google Cloud Project**: Create a project in Google Cloud Console.
2. **Gmail API**: Enable the Gmail API for your project.
3. **OAuth Credentials**: Create OAuth 2.0 credentials (Web application type).
4. **Python Dependencies**: Install the required packages.

## 🛠️ Setup Instructions

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project or select an existing one.
3. Enable the Gmail API:
   - Go to **APIs & Services** → **Library**.
   - Search for "Gmail API" and enable it.
4. Create OAuth credentials:
   - Go to **APIs & Services** → **Credentials**.
   - Click **Create Credentials** → **OAuth 2.0 Client IDs**.
   - Choose **Web application**.
   - Add `http://localhost:8000/api/gmail/oauth2/callback` to the "Authorized redirect URIs".
   - Download the JSON file and rename it to `client_secret.json`.

### 2. Install Dependencies

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client fastapi uvicorn motor
```

### 3. Authentication Flow (Multi-User)

The authentication flow supports multiple users. Each user must authorize the application to access their Gmail account.

1. **Initiate Authorization**:
   - The user navigates to `GET /api/gmail/oauth2/authorize?user_id=YOUR_USER_ID`.
   - This will redirect them to Google's consent screen to grant permission.

2. **Handle Callback**:
   - After the user grants permission, Google redirects them to `GET /api/gmail/oauth2/callback`.
   - The application exchanges the authorization code for an access token and stores the credentials securely for the user.

## 📚 API Endpoints

### Authentication

#### `GET /api/gmail/oauth2/authorize`
Initiates the OAuth2 authorization flow by redirecting the user to Google's consent screen.

**Query Parameters:**
- `user_id` (string, required): The unique ID of the user to authenticate.

#### `GET /api/gmail/oauth2/callback`
Handles the callback from Google after the user grants permission. The application receives an authorization code, exchanges it for an access token, and stores the user's credentials.

### Health Check

#### `GET /api/gmail/health`
Check the health of the API.
```json
{
  "status": "ok"
}
```

### Purchase Emails

All endpoints require a `user_id` to identify the user.

#### `GET /api/gmail/purchases`
Fetch purchase emails with pagination for a specific user.

**Query Parameters:**
- `user_id` (string, required): The ID of the user to fetch emails for.
- `max_results` (int): Number of emails to fetch (1-500, default: 50)
- `page_token` (string): Pagination token

**Response:**
```json
{
  "emails": [
    {
      "id": "email_id",
      "sender": "Amazon <no-reply@amazon.com>",
      "sender_name": "Amazon",
      "sender_email": "no-reply@amazon.com",
      "subject": "Your order has been shipped",
      "amount": 29.99,
      "currency": "USD",
      "order_number": "123-4567890-1234567",
      "merchant": "Amazon",
      "purchase_type": "shipping",
      "date": "2024-01-15T10:30:00Z"
    }
  ],
  "next_page_token": "token_for_next_page",
  "total_found": 25,
  "page_size": 50
}
```

#### `POST /api/gmail/search`
Search emails with a custom Gmail query for a specific user.

**Query Parameters:**
- `user_id` (string, required): The ID of the user to search emails for.

**Request Body:**
```json
{
  "query": "from:amazon.com subject:order",
  "max_results": 100
}
```

**Gmail Search Operators:**
- `from:sender@example.com` - emails from a specific sender
- `subject:keyword` - emails with a keyword in the subject
- `after:2024/1/1` - emails after a specific date
- `before:2024/12/31` - emails before a specific date
- `has:attachment` - emails with attachments
- `category:purchases` - emails in the purchases category

### Filtering & Analytics

#### `POST /api/gmail/purchases/filter`
Filter purchase emails by various criteria for a specific user.

**Query Parameters:**
- `user_id` (string, required): The ID of the user to filter emails for.

**Request Body:**
```json
{
  "sender": "Amazon",
  "subject_contains": "order",
  "min_amount": 50,
  "max_amount": 200,
  "date_from": "2024-01-01",
  "date_to": "2024-01-31",
  "merchant": "Amazon",
  "purchase_type": "receipt"
}
```

#### `GET /api/gmail/purchases/summary`
Get purchase summary statistics for a specific user.

**Query Parameters:**
- `user_id` (string, required): The ID of the user to get the summary for.

**Response:**
```json
{
  "by_merchant": {
    "Amazon": {
      "total_amount": 150.50,
      "count": 5
    }
  },
  "by_purchase_type": {
    "receipt": {
      "total_amount": 120.00,
      "count": 4
    },
    "shipping": {
      "total_amount": 30.50,
      "count": 1
    }
  }
}
```

#### `GET /api/gmail/{email_id}`
Get detailed information about a specific email for a specific user.

**Path Parameters:**
- `email_id` (string, required): The ID of the email to fetch.

**Query Parameters:**
- `user_id` (string, required): The ID of the user the email belongs to.

## 🔧 Usage Examples

### Python Client Example

```python
import requests

# Base URL for your FastAPI app
BASE_URL = "http://localhost:8000"
USER_ID = "YOUR_USER_ID"  # Replace with the actual user ID

# Initiate authentication (if needed)
# This will redirect to Google's consent screen
# response = requests.get(f"{BASE_URL}/api/gmail/oauth2/authorize?user_id={USER_ID}")

# Get purchase emails
response = requests.get(f"{BASE_URL}/api/gmail/purchases?user_id={USER_ID}&max_results=10")
emails = response.json()

for email in emails['emails']:
    print(f"From: {email['sender_name']}")
    print(f"Subject: {email['subject']}")
    if email['amount']:
        print(f"Amount: ${email['amount']}")
    print("-" * 40)

# Search for specific emails
search_data = {
    "query": "from:amazon.com after:2024/1/1",
    "max_results": 50
}
response = requests.post(f"{BASE_URL}/api/gmail/search?user_id={USER_ID}", json=search_data)
results = response.json()

# Filter by amount
filter_data = {
    "min_amount": 100,
    "merchant": "Amazon"
}
response = requests.post(f"{BASE_URL}/api/gmail/purchases/filter?user_id={USER_ID}", json=filter_data)
filtered_emails = response.json()

# Get summary
response = requests.get(f"{BASE_URL}/api/gmail/purchases/summary?user_id={USER_ID}")
summary = response.json()
print(f"Total spent at Amazon: ${summary['by_merchant']['Amazon']['total_amount']}")
```

### JavaScript/Frontend Example

```javascript
const BASE_URL = 'http://localhost:8000';
const USER_ID = 'YOUR_USER_ID'; // Replace with the actual user ID

// Initiate authentication (if needed)
// window.location.href = `${BASE_URL}/api/gmail/oauth2/authorize?user_id=${USER_ID}`;

// Fetch purchase emails
async function fetchPurchaseEmails() {
  const response = await fetch(`${BASE_URL}/api/gmail/purchases?user_id=${USER_ID}&max_results=10`);
  const data = await response.json();
  console.log(data.emails);
}

fetchPurchaseEmails();
```

```