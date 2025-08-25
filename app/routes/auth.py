from fastapi import APIRouter, File, Depends, HTTPException
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
from dotenv import load_dotenv
from fastapi.security import OAuth2PasswordBearer
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from enum import Enum
from fastapi import Form, File, UploadFile
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
    type_id: Optional[str] = None   # dropdown selection
    type_name: Optional[str] = None # custom user entry
    company_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None

class BankDetails(BaseModel):
    iban: str
    account_holder: str
class PaymentMethod(str, Enum):
    stripe = "Stripe"
    redsys = "Redsys"
    bizum = "Bizum"
class Role(str, Enum):
    user = "user"
    admin = "admin"
class OtherCertificate(BaseModel):
    name: str
    url_: str
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    password: str
    type: UserType
    tax_id: Optional[str] = None
    organization_info: Optional[OrganizationInfo] = None
    registration_flow: Optional[str] = None
    has_digital_certificate: Optional[str] = None
    auto_fill: Optional[bool] = False
    dni_nie: Optional[str] = None
    bank_details: Optional[BankDetails] = None
    # Change this line to properly handle None:
    payment_method: Optional[PaymentMethod] = None  # This should work now
    role: Optional[Role] = None
    connect_to_fnmt: Optional[bool] = False
    connect_to_aeat: Optional[bool] = False
    administrator_check: Optional[bool] = False
    type_of_administration: Optional[str] = None
    other_certificate: Optional[List[OtherCertificate]] = []
    status: Optional[bool] = False
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
@router.post(
    "/signup",
    summary="Register a new user",
    description="""
This endpoint registers a new user in the system.  
It supports both **individual** and **organization** flows.  

### Features:
- Registration flow (personal/company)
- Optional Digital Certificate upload
- FNMT & AEAT integration flags
- Administration checks
- Additional certificates (JSON list)
- Payment method selection

### Notes:
- `certificate` must be uploaded as `multipart/form-data` file.
- `other_certificate` must be sent as a **JSON string** inside form-data,  
  e.g. `[{"name":"Cert A","url_":"https://example.com/a"}]`.

"""
)
async def signup(
    # Basic info
    name: str = Form(..., description="Full name of the user"),
    email: EmailStr = Form(..., description="Email address (must be unique)"),
    password: str = Form(..., description="Password (will be hashed)"),
    type: UserType = Form(..., description="User type: 'individual' or 'organization'"),
    phone: Optional[str] = Form(None, description="Phone number"),
    tax_id: Optional[str] = Form(None, description="Tax identification number (NIF/CIF)"),

    # Registration flow
    registration_flow: Optional[str] = Form(None, description="Registration flow: 'personal_flow' or 'company_flow'"),
    role: Optional[Role] = Form(None, description="User role: 'user' or 'admin'"),
    # Digital certificate
    has_digital_certificate: Optional[str] = Form(None, description="'yes_flow' or 'no_flow'"),
    auto_fill: Optional[bool] = Form(False, description="Auto-fill data if certificate available"),
    dni_nie: Optional[str] = Form(None, description="National ID (DNI/NIE)"),
    iban: Optional[str] = Form(None, description="IBAN (bank account)"),
    account_holder: Optional[str] = Form(None, description="Bank account holder name"),
    certificate: UploadFile = File(None, description="Digital certificate file (.p12/.pfx/.pdf)"),

    # FNMT & AEAT
    connect_to_fnmt: Optional[bool] = Form(False, description="Generate FNMT request code"),
    connect_to_aeat: Optional[bool] = Form(False, description="Request AEAT appointment (online/in-person)"),
    status: Optional[bool] = Form(False, description="Status of the organization (default: False)"),
    # Administration
    administrator_check: Optional[bool] = Form(False, description="Admin validation required?"),
    type_of_administration: Optional[str] = Form(None, description="Type of administration (e.g. central, regional)"),

    # Other certificates
    other_certificate: Optional[str] = Form(
        None,
        description="JSON list of certificates. Example: "
                    "[{\"name\":\"Cert A\",\"url_\":\"https://example.com/a\"}]"
    ),

    # Payment
    payment_method: Optional[PaymentMethod] = Form(None, description="Payment method: Stripe / Redsys / Bizum")
):
    import os, json, uuid
    # In your signup endpoint, add this before building the UserCreate object:
    if payment_method:
        # Convert to proper case to match enum
        payment_method_lower = payment_method.lower()
        if payment_method_lower == "stripe":
            payment_method_enum = PaymentMethod.stripe
        elif payment_method_lower == "redsys":
            payment_method_enum = PaymentMethod.redsys
        elif payment_method_lower == "bizum":
            payment_method_enum = PaymentMethod.bizum
        else:
            payment_method_enum = None
    else:
        payment_method_enum = None
    # Hash password
    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    # Save uploaded certificate with unique filename
    cert_path = None
    if certificate:
        os.makedirs("certs", exist_ok=True)
        unique_filename = f"{uuid.uuid4()}_{certificate.filename}"
        cert_path = os.path.join("certs", unique_filename)
        with open(cert_path, "wb") as f:
            f.write(await certificate.read())

    # Parse other_certificate JSON
    other_certs = []
    if other_certificate:
        try:
            other_certs = json.loads(other_certificate)
        except Exception:
            other_certs = []

    # Build user object
# Build user object
    user = UserCreate(
        name=name,
        email=email,
        phone=phone,
        password=password,
        type=type,
        tax_id=tax_id,
        organization_info=None,
        registration_flow=registration_flow,
        has_digital_certificate=has_digital_certificate,
        auto_fill=auto_fill,
        dni_nie=dni_nie,
        bank_details=BankDetails(iban=iban, account_holder=account_holder) if iban and account_holder else None,
        payment_method=payment_method_enum,  # Use the converted enum
        connect_to_fnmt=connect_to_fnmt,
        connect_to_aeat=connect_to_aeat,
        administrator_check=administrator_check,
        status=status,
        role=role,
        type_of_administration=type_of_administration,
        other_certificate=[OtherCertificate(**oc) for oc in other_certs] if other_certs else []
    )

    # Prepare DB document
    new_user = user.dict()
    new_user.update({
        "password_hash": hashed_pw,
        "certificate_path": cert_path if has_digital_certificate == "yes_flow" else None,
        "created_at": datetime.utcnow(),
    })

    # Organization handling
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
            "type": org_type["name"],
            "company_name": user.organization_info.company_name,
            "address": user.organization_info.address,
            "phone": user.organization_info.phone,
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

    # Remove password from response
    db_user["_id"] = str(db_user["_id"])
    db_user.pop("password_hash", None)

    access_token = create_access_token(
        {"sub": str(db_user["_id"])},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    return {
        "message": "User logged in successfully",
        "access_token": access_token,
        "token_type": "bearer",
        "name": db_user["name"],
        "email": db_user["email"],
        "user_id": db_user["_id"],
        "tax_id": db_user["tax_id"],
        "organization_info": db_user.get("organization_info", {})
    }

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


