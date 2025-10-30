
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
from pydantic import BaseModel, EmailStr
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



router = APIRouter(prefix="/accounting/ocr", tags=["OCR"])

# OpenAI client
clients = OpenAI(api_key=OPENAI_KEY)


# ---------------- OCR CLEANING ---------------- #
def clean_ocr_text(raw_text: str) -> str:
    """Clean OCR extracted text and fix common issues."""
    text = re.sub(r'\n+', '\n', raw_text)  # collapse multiple newlines
    text = re.sub(r'[ \t]+', ' ', text)  # collapse multiple spaces
    text = text.strip()

    # Fix common OCR errors
    corrections = {
        r'jank Name': 'Bank Name',
        r'\\ccount': 'Account',
        r'ase make the payment': 'Please make the payment',
        r'\bO H W\b': 'OHW'
    }
    for wrong, right in corrections.items():
        text = re.sub(wrong, right, text, flags=re.IGNORECASE)

    # Merge broken lines that are part of same sentence
    text = re.sub(r'(?<=\w)\n(?=\w)', ' ', text)

    return text


# ---------------- LLM HTML GENERATION ---------------- #
def send_to_llm(text: str) -> str:
    prompt = f"""
You are a professional invoice extraction assistant familiar with **Spanish VAT rules**.

Your task:
Extract structured invoice data from the given invoice text and compute VAT correctly.

📌 Spanish VAT Rules:
- Apply **21%** for general goods and services (default).
- Apply **10%** for hotel, transport, some food.
- Apply **4%** for books, newspapers, basic food, medicine.
- Apply **0% (Exempt)** for healthcare, education, financial services.
- If unsure, use 21%.

📐 Rules for Calculations:
- Always calculate:
  - total = sum of all item subtotals
  - VAT_rate = numeric value (e.g., 21.0)
  - VAT_amount = round(total * VAT_rate / 100, 2)
  - Total_with_Tax = round(total + VAT_amount, 2)
- If VAT_rate = 0, VAT_amount must be 0 and Total_with_Tax = total.

🧾 Output format (as valid JSON, no extra text):
{{
  "supplier": {{
    "business_name": "...",
    "address_line1": "...",
    "address_line2": "...",
    "Email": "..."
  }},
  "customer": {{
    "company_name": "...",
    "address_line1": "...",
    "address_line2": "...",
    "Email": "..."
  }},
  "invoice": {{
    "invoice_number": "...",
    "invoice_date": "...",
    "due_date": "...",
    "amount_in_words": "..."
  }},
  "items": [
    {{"description": "...", "qty": 0, "unit_price": 0.0, "subtotal": 0.0}}
  ],
  "totals": {{
    "total": 0.0,
    "VAT_rate": 0.0,
    "VAT_amount": 0.0,
    "Total_with_Tax": 0.0
  }}
}}

📄 Invoice text:
{text}
"""

    response = clients.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message.content.strip()

# ---------------- LLM HTML GENERATION ---------------- #
def clean_json_string(json_text: str) -> str:
    # Remove inline comments starting with //
    json_text = re.sub(r'//.*', '', json_text)
    # Remove ```json or ``` markers
    json_text = re.sub(r'```(?:json)?', '', json_text)
    # Keep only the part from the first { to the last }
    match = re.search(r'\{.*\}', json_text, re.DOTALL)
    if match:
        json_text = match.group(0)
    else:
        raise ValueError("No valid JSON object found in text")
    return json_text.strip()

def OCR(image_bytes: bytes) -> str:
    client = genai.Client(api_key=os.getenv("GENAI_API_KEY"))
    response = client.models.generate_content(
        model='gemini-2.0-flash-lite',
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type='image/jpeg',
            ),
            'Extract all text from this image.'
        ]
    )
    return response.text

