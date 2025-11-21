
from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks
from PIL import Image
import pytesseract
import io
import re
import tempfile
from google import genai
from google.genai import types
import os
import re
import xml.etree.ElementTree as ET
from openai import OpenAI
import bcrypt
from datetime import datetime, timedelta
import certifi
from app.routes.auth import get_current_user
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from fastapi.security import OAuth2PasswordBearer
import certifi
from dotenv import load_dotenv
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, File, UploadFile, Depends, Form, HTTPException
from bson import ObjectId
from fastapi import APIRouter, File, Form, Depends, HTTPException, UploadFile
import boto3
import json
from PIL import Image
import io
import os
from openai import OpenAI
from fastapi import Form
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, 
    PageBreak
)
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
from urllib.parse import unquote

load_dotenv()
# Set Tesseract path (Windows)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name="eu-north-1"
)

bucket_name = "ai-auto-invoice"



client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
# Replace 'db' with your actual DB object
voucher_collection = db["voucher"]
ocr_collection = db["ocr"] 
users_collection = db["users"]
ledger_collection = db["ledger"]
ocr_jobs_collection = db["ocr_jobs"]



router = APIRouter(prefix="/accounting/ledgers", tags=["Ledgers"])


# Pydantic Model
class LedgerUpdateRequest(BaseModel):
    invoice_data: Dict[str, Any] = Field(..., description="Complete invoice data to update")


