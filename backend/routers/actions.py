"""
Actions Router - Send replies (Approve / Reject / Request More Info)
Enhanced with:
- AI-polished HTML comments via OpenAI
- Thread trail history logging
- Persistent DB via SQLite
"""

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta

from backend.config import settings
from backend.routers.auth import get_valid_access_token
from backend.services.tracking import tracking_store
from backend.services.db import log_action, add_thread_entry, get_action_log

router = APIRouter()


class ActionRequest(BaseModel):
    email_id: str
    action: str           # "approve", "reject", "request_info"
    comment: str = ""
    email_subject: str = ""
    email_sender: str = ""
    email_body_preview: str = ""
    conversation_id: str = ""
    received_at: str = ""


STATUS_MAP = {
    "approve": "approved",
    "reject": "rejected",
    "request_info": "needs_info",
}

ACTION_LABELS = {
    "approve": "Approval Granted",
    "reject": "Request Rejected",
    "request_info": "Additional Information Required",
}


async def _enhance_comment_with_openai(
    action: str,
    comment: str,
    email_subject: str,
    email_sender: str,
) -> str:
    action_label = ACTION_LABELS.get(action, action.title())
    color = {"approve": "#16a34a", "reject": "#dc2626", "request_info": "#d97706"}.get(action, "#374151")

    system_prompt = f"""You are a professional business email writer.
Convert the user's raw comment into a polished, structured HTML email reply for an "{action_label}" action.
Rules:
- Respond ONLY with HTML content (no html/head/body tags)
- Use inline CSS only, professional formatting
- Show the decision clearly at the top in a colored banner
- Keep it concise and respectful
- For "request_info": clearly list what information is needed
- For "approve": confirm approval with any conditions
- For "reject": state reason diplomatically
- Do NOT include markdown or code fences"""

    user_prompt = f"""Email Subject: {email_subject}
Recipient: {email_sender}
Action: {action_label}
My raw comment: {comment or '(no additional comment)'}
Generate a professional HTML email reply."""

    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 800,
        "temperature": 0.4,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
        if resp.status_code == 200:
            html_body = resp.json()["choices"][0]["message"]["content"].strip()
            if html_body.startswith("```"):
                html_body = html_body.split("```")[1]
                if html_body.lower().startswith("html"):
                    html_body = html_body[4:]
                html_body = html_body.strip()
            return html_body
    except Exception:
        pass

    # Fallback
    return f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <div style="border-left:4px solid {color};padding:12px 16px;background:#f9fafb;margin-bottom:16px;">
    <h2 style="margin:0;color:{color};font-size:18px;">{action_label}</h2>
  </div>
  <p style="color:#374151;line-height:1.6;">{comment or 'Please see the action above for details.'}</p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;"/>
  <p style="color:#9ca3af;font-size:12px;">Sent via Approval Email Assistant.</p>
