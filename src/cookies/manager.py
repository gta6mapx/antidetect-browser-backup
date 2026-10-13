"""Cookie import / export.

Moving an account between machines (or seeding a fresh profile with a session
you already own) is the single most-requested job of a profile manager, so
this module speaks the formats people actually have on disk:

  * **Playwright storage state** — ``{"cookies": [...], "origins": [...]}``
  * **Browser-extension JSON** — the flat list produced by EditThisCookie,
    Cookie-Editor, and friends (``expirationDate``, ``sameSite: "no_restriction"``…)
  * **Netscape ``cookies.txt``** — what curl/wget/yt-dlp read and write

Vendored VERBATIM from persona-studio
(https://github.com/anhnnp/persona-studio, MIT License,
Copyright (c) 2025 Ahsan, commit d46e984cfbdcd7278063e793e6ab1ad95b8cdc78):
``normalize``, ``parse``, ``parse_netscape``, ``to_netscape``, ``dumps``,
``read_file``. See ``vendor/persona-import/ATTRIBUTION.md``.

ADAPTED (not vendored): ``export_cookies`` / ``import_cookies`` replace
upstream's ``read``/``write`` (bound to persona-studio's
``drivers.session``). The logic is the same (read via ``context.cookies()``,
write via ``context.add_cookies()``, ``clear_cookies()`` with ``clear=True``)
but the browser is acquired through OUR launch layer
(:func:`src.browser.launcher.launch_profile`, headless), so the profile's
persistent Camoufox user-data dir is what gets read/written.

Reading and writing the cookies themselves goes through the browser rather
than the profile's data directory: values are only portably readable from
the live cookie store, so a headless launch is required. When the Camoufox
browser binary is missing (checked via ``browser_binary_present()`` first —
no multi-GB download is ever triggered), the functions return
``{'error': ...}`` instead of crashing.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

import json
import os
import re
from pathlib import Path
from typing import Optional

from src import paths as _paths

# ---------------------------------------------------------------------------
# Vendored VERBATIM from persona-studio engine/persona/cookies.py
# (MIT, Copyright (c) 2025 Ahsan).
# ---------------------------------------------------------------------------

# Extension exports spell sameSite in their own way; Playwright wants these.
_SAME_SITE = {
    "no_restriction": "None", "none": "None",
    "lax": "Lax", "unspecified": "Lax", "": "Lax",
    "strict": "Strict",
}


def normalize(raw: dict) -> Optional[dict]:
    """Map one cookie from any supported dialect to Playwright's shape.

    Returns ``None`` for entries too incomplete to be usable (no name, or no
    domain to attach them to).
    """
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    domain = raw.get("domain") or raw.get("host")
    if not name or not domain:
        return None

    # "expirationDate" (extensions) and "expires" (Playwright) mean the same
    # thing; a session cookie is -1.
    expires = raw.get("expires", raw.get("expirationDate"))
    if raw.get("session") or expires in (None, "", 0):
        expires = -1

    cookie = {
        "name": str(name),
        "value": str(raw.get("value", "")),
        "domain": str(domain),
        "path": str(raw.get("path") or "/"),
        "expires": float(expires),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure": bool(raw.get("secure", False)),
        "sameSite": _SAME_SITE.get(str(raw.get("sameSite", "")).lower(), "Lax"),
    }
    # Chromium rejects SameSite=None unless the cookie is also Secure.
    if cookie["sameSite"] == "None" and not cookie["secure"]:
        cookie["sameSite"] = "Lax"
    return cookie


def parse_netscape(text: str) -> list:
    """Parse a Netscape ``cookies.txt`` file (the curl/wget/yt-dlp format)."""
    out = []
    for line in text.splitlines():
        raw = line.strip()
        # "#HttpOnly_" is a real prefix, not a comment.
        http_only = raw.startswith("#HttpOnly_")
        if http_only:
            raw = raw[len("#HttpOnly_"):]
        elif not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 7:
            parts = raw.split()
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expiry, name, value = parts[:7]
        c = normalize({
            "name": name, "value": value, "domain": domain, "path": path,
            "expires": float(expiry) if expiry.replace(".", "", 1).isdigit() else -1,
            "secure": secure.upper() == "TRUE", "httpOnly": http_only,
        })
        if c:
            out.append(c)
    return out


def parse(text: str) -> list:
    """Parse cookies from any supported format and return Playwright cookies.

    Content-sniffed: JSON first (Playwright storage state
    ``{"cookies": [...], "origins": [...]}``, a flat extension JSON list, or
    a single cookie object), Netscape ``cookies.txt`` as fallback.
    """
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return parse_netscape(text)

    if isinstance(data, dict):
        data = data.get("cookies", [data])  # storage state, or a lone cookie
    if not isinstance(data, list):
        return []
    return [c for c in (normalize(x) for x in data) if c]


def to_netscape(cookies: list) -> str:
    """Serialise cookies as a Netscape ``cookies.txt`` file."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# Exported by Persona — https://github.com/TechQaiser/persona-studio",
        "",
    ]
    for c in cookies:
        domain = c["domain"]
        # A leading dot marks a domain cookie (valid for subdomains too).
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        lines.append("\t".join([
            prefix + domain,
            include_sub,
            c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(int(c.get("expires", -1) or -1)),
            c["name"],
            c.get("value", ""),
        ]))
    return "\n".join(lines) + "\n"


