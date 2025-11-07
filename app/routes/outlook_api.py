import os
import httpx
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient

# Router: will be mounted under "/api" in main.py
router = APIRouter(prefix="/outlook", tags=["Outlook"])

# --- Env & Config ---
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("SECERAT_ID")  # Using SECERAT_ID from .env
TENANT_ID = os.getenv("TENANT_ID")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# --- Database ---
db_client = MongoClient(os.getenv("MONGO_URI"))
db = db_client[os.getenv("DB_NAME")]
app_token_collection = db["outlook_app_tokens"]

class OutlookEmail(BaseModel):
    id: str
    subject: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    received_at: Optional[str] = None
    body_preview: Optional[str] = None
    has_attachments: Optional[bool] = False
    importance: Optional[str] = None

class OutlookEmailsResponse(BaseModel):
    emails: List[OutlookEmail]
    next_link: Optional[str] = None
    page_size: int
    user_email: Optional[str] = None

class UserInfo(BaseModel):
    id: str
    email: str
    display_name: str

async def _ensure_app_access_token():
    """
    Ensure we have a valid application access token, refresh if needed.
    """
    try:
        # Get current application token
        token_doc = app_token_collection.find_one({"token_type": "application"})
        
        if not token_doc:
            raise HTTPException(
                status_code=401,
                detail="No application token found. Please authorize the application first using /admin/authorize"
            )
        
        # Check if token is expired (with 5-minute buffer)
        if token_doc["expires_at"] <= datetime.utcnow() + timedelta(minutes=5):
            # Refresh the token using client credentials
            client_id = CLIENT_ID
            client_secret = CLIENT_SECRET
            tenant_id = TENANT_ID
            
            token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            
            token_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=token_data)
                
                if response.status_code != 200:
                    error_detail = response.json().get("error_description", "Unknown error")
                    raise HTTPException(
                        status_code=401,
                        detail=f"Token refresh failed: {error_detail}"
                    )
                
                token_response = response.json()
                
                # Update token in database
                token_doc = {
                    "token_type": "application",
                    "access_token": token_response["access_token"],
                    "expires_at": datetime.utcnow() + timedelta(seconds=token_response["expires_in"]),
                    "updated_at": datetime.utcnow()
                }
                
                app_token_collection.replace_one(
                    {"token_type": "application"},
                    token_doc,
                    upsert=True
                )
                
                return token_response["access_token"]
        
        return token_doc["access_token"]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token management error: {str(e)}")

