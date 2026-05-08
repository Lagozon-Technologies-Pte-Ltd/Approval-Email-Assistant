"""
Tracking Store - now backed by SQLite (persistent across restarts).
Keeps the same interface that actions.py / emails.py expect.
"""

from backend.services.db import (
    get_status,
    set_status,
    get_stats_for_period,
    get_all_actioned_email_ids,
)


class TrackingStore:
    """Thin wrapper so existing code needs no changes."""

    def get_status(self, email_id: str) -> str:
        return get_status(email_id)

    def set_status(self, email_id: str, status: str, conversation_id: str = None, received_at: str = None):
        set_status(email_id, status, conversation_id, received_at)

    def get_stats(self) -> dict:
        return get_stats_for_period()

    def get_stats_for_period(self, start_iso=None, end_iso=None) -> dict:
        return get_stats_for_period(start_iso, end_iso)

    def get_all_actioned_email_ids(self, status: str) -> list:
        return get_all_actioned_email_ids(status)


# Singleton
tracking_store = TrackingStore()
