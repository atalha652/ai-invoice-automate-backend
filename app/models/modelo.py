"""
Modelo Model
Represents tax/legal form models with their properties
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Annotated
from datetime import datetime
from bson import ObjectId


# ObjectId handling is done in the repository layer


class ModeloBase(BaseModel):
    modelo_no: str = Field(..., description="Model number/identifier")
    name: str = Field(..., description="Name or purpose of the model")
    periodicity: str = Field(..., description="How often it needs to be filed (monthly, quarterly, yearly)")
    deadline: str = Field(..., description="Filing deadline (e.g., '20th of following month'")

    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )


class ModeloCreate(ModeloBase):
    """Model for creating a new modelo"""
    pass


class ModeloUpdate(BaseModel):
    """Model for updating an existing modelo"""
    modelo_no: Optional[str] = None
    name: Optional[str] = None
    periodicity: Optional[str] = None
    deadline: Optional[str] = None


class ModeloResponse(ModeloBase):
    """Model for modelo response"""
    id: str = Field(alias="_id")
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )


class ModeloBulkCreate(BaseModel):
    """Model for bulk creating modelos"""
    modelos: List[ModeloCreate] = Field(..., description="List of modelos to create")


class ModeloBulkResponse(BaseModel):
    """Response for bulk operations"""
    success: bool
    created_count: int
    failed_count: int = 0
    created_ids: List[str] = []
    errors: List[str] = []