</div>"""


async def _send_reply(access_token: str, email_id: str, html_body: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "message": {
            "body": {"contentType": "HTML", "content": html_body}
        },
        "comment": "",
    }
    url = f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/reply"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code not in (200, 202):
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Failed to send reply: {resp.text}",
            )


async def _fetch_conversation_thread(access_token: str, conversation_id: str) -> list:
    """
    Fetch ALL messages in a conversation across the entire mailbox.
    Uses /me/messages (all-folders search) first, then falls back to
    inbox + sentItems explicitly to catch any missed messages.
    De-duplicates by message ID.
    """
    if not conversation_id:
        return []

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.body-content-type="text"',
    }
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$orderby": "receivedDateTime asc",
        "$top": "50",
        "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,conversationId,isDraft",
    }

    seen_ids: set = set()
    messages: list = []

    # Primary: search entire mailbox at once (finds inbox + sent + replied emails)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me/messages",
            headers=headers,
            params=params,
        )
    if resp.status_code == 200:
        for m in resp.json().get("value", []):
            if m["id"] not in seen_ids and not m.get("isDraft"):
                seen_ids.add(m["id"])
                messages.append(m)

    # Fallback: also check inbox + sentItems explicitly to catch any missed
    for folder in ["inbox", "sentItems"]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp2 = await client.get(
                f"{settings.GRAPH_API_BASE}/me/mailFolders/{folder}/messages",
                headers=headers,
                params=params,
            )
        if resp2.status_code == 200:
            for m in resp2.json().get("value", []):
                if m["id"] not in seen_ids and not m.get("isDraft"):
                    seen_ids.add(m["id"])
                    messages.append(m)

    # Sort ascending by time so thread reads top-to-bottom chronologically
    messages.sort(key=lambda x: x.get("receivedDateTime", ""))
    return messages


@router.post("/")
async def perform_action(action_req: ActionRequest, request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if action_req.action not in STATUS_MAP:
        raise HTTPException(status_code=400, detail="Invalid action")

    access_token = await get_valid_access_token(session_id)

    # 1. AI-enhanced HTML reply
    enhanced_html = await _enhance_comment_with_openai(
        action=action_req.action,
        comment=action_req.comment,
        email_subject=action_req.email_subject,
        email_sender=action_req.email_sender,
    )

    # 2. Send reply
    await _send_reply(access_token, action_req.email_id, enhanced_html)

    # 3. Persist status — pass received_at so the DB knows WHEN the email
    #    originally arrived (used by stats: pending counts by arrival time,
    #    approved/rejected/needs_info count by when the decision was made).
    new_status = STATUS_MAP[action_req.action]
    tracking_store.set_status(
        action_req.email_id, new_status,
        conversation_id=action_req.conversation_id or None,
        received_at=action_req.received_at or None,
    )

    # 4. Log action
    log_action(
        email_id=action_req.email_id,
        action=action_req.action,
        original_comment=action_req.comment,
        enhanced_html=enhanced_html,
    )

    # 5. Add trail entry
    if action_req.conversation_id:
        add_thread_entry(
            email_id=action_req.email_id,
            conversation_id=action_req.conversation_id,
            message_type="reply",
            sender="You",
            sender_email="",
            subject=f"Re: {action_req.email_subject}",
            body_preview=action_req.comment or ACTION_LABELS[action_req.action],
            received_at=datetime.now(timezone.utc).isoformat(),
            is_our_reply=True,
            action_type=action_req.action,
            enhanced_html=enhanced_html,
        )

    return {
        "success": True,
        "action": action_req.action,
        "status": new_status,
        "message": f"Email {new_status} successfully.",
        "enhanced_html": enhanced_html,
    }


@router.get("/stats")
async def get_action_stats(
    request: Request,
    preset: Optional[str] = Query(None, description="24h, 2d, 1w, 1m"),
    start_dt: Optional[str] = Query(None),
    end_dt: Optional[str] = Query(None),
    duration_value: Optional[int] = Query(None),
    duration_unit: Optional[str] = Query(None, description="hours, days, weeks, months"),
):
    """
    Return accurate queue counts by fetching live approval emails from Graph API
    and resolving each one's status from the DB (defaulting to 'pending' for
    untracked emails). Accepts the same time-filter params as the emails endpoint
    so the stat card numbers always match the currently selected time range.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Import here to avoid circular imports
    from routers.emails import (
        _fetch_emails_from_graph,
        _is_approval_email,
        _build_time_filter,
    )
    from routers.auth import get_valid_access_token

    # If no filter param provided at all, default to "24h" (matches default UI state)
    has_any_filter = any([preset, start_dt, end_dt, duration_value, duration_unit])
    effective_preset = preset if has_any_filter else "24h"

    try:
        access_token = await get_valid_access_token(session_id)
        start_iso, end_iso = _build_time_filter(
            preset=effective_preset,
            start_dt=start_dt,
            end_dt=end_dt,
            duration_value=duration_value,
            duration_unit=duration_unit,
        )
        raw_emails = await _fetch_emails_from_graph(access_token, start_iso, end_iso)
        approval_emails = [e for e in raw_emails if _is_approval_email(e)]

        # Seed received_at for every email we see from Graph API so the DB
        # can correctly count pending-by-arrival and actioned-by-decision-time.
        # set_status with status="pending" will only INSERT if not already tracked
        # (ON CONFLICT preserves existing status and never overwrites received_at once set).
        for email in approval_emails:
            existing = tracking_store.get_status(email["id"])
            if existing == "pending":
                # Upsert with pending — this seeds received_at without overwriting
                # any existing approved/rejected/needs_info status.
                tracking_store.set_status(
                    email["id"],
                    "pending",
                    received_at=email.get("receivedDateTime"),
                )

        # Now query the DB with the correct dual logic:
        #   pending   → received_at in range, still pending
        #   approved/rejected/needs_info → updated_at (decision time) in range
        stats = tracking_store.get_stats_for_period(start_iso, end_iso)

        return {
            "total_tracked": stats["total_tracked"],
            "filter_range": {"start": start_iso, "end": end_iso},
            "pending":    stats["pending"],
            "approved":   stats["approved"],
            "rejected":   stats["rejected"],
            "needs_info": stats["needs_info"],
        }

    except Exception:
        # Fallback to DB-only counts if Graph API fails (e.g., token expired)
        try:
            start_iso, end_iso = _build_time_filter(
                preset=effective_preset,
                start_dt=start_dt,
                end_dt=end_dt,
                duration_value=duration_value,
                duration_unit=duration_unit,
            )
            return tracking_store.get_stats_for_period(start_iso, end_iso)
        except Exception:
            return tracking_store.get_stats()