def process_vouchers_background(job_id: str, user_id: str, voucher_object_ids: list):
    """Background task to process vouchers"""
    try:
        # Update job status to processing
        ocr_jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "processing", "started_at": datetime.utcnow()}}
        )
        
        # Fetch vouchers from collection
        vouchers = list(voucher_collection.find(
            {"_id": {"$in": voucher_object_ids}, "user_id": user_id},
            {"files": 1}
        ))
        
        results = []
        
        for voucher in vouchers:
            voucher_id = str(voucher["_id"])
            files = voucher.get("files", [])
            
            voucher_ocr_results = []
            
            # Process each file in the voucher
            if isinstance(files, list):
                for file_obj in files:
                    if isinstance(file_obj, dict) and "file_url" in file_obj:
                        file_url = file_obj["file_url"]
                        
                        s3_key = "unknown"
                        try:
                            # Extract S3 key from URL
                            url_without_params = file_url.split("?")[0]
                            
                            if ".s3." in url_without_params and ".amazonaws.com/" in url_without_params:
                                s3_key = url_without_params.split(".amazonaws.com/")[1]
                            elif f"{bucket_name}/" in url_without_params:
                                s3_key = url_without_params.split(f"{bucket_name}/")[1]
                            else:
                                raise ValueError(f"Could not parse S3 key from URL: {file_url}")
                            
                            # URL decode the S3 key
                            s3_key = unquote(s3_key)
                            
                            # Download image from S3
                            img_stream = io.BytesIO()
                            s3.download_fileobj(bucket_name, s3_key, img_stream)
                            img_stream.seek(0)
                            image_bytes = img_stream.getvalue()
                            
                            # Process through OCR
                            raw_text = OCR(image_bytes)
                            cleaned_text = clean_ocr_text(raw_text)
                            
                            # Pass cleaned text to LLM for invoice extraction
                            llm_response = None
                            invoice_data = None
                            ledger_id = None
                            llm_error_msg = None
                            
                            try:
                                llm_raw = send_to_llm(cleaned_text)
                                llm_cleaned = clean_json_string(llm_raw)
                                invoice_data = json.loads(llm_cleaned)
                                llm_response = invoice_data
                            except Exception as llm_error:
                                llm_error_msg = str(llm_error)
                                llm_response = {"error": llm_error_msg}
                            
                            # Store in ledger collection
                            ledger_entry = {
                                "user_id": user_id,
                                "voucher_id": voucher_id,
                                "file_url": file_url,
                                "s3_key": s3_key,
                                "ocr_text": cleaned_text,
                                "invoice_data": invoice_data if invoice_data else None,
                                "llm_error": llm_error_msg,
                                "processing_status": "success" if invoice_data else "llm_failed",
                                "created_at": datetime.utcnow()
                            }
                            ledger_result = ledger_collection.insert_one(ledger_entry)
                            ledger_id = str(ledger_result.inserted_id)
                            
                            voucher_ocr_results.append({
                                "file_url": file_url,
                                "s3_key": s3_key,
                                "ledger_id": ledger_id,
                                "status": "success"
                            })
                            
                        except Exception as e:
                            voucher_ocr_results.append({
                                "file_url": file_url,
                                "s3_key": s3_key,
                                "status": "failed",
                                "error": str(e)
                            })
            
            # Check if all files were successfully processed
            all_success = all(file["status"] == "success" for file in voucher_ocr_results)
            
            # Update voucher with OCR status
            if all_success and len(voucher_ocr_results) > 0:
                voucher_collection.update_one(
                    {"_id": ObjectId(voucher_id)},
                    {"$set": {"OCR": "Done", "ocr_completed_at": datetime.utcnow()}}
                )
                ocr_status = "Done"
            elif len(voucher_ocr_results) > 0:
                voucher_collection.update_one(
                    {"_id": ObjectId(voucher_id)},
                    {"$set": {"OCR": "Partial", "ocr_completed_at": datetime.utcnow()}}
                )
                ocr_status = "Partial"
            else:
                ocr_status = "No files"
            
            results.append({
                "voucher_id": voucher_id,
                "file_count": len(voucher_ocr_results),
                "ocr_status": ocr_status,
                "files": voucher_ocr_results
            })
        
        # Update job status to completed
        ocr_jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": "completed",
                "completed_at": datetime.utcnow(),
                "results": results
            }}
        )
        
    except Exception as e:
        # Update job status to failed
        ocr_jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": "failed",
                "error": str(e),
                "failed_at": datetime.utcnow()
            }}
        )


