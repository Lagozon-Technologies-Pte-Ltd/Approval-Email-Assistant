"""
Persistent SQLite Database Service
Replaces in-memory tracking store with persistent storage.
Handles: email statuses, action history, thread/trail comments
"""

import sqlite3
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "approval_data.db")

_lock = Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist, and migrate schema safely."""
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_status (
                email_id        TEXT NOT NULL,
                user_id         TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'pending',
                updated_at      TEXT NOT NULL,
                conversation_id TEXT,
                received_at     TEXT,
                PRIMARY KEY (email_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS action_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id        TEXT NOT NULL,
                action          TEXT NOT NULL,
                original_comment TEXT,
                enhanced_html   TEXT,
                actor           TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY(email_id) REFERENCES email_status(email_id)
            );

            CREATE TABLE IF NOT EXISTS thread_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id        TEXT NOT NULL,
                conversation_id TEXT,
                message_type    TEXT NOT NULL,
                sender          TEXT,
                sender_email    TEXT,
                subject         TEXT,
                body_preview    TEXT,
                received_at     TEXT,
                is_our_reply    INTEGER DEFAULT 0,
                action_type     TEXT,
                enhanced_html   TEXT,
                created_at      TEXT NOT NULL
            );
        """)
        conn.commit()

        # Safe migration: add received_at to email_status if upgrading from old schema
        try:
            conn.execute("ALTER TABLE email_status ADD COLUMN received_at TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists — no-op

        try:
            conn.execute("ALTER TABLE email_status ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:
            pass  # Column already exists — no-op

        conn.close()


# ── Email Status ──────────────────────────────────────────────────────────────

def get_status(email_id: str, user_id: str = "") -> str:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT status FROM email_status WHERE email_id = ? AND user_id = ?",
            (email_id, user_id.lower())
        ).fetchone()
        conn.close()
        return row["status"] if row else "pending"


def set_status(email_id: str, status: str, user_id: str = "", conversation_id: str = None, received_at: str = None):
    """
    Persist the status for an email, scoped to a specific user.
    - user_id:    the logged-in user's email — ensures counts are per-user.
    - received_at: the email's original arrival time (ISO string from Graph API).
    - updated_at:  when this action was taken (now).
    """
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO email_status (email_id, user_id, status, updated_at, conversation_id, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(email_id, user_id) DO UPDATE SET
                status          = excluded.status,
                updated_at      = excluded.updated_at,
                conversation_id = COALESCE(excluded.conversation_id, conversation_id),
                received_at     = COALESCE(email_status.received_at, excluded.received_at)
        """, (email_id, user_id.lower(), status, now, conversation_id, received_at))
        conn.commit()
        conn.close()


def get_stats_for_period(start_iso: Optional[str] = None, end_iso: Optional[str] = None, user_id: str = "") -> dict:
    """
    Mixed-time stats — each status type uses the rule that makes sense for it:

      - pending:               emails received within the time window that are still pending.
                               Time-range sensitive: only shows what arrived in the selected period.
                               (filtered by received_at)

      - approved / rejected /
        needs_info:            ALL actioned emails ever, regardless of time range.
                               Once you approve/reject something it always shows in the count.
                               (no time filter applied)
    """
    with _lock:
        conn = _get_conn()

        # Pending: only within the selected time window (by arrival time)
        uid = user_id.lower()
        if start_iso and end_iso:
            pending_row = conn.execute("""
                SELECT COUNT(*) as cnt FROM email_status
                WHERE status = 'pending' AND user_id = ?
                AND received_at >= ? AND received_at <= ?
            """, (uid, start_iso, end_iso)).fetchone()
            pending_count = pending_row["cnt"] if pending_row else 0
        else:
            pending_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM email_status WHERE status = 'pending' AND user_id = ?",
                (uid,)
            ).fetchone()
            pending_count = pending_row["cnt"] if pending_row else 0

        # Approved / Rejected / Needs Info: ALL TIME — no time filter
        actioned_rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM email_status
            WHERE status != 'pending' AND user_id = ?
            GROUP BY status
        """, (uid,)).fetchall()

        conn.close()

    counts = {"approved": 0, "rejected": 0, "needs_info": 0}
    for row in actioned_rows:
        if row["status"] in counts:
            counts[row["status"]] = row["cnt"]

    total = pending_count + sum(counts.values())
    return {
        "total_tracked": total,
        "pending":    pending_count,
        "approved":   counts["approved"],
        "rejected":   counts["rejected"],
        "needs_info": counts["needs_info"],
    }



def get_all_actioned_email_ids(status: str, user_id: str = "") -> list:
    """
    Return ALL email_ids that have ever been set to a given status for a specific user.
    """
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT email_id, received_at, updated_at FROM email_status WHERE status = ? AND user_id = ? ORDER BY updated_at DESC",
            (status, user_id.lower())
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]

# ── Action Log ────────────────────────────────────────────────────────────────

def log_action(
    email_id: str,
    action: str,
    original_comment: str,
    enhanced_html: str,
    actor: str = None,
):
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO action_log (email_id, action, original_comment, enhanced_html, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (email_id, action, original_comment, enhanced_html, actor, now))
        conn.commit()
        conn.close()


def get_action_log(email_id: str) -> list:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM action_log WHERE email_id = ? ORDER BY created_at ASC",
            (email_id,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


# ── Thread History ────────────────────────────────────────────────────────────

def add_thread_entry(
    email_id: str,
    conversation_id: str,
    message_type: str,
    sender: str,
    sender_email: str,
    subject: str,
    body_preview: str,
    received_at: str,
    is_our_reply: bool = False,
    action_type: str = None,
    enhanced_html: str = None,
):
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO thread_history
            (email_id, conversation_id, message_type, sender, sender_email,
             subject, body_preview, received_at, is_our_reply, action_type, enhanced_html, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email_id, conversation_id, message_type, sender, sender_email,
            subject, body_preview, received_at,
            1 if is_our_reply else 0,
            action_type, enhanced_html, now
        ))
        conn.commit()
        conn.close()


def get_thread_history(email_id: str) -> list:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM thread_history WHERE email_id = ? ORDER BY received_at ASC",
            (email_id,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_thread_by_conversation(conversation_id: str) -> list:
    """Get all thread entries for a conversation (across email IDs)."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM thread_history WHERE conversation_id = ? ORDER BY received_at ASC",
            (conversation_id,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


# Initialise on import
init_db()


def get_emails_by_conversation(conversation_id: str) -> list:
    """Return all email_status rows sharing the same conversation_id, ordered oldest first."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT email_id, status, updated_at, received_at
               FROM email_status
               WHERE conversation_id = ?
               ORDER BY COALESCE(received_at, updated_at) ASC""",
            (conversation_id,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]