def dumps(cookies: list, fmt: str = "json") -> str:
    """Render cookies as ``json`` (Playwright storage state) or ``netscape``."""
    if fmt == "netscape":
        return to_netscape(cookies)
    return json.dumps({"cookies": cookies, "origins": []}, indent=2)


def read_file(path) -> list:
    """Parse a cookie file from disk (format detected from its contents)."""
    return parse(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Adaptation: browser round-trip through OUR launch layer.
# ---------------------------------------------------------------------------

def _export_dir() -> str:
    """Cookie-export dir, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.cookie_exports_dir(create=True)

_EXPORT_FORMATS = ("cookie-editor", "netscape", "playwright")


def _safe_filename(name: str) -> str:
    """Make a profile name safe for use as a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "profile"


def _profile_manager(profile_manager=None):
    """Return a ProfileManager (lazy so tests can inject a temp-db one)."""
    if profile_manager is not None:
        return profile_manager
    from src.profiles.manager import ProfileManager
    return ProfileManager()


def _launch_headless(persona):
    """Launch the profile headless and return the LaunchedProfile.

    Raises RuntimeError with a clear message when the browser binary is
    missing (checked BEFORE launching so camoufox never starts a
    multi-GB download).

    Cookie I/O does not depend on the proxy (no site is ever visited), so
    when the launch fails only because camoufox's optional ``geoip`` extra
    is not installed — which happens for proxied profiles, since the
    launcher sets ``geoip=True`` for them — we retry with the proxy
    stripped from the persona copy. The cookie jar is identical either way;
    ``geoip`` only affects spoofed locale/timezone signals, which are
    irrelevant to reading/writing cookies.
    """
    from src.browser.launcher import (browser_binary_present, launch_profile)
    try:
        binary_ok = browser_binary_present()
    except Exception:
        binary_ok = False
    if not binary_ok:
        raise RuntimeError(
            "camoufox browser binary not installed; run setup_browser.py "
            "first")
    try:
        return launch_profile(persona, headless=True)
    except Exception as exc:
        if "geoip" not in str(exc).lower() or not persona.get("proxy"):
            raise
        stripped = dict(persona)
        stripped["proxy"] = None
        return launch_profile(stripped, headless=True)


def export_cookies(profile_name, fmt="cookie-editor", profile_manager=None):
    """Export a profile's live cookie jar to a file.

    :param profile_name: name of an existing profile.
    :param fmt: ``'cookie-editor'`` (flat extension-style JSON list),
        ``'netscape'`` (``cookies.txt``), or ``'playwright'`` (storage-state
        JSON).
    :param profile_manager: optional injected manager (tests).
    :return: the exported file path (str) on success, or
        ``{'error': msg}`` — unknown profile, unknown format, or missing
        browser binary. Never raises for those cases.
    """
    if fmt not in _EXPORT_FORMATS:
        return {"error": "unknown format %r; expected one of %s"
                % (fmt, list(_EXPORT_FORMATS))}
    pm = _profile_manager(profile_manager)
    try:
        persona = pm.get(profile_name)
    except (KeyError, LookupError):
        return {"error": "no profile named '%s'" % profile_name}
    try:
        launched = _launch_headless(persona)
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": "launch failed: %s" % exc}
    try:
        jar = [c for c in
               (normalize(dict(c)) for c in launched.browser.cookies()) if c]
    finally:
        try:
            launched.close()
        except Exception:
            pass

    export_dir = _export_dir()
    base = _safe_filename(profile_name)
    if fmt == "netscape":
        body = to_netscape(jar)
        ext = "txt"
    elif fmt == "playwright":
        body = dumps(jar, fmt="json")
        ext = "json"
    else:  # cookie-editor: flat list of cookies as the extensions export
        body = json.dumps(jar, indent=2)
        ext = "json"
    path = os.path.join(export_dir, "%s.%s" % (base, ext))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _to_playwright_add(cookies: list) -> list:
    """Convert normalized cookies to the shape ``add_cookies`` accepts here.

    Three quirks of our Camoufox/Firefox build, all verified empirically:

    1. *Domain*-based ``add_cookies`` calls are silently dropped, so every
       cookie is rewritten in *url* form (``https://`` for secure cookies,
       ``http://`` otherwise; leading dot stripped from the domain; the
       cookie path embedded in the URL). This Playwright build also rejects
       ``url`` combined with ``domain``/``path`` keys, so only ``url`` is
       passed.
    2. Firefox enforces a hard ~400-day maximum cookie lifetime: any
       ``expires`` beyond ``now + 34560000`` s is rejected, so such values
       are clamped to the cap (the session stays logged in as long as the
       browser allows).
    3. Session cookies (``expires <= 0``) omit ``expires`` entirely.
    """
    import time
    cap = int(time.time()) + 34560000  # 400 days: Firefox's max cookie age
    out = []
    for c in cookies:
        host = str(c.get("domain") or "").lstrip(".")
        if not host or not c.get("name"):
            continue
        scheme = "https" if c.get("secure") else "http"
        path = c.get("path") or "/"
        if not path.startswith("/"):
            path = "/" + path
        entry = {
            "name": c["name"],
            "value": c.get("value", ""),
            "url": "%s://%s%s" % (scheme, host, path),
            "httpOnly": bool(c.get("httpOnly")),
            "secure": bool(c.get("secure")),
            "sameSite": c.get("sameSite") or "Lax",
        }
        try:
            expires = float(c.get("expires", -1))
        except (TypeError, ValueError):
            expires = -1
        if expires > 0:
            entry["expires"] = min(int(expires), cap)
        out.append(entry)
    return out


def import_cookies(profile_name, path, clear=False, profile_manager=None):
    """Import cookies from a file into a profile's live cookie jar.

    The file format is auto-detected (extension JSON, Playwright storage
    state, or Netscape ``cookies.txt``). With ``clear=True`` the existing
    jar is emptied first, so the profile ends up with exactly the cookies
    supplied. Importing an empty file is a no-op (no browser launch).

    :param profile_name: name of an existing profile.
    :param path: cookie file to import.
    :param clear: empty the jar before adding.
    :param profile_manager: optional injected manager (tests).
    :return: ``{'imported': n}`` or ``{'error': msg}``.
    """
    try:
        cookies = read_file(path)
    except OSError as exc:
        return {"error": "cannot read cookie file: %s" % exc}
    except Exception as exc:
        return {"error": "cannot parse cookie file: %s" % exc}
    if not cookies:
        return {"imported": 0}  # nothing to write -> no browser launch

    pm = _profile_manager(profile_manager)
    try:
        persona = pm.get(profile_name)
    except (KeyError, LookupError):
        return {"error": "no profile named '%s'" % profile_name}
    try:
        launched = _launch_headless(persona)
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": "launch failed: %s" % exc}
    try:
        context = launched.browser
        if clear:
            context.clear_cookies()
        context.add_cookies(_to_playwright_add(cookies))
    except Exception as exc:
        return {"error": "cookie write failed: %s" % exc}
    finally:
        try:
            launched.close()
        except Exception:
            pass
    return {"imported": len(cookies)}
