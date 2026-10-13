"""Event webhooks for the external REST API (``/api/v1``).

Pattern learned from J1BON/BZ-Browser (MIT,
https://github.com/j1bon/bz-browser); implementation is original.

Events fired: ``profile.launched``, ``profile.stopped``, ``profile.failed``.

Delivery contract
-----------------
- JSON body: ``{"event": ..., "profile": ..., "at": <ISO-8601 UTC>, ...}``.
- Integrity: ``X-Webhook-Signature: sha256=<hex HMAC-SHA256>`` over the raw
  request body, keyed with the webhook's per-URL secret.
- Dispatch is non-blocking (a daemon thread per fire), with up to 3
  attempts using exponential backoff (1s, 2s, 4s). Failed deliveries are
  logged to stdout, never raised — a dead webhook must not take down a
  profile launch.
- stdlib only (``urllib``) so webhook delivery has no new dependencies.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.request

from src.api.keys import _api_db_path

# ---------------------------------------------------------------------------
# Event names
# ---------------------------------------------------------------------------

EVENT_PROFILE_LAUNCHED = "profile.launched"
EVENT_PROFILE_STOPPED = "profile.stopped"
EVENT_PROFILE_FAILED = "profile.failed"

EVENTS = (EVENT_PROFILE_LAUNCHED, EVENT_PROFILE_STOPPED, EVENT_PROFILE_FAILED)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.0
_DELIVERY_TIMEOUT_S = 10
SIGNATURE_HEADER = "X-Webhook-Signature"


def sign_payload(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` HMAC-SHA256 of ``body`` keyed by ``secret``."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookStore:
    """SQLite-backed webhook registration.

    Args:
        db_path: Path to the SQLite file. Defaults to the shared API db
            (``<ANTIDETECT_HOME>/.antidetect-browser/api.db``).
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _api_db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT '[]',
                secret TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _row_to_public(row) -> dict:
        try:
            events = json.loads(row["events"] or "[]")
        except (ValueError, TypeError):
            events = []
        return {
            "id": row["id"],
            "url": row["url"],
            "events": events,
            "created_at": row["created_at"],
            # The secret is never returned after registration; the test
            # endpoint uses it server-side for signing.
        }

    def register(self, url: str, events: list | tuple | None = None,
                 secret: str | None = None) -> dict:
        """Register a webhook URL for ``events``.

        :raises ValueError: on a bad URL or an unknown event name.
        """
        url = (url or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("webhook url must start with http:// or https://")
        events = list(events) if events else list(EVENTS)
        for event in events:
            if event not in EVENTS:
                raise ValueError(
                    "unknown event %r; choose from %s" % (event, list(EVENTS)))
        secret = secret or secrets.token_urlsafe(24)
        cur = self._conn.execute(
            "INSERT INTO webhooks (url, events, secret, created_at) "
            "VALUES (?, ?, ?, ?)",
            (url, json.dumps(events), secret, self._now()),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM webhooks WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return self._row_to_public(row)

    def unregister(self, webhook_id: int) -> bool:
        """Remove a webhook. Returns True when one was removed."""
        cur = self._conn.execute(
            "DELETE FROM webhooks WHERE id = ?", (int(webhook_id),))
        self._conn.commit()
        return cur.rowcount > 0

    def list(self) -> list:
        """All registered webhooks (secrets never included)."""
        rows = self._conn.execute(
            "SELECT * FROM webhooks ORDER BY id DESC").fetchall()
        return [self._row_to_public(r) for r in rows]

    def _with_secrets(self, event: str) -> list:
        """Internal: (url, secret) pairs subscribed to ``event``."""
        rows = self._conn.execute("SELECT url, events, secret FROM webhooks"
                                  ).fetchall()
        out = []
        for row in rows:
            try:
                events = json.loads(row["events"] or "[]")
            except (ValueError, TypeError):
                events = []
            if event in events:
                out.append((row["url"], row["secret"]))
        return out


# ---------------------------------------------------------------------------
# Delivery (stdlib urllib, non-blocking, retry w/ exponential backoff)
# ---------------------------------------------------------------------------

def _deliver(url: str, secret: str, payload: dict) -> bool:
    """POST ``payload`` to ``url``; 3 attempts, exponential backoff.

    Returns True on the first 2xx response. Never raises.
    """
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "antidetect-webhooks/1.0",
        SIGNATURE_HEADER: sign_payload(secret, body),
    }
    for attempt in range(_MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
            with urllib.request.urlopen(req,
                                        timeout=_DELIVERY_TIMEOUT_S) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception as exc:  # network errors, 4xx/5xx, timeouts
            if attempt == _MAX_ATTEMPTS - 1:
                print("[WEBHOOK] delivery to %s failed after %d attempts: %s"
                      % (url, _MAX_ATTEMPTS, exc))
            else:
                time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
    return False


def fire_event(store: WebhookStore, event: str, payload: dict,
               block: bool = False) -> threading.Thread:
    """Fire ``event`` to every subscribed webhook (daemon thread).

    :param block: when True, deliver synchronously (used by tests and the
        ``/test`` endpoint).
    :returns: the delivery thread.
    """
    body = {
        "event": event,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    body.update(payload or {})

    def _run():
        for url, secret in store._with_secrets(event):
            _deliver(url, secret, body)

    thread = threading.Thread(target=_run, daemon=True,
                              name="webhook-%s" % event)
    if block:
        _run()
    else:
        thread.start()
    return thread


# ---------------------------------------------------------------------------
# Process-wide dispatcher used by the ``on_profile_*`` hooks
# ---------------------------------------------------------------------------

_dispatcher: WebhookStore | None = None
_dispatcher_lock = threading.Lock()


def get_dispatcher(db_path: str | None = None) -> WebhookStore:
    """Process-wide :class:`WebhookStore` singleton (lazy, thread-safe)."""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None or db_path is not None:
            _dispatcher = WebhookStore(db_path)
        return _dispatcher


def dispatch(event: str, profile_name: str, extra: dict | None = None,
             block: bool = False) -> threading.Thread | None:
    """Fire ``event`` for ``profile_name`` through the shared dispatcher.

    Never raises — webhook delivery must not break the profile lifecycle.
    """
    try:
        store = get_dispatcher()
        payload = {"profile": profile_name}
        if extra:
            payload.update(extra)
        return fire_event(store, event, payload, block=block)
    except Exception as exc:
        print("[WEBHOOK] dispatch error for event %s: %s" % (event, exc))
        return None
