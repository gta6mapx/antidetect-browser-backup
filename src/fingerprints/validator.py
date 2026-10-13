"""Consistency checks for anti-detect browser personas.

Every check validates that the fields of a generated persona are mutually
consistent (OS token in UA matches the declared OS, WebGL vendor matches the
OS, timezone matches locale region, and so on). This module is pure logic:
no I/O, no network, stdlib only (``re`` and ``zoneinfo``), except for check
``ua_matches_installed_browser``, which lazily queries
:func:`src.browser.launcher.installed_firefox_version` (itself I/O-free
apart from reading the local browser dir) and never fails when the browser
is not installed.

Engine dispatch: :func:`validate` routes on ``profile['engine']`` —
the full 23 Firefox checks (:class:`ConsistencyValidator`) for camoufox
profiles (the default), and a Chromium-appropriate subset
(:class:`ChromiumConsistencyValidator`) for patchright profiles. The
Chromium checks assert a Chrome UA (never a ``HeadlessChrome`` token)
whose major matches the installed Patchright Chromium, platform↔OS
consistency, and the generic automation-tell expectations; they
deliberately skip fingerprint-spoof fields (WebGL/fonts) because
patchright does not spoof them — its posture is honest, internally
consistent stock-Chromium signals.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from zoneinfo import available_timezones

# Mapping of common IANA timezones to their ISO-3166-1 alpha-2 country code,
# plus a coarse lat/lon bounding box (min_lat, max_lat, min_lon, max_lon)
# used for geolocation plausibility. Boxes are deliberately rough.
_TIMEZONE_COUNTRIES: Dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "America/New_York": ("US", (24.0, 50.0, -126.0, -66.0)),
    "America/Chicago": ("US", (24.0, 50.0, -126.0, -66.0)),
    "America/Denver": ("US", (24.0, 50.0, -126.0, -66.0)),
    "America/Los_Angeles": ("US", (24.0, 50.0, -126.0, -66.0)),
    "America/Anchorage": ("US", (24.0, 72.0, -126.0, -66.0)),
    "America/Toronto": ("CA", (42.0, 72.0, -142.0, -52.0)),
    "America/Vancouver": ("CA", (42.0, 72.0, -142.0, -52.0)),
    "America/Mexico_City": ("MX", (14.0, 33.0, -118.0, -86.0)),
    "America/Sao_Paulo": ("BR", (-34.0, 6.0, -74.0, -34.0)),
    "Europe/London": ("GB", (49.0, 59.0, -6.0, 2.0)),
    "Europe/Paris": ("FR", (41.0, 52.0, -6.0, 10.0)),
    "Europe/Berlin": ("DE", (47.0, 55.0, 6.0, 15.0)),
    "Europe/Madrid": ("ES", (35.0, 44.0, -10.0, 5.0)),
    "Europe/Rome": ("IT", (35.0, 48.0, 6.0, 19.0)),
    "Europe/Amsterdam": ("NL", (50.0, 54.0, 3.0, 8.0)),
    "Europe/Stockholm": ("SE", (55.0, 70.0, 11.0, 25.0)),
    "Europe/Dublin": ("IE", (51.0, 56.0, -11.0, -5.0)),
    "Europe/Zurich": ("CH", (45.0, 48.0, 6.0, 11.0)),
    "Europe/Vienna": ("AT", (46.0, 49.0, 9.0, 18.0)),
    "Asia/Dubai": ("AE", (22.0, 27.0, 51.0, 57.0)),
    "Asia/Singapore": ("SG", (1.0, 2.0, 103.0, 105.0)),
    "Asia/Tokyo": ("JP", (30.0, 46.0, 129.0, 146.0)),
    "Asia/Kolkata": ("IN", (8.0, 36.0, 68.0, 98.0)),
    "Australia/Sydney": ("AU", (-40.0, -10.0, 113.0, 154.0)),
    "Australia/Melbourne": ("AU", (-40.0, -10.0, 113.0, 154.0)),
    "Pacific/Auckland": ("NZ", (-48.0, -34.0, 166.0, 179.0)),
}

_UA_OS_TOKENS: Dict[str, str] = {
    "windows": "Windows NT",
    "macos": "Macintosh",
    "linux": "Linux",
}

_PLATFORM_FOR_OS: Dict[str, str] = {
    "windows": "Win32",
    "macos": "MacIntel",
    "linux": "Linux x86_64",
}

_WINDOWS_ONLY_FONTS = frozenset({
    "Calibri", "Segoe UI", "Agency FB", "Cambria", "Candara", "Consolas",
    "Constantia", "Corbel", "Segoe UI Light", "Segoe UI Semibold",
})

_APPLE_ONLY_FONTS = frozenset({
    "Helvetica", "Geneva", "San Francisco", "SF Pro", "SF Pro Text",
    "New York", "Menlo", "Lucida Grande", "Apple Chancery",
})

_COMMON_RESOLUTIONS = frozenset({
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900), (2560, 1440),
    (1280, 720), (1600, 900), (1920, 1200), (3440, 1440), (1280, 800),
    (1512, 982), (1728, 1117), (2560, 1600), (3840, 2160), (1024, 768),
})

_VALID_DEVICE_MEMORY = frozenset({0.5, 1, 2, 4, 8, 16, 32, 64})
_VALID_COLOR_DEPTHS = frozenset({24, 30})
_VALID_PROXY_TYPES = frozenset({"http", "https", "socks5", "socks4"})
_VALID_OS_VALUES = frozenset({"windows", "macos", "linux"})

_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_FIREFOX_RE = re.compile(r"Firefox/(\d+)")
_CHROME_RE = re.compile(r"Chrome/(\d+)")


def _result(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    """Build the canonical per-check result dict."""
    return {"check": name, "passed": passed, "detail": detail}


class ConsistencyValidator:
    """Runs a fixed set of consistency checks against a persona dict.

    ``validate`` never raises on bad persona data: every check is wrapped
    in try/except and a failing check yields ``passed=False`` with an
    explanatory detail string.
    """

    def _get(self, persona: Dict[str, Any], key: str) -> Any:
        """Fetch a persona field, returning None instead of raising."""
        if not isinstance(persona, dict):
            return None
        return persona.get(key)

    # -- individual checks ------------------------------------------------

    def check_ua_os_token(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """UA must contain the OS token for persona['os'] and not the others'."""
        name = "ua_os_token"
        os_name = self._get(persona, "os")
        ua = self._get(persona, "user_agent") or ""
        if os_name not in _UA_OS_TOKENS:
            return _result(name, False, f"unknown os value: {os_name!r}")
        expected = _UA_OS_TOKENS[os_name]
        if expected not in ua:
            return _result(name, False, f"UA missing OS token {expected!r} for os={os_name!r}")
        wrong = [tok for other, tok in _UA_OS_TOKENS.items() if other != os_name and tok in ua]
        if wrong:
            return _result(name, False, f"UA contains other OS tokens: {wrong}")
        return _result(name, True, f"UA contains {expected!r}, no conflicting OS tokens")

    def check_ua_firefox_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """UA must contain Firefox/<major> with major >= 100 (Camoufox)."""
        name = "ua_firefox_sane"
        ua = self._get(persona, "user_agent") or ""
        m = _FIREFOX_RE.search(ua)
        if not m:
            return _result(name, False, "UA does not contain a Firefox/<version> token")
        major = int(m.group(1))
        if major < 100:
            return _result(name, False, f"Firefox major version {major} < 100")
        return _result(name, True, f"Firefox major version {major} is sane")

    def check_platform_matches_os(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """navigator.platform must match the declared OS."""
        name = "platform_matches_os"
        os_name = self._get(persona, "os")
        platform = self._get(persona, "platform")
        if os_name not in _PLATFORM_FOR_OS:
            return _result(name, False, f"unknown os value: {os_name!r}")
        expected = _PLATFORM_FOR_OS[os_name]
        if platform == expected:
            return _result(name, True, f"platform {platform!r} matches os={os_name!r}")
        return _result(name, False, f"platform {platform!r} != expected {expected!r} for os={os_name!r}")

    def check_webgl_vendor_matches_os(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """WebGL vendor string must be plausible for the declared OS."""
        name = "webgl_vendor_matches_os"
        os_name = self._get(persona, "os")
        vendor = self._get(persona, "webgl_vendor") or ""
        renderer = self._get(persona, "webgl_renderer") or ""
        if os_name == "macos":
            ok = "Apple" in vendor
            detail = "vendor contains 'Apple'" if ok else f"macOS vendor should contain 'Apple', got {vendor!r}"
        elif os_name == "windows":
            gpu_ok = any(tok in vendor for tok in ("NVIDIA", "Intel", "AMD", "Qualcomm"))
            angle_ok = vendor == "Google Inc." and "ANGLE" in renderer
            ok = gpu_ok or angle_ok
            detail = (f"vendor {vendor!r} plausible for windows"
                      if ok else f"windows vendor {vendor!r} not Google Inc./NVIDIA/Intel/AMD/Qualcomm")
        elif os_name == "linux":
            ok = any(tok in vendor for tok in ("Mesa", "Intel", "AMD", "NVIDIA", "X.Org"))
            detail = (f"vendor {vendor!r} plausible for linux"
                      if ok else f"linux vendor {vendor!r} not Mesa/Intel/AMD/NVIDIA/X.Org")
        else:
            ok = False
            detail = f"unknown os value: {os_name!r}"
        return _result(name, ok, detail)

    def check_webgl_renderer_os_consistent(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Renderer backend must be consistent with the OS."""
        name = "webgl_renderer_os_consistent"
        os_name = self._get(persona, "os")
        renderer = self._get(persona, "webgl_renderer") or ""
        if "Direct3D" in renderer and os_name != "windows":
            return _result(name, False, f"'Direct3D' renderer only valid on windows, got os={os_name!r}")
        if "Apple GPU" in renderer and os_name != "macos":
            return _result(name, False, f"'Apple GPU' renderer only valid on macos, got os={os_name!r}")
        if "ANGLE" in renderer and os_name not in ("windows", "linux"):
            return _result(name, False, f"ANGLE renderer not expected on os={os_name!r}")
        return _result(name, True, f"renderer {renderer!r} consistent with os={os_name!r}")

    def check_fonts_os_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Font list must be non-empty and contain OS-typical fonts."""
        name = "fonts_os_sane"
        fonts = self._get(persona, "fonts")
        os_name = self._get(persona, "os")
        if not isinstance(fonts, list) or not fonts:
            return _result(name, False, "font list is empty or missing")
        font_set = set(fonts)
        if os_name == "windows":
            hit = font_set & _WINDOWS_ONLY_FONTS
            if hit:
                return _result(name, True, f"windows-only font present: {sorted(hit)[0]!r}")
            return _result(name, False, "no Windows-only font (e.g. Calibri, Segoe UI) in list")
        if os_name == "macos":
            hit = font_set & _APPLE_ONLY_FONTS
            if hit:
                return _result(name, True, f"Apple font present: {sorted(hit)[0]!r}")
            return _result(name, False, "no Apple font (e.g. Helvetica, Geneva) in list")
        if os_name == "linux":
            return _result(name, True, f"{len(fonts)} fonts listed for linux")
        return _result(name, False, f"unknown os value: {os_name!r}")

    def check_timezone_valid(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Timezone must be a real IANA zone name."""
        name = "timezone_valid"
        tz = self._get(persona, "timezone")
        if isinstance(tz, str) and tz in available_timezones():
            return _result(name, True, f"{tz!r} is a valid IANA timezone")
        return _result(name, False, f"{tz!r} is not a valid IANA timezone")

    def check_locale_format(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Locale must match xx-XX form (e.g. en-US)."""
        name = "locale_format"
        locale = self._get(persona, "locale") or ""
        if _LOCALE_RE.match(locale):
            return _result(name, True, f"locale {locale!r} format OK")
        return _result(name, False, f"locale {locale!r} does not match xx-XX format")

    def check_timezone_locale_region(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Country from locale should match country mapped from timezone."""
        name = "timezone_locale_region"
        tz = self._get(persona, "timezone")
        locale = self._get(persona, "locale") or ""
        if tz not in _TIMEZONE_COUNTRIES:
            return _result(name, True, f"timezone {tz!r} not in mapping table, skipped")
        country = _TIMEZONE_COUNTRIES[tz][0]
        parts = locale.split("-")
        locale_country = parts[1] if len(parts) == 2 else ""
        if locale_country == country:
            return _result(name, True, f"locale country {locale_country} matches timezone country {country}")
        return _result(name, False, f"locale country {locale_country!r} != timezone country {country!r}")

    def check_geolocation_timezone_plausible(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Geolocation must fall inside the coarse box for the timezone country."""
        name = "geolocation_timezone_plausible"
        geo = self._get(persona, "geolocation")
        if geo is None:
            return _result(name, True, "no geolocation set")
        tz = self._get(persona, "timezone")
        if tz not in _TIMEZONE_COUNTRIES:
            return _result(name, True, f"timezone {tz!r} not in mapping table, country unknown")
        country, (min_lat, max_lat, min_lon, max_lon) = _TIMEZONE_COUNTRIES[tz]
        lat = geo.get("latitude") if isinstance(geo, dict) else None
        lon = geo.get("longitude") if isinstance(geo, dict) else None
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return _result(name, False, f"geolocation missing numeric latitude/longitude: {geo!r}")
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return _result(name, True, f"({lat}, {lon}) inside {country} bounding box")
        return _result(name, False, f"({lat}, {lon}) outside {country} bounding box")

    def check_viewport_within_screen(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Viewport must fit inside the screen and be positive."""
        name = "viewport_within_screen"
        vp = self._get(persona, "viewport") or {}
        sc = self._get(persona, "screen") or {}
        vw, vh = vp.get("width"), vp.get("height")
        sw, sh = sc.get("width"), sc.get("height")
        for label, v in (("viewport.width", vw), ("viewport.height", vh),
                         ("screen.width", sw), ("screen.height", sh)):
            if not isinstance(v, (int, float)) or v <= 0:
                return _result(name, False, f"{label} must be a positive number, got {v!r}")
        if vw <= sw and vh <= sh:
            return _result(name, True, f"viewport {int(vw)}x{int(vh)} fits screen {int(sw)}x{int(sh)}")
        return _result(name, False, f"viewport {int(vw)}x{int(vh)} exceeds screen {int(sw)}x{int(sh)}")

    def check_viewport_aspect_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Viewport aspect ratio should be in [1.2, 2.2]."""
        name = "viewport_aspect_sane"
        vp = self._get(persona, "viewport") or {}
        w, h = vp.get("width"), vp.get("height")
        if not isinstance(w, (int, float)) or not isinstance(h, (int, float)) or h <= 0:
            return _result(name, False, f"bad viewport dimensions: width={w!r}, height={h!r}")
        ratio = w / h
        if 1.2 <= ratio <= 2.2:
            return _result(name, True, f"aspect ratio {ratio:.2f} in [1.2, 2.2]")
        return _result(name, False, f"aspect ratio {ratio:.2f} outside [1.2, 2.2]")

    def check_screen_resolution_common(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Screen resolution must be one of the common desktop resolutions."""
        name = "screen_resolution_common"
        sc = self._get(persona, "screen") or {}
        w, h = sc.get("width"), sc.get("height")
        if (w, h) in _COMMON_RESOLUTIONS:
            return _result(name, True, f"screen {w}x{h} is a common resolution")
        return _result(name, False, f"screen {w}x{h} not in common-resolution list")

    def check_hardware_concurrency_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """hardwareConcurrency must be an int in 2..64."""
        name = "hardware_concurrency_sane"
        hc = self._get(persona, "hardware_concurrency")
        if isinstance(hc, bool) or not isinstance(hc, int):
            return _result(name, False, f"hardware_concurrency must be int, got {hc!r}")
        if 2 <= hc <= 64:
            return _result(name, True, f"hardware_concurrency={hc} in 2..64")
        return _result(name, False, f"hardware_concurrency={hc} outside 2..64")

    def check_device_memory_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """deviceMemory must be one of the real bucket values."""
        name = "device_memory_sane"
        dm = self._get(persona, "device_memory")
        if isinstance(dm, bool) or not isinstance(dm, (int, float)):
            return _result(name, False, f"device_memory must be numeric, got {dm!r}")
        if float(dm) in _VALID_DEVICE_MEMORY:
            return _result(name, True, f"device_memory={dm} is a valid bucket")
        return _result(name, False, f"device_memory={dm} not in {sorted(_VALID_DEVICE_MEMORY)}")

    def check_touch_points_desktop_zero(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Desktop-only product: maxTouchPoints must be 0."""
        name = "touch_points_desktop_zero"
        tp = self._get(persona, "touch_points")
        if tp == 0:
            return _result(name, True, "touch_points=0 (desktop)")
        return _result(name, False, f"touch_points={tp!r} != 0 for a desktop persona")

    def check_color_depth_valid(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """colorDepth must be 24 or 30."""
        name = "color_depth_valid"
        cd = self._get(persona, "color_depth")
        if cd in _VALID_COLOR_DEPTHS:
            return _result(name, True, f"color_depth={cd} valid")
        return _result(name, False, f"color_depth={cd!r} not in {sorted(_VALID_COLOR_DEPTHS)}")

    def check_canvas_seed_present(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """canvas_seed must be a non-empty string."""
        name = "canvas_seed_present"
        seed = self._get(persona, "canvas_seed")
        if isinstance(seed, str) and seed:
            return _result(name, True, "canvas_seed present")
        return _result(name, False, f"canvas_seed must be a non-empty string, got {seed!r}")

    def check_proxy_shape_valid(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Proxy dict (if set) must have host, port 1..65535, and a valid type."""
        name = "proxy_shape_valid"
        proxy = self._get(persona, "proxy")
        if proxy is None:
            return _result(name, True, "direct connection (no proxy)")
        if not isinstance(proxy, dict):
            return _result(name, False, f"proxy must be a dict or None, got {type(proxy).__name__}")
        host = proxy.get("host")
        if not isinstance(host, str) or not host:
            return _result(name, False, f"proxy host must be a non-empty string, got {host!r}")
        port = proxy.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            return _result(name, False, f"proxy port must be int in 1..65535, got {port!r}")
        ptype = proxy.get("type")
        if ptype not in _VALID_PROXY_TYPES:
            return _result(name, False, f"proxy type {ptype!r} not in {sorted(_VALID_PROXY_TYPES)}")
        return _result(name, True, f"proxy {ptype}://{host}:{port} shape valid")

    def check_proxy_webrtc_managed(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """WebRTC leak handling is owned by the launcher when a proxy is set."""
        name = "proxy_webrtc_managed"
        proxy = self._get(persona, "proxy")
        if proxy is not None:
            return _result(name, True, "launcher disables WebRTC when proxy is set")
        return _result(name, True, "no proxy, WebRTC managed as direct")

    def check_name_present(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Persona name must be a non-empty string."""
        name = "name_present"
        pname = self._get(persona, "name")
        if isinstance(pname, str) and pname:
            return _result(name, True, f"name {pname!r} present")
        return _result(name, False, f"name must be a non-empty string, got {pname!r}")

    def check_os_value_valid(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """os must be one of windows/macos/linux."""
        name = "os_value_valid"
        os_name = self._get(persona, "os")
        if os_name in _VALID_OS_VALUES:
            return _result(name, True, f"os={os_name!r} valid")
        return _result(name, False, f"os {os_name!r} not in {sorted(_VALID_OS_VALUES)}")

    def check_ua_matches_installed_browser(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Persona UA major must match the installed Camoufox browser major.

        Passes with a note (never fails) when the browser binary is not
        installed or its version cannot be determined — an unknown version
        is not an inconsistency.
        """
        name = "ua_matches_installed_browser"
        try:
            from src.browser.launcher import installed_firefox_version
            installed = installed_firefox_version()
        except Exception as exc:  # noqa: BLE001 - version lookup must not fail validation
            return _result(name, True, f"version lookup failed ({exc}); skipped")
        if installed is None:
            return _result(name, True, "browser binary not installed; version match skipped")
        installed_major, installed_full = installed
        ua = self._get(persona, "user_agent") or ""
        m = _FIREFOX_RE.search(ua)
        if not m:
            return _result(name, False, "UA has no Firefox/<version> token to compare")
        ua_major = int(m.group(1))
        if ua_major == installed_major:
            return _result(name, True,
                            f"UA Firefox/{ua_major} matches installed browser {installed_full}")
        return _result(name, False,
                        f"UA Firefox/{ua_major} != installed browser {installed_full} "
                        f"(major {installed_major})")

    # -- Chromium (patchright engine) checks --------------------------------

    def check_ua_chrome_sane(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """UA must be a Chrome UA with major >= 100 and no HeadlessChrome token.

        The ``HeadlessChrome`` token is the default headless UA leak our
        launcher always overrides; a persona carrying it would launch
        detectably.
        """
        name = "ua_chrome_sane"
        ua = self._get(persona, "user_agent") or ""
        if "HeadlessChrome" in ua:
            return _result(name, False,
                            "UA contains the 'HeadlessChrome' headless leak token")
        m = _CHROME_RE.search(ua)
        if not m:
            return _result(name, False,
                            "UA does not contain a Chrome/<version> token")
        major = int(m.group(1))
        if major < 100:
            return _result(name, False, f"Chrome major version {major} < 100")
        return _result(name, True, f"Chrome major version {major} is sane")

    def check_ua_matches_installed_chromium(
            self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Persona UA Chrome major must match the installed Patchright Chromium.

        Passes with a note (never fails) when the Chromium binary is not
        installed or its version cannot be determined — an unknown version
        is not an inconsistency. Mirrors ``ua_matches_installed_browser``
        for the Firefox engine.
        """
        name = "ua_matches_installed_chromium"
        try:
            from src.engines.patchright_engine import installed_chromium_version
            installed = installed_chromium_version()
        except Exception as exc:  # noqa: BLE001 - version lookup must not fail validation
            return _result(name, True, f"version lookup failed ({exc}); skipped")
        if installed is None:
            return _result(name, True,
                            "chromium binary not installed; version match skipped")
        installed_major, installed_full = installed
        ua = self._get(persona, "user_agent") or ""
        if "HeadlessChrome" in ua:
            return _result(name, False,
                            "UA contains the 'HeadlessChrome' headless leak token")
        m = _CHROME_RE.search(ua)
        if not m:
            return _result(name, False, "UA has no Chrome/<version> token to compare")
        ua_major = int(m.group(1))
        if ua_major == installed_major:
            return _result(name, True,
                            f"UA Chrome/{ua_major} matches installed chromium {installed_full}")
        return _result(name, False,
                        f"UA Chrome/{ua_major} != installed chromium {installed_full} "
                        f"(major {installed_major})")

    def check_webdriver_false(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Automation tell ``navigator.webdriver`` must be false.

        Generic across engines: both camoufox and patchright hide this
        tell at launch. The persona dict carries no webdriver field, so a
        missing/False value passes; an explicit True fails.
        """
        name = "webdriver_false"
        value = self._get(persona, "webdriver")
        if value is None or value is False:
            return _result(name, True,
                            "navigator.webdriver hidden by the engine (no opt-out in persona)")
        return _result(name, False,
                        f"persona declares webdriver={value!r}; engine must hide it")

    # -- runner ------------------------------------------------------------

    def _run_checks(self, persona: Dict[str, Any],
                    check_names: List[str]) -> List[Dict[str, Any]]:
        """Run ``check_names`` against ``persona``.

        Every check is wrapped in try/except: a check that raises is
        reported as ``passed=False`` with the exception in the detail, so
        one bad check can never crash the whole run.
        """
        results: List[Dict[str, Any]] = []
        for name in check_names:
            method = getattr(self, "check_" + name)
            try:
                result = method(persona)
            except Exception as exc:  # noqa: BLE001 - defensive catch is the point
                result = _result(name, False, f"check raised {type(exc).__name__}: {exc}")
            results.append(result)
        return results

    def validate(self, persona: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run all consistency checks against ``persona``.

        Returns a list of ``{'check': str, 'passed': bool, 'detail': str}``
        dicts, one per check in ``CHECK_NAMES`` order. A check that raises is
        caught and reported as ``passed=False`` with the exception in the
        detail, so one bad check can never crash the whole run.
        """
        return self._run_checks(persona, CHECK_NAMES)


#: Ordered list of all check names run by :meth:`ConsistencyValidator.validate`.
CHECK_NAMES: List[str] = [
    "ua_os_token",
    "ua_firefox_sane",
    "platform_matches_os",
    "webgl_vendor_matches_os",
    "webgl_renderer_os_consistent",
    "fonts_os_sane",
    "timezone_valid",
    "locale_format",
    "timezone_locale_region",
    "geolocation_timezone_plausible",
    "viewport_within_screen",
    "viewport_aspect_sane",
    "screen_resolution_common",
    "hardware_concurrency_sane",
    "device_memory_sane",
    "touch_points_desktop_zero",
    "color_depth_valid",
    "canvas_seed_present",
    "proxy_shape_valid",
    "proxy_webrtc_managed",
    "name_present",
    "os_value_valid",
    "ua_matches_installed_browser",
]


#: Ordered list of check names run for patchright (Chromium) profiles.
#: Inherits the generic coherence checks from :class:`ConsistencyValidator`
#: and replaces the Firefox-specific UA checks with Chromium ones. WebGL /
#: fonts checks are deliberately absent: patchright does not spoof those
#: signals, so there is nothing engine-specific to assert about them.
CHROMIUM_CHECK_NAMES: List[str] = [
    "ua_os_token",
    "ua_chrome_sane",
    "platform_matches_os",
    "timezone_valid",
    "locale_format",
    "timezone_locale_region",
    "geolocation_timezone_plausible",
    "viewport_within_screen",
    "viewport_aspect_sane",
    "screen_resolution_common",
    "hardware_concurrency_sane",
    "device_memory_sane",
    "touch_points_desktop_zero",
    "color_depth_valid",
    "canvas_seed_present",
    "proxy_shape_valid",
    "proxy_webrtc_managed",
    "name_present",
    "os_value_valid",
    "ua_matches_installed_chromium",
    "webdriver_false",
]


class ChromiumConsistencyValidator(ConsistencyValidator):
    """Consistency checks for patchright (Chromium) personas.

    Reuses every generic check from :class:`ConsistencyValidator`; only the
    UA-family checks differ (Chrome instead of Firefox). ``validate``
    runs ``CHROMIUM_CHECK_NAMES`` instead of ``CHECK_NAMES``.
    """

    def validate(self, persona: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run the Chromium check set against ``persona``.

        Returns a list of ``{'check': str, 'passed': bool, 'detail': str}``
        dicts, one per check in ``CHROMIUM_CHECK_NAMES`` order.
        """
        return self._run_checks(persona, CHROMIUM_CHECK_NAMES)


def validate(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a profile persona, dispatching on ``profile['engine']``.

    - ``engine == "patchright"`` (or the ``"chromium"`` alias): the
      Chromium check set via :class:`ChromiumConsistencyValidator`.
    - anything else (``"camoufox"``, missing, unknown): the full 23
      Firefox checks via :class:`ConsistencyValidator` (safe default —
      unknown engines fall back to the strictest set).

    Never raises on bad persona data: per-check failures are caught by
    the validators themselves.
    """
    engine = ""
    if isinstance(profile, dict):
        engine = str(profile.get("engine") or "").strip().lower()
    if engine in ("patchright", "chromium", "chrome"):
        return ChromiumConsistencyValidator().validate(profile)
    return ConsistencyValidator().validate(profile)
