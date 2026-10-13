"""CreepJS lie-detection check for anti-detect profiles.

Upstream: https://github.com/abrahamjuliot/creepjs
License: MIT License, Copyright (c) 2021 abrahamjuliot
(see ``src/fingerprint/creepjs/LICENSE`` and ``ATTRIBUTION.md``).

Serves the self-hosted CreepJS build (``src/fingerprint/creepjs/``) on
127.0.0.1, loads it in a real Chromium via Playwright (vendored patchright
preferred, venv playwright fallback), waits for CreepJS to expose
``window.Creep`` / ``window.Fingerprint``, and returns a lie-score summary::

    {
        "lies": ["plugins: invalid mimetype", ...],
        "score": 1,
        "passed": False,
        ...
    }

``score`` is CreepJS's ``totalLies`` count (0 = no lies detected).
"""

from __future__ import annotations

import functools
import glob
import http.server
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Make the repo root importable so ``src._vendor`` (vendored patchright) works
# no matter where this module is launched from.
# __file__ = <repo>/src/fingerprint/creepjs_check.py -> three levels up = <repo>
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    import src._vendor  # noqa: F401  (enables vendored patchright import)
except Exception:  # noqa: BLE001 - vendored shim is best-effort
    pass

CREEPJS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creepjs")

#: JS predicate: true once CreepJS has finished computing and exposed results.
#: Note: the full lie data lives on ``window.Fingerprint`` (the fp object);
#: ``window.Creep`` is a reduced visitor-matching subset without a lies section.
_RESULTS_READY_JS = "() => !!window.Fingerprint"

#: Extract only the lie-detection section (the full Creep/Fingerprint objects
#: embed base64 canvas captures and are megabytes in size).
_EXTRACT_JS = """() => {
  const fp = window.Fingerprint;
  const creep = window.Creep;
  if (!fp || typeof fp !== 'object') return { ready: false };
  const lies = (fp && fp.lies) || (creep && creep.lies) || null;
  return {
    ready: true,
    liesComputed: !!lies,
    totalLies: lies ? (lies.totalLies || 0) : 0,
    liesData: lies ? (lies.data || {}) : {},
    liesHash: lies ? (lies.$hash || null) : null,
    hasFingerprint: true,
    hasCreep: !!creep,
  };
}"""

_MAX_DETAIL_CHARS = 300


