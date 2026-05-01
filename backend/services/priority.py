"""
Priority Service - Compute email priority based on content and attachments
"""

HIGH_PRIORITY_KEYWORDS = [
    "invoice", "contract", "agreement", "approval form",
    "urgent", "urgent approval", "asap", "immediate", "deadline today",
    "overdue", "time sensitive", "critical", "escalated",
]

MEDIUM_PRIORITY_KEYWORDS = [
    "approval", "approve", "review", "request", "pending",
    "action required", "decision", "authorize",
]

HIGH_ATTACHMENT_TYPES = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}


def compute_priority(email: dict, attachments: list) -> str:
    """
    Compute priority badge: 'high', 'medium', or 'low'.
    
    Rules:
    - HIGH if high-priority keywords OR high-value attachment types
    - MEDIUM if attachments present OR medium keywords
    - LOW otherwise
    """
    subject = (email.get("subject") or "").lower()
    body = (email.get("bodyPreview") or "").lower()
    importance = (email.get("importance") or "normal").lower()
    combined = subject + " " + body

    # Microsoft importance flag
    if importance == "high":
        return "high"

    # Check high priority keywords
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in combined:
            return "high"

    # Check attachment types
    for att in attachments:
        name = (att.get("name") or "").lower()
        for ext in HIGH_ATTACHMENT_TYPES:
            if name.endswith(ext):
                return "high"

    # Check medium keywords
    for kw in MEDIUM_PRIORITY_KEYWORDS:
        if kw in combined:
            return "medium"

    # Has attachments → medium
    if attachments or email.get("hasAttachments"):
        return "medium"

    return "low"
