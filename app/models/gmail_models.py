from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class PurchaseType(str, Enum):
    RECEIPT = "receipt"
    SHIPPING = "shipping"
    REFUND = "refund"
    UNKNOWN = "unknown"

class EmailStatus(str, Enum):
    READ = "read"
    UNREAD = "unread"
    STARRED = "starred"

class GmailEmail(BaseModel):
    """Base Gmail email model"""
    id: str = Field(..., description="Gmail message ID")
    thread_id: str = Field(..., description="Gmail thread ID")
    label_ids: List[str] = Field(default=[], description="Gmail label IDs")
    snippet: str = Field(..., description="Email snippet/preview")
    internal_date: str = Field(..., description="Internal Gmail date")
    size_estimate: int = Field(..., description="Estimated size in bytes")

class EmailHeaders(BaseModel):
    """Email header information"""
    sender: str = Field(..., description="Full sender string")
    sender_name: str = Field(..., description="Extracted sender name")
    sender_email: str = Field(..., description="Extracted sender email")
    recipient: Optional[str] = Field(None, description="Recipient email")
    subject: str = Field(..., description="Email subject")
    date: str = Field(..., description="Email date string")
    parsed_date: Optional[datetime] = Field(None, description="Parsed datetime")

class PurchaseInfo(BaseModel):
    """Extracted purchase information"""
    amount: Optional[float] = Field(None, description="Purchase amount", ge=0)
    currency: Optional[str] = Field(None, description="Currency code")
    order_number: Optional[str] = Field(None, description="Order/transaction number")
    merchant: Optional[str] = Field(None, description="Merchant name")
    purchase_type: PurchaseType = Field(PurchaseType.UNKNOWN, description="Type of purchase")

class PurchaseEmail(GmailEmail, EmailHeaders, PurchaseInfo):
    """Complete purchase email model combining all information"""
    body: Optional[str] = Field(None, description="Email body content")
    
    class Config:
        use_enum_values = True

class EmailSearchQuery(BaseModel):
    """Gmail search query parameters"""
    query: str = Field(..., description="Gmail search query", min_length=1)
    max_results: int = Field(50, description="Maximum results", ge=1, le=500)
    page_token: Optional[str] = Field(None, description="Pagination token")

class EmailFilter(BaseModel):
    """Email filtering parameters"""
    sender: Optional[str] = Field(None, description="Filter by sender")
    subject_contains: Optional[str] = Field(None, description="Subject contains text")
    min_amount: Optional[float] = Field(None, description="Minimum amount", ge=0)
    max_amount: Optional[float] = Field(None, description="Maximum amount", ge=0)
    date_from: Optional[str] = Field(None, description="Date from (YYYY-MM-DD)")
    date_to: Optional[str] = Field(None, description="Date to (YYYY-MM-DD)")
    merchant: Optional[str] = Field(None, description="Merchant name")
    purchase_type: Optional[PurchaseType] = Field(None, description="Purchase type")
    
    class Config:
        use_enum_values = True

class PaginatedEmailResponse(BaseModel):
    """Paginated email response"""
    emails: List[PurchaseEmail]
    next_page_token: Optional[str] = Field(None, description="Next page token")
    total_found: int = Field(..., description="Total emails found")
    page_size: int = Field(..., description="Current page size")

class EmailSearchResponse(BaseModel):
    """Email search response"""
    emails: List[PurchaseEmail]
    total_found: int = Field(..., description="Total emails found")
    query: str = Field(..., description="Search query used")
    max_results: int = Field(..., description="Maximum results requested")

class MerchantSummary(BaseModel):
    """Merchant summary statistics"""
    name: str = Field(..., description="Merchant name")
    count: int = Field(..., description="Number of emails")
    total_amount: Optional[float] = Field(None, description="Total amount spent")
    avg_amount: Optional[float] = Field(None, description="Average amount")

class PurchaseSummary(BaseModel):
    """Purchase emails summary statistics"""
    total_emails: int = Field(..., description="Total purchase emails")
    total_amount: float = Field(..., description="Total amount spent")
    average_amount: float = Field(..., description="Average purchase amount")
    currency: str = Field("USD", description="Primary currency")
    top_merchants: List[MerchantSummary] = Field(..., description="Top merchants by count")
    purchase_types: Dict[str, int] = Field(..., description="Purchase type breakdown")
    emails_with_amounts: int = Field(..., description="Emails with amount data")
    sample_size: int = Field(..., description="Sample size analyzed")

class GmailAuthResponse(BaseModel):
    """Gmail authentication response"""
    success: bool = Field(..., description="Authentication success")
    message: str = Field(..., description="Status message")
    auth_url: Optional[str] = Field(None, description="Authentication URL if needed")

class GmailHealthResponse(BaseModel):
    """Gmail API health check response"""
    status: str = Field(..., description="Health status")
    gmail_connected: bool = Field(..., description="Gmail connection status")
    email_address: Optional[str] = Field(None, description="Connected email address")
    messages_total: Optional[int] = Field(None, description="Total messages in account")
    threads_total: Optional[int] = Field(None, description="Total threads in account")
    error: Optional[str] = Field(None, description="Error message if any")

class EmailFilterResponse(BaseModel):
    """Filtered email response"""
    emails: List[PurchaseEmail]
    total_found: int = Field(..., description="Total emails found after filtering")
    filters_applied: EmailFilter = Field(..., description="Filters that were applied")
    gmail_query: str = Field(..., description="Gmail query used")

class EmailAttachment(BaseModel):
    """Email attachment information"""
    filename: str = Field(..., description="Attachment filename")
    mime_type: str = Field(..., description="MIME type")
    size: int = Field(..., description="Size in bytes")
    attachment_id: str = Field(..., description="Gmail attachment ID")

class DetailedEmail(PurchaseEmail):
    """Detailed email with additional information"""
    attachments: List[EmailAttachment] = Field(default=[], description="Email attachments")
    raw_headers: Dict[str, str] = Field(default={}, description="All email headers")
    html_body: Optional[str] = Field(None, description="HTML body content")
    text_body: Optional[str] = Field(None, description="Plain text body content")

# Request models for API endpoints
class BulkEmailRequest(BaseModel):
    """Request for bulk email operations"""
    email_ids: List[str] = Field(..., description="List of email IDs")
    operation: str = Field(..., description="Operation to perform")

class EmailExportRequest(BaseModel):
    """Request for exporting emails"""
    filters: EmailFilter = Field(..., description="Filters to apply")
    format: str = Field("json", description="Export format (json, csv)")
    include_body: bool = Field(False, description="Include email body content")

class EmailAnalyticsRequest(BaseModel):
    """Request for email analytics"""
    date_range: int = Field(30, description="Date range in days", ge=1, le=365)
    group_by: str = Field("merchant", description="Group analytics by (merchant, date, type)")

class EmailAnalyticsResponse(BaseModel):
    """Email analytics response"""
    period: str = Field(..., description="Analysis period")
    total_emails: int = Field(..., description="Total emails in period")
    total_amount: float = Field(..., description="Total amount in period")
    daily_breakdown: List[Dict[str, Any]] = Field(..., description="Daily breakdown")
    merchant_breakdown: List[Dict[str, Any]] = Field(..., description="Merchant breakdown")
    category_breakdown: List[Dict[str, Any]] = Field(..., description="Category breakdown")