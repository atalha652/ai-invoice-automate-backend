from fastapi import APIRouter, HTTPException, Query
from datetime import datetime,timezone
from bson import ObjectId
import certifi
import os
from pymongo import MongoClient
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
