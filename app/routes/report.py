from fastapi import APIRouter, HTTPException, Query
from datetime import datetime,timezone
from bson import ObjectId
import certifi
import os
from pymongo import MongoClient
from typing import Optional
# Set Tesseract path (Windows)
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
projects_collection = db["projects"]
report_collection = db["report"]
invoice_collection = db["e-invoice"]


router = APIRouter()

@router.get("/report/{user_id}")
def get_user_monthly_report(
    user_id: str, 
    year: Optional[int] = Query(None, description="Year for the report (default: current year)"),
    month: Optional[int] = Query(None, description="Month for the report (default: current month)")
):
    """
    Get monthly report summary for a user including total amount, tax, and total with tax.
    
    Args:
        user_id: The user ID to get reports for
        year: Optional year (defaults to current year)
        month: Optional month (defaults to current month)
    
    Returns:
        Dict with total_monthly_amount, total_monthly_tax, and total_monthly_with_tax
    """
    try:
        # Validate user_id format
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        
        # Get current date if year/month not provided
        now = datetime.now(timezone.utc)
        target_year = year if year else now.year
        target_month = month if month else now.month
        
        # Validate month
        if not (1 <= target_month <= 12):
            raise HTTPException(status_code=400, detail="Month must be between 1 and 12")
        
        # Create date range for the target month
        start_of_month = datetime(target_year, target_month, 1, tzinfo=timezone.utc)
        
        # Calculate start of next month for range query
        if target_month == 12:
            start_of_next_month = datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            start_of_next_month = datetime(target_year, target_month + 1, 1, tzinfo=timezone.utc)
        
        # MongoDB aggregation pipeline to sum monthly totals
        pipeline = [
    {
        "$match": {
            "user_id": user_id,
            "created_at": {
                "$gte": start_of_month,
                "$lt": start_of_next_month
            }
        }
    },
    {
        "$group": {
            "_id": None,
            "total_monthly_amount": {"$sum": "$totals.total"},
            "total_monthly_tax": {"$sum": "$totals.VAT_amount"},
            "total_monthly_with_tax": {"$sum": "$totals.Total_with_Tax"},
            "report_count": {"$sum": 1}
        }
    }
]

        
        # Execute aggregation
        result = list(report_collection.aggregate(pipeline))
        
        # If no reports found for the month
        if not result:
            return {
                "user_id": user_id,
                "year": target_year,
                "month": target_month,
                "total_monthly_amount": 0,
                "total_monthly_tax": 0,
                "total_monthly_with_tax": 0,
                "report_count": 0,
                "message": f"No reports found for {target_year}-{target_month:02d}"
            }
        
        # Extract the aggregated data
        monthly_data = result[0]
        
        return {
            "user_id": user_id,
            "year": target_year,
            "month": target_month,
            "total_monthly_amount": monthly_data["total_monthly_amount"],
            "total_monthly_tax": monthly_data["total_monthly_tax"],
            "total_monthly_with_tax": monthly_data["total_monthly_with_tax"],
            "report_count": monthly_data["report_count"]
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date parameters: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/projects/date-range")
def get_projects_by_user_and_date_range(
    start_date: str = Query(..., description="Start date in format DD-MM-YYYY"),
    end_date: str = Query(..., description="End date in format DD-MM-YYYY"),
    user_id: str = Query(..., description="User ID to filter data")
):
    try:
        start_dt = datetime.strptime(start_date, "%d-%m-%Y")
        end_dt = datetime.strptime(end_date, "%d-%m-%Y").replace(hour=23, minute=59, second=59)

        query = {
            "user_id": user_id,
            "created_at": {"$gte": start_dt, "$lte": end_dt}
        }

        results = list(report_collection.find(query))  # ✅ query correct collection

        for doc in results:
            doc["_id"] = str(doc["_id"])
            if "project_id" in doc:
                doc["project_id"] = str(doc["project_id"])

        return {"count": len(results), "data": results}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use DD-MM-YYYY.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
