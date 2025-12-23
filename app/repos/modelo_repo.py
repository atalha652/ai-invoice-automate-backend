"""
Modelo Repository
Database operations for modelo collection
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from pymongo.database import Database
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
import logging

from app.models.modelo import ModeloCreate, ModeloUpdate, ModeloResponse

logger = logging.getLogger(__name__)


class ModeloRepository:
    """Repository for modelo operations"""

    def __init__(self, db: Database):
        self.db = db
        self.collection: Collection = db["modelos"]
        self._create_indexes()

    def _create_indexes(self):
        """Create database indexes"""
        try:
            self.collection.create_index("modelo_no", unique=True)
            self.collection.create_index([("name", "text"), ("modelo_no", "text")])
        except Exception as e:
            logger.warning(f"Index creation failed: {e}")

    def create_modelo(self, modelo: ModeloCreate) -> str:
        """Create a new modelo"""
        try:
            modelo_dict = modelo.dict()
            modelo_dict["created_at"] = datetime.utcnow()
            modelo_dict["updated_at"] = None
            
            result = self.collection.insert_one(modelo_dict)
            logger.info(f"Created modelo with ID: {result.inserted_id}")
            return str(result.inserted_id)
            
        except DuplicateKeyError:
            raise ValueError(f"Modelo with number '{modelo.modelo_no}' already exists")

    def get_modelo(self, modelo_id: str) -> Optional[ModeloResponse]:
        """Get modelo by ID"""
        try:
            if not ObjectId.is_valid(modelo_id):
                return None
            modelo = self.collection.find_one({"_id": ObjectId(modelo_id)})
            if modelo:
                modelo["_id"] = str(modelo["_id"])  # Convert ObjectId to string
                return ModeloResponse(**modelo)
            return None
        except Exception as e:
            logger.error(f"Error getting modelo: {e}")
            return None

    def get_all_modelos(self, skip: int = 0, limit: int = 100) -> List[ModeloResponse]:
        """Get all modelos with pagination"""
        try:
            cursor = self.collection.find().skip(skip).limit(limit).sort("modelo_no", 1)
            modelos = []
            for m in cursor:
                m["_id"] = str(m["_id"])  # Convert ObjectId to string
                modelos.append(ModeloResponse(**m))
            return modelos
        except Exception as e:
            logger.error(f"Error getting modelos: {e}")
            return []

    def get_modelo_by_number(self, modelo_no: str) -> Optional[ModeloResponse]:
        """Get modelo by modelo number"""
        try:
            modelo = self.collection.find_one({"modelo_no": modelo_no})
            if modelo:
                modelo["_id"] = str(modelo["_id"])  # Convert ObjectId to string
                return ModeloResponse(**modelo)
            return None
        except Exception as e:
            logger.error(f"Error getting modelo by number: {e}")
            return None

    def count_modelos(self) -> int:
        """Count total modelos"""
        try:
            return self.collection.count_documents({})
        except Exception as e:
            logger.error(f"Error counting modelos: {e}")
            return 0

    def update_modelo(self, modelo_id: str, updates: ModeloUpdate) -> bool:
        """Update modelo"""
        try:
            if not ObjectId.is_valid(modelo_id):
                return False
                
            # Filter out None values
            update_dict = {k: v for k, v in updates.dict().items() if v is not None}
            
            if not update_dict:
                return True  # Nothing to update
                
            update_dict["updated_at"] = datetime.utcnow()
            
            result = self.collection.update_one(
                {"_id": ObjectId(modelo_id)},
                {"$set": update_dict}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error updating modelo {modelo_id}: {e}")
            return False

    def delete_modelo(self, modelo_id: str) -> bool:
        """Delete modelo"""
        try:
            if not ObjectId.is_valid(modelo_id):
                return False
                
            result = self.collection.delete_one({"_id": ObjectId(modelo_id)})
            return result.deleted_count > 0
            
        except Exception as e:
            logger.error(f"Error deleting modelo {modelo_id}: {e}")
            return False

    def bulk_create_modelos(self, modelos: List[ModeloCreate]) -> Dict[str, Any]:
        """Bulk create modelos"""
        results = {
            "created_count": 0,
            "failed_count": 0,
            "created_ids": [],
            "errors": []
        }
        
        for modelo in modelos:
            try:
                modelo_id = self.create_modelo(modelo)
                results["created_count"] += 1
                results["created_ids"].append(modelo_id)
            except Exception as e:
                results["failed_count"] += 1
                results["errors"].append(f"Modelo {modelo.modelo_no}: {str(e)}")
                
        return results