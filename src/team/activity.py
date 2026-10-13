"""Activity log for team visibility.

Records important dashboard actions (profile create/delete/launch,
proxy changes, etc.) in a SQLite table. The UI shows recent activity
per user.
"""

import sqlite3
import threading
import time

from src import paths as _paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(ts DESC);
"""


class ActivityLog:
    """Thread-safe activity log backed by SQLite."""

    def __init__(self, db_path=None):
        self._db = db_path or str(_paths._under("activity.db"))
        self._lock = threading.Lock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def record(self, username, action, target=None, detail=None):
        """Record an activity entry. Never raises."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO activity_log (ts, username, action, target, detail)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (time.time(), username, action, target, detail),
                )
                conn.commit()
        except Exception:
            pass  # logging must never break the main flow

    def list(self, limit=100, username=None):
        """Return recent entries, newest first."""
        with self._lock, self._connect() as conn:
            if username:
                rows = conn.execute(
                    "SELECT * FROM activity_log WHERE username = ?"
                    " ORDER BY ts DESC LIMIT ?",
                    (username, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM activity_log ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def clear(self):
        """Delete all entries."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM activity_log")
            conn.commit()
