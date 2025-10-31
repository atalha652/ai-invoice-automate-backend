from typing import Optional
from fastapi import APIRouter, File, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import os
import certifi
from fastapi import APIRouter, HTTPException
from app.routes.auth import get_current_user
from pymongo import MongoClient
from datetime import datetime
import certifi
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from fastapi import APIRouter, File, UploadFile, Depends, Form, HTTPException, UploadFile
from datetime import datetime
from bson import ObjectId
import boto3
import pytesseract
import os
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from typing import List, Optional
from fastapi import APIRouter, File, UploadFile, Form, Depends, HTTPException
from datetime import datetime
from fastapi import Query
from fastapi.responses import FileResponse
# Load env variables
load_dotenv()

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Font config for WeasyPrint (if needed)
os.environ["FONTCONFIG_FILE"] = r"C:\OCR Project\fonts\fonts.conf"


router = APIRouter(prefix="/accounting/voucher", tags=["vouchers"])

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
voucher_collection = db["voucher"]
ocr_collection = db["ocr"]  # Replace 'db' with your actual DB object
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")


s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
   region_name="eu-north-1"
)

bucket_name = "ai-auto-invoice"



def upload_to_s3(user_id, project_id, file: UploadFile, folder_type="Package"):
    """Uploads file to S3 in Images/Package or Images/Result folder."""
    if folder_type not in ["Package", "Result"]:
        raise ValueError("folder_type must be 'Package' or 'Result'")

    s3_folder = f"{user_id}/{project_id}/Images/{folder_type}/"

    # Save temporarily
    temp_path = f"{file.filename}"
    with open(temp_path, "wb") as buffer:
        buffer.write(file.file.read())

    # Upload to S3
    content_type = file.content_type
    s3.upload_file(
        Filename=temp_path,
        Bucket=bucket_name,
        Key=f"{s3_folder}{file.filename}",
        ExtraArgs={
            "ContentType": content_type,
            "ContentDisposition": "inline"
        }
    )

    # Remove temp file
    os.remove(temp_path)

    return f"{s3_folder}{file.filename}"

@router.post("/upload")
async def upload_voucher(
    user_id: str = Form(..., description="User ID of the person uploading the voucher"),
    files: List[UploadFile] = File(...),   # Accept multiple files
):
    # Step 1: Validate all files
    for file in files:
        if file.content_type not in ["image/png", "image/jpeg", "application/pdf"]:
            raise HTTPException(status_code=400, detail="Only image or PDF allowed")

    # Step 2: Create new voucher record with status "pending"
    new_voucher = {
        "user_id": user_id,
        "status": "pending",
        "created_at": datetime.utcnow(),
        "files": []  # to store file metadata
    }
    result = voucher_collection.insert_one(new_voucher)
    voucher_id = str(result.inserted_id)

    # Step 3: Upload each file to S3
    file_records = []
    for file in files:
        s3_key = upload_to_s3(
            user_id=user_id,
            project_id=voucher_id,
            file=file,
            folder_type="Package"
        )
        file_url = s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': s3_key,
                'ResponseContentType': file.content_type
            },
            ExpiresIn=86400  # 24 hours
        )
        file_records.append({
            "name": file.filename,
            "file_url": file_url,
            "s3_key": s3_key
        })

    # Step 4: Update voucher with uploaded file info
    voucher_collection.update_one(
        {"_id": result.inserted_id},
        {"$set": {"files": file_records}}
    )

    # Step 5: Return response
    return {
        "message": "Voucher uploaded successfully",
        "voucher_id": voucher_id,
        "user_id": user_id,
        "files": file_records,
        "status": "pending"
    }


