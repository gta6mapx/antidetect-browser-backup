"""Symmetric encryption for sensitive strings at rest.

A single master Fernet key lives at the data home's ``.master.key`` file
(see :mod:`src.paths`, which honors ``ANTIDETECT_HOME``). The key file is
created with mode ``0o600`` (owner-only read/write) on first use. Callers
mark ciphertext with the ``"enc:"`` prefix via :func:`is_encrypted` before
decrypting; plaintext values without the prefix are treated as
legacy/unencrypted and returned untouched by the caller's decryption path.

Key rotation
------------
Rotating the master key is a two-step operation provided by this module:

1. :func:`rotate_key` re-encrypts every ``"enc:"`` proxy password in the
   proxies DB from ``old_key`` to ``new_key``. This is the dangerous step:
   run it BEFORE replacing the key file, while both keys are known.
2. :func:`replace_master_key` writes ``new_key`` to the key file
   (mode ``0o600``). Back up the old key file first — if rotation is
   interrupted between the two steps, the old key file is your only way
   back.

See ``SECURITY.md`` (repo root) for the full key model and the rotation
procedure, and ``python -m src.cli key-rotate --help`` for the CLI.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import os
import sqlite3
from typing import Union

from cryptography.fernet import Fernet, InvalidToken

from src import paths as _paths

#: Prefix marking a stored value as encrypted by this module.
_ENC_PREFIX = "enc:"

#: Accepted key types for the rotation helpers (bytes or ASCII str).
_KeyLike = Union[bytes, str]


def _key_path() -> str:
    """Master key path, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.master_key_path()


def _as_bytes(key: _KeyLike, what: str) -> bytes:
    """Coerce a Fernet key to bytes, validating its format."""
    raw = key.encode("ascii") if isinstance(key, str) else key
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("%s must be bytes or ASCII str, got %s"
                        % (what, type(key).__name__))
    try:
        Fernet(bytes(raw))  # validates base64 + length
    except Exception as exc:
        raise ValueError("%s is not a valid Fernet key: %s" % (what, exc))
    return bytes(raw)


def get_or_create_key() -> bytes:
    """Return the master Fernet key, creating it on first use.

    The key is stored at the data home's ``.master.key`` file (honors
    ``ANTIDETECT_HOME`` via :mod:`src.paths`). When the file does not
    exist, a fresh random key is generated, written with ``os.open`` after
    ``os.makedirs`` of the parent directory, then ``os.chmod(path, 0o600)``
    so only the owning user can read it. When the file exists, its bytes
    are returned as-is.

    Returns:
        The 32-byte URL-safe base64-encoded Fernet key.

    Note: replacing or deleting this file without running
    :func:`rotate_key` first orphans every previously encrypted value.
    """
    key_path = _key_path()
    if os.path.exists(key_path):
        with open(key_path, "rb") as fh:
            return fh.read().strip()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    # Belt and suspenders: the fd was created with 0o600, but chmod again
    # in case the file somehow pre-existed with looser permissions (race
    # between the exists() check and os.open()).
    os.chmod(key_path, 0o600)
    return key


def _fernet() -> Fernet:
    """Build a Fernet instance from the master key."""
    return Fernet(get_or_create_key())


def encrypt_str(plaintext: "str | None") -> "str | None":
    """Encrypt a string with the master key.

    Args:
        plaintext: The string to encrypt. ``None`` passes through.

    Returns:
        ``None`` if ``plaintext`` is ``None``; otherwise the Fernet
        token as a ``str`` (the caller is responsible for adding the
        ``"enc:"`` marker prefix when persisting).
    """
    if plaintext is None:
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_str(token: "str | None") -> "str | None":
    """Decrypt a Fernet token produced by :func:`encrypt_str`.

    Args:
        token: The token string. ``None`` passes through.

    Returns:
        The decrypted plaintext, or ``None`` if ``token`` is ``None``.

    Raises:
        cryptography.fernet.InvalidToken: If the token cannot be
            decrypted with the current master key (wrong key, tampered
            or corrupt token).
    """
    if token is None:
        return None
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def is_encrypted(value) -> bool:
    """Return True if ``value`` carries the ``"enc:"`` encryption marker.

    Args:
        value: Any value; non-strings return False.
    """
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def _proxies_db_path() -> str:
    """Proxies DB path, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.proxies_db()


def rotate_key(old_key: _KeyLike, new_key: _KeyLike) -> int:
    """Re-encrypt every encrypted proxy password from ``old_key`` to ``new_key``.

    Scans the ``proxies`` table of the proxies DB (honors
    ``ANTIDETECT_HOME``); each value carrying the ``"enc:"`` marker is
    decrypted with ``old_key`` and stored re-encrypted under ``new_key``.
    Values without the marker (legacy plaintext, NULL) are left untouched.

    This function does NOT touch the master key file: run it while the
    file still holds ``old_key``, then call :func:`replace_master_key`
    (or the ``key-rotate`` CLI, which does both plus a backup).

    Args:
        old_key: The current master key (bytes or ASCII str).
        new_key: The replacement master key (bytes or ASCII str).

    Returns:
        Number of proxy passwords rotated. 0 when the DB or the
        ``proxies`` table does not exist yet.

    Raises:
        ValueError: If either key is not a valid Fernet key.
        cryptography.fernet.InvalidToken: If ``old_key`` cannot decrypt a
            stored value (wrong old key or tampered data). Already-rotated
            rows keep their new ciphertext; the rest are untouched, so the
            operation is safely re-runnable with the correct key.
    """
    old_raw = _as_bytes(old_key, "old_key")
    new_raw = _as_bytes(new_key, "new_key")
    old_fernet = Fernet(old_raw)
    new_fernet = Fernet(new_raw)

    db_path = _proxies_db_path()
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT name, password FROM proxies").fetchall()
        except sqlite3.OperationalError:
            return 0  # table not created yet
        rotated = 0
        for name, password in rows:
            if not is_encrypted(password):
                continue
            plaintext = old_fernet.decrypt(
                password[len(_ENC_PREFIX):].encode("ascii"))
            new_token = _ENC_PREFIX + new_fernet.encrypt(plaintext).decode(
                "ascii")
            conn.execute("UPDATE proxies SET password = ? WHERE name = ?",
                         (new_token, name))
            rotated += 1
        conn.commit()
        return rotated
    finally:
        conn.close()


def replace_master_key(new_key: _KeyLike) -> str:
    """Write ``new_key`` to the master key file (mode ``0o600``).

    Back up the existing key file yourself BEFORE calling this (the
    ``key-rotate`` CLI does that automatically). After this call,
    :func:`encrypt_str` / :func:`decrypt_str` use the new key.

    Args:
        new_key: The replacement master key (bytes or ASCII str).

    Returns:
        The key file path written.

    Raises:
        ValueError: If ``new_key`` is not a valid Fernet key.
    """
    raw = _as_bytes(new_key, "new_key")
    key_path = _key_path()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(key_path, 0o600)
    return key_path


__all__ = [
    "encrypt_str",
    "decrypt_str",
    "is_encrypted",
    "get_or_create_key",
    "rotate_key",
    "replace_master_key",
    "InvalidToken",
]
