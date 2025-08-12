from fastapi import FastAPI
from app.routes import api ,auth ,project, report

app = FastAPI()

# Include routes
app.include_router(api.router,prefix="/api/api")
app.include_router(auth.router,prefix="/api/auth")
app.include_router(project.router,prefix="/api/project")
app.include_router(report.router,prefix="/api/report")
@app.get("/")
def root():
    return {"message": "Welcome to my FastAPI app"}
