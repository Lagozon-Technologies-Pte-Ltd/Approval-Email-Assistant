"""
Tracking Store - Maintains email approval status
(In-memory; replace with Redis or DB for production)
"""

from datetime import datetime
from threading import Lock


class TrackingStore:
    def __init__(self):
        self._store: dict[str, dict] = {}
        self._lock = Lock()

    def get_status(self, email_id: str) -> str:
        with self._lock:
            entry = self._store.get(email_id)
            return entry["status"] if entry else "pending"

    def set_status(self, email_id: str, status: str):
        with self._lock:
            self._store[email_id] = {
                "status": status,
                "updated_at": datetime.utcnow().isoformat(),
            }

    def get_stats(self) -> dict:
        with self._lock:
            total = len(self._store)
            approved = sum(1 for v in self._store.values() if v["status"] == "approved")
            rejected = sum(1 for v in self._store.values() if v["status"] == "rejected")
            pending = sum(1 for v in self._store.values() if v["status"] == "pending")
            return {
                "total_tracked": total,
                "approved": approved,
                "rejected": rejected,
                "pending": pending,
            }

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._store)


# Singleton
tracking_store = TrackingStore()
