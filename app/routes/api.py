
from fastapi import APIRouter, File, UploadFile, HTTPException
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
projects_collection = db["projects"]
ocr_collection = db["ocr"]  # Replace 'db' with your actual DB object
report_collection = db["report"]
invoice_collection = db["e-invoice"]
users_collection = db["users"]


router = APIRouter()

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

def generate_invoice_from_json(invoice_data, filename):
    pdf = SimpleDocTemplate(filename, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(name='RightAlign', parent=styles['Normal'], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='Bold', parent=styles['Normal'], fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='MetaInfo', parent=styles['Normal'], spaceAfter=6))
    styles.add(ParagraphStyle(name='InvoiceTitle', parent=styles['Title'], fontSize=24, leading=28))

    # Title
    elements.append(Paragraph("INVOICE", styles['InvoiceTitle']))
    elements.append(Spacer(1, 20))

    # FROM and TO sections
    supplier = invoice_data.get("supplier", {})
    customer = invoice_data.get("customer", {})
    from_info = Paragraph(
        f"""
        {supplier.get("business_name", "")}<br/>
        {supplier.get("address_line1", "")}<br/>
        {supplier.get("address_line2", "")}<br/>
        Email: {supplier.get("phone", "")}""",
        styles['Normal']
    )

    to_info = Paragraph(
        f"""
        {customer.get("company_name", "")}<br/>
        {customer.get("address_line1", "")}<br/>
        {customer.get("address_line2", "")}<br/>
        Email: {customer.get("tax_number", "")}""",
        styles['Normal']
    )

    info_table = Table([[from_info, to_info]], colWidths=[260, 260])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))

    # Invoice Metadata
    invoice = invoice_data.get("invoice", {})
    meta_table = Table([
        ["N.º de factura:", invoice.get("invoice_number", "")],
        ["Fecha de factura:", invoice.get("invoice_date", "")],
        ["Importe en palabras:", invoice.get("amount_in_words", "")]
    ], colWidths=[120, 380])
    meta_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 20))

    # Itemized Table
    table_data = [['#', 'Descripción', 'Cantidad', 'Precio unitario', 'Total']]
    items = invoice_data.get("items", [])
    for idx, item in enumerate(items, start=1):
        table_data.append([
            idx,
            item.get("description", ""),
            item.get("qty", ""),
            f"{item.get('unit_price', 0):,.2f}",
            f"{item.get('subtotal', 0):,.2f}"
        ])

    # Grand total row
    totals = invoice_data.get("totals", {})

    table_data.append(["", "", "", "Total general:", f"{totals.get('total', 0):,.2f}"])
    table_data.append(["", "", "", "IVA:", totals.get('VAT_rate', '0%')])
    table_data.append(["", "", "", "Importe del IVA:", totals.get('VAT_amount')])
    table_data.append(["", "", "", "Total con impuestos:", f"{totals.get('Total_with_Tax', 0):,.2f}"])

    # Create and style item table
    table = Table(table_data, hAlign='LEFT', colWidths=[30, 230, 70, 100, 100])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#003366")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 1), (-1, -2), colors.whitesmoke),
        ('BACKGROUND', (-2, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (-2, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (-2, -1), (-1, -1), 10),
        ('BOTTOMPADDING', (-2, -1), (-1, -1), 10),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 30))

    # Footer
    elements.append(Paragraph("Thank you for your business!", styles['Italic']))
    pdf.build(elements)
    print(f"✅ Invoice generated: {filename}")
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

def send_invoice_email(to_email: str, file_paths: list, file_type: str):
    from_email = "zainisrar2003@gmail.com"
    password = "yqjq bvjq cnmx glcm"

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = f"Your Invoices ({file_type.upper()})"
    msg.attach(MIMEText(f"Hello,\n\nPlease find your {file_type.upper()} invoices attached.\n\nBest regards", "plain"))

    if not isinstance(file_paths, list):
        file_paths = [file_paths]

    # Attach all files
    for file_path in file_paths:
        with open(file_path, "rb") as f:
            attach = MIMEApplication(f.read(), _subtype=file_type)
            attach.add_header("Content-Disposition", "attachment", filename=file_path.split("/")[-1])
            msg.attach(attach)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(from_email, password)
        server.send_message(msg)


