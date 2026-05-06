"""
Persistent SQLite Database Service
Replaces in-memory tracking store with persistent storage.
Handles: email statuses, action history, thread/trail comments
"""

import sqlite3
import json
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
    """Create tables if they don't exist."""
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_status (
                email_id    TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'pending',
                updated_at  TEXT NOT NULL,
                conversation_id TEXT
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
        conn.close()


# ── Email Status ──────────────────────────────────────────────────────────────

def get_status(email_id: str) -> str:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT status FROM email_status WHERE email_id = ?", (email_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else "pending"


def set_status(email_id: str, status: str, conversation_id: str = None):
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO email_status (email_id, status, updated_at, conversation_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at,
                conversation_id = COALESCE(excluded.conversation_id, conversation_id)
        """, (email_id, status, now, conversation_id))
        conn.commit()
        conn.close()


def get_stats_for_period(start_iso: Optional[str] = None, end_iso: Optional[str] = None) -> dict:
    """Return counts filtered to emails updated within a time window."""
    with _lock:
        conn = _get_conn()
        if start_iso and end_iso:
            rows = conn.execute(
                "SELECT status FROM email_status WHERE updated_at >= ? AND updated_at <= ?",
                (start_iso, end_iso)
            ).fetchall()
        else:
            rows = conn.execute("SELECT status FROM email_status").fetchall()
        conn.close()

    statuses = [r["status"] for r in rows]
    return {
        "total_tracked": len(statuses),
        "approved": statuses.count("approved"),
        "rejected": statuses.count("rejected"),
        "pending": statuses.count("pending"),
        "needs_info": statuses.count("needs_info"),
    }


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
    message_type: str,     # "original", "reply", "follow_up_response"
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
