"""
Emails Router - Fetch and filter approval emails via Microsoft Graph API
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from backend.config import settings
from backend.routers.auth import get_valid_access_token, get_session_user_email
from backend.services.priority import compute_priority
from backend.services.tracking import tracking_store
from backend.services.db import get_emails_by_conversation

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
    """Check if email is an approval REQUEST (not a decision/reply/completion)."""

    # These patterns in the body preview indicate the email IS a decision or status
    # notification — not an incoming approval request.
    BODY_EXCLUSION_PREFIXES = [
        "approval granted",
        "your request for",
        "we are pleased to inform",
        "has been approved",
        "has been rejected",
        "has been declined",
        "request has been",
        "approval has been",
    ]

    # These anywhere in subject OR body mean it's a completed/notif email
    EXCLUDED_KEYWORDS = [
        "need more info",
        "needs more info",
        "request completed",
        "approval completed",
        "cancelled",
        "[approval-decision]",
    ]

    # Subject prefixes that indicate a reply thread we sent (not incoming request)
    SUBJECT_REPLY_EXCLUSIONS = [
        "re: approval granted",
        "re: request rejected",
        "re: additional information",
    ]

    subject = (email.get("subject") or "").lower()
    body = (email.get("bodyPreview") or "").lower()
    has_attachments = email.get("hasAttachments", False)

    # 1. Exclude if body preview starts with a decision/completion pattern
    body_stripped = body.strip()
    for prefix in BODY_EXCLUSION_PREFIXES:
        if body_stripped.startswith(prefix):
            return False

    # 2. Exclude if subject indicates it's a reply we sent
    for excl in SUBJECT_REPLY_EXCLUSIONS:
        if subject.startswith(excl):
            return False

    # 3. Exclude if keyword found in combined text
    combined_text = f"{subject} {body}"
    for kw in EXCLUDED_KEYWORDS:
        if kw in combined_text:
            return False

    # 4. Include if matches approval request keywords
    for kw in APPROVAL_KEYWORDS:
        if kw in combined_text:
            return True

    # 5. Include if has attachments (likely needs review)
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


def _format_email(raw: dict, attachments: list = None, user_id: str = "") -> dict:
    """Format raw Graph API email into our schema."""
    from backend.services.db import set_status as db_set_status
    status = tracking_store.get_status(raw["id"], user_id)
    priority = compute_priority(raw, attachments or [])

    # Register email in DB so conversation threading works even before any action is taken
    db_set_status(
        email_id=raw["id"],
        status=status,
        user_id=user_id,
        conversation_id=raw.get("conversationId", ""),
        received_at=raw.get("receivedDateTime", ""),
    )

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


def _enrich_with_thread_counts(emails: list) -> list:
    """
    For each email, count how many OTHER tracked emails share the same conversationId.
    Adds a 'threadCount' field (1 = standalone, 2+ = has thread siblings).
    """
    from backend.services.db import get_emails_by_conversation
    conv_cache: dict = {}
    for email in emails:
        conv_id = email.get("conversationId", "")
        if not conv_id:
            email["threadCount"] = 1
            continue
        if conv_id not in conv_cache:
            conv_cache[conv_id] = len(get_emails_by_conversation(conv_id))
        email["threadCount"] = max(conv_cache[conv_id], 1)
    return emails


async def _fetch_email_by_id(access_token: str, email_id: str) -> Optional[dict]:
    """Fetch a single email from Graph API by its ID."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.body-content-type="text"',
    }
    params = {
        "$select": (
            "id,subject,from,receivedDateTime,bodyPreview,body,"
            "hasAttachments,isRead,isDraft,importance,conversationId"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.GRAPH_API_BASE}/me/messages/{email_id}",
                headers=headers,
                params=params,
            )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


