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

# Load env variables
load_dotenv()

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Font config for WeasyPrint (if needed)
os.environ["FONTCONFIG_FILE"] = r"C:\OCR Project\fonts\fonts.conf"


router = APIRouter()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
projects_collection = db["projects"]
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





from pydantic import BaseModel, Field
from typing import Optional

class ProjectCreate(BaseModel):
    title: str = Field(..., description="Title of the project")
    description: str = Field(..., description="Detailed description of the project")
    color: Optional[str] = Field(None, description="Color theme for the project (e.g., #FF5733 or 'red')")



@router.post("/create")
async def create_project(
    title: str = Form(...),
    description: str = Form(...),
    color: Optional[str] = Form(None),  # New color field
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    # Validate file type
    if file.content_type not in ["image/png", "image/jpeg", "application/pdf"]:
        raise HTTPException(status_code=400, detail="Only image or PDF allowed")

    # Step 1: Save project with status "pending"
    new_project = {
        "user_id": str(current_user["_id"]),
        "title": title,
        "filename": file.filename,
        "filetype": file.content_type,
        "description": description,
        "color": color,  # Store color in DB
        "status": "pending",
        "created_at": datetime.utcnow()
    }

    result = projects_collection.insert_one(new_project)
    project_id = str(result.inserted_id)

    # Step 2: Upload to S3
    s3_key = upload_to_s3(
        user_id=str(current_user["_id"]),
        project_id=project_id,
        file=file,
        folder_type="Package"
    )
    package_url = s3.generate_presigned_url(
                            'get_object',
                            Params={
                                'Bucket': bucket_name,
                                'Key': s3_key,
                                'ResponseContentType': 'image/jpeg'
                            },
                            ExpiresIn=86400
                        )
    # Step 3: Update the project with s3_key
    projects_collection.update_one(
        {"_id": result.inserted_id},
        {"$set": {"package_url": package_url}}
    )
    user_id = str(current_user["_id"])

    # Step 4: Return response
    return {
        "message": "Project created",
        "project_id": project_id,
        "user_id": user_id,
        "package_key": s3_key,
        "package_url": package_url,
        "status": "pending"
    }



@router.get("/{user_id}")
def get_projects_by_user_id(user_id: str):
    projects = list(projects_collection.find({"user_id": user_id}))

    if not projects:
        return {"projects": []}

    # Convert ObjectId to string for JSON serialization
    for p in projects:
        p["_id"] = str(p["_id"])

    return {"projects": projects}




# --- UPDATE PROJECT ---
# @router.put("/update/{project_id}")
# async def update_project(
#     project_id: str,
#     title: str = Form(None),
#     file: UploadFile = File(None),
#     current_user: dict = Depends(get_current_user)
# ):
#     # Check if project exists and belongs to current user
#     project = projects_collection.find_one({
#         "_id": ObjectId(project_id),
#         "user_id": str(current_user["_id"])
#     })
#     if not project:
#         raise HTTPException(status_code=404, detail="Project not found")

#     update_data = {}

#     # Update title if provided
#     if title:
#         update_data["title"] = title

#     # Update file if provided
#     if file:
#         if file.content_type not in ["image/png", "image/jpeg", "application/pdf"]:
#             raise HTTPException(status_code=400, detail="Only image or PDF allowed")
#         update_data["filename"] = file.filename
#         update_data["filetype"] = file.content_type

#     # Add updated timestamp
#     update_data["updated_at"] = datetime.utcnow()

#     # Update in database
#     projects_collection.update_one(
#         {"_id": ObjectId(project_id)},
#         {"$set": update_data}
#     )

#     return {"message": "Project updated successfully"}


# --- DELETE PROJECT ---
@router.delete("/delete/{project_id}")
def delete_project(
    project_id: str,
    current_user: dict = Depends(get_current_user)
):
    # Check if project exists and belongs to user
    project = projects_collection.find_one({
        "_id": ObjectId(project_id),
        "user_id": str(current_user["_id"])
    })
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete project
    projects_collection.delete_one({"_id": ObjectId(project_id)})

    return {"message": "Project deleted successfully"}



