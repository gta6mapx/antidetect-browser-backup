"""HMAC-signed session tokens for the local GUI backend (Phase 8).

Cookie name: ``ad_session``. The token is
``base64url(payload) + "." + hex(HMAC-SHA256(secret, body))`` where the
payload is JSON ``{"u": username, "exp": epoch_seconds, "n": nonce}``.

The 32-byte signing secret lives at
``~/.antidetect-browser/.session.key``, created with mode ``0o600`` on
first use. Tokens default to a 12-hour lifetime. Standard library only
(``hmac`` + ``secrets``).
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from src import paths as _paths


def _secret_path() -> str:
    """Session secret path, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.session_key_path()

#: Name of the session cookie issued by POST /api/login.
COOKIE_NAME = "ad_session"

#: Default session lifetime (hours).
SESSION_TTL_HOURS = 12


def get_or_create_secret() -> bytes:
    """Return the session signing secret, creating it (0o600) on first use."""
    secret_path = _secret_path()
    if os.path.exists(secret_path):
        with open(secret_path, "rb") as fh:
            return fh.read().strip()
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    secret = secrets.token_bytes(32)
    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(secret)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(secret_path, 0o600)
    return secret


def create_session_token(username: str,
                         ttl_hours: float = SESSION_TTL_HOURS) -> str:
    """Create a signed session token for ``username``.

    Args:
        username: The authenticated username.
        ttl_hours: Token lifetime; defaults to 12 hours.

    Returns:
        The ``body.signature`` token string.
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("username must be non-empty")
    payload = {
        "u": username,
        "exp": int(time.time()) + int(ttl_hours * 3600),
        "n": secrets.token_hex(8),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sig = hmac.new(get_or_create_secret(), body.encode("ascii"),
                   hashlib.sha256).hexdigest()
    return body + "." + sig


def verify_session_token(token: str | None) -> dict | None:
    """Verify a token.

    Returns:
        The payload dict (``u``, ``exp``, ``n``) when the signature is
        valid and the token has not expired; otherwise ``None``.
    """
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expected = hmac.new(get_or_create_secret(), body.encode("ascii"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8")
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    if not payload.get("u"):
        return None
    return payload
