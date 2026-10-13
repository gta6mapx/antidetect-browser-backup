"""API keys for the external REST API (``/api/v1``).

Pattern learned from J1BON/BZ-Browser (MIT,
https://github.com/j1bon/bz-browser); implementation is original.

Design
------
- Keys are ``sk_<43-char-urlsafe>`` bearer tokens. The raw token is returned
  **once**, at issuance, and is never persisted or logged afterwards.
- Only the SHA-256 hash of the token is stored, in the ``api_keys`` SQLite
  table next to the users database (honors ``ANTIDETECT_HOME`` via
  ``src.paths``).
- Scope model: ``profiles:read`` (GET), ``profiles:write`` (create),
  ``profiles:launch`` (launch/stop). ``profiles:write`` implies
  ``profiles:read``; ``admin`` implies everything and is reserved for key
  issuance/rotation management.
- Verification is constant-time (``hmac.compare_digest``) to avoid leaking
  key identity through timing.

Admin-only: :func:`issue_api_key` refuses callers whose role is not
``admin`` — the dashboard's RBAC (``src/security/users.py``) is the source
of truth; the v1 router's key-management endpoints additionally check
``request.state.user["role"] == "admin"`` for the session-cookie path.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from src import paths as _paths

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

SCOPE_READ = "profiles:read"
SCOPE_WRITE = "profiles:write"
SCOPE_LAUNCH = "profiles:launch"
SCOPE_ADMIN = "admin"

#: Scopes a dashboard admin may grant to a key (``admin`` itself is never
#: granted to external keys — it exists so the router can distinguish).
GRANTABLE_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_LAUNCH)

#: Scope implication map: holding the key implies every value scope.
_SCOPE_IMPLICATIONS = {
    SCOPE_WRITE: {SCOPE_READ},
    SCOPE_ADMIN: {SCOPE_READ, SCOPE_WRITE, SCOPE_LAUNCH},
}


def has_scope(granted_scopes, required: str) -> bool:
    """Return True when ``granted_scopes`` satisfy ``required``.

    ``profiles:write`` implies ``profiles:read``; ``admin`` implies all.
    """
    granted = set(granted_scopes or [])
    if required in granted:
        return True
    for scope in granted:
        if required in _SCOPE_IMPLICATIONS.get(scope, ()):
            return True
    return False


def _api_db_path() -> str:
    """SQLite file for API keys + webhooks, honoring ANTIDETECT_HOME."""
    return os.path.join(_paths.data_dir(), "api.db")


_TOKEN_PREFIX = "sk_"
_TOKEN_BYTES = 32  # -> 43 urlsafe chars
_PREFIX_CHARS = 8  # chars of raw token shown as the visible key prefix


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw token (what we persist)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


class ApiKeyStore:
    """SQLite-backed API key store (hashed tokens only).

    Args:
        db_path: Path to the SQLite file. Defaults to
            ``<ANTIDETECT_HOME>/.antidetect-browser/api.db``.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _api_db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                prefix TEXT NOT NULL,
                scopes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    # -- helpers ------------------------------------------------------
    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _row_to_public(row) -> dict:
        """Public key dict (never includes the hash)."""
        import json

        try:
            scopes = json.loads(row["scopes"] or "[]")
        except (ValueError, TypeError):
            scopes = []
        return {
            "id": row["id"],
            "name": row["name"],
            "prefix": row["prefix"],
            "scopes": scopes,
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
            "revoked": bool(row["revoked"]),
        }

    def _row_to_record(self, row) -> dict:
        record = self._row_to_public(row)
        record["key_hash"] = row["key_hash"]
        return record

    # -- issuance -----------------------------------------------------
    def issue(self, name: str, scopes: list | tuple = (SCOPE_READ,)) -> tuple:
        """Create a key and return ``(raw_token, public_record)``.

        The raw token is returned **only here**; it is never stored or
        logged. Callers must show it to the user exactly once.

        :raises ValueError: on empty name, duplicate name, or a scope that
            is not in :data:`GRANTABLE_SCOPES`.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("key name must be a non-empty string")
        scopes = list(scopes or [])
        for scope in scopes:
            if scope not in GRANTABLE_SCOPES:
                raise ValueError(
                    "scope %r is not grantable; choose from %s"
                    % (scope, list(GRANTABLE_SCOPES))
                )
        token = _new_token()
        key_hash = _hash_token(token)
        prefix = _TOKEN_PREFIX + token[len(_TOKEN_PREFIX):
                                        len(_TOKEN_PREFIX) + _PREFIX_CHARS]
        import json

        try:
            cur = self._conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (name, key_hash, prefix, json.dumps(scopes), self._now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "API key name %r is already taken" % name) from exc
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM api_keys WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return token, self._row_to_public(row)

    # -- verification -------------------------------------------------
    def verify_bearer(self, token: str) -> dict | None:
        """Verify a ``Authorization: Bearer <token>`` value.

        Returns the key record (with ``scopes``) on success, ``None`` on
        unknown / revoked / malformed tokens. Uses a constant-time compare
        against the stored SHA-256 hashes.
        """
        if not isinstance(token, str) or not token.startswith(_TOKEN_PREFIX):
            return None
        digest = _hash_token(token)
        rows = self._conn.execute(
            "SELECT * FROM api_keys WHERE revoked = 0").fetchall()
        for row in rows:
            if hmac.compare_digest(row["key_hash"], digest):
                self._conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (self._now(), row["id"]),
                )
                self._conn.commit()
                return self._row_to_record(row)
        return None

    # -- management ---------------------------------------------------
    def list(self) -> list:
        """All keys, newest first (hashes never included)."""
        rows = self._conn.execute(
            "SELECT * FROM api_keys ORDER BY id DESC").fetchall()
        return [self._row_to_public(r) for r in rows]

    def revoke(self, name_or_id) -> bool:
        """Revoke a key by name or id. Returns True when a key was revoked."""
        if isinstance(name_or_id, int):
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0",
                (name_or_id,),
            )
        else:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE name = ? "
                "AND revoked = 0",
                (str(name_or_id),),
            )
        self._conn.commit()
        return cur.rowcount > 0

    def get(self, name_or_id) -> dict | None:
        """Public record for one key, or None."""
        if isinstance(name_or_id, int):
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE id = ?",
                (name_or_id,)).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE name = ?",
                (str(name_or_id),)).fetchone()
        return self._row_to_public(row) if row else None


# ---------------------------------------------------------------------------
# Admin-only issuance facade (dashboard RBAC boundary)
# ---------------------------------------------------------------------------

def issue_api_key(store: ApiKeyStore, name: str, scopes, actor_role: str):
    """Issue an API key; **admin role required**.

    :raises PermissionError: when ``actor_role`` is not ``"admin"``.
    """
    if actor_role != "admin":
        raise PermissionError("API key issuance requires the admin role")
    return store.issue(name, scopes)


def revoke_api_key(store: ApiKeyStore, name_or_id, actor_role: str) -> bool:
    """Revoke an API key; **admin role required**.

    :raises PermissionError: when ``actor_role`` is not ``"admin"``.
    """
    if actor_role != "admin":
        raise PermissionError("API key revocation requires the admin role")
    return store.revoke(name_or_id)