def classify_invoice_with_llm(cleaned_text: str) -> dict:
    """
    Calls LLM to classify invoice into:
    - classify: income | expense
    - label: product | service
    - details: merchant, date, amount, tax_rate
    """
    prompt = f"""
    You are an AI assistant. Classify the following OCR invoice text into a structured JSON.

    The JSON format must be:
    {{
      "classification": {{
        "classify": "income" | "expense",
        "label": "product" | "service",
        "details": {{
          "merchant": "<merchant_name>",
          "date": "<YYYY-MM-DD>",
          "amount": <number>,
          "tax_rate": <number>
        }}
      }}
    }}

    Text:
    {cleaned_text}
    """

    response = clients.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    raw_output = response.choices[0].message.content.strip()
    cleaned_output = clean_json_string(raw_output)
    return json.loads(cleaned_output)



@router.post("/voucher_ocr")
async def extract_text_from_s3(
    background_tasks: BackgroundTasks,
    user_id: str = Form(...),
    voucher_ids: str = Form(..., description="Comma-separated list of voucher IDs")
):
    """
    Start background OCR processing for vouchers.
    Returns immediately with a job_id to track progress.
    """
    try:
        # Parse voucher IDs from comma-separated string
        voucher_id_list = [vid.strip() for vid in voucher_ids.split(",") if vid.strip()]
        
        if not voucher_id_list:
            raise HTTPException(status_code=400, detail="No voucher IDs provided")
        
        # Convert to ObjectIds
        try:
            voucher_object_ids = [ObjectId(vid) for vid in voucher_id_list]
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid voucher ID format")
        
        # Verify vouchers exist
        voucher_count = voucher_collection.count_documents(
            {"_id": {"$in": voucher_object_ids}, "user_id": user_id}
        )
        
        if voucher_count == 0:
            raise HTTPException(status_code=404, detail="No vouchers found for the provided IDs")
        
        # Create job record
        job_doc = {
            "user_id": user_id,
            "voucher_ids": voucher_id_list,
            "status": "pending",
            "total_vouchers": voucher_count,
            "created_at": datetime.utcnow()
        }
        job_result = ocr_jobs_collection.insert_one(job_doc)
        job_id = str(job_result.inserted_id)
        
        # Start background processing
        background_tasks.add_task(process_vouchers_background, job_id, user_id, voucher_object_ids)
        
        return {
            "message": "OCR processing started in background",
            "job_id": job_id,
            "user_id": user_id,
            "total_vouchers": voucher_count,
            "status": "pending",
            "check_status_url": f"/accounting/ocr/job/{job_id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing OCR: {str(e)}")


@router.get("/job/{job_id}")
async def get_ocr_job_status(job_id: str):
    """
    Check the status of an OCR background job.
    """
    try:
        job = ocr_jobs_collection.find_one({"_id": ObjectId(job_id)})
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        job["_id"] = str(job["_id"])
        
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "user_id": job.get("user_id"),
            "total_vouchers": job.get("total_vouchers"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "failed_at": job.get("failed_at"),
            "error": job.get("error"),
            "results": job.get("results")
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ocr/{project_id}")
async def get_ocr_results(project_id: str, user_id: str):
    try:
        # ✅ Step 1: Fetch project details
        project = projects_collection.find_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {"title": 1, "description": 1, "color": 1, "status": 1, "files": 1, "total_images": 1, "processed_count": 1}
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # ✅ Step 2: Extract file URLs from project
        file_urls = []
        if "files" in project and isinstance(project["files"], list):
            file_urls = [f.get("file_url") for f in project["files"] if "file_url" in f]

        # ✅ Step 3: Fetch OCR logs for this project
        ocr_logs = list(ocr_collection.find(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 1, "result_url": 1, "package_key": 1, "pdf_text": 1, "status": 1, "created_at": 1}
        ))

        results = []
        for log in ocr_logs:
            results.append({
                "ocr_id": str(log["_id"]),
                "result_url": log.get("result_url"),
                "package_key": log.get("package_key"),
                "status": log.get("status"),
                "created_at": log.get("created_at"),
                "ocr_text": log.get("pdf_text")
            })

        return {
            "project_id": project_id,
            "user_id": user_id,
            "status": project.get("status", "Unknown"),
            "total_images": project.get("total_images", 0),
            "processed_count": project.get("processed_count", 0),
            "file_urls": file_urls,      # ✅ All original file URLs
            "results": results           # ✅ Includes result_url for each processed file
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

