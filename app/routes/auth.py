from fastapi import APIRouter, File, Depends, HTTPException
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from pymongo import MongoClient
from bson import ObjectId
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List

import bcrypt
import certifi
from jose import JWTError, jwt
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from pymongo import MongoClient

# -------------------- Load Environment Variables --------------------
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
SECRET_KEY = os.getenv("SECRET_KEY", "ikingkhs23a")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "600"))

# -------------------- Database Connection --------------------
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
users_collection = db["users"]
org_types_collection = db["org_types"]

# -------------------- Router --------------------
router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# -------------------- Enums --------------------
class UserType(str, Enum):
    individual = "individual"
    organization = "organization"

# -------------------- Pydantic Models --------------------
class OrgTypeCreate(BaseModel):
    name: str

class OrgTypeResponse(BaseModel):
    id: str
    name: str

class OrganizationInfo(BaseModel):
    org_name: str
    type_id: Optional[str] = None   # dropdown selection
    type_name: Optional[str] = None # custom user entry
    website: Optional[str] = None

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    type: UserType
    organization_info: Optional[OrganizationInfo] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

# -------------------- Token Helper --------------------
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# -------------------- Add Organization Type --------------------
@router.post("/org-types", response_model=dict)
def add_org_type(type_data: OrgTypeCreate):
    # Check if type already exists
    if org_types_collection.find_one({"name": type_data.name.lower()}):
        raise HTTPException(status_code=400, detail="Type already exists")

    result = org_types_collection.insert_one({
        "name": type_data.name.lower(),
        "created_at": datetime.utcnow()
    })
    return {"message": "Organization type added", "type_id": str(result.inserted_id)}

# -------------------- Get All Organization Types --------------------
@router.get("/org-types", response_model=List[OrgTypeResponse])
def get_org_types():
    types = org_types_collection.find({}, {"name": 1})
    return [{"id": str(t["_id"]), "name": t["name"]} for t in types]

# -------------------- Signup --------------------
@router.post("/signup")
def signup(user: UserCreate):
    # Check if email already exists
    if users_collection.find_one({"email": user.email.lower()}):
        raise HTTPException(status_code=400, detail="Email already registered")

    # Hash password
    hashed_pw = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()

    # Prepare new user document
    new_user = {
        "name": user.name,
        "email": user.email.lower(),
        "password_hash": hashed_pw,
        "type": user.type,
        "created_at": datetime.utcnow()
    }

    # If organization, handle org type logic
    if user.type == UserType.organization and user.organization_info:
        org_type = None

        # Case 1: Dropdown selection (type_id provided)
        if getattr(user.organization_info, "type_id", None):
            try:
                org_type = org_types_collection.find_one(
                    {"_id": ObjectId(user.organization_info.type_id)}
                )
            except:
                raise HTTPException(status_code=400, detail="Invalid organization type ID")

            if not org_type:
                raise HTTPException(status_code=400, detail="Invalid organization type")

        # Case 2: User entered their own type (type_name provided)
        elif getattr(user.organization_info, "type_name", None):
            type_name = user.organization_info.type_name.strip().lower()
            org_type = org_types_collection.find_one({"name": type_name})

            # If type doesn't exist, insert it
            if not org_type:
                inserted_type = org_types_collection.insert_one({
                    "name": type_name,
                    "created_at": datetime.utcnow()
                })
                org_type = {"_id": inserted_type.inserted_id, "name": type_name}

        else:
            raise HTTPException(status_code=400, detail="Organization type is required")

        # Save organization info in user document
        new_user["organization_info"] = {
            "org_name": user.organization_info.org_name,
            "type": org_type["name"],
            "website": user.organization_info.website
        }

    # Insert into database
    result = users_collection.insert_one(new_user)
    return {"message": "User created successfully", "user_id": str(result.inserted_id)}

# -------------------- Login --------------------
@router.post("/login")
def login(user: UserLogin):
    db_user = users_collection.find_one({"email": user.email.lower()})
    if not db_user or not bcrypt.checkpw(user.password.encode("utf-8"), db_user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(
        {"sub": str(db_user["_id"])},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"message": "User logged in successfully", "access_token": access_token, "token_type": "bearer"}
    
# Get current logged-in user
def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# Example protected route
@router.get("/dashboard")
def dashboard(current_user: dict = Depends(get_current_user)):
    return {
        "message": f"Welcome {current_user['name']}!",
        "email": current_user["email"],
        "id": str(current_user["_id"])  # Convert ObjectId to string
    }


