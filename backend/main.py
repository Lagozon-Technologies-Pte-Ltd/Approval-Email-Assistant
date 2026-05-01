"""
Approval AI Dashboard - FastAPI Backend
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from routers import auth, emails, actions, summary

# =========================
# APP INIT
# =========================
app = FastAPI(
    title="Approval AI Dashboard API",
    description="Microsoft Outlook + OpenAI powered approval management system",
    version="1.0.0"
)

# =========================
# CORS (Allow frontend access)
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# ROUTERS
# =========================
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(emails.router, prefix="/api/emails", tags=["Emails"])
app.include_router(actions.router, prefix="/api/actions", tags=["Actions"])
app.include_router(summary.router, prefix="/api/summary", tags=["AI Summary"])

# =========================
# PATH SETUP (IMPORTANT FOR AZURE)
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)  # go outside backend/
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# =========================
# STATIC FILES
# =========================
if os.path.exists(FRONTEND_DIR):
    app.mount(
        "/css",
        StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")),
        name="css"
    )
    app.mount(
        "/js",
        StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")),
        name="js"
    )

# =========================
# FRONTEND ROUTE
# =========================
@app.get("/")
def serve_frontend():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Frontend not found"}

# =========================
# HEALTH CHECK (FOR AZURE)
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Approval AI Dashboard",
        "version": "1.0.0"
    }