@router.get("/{voucher_id}")
async def get_voucher_by_id(
    voucher_id: str,
    user_id: Optional[str] = Query(None, description="Optional user ID to verify ownership")
):
    """
    Get a specific voucher by its ID.
    Example: GET /accounting/vouchers/68f880bcadf2e0b66e482d11?user_id=68a46f1d960572d49facd776
    """
    try:
        # Convert string to ObjectId
        obj_id = ObjectId(voucher_id)
        query = {"_id": obj_id}
        
        # Debug: Check if voucher exists without user_id filter first
        voucher_exists = voucher_collection.find_one({"_id": obj_id})
        
        # Optionally filter by user_id if provided
        if user_id:
            query["user_id"] = user_id
        
        voucher = voucher_collection.find_one(query)
        
        if not voucher:
            if voucher_exists:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Voucher exists but user_id mismatch. Voucher user_id: {voucher_exists.get('user_id')}"
                )
            else:
                raise HTTPException(status_code=404, detail=f"Voucher not found with ID: {voucher_id}")
        
        # Convert ObjectId and datetime for readability
        voucher["_id"] = str(voucher["_id"])
        if "created_at" in voucher:
            voucher["created_at"] = voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return voucher
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid voucher ID format: {str(e)}")


@router.get("/")
async def get_vouchers(
    user_id: str = Query(..., description="User ID to fetch vouchers for"),
    status: Optional[str] = Query(None, description="Filter vouchers by status (e.g., pending, completed)")
):
    """
    Get all vouchers for a specific user, optionally filtered by status.
    Example: GET /accounting/vouchers?user_id=123&status=pending
    """
    query = {"user_id": user_id}

    if status:
        query["status"] = status

    vouchers = list(voucher_collection.find(query))

    if not vouchers:
        raise HTTPException(status_code=404, detail="No vouchers found")

    # Convert ObjectId and datetime for readability
    for voucher in vouchers:
        voucher["_id"] = str(voucher["_id"])
        if "created_at" in voucher:
            voucher["created_at"] = voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")

    return {
        "count": len(vouchers),
        "vouchers": vouchers
    }



# Pydantic models for request bodies
class ApprovalRequest(BaseModel):
    approver_id: str = Field(..., description="ID of the user who will approve")


class RejectionRequest(BaseModel):
    rejected_by: str = Field(..., description="ID of the user rejecting the voucher")
    rejection_reason: str = Field(..., description="Reason for rejection")


class ClassificationRequest(BaseModel):
    document_type: Optional[str] = Field(None, description="Manual document type (supplier_invoice, expense, receipt, purchase_order, credit_note, etc.)")
    use_ai: bool = Field(False, description="Use AI to auto-classify the document")


class ForwardRequest(BaseModel):
    current_approver_id: str = Field(..., description="ID of the current approver forwarding the voucher")
    new_approver_id: str = Field(..., description="ID of the new approver to forward to")
    reason: Optional[str] = Field(None, description="Reason for forwarding")


# ==================== AUDIT TRAIL MODELS ====================
class AuditTrailEntry(BaseModel):
    action: str = Field(..., description="Action performed (e.g., 'approval_requested', 'approved', 'rejected', 'forwarded')")
    user_id: str = Field(..., description="ID of the user who performed the action")
    user_name: Optional[str] = Field(None, description="Name of the user who performed the action")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When the action was performed")
    details: Optional[dict] = Field(None, description="Additional details about the action")
    notes: Optional[str] = Field(None, description="Optional notes or comments")


# ==================== AUDIT TRAIL HELPER FUNCTIONS ====================
def add_audit_trail_entry(voucher_id: str, action: str, user_id: str, user_name: str = None, details: dict = None, notes: str = None):
    """Add an audit trail entry to a voucher."""
    try:
        # Create audit trail entry
        audit_entry = {
            "action": action,
            "user_id": user_id,
            "user_name": user_name,
            "timestamp": datetime.utcnow(),
            "details": details or {},
            "notes": notes
        }
        
        # Add to voucher's audit trail
        result = voucher_collection.update_one(
            {"_id": ObjectId(voucher_id)},
            {"$push": {"audit_trail": audit_entry}}
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"Error adding audit trail entry: {str(e)}")
        return False


def format_audit_trail(audit_trail: list) -> list:
    """Format audit trail entries for API response."""
    formatted_trail = []
    for entry in audit_trail:
        formatted_entry = {
            "action": entry.get("action"),
            "user_id": entry.get("user_id"),
            "user_name": entry.get("user_name"),
            "timestamp": entry.get("timestamp").strftime("%Y-%m-%d %H:%M:%S") if entry.get("timestamp") else None,
            "details": entry.get("details", {}),
            "notes": entry.get("notes")
        }
        formatted_trail.append(formatted_entry)
    return formatted_trail


