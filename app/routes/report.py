import bcrypt
from pymongo import MongoClient
import os
import bcrypt
import os
import bcrypt
import bcrypt
from fastapi import APIRouter
import certifi
import io
import os
import re
from fastapi import APIRouter, HTTPException
from bson import ObjectId
from datetime import datetime
# Set Tesseract path (Windows)
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
projects_collection = db["projects"]



router = APIRouter()

@router.get("/report/{user_id}")
def get_user_monthly_report(user_id: str):
    pipeline = [
        {
            "$match": {
                "user_id": user_id  # Filter by user ID
            }
        },
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$created_at"},
                    "month": {"$month": "$created_at"}
                },
                "projects": {"$sum": 1},
                "success_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "Success"]}, 1, 0]}
                },
                "total_bill": {"$sum": "$invoice_data.total"},
                "total_vat": {"$sum": "$invoice_data.VAT_amount"},
                "total_with_vat": {"$sum": "$invoice_data.Total_with_Tax"}
            }
        },
        {
            "$addFields": {
                "success_percentage": {
                    "$cond": [
                        {"$eq": ["$projects", 0]},
                        0,
                        {"$round": [
                            {"$multiply": [
                                {"$divide": ["$success_count", "$projects"]},
                                100
                            ]},
                            2
                        ]}
                    ]
                }
            }
        },
        {
            "$sort": {"_id.year": 1, "_id.month": 1}
        }
    ]

    report_data = list(projects_collection.aggregate(pipeline))

    # Format result
    formatted_report = []
    for row in report_data:
        year = row["_id"]["year"]
        month = f"{row['_id']['month']:02d}"
        formatted_report.append({
            "Month": f"{year}-{month}",
            "Projects": row["projects"],
            "Success %": f"{row['success_percentage']}%",
            "Total Bill": row.get("total_bill", 0),
            "VAT Total": row.get("total_vat", 0),
            "Bill+VAT Total": row.get("total_with_vat", 0)
        })

    if not formatted_report:
        raise HTTPException(status_code=404, detail="No data found for this user")

    return formatted_report