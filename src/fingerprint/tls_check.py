"""solvetls TLS fingerprint checker for anti-detect profiles.

Upstream: https://github.com/solvefixed/solvetls
License: MIT License, Copyright (c) 2026 solvefixed

Launches a headless Chromium via Playwright, navigates to a solvetls-compatible
fingerprinting server URL, and parses/returns the JA3/JA4 fingerprint fields.

Works against ANY solvetls-compatible endpoint: the URL is passed as an
argument. See also ``docs/SOLVETLS.md``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional


def _full_chromium_executable() -> Optional[str]:
    """Best-effort path to a full (non-headless-shell) Chromium build in the
    Playwright cache. Used as a fallback when the headless-shell artifact is
    not installed in an environment (e.g. no network to download it).
    """
    base = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")
    if not os.path.isdir(base):
        return None
    for entry in sorted(os.listdir(base)):
        if not entry.startswith("chromium-"):
            continue
        for rel in (
            os.path.join("chrome-linux", "chrome"),          # Linux (older)
            os.path.join("chrome-linux64", "chrome"),        # Linux (newer)
            os.path.join("chrome-mac", "Chromium.app", "Contents",
                         "MacOS", "Chromium"),               # macOS
            os.path.join("chrome-win", "chrome.exe"),        # Windows
        ):
            cand = os.path.join(base, entry, rel)
            if os.path.isfile(cand):
                return cand
    return None


def parse_solvetls_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the fingerprint fields from a solvetls JSON response dict.

    Tolerates missing sections (e.g. HTTP/1.1 clients get an ``http1`` section
    instead of ``http2``). Returns a dict with the key fields plus the raw
    response for drill-down.
    """
    tls = data.get("tls") or {}
    http2 = data.get("http2") or {}
    version = tls.get("version") or {}

    ciphers = []
    for cs in tls.get("cipher_suites") or []:
        ciphers.append(cs.get("name") or cs.get("hex"))

    extensions = tls.get("extensions") or []

    return {
        "ja3_hash": tls.get("ja3_hash"),
        "ja4": tls.get("ja4"),
        "akamai_fingerprint": http2.get("akamai_fingerprint"),
        "akamai_fingerprint_hash": http2.get("akamai_fingerprint_hash"),
        "tls_version": version.get("name") or version.get("hex"),
        "cipher_suites": ciphers,
        "extension_count": len(extensions),
        "http_version": data.get("http_version"),
        "user_agent": data.get("user_agent"),
        "ip": data.get("ip"),
        "response": data,
    }


def fetch_fingerprint_json(url: str, headless: bool = True,
                           timeout: int = 30000) -> Dict[str, Any]:
    """Launch headless Chromium via Playwright, navigate to ``url`` and return
    the raw solvetls JSON response dict.

    ``url`` may be HTTP or HTTPS; solvetls redirects plain HTTP to HTTPS (308)
    on a real server — a stub server serving JSON over plain HTTP works too.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
        except Exception as e:  # headless-shell artifact missing?
            exe = (_full_chromium_executable()
                   if "Executable doesn't exist" in str(e) else None)
            if not exe:
                raise
            browser = p.chromium.launch(headless=headless,
                                       executable_path=exe)
        try:
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Fetch the exact response bytes inside the page (full TLS path),
            # falling back to the rendered body text if fetch fails.
            try:
                raw = page.evaluate(
                    "fetch(window.location.href).then(r => r.text())")
            except Exception:
                raw = page.evaluate("document.body.innerText")
            return json.loads(raw)
        finally:
            browser.close()


def check_tls(url: str, headless: bool = True) -> Dict[str, Any]:
    """Navigate headless Chromium to a solvetls endpoint and return the parsed
    JA3/JA4 fingerprint fields as a dict (see :func:`parse_solvetls_response`).
    """
    return parse_solvetls_response(fetch_fingerprint_json(url, headless))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: tls_check.py <solvetls-url> [--headful]")
        print("example: python src/fingerprint/tls_check.py https://localhost:8443/")
        return 2
    url = argv[0]
    headless = "--headful" not in argv
    try:
        result = check_tls(url, headless=headless)
    except Exception as e:  # noqa: BLE001 - report fetch errors plainly
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