class CreepJSCheckError(RuntimeError):
    """Raised when the CreepJS lie-detection check cannot complete."""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without per-request log spam."""

    def log_message(self, *args: Any) -> None:  # noqa: ANN002, ANN003
        pass


class CreepJSServer:
    """Serve the vendored CreepJS build on 127.0.0.1 in a daemon thread.

    Usage::

        with CreepJSServer() as server:
            check_creepjs(server.url, ...)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        if not os.path.isdir(CREEPJS_DIR) or not os.path.isfile(
            os.path.join(CREEPJS_DIR, "index.html")
        ):
            raise CreepJSCheckError(
                f"CreepJS build not found at {CREEPJS_DIR} "
                "(expected index.html + creep.js + style.min.css)"
            )
        self._host = host
        self._port = port
        self._server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise CreepJSCheckError("server not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/index.html"

    def __enter__(self) -> "CreepJSServer":
        handler = functools.partial(_QuietHandler, directory=CREEPJS_DIR)
        try:
            self._server = http.server.ThreadingHTTPServer(
                (self._host, self._port), handler
            )
        except OSError as e:
            raise CreepJSCheckError(
                f"cannot bind CreepJS server on {self._host}:{self._port}: {e}"
            )
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="creepjs-server", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _playwright_factories() -> List[Tuple[str, Any]]:
    """Return (name, sync_playwright) candidates, patchright first."""
    factories: List[Tuple[str, Any]] = []
    try:
        from patchright.sync_api import sync_playwright as _pw

        factories.append(("patchright (vendored)", _pw))
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright as _pw

        factories.append(("playwright (venv)", _pw))
    except ImportError:
        pass
    return factories


def _fallback_chromium_executables(pw: Any, headless: bool) -> List[str]:
    """Find usable Chromium binaries under the Playwright browsers root.

    Needed because ``BrowserType.executable_path`` does not reflect the
    headless-shell binary that ``launch(headless=True)`` actually requires
    (Playwright >= 1.49), which may simply not be installed. For headless
    launches the headless shell is preferred, then full Chromium (which also
    supports ``--headless``); for headful launches the order is reversed.
    """
    _ = pw  # reserved: future registry-aware lookup
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser(
        "~/.cache/ms-playwright"
    )
    headless_shell = [
        os.path.join(rev_dir, "chrome-headless-shell-linux64",
                     "chrome-headless-shell")
        for rev_dir in sorted(glob.glob(os.path.join(root, "chromium*")))
    ]
    full_chrome = [
        os.path.join(rev_dir, rel)
        for rev_dir in sorted(glob.glob(os.path.join(root, "chromium-*")))
        for rel in (
            os.path.join("chrome-linux64", "chrome"),
            os.path.join("chrome-linux", "chrome"),
        )
    ]
    ordered = headless_shell + full_chrome if headless else full_chrome + headless_shell
    return [
        c for c in ordered if os.path.isfile(c) and os.access(c, os.X_OK)
    ]


def _launch_chromium(
    sync_playwright: Any, driver_name: str, headless: bool
) -> Tuple[Any, Any, str]:
    """Start the driver and launch Chromium.

    Falls back to an explicitly resolved full-Chromium binary when the
    default headless-shell executable is missing. Returns (pw, browser, via).
    """
    try:
        pw = sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        raise CreepJSCheckError(f"{driver_name}: driver failed to start: {e}")
    try:
        try:
            browser = pw.chromium.launch(headless=headless)
            return pw, browser, f"{driver_name}"
        except Exception as first:  # noqa: BLE001
            last_error: Exception = first
            for exe in _fallback_chromium_executables(pw, headless):
                try:
                    browser = pw.chromium.launch(
                        headless=headless, executable_path=exe
                    )
                    return pw, browser, f"{driver_name} (executable_path={exe})"
                except Exception as e:  # noqa: BLE001
                    last_error = e
            raise CreepJSCheckError(
                f"{driver_name}: chromium launch failed (tried default "
                f"plus installed-browser fallbacks): {last_error}"
            )
    except Exception:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
        raise


def fetch_creepjs_results(
    url: str,
    headless: bool = True,
    navigation_timeout_ms: int = 30000,
    results_timeout_ms: int = 90000,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Load the CreepJS page at ``url`` in Chromium and return the raw
    lie-detection section (``totalLies``, ``liesData``, hashes).

    Raises :class:`CreepJSCheckError` on any failure (no browser, no page,
    results never exposed).
    """
    factories = _playwright_factories()
    if not factories:
        raise CreepJSCheckError(
            "neither patchright nor playwright is importable "
            "(is the venv activated?)"
        )
    errors: List[str] = []
    for driver_name, sync_playwright in factories:
        pw = browser = None
        try:
            pw, browser, via = _launch_chromium(
                sync_playwright, driver_name, headless
            )
            context = browser.new_context(
                **({"user_agent": user_agent} if user_agent else {})
            )
            page = context.new_page()
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms,
                )
                try:
                    page.wait_for_function(
                        _RESULTS_READY_JS, timeout=results_timeout_ms
                    )
                except Exception as e:  # noqa: BLE001
                    raise CreepJSCheckError(
                        f"CreepJS results (window.Creep) never appeared "
                        f"within {results_timeout_ms}ms at {url}: {e}"
                    )
                data = page.evaluate(_EXTRACT_JS)
            finally:
                context.close()
            if not isinstance(data, dict) or not data.get("ready"):
                raise CreepJSCheckError(
                    f"CreepJS page at {url} exposed no usable results"
                )
            data["driver"] = via
            data["url"] = url
            return data
        except CreepJSCheckError as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001 - report browser errors plainly
            errors.append(f"{driver_name}: {type(e).__name__}: {e}")
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:  # noqa: BLE001
                    pass
    raise CreepJSCheckError("all browser drivers failed: " + " | ".join(errors))


def parse_creepjs_results(data: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the raw CreepJS lie section into a lie-score summary dict.

    ``lies`` is a flat ``["category: detail", ...]`` list, ``score`` is
    CreepJS's ``totalLies`` (0 = clean), ``passed`` is True only when the
    lies section was computed and the count is 0.
    """
    lies_data = data.get("liesData") or {}
    lies: List[str] = []
    for category, details in lies_data.items():
        items = details if isinstance(details, list) else [details]
        for detail in items:
            text = str(detail)
            if len(text) > _MAX_DETAIL_CHARS:
                text = text[:_MAX_DETAIL_CHARS] + "…"
            lies.append(f"{category}: {text}")
    lies_computed = bool(data.get("liesComputed"))
    total = int(data.get("totalLies") or 0)
    return {
        "lies": lies,
        "score": total if lies_computed else None,
        "passed": lies_computed and total == 0,
        "lies_computed": lies_computed,
        "lies_hash": data.get("liesHash"),
        "has_fingerprint": bool(data.get("hasFingerprint")),
        "driver": data.get("driver"),
        "url": data.get("url"),
    }


def check_creepjs(
    headless: bool = True,
    navigation_timeout_ms: int = 30000,
    results_timeout_ms: int = 90000,
    user_agent: Optional[str] = None,
    port: int = 0,
) -> Dict[str, Any]:
    """Serve the vendored CreepJS build locally, run Chromium at it, and
    return the lie-score summary dict (see :func:`parse_creepjs_results`).
    """
    started = time.monotonic()
    with CreepJSServer(port=port) as server:
        raw = fetch_creepjs_results(
            server.url,
            headless=headless,
            navigation_timeout_ms=navigation_timeout_ms,
            results_timeout_ms=results_timeout_ms,
            user_agent=user_agent,
        )
    result = parse_creepjs_results(raw)
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if any(a in ("-h", "--help") for a in argv):
        print("usage: creepjs_check.py [--headful] [--port N] [--timeout SEC]")
        print("Runs the self-hosted CreepJS lie-detection check and prints")
        print('a JSON summary like {"lies": [...], "score": N, "passed": bool}.')
        return 2
    headless = "--headful" not in argv
    port = 0
    results_timeout_ms = 90000
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        if arg == "--timeout" and i + 1 < len(argv):
            results_timeout_ms = int(argv[i + 1]) * 1000
    try:
        result = check_creepjs(
            headless=headless,
            port=port,
            results_timeout_ms=results_timeout_ms,
        )
    except CreepJSCheckError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - report unexpected errors plainly
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