@router.get("/approval")
async def get_approval_emails(
    request: Request,
    preset: Optional[str] = Query(None, description="24h, 2d, 1w, 1m"),
    start_dt: Optional[str] = Query(None),
    end_dt: Optional[str] = Query(None),
    duration_value: Optional[int] = Query(None),
    duration_unit: Optional[str] = Query(None, description="hours, days, weeks, months"),
    queue: Optional[str] = Query(None, description="pending, approved, rejected, needs_info"),
):
    """
    Fetch approval emails.

    - pending queue:    fetch emails from Graph API within the selected time range.
    - approved / rejected / needs_info queues:
                        fetch ALL ever-actioned emails from the local DB regardless
                        of time range, then pull their details from Graph API.
                        This means once you approve an email it's always visible.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    user_id = get_session_user_email(session_id)
    VALID_QUEUES = {"pending", "approved", "rejected", "needs_info"}

    # ── Actioned queues (approved / rejected / needs_info) ────────────────────
    # Pull from DB (all-time), then hydrate from Graph API.
    if queue and queue in VALID_QUEUES and queue != "pending":
        db_records = tracking_store.get_all_actioned_email_ids(queue, user_id=user_id)
        result = []
        for record in db_records:
            raw = await _fetch_email_by_id(access_token, record["email_id"])
            if raw:
                attachments = []
                if raw.get("hasAttachments"):
                    attachments = await _fetch_attachments_meta(access_token, raw["id"])
                result.append(_format_email(raw, attachments, user_id=user_id))
            else:
                # Email may have been deleted/moved — build a minimal placeholder
                # so it still appears in the list with correct status
                result.append({
                    "id": record["email_id"],
                    "subject": "(Email no longer available)",
                    "sender": "—",
                    "senderEmail": "",
                    "receivedDateTime": record.get("received_at") or record.get("updated_at") or "",
                    "bodyPreview": "",
                    "body": "",
                    "hasAttachments": False,
                    "isRead": True,
                    "importance": "normal",
                    "conversationId": "",
                    "priority": "low",
                    "status": queue,
                    "attachments": [],
                })

        # Group by time of the original email (receivedDateTime)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start  = today_start - timedelta(days=now.weekday())
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

        result = _enrich_with_thread_counts(result)
        return {
            "emails": result,
            "grouped": grouped,
            "total": len(result),
            "filter_range": {"start": "", "end": ""},
        }

    # ── Pending queue (and default / no queue) — time-range filtered ──────────
    start_iso, end_iso = _build_time_filter(preset, start_dt, end_dt, duration_value, duration_unit)
    raw_emails = await _fetch_emails_from_graph(access_token, start_iso, end_iso)
    approval_emails = [e for e in raw_emails if _is_approval_email(e)]

    result = []
    for email in approval_emails:
        attachments = []
        if email.get("hasAttachments"):
            attachments = await _fetch_attachments_meta(access_token, email["id"])
        result.append(_format_email(email, attachments, user_id=user_id))

    # For pending queue filter out anything already actioned
    if queue == "pending":
        result = [e for e in result if e["status"] == "pending"]

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

    result = _enrich_with_thread_counts(result)
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


@router.get("/conversation/{conversation_id}")
async def get_conversation_emails(conversation_id: str, request: Request):
    """
    Return all emails that share a conversation_id, enriched with live Graph data.
    Used to render the clubbed thread view on the frontend.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)

    db_records = get_emails_by_conversation(conversation_id)

    messages = []
    for record in db_records:
        raw = await _fetch_email_by_id(access_token, record["email_id"])
        if raw:
            attachments = []
            if raw.get("hasAttachments"):
                attachments = await _fetch_attachments_meta(access_token, raw["id"])
            formatted = _format_email(raw, attachments)
            formatted["db_status"]  = record["status"]
            formatted["updated_at"] = record["updated_at"]
            messages.append(formatted)
        else:
            # Email deleted/moved — use DB metadata as fallback
            messages.append({
                "id":                record["email_id"],
                "subject":           "(Email no longer available)",
                "sender":            "—",
                "senderEmail":       "",
                "receivedDateTime":  record.get("received_at") or record.get("updated_at") or "",
                "bodyPreview":       "",
                "body":              "",
                "hasAttachments":    False,
                "conversationId":    conversation_id,
                "priority":          "low",
                "status":            record["status"],
                "db_status":         record["status"],
                "updated_at":        record["updated_at"],
                "attachments":       [],
            })

    # Determine overall thread state for the banner label
    statuses = [m["status"] for m in messages]
    if "approved" in statuses:
        thread_state = "approved"
    elif "rejected" in statuses:
        thread_state = "rejected"
    elif "needs_info" in statuses:
        all_replied = any(m.get("sender") not in ("—", "") and not m.get("senderEmail", "").endswith(
            request.cookies.get("session_id", "")  # rough check
        ) for m in messages[1:])
        thread_state = "waiting_reply" if not all_replied else "info_received"
    else:
        thread_state = "pending"

    return {
        "conversation_id": conversation_id,
        "messages":        messages,
        "thread_state":    thread_state,
        "count":           len(messages),
    }
