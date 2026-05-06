"""
Tracking Store - now backed by SQLite (persistent across restarts).
Keeps the same interface that actions.py / emails.py expect.
"""

from backend.services.db import (
    get_status,
    set_status,
    get_stats_for_period,
)


class TrackingStore:
    """Thin wrapper so existing code needs no changes."""

    def get_status(self, email_id: str) -> str:
        return get_status(email_id)

    def set_status(self, email_id: str, status: str, conversation_id: str = None):
        set_status(email_id, status, conversation_id)

    def get_stats(self) -> dict:
        return get_stats_for_period()

    def get_stats_for_period(self, start_iso=None, end_iso=None) -> dict:
        return get_stats_for_period(start_iso, end_iso)


# Singleton
tracking_store = TrackingStore()