@router.post("/{voucher_id}/approve-request")
async def send_for_approval(
    voucher_id: str,
    approval_data: ApprovalRequest
):
    """
    Send a voucher for approval.
    Changes status to 'awaiting_approval' and assigns an approver.
    Example: POST /accounting/voucher/68f880bcadf2e0b66e482d11/approve-request
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Check if voucher is in a valid state for approval request
        current_status = voucher.get("status")
        if current_status in ["approved", "rejected"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot send for approval. Voucher is already {current_status}"
            )
        
        # Update voucher with approval request details
        update_data = {
            "status": "awaiting_approval",
            "approver_id": approval_data.approver_id,
            "approval_requested_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        if approval_data.approver_name:
            update_data["approver_name"] = approval_data.approver_name
        
        if approval_data.notes:
            update_data["approval_notes"] = approval_data.notes
        
        result = voucher_collection.update_one(
            {"_id": obj_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update voucher")
        
        # Add audit trail entry
        audit_details = {
            "approver_id": approval_data.approver_id,
            "approver_name": getattr(approval_data, 'approver_name', None),
            "previous_status": current_status
        }
        add_audit_trail_entry(
            voucher_id=voucher_id,
            action="approval_requested",
            user_id=approval_data.approver_id,  # The requester ID should be passed separately in a real app
            user_name=getattr(approval_data, 'approver_name', None),
            details=audit_details,
            notes=getattr(approval_data, 'notes', None)
        )
        
        # Get updated voucher
        updated_voucher = voucher_collection.find_one({"_id": obj_id})
        updated_voucher["_id"] = str(updated_voucher["_id"])
        if "created_at" in updated_voucher:
            updated_voucher["created_at"] = updated_voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "approval_requested_at" in updated_voucher:
            updated_voucher["approval_requested_at"] = updated_voucher["approval_requested_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in updated_voucher:
            updated_voucher["updated_at"] = updated_voucher["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": "Voucher sent for approval successfully",
            "voucher": updated_voucher
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.post("/{voucher_id}/approve")
async def approve_voucher(
    voucher_id: str,
    approver_id: str = Query(..., description="ID of the user approving the voucher"),
    notes: Optional[str] = Query(None, description="Approval notes")
):
    """
    Approve a voucher.
    Changes status to 'approved'.
    Example: POST /accounting/voucher/68f880bcadf2e0b66e482d11/approve?approver_id=123
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Check if voucher is awaiting approval
        current_status = voucher.get("status")
        if current_status != "awaiting_approval":
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot approve. Voucher status is '{current_status}', expected 'awaiting_approval'"
            )
        
        # Verify approver
        assigned_approver = voucher.get("approver_id")
        if assigned_approver and assigned_approver != approver_id:
            raise HTTPException(
                status_code=403, 
                detail=f"Unauthorized. This voucher is assigned to approver: {assigned_approver}"
            )
        
        # Update voucher to approved
        update_data = {
            "status": "approved",
            "approved_by": approver_id,
            "approved_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        if notes:
            update_data["approval_notes"] = notes
        
        result = voucher_collection.update_one(
            {"_id": obj_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to approve voucher")
        
        # Add audit trail entry
        audit_details = {
            "approved_by": approver_id,
            "previous_status": current_status,
            "assigned_approver": assigned_approver
        }
        add_audit_trail_entry(
            voucher_id=voucher_id,
            action="approved",
            user_id=approver_id,
            details=audit_details,
            notes=notes
        )
        
        # Get updated voucher
        updated_voucher = voucher_collection.find_one({"_id": obj_id})
        updated_voucher["_id"] = str(updated_voucher["_id"])
        if "created_at" in updated_voucher:
            updated_voucher["created_at"] = updated_voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "approved_at" in updated_voucher:
            updated_voucher["approved_at"] = updated_voucher["approved_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in updated_voucher:
            updated_voucher["updated_at"] = updated_voucher["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": "Voucher approved successfully",
            "voucher": updated_voucher
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.post("/{voucher_id}/reject")
async def reject_voucher(
    voucher_id: str,
    rejection_data: RejectionRequest
):
    """
    Reject a voucher.
    Changes status to 'rejected' with reason.
    Example: POST /accounting/voucher/68f880bcadf2e0b66e482d11/reject
    Body: {
        "rejected_by": "123",
        "rejection_reason": "Missing documentation"
    }
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Check if voucher can be rejected
        current_status = voucher.get("status")
        if current_status in ["approved", "rejected"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot reject. Voucher is already {current_status}"
            )
        
        # Update voucher to rejected
        update_data = {
            "status": "rejected",
            "rejected_by": rejection_data.rejected_by,
            "rejection_reason": rejection_data.rejection_reason,
            "rejected_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = voucher_collection.update_one(
            {"_id": obj_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to reject voucher")
        
        # Add audit trail entry
        audit_details = {
            "rejected_by": rejection_data.rejected_by,
            "rejection_reason": rejection_data.rejection_reason,
            "previous_status": current_status
        }
        add_audit_trail_entry(
            voucher_id=voucher_id,
            action="rejected",
            user_id=rejection_data.rejected_by,
            details=audit_details,
            notes=rejection_data.rejection_reason
        )
        
        # Get updated voucher
        updated_voucher = voucher_collection.find_one({"_id": obj_id})
        updated_voucher["_id"] = str(updated_voucher["_id"])
        if "created_at" in updated_voucher:
            updated_voucher["created_at"] = updated_voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "rejected_at" in updated_voucher:
            updated_voucher["rejected_at"] = updated_voucher["rejected_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in updated_voucher:
            updated_voucher["updated_at"] = updated_voucher["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": "Voucher rejected successfully",
            "voucher": updated_voucher
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")




@router.patch("/{voucher_id}/classify")
async def classify_voucher(
    voucher_id: str,
    classification_data: ClassificationRequest
):
    """
    Classify a voucher by document type.
    Can be done manually or using AI auto-classification.
    
    Document types: supplier_invoice, expense, receipt, purchase_order, credit_note, debit_note, payment_voucher
    
    Example 1 (Manual): PATCH /accounting/voucher/68f880bcadf2e0b66e482d11/classify
    Body: {"document_type": "supplier_invoice"}
    
    Example 2 (AI): PATCH /accounting/voucher/68f880bcadf2e0b66e482d11/classify
    Body: {"use_ai": true}
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        document_type = None
        classification_method = "manual"
        ai_confidence = None
        
        # AI Classification
        if classification_data.use_ai:
            classification_method = "ai"
            
            # Check if voucher has files
            files = voucher.get("files", [])
            if not files:
                raise HTTPException(status_code=400, detail="No files found in voucher for AI classification")
            
            # Use OpenAI to classify the document
            try:
                import openai
                openai.api_key = OPENAI_KEY
                
                # Get the first file URL or OCR data
                file_info = files[0]
                
                # Check if OCR data exists for this voucher
                ocr_data = ocr_collection.find_one({"voucher_id": voucher_id})
                
                if ocr_data and ocr_data.get("extracted_text"):
                    text_content = ocr_data.get("extracted_text", "")
                else:
                    text_content = f"Document filename: {file_info.get('name', 'unknown')}"
                
                # Create prompt for classification
                prompt = f"""Analyze this document and classify it into one of these categories:
- supplier_invoice: Invoice from a supplier/vendor
- expense: Employee expense report or reimbursement
- receipt: Purchase receipt
- purchase_order: Purchase order document
- credit_note: Credit note from supplier
- debit_note: Debit note
- payment_voucher: Payment voucher or proof of payment

Document content:
{text_content[:1000]}

Respond with ONLY the category name and confidence (0-100). Format: category|confidence
Example: supplier_invoice|95"""

                response = openai.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a document classification expert."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=50
                )
                
                result = response.choices[0].message.content.strip()
                
                # Parse result
                if "|" in result:
                    document_type, confidence = result.split("|")
                    document_type = document_type.strip()
                    ai_confidence = float(confidence.strip())
                else:
                    document_type = result.strip()
                    ai_confidence = 85.0
                
            except Exception as ai_error:
                raise HTTPException(
                    status_code=500, 
                    detail=f"AI classification failed: {str(ai_error)}"
                )
        
        # Manual Classification
        elif classification_data.document_type:
            document_type = classification_data.document_type
            classification_method = "manual"
        else:
            raise HTTPException(
                status_code=400, 
                detail="Either provide 'document_type' for manual classification or set 'use_ai' to true"
            )
        
        # Validate document type
        valid_types = [
            "supplier_invoice", "expense", "receipt", "purchase_order", 
            "credit_note", "debit_note", "payment_voucher"
        ]
        if document_type not in valid_types:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid document type. Must be one of: {', '.join(valid_types)}"
            )
        
        # Update voucher with classification
        update_data = {
            "document_type": document_type,
            "classification_method": classification_method,
            "classified_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        if ai_confidence:
            update_data["ai_confidence"] = ai_confidence
        
        result = voucher_collection.update_one(
            {"_id": obj_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to classify voucher")
        
        # Get updated voucher
        updated_voucher = voucher_collection.find_one({"_id": obj_id})
        updated_voucher["_id"] = str(updated_voucher["_id"])
        if "created_at" in updated_voucher:
            updated_voucher["created_at"] = updated_voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "classified_at" in updated_voucher:
            updated_voucher["classified_at"] = updated_voucher["classified_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in updated_voucher:
            updated_voucher["updated_at"] = updated_voucher["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": f"Voucher classified successfully as '{document_type}' using {classification_method}",
            "document_type": document_type,
            "classification_method": classification_method,
            "ai_confidence": ai_confidence,
            "voucher": updated_voucher
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.get("/approvals/pending")
async def get_pending_vouchers(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    limit: int = Query(50, description="Number of results to return", ge=1, le=100),
    offset: int = Query(0, description="Number of results to skip", ge=0)
):
    """
    Get all vouchers with status 'pending'.
    
    Examples:
    - GET /accounting/voucher/pending
    - GET /accounting/voucher/pending?user_id=123
    - GET /accounting/voucher/pending?limit=20&offset=0
    """
    try:
        # Build query for pending status
        query = {"status": "pending"}
        
        # Add user filter if provided
        if user_id:
            query["user_id"] = user_id
        
        # Get total count
        total_count = voucher_collection.count_documents(query)
        
        # Get vouchers with pagination
        vouchers = list(
            voucher_collection.find(query)
            .sort("created_at", -1)  # Most recent first
            .skip(offset)
            .limit(limit)
        )
        
        # Format vouchers
        formatted_vouchers = []
        for voucher in vouchers:
            formatted_voucher = {
                "_id": str(voucher["_id"]),
                "user_id": voucher.get("user_id"),
                "status": voucher.get("status"),
                "document_type": voucher.get("document_type"),
                "created_at": voucher.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if voucher.get("created_at") else None,
                "updated_at": voucher.get("updated_at").strftime("%Y-%m-%d %H:%M:%S") if voucher.get("updated_at") else None,
                "files_count": len(voucher.get("files", [])),
                "files": voucher.get("files", [])
            }
            formatted_vouchers.append(formatted_voucher)
        
        # Calculate pagination info
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0
        current_page = (offset // limit) + 1
        has_next = offset + limit < total_count
        has_previous = offset > 0
        
        return {
            "status": "pending",
            "total_count": total_count,
            "count": len(formatted_vouchers),
            "pagination": {
                "current_page": current_page,
                "total_pages": total_pages,
                "limit": limit,
                "offset": offset,
                "has_next": has_next,
                "has_previous": has_previous
            },
            "vouchers": formatted_vouchers
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.patch("/{voucher_id}/forward")
async def forward_voucher(
    voucher_id: str,
    forward_data: ForwardRequest
):
    """
    Forward/Reassign a voucher to another approver.
    Only the current assigned approver can forward the voucher.
    
    Example: PATCH /accounting/voucher/68f880bcadf2e0b66e482d11/forward
    Body: {
        "current_approver_id": "123",
        "new_approver_id": "456",
        "reason": "This requires finance team approval"
    }
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Check if voucher is in awaiting_approval status
        current_status = voucher.get("status")
        if current_status != "awaiting_approval":
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot forward voucher. Status is '{current_status}', expected 'awaiting_approval'"
            )
        
        # Verify current user is the assigned approver
        current_approver = voucher.get("approver_id")
        if current_approver != forward_data.current_approver_id:
            raise HTTPException(
                status_code=403, 
                detail=f"Unauthorized. Only the assigned approver can forward this voucher. Current approver: {current_approver}"
            )
        
        # Check if forwarding to the same approver
        if forward_data.new_approver_id == forward_data.current_approver_id:
            raise HTTPException(
                status_code=400, 
                detail="Cannot forward to the same approver"
            )
        
        # Create forwarding history entry
        forwarding_history = voucher.get("forwarding_history", [])
        forwarding_entry = {
            "from_approver_id": forward_data.current_approver_id,
            "to_approver_id": forward_data.new_approver_id,
            "forwarded_at": datetime.utcnow(),
            "reason": forward_data.reason
        }
        forwarding_history.append(forwarding_entry)
        
        # Update voucher with new approver
        update_data = {
            "approver_id": forward_data.new_approver_id,
            "previous_approver_id": forward_data.current_approver_id,
            "forwarding_history": forwarding_history,
            "forwarded_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        if forward_data.reason:
            update_data["forward_reason"] = forward_data.reason
        
        result = voucher_collection.update_one(
            {"_id": obj_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to forward voucher")
        
        # Add audit trail entry
        audit_details = {
            "from_approver_id": forward_data.current_approver_id,
            "to_approver_id": forward_data.new_approver_id,
            "reason": forward_data.reason
        }
        add_audit_trail_entry(
            voucher_id=voucher_id,
            action="forwarded",
            user_id=forward_data.current_approver_id,
            details=audit_details,
            notes=forward_data.reason
        )
        
        # Get updated voucher
        updated_voucher = voucher_collection.find_one({"_id": obj_id})
        updated_voucher["_id"] = str(updated_voucher["_id"])
        if "created_at" in updated_voucher:
            updated_voucher["created_at"] = updated_voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "forwarded_at" in updated_voucher:
            updated_voucher["forwarded_at"] = updated_voucher["forwarded_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in updated_voucher:
            updated_voucher["updated_at"] = updated_voucher["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        # Format forwarding history
        if "forwarding_history" in updated_voucher:
            for entry in updated_voucher["forwarding_history"]:
                if "forwarded_at" in entry:
                    entry["forwarded_at"] = entry["forwarded_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": f"Voucher forwarded successfully from approver {forward_data.current_approver_id} to {forward_data.new_approver_id}",
            "previous_approver_id": forward_data.current_approver_id,
            "new_approver_id": forward_data.new_approver_id,
            "forward_reason": forward_data.reason,
            "voucher": updated_voucher
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.get("/{voucher_id}/forwarding-history")
async def get_forwarding_history(voucher_id: str):
    """
    Get the forwarding history of a voucher.
    Shows all approvers the voucher has been forwarded to.
    
    Example: GET /accounting/voucher/68f880bcadf2e0b66e482d11/forwarding-history
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Get forwarding history
        forwarding_history = voucher.get("forwarding_history", [])
        
        # Format dates
        formatted_history = []
        for entry in forwarding_history:
            formatted_entry = {
                "from_approver_id": entry.get("from_approver_id"),
                "to_approver_id": entry.get("to_approver_id"),
                "forwarded_at": entry.get("forwarded_at").strftime("%Y-%m-%d %H:%M:%S") if entry.get("forwarded_at") else None,
                "reason": entry.get("reason")
            }
            formatted_history.append(formatted_entry)
        
        return {
            "voucher_id": voucher_id,
            "current_approver_id": voucher.get("approver_id"),
            "total_forwards": len(formatted_history),
            "forwarding_history": formatted_history
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.get("/{voucher_id}/audit-trail")
async def get_voucher_audit_trail(voucher_id: str):
    """
    Get the complete audit trail for a specific voucher.
    Shows all actions performed on the voucher with timestamps and details.
    
    Example: GET /accounting/voucher/68f880bcadf2e0b66e482d11/audit-trail
    """
    try:
        obj_id = ObjectId(voucher_id)
        
        # Check if voucher exists
        voucher = voucher_collection.find_one({"_id": obj_id})
        if not voucher:
            raise HTTPException(status_code=404, detail="Voucher not found")
        
        # Get audit trail
        audit_trail = voucher.get("audit_trail", [])
        
        # Format audit trail
        formatted_trail = format_audit_trail(audit_trail)
        
        return {
            "voucher_id": voucher_id,
            "total_entries": len(formatted_trail),
            "audit_trail": formatted_trail
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.get("/audit-trail")
async def get_audit_trails(
    user_id: Optional[str] = Query(None, description="Filter by user ID who performed actions"),
    action: Optional[str] = Query(None, description="Filter by action type (approval_requested, approved, rejected, forwarded)"),
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD format)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD format)"),
    limit: int = Query(50, description="Number of results to return", ge=1, le=100),
    offset: int = Query(0, description="Number of results to skip", ge=0)
):
    """
    Get audit trails across all vouchers with optional filtering.
    
    Example: GET /accounting/voucher/audit-trail?user_id=123&action=approved&limit=20
    """
    try:
        # Build aggregation pipeline
        pipeline = []
        
        # Match stage for filtering
        match_conditions = {}
        
        # Add date range filter if provided
        if start_date or end_date:
            date_filter = {}
            if start_date:
                try:
                    start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
                    date_filter["$gte"] = start_datetime
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
            
            if end_date:
                try:
                    end_datetime = datetime.strptime(end_date, "%Y-%m-%d")
                    # Add 23:59:59 to include the entire end date
                    end_datetime = end_datetime.replace(hour=23, minute=59, second=59)
                    date_filter["$lte"] = end_datetime
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")
            
            match_conditions["audit_trail.timestamp"] = date_filter
        
        # Add match stage if there are conditions
        if match_conditions:
            pipeline.append({"$match": match_conditions})
        
        # Unwind audit trail entries
        pipeline.append({"$unwind": "$audit_trail"})
        
        # Additional filtering on unwound entries
        unwind_match = {}
        if user_id:
            unwind_match["audit_trail.user_id"] = user_id
        if action:
            unwind_match["audit_trail.action"] = action
        
        if unwind_match:
            pipeline.append({"$match": unwind_match})
        
        # Sort by timestamp (newest first)
        pipeline.append({"$sort": {"audit_trail.timestamp": -1}})
        
        # Add voucher info and format output
        pipeline.append({
            "$project": {
                "voucher_id": {"$toString": "$_id"},
                "voucher_number": "$voucher_number",
                "action": "$audit_trail.action",
                "user_id": "$audit_trail.user_id",
                "user_name": "$audit_trail.user_name",
                "timestamp": "$audit_trail.timestamp",
                "details": "$audit_trail.details",
                "notes": "$audit_trail.notes"
            }
        })
        
        # Get total count for pagination
        count_pipeline = pipeline.copy()
        count_pipeline.append({"$count": "total"})
        count_result = list(voucher_collection.aggregate(count_pipeline))
        total_count = count_result[0]["total"] if count_result else 0
        
        # Add pagination
        pipeline.extend([
            {"$skip": offset},
            {"$limit": limit}
        ])
        
        # Execute aggregation
        results = list(voucher_collection.aggregate(pipeline))
        
        # Format timestamps
        formatted_results = []
        for entry in results:
            formatted_entry = {
                "voucher_id": entry["voucher_id"],
                "voucher_number": entry.get("voucher_number"),
                "action": entry["action"],
                "user_id": entry["user_id"],
                "user_name": entry.get("user_name"),
                "timestamp": entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if entry.get("timestamp") else None,
                "details": entry.get("details"),
                "notes": entry.get("notes")
            }
            formatted_results.append(formatted_entry)
        
        return {
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "audit_trails": formatted_results
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")