@router.post("/ocr")
async def extract_text_from_s3(
    user_id: str = Form(...),
    project_id: str = Form(...)
):
    try:
        # ✅ Step 1: Mark project as Processing
        projects_collection.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {
                "status": "Inprogress",
                "last_processed_at": datetime.utcnow(),
                "processed_count": 0
            }}
        )

        # Step 2: List all objects in Package folder
        package_prefix = f"{user_id}/{project_id}/Images/Package/"
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=package_prefix)

        if 'Contents' not in response or len(response['Contents']) == 0:
            raise HTTPException(status_code=404, detail="No images found in Package folder")

        total_images = len([obj for obj in response['Contents'] if not obj['Key'].endswith("/")])

        # Save total images in project (for progress tracking)
        projects_collection.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"total_images": total_images}}
        )

        user_doc = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user_doc or "email" not in user_doc:
            raise HTTPException(status_code=400, detail="User email not found")
        user_email = user_doc["email"]

        ocr_results = []
        processed_count = 0
        pdf_files = []  # ✅ collect generated PDFs

        for obj in response['Contents']:
            image_key = obj['Key']
            if image_key.endswith("/"):  # skip folders
                continue

            # Step 3: Download image from S3
            img_stream = io.BytesIO()
            s3.download_fileobj(bucket_name, image_key, img_stream)
            img_stream.seek(0)

            # Step 4: OCR
            extracted_text = OCR(img_stream.getvalue())
            cleaned_text = clean_ocr_text(extracted_text)

            # Step 4.5: Classification
            classify_json = classify_invoice_with_llm(cleaned_text)

            # Step 5: LLM invoice extraction
            llm_raw = send_to_llm(cleaned_text)
            cleaned = clean_json_string(llm_raw)
            invoice_data = json.loads(cleaned)

            # Step 6: Save PDF locally
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_filename = f"invoice_{timestamp}.pdf"
            pdf_path = os.path.join("invoices", pdf_filename)
            os.makedirs("invoices", exist_ok=True)
            generate_invoice_from_json(invoice_data, pdf_path)
            pdf_files.append(pdf_path)  # ✅ collect file for email later

            # Step 7: Upload PDF to S3 in Result folder
            result_key = f"{user_id}/{project_id}/Images/Result/{pdf_filename}"
            s3.upload_file(
                Filename=pdf_path,
                Bucket=bucket_name,
                Key=result_key,
                ExtraArgs={
                    "ContentType": "application/pdf",
                    "ContentDisposition": "inline"
                }
            )

            result_url = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': result_key,
                    'ResponseContentType': 'application/pdf'
                },
                ExpiresIn=86400
            )

            # Step 8: Save report in MongoDB
            report_doc = {
                "user_id": user_id,
                "project_id": project_id,
                "created_at": datetime.utcnow(),
                **invoice_data
            }
            report_collection.insert_one(report_doc)

            # Step 9: Save OCR log in MongoDB
            ocr = ocr_collection.insert_one({
                "user_id": user_id,
                "project_id": project_id,
                "result_key": result_key,
                "package_key": image_key,
                "pdf_text": cleaned,
                **classify_json,
                "status": "Success",
                "created_at": datetime.utcnow()
            })

            # Step 10: Update progress
            processed_count += 1
            projects_collection.update_one(
                {"_id": ObjectId(project_id)},
                {"$set": {"processed_count": processed_count}}
            )

            ocr_results.append({
                "ocr_id": str(ocr.inserted_id),
                "image_key": image_key,
                "result_key": result_key,
                "result_url": result_url,
                "ocr_text": cleaned_text
            })

        # ✅ Send all invoices in one email after loop
        if pdf_files:
            send_invoice_email(user_email, pdf_files, "pdf")
            # cleanup PDFs after sending email
            for pdf_file in pdf_files:
                os.remove(pdf_file)

        # ✅ Step 11: Mark project as Done
        projects_collection.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {
                "status": "Done",
                "last_processed_at": datetime.utcnow(),
                "total_processed": len(ocr_results)
            }}
        )

        return {
            "message": "OCR completed for all images",
            "project_id": project_id,
            "user_id": user_id,
            "results": ocr_results
        }

    except Exception as e:
        # ❌ Step 12: Mark project as Failed
        projects_collection.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {
                "status": "Failed",
                "error_message": str(e),
                "last_processed_at": datetime.utcnow()
            }}
        )
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/ocr/{project_id}")
async def get_ocr_results(project_id: str, user_id: str):
    try:
        # ✅ Step 1: Fetch project details
        project = projects_collection.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # ✅ Step 2: Fetch OCR logs for this project
        ocr_logs = list(ocr_collection.find(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 1, "result_key": 1, "package_key": 1, "pdf_text": 1, "status": 1, "created_at": 1}
        ))

        results = []
        for log in ocr_logs:
            results.append({
                "ocr_id": str(log["_id"]),
                "result_key": log.get("result_key"),
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
            "results": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




def clean_xml(response_text: str) -> str:
    """
    Extracts and returns only the XML part from a text response.
    Looks for content starting with <?xml ... ?> and ending with </fe:Facturae>.
    """
    pattern = re.compile(r'(<\?xml.*?</fe:Facturae>)', re.DOTALL)
    match = pattern.search(response_text)
    if match:
        return match.group(1).strip()
    return ""  # return empty if no XML found

@router.post("/xml")
async def extract_text_from_s3(
    user_id: str = Form(...),
    project_id: str = Form(...)
):
    # 1️⃣ Get all OCR documents for this project
    ocr_docs = list(ocr_collection.find(
        {"user_id": user_id, "project_id": project_id}
    ))
    
    if not ocr_docs:
        raise HTTPException(status_code=404, detail="No OCR data found for this project.")

    results = []
    xml_files = []  # ✅ collect XMLs for email

    user_doc = users_collection.find_one({"_id": ObjectId(user_id)})
    if not user_doc or "email" not in user_doc:
        raise HTTPException(status_code=400, detail="User email not found")
    user_email = user_doc["email"]

    for ocr_doc in ocr_docs:
        pdf_text = ocr_doc.get("pdf_text")
        if not pdf_text:
            continue 

        # 2️⃣ Create XML using LLM
        prompt = f"""
You are an expert in structured invoice data extraction and XML formatting.

Your task:
Convert the given invoice text into **pure, valid XML** in the **Facturae 3.2.1 format**.

Strict Rules:
1. Output **only** valid XML — no explanations, comments, or extra text.
2. The XML must start with the declaration:
   <?xml version="1.0" encoding="UTF-8"?>
3. The root element must be:
   <fe:Facturae xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
                xmlns:fe="http://www.facturae.es/Facturae/2014/v3.2.1/Facturae">
4. Escape all special XML characters (`&`, `<`, `>`, `"`, `'`) properly.
5. Preserve **all available details** from the text — do not omit any field.
6. If a value is missing in the source text, leave the tag empty but keep it present.
7. Follow the XML structure shown in the example exactly, replacing placeholders with extracted values.
8. Keep numeric values without currency symbols, formatted with `.` as decimal separator.
9. Keep dates in ISO format (YYYY-MM-DD).

Example XML structure:
<?xml version="1.0" encoding="UTF-8"?>
<fe:Facturae xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    xmlns:fe="http://www.facturae.es/Facturae/2014/v3.2.1/Facturae">
    <FileHeader>
        <SchemaVersion>3.2.1</SchemaVersion>
        <Modality>I</Modality>
        <InvoiceIssuerType>EM</InvoiceIssuerType>
        <Batch>
            <BatchIdentifier>BATCH_IDENTIFIER</BatchIdentifier>
            <InvoicesCount>1</InvoicesCount>
            <TotalInvoicesAmount>
                <TotalAmount>INVOICE_TOTAL</TotalAmount>
            </TotalInvoicesAmount>
            <TotalOutstandingAmount>
                <TotalAmount>INVOICE_BALANCE_DUE</TotalAmount>
            </TotalOutstandingAmount>
            <TotalExecutableAmount>
                <TotalAmount>INVOICE_BALANCE_DUE</TotalAmount>
            </TotalExecutableAmount>
            <InvoiceCurrencyCode>CURRENCY_CODE</InvoiceCurrencyCode>
        </Batch>
    </FileHeader>
    <Parties>
        <SellerParty>
            <TaxIdentification>
                <PersonTypeCode>J</PersonTypeCode>
                <ResidenceTypeCode>R</ResidenceTypeCode>
                <TaxIdentificationNumber>COMPANY_VAT_NUMBER</TaxIdentificationNumber>
            </TaxIdentification>
            <LegalEntity>
                <CorporateName>COMPANY_NAME</CorporateName>
                <AddressInSpain>
                    <Address>COMPANY_ADDRESS</Address>
                    <PostCode>COMPANY_ZIP</PostCode>
                    <Town>COMPANY_CITY_NAME</Town>
                    <Province>COMPANY_STATE</Province>
                    <CountryCode>COMPANY_COUNTRY_CODE</CountryCode>
                </AddressInSpain>
            </LegalEntity>
        </SellerParty>
        <BuyerParty>
            <TaxIdentification>
                <PersonTypeCode>J</PersonTypeCode>
                <ResidenceTypeCode>R</ResidenceTypeCode>
                <TaxIdentificationNumber>CLIENT_VAT_NUMBER</TaxIdentificationNumber>
            </TaxIdentification>
            <LegalEntity>
                <CorporateName>CLIENT_NAME</CorporateName>
                <AddressInSpain>
                    <Address>CLIENT_ADDRESS</Address>
                    <PostCode>CLIENT_ZIP</PostCode>
                    <Town>CLIENT_CITY</Town>
                    <Province>CLIENT_STATE</Province>
                    <CountryCode>CLIENT_COUNTRY_CODE_ALPHA_3</CountryCode>
                </AddressInSpain>
            </LegalEntity>
        </BuyerParty>
    </Parties>
    <Invoices>
        <Invoice>
            <InvoiceHeader>
                <InvoiceNumber>INVOICE_ID</InvoiceNumber>
                <InvoiceSeriesCode>F</InvoiceSeriesCode>
                <InvoiceDocumentType>FC</InvoiceDocumentType>
                <InvoiceClass>OO</InvoiceClass>
            </InvoiceHeader>
            <InvoiceIssueData>
                <IssueDate>INVOICE_BILL_DATE</IssueDate>
                <InvoiceCurrencyCode>CURRENCY_CODE</InvoiceCurrencyCode>
                <TaxCurrencyCode>CURRENCY_CODE</TaxCurrencyCode>
                <LanguageName>es</LanguageName>
            </InvoiceIssueData>
            <TaxesOutputs>
                <Tax>
                    <TaxTypeCode>01</TaxTypeCode>
                    <TaxRate>TAX1_PERCENT</TaxRate>
                    <TaxableBase>
                        <TotalAmount>INVOICE_TAXABLE_SUBTOTAL</TotalAmount>
                    </TaxableBase>
                    <TaxAmount>
                        <TotalAmount>TAX_TOTAL_AMOUNT</TotalAmount>
                    </TaxAmount>
                </Tax>
                <Tax>
                    <TaxTypeCode>01</TaxTypeCode>
                    <TaxRate>0</TaxRate>
                    <TaxableBase>
                        <TotalAmount>INVOICE_NON_TAXABLE_SUBTOTAL</TotalAmount>
                    </TaxableBase>
                    <TaxAmount>
                        <TotalAmount>0</TotalAmount>
                    </TaxAmount>
                </Tax>
            </TaxesOutputs>
            <InvoiceTotals>
                <TotalGrossAmount>INVOICE_SUBTOTAL</TotalGrossAmount>
                <TotalGeneralDiscounts>INVOICE_DISCOUNT_TOTAL</TotalGeneralDiscounts>
                <TotalGeneralSurcharges>0</TotalGeneralSurcharges>
                <TotalGrossAmountBeforeTaxes>INVOICE_SUBTOTAL</TotalGrossAmountBeforeTaxes>
                <TotalTaxOutputs>TAX_TOTAL_AMOUNT</TotalTaxOutputs>
                <TotalTaxesWithheld>0.0</TotalTaxesWithheld>
                <InvoiceTotal>INVOICE_TOTAL</InvoiceTotal>
                <TotalOutstandingAmount>INVOICE_BALANCE_DUE</TotalOutstandingAmount>
                <TotalExecutableAmount>INVOICE_BALANCE_DUE</TotalExecutableAmount>
            </InvoiceTotals>
            <Items>
                <InvoiceLine>
                    <ItemDescription>INVOICE_LINE_TITLE</ItemDescription>
                    <Quantity>INVOICE_LINE_QUANTITY</Quantity>
                    <UnitPriceWithoutTax>INVOICE_LINE_RATE</UnitPriceWithoutTax>
                    <TotalCost>INVOICE_LINE_TOTAL</TotalCost>
                    <GrossAmount>INVOICE_LINE_TOTAL</GrossAmount>
                    <TaxesOutputs>
                        <Tax>
                            <TaxTypeCode>01</TaxTypeCode>
                            <TaxRate>INVOICE_LINE_TAX1_PERCENT</TaxRate>
                            <TaxableBase>
                                <TotalAmount>INVOICE_LINE_TOTAL</TotalAmount>
                            </TaxableBase>
                            <TaxAmount>
                                <TotalAmount>INVOICE_LINE_TAX_TOTAL</TotalAmount>
                            </TaxAmount>
                        </Tax>
                    </TaxesOutputs>
                </InvoiceLine>
            </Items>
        </Invoice>
    </Invoices>
</fe:Facturae>

Input Invoice Text:
{pdf_text}
"""
        response = clients.responses.create(
            model="gpt-4.1",
            input=prompt
        )

        xml_output = response.output_text if hasattr(response, "output_text") else str(response)
        xml_code = clean_xml(xml_output)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        xml_filename = f"invoice_{timestamp}.xml"
        xml_path = os.path.join("xml_files", xml_filename)
        os.makedirs("xml_files", exist_ok=True)

        # Save cleaned XML file
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_code)

        # ✅ Append file path (not XML string)
        xml_files.append(xml_path)

        # Save in DB
        invoice_doc = {
            "user_id": user_id,
            "project_id": project_id,
            "ocr_id": str(ocr_doc.get("_id")),
            "invoice": xml_code,  # save cleaned XML, not raw
            "created_at": datetime.utcnow()
        }
        invoice_collection.insert_one(invoice_doc)

        results.append({
            "ocr_id": str(ocr_doc.get("_id")),
            "xml_preview": xml_code[:500]  # preview cleaned XML
        })

        # ✅ Send all XMLs in one email
    if xml_files:
        send_invoice_email(user_email, xml_files, "xml")

        # cleanup after sending
        for xml_file in xml_files:
            os.remove(xml_file)

    return {
        "message": f"XML files created for {len(results)} OCR documents",
        "project_id": project_id,
        "user_id": user_id,
        "results": results
    }
