"""Local user accounts with role-based access (Phase 8).

Stores users in a SQLite database at ``~/.antidetect-browser/users.db``
(the same data directory the rest of the app uses). Passwords are
hashed with PBKDF2-HMAC-SHA256 from the standard library — no new
dependencies.

Hash format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``

Roles: ``admin`` (everything), ``member`` (everything except user
management / key rotation), ``viewer`` (read-only).
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from src import paths as _paths


def _db_path() -> str:
    """Users DB path, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.users_db()

_HASH_ALGO = "pbkdf2_sha256"
_HASH_ITERS = 260_000
_SALT_BYTES = 32

#: Roles the system understands, ordered by power.
ROLES = ("admin", "member", "viewer")


def hash_password(password: str) -> str:
    """Hash ``password`` for storage.

    Returns:
        ``"pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>"``.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _HASH_ITERS
    )
    return "%s$%d$%s$%s" % (
        _HASH_ALGO, _HASH_ITERS, salt.hex(), digest.hex()
    )


def check_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash string."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$", 3)
    except (ValueError, AttributeError):
        return False
    if algo != _HASH_ALGO:
        return False
    try:
        iters = int(iters)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iters
    )
    return hmac.compare_digest(digest, expected)


class UserStore:
    """SQLite-backed user store.

    Args:
        db_path: Path to the SQLite file. Defaults to
            ``~/.antidetect-browser/users.db``.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # -- helpers ------------------------------------------------------
    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _row_to_public(row) -> dict:
        """User dict for API output (never includes the password hash)."""
        return {
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    # -- public API ---------------------------------------------------
    def count(self) -> int:
        """Number of users in the store."""
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM users")
        return int(cur.fetchone()["n"])

    def create_user(self, username: str, password: str,
                    role: str = "member") -> dict:
        """Create a user. Raises ``ValueError`` on bad input, ``KeyError``
        when the username already exists.

        Returns the public user dict.
        """
        username = (username or "").strip()
        if not username:
            raise ValueError("username must be non-empty")
        if not password:
            raise ValueError("password must be non-empty")
        if role not in ROLES:
            raise ValueError("role must be one of %s" % (list(ROLES),))
        try:
            cur = self._conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at)"
                " VALUES (?, ?, ?, ?)",
                (username, hash_password(password), role, self._now()),
            )
        except sqlite3.IntegrityError:
            raise KeyError("user '%s' already exists" % username)
        self._conn.commit()
        return self.get_user(username)

    def get_user(self, username: str) -> dict:
        """Public user dict; raises ``KeyError`` when unknown."""
        cur = self._conn.execute(
            "SELECT username, role, created_at FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError("unknown user '%s'" % username)
        return dict(row)

    def verify_login(self, username: str, password: str) -> str | None:
        """Check credentials. Returns the user's role on success, ``None``
        on failure (unknown user or wrong password)."""
        cur = self._conn.execute(
            "SELECT password_hash, role FROM users WHERE username = ?",
            ((username or "").strip(),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        if not check_password(password or "", row["password_hash"]):
            return None
        return row["role"]

    def list_users(self) -> list[dict]:
        """All users as public dicts (no password hashes), sorted by name."""
        cur = self._conn.execute(
            "SELECT username, role, created_at FROM users ORDER BY username"
        )
        return [dict(r) for r in cur.fetchall()]

    def delete_user(self, username: str) -> bool:
        """Delete a user. Returns True when a row was removed."""
        cur = self._conn.execute(
            "DELETE FROM users WHERE username = ?", ((username or "").strip(),)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_role(self, username: str, role: str) -> bool:
        """Change a user's role. Raises ``ValueError`` for an unknown role;
        returns False when the user does not exist."""
        if role not in ROLES:
            raise ValueError("role must be one of %s" % (list(ROLES),))
        cur = self._conn.execute(
            "UPDATE users SET role = ? WHERE username = ?",
            (role, (username or "").strip()),
        )
        self._conn.commit()
        return cur.rowcount > 0