@router.get("/thread/{email_id}")
async def get_email_thread(email_id: str, request: Request):
    """
    Fetch the full conversation thread for an email.
    - Pulls ALL messages in the conversation across the entire mailbox
    - Tags each message as: original | our_reply (with action) | their_reply_back
    - Action log entries from our DB are merged in so the AI-enhanced HTML is shown
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)
    headers_base = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.body-content-type="text"',
    }

    # 1. Get the original email metadata
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.GRAPH_API_BASE}/me/messages/{email_id}",
            headers=headers_base,
            params={"$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Email not found")

    email_data = resp.json()
    conversation_id = email_data.get("conversationId", "")
    original_subject = email_data.get("subject", "")
    original_sender_email = email_data.get("from", {}).get("emailAddress", {}).get("address", "").lower()

    # 2. Get current user email so we can mark our own messages
    my_email = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            me_resp = await client.get(
                f"{settings.GRAPH_API_BASE}/me",
                headers=headers_base,
                params={"$select": "mail,userPrincipalName"},
            )
        if me_resp.status_code == 200:
            me_data = me_resp.json()
            my_email = (me_data.get("mail") or me_data.get("userPrincipalName") or "").lower()
    except Exception:
        pass

    # 3. Fetch all messages in this conversation (entire mailbox)
    live_thread = await _fetch_conversation_thread(access_token, conversation_id)

    # 4. Get our local action log (stores AI-enhanced HTML of replies we sent)
    action_log = get_action_log(email_id)
    # Index action_log by approximate time for matching
    action_log_list = list(action_log)

    # 5. Build formatted thread
    formatted_thread = []
    action_log_idx = 0  # pointer into action_log_list for matching sent messages

    for msg in live_thread:
        from_name  = msg.get("from", {}).get("emailAddress", {}).get("name", "Unknown")
        from_email = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()

        is_original = msg["id"] == email_id
        # A message is "ours" if it was sent from our account
        is_ours = (my_email and from_email == my_email)

        # Try to find a matching action_log entry for our sent messages
        matched_action = None
        if is_ours and action_log_idx < len(action_log_list):
            matched_action = action_log_list[action_log_idx]
            action_log_idx += 1

        # Determine message role for UI display
        if is_original:
            msg_role = "original"       # The approval request that came in
        elif is_ours:
            msg_role = "our_reply"      # Our approve/reject/request-info reply
        else:
            msg_role = "their_response" # Sender replied back to our request!

        formatted_thread.append({
            "message_id":   msg["id"],
            "is_original":  is_original,
            "is_our_reply": is_ours,
            "msg_role":     msg_role,
            "sender":       "You" if is_ours else from_name,
            "sender_email": from_email,
            "subject":      msg.get("subject", ""),
            "body_preview": msg.get("bodyPreview", ""),
            "received_at":  msg.get("receivedDateTime", ""),
            "action_type":  matched_action["action"] if matched_action else None,
            "enhanced_html": matched_action["enhanced_html"] if matched_action else None,
        })

    # 6. If any action_log entries weren't matched to a live Graph message
    #    (e.g. sent items not yet synced), add them manually
    for al in action_log_list[action_log_idx:]:
        formatted_thread.append({
            "message_id":   f"action_{al['id']}",
            "is_original":  False,
            "is_our_reply": True,
            "msg_role":     "our_reply",
            "sender":       "You",
            "sender_email": my_email,
            "subject":      f"Re: {original_subject}",
            "body_preview": al["original_comment"] or ACTION_LABELS.get(al["action"], al["action"]),
            "received_at":  al["created_at"],
            "action_type":  al["action"],
            "enhanced_html": al["enhanced_html"],
        })

    # 7. Final sort by time
    formatted_thread.sort(key=lambda x: x.get("received_at") or "")

    return {
        "email_id":        email_id,
        "conversation_id": conversation_id,
        "my_email":        my_email,
        "thread":          formatted_thread,
        "action_log":      action_log,
    }
