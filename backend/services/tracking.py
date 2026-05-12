"""
Tracking Store - now backed by SQLite (persistent across restarts).
Keeps the same interface that actions.py / emails.py expect.
All methods now accept an optional user_id for per-user isolation.
"""

from backend.services.db import (
    get_status,
    set_status,
    get_stats_for_period,
    get_all_actioned_email_ids,
)


class TrackingStore:
    """Thin wrapper so existing code needs no changes."""

    def get_status(self, email_id: str, user_id: str = "") -> str:
        return get_status(email_id, user_id)

    def set_status(self, email_id: str, status: str, user_id: str = "", conversation_id: str = None, received_at: str = None):
        set_status(email_id, status, user_id, conversation_id, received_at)

    def get_stats(self, user_id: str = "") -> dict:
        return get_stats_for_period(user_id=user_id)

    def get_stats_for_period(self, start_iso=None, end_iso=None, user_id: str = "") -> dict:
        return get_stats_for_period(start_iso, end_iso, user_id)

    def get_all_actioned_email_ids(self, status: str, user_id: str = "") -> list:
        return get_all_actioned_email_ids(status, user_id)


# Singleton
tracking_store = TrackingStore()
