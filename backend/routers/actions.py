"""
Actions Router - Send replies (Approve / Reject / Request More Info)
"""

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import settings
from routers.auth import get_valid_access_token
from services.tracking import tracking_store

router = APIRouter()


class ActionRequest(BaseModel):
    email_id: str
    action: str  # "approve", "reject", "request_info"
    comment: str = ""


REPLY_TEMPLATES = {
    "approve": "Approved.\n\nThis request has been reviewed and approved.\n\n{comment}",
    "reject": "Rejected.\n\nThis request has been reviewed and rejected.\n\nReason: {comment}",
    "request_info": "Additional information required.\n\nPlease provide more details:\n{comment}\n\nThank you.",
}

STATUS_MAP = {
    "approve": "approved",
    "reject": "rejected",
    "request_info": "pending",
}


async def _send_reply(access_token: str, email_id: str, reply_body: str):
    """Send a reply to an email using Microsoft Graph API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
    "comment": reply_body
}  
    url = f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/reply"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code not in (200, 202):
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Failed to send reply: {resp.text}",
            )


@router.post("/")
async def perform_action(action_req: ActionRequest, request: Request):
    """Perform an approve/reject/request_info action on an email."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if action_req.action not in REPLY_TEMPLATES:
        raise HTTPException(status_code=400, detail="Invalid action")

    access_token = await get_valid_access_token(session_id)

    template = REPLY_TEMPLATES[action_req.action]
    reply_body = template.format(comment=action_req.comment or "")

    await _send_reply(access_token, action_req.email_id, reply_body)

    # Update tracking
    new_status = STATUS_MAP[action_req.action]
    tracking_store.set_status(action_req.email_id, new_status)

    return {
        "success": True,
        "action": action_req.action,
        "status": new_status,
        "message": f"Email {new_status} successfully.",
    }


@router.get("/stats")
async def get_action_stats(request: Request):
    """Return approval tracking statistics."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stats = tracking_store.get_stats()
    return stats