@router.get("/user/{user_id}")
async def get_ledger_by_user(
    user_id: str
):
    """
    Get all ledger entries for a specific user.
    Fetches from both 'ledger' (OCR-based) and 'ledger_entries' (accounting-based) collections.
    Example: GET /accounting/ledgers/user/123
    """
    try:
        # Get user's organization_id
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        organization_id = str(user.get("organization_id", user_id)) if user else user_id
        # Query 1: Fetch from old 'ledger' collection (OCR-based ledger)
        query_ocr = {"user_id": user_id}
        ocr_ledger_entries = list(ledger_collection.find(query_ocr).sort("created_at", -1))

        # Format OCR entries - keep original format
        for entry in ocr_ledger_entries:
            entry["_id"] = str(entry["_id"])
            if isinstance(entry.get("created_at"), datetime):
                entry["created_at"] = entry["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        # Query 2: Fetch from new 'ledger_entries' collection (accounting ledger)
        ledger_entries_collection = db["ledger_entries"]
        query_accounting = {"organization_id": organization_id}
        accounting_ledger_entries = list(ledger_entries_collection.find(query_accounting).sort("created_at", -1))

        # Format accounting entries to match OCR ledger structure
        formatted_accounting_entries = []
        for entry in accounting_ledger_entries:
            # Convert accounting ledger to display format that matches OCR structure
            formatted_entry = {
                "_id": str(entry["_id"]),
                "user_id": user_id,
                "voucher_id": entry.get("journal_entry_id", ""),
                "file_name": f"Bank Transaction - {entry.get('reference', 'N/A')}",
                "data_type": "bank_transaction",
                "ocr_text": entry.get("description", ""),
                "invoice_data": {
                    "transaction_type": "debit" if entry.get("entry_type") == "DEBIT" else "credit",
                    "account": {
                        "account_code": entry.get("account_code", ""),
                        "account_name": entry.get("account_name", "")
                    },
                    "invoice": {
                        "invoice_number": entry.get("reference", ""),
                        "invoice_date": entry.get("transaction_date").strftime("%Y-%m-%d") if isinstance(entry.get("transaction_date"), datetime) else str(entry.get("transaction_date", "")),
                        "due_date": "",
                        "amount_in_words": ""
                    },
                    "items": [
                        {
                            "description": entry.get("description", ""),
                            "qty": 1,
                            "unit_price": entry.get("amount", 0),
                            "subtotal": entry.get("amount", 0)
                        }
                    ],
                    "totals": {
                        "total": entry.get("amount", 0),
                        "running_balance": entry.get("running_balance", 0)
                    }
                },
                "llm_error": None,
                "processing_status": "success",
                "created_at": entry.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if isinstance(entry.get("created_at"), datetime) else str(entry.get("created_at", ""))
            }
            formatted_accounting_entries.append(formatted_entry)

        # Combine both lists
        all_entries = ocr_ledger_entries + formatted_accounting_entries

        # Sort combined list by created_at (newest first)
        all_entries.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        total_count = len(all_entries)

        if total_count == 0:
            return {
                "user_id": user_id,
                "entries": [],
                "total_count": 0,
                "message": "No ledger entries found for this user"
            }

        # Return in original format (backward compatible)
        return {
            "user_id": user_id,
            "entries": all_entries,
            "total_count": total_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving ledger entries: {str(e)}")


@router.put("/{ledger_id}")
async def update_ledger_entry(
    ledger_id: str,
    update_data: LedgerUpdateRequest
):
    """
    Update only the invoice_data field of a ledger entry.
    Example: PUT /accounting/ledgers/69083ec9be8d0f81ff44275b
    Body: {
        "invoice_data": {
            "supplier": {
                "business_name": "Your Company Inc.",
                "address_line1": "1234 Company St",
                "address_line2": "Company Town, ST 12345",
                "Email": "support@example.com"
            },
            "customer": {
                "company_name": "Customer Name",
                "address_line1": "1234 Customer St",
                "address_line2": "Customer Town, ST 12345",
                "Email": ""
            },
            "invoice": {
                "invoice_number": "0000457",
                "invoice_date": "11-04-2025",
                "due_date": "",
                "amount_in_words": "Two hundred fifty-two dollars"
            },
            "items": [
                {
                    "description": "Product A",
                    "qty": 2,
                    "unit_price": 45.00,
                    "subtotal": 90.00
                }
            ],
            "totals": {
                "total": 240.00,
                "VAT_rate": 21.0,
                "VAT_amount": 50.40,
                "Total_with_Tax": 290.40
            }
        }
    }
    """
    try:
        # Check if ledger entry exists
        ledger_entry = ledger_collection.find_one({"_id": ObjectId(ledger_id)})
        if not ledger_entry:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        
        # Update only invoice_data field
        update_doc = {
            "invoice_data": update_data.invoice_data,
            "updated_at": datetime.utcnow()
        }
        
        # Update the ledger entry
        result = ledger_collection.update_one(
            {"_id": ObjectId(ledger_id)},
            {"$set": update_doc}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update ledger entry")
        
        # Get updated entry
        updated_entry = ledger_collection.find_one({"_id": ObjectId(ledger_id)})
        updated_entry["_id"] = str(updated_entry["_id"])
        if isinstance(updated_entry.get("created_at"), datetime):
            updated_entry["created_at"] = updated_entry["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(updated_entry.get("updated_at"), datetime):
            updated_entry["updated_at"] = updated_entry["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "message": "Invoice data updated successfully",
            "ledger_id": ledger_id,
            "updated_entry": updated_entry
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error updating ledger entry: {str(e)}")


@router.delete("/{ledger_id}")
async def delete_ledger_entry(
    ledger_id: str
):
    """
    Delete a ledger entry by its ID.
    Example: DELETE /accounting/ledgers/69083ec9be8d0f81ff44275b
    """
    try:
        # Check if ledger entry exists
        ledger_entry = ledger_collection.find_one({"_id": ObjectId(ledger_id)})
        if not ledger_entry:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        
        # Delete the ledger entry
        result = ledger_collection.delete_one({"_id": ObjectId(ledger_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=500, detail="Failed to delete ledger entry")
        
        return {
            "message": "Ledger entry deleted successfully",
            "ledger_id": ledger_id,
            "deleted_entry": {
                "user_id": ledger_entry.get("user_id"),
                "voucher_id": ledger_entry.get("voucher_id"),
                "processing_status": ledger_entry.get("processing_status")
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error deleting ledger entry: {str(e)}")
