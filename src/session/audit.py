"""Session audit using rookie-cookies (MIT).

Bulk-checks which profile dirs still hold live Google session cookies,
without launching a browser. Used by the session-status API.
"""
from __future__ import annotations

import os

# Google session cookies that indicate a logged-in Gmail session.
_GOOGLE_SESSION_COOKIES = frozenset({"SID", "HSID", "SSID", "APISID", "SAPISID"})


def find_cookies_db(profile_dir: str) -> str | None:
    """Locate cookies.sqlite inside a Camoufox profile dir."""
    if not profile_dir or not os.path.isdir(profile_dir):
        return None
    for root, _dirs, files in os.walk(profile_dir):
        if "cookies.sqlite" in files:
            return os.path.join(root, "cookies.sqlite")
    return None


def google_session_cookies(profile_dir: str) -> dict:
    """Extract Google session cookies from a profile dir.

    Returns {"present": [...names...], "count": N, "db": path|None}.
    Never raises — returns empty on any failure (locked DB, missing file).
    """
    db = find_cookies_db(profile_dir)
    if not db:
        return {"present": [], "count": 0, "db": None}
    try:
        import rookie_cookies
        cookies = rookie_cookies.extract_from_path(
            db, domains=["google.com", "accounts.google.com", "mail.google.com"])
    except Exception:
        return {"present": [], "count": 0, "db": db}
    names = {c.get("name") for c in (cookies or []) if c.get("name")}
    present = sorted(names & _GOOGLE_SESSION_COOKIES)
    return {"present": present, "count": len(cookies or []), "db": db}


def is_gmail_logged_in(profile_dir: str) -> bool:
    """True if the profile dir holds Google session cookies (likely logged in)."""
    return len(google_session_cookies(profile_dir)["present"]) > 0


def audit_many(profile_dirs: dict[str, str]) -> dict[str, dict]:
    """Bulk audit: {profile_name: profile_dir} -> {name: audit result}."""
    return {name: google_session_cookies(pdir)
            for name, pdir in profile_dirs.items()}
