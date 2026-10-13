"""Patchright engine: patched Playwright driver on stock Chromium.

What patchright does and does not do (see ``src/engines/README.md``):

* DOES hide automation tells at the protocol/driver layer
  (``navigator.webdriver`` is false out of the box, no ``cdc_`` keys,
  no ``--enable-automation`` flag, Console API disabled by design).
* does NOT spoof fingerprints — canvas/WebGL/audio/font/TLS signals stay
  stock Chromium. Fingerprint *coherence* (UA/platform/timezone/locale
  agreeing with each other) is our consistency layer's job, driven by the
  persona.

Two gaps the engine closes explicitly, because the defaults leak:

1. The default headless UA contains a ``HeadlessChrome`` token, so the
   persona's ``user_agent`` is ALWAYS injected via the context
   ``user_agent`` option (never rely on the browser default).
2. ``navigator.platform`` is not settable via ``new_context()`` options,
   so a tiny init script redefines it from the persona on every page of
   every context we open.

Kwargs-building is factored into the pure function
:func:`build_context_kwargs`, unit-testable without launching.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import json
import os
import re
import subprocess
import sys

from src import paths as _paths
from src.engines.base import Engine

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")

#: Playwright-style proxy types accepted by patchright context options.
_PROXY_TYPES = {"http", "https", "socks5", "socks4"}


def _registry_root() -> str:
    """Browser registry root patchright installs into / resolves from.

    Matches Playwright's own platform-specific cache location:
    Windows -> %LOCALAPPDATA%\\ms-playwright,
    macOS -> ~/Library/Caches/ms-playwright,
    Linux -> ~/.cache/ms-playwright.
    """
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return env
    home = os.path.expanduser("~")
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return os.path.join(local_app_data, "ms-playwright")
        return os.path.join(home, "AppData", "Local", "ms-playwright")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches", "ms-playwright")
    return os.path.join(home, ".cache", "ms-playwright")


def _chromium_revision():
    """Return the Chromium build revision pinned by vendored patchright.

    Read from the vendored driver's own ``browsers.json`` (the same pin
    ``patchright install chromium`` downloads), so this can never drift
    from what the driver expects. Returns None when unreadable.
    """
    try:
        import patchright
        browsers_json = os.path.join(
            os.path.dirname(os.path.abspath(patchright.__file__)),
            "driver", "package", "browsers.json",
        )
        with open(browsers_json, encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("browsers", []):
            if entry.get("name") == "chromium":
                return str(entry.get("revision"))
    except Exception:
        pass
    return None


def chromium_executable():
    """Return the path of the Chromium binary patchright would launch.

    Searches the pinned ``chromium-<revision>`` folder under the registry
    root for the known executable layouts. Returns None when not found.
    Never downloads, never raises.
    """
    revision = _chromium_revision()
    if not revision:
        return None
    root = _registry_root()
    if os.name == "nt":
        candidates = (
            os.path.join(root, "chromium-%s" % revision,
                         "chrome-win64", "chrome.exe"),
            os.path.join(root, "chromium-%s" % revision,
                         "chrome-win", "chrome.exe"),
        )
    elif sys.platform == "darwin":
        candidates = (
            os.path.join(root, "chromium-%s" % revision,
                         "chrome-mac", "Chromium.app",
                         "Contents", "MacOS", "Chromium"),
        )
    else:
        candidates = (
            os.path.join(root, "chromium-%s" % revision,
                         "chrome-linux64", "chrome"),
            os.path.join(root, "chromium-%s" % revision,
                         "chrome-linux", "chrome"),
        )
    for path in candidates:
        try:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        except Exception:
            continue
    return None


def installed_chromium_version():
    """Return ``(major, full)`` of the installed Patchright Chromium, or None.

    Resolution: run the resolved Chromium binary with ``--version`` and
    parse the dotted version (e.g. ``"Google Chrome for Testing
    153.0.8010.12"`` -> ``(153, "153.0.8010.12")``).

    ``None`` means "not installed or version unknown" — callers must
    treat it as unknown, never as a failure. Never downloads, never
    raises.
    """
    exe = chromium_executable()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    try:
        output = (proc.stdout or "") + " " + (proc.stderr or "")
        match = _VERSION_RE.search(output)
        if not match:
            return None
        full = match.group(0)
        return (int(match.group(1)), full)
    except Exception:
        return None


def chromium_binary_present() -> bool:
    """Return True if a Chromium build usable by patchright is installed.

    No download is attempted.
    """
    return chromium_executable() is not None


def ensure_chromium_binary() -> bool:
    """Download the Patchright Chromium binary if missing (auto-install).

    Returns True when a usable binary exists afterwards.
    """
    if chromium_binary_present():
        return True
    import subprocess
    import sys
    try:
        subprocess.run(
            [sys.executable, "-m", "patchright", "install", "chromium",
             "--only-shell"],
            check=False, capture_output=True, timeout=1800,
        )
        # Full chromium (not just headless shell) for visible launches.
        subprocess.run(
            [sys.executable, "-m", "patchright", "install", "chromium"],
            check=False, capture_output=True, timeout=1800,
        )
    except Exception:
        pass
    return chromium_binary_present()


def install_platform_spoof(context, platform: str) -> None:
    """Install best-effort ``navigator.platform`` spoofing on a context.

    Patchright 1.63.0 disables the CDP-backed ``add_init_script`` mechanism
    (its anti-detection patches avoid ``Runtime.enable``-adjacent calls), so
    init scripts are silently dropped. This installs the next-best thing:

    * every existing and future page gets the spoof injected via
      ``page.evaluate`` as soon as the page object exists, and
    * it is re-injected on every frame navigation (a navigation creates a
      fresh JS execution context, which drops the override).

    Known limitation: scripts that read ``navigator.platform`` at
    ``document_start`` — before our post-navigation ``evaluate`` runs — may
    still observe the host platform. For maximum safety, match the persona
    OS to the host OS, or use the Camoufox engine (C++-level spoofing) for
    cross-OS personas. Documented in :mod:`src.engines` README.
    """
    script = platform_spoof_script(platform)

    def _spoof(page) -> None:
        try:
            page.evaluate(script)
        except Exception:
            pass

        def _on_navigated(frame) -> None:
            try:
                if frame == page.main_frame:
                    page.evaluate(script)
            except Exception:
                pass

        try:
            page.on("framenavigated", _on_navigated)
        except Exception:
            pass

    try:
        for existing in context.pages:
            _spoof(existing)
    except Exception:
        pass
    try:
        context.on("page", _spoof)
    except Exception:
        pass


def platform_spoof_script(platform: str) -> str:
    """Return the JS snippet that spoofs ``navigator.platform``.

    Injected via :func:`install_platform_spoof` (page-level ``evaluate`` +
    re-injection on navigation), because patchright's ``add_init_script``
    is a no-op in this build. ``json.dumps`` keeps the value safely quoted.
    """
    return (
        "Object.defineProperty(navigator, 'platform', "
        "{get: () => %s});" % json.dumps(str(platform))
    )


def _normalize_geolocation(geo) -> dict:
    """Normalize a persona geolocation dict to Playwright format.

    Accepts ``{'latitude', 'longitude'}`` (optionally ``'accuracy'``) or
    the ``{'lat', 'lng'}`` aliases.

    Raises:
        ValueError: If the dict is missing coordinates or they are not
            numeric.
    """
    lat = geo.get("latitude", geo.get("lat"))
    lng = geo.get("longitude", geo.get("lng"))
    if lat is None or lng is None:
        raise ValueError(
            "persona geolocation needs latitude/longitude (or lat/lng), "
            "got %r" % (geo,)
        )
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise ValueError(
            "persona geolocation coordinates must be numeric, got %r" % (geo,)
        )
    result = {"latitude": lat, "longitude": lng}
    if geo.get("accuracy") is not None:
        result["accuracy"] = float(geo["accuracy"])
    return result


def _playwright_proxy(proxy):
    """Convert a ProxyManager-style proxy dict to Playwright's proxy option.

    Returns None when ``proxy`` is falsy.

    Raises:
        ValueError: On an unsupported proxy type.
    """
    if not proxy:
        return None
    ptype = str(proxy.get("type", "http")).lower()
    if ptype not in _PROXY_TYPES:
        raise ValueError(
            "unsupported proxy type %r; expected one of %s"
            % (proxy.get("type"), sorted(_PROXY_TYPES))
        )
    opts = {"server": "%s://%s:%s" % (ptype, proxy["host"], proxy["port"])}
    if proxy.get("username"):
        opts["username"] = proxy["username"]
    if proxy.get("password") is not None:
        opts["password"] = proxy["password"]
    return opts


def build_context_kwargs(persona: dict, headless: bool = False) -> dict:
    """Build kwargs for ``chromium.launch_persistent_context`` from a persona.

    Pure function: performs no I/O and launches nothing.

    Always sets ``user_agent`` from the persona (the browser default leaks a
    ``HeadlessChrome`` token in headless mode) and always returns the
    platform-spoof init script separately via :func:`platform_spoof_script`
    (``navigator.platform`` is not a context option).

    Args:
        persona: Persona dict with keys ``name``, ``user_agent``,
            ``platform``, ``viewport``, ``timezone`` (optional),
            ``locale`` (optional), ``geolocation`` (or None),
            ``proxy`` (dict or None).
        headless: Passed through to the launch.

    Returns:
        Dict of kwargs ready for
        ``chromium.launch_persistent_context(user_data_dir, **kwargs)``.
        The ``user_data_dir`` key is included in the returned dict.

    Raises:
        KeyError: If ``name``, ``user_agent`` or ``platform`` is missing.
        ValueError: On an unsupported proxy type or malformed geolocation.
    """
    name = persona["name"]
    user_agent = persona["user_agent"]  # always injected: see module docstring
    if "HeadlessChrome" in user_agent:
        raise ValueError(
            "persona user_agent contains a 'HeadlessChrome' token; refusing "
            "to launch a detectable UA"
        )
    kwargs = {
        "user_data_dir": os.path.join(_paths.profiles_dir(), name),
        "user_agent": user_agent,
        "headless": headless,
    }

    viewport = persona.get("viewport")
    if viewport:
        kwargs["viewport"] = {"width": int(viewport["width"]),
                              "height": int(viewport["height"])}

    timezone = persona.get("timezone")
    if timezone:
        kwargs["timezone_id"] = timezone

    locale = persona.get("locale")
    if locale:
        kwargs["locale"] = locale

    geo = persona.get("geolocation")
    if geo:
        kwargs["geolocation"] = _normalize_geolocation(geo)
        kwargs["permissions"] = ["geolocation"]

    proxy_opts = _playwright_proxy(persona.get("proxy"))
    if proxy_opts:
        kwargs["proxy"] = proxy_opts

    # Headless-shell may be absent while the full Chromium build is present
    # (e.g. partial installs). Point the launch at the resolved full binary
    # so headless works either way; patchright runs it with --headless.
    exe = chromium_executable()
    if exe:
        kwargs["executable_path"] = exe

    # Per-profile extensions (Chromium): --load-extension with enabled .crx
    # paths. Best-effort: failures to resolve are ignored (no extensions).
    try:
        from src.extensions.manager import enabled_extension_paths
        ext_paths = enabled_extension_paths(name)
        if ext_paths:
            args = list(kwargs.get("args", []))
            args.append("--load-extension=" + ",".join(ext_paths))
            kwargs["args"] = args
    except Exception:
        pass

    return kwargs


class LaunchedPatchrightProfile:
    """Wrapper around a launched patchright profile.

    Same surface as :class:`src.browser.launcher.LaunchedProfile`:
    ``.page`` / ``.browser`` / ``.close()`` plus context-manager support.
    For persistent launches ``.browser`` is the Playwright
    ``BrowserContext``; ``close()`` also stops the patchright session.
    """

    def __init__(self, playwright, context, page):
        """Wrap an already-started patchright session and context.

        Args:
            playwright: The ``sync_playwright().start()`` instance.
            context: The persistent ``BrowserContext``.
            page: A fresh ``Page`` from that context.
        """
        self._playwright = playwright
        self._context = context
        self._page = page
        self._closed = False

    @property
    def page(self):
        """The Playwright page for this profile."""
        return self._page

    @property
    def browser(self):
        """The underlying persistent ``BrowserContext``."""
        return self._context

    def close(self):
        """Close the profile cleanly: page, context, patchright session.

        Safe to call more than once.
        """
        if self._closed:
            return
        self._closed = True
        for fn in (self._page.close, self._context.close):
            try:
                fn()
            except Exception:
                pass
        try:
            self._playwright.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def _ensure_node():
    """Point patchright at a Node runtime.

    The vendored ``driver/node`` binary (121MB) is intentionally NOT in git
    (GitHub's blob API rejects it); a backup lives in
    ``~/workspace/backups/patchright-node-linux-x64``. Patchright honors
    ``PLAYWRIGHT_NODEJS_PATH``, so if the bundled binary is missing we fall
    back to a system node — and if neither exists we raise a clear error.
    """
    import shutil

    if os.environ.get("PLAYWRIGHT_NODEJS_PATH"):
        return
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    bundled = os.path.join(root, "vendor", "patchright", "driver", "node")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return
    system_node = shutil.which("node")
    if system_node:
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = system_node
        return
    raise RuntimeError(
        "No Node.js runtime for patchright: vendored driver/node is absent "
        "and no system 'node' found. Restore ~/workspace/backups/"
        "patchright-node-linux-x64 to vendor/patchright/driver/node, or "
        "install Node.js, or set PLAYWRIGHT_NODEJS_PATH."
    )


class PatchrightEngine(Engine):
    """Chromium engine via the patched patchright driver."""

    name = "patchright"

    def launch_profile(self, profile, headless=False):
        """Launch a persistent Chromium context for ``profile``.

        Creates the per-profile user-data dir
        ``~/.antidetect-browser/profiles/<name>/`` so cookies, storage and
        logins survive across runs of the same profile name. Always
        injects the persona UA and always installs the platform-spoof
        init script (see module docstring for why).

        Raises:
            KeyError / ValueError: From :func:`build_context_kwargs` on a
                bad persona.
            Exception: If the Chromium binary is missing (install with
                ``PYTHONPATH=vendor .venv/bin/python -m patchright install
                chromium``) or the launch otherwise fails.
        """
        from patchright.sync_api import sync_playwright

        _ensure_node()
        kwargs = build_context_kwargs(profile, headless=headless)
        user_data_dir = kwargs.pop("user_data_dir")
        os.makedirs(user_data_dir, exist_ok=True)

        playwright = sync_playwright().start()
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir, **kwargs
            )
        except Exception:
            try:
                playwright.stop()
            except Exception:
                pass
            raise
        # Platform is not a context option, and patchright's add_init_script
        # is a no-op in this build: use best-effort page-level injection.
        install_platform_spoof(context, profile["platform"])
        # Fingerprint noise (fingerprint-toolkit, MIT): canvas/WebGL/audio
        # spoofing injected at page level, deterministic per profile.
        try:
            from src.fingerprint.noise import injection_script
            _noise_js = injection_script(profile["name"])
            _orig_new_page = context.new_page

            def _new_page_with_noise(*a, **kw):
                pg = _orig_new_page(*a, **kw)
                try:
                    pg.add_init_script(_noise_js)
                except Exception:
                    pass
                return pg

            context.new_page = _new_page_with_noise
        except Exception:
            pass
        # Comprehensive fingerprint spoofer (browser-fingerprint-spoofer,
        # MIT): navigator/screen/WebGL/canvas/WebRTC/sensors/timezone/fonts.
        # Injected at page level, configured from the profile fingerprint.
        try:
            from src.fingerprint.spoofer import build_injection_script
            _spoof_js = build_injection_script(profile.get("fingerprint")
                                               or profile)
            _orig_new_page2 = context.new_page

            def _new_page_with_spoof(*a, **kw):
                pg = _orig_new_page2(*a, **kw)
                try:
                    pg.add_init_script(_spoof_js)
                except Exception:
                    pass
                return pg

            context.new_page = _new_page_with_spoof
        except Exception:
            pass
        try:
            page = context.new_page()
        except Exception:
            try:
                context.close()
            except Exception:
                pass
            try:
                playwright.stop()
            except Exception:
                pass
            raise
        # tf-playwright-stealth (MIT): extra anti-detection init scripts
        # for the Patchright/Chromium engine (Camoufox has its own).
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except Exception:
            pass
        return LaunchedPatchrightProfile(playwright, context, page)

    def installed_version(self):
        """``(major, full)`` of the installed Patchright Chromium, or None."""
        return installed_chromium_version()

    def binary_present(self):
        """True when a Chromium build usable by patchright is installed."""
        return chromium_binary_present()

    def ensure_binary(self):
        """Download the Chromium binary if missing (auto-install)."""
        return ensure_chromium_binary()
