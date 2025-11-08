from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import os
import sys
from google_auth_oauthlib.flow import Flow
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

# Add the parent directory to the path to import services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from services.gmail_service import GmailService
from routes.auth import get_current_user

# Mount under "/api" in main; this keeps routes at "/api/gmail"
router = APIRouter(prefix="/gmail", tags=["Gmail"])

# Pydantic models for request/response
class EmailResponse(BaseModel):
    id: str
    thread_id: str
    sender: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    date: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    order_number: Optional[str] = None
    merchant: Optional[str] = None
    purchase_type: str = "unknown"
    internal_date: str
    size_estimate: int

class PurchaseEmailsResponse(BaseModel):
    emails: List[EmailResponse]
    next_page_token: Optional[str] = None
    total_found: int
    page_size: int

class AuthResponse(BaseModel):
    success: bool
    message: str
    auth_url: Optional[str] = None

class SearchRequest(BaseModel):
    query: str = Field(..., description="Gmail search query")
    max_results: int = Field(50, ge=1, le=500, description="Maximum number of results")

class EmailFilter(BaseModel):
    sender: Optional[str] = None
    subject_contains: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    merchant: Optional[str] = None
    purchase_type: Optional[str] = None

# Global Gmail service instance
gmail_service = GmailService()

# --- Database Connection ---
db_client = MongoClient(os.getenv("MONGO_URI"))
db = db_client[os.getenv("DB_NAME")]
users_collection = db["users"]

# --- OAuth2 Flow Settings ---
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]
# Allow overriding via env var to match your running port/domain
REDIRECT_URI = os.getenv("GMAIL_REDIRECT_URI", "https://ai-invoice-automate-backend-njgp.onrender.com/api/gmail/oauth2/callback")

# OAuth client credentials and endpoints from environment
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_AUTH_URI = os.getenv("GMAIL_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
GMAIL_TOKEN_URI = os.getenv("GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token")
GMAIL_CERT_URL = os.getenv("GMAIL_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs")

def _build_google_client_config():
    if not GMAIL_CLIENT_ID or not GMAIL_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Missing GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET in environment")
    return {
        "web": {
            "client_id": GMAIL_CLIENT_ID,
            "client_secret": GMAIL_CLIENT_SECRET,
            "auth_uri": GMAIL_AUTH_URI,
            "token_uri": GMAIL_TOKEN_URI,
            "auth_provider_x509_cert_url": GMAIL_CERT_URL,
            "redirect_uris": [REDIRECT_URI],
        }
    }

@router.get("/auth", response_model=AuthResponse)
async def authenticate_gmail():
    """
    Authenticate with Gmail API
    Returns authentication status and auth URL if needed
    """
    try:
        success = gmail_service.authenticate()
        
        if success:
            return AuthResponse(
                success=True,
                message="Successfully authenticated with Gmail"
            )
        else:
            return AuthResponse(
                success=False,
                message="Authentication failed. Please check credentials.",
                auth_url="Please run the authentication flow manually"
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication error: {str(e)}")


@router.get("/oauth2/authorize/")
async def oauth2_authorize(user_id: str):
    """
    Initiate OAuth2 flow to authorize Gmail access.
    Redirects user to Google's consent screen.
    """
    try:
        flow = Flow.from_client_config(
            _build_google_client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )

        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        # Store state in user's session or database to prevent CSRF
        users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"oauth_state": state}}
        )
        
        return RedirectResponse(authorization_url)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OAuth2 flow error: {str(e)}")


@router.get("/oauth2/callback")
async def oauth2_callback(code: str, state: str):
    """
    Handle OAuth2 callback from Google.
    Exchanges authorization code for credentials and stores them.
    """
    try:
        # Find user by state to prevent CSRF
        user = users_collection.find_one({"oauth_state": state})
        if not user:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")

        flow = Flow.from_client_config(
            _build_google_client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )

        # Exchange code for credentials
        flow.fetch_token(code=code)
        credentials = flow.credentials

        # Store credentials in the user's record
        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "gmail_credentials": {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token,
                    "token_uri": credentials.token_uri,
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "scopes": credentials.scopes
                },
                "oauth_state": None  # Clear state after use
            }}
        )

        return JSONResponse({"message": "Gmail authorization successful"})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OAuth2 callback error: {str(e)}")


