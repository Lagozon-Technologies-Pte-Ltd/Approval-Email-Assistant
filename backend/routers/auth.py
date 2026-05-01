"""
Authentication Router - Microsoft OAuth via Entra ID
"""

import secrets
import urllib.parse
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from config import settings, OAUTH_SCOPES

router = APIRouter()

# In-memory token store (use Redis/DB in production)
_token_store: dict = {}


def _build_auth_url(state: str) -> str:
    params = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        "scope": " ".join(OAUTH_SCOPES),
        "state": state,
        "response_mode": "query",
        "prompt": "select_account",
    }
    base = f"{settings.AUTHORITY_URL}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/authorize"
    return base + "?" + urllib.parse.urlencode(params)


async def _exchange_code_for_tokens(code: str) -> dict:
    token_url = f"{settings.AUTHORITY_URL}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")
        return resp.json()


async def _refresh_access_token(refresh_token: str) -> dict:
    token_url = f"{settings.AUTHORITY_URL}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": " ".join(OAUTH_SCOPES),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Token refresh failed")
        return resp.json()


from typing import Optional

def get_stored_token(session_id: str) -> Optional[dict]:   
    return _token_store.get(session_id)


def store_token(session_id: str, token_data: dict):
    token_data["stored_at"] = datetime.utcnow().isoformat()
    _token_store[session_id] = token_data


async def get_valid_access_token(session_id: str) -> str:
    """Return a valid access token, refreshing if needed."""
    data = _token_store.get(session_id)
    if not data:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stored_at = datetime.fromisoformat(data["stored_at"])
    expires_in = data.get("expires_in", 3600)
    if datetime.utcnow() > stored_at + timedelta(seconds=expires_in - 60):
        # Refresh
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise HTTPException(status_code=401, detail="Session expired, please login again")
        new_tokens = await _refresh_access_token(refresh_token)
        store_token(session_id, new_tokens)
        return new_tokens["access_token"]

    return data["access_token"]


@router.get("/login")
async def login(response: Response):
    """Initiate Microsoft OAuth flow."""
    state = secrets.token_urlsafe(32)
    auth_url = _build_auth_url(state)
    resp = JSONResponse({"auth_url": auth_url, "state": state})
    resp.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return resp


@router.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handle OAuth callback from Microsoft."""
    if error:
        return RedirectResponse(url=f"/?error={error}")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    stored_state = request.cookies.get("oauth_state")
    if stored_state and stored_state != state:
        raise HTTPException(status_code=400, detail="State mismatch - possible CSRF attack")

    tokens = await _exchange_code_for_tokens(code)
    session_id = secrets.token_urlsafe(32)
    store_token(session_id, tokens)

    # Fetch user profile
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        user_info = resp.json() if resp.status_code == 200 else {}

    redirect = RedirectResponse(url="/?authenticated=true")
    redirect.set_cookie(
        "session_id",
        session_id,
        httponly=True,
        samesite="lax",
        max_age=3600 * 8,
        secure=settings.ENVIRONMENT == "production",
    )
    redirect.delete_cookie("oauth_state")
    return redirect


@router.get("/me")
async def get_me(request: Request):
    """Return current user info."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user info")
        user = resp.json()

    return {
        "id": user.get("id"),
        "displayName": user.get("displayName"),
        "mail": user.get("mail") or user.get("userPrincipalName"),
        "jobTitle": user.get("jobTitle"),
    }


@router.post("/logout")
async def logout(request: Request):
    """Clear session."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in _token_store:
        del _token_store[session_id]
    resp = JSONResponse({"message": "Logged out"})
    resp.delete_cookie("session_id")
    return resp


@router.get("/status")
async def auth_status(request: Request):
    """Check if user is authenticated."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in _token_store:
        return {"authenticated": False}
    return {"authenticated": True}
