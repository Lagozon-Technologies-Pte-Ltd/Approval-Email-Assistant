"""
Emails Router - Fetch and filter approval emails via Microsoft Graph API
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from config import settings
from routers.auth import get_valid_access_token
from services.priority import compute_priority
from services.tracking import tracking_store

router = APIRouter()

APPROVAL_KEYWORDS = [
    "approval", "approve", "approved", "request", "review",
    "urgent approval", "pending", "authorize", "sign off", "sign-off",
    "invoice", "contract", "agreement", "approval form", "needs your approval",
    "action required", "decision needed", "please review", "your review",
]

HIGH_PRIORITY_KEYWORDS = ["invoice", "contract", "agreement", "approval form", "urgent"]


def _build_time_filter(
    preset: Optional[str] = None,
    start_dt: Optional[str] = None,
    end_dt: Optional[str] = None,
    duration_value: Optional[int] = None,
    duration_unit: Optional[str] = None,
) -> tuple[str, str]:
    """Returns (start_iso, end_iso) for Graph API $filter."""
    now = datetime.now(timezone.utc)

    if preset:
        mapping = {
            "24h": timedelta(hours=24),
            "2d": timedelta(days=2),
            "1w": timedelta(weeks=1),
            "1m": timedelta(days=30),
        }
        delta = mapping.get(preset, timedelta(days=7))
        start = now - delta
        end = now

    elif start_dt and end_dt:
        start = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))

    elif duration_value and duration_unit:
        unit_map = {
            "hours": timedelta(hours=duration_value),
            "days": timedelta(days=duration_value),
            "weeks": timedelta(weeks=duration_value),
            "months": timedelta(days=duration_value * 30),
        }
        delta = unit_map.get(duration_unit, timedelta(days=7))
        start = now - delta
        end = now

    else:
        start = now - timedelta(days=7)
        end = now

    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_approval_email(email: dict) -> bool:
    """Check if email is approval-related."""
    subject = (email.get("subject") or "").lower()
    body = (email.get("bodyPreview") or "").lower()
    has_attachments = email.get("hasAttachments", False)

    for kw in APPROVAL_KEYWORDS:
        if kw in subject or kw in body:
            return True

    if has_attachments:
        return True

    return False


async def _fetch_emails_from_graph(
    access_token: str,
    start_iso: str,
    end_iso: str,
    folder: str = "inbox",
    top: int = 50,
) -> list[dict]:
    """Fetch emails from Microsoft Graph API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }

    filter_query = (
        f"receivedDateTime ge {start_iso} and receivedDateTime le {end_iso}"
    )

    params = {
        "$filter": filter_query,
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$select": (
            "id,subject,from,receivedDateTime,bodyPreview,body,"
            "hasAttachments,isRead,isDraft,importance,conversationId"
        ),
    }

    url = f"{settings.GRAPH_API_BASE}/me/mailFolders/{folder}/messages"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Graph API unauthorized")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Graph API error: {resp.text}")
        data = resp.json()

    return data.get("value", [])


async def _fetch_attachments_meta(access_token: str, email_id: str) -> list[dict]:
    """Fetch attachment metadata for an email."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/attachments"
    params = {"$select": "id,name,contentType,size"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            return []
        return resp.json().get("value", [])


async def _fetch_attachment_content(access_token: str, email_id: str, attachment_id: str) -> bytes:
    """Fetch attachment binary content."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/attachments/{attachment_id}/$value"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return b""
        return resp.content


def _format_email(raw: dict, attachments: list = None) -> dict:
    """Format raw Graph API email into our schema."""
    status = tracking_store.get_status(raw["id"])
    priority = compute_priority(raw, attachments or [])

    return {
        "id": raw["id"],
        "subject": raw.get("subject", "(No Subject)"),
        "sender": raw.get("from", {}).get("emailAddress", {}).get("name", "Unknown"),
        "senderEmail": raw.get("from", {}).get("emailAddress", {}).get("address", ""),
        "receivedDateTime": raw.get("receivedDateTime", ""),
        "bodyPreview": raw.get("bodyPreview", ""),
        "body": raw.get("body", {}).get("content", ""),
        "hasAttachments": raw.get("hasAttachments", False),
        "isRead": raw.get("isRead", False),
        "importance": raw.get("importance", "normal"),
        "conversationId": raw.get("conversationId", ""),
        "priority": priority,
        "status": status,
        "attachments": attachments or [],
    }


@router.get("/approval")
async def get_approval_emails(
    request: Request,
    preset: Optional[str] = Query(None, description="24h, 2d, 1w, 1m"),
    start_dt: Optional[str] = Query(None),
    end_dt: Optional[str] = Query(None),
    duration_value: Optional[int] = Query(None),
    duration_unit: Optional[str] = Query(None, description="hours, days, weeks, months"),
):
    """Fetch approval-related emails with time filtering."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    start_iso, end_iso = _build_time_filter(preset, start_dt, end_dt, duration_value, duration_unit)

    raw_emails = await _fetch_emails_from_graph(access_token, start_iso, end_iso)
    approval_emails = [e for e in raw_emails if _is_approval_email(e)]

    result = []
    for email in approval_emails:
        attachments = []
        if email.get("hasAttachments"):
            attachments = await _fetch_attachments_meta(access_token, email["id"])
        result.append(_format_email(email, attachments))

    # Group by time
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    grouped = {"today": [], "this_week": [], "older": []}
    for e in result:
        try:
            rd = datetime.fromisoformat(e["receivedDateTime"].replace("Z", "+00:00"))
        except Exception:
            grouped["older"].append(e)
            continue
        if rd >= today_start:
            grouped["today"].append(e)
        elif rd >= week_start:
            grouped["this_week"].append(e)
        else:
            grouped["older"].append(e)

    return {
        "emails": result,
        "grouped": grouped,
        "total": len(result),
        "filter_range": {"start": start_iso, "end": end_iso},
    }


@router.get("/other")
async def get_other_emails(
    request: Request,
    preset: Optional[str] = Query(None),
    start_dt: Optional[str] = Query(None),
    end_dt: Optional[str] = Query(None),
):
    """Fetch non-approval emails for the digest view."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    start_iso, end_iso = _build_time_filter(preset, start_dt, end_dt)

    raw_emails = await _fetch_emails_from_graph(access_token, start_iso, end_iso, top=30)
    other_emails = [e for e in raw_emails if not _is_approval_email(e)]

    return {
        "emails": [_format_email(e) for e in other_emails],
        "total": len(other_emails),
    }


@router.get("/{email_id}")
async def get_email_detail(email_id: str, request: Request):
    """Get full email detail including attachments."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.body-content-type="html"',
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me/messages/{email_id}",
            headers=headers,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Email not found")
        raw = resp.json()

    attachments = []
    if raw.get("hasAttachments"):
        attachments = await _fetch_attachments_meta(access_token, email_id)

    return _format_email(raw, attachments)


@router.get("/{email_id}/attachments/{attachment_id}/download")
async def download_attachment(email_id: str, attachment_id: str, request: Request):
    """Stream attachment download."""
    from fastapi.responses import Response

    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    content = await _fetch_attachment_content(access_token, email_id, attachment_id)

    # Get meta for content-type
    headers_auth = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        meta_resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/attachments/{attachment_id}",
            headers=headers_auth,
            params={"$select": "name,contentType"},
        )
        meta = meta_resp.json() if meta_resp.status_code == 200 else {}

    filename = meta.get("name", "attachment")
    content_type = meta.get("contentType", "application/octet-stream")

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
