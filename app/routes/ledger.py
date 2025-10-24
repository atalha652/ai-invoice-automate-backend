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


router = APIRouter(prefix="/accounting/ledger", tags=["ledger"])

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




@router.post("/post")
async def extract_text_from_s3(
    user_id: str = Form(...),
    project_id: str = Form(...)
):
    pass