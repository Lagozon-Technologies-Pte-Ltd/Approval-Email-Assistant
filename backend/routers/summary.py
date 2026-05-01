"""
AI Summary Router - OpenAI-powered email + attachment summarization
"""

import base64
import io

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import settings
from routers.auth import get_valid_access_token
from services.attachment_parser import parse_attachment_content

router = APIRouter()


class SummaryRequest(BaseModel):
    email_id: str
    email_subject: str = ""
    email_body: str = ""
    email_sender: str = ""


async def _call_openai(messages: list[dict], max_tokens: int = 800) -> str:
    """Call OpenAI chat completion."""
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.OPENAI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"OpenAI API error: {resp.text}",
            )
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _fetch_attachment_for_summary(
    access_token: str, email_id: str, attachment_meta: dict
) -> str:
    """Fetch and parse an attachment, returning extracted text."""
    att_id = attachment_meta["id"]
    filename = attachment_meta.get("name", "")
    content_type = attachment_meta.get("contentType", "")

    # Get full attachment with content
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{settings.GRAPH_API_BASE}/me/messages/{email_id}/attachments/{att_id}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return ""
        att_data = resp.json()

    # Graph returns base64-encoded content
    b64_content = att_data.get("contentBytes", "")
    if not b64_content:
        return ""

    raw_bytes = base64.b64decode(b64_content)
    return parse_attachment_content(raw_bytes, filename, content_type)


GRAPH_API_BASE = settings.GRAPH_API_BASE


async def _get_attachments_text(access_token: str, email_id: str) -> list[dict]:
    """Fetch and parse all attachments for an email."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GRAPH_API_BASE}/me/messages/{email_id}/attachments"
    params = {"$select": "id,name,contentType,size"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            return []
        attachments = resp.json().get("value", [])

    results = []
    for att in attachments:
        if att.get("size", 0) > 10_000_000:  # Skip files > 10MB
            continue
        text = await _fetch_attachment_for_summary(access_token, email_id, att)
        results.append({"name": att.get("name", ""), "text": text[:3000]})  # Limit to 3k chars

    return results


@router.post("/email")
async def summarize_email(req: SummaryRequest, request: Request):
    """Generate AI summary for an email including attachments."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = await get_valid_access_token(session_id)

    # Fetch attachments
    attachments_text = await _get_attachments_text(access_token, req.email_id)

    # Build context
    att_section = ""
    if attachments_text:
        att_parts = []
        for att in attachments_text:
            if att["text"]:
                att_parts.append(f"--- Attachment: {att['name']} ---\n{att['text']}")
        att_section = "\n\n".join(att_parts)

    system_prompt = """You are an expert executive assistant that analyzes approval-related emails.
Your job is to provide concise, actionable analysis to help managers make quick decisions.
Always respond in this exact JSON format (no markdown, pure JSON):
{
  "email_summary": "2-3 sentence summary of the email",
  "key_decision_points": ["point 1", "point 2", "point 3"],
  "suggested_action": "Approve" or "Reject" or "Review",
  "smart_reply": "a professional one-liner reply suggestion"
}"""

    user_content = f"""Email Subject: {req.email_subject}
From: {req.email_sender}

Email Body:
{req.email_body[:3000]}

{f'Attached Documents:{chr(10)}{att_section}' if att_section else ''}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    raw_output = await _call_openai(messages, max_tokens=1000)

    # Parse JSON response
    import json
    try:
        # Strip possible markdown code fences
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        summary_data = json.loads(cleaned)
    except Exception:
        summary_data = {
            "email_summary": raw_output,
            "document_summary": None,
            "combined_insight": "Unable to parse structured response.",
            "key_decision_points": [],
            "suggested_action": "Review",
            "suggested_action_reason": "Manual review recommended",
            "urgency": "Medium",
            "smart_reply": "Thank you for your email. I will review this shortly.",
        }

    return summary_data


@router.post("/batch")
async def summarize_batch(email_ids: list[str], request: Request):
    """Summarize multiple emails (lightweight, body only)."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Limit batch
    if len(email_ids) > 10:
        raise HTTPException(status_code=400, detail="Max 10 emails per batch")

    access_token = await get_valid_access_token(session_id)
    results = {}

    for email_id in email_ids:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.body-content-type="text"',
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me/messages/{email_id}",
                headers=headers,
                params={"$select": "subject,from,bodyPreview"},
            )
            if resp.status_code != 200:
                continue
            email = resp.json()

        summary_req = SummaryRequest(
            email_id=email_id,
            email_subject=email.get("subject", ""),
            email_body=email.get("bodyPreview", ""),
            email_sender=email.get("from", {}).get("emailAddress", {}).get("address", ""),
        )
        # Pass a fake request object—workaround since we already have access_token
        try:
            result = await summarize_email(summary_req, request)
            results[email_id] = result
        except Exception as e:
            results[email_id] = {"error": str(e)}

    return results
