"""Central home-directory resolution for all user data.

Every module that touches user data (databases, profile session dirs,
backups, cookie exports, templates, RPA state, sync state, the master
encryption key) resolves paths through this module so the
``ANTIDETECT_HOME`` environment variable is honored everywhere.

Resolution order for the data home:

1. ``ANTIDETECT_HOME`` when set and non-empty — wins unconditionally.
2. Legacy ``~/.antidetect-browser`` when that directory already exists,
   so data written by earlier versions (profiles, proxies, master key)
   keeps working without any migration step.
3. ``~/.antidetect`` otherwise (fresh installs follow the Phase 8 spec).

All helpers return ``str`` paths and resolve the environment on every
call, so changing ``ANTIDETECT_HOME`` between calls is honored (tests
rely on this; do NOT cache the result at module import time).
"""

import os

#: Name of the environment variable overriding the data home.
ENV_VAR = "ANTIDETECT_HOME"

#: Data-home used for fresh installs when neither ANTIDETECT_HOME nor the
#: legacy directory applies.
_DEFAULT_DIRNAME = ".antidetect"

#: Data-home written by versions before Phase 8; kept working when present.
_LEGACY_DIRNAME = ".antidetect-browser"


def _home() -> str:
    """Return the user's home directory."""
    return os.path.expanduser("~")


def data_dir() -> str:
    """Return the root data directory for all user data.

    Honors ``ANTIDETECT_HOME``; falls back to the legacy
    ``~/.antidetect-browser`` when it already exists, else ``~/.antidetect``.
    Does not create anything.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    legacy = os.path.join(_home(), _LEGACY_DIRNAME)
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(_home(), _DEFAULT_DIRNAME)


def ensure_data_dir() -> str:
    """Return :func:`data_dir`, creating it when missing."""
    path = data_dir()
    os.makedirs(path, exist_ok=True)
    return path


def _under(*parts: str, create: bool = False) -> str:
    """Join ``parts`` under :func:`data_dir`, optionally creating it."""
    path = os.path.join(data_dir(), *parts)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


# -- databases ------------------------------------------------------------

def profiles_db() -> str:
    """Path of the profiles SQLite database."""
    return _under("profiles.db")


def proxies_db() -> str:
    """Path of the proxies SQLite database (encrypted passwords live here)."""
    return _under("proxies.db")


def master_key_path() -> str:
    """Path of the Fernet master key file (mode 0o600)."""
    return _under(".master.key")


def users_db() -> str:
    """Path of the dashboard users SQLite database."""
    return _under("users.db")


def session_key_path() -> str:
    """Path of the dashboard session-signing secret (mode 0o600)."""
    return _under(".session.key")


def admin_seed_path() -> str:
    """Path of the first-boot admin seed marker file (mode 0o600)."""
    return _under(".admin_seed")


# -- directories ----------------------------------------------------------

def profiles_dir(create: bool = False) -> str:
    """Root dir of per-profile Camoufox user-data dirs (``profiles/<name>/``)."""
    return _under("profiles", create=create)


def profile_dir(name: str) -> str:
    """Session dir for one profile (does not create it; caller sanitizes)."""
    return os.path.join(profiles_dir(), name)


def backups_dir(create: bool = False) -> str:
    """Root dir for session backup zips (``backups/<name>/``)."""
    return _under("backups", create=create)


def cookie_exports_dir(create: bool = False) -> str:
    """Dir where ``cookies export`` writes files."""
    return _under("cookie-exports", create=create)


def templates_dir(create: bool = False) -> str:
    """Dir holding bulk-import JSON templates."""
    return _under("templates", create=create)


def rpa_dir(create: bool = False) -> str:
    """Root dir for RPA runtime state (recordings, run artifacts)."""
    return _under("rpa", create=create)


def sync_dir(create: bool = False) -> str:
    """Root dir for sync-session bookkeeping (CLI pidfiles)."""
    return _under("sync", create=create)
