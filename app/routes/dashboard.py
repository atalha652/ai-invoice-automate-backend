from fastapi import APIRouter, HTTPException, Query
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import os
import certifi
from datetime import datetime, timedelta
from typing import Optional

load_dotenv()

router = APIRouter(prefix="/accounting/dashboard", tags=["Dashboard"])

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]

# Collections
voucher_collection = db["voucher"]
ledger_collection = db["ledger"]
ocr_jobs_collection = db["ocr_jobs"]
users_collection = db["users"]


@router.get("/stats/{user_id}")
async def get_dashboard_stats(
    user_id: str,
    period: Optional[str] = Query("all", description="Time period: 'today', 'week', 'month', 'year', 'all'")
):
    """
    Get comprehensive dashboard statistics for a user.
    Example: GET /accounting/dashboard/stats/6904a8e4fbb19569d323c78c?period=month
    """
    try:
        # Calculate date range based on period
        now = datetime.utcnow()
        date_filter = {}
        
        if period == "today":
            start_of_day = datetime(now.year, now.month, now.day)
            date_filter = {"created_at": {"$gte": start_of_day}}
        elif period == "week":
            start_of_week = now - timedelta(days=7)
            date_filter = {"created_at": {"$gte": start_of_week}}
        elif period == "month":
            start_of_month = now - timedelta(days=30)
            date_filter = {"created_at": {"$gte": start_of_month}}
        elif period == "year":
            start_of_year = now - timedelta(days=365)
            date_filter = {"created_at": {"$gte": start_of_year}}
        # 'all' means no date filter
        
        # Base query for user
        base_query = {"user_id": user_id}
        query_with_date = {**base_query, **date_filter}
        
        # 1. Voucher Statistics
        total_vouchers = voucher_collection.count_documents(query_with_date)
        pending_vouchers = voucher_collection.count_documents({**query_with_date, "status": "pending"})
        awaiting_approval = voucher_collection.count_documents({**query_with_date, "status": "awaiting_approval"})
        approved_vouchers = voucher_collection.count_documents({**query_with_date, "status": "approved"})
        rejected_vouchers = voucher_collection.count_documents({**query_with_date, "status": "rejected"})
        
        # 2. OCR Statistics
        ocr_pending = voucher_collection.count_documents({**query_with_date, "OCR": "pending"})
        ocr_processing = voucher_collection.count_documents({**query_with_date, "OCR": "processing"})
        ocr_done = voucher_collection.count_documents({**query_with_date, "OCR": "done"})
        ocr_failed = voucher_collection.count_documents({**query_with_date, "OCR": "failed"})
        ocr_partial = voucher_collection.count_documents({**query_with_date, "OCR": "partial"})
        
        # 3. Transaction Type Statistics
        credit_transactions = voucher_collection.count_documents({**query_with_date, "transaction_type": "credit"})
        debit_transactions = voucher_collection.count_documents({**query_with_date, "transaction_type": "debit"})
        
        # 4. Recent Activity - Last 5 vouchers
        recent_vouchers = list(voucher_collection.find(
            base_query,
            {"_id": 1, "status": 1, "OCR": 1, "created_at": 1, "title": 1, "transaction_type": 1}
        ).sort("created_at", -1).limit(5))
        
        for voucher in recent_vouchers:
            voucher["_id"] = str(voucher["_id"])
            if "created_at" in voucher:
                voucher["created_at"] = voucher["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        
        # 5. Ledger Statistics
        total_ledger_entries = ledger_collection.count_documents(base_query)
        successful_ocr = ledger_collection.count_documents({**base_query, "processing_status": "success"})
        failed_ocr = ledger_collection.count_documents({**base_query, "processing_status": "llm_failed"})
        
        # 6. Financial Summary from Ledger
        ledger_entries = list(ledger_collection.find(
            base_query,
            {"invoice_data": 1}
        ))
        
        total_amount = 0
        total_vat = 0
        invoice_count = 0
        
        for entry in ledger_entries:
            if entry.get("invoice_data") and entry["invoice_data"].get("totals"):
                totals = entry["invoice_data"]["totals"]
                if totals.get("Total_with_Tax"):
                    total_amount += totals["Total_with_Tax"]
                if totals.get("VAT_amount"):
                    total_vat += totals["VAT_amount"]
                invoice_count += 1
        
        # 7. Rejection Statistics
        vouchers_with_rejections = list(voucher_collection.find(
            {**base_query, "rejection_count": {"$exists": True, "$gt": 0}},
            {"rejection_count": 1}
        ))
        
        total_rejections = sum(v.get("rejection_count", 0) for v in vouchers_with_rejections)
        avg_rejections = total_rejections / len(vouchers_with_rejections) if vouchers_with_rejections else 0
        
        # 8. Category Breakdown
        categories = voucher_collection.aggregate([
            {"$match": query_with_date},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ])
        category_breakdown = [{"category": cat["_id"] or "Uncategorized", "count": cat["count"]} for cat in categories]
        
        # 9. OCR Job Statistics
        total_ocr_jobs = ocr_jobs_collection.count_documents({"user_id": user_id})
        successful_jobs = ocr_jobs_collection.count_documents({"user_id": user_id, "status": "success"})
        failed_jobs = ocr_jobs_collection.count_documents({"user_id": user_id, "status": "failed"})
        awaiting_jobs = ocr_jobs_collection.count_documents({"user_id": user_id, "status": "awaiting"})
        
        # Build response
        return {
            "user_id": user_id,
            "period": period,
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            
            "voucher_stats": {
                "total": total_vouchers,
                "pending": pending_vouchers,
                "awaiting_approval": awaiting_approval,
                "approved": approved_vouchers,
                "rejected": rejected_vouchers,
                "approval_rate": round((approved_vouchers / total_vouchers * 100) if total_vouchers > 0 else 0, 2)
            },
            
            "ocr_stats": {
                "pending": ocr_pending,
                "processing": ocr_processing,
                "done": ocr_done,
                "failed": ocr_failed,
                "partial": ocr_partial,
                "success_rate": round((ocr_done / total_vouchers * 100) if total_vouchers > 0 else 0, 2)
            },
            
            "transaction_stats": {
                "credit": credit_transactions,
                "debit": debit_transactions,
                "total": credit_transactions + debit_transactions
            },
            
            "financial_summary": {
                "total_amount": round(total_amount, 2),
                "total_vat": round(total_vat, 2),
                "invoice_count": invoice_count,
                "average_invoice_amount": round(total_amount / invoice_count, 2) if invoice_count > 0 else 0
            },
            
            "rejection_stats": {
                "total_rejections": total_rejections,
                "vouchers_with_rejections": len(vouchers_with_rejections),
                "average_rejections_per_voucher": round(avg_rejections, 2)
            },
            
            "ledger_stats": {
                "total_entries": total_ledger_entries,
                "successful_ocr": successful_ocr,
                "failed_ocr": failed_ocr
            },
            
            "ocr_job_stats": {
                "total_jobs": total_ocr_jobs,
                "successful": successful_jobs,
                "failed": failed_jobs,
                "awaiting": awaiting_jobs
            },
            
            "category_breakdown": category_breakdown,
            
            "recent_activity": recent_vouchers
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching dashboard stats: {str(e)}")


@router.get("/summary/{user_id}")
async def get_quick_summary(user_id: str):
    """
    Get a quick summary for dashboard header/overview.
    Example: GET /accounting/dashboard/summary/6904a8e4fbb19569d323c78c
    """
    try:
        # Quick counts
        total_vouchers = voucher_collection.count_documents({"user_id": user_id})
        pending_approval = voucher_collection.count_documents({"user_id": user_id, "status": "awaiting_approval"})
        approved_today = voucher_collection.count_documents({
            "user_id": user_id,
            "status": "approved",
            "approved_at": {"$gte": datetime(datetime.utcnow().year, datetime.utcnow().month, datetime.utcnow().day)}
        })
        
        ocr_in_progress = voucher_collection.count_documents({"user_id": user_id, "OCR": "processing"})
        
        return {
            "user_id": user_id,
            "total_vouchers": total_vouchers,
            "pending_approval": pending_approval,
            "approved_today": approved_today,
            "ocr_in_progress": ocr_in_progress
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching summary: {str(e)}")
