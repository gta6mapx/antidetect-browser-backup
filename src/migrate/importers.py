"""Import profiles from other anti-detect browsers (AdsPower / GoLogin / Multilogin).

Portions of this module are vendored VERBATIM from persona-studio
(https://github.com/anhnnp/persona-studio, MIT License,
Copyright (c) 2025 Ahsan, commit d46e984cfbdcd7278063e793e6ab1ad95b8cdc78):
``_first``, ``_norm_os``, ``_parse_resolution``, ``_parse_proxy`` and
``load_export`` — stdlib-only helpers with no persona-studio coupling (the
upstream ``Proxy`` model was replaced by a plain dict). See
``vendor/persona-import/ATTRIBUTION.md``.

Deliberate changes vs upstream (documented, not silent):
  * ``_unwrap`` additionally accepts the real AdsPower Local API nesting
    ``{"data": {"list": [...]}}`` (upstream only checked one level deep).
  * ``_parse_proxy`` returns a plain dict instead of the persona-studio
    ``Proxy`` model (same fields otherwise).
  * ``profile_from_export`` is an ADAPTATION: instead of building a
    persona-studio ``Profile``/``Fingerprint`` it builds a persona through
    our :meth:`src.profiles.manager.ProfileManager.create`, registers any
    imported proxy with :meth:`src.proxy.manager.ProxyManager.add`, and maps
    ``group_name``/``tags`` onto our ``client_tag``. It returns
    ``(persona_dict, issues)`` where ``issues`` is a list of human-readable
    notes about mappings that needed a judgement call.

GoLogin and Multilogin shapes are handled best-effort through the same
tolerant field aliasing (see ``src/migrate/README.md`` for the supported
field shapes). An import is a starting point to review, not a black box.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

import json
from pathlib import Path
from typing import Optional

from src.profiles.manager import ProfileManager
from src.proxy.manager import ProxyManager

# ---------------------------------------------------------------------------
# Vendored VERBATIM from persona-studio engine/persona/importers.py
# (MIT, Copyright (c) 2025 Ahsan). Only _parse_proxy's return type changed
# (Proxy model -> plain dict).
# ---------------------------------------------------------------------------


def _first(d: dict, *names, default=None):
    """First present, non-empty value among ``names`` (supports 'a.b' paths)."""
    for name in names:
        node = d
        for part in name.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if node not in (None, "", [], {}):
            return node
    return default


def _norm_os(value: Optional[str], ua: str = "") -> str:
    text = f"{value or ''} {ua}".lower()
    if "mac" in text or "os x" in text or "darwin" in text:
        return "macos"
    if "android" in text:
        return "android"
    if "linux" in text and "android" not in text:
        return "linux"
    return "windows"


def _parse_resolution(value) -> tuple:
    try:
        w, h = (int(x) for x in str(value).lower().replace(" ", "").split("x")[:2])
        return w, h
    except (ValueError, AttributeError):
        return 1920, 1080


def _parse_proxy(d: dict) -> Optional[dict]:
    """Parse the export's proxy block into a plain dict.

    Verbatim logic from upstream; the persona-studio ``Proxy`` model was
    replaced by a dict with the same data: ``server`` is ``"scheme://host"``
    (port appended when present), plus ``username`` / ``password`` /
    ``country``. Returns ``None`` when the export carries no usable proxy
    (or explicitly says ``none`` / ``no_proxy`` / ``direct``).
    """
    px = _first(d, "proxy", "proxyConfig", "user_proxy_config", default=None)
    if not isinstance(px, dict):
        return None
    host = _first(px, "host", "ip", "proxy_host", "addr")
    if not host:
        return None
    # "mode: none" / "type: no_proxy" means the profile is really direct.
    mode = str(_first(px, "mode", "type", "proxy_type", "proxy_soft", default="")).lower()
    if mode in ("none", "no_proxy", "direct", ""):
        if not _first(px, "type", "proxy_type"):
            return None
    scheme = "socks5" if "socks" in mode else "http"
    port = _first(px, "port", "proxy_port", default="")
    return {
        "server": f"{scheme}://{host}:{port}".rstrip(":"),
        "username": _first(px, "username", "user", "proxy_user", "login"),
        "password": _first(px, "password", "pass", "proxy_password"),
        "country": _first(px, "country", "geo"),
    }


def _unwrap(data) -> list:
    """Pull the list of profiles out of whatever the export wraps them in.

    PATCHED vs upstream: also handles the real AdsPower Local API nesting
    ``{"data": {"list": [...]}}`` (``GET /api/v1/user/list``) — upstream
    only checked one level deep.
    """
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "profiles", "list", "items", "rows"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
        # Real AdsPower wire format: {"data": {"list": [...]}}.
        data_inner = data.get("data")
        if isinstance(data_inner, dict):
            for key in ("list", "profiles", "items", "rows"):
                sub = data_inner.get(key)
                if isinstance(sub, list):
                    return [x for x in sub if isinstance(x, dict)]
        return [data]   # a single profile object
    return []


def load_export(path) -> list:
    """Read a JSON export file and return the list of raw profile dicts."""
    return _unwrap(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Adaptation layer: persona-studio Profile -> our ProfileManager / ProxyManager
# ---------------------------------------------------------------------------

_PROXY_TYPES = ("http", "https", "socks5", "socks4")

# persona-studio os values -> our ProfileManager os values. Our manager only
# accepts windows/macos/linux, so android falls back to linux (closest).
_OS_MAP = {"windows": "windows", "macos": "macos", "linux": "linux",
           "android": "linux"}

# our os -> navigator.platform default when the export does not say.
_PLATFORM_DEFAULTS = {"windows": "Win32", "macos": "MacIntel",
                      "linux": "Linux x86_64"}

_CHROME_HEIGHT = 88  # pixels reserved for browser chrome (viewport < screen)


def _split_proxy(parsed: dict) -> Optional[dict]:
    """Turn ``_parse_proxy`` output into our proxy dict.

    Strips the ``scheme://`` prefix of ``server`` into the ``type`` field,
    producing ``{host, port, username, password, type}``. Returns ``None``
    when the port is missing/invalid (a host with no port is not launchable,
    so the profile is imported direct instead of half-configured).
    """
    server = parsed.get("server") or ""
    scheme, _, rest = server.partition("://")
    host, _, port_raw = rest.partition(":")
    try:
        port = int(port_raw) if port_raw else None
    except (TypeError, ValueError):
        port = None
    if not host or not port or not 1 <= port <= 65535:
        return None
    ptype = scheme.lower() if scheme.lower() in _PROXY_TYPES else "http"
    return {
        "host": host,
        "port": port,
        "username": parsed.get("username"),
        "password": parsed.get("password"),
        "type": ptype,
    }


def profile_from_export(d: dict, profile_manager: ProfileManager = None,
                        proxy_manager: ProxyManager = None) -> tuple:
    """Import one exported profile dict into our managers.

    Adapted from persona-studio's ``profile_from_export``: builds the persona
    via :meth:`ProfileManager.create(name, os, proxy_dict, **overrides)` so
    gaps are filled from our own coherent persona generator (the equivalent
    of upstream's ``fingerprint.generate`` base), registers the imported
    proxy with :meth:`ProxyManager.add` when it has a host, and maps
    ``group_name``/``tags`` onto our ``client_tag`` param (group = client).

    ``overrides`` passed to ``create`` are the export's own values:
    ``user_agent``, ``viewport``, ``screen``, ``timezone``, ``locale``,
    ``hardware_concurrency``, ``device_memory``, ``webgl_vendor``,
    ``webgl_renderer`` (plus ``platform`` and ``notes`` when present).

    :return: ``(persona_dict, issues)`` where ``issues`` lists
        human-readable notes about judgement calls (OS remap, proxy
        registration, tag mapping...).
    :raises ValueError: on duplicate profile name (message contains
        ``"already exists"``) — callers treat that as *skipped*, not error.
    """
    pm = profile_manager or ProfileManager()
    pxm = proxy_manager or ProxyManager()
    issues = []

    name = _first(d, "name", "title", "profile_name",
                  default="imported profile")

    # -- OS -------------------------------------------------------------
    ua = _first(d, "navigator.userAgent", "user_agent", "ua", "useragent",
                "fingerprint.ua", "fingerprint.userAgent", default="") or ""
    os_raw = _norm_os(_first(d, "os", "os_type", "osType",
                             "navigator.platform"), ua)
    if os_raw == "android":
        issues.append("os 'android' is not supported by ProfileManager; "
                      "imported as 'linux'")
    os_name = _OS_MAP.get(os_raw, "windows")

    # -- screen / viewport ----------------------------------------------
    sw, sh = _parse_resolution(_first(d, "navigator.resolution",
                                      "screen_resolution", "resolution",
                                      "screen", default=""))
    overrides = {
        "screen": {"width": sw, "height": sh},
        "viewport": {"width": sw, "height": max(400, sh - _CHROME_HEIGHT)},
    }

    # -- scalar overrides ------------------------------------------------
    def _int(name_aliases, export_default=None):
        try:
            return int(_first(d, *name_aliases, default=export_default))
        except (TypeError, ValueError):
            return None

    if ua:
        overrides["user_agent"] = ua
    platform = _first(d, "navigator.platform")
    overrides["platform"] = platform or _PLATFORM_DEFAULTS[os_name]
    timezone = _first(d, "timezone.timezone", "timezone", "time_zone",
                      "fingerprint.timezone")
    if timezone:
        overrides["timezone"] = timezone
    locale = _first(d, "navigator.language", "language", "locale")
    if locale:
        overrides["locale"] = locale
    cores = _int(("navigator.hardwareConcurrency", "hardware_concurrency",
                  "cores"))
    if cores:
        overrides["hardware_concurrency"] = cores
    memory = _int(("navigator.deviceMemory", "device_memory", "memory"))
    if memory:
        overrides["device_memory"] = memory
    webgl_vendor = _first(d, "webGLMetadata.vendor", "webgl_vendor",
                          "fingerprint.webgl_vendor",
                          # AdsPower's real field (missing from upstream):
                          "fingerprint_config.webgl_vendor")
    if webgl_vendor:
        overrides["webgl_vendor"] = webgl_vendor
    webgl_renderer = _first(d, "webGLMetadata.renderer", "webgl_renderer",
                            "fingerprint.webgl_renderer",
                            # AdsPower's real field (missing from upstream):
                            "fingerprint_config.webgl_renderer")
    if webgl_renderer:
        overrides["webgl_renderer"] = webgl_renderer

    # -- tags / notes ----------------------------------------------------
    tags = _first(d, "tags", default=[]) or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    group = _first(d, "group_name", "group", "folder")
    if group and group not in tags:
        tags.append(str(group))
    client_tag = str(group) if group else (str(tags[0]) if tags else None)

    notes = _first(d, "notes", "description", default="") or ""
    if tags:
        notes = (notes + " " if notes else "") + "[imported tags: %s]" % (
            ", ".join(str(t) for t in tags))
    overrides["notes"] = notes

    # -- proxy ------------------------------------------------------------
    parsed_proxy = _parse_proxy(d)
    proxy_dict = _split_proxy(parsed_proxy) if parsed_proxy else None
    if parsed_proxy and not proxy_dict:
        issues.append("proxy host found but port missing/invalid "
                      "(server=%r); imported as direct" %
                      parsed_proxy.get("server"))
    if proxy_dict:
        try:
            pxm.add("import-%s" % name, proxy_dict["host"],
                    proxy_dict["port"], username=proxy_dict["username"],
                    password=proxy_dict["password"], ptype=proxy_dict["type"])
        except Exception as exc:  # duplicate / invalid: keep the import going
            issues.append("proxy not registered in ProxyManager: %s" % exc)

    persona = pm.create(name, os_name, proxy_dict, client_tag=client_tag,
                        **overrides)
    return persona, issues


def _import_profiles(items, profile_manager=None,
                     proxy_manager=None) -> dict:
    """Import raw export dicts; never raises.

    :return: ``{'created': int, 'skipped': list, 'errors': list}`` —
        ``skipped`` holds duplicate profile names, ``errors`` holds
        ``{'name': ..., 'error': str}`` dicts.
    """
    pm = profile_manager or ProfileManager()
    pxm = proxy_manager or ProxyManager()
    result = {"created": 0, "skipped": [], "errors": []}
    for raw in items:
        name = _first(raw, "name", "title", "profile_name",
                      default="imported profile")
        try:
            profile_from_export(raw, profile_manager=pm, proxy_manager=pxm)
            result["created"] += 1
        except ValueError as exc:
            if "already exists" in str(exc):
                result["skipped"].append(name)
            else:
                result["errors"].append({"name": name, "error": str(exc)})
        except Exception as exc:  # never let one bad row stop the batch
            result["errors"].append({"name": name, "error": str(exc)})
    return result


def _coerce_items(path_or_dict) -> list:
    """Accept a file path, a dict, a list, or a raw JSON string."""
    if isinstance(path_or_dict, (dict, list)):
        return _unwrap(path_or_dict)
    if isinstance(path_or_dict, (str, Path)):
        text = str(path_or_dict)
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            return _unwrap(json.loads(stripped))
        return load_export(path_or_dict)
    raise TypeError("expected a file path, dict, list or JSON string, got %r"
                    % type(path_or_dict).__name__)


def import_adspower(path_or_dict, profile_manager=None,
                     proxy_manager=None) -> dict:
    """Import profiles from an AdsPower JSON export.

    Accepts a file path, a dict, a list of dicts, or a raw JSON string —
    including the real AdsPower Local API shape
    ``{"data": {"list": [...]}}``. Tolerant to field renames between
    AdsPower versions (see ``src/migrate/README.md``).

    :return: ``{'created': int, 'skipped': list, 'errors': list}``.
        Duplicate profile names land in ``skipped`` (not errors).
    """
    return _import_profiles(_coerce_items(path_or_dict), profile_manager,
                            proxy_manager)


def import_gologin(path_or_dict, profile_manager=None,
                    proxy_manager=None) -> dict:
    """Import profiles from a GoLogin JSON export (best-effort).

    Same tolerant machinery as :func:`import_adspower` — GoLogin fields
    (``navigator.userAgent``, ``timezone.timezone``, ``webGLMetadata``,
    ``proxy.mode``/``host``/``port``...) are covered by the same alias
    tables; see ``src/migrate/README.md`` for the exact shapes handled.
    Multilogin exports also flow through this path best-effort.

    :return: ``{'created': int, 'skipped': list, 'errors': list}``.
    """
    return _import_profiles(_coerce_items(path_or_dict), profile_manager,
                            proxy_manager)


def detect_source(items) -> str:
    """Best-effort guess of the export origin: ``'adspower'`` or ``'gologin'``.

    AdsPower markers: ``user_proxy_config``, ``group_name``/``group_id``,
    ``fingerprint_config``. GoLogin markers: ``navigator`` dict with
    ``userAgent``/``resolution``, ``webGLMetadata``, ``timezone.timezone``.
    Defaults to ``'adspower'`` when nothing distinctive is found.
    """
    for raw in _unwrap(items):
        if not isinstance(raw, dict):
            continue
        if any(k in raw for k in ("user_proxy_config", "group_name",
                                  "group_id", "fingerprint_config")):
            return "adspower"
        nav = raw.get("navigator")
        if isinstance(nav, dict) and any(
                k in nav for k in ("userAgent", "resolution", "user_agent")):
            return "gologin"
        if "webGLMetadata" in raw:
            return "gologin"
        tz = raw.get("timezone")
        if isinstance(tz, dict) and "timezone" in tz:
            return "gologin"
    return "adspower"