@router.post("/admin/authorize")
async def admin_authorize_outlook():
    """
    Authorize the application to access all users' emails using client credentials flow.
    This requires admin consent in Azure AD.
    """
    try:
        client_id = CLIENT_ID
        client_secret = CLIENT_SECRET
        tenant_id = TENANT_ID
        
        if not all([client_id, client_secret, tenant_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing environment configuration. Ensure CLIENT_ID, SECERAT_ID, and TENANT_ID are set."
            )
        
        # Client credentials flow for application permissions
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=token_data)
            
            if response.status_code != 200:
                error_detail = response.json().get("error_description", "Unknown error")
                raise HTTPException(
                    status_code=400,
                    detail=f"Token exchange failed: {error_detail}"
                )
            
            token_response = response.json()
            
            # Store application token
            token_doc = {
                "token_type": "application",
                "access_token": token_response["access_token"],
                "expires_at": datetime.utcnow() + timedelta(seconds=token_response["expires_in"]),
                "created_at": datetime.utcnow()
            }
            
            # Update or insert application token
            app_token_collection.replace_one(
                {"token_type": "application"},
                token_doc,
                upsert=True
            )
            
            return {
                "message": "Application authorized successfully for organization-wide email access",
                "expires_in": token_response["expires_in"]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authorization failed: {str(e)}")

@router.get("/users", response_model=List[UserInfo])
async def get_all_users():
    """
    Get all users in the organization.
    """
    try:
        access_token = await _ensure_app_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}/users",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to fetch users: {response.text}"
                )
            
            users_data = response.json()
            users = []
            
            for user in users_data.get("value", []):
                users.append(UserInfo(
                    id=user["id"],
                    email=user.get("mail") or user.get("userPrincipalName", ""),
                    display_name=user.get("displayName", "")
                ))
            
            return users
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@router.get("/emails/all", response_model=List[OutlookEmailsResponse])
async def get_all_users_emails(
    top: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search keyword for subject/body"),
    folder: Optional[str] = Query("Inbox", description="Folder name, default Inbox"),
    only_unread: bool = Query(False, description="Return only unread emails")
):
    """
    Get emails from all users in the organization.
    """
    try:
        access_token = await _ensure_app_access_token()
        
        # First get all users
        async with httpx.AsyncClient() as client:
            users_response = await client.get(
                f"{GRAPH_API_BASE}/users",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if users_response.status_code != 200:
                raise HTTPException(
                    status_code=users_response.status_code,
                    detail=f"Failed to fetch users: {users_response.text}"
                )
            
            users_data = users_response.json()
            all_emails = []
            
            # Get emails for each user
            for user in users_data.get("value", []):
                user_id = user["id"]
                user_email = user.get("mail") or user.get("userPrincipalName", "")
                
                try:
                    # Build query parameters
                    query_params = [f"$top={top}"]
                    
                    if search:
                        query_params.append(f"$search=\"{search}\"")
                    
                    if only_unread:
                        query_params.append("$filter=isRead eq false")
                    
                    query_string = "&".join(query_params)
                    
                    # Get emails for this user
                    emails_url = f"{GRAPH_API_BASE}/users/{user_id}/mailFolders/{folder}/messages?{query_string}"
                    
                    emails_response = await client.get(
                        emails_url,
                        headers={"Authorization": f"Bearer {access_token}"}
                    )
                    
                    if emails_response.status_code == 200:
                        emails_data = emails_response.json()
                        emails = []
                        
                        for email in emails_data.get("value", []):
                            from_info = email.get("from", {}).get("emailAddress", {})
                            
                            emails.append(OutlookEmail(
                                id=email["id"],
                                subject=email.get("subject", ""),
                                from_email=from_info.get("address", ""),
                                from_name=from_info.get("name", ""),
                                received_at=email.get("receivedDateTime", ""),
                                body_preview=email.get("bodyPreview", ""),
                                has_attachments=email.get("hasAttachments", False),
                                importance=email.get("importance", "normal")
                            ))
                        
                        if emails:  # Only add if user has emails
                            all_emails.append(OutlookEmailsResponse(
                                emails=emails,
                                next_link=emails_data.get("@odata.nextLink"),
                                page_size=len(emails),
                                user_email=user_email
                            ))
                
                except Exception as user_error:
                    # Continue with other users if one fails
                    print(f"Failed to get emails for user {user_email}: {str(user_error)}")
                    continue
            
            return all_emails
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching emails: {str(e)}")

@router.get("/emails/user/{user_email}", response_model=OutlookEmailsResponse)
async def get_user_emails_by_email(
    user_email: str,
    top: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search keyword for subject/body"),
    folder: Optional[str] = Query("Inbox", description="Folder name, default Inbox"),
    only_unread: bool = Query(False, description="Return only unread emails")
):
    """
    Get emails for a specific user by their email address.
    """
    try:
        access_token = await _ensure_app_access_token()
        
        # Build query parameters
        query_params = [f"$top={top}"]
        
        if search:
            query_params.append(f"$search=\"{search}\"")
        
        if only_unread:
            query_params.append("$filter=isRead eq false")
        
        query_string = "&".join(query_params)
        
        async with httpx.AsyncClient() as client:
            # Get emails for the specified user
            emails_url = f"{GRAPH_API_BASE}/users/{user_email}/mailFolders/{folder}/messages?{query_string}"
            
            response = await client.get(
                emails_url,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to fetch emails for user {user_email}: {response.text}"
                )
            
            emails_data = response.json()
            emails = []
            
            for email in emails_data.get("value", []):
                from_info = email.get("from", {}).get("emailAddress", {})
                
                emails.append(OutlookEmail(
                    id=email["id"],
                    subject=email.get("subject", ""),
                    from_email=from_info.get("address", ""),
                    from_name=from_info.get("name", ""),
                    received_at=email.get("receivedDateTime", ""),
                    body_preview=email.get("bodyPreview", ""),
                    has_attachments=email.get("hasAttachments", False),
                    importance=email.get("importance", "normal")
                ))
            
            return OutlookEmailsResponse(
                emails=emails,
                next_link=emails_data.get("@odata.nextLink"),
                page_size=len(emails),
                user_email=user_email
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching emails: {str(e)}")

@router.get("/admin/consent-url")
async def get_admin_consent_url():
    """
    Generate the admin consent URL for granting application permissions.
    """
    try:
        client_id = CLIENT_ID
        tenant_id = TENANT_ID
        
        if not all([client_id, tenant_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing environment configuration. Ensure CLIENT_ID and TENANT_ID are set."
            )
        
        # Admin consent URL for application permissions
        # Using the correct route path that includes the /api/outlook prefix
        redirect_uri = "http://localhost:8002/api/outlook/auth/callback"
        consent_url = (
            f"https://login.microsoftonline.com/{tenant_id}/adminconsent?"
            f"client_id={client_id}&"
            f"state=admin_consent&"
            f"redirect_uri={redirect_uri}"
        )
        
        return {
            "consent_url": consent_url,
            "instructions": [
                "1. First, add the redirect URI 'http://localhost:8002/api/outlook/auth/callback' to your Azure AD app registration",
                "2. Click the consent_url above or copy it to your browser",
                "3. Sign in with your Azure AD admin account",
                "4. Review and accept the permissions",
                "5. After consent, call POST /api/outlook/admin/authorize to get the application token"
            ],
            "required_permissions": [
                "Mail.Read - Read mail in all mailboxes",
                "User.Read.All - Read all users' profiles"
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating consent URL: {str(e)}")

@router.get("/auth/callback")
async def admin_consent_callback(admin_consent: str = None, state: str = None, error: str = None):
    """
    Handle the admin consent callback.
    """
    if error:
        return {
            "status": "error",
            "message": f"Admin consent failed: {error}",
            "next_step": "Please try the consent process again or contact your Azure AD administrator"
        }
    
    if admin_consent == "True":
        return {
            "status": "success",
            "message": "Admin consent granted successfully!",
            "next_step": "Now call POST /api/outlook/admin/authorize to get the application token"
        }
    
    return {
        "status": "unknown",
        "message": "Admin consent status unclear",
        "admin_consent": admin_consent,
        "state": state
    }

@router.get("/admin/status")
async def get_admin_status():
    """
    Check the current authorization status and provide guidance.
    """
    try:
        # Check if we have an application token
        token_doc = app_token_collection.find_one({"token_type": "application"})
        
        if not token_doc:
            return {
                "status": "not_authorized",
                "message": "Application not authorized yet",
                "steps": [
                    "1. First, grant admin consent using GET /api/outlook/admin/consent-url",
                    "2. Then authorize the application using POST /api/outlook/admin/authorize"
                ]
            }
        
        # Check if token is expired
        if token_doc["expires_at"] <= datetime.utcnow():
            return {
                "status": "token_expired",
                "message": "Application token has expired",
                "expires_at": token_doc["expires_at"].isoformat(),
                "steps": [
                    "Call POST /api/outlook/admin/authorize to refresh the token"
                ]
            }
        
        # Try to test the token with a simple API call
        try:
            access_token = await _ensure_app_access_token()
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GRAPH_API_BASE}/users?$top=1",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                if response.status_code == 200:
                    return {
                        "status": "authorized_and_working",
                        "message": "Application is properly authorized and working",
                        "token_expires_at": token_doc["expires_at"].isoformat(),
                        "test_result": "Successfully accessed Microsoft Graph API"
                    }
                elif response.status_code == 403:
                    error_data = response.json()
                    return {
                        "status": "insufficient_permissions",
                        "message": "Application token works but lacks required permissions",
                        "error": error_data,
                        "steps": [
                            "1. Go to Azure Portal > App Registrations > Your App > API permissions",
                            "2. Add Application permissions: Mail.Read and User.Read.All",
                            "3. Click 'Grant admin consent for [Your Organization]'",
                            "4. Then call POST /api/outlook/admin/authorize to get a new token"
                        ]
                    }
                else:
                    return {
                        "status": "token_error",
                        "message": f"Token validation failed with status {response.status_code}",
                        "error": response.text,
                        "steps": [
                            "Call POST /api/outlook/admin/authorize to get a fresh token"
                        ]
                    }
        
        except Exception as token_error:
            return {
                "status": "token_management_error",
                "message": f"Error managing application token: {str(token_error)}",
                "steps": [
                    "Call POST /api/outlook/admin/authorize to get a fresh token"
                ]
            }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error checking status: {str(e)}"
        }

@router.get("/health")
async def health_check():
    return {"status": "ok", "message": "Outlook API service is running"}