@router.get("/purchases", response_model=PurchaseEmailsResponse)
async def get_purchase_emails(
    user_id: str,
    max_results: int = Query(50, ge=1, le=500, description="Maximum number of emails to fetch"),
    page_token: Optional[str] = Query(None, description="Page token for pagination")
):
    """
    Fetch purchase emails from Gmail
    
    - **user_id**: The ID of the user to fetch emails for
    - **max_results**: Number of emails to fetch (1-500)
    - **page_token**: Token for pagination (get from previous response)
    
    Returns detailed information about purchase emails including:
    - Email metadata (sender, subject, date)
    - Extracted purchase information (amount, order number, merchant)
    - Purchase type classification
    """
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("gmail_credentials"):
            raise HTTPException(status_code=401, detail="Gmail not authorized for this user")

        gmail_service = GmailService(user_credentials=user["gmail_credentials"])
        
        result = gmail_service.get_purchase_emails(
            max_results=max_results,
            page_token=page_token
        )
        
        return PurchaseEmailsResponse(
            emails=result['emails'],
            next_page_token=result.get('next_page_token'),
            total_found=result['total_found'],
            page_size=max_results
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching emails: {str(e)}")

@router.post("/search")
async def search_emails(user_id: str, search_request: SearchRequest):
    """
    Search emails with custom Gmail query
    
    - **user_id**: The ID of the user to search emails for
    - **query**: Gmail search query (e.g., "from:amazon.com", "subject:order")
    - **max_results**: Maximum number of results to return
    
    Gmail search operators you can use:
    - `from:sender@example.com` - emails from specific sender
    - `subject:keyword` - emails with keyword in subject
    - `after:2024/1/1` - emails after specific date
    - `before:2024/12/31` - emails before specific date
    - `has:attachment` - emails with attachments
    - `category:purchases` - emails in purchases category
    """
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("gmail_credentials"):
            raise HTTPException(status_code=401, detail="Gmail not authorized for this user")

        gmail_service = GmailService(user_credentials=user["gmail_credentials"])
        result = gmail_service.search_emails(
            query=search_request.query,
            max_results=search_request.max_results
        )
        
        return {
            "emails": result['emails'],
            "total_found": result['total_found'],
            "query": result['query'],
            "max_results": search_request.max_results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching emails: {str(e)}")

@router.post("/purchases/filter")
async def filter_purchase_emails(user_id: str, email_filter: EmailFilter):
    """
    Filter purchase emails based on criteria
    
    - **user_id**: The ID of the user to filter emails for
    - **sender**: Filter by sender's email
    - **subject_contains**: Filter by keyword in subject
    - **min_amount**: Minimum purchase amount
    - **max_amount**: Maximum purchase amount
    - **date_from**: Start date (YYYY-MM-DD)
    - **date_to**: End date (YYYY-MM-DD)
    - **merchant**: Filter by merchant name
    - **purchase_type**: Filter by purchase type
    """
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("gmail_credentials"):
            raise HTTPException(status_code=401, detail="Gmail not authorized for this user")

        gmail_service = GmailService(user_credentials=user["gmail_credentials"])
        
        # Build query from filter
        query_parts = []
        if email_filter.sender:
            query_parts.append(f"from:{email_filter.sender}")
        if email_filter.subject_contains:
            query_parts.append(f"subject:({email_filter.subject_contains})")
        if email_filter.date_from:
            query_parts.append(f"after:{email_filter.date_from.replace('-', '/')}")
        if email_filter.date_to:
            query_parts.append(f"before:{email_filter.date_to.replace('-', '/')}")
        
        query = " ".join(query_parts)
        
        result = gmail_service.search_emails(query=query, max_results=100) # Limit filter results
        
        # Further filter by fields not supported in Gmail query
        filtered_emails = result['emails']
        if email_filter.min_amount is not None:
            filtered_emails = [e for e in filtered_emails if e.get('amount', 0) >= email_filter.min_amount]
        if email_filter.max_amount is not None:
            filtered_emails = [e for e in filtered_emails if e.get('amount', 0) <= email_filter.max_amount]
        if email_filter.merchant:
            filtered_emails = [e for e in filtered_emails if e.get('merchant', '').lower() == email_filter.merchant.lower()]
        if email_filter.purchase_type:
            filtered_emails = [e for e in filtered_emails if e.get('purchase_type', '').lower() == email_filter.purchase_type.lower()]

        return {
            "emails": filtered_emails,
            "total_found": len(filtered_emails)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error filtering emails: {str(e)}")


@router.get("/purchases/summary")
async def get_purchase_summary(user_id: str):
    """
    Get a summary of purchases by merchant and purchase type
    
    - **user_id**: The ID of the user to get the summary for
    """
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("gmail_credentials"):
            raise HTTPException(status_code=401, detail="Gmail not authorized for this user")

        gmail_service = GmailService(user_credentials=user["gmail_credentials"])
        
        all_emails = gmail_service.get_all_purchase_emails()
        
        summary = {
            "by_merchant": {},
            "by_purchase_type": {}
        }
        
        for email in all_emails:
            merchant = email.get('merchant', 'Unknown')
            purchase_type = email.get('purchase_type', 'unknown')
            amount = email.get('amount', 0)
            
            # Summary by merchant
            if merchant not in summary['by_merchant']:
                summary['by_merchant'][merchant] = {'total_amount': 0, 'count': 0}
            summary['by_merchant'][merchant]['total_amount'] += amount
            summary['by_merchant'][merchant]['count'] += 1
            
            # Summary by purchase type
            if purchase_type not in summary['by_purchase_type']:
                summary['by_purchase_type'][purchase_type] = {'total_amount': 0, 'count': 0}
            summary['by_purchase_type'][purchase_type]['total_amount'] += amount
            summary['by_purchase_type'][purchase_type]['count'] += 1
            
        return summary
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating summary: {str(e)}")


@router.get("/{email_id}")
async def get_email_details(user_id: str, email_id: str):
    """
    Get full details of a specific email
    
    - **user_id**: The ID of the user the email belongs to
    - **email_id**: The ID of the email to fetch
    """
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("gmail_credentials"):
            raise HTTPException(status_code=401, detail="Gmail not authorized for this user")

        gmail_service = GmailService(user_credentials=user["gmail_credentials"])
        email_details = gmail_service.get_email_details(email_id)
        
        if not email_details:
            raise HTTPException(status_code=404, detail="Email not found")
            
        return email_details
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching email details: {str(e)}")
@router.get("/health")
def health_check():
    return {"status": "ok"}