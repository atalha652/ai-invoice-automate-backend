from fastapi import FastAPI
from app.routes import api, auth, project, report, accounting, voucher, ledger, ocr, gmail_api, ledgers
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()
# CORS settings
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Include routes
app.include_router(api.router, prefix="/api/api")
app.include_router(auth.router, prefix="/api/auth")
app.include_router(project.router, prefix="/api/project")
app.include_router(report.router, prefix="/api/report")
app.include_router(accounting.router, prefix="/api")
app.include_router(voucher.router, prefix="/api")
app.include_router(ledger.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")
app.include_router(gmail_api.router, prefix="/api")
app.include_router(ledgers.router, prefix="/api")
@app.get("/")
def root():
    return {"message": "Welcome to my FastAPI app"}
