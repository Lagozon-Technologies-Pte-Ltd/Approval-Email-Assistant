"""
Approval AI Dashboard - FastAPI Backend
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from backend.routers import auth, emails, actions, summary

# =========================================================
# APP INIT
# =========================================================

app = FastAPI(
    title="Approval AI Dashboard API",
    description="Microsoft Outlook + OpenAI powered approval management system",
    version="1.0.0"
)

# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# ROUTERS
# =========================================================

app.include_router(
    auth.router,
    prefix="/api/auth",
    tags=["Authentication"]
)

app.include_router(
    emails.router,
    prefix="/api/emails",
    tags=["Emails"]
)

app.include_router(
    actions.router,
    prefix="/api/actions",
    tags=["Actions"]
)

app.include_router(
    summary.router,
    prefix="/api/summary",
    tags=["AI Summary"]
)

# =========================================================
# PATH SETUP
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# project root
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# frontend directory
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# css/js/static
CSS_DIR = os.path.join(FRONTEND_DIR, "css")
JS_DIR = os.path.join(FRONTEND_DIR, "js")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")

# =========================================================
# STATIC FILES
# =========================================================

if os.path.exists(CSS_DIR):
    app.mount(
        "/css",
        StaticFiles(directory=CSS_DIR),
        name="css"
    )

if os.path.exists(JS_DIR):
    app.mount(
        "/js",
        StaticFiles(directory=JS_DIR),
        name="js"
    )

# IMPORTANT
# THIS FIXES YOUR OUTLOOK ICON ISSUE

if os.path.exists(STATIC_DIR):
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static"
    )

# =========================================================
# FRONTEND ROUTE
# =========================================================

@app.get("/")
def serve_frontend():

    index_file = os.path.join(FRONTEND_DIR, "index.html")

    if os.path.exists(index_file):
        return FileResponse(index_file)

    return {"message": "Frontend not found"}

# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "Approval AI Dashboard",
        "version": "1.0.0"
    }