"""High-entropy persona generator for the anti-detect browser.

This module fixes the Phase 2 finding that browserforge's per-OS fingerprint
pool is too small (10 same-OS personas produced 23 collisions across
user_agent / viewport / fonts / webgl_renderer / timezone).  It builds
personas from wide, per-OS value pools so that hundreds of same-OS personas
stay distinguishable on the high-signal fields, while every output still
passes every :class:`~src.fingerprints.validator.ConsistencyValidator`
check.

Design notes
------------
* The default engine is Camoufox = Firefox-based, so generated user agents
  are **Firefox UAs by default**; pass ``engine="chromium"`` for the
  patchright engine and the UA becomes a Chrome UA whose major matches
  the installed Patchright Chromium (mirroring the Firefox UA auto-match).
  A Chrome UA on the Firefox engine (or vice versa) fails the consistency
  validator and looks fake.
* Exact user-agent uniqueness across 100+ same-OS personas is impossible
  with realistic UAs (real users share UAs too -- that is fine and expected);
  entropy is therefore prioritized in the high-signal fields: canvas_seed
  (cryptographically unique), fonts signature, webgl_renderer, viewport and
  timezone.
* Screen resolutions are derived from the validator's own
  ``_COMMON_RESOLUTIONS`` list (filtered to those whose ``height - 80``
  viewport keeps an aspect ratio in [1.2, 2.2]), so
  ``screen_resolution_common`` and ``viewport_aspect_sane`` pass by
  construction.
* Timezone/locale pairs come from :mod:`src.proxy.geosync`'s
  ``COUNTRY_TIMEZONES`` / ``COUNTRY_LOCALES`` tables, so
  ``timezone_locale_region`` passes by construction.
* Randomness: everything except ``canvas_seed`` uses the caller-supplied
  ``random.Random`` (``rng`` param, seedable for tests); ``canvas_seed`` uses
  ``secrets.token_hex(8)`` and is guaranteed unique per persona.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import random
import secrets
from typing import Any, Dict, List, Optional, Tuple

from src.fingerprints.validator import (
    _APPLE_ONLY_FONTS,
    _COMMON_RESOLUTIONS,
    _WINDOWS_ONLY_FONTS,
)
from src.proxy.geosync import COUNTRY_LOCALES, COUNTRY_TIMEZONES

#: The exact persona contract keys, in a fixed order. generate_persona
#: returns EXACTLY these 18 keys.
PERSONA_KEYS = (
    "name",
    "os",
    "user_agent",
    "platform",
    "viewport",
    "screen",
    "timezone",
    "locale",
    "geolocation",
    "webgl_vendor",
    "webgl_renderer",
    "fonts",
    "hardware_concurrency",
    "device_memory",
    "touch_points",
    "color_depth",
    "proxy",
    "canvas_seed",
)

_CHROME_HEIGHT = 80  # pixels reserved for browser chrome (viewport < screen)
_VALID_OS = ("windows", "macos", "linux")

_PLATFORM_FOR_OS = {
    "windows": "Win32",
    "macos": "MacIntel",
    "linux": "Linux x86_64",
}

# Firefox desktop version pool. Kept for reference/history; the generator
# no longer samples it (see _resolve_ua_version below).
_UA_VERSIONS = (
    "128.0", "128.0.2", "128.0.3",
    "129.0", "129.0.1",
    "130.0", "130.0.1",
    "131.0", "131.0.2",
    "132.0", "132.0.2",
    "133.0", "133.0.3",
    "134.0", "134.0.2",
    "135.0", "135.0.1",
    "136.0", "136.0.2",
    "137.0", "137.0.2",
    "138.0", "138.0.1",
    "139.0", "139.0.1",
    "140.0", "140.0.2", "140.0.4",
    "141.0",
    "142.0",
)

#: Fallback UA versions, used ONLY when the Camoufox browser binary is not
#: installed (so its exact version cannot be known) and the caller did not
#: pin a version: a narrow recent range instead of the full historical pool.
#: Update the majors here as new Firefox releases ship.
_FALLBACK_UA_VERSIONS = ("155.0", "156.0", "156.0.1", "157.0")

# Per-OS Firefox UA templates. NEVER Chrome/Chromium: the engine is Firefox.
# ``rv`` is the major-only token (``rv:156.0``), ``full`` the exact build
# (``Firefox/156.0.1``) — real Firefox UAs use both forms.
_UA_TEMPLATES = {
    "windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{rv}) "
        "Gecko/20100101 Firefox/{full}"
    ),
    "macos": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{rv}) "
        "Gecko/20100101 Firefox/{full}"
    ),
    "linux": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:{rv}) "
        "Gecko/20100101 Firefox/{full}"
    ),
}

#: Fallback Chrome versions, used ONLY when the Patchright Chromium binary
#: is not installed (so its exact version cannot be known) and the caller
#: did not pin a version: a narrow recent range instead of a guess.
#: ``full`` is the dotted ``major.minor.build.patch`` form real Chrome UAs
#: carry (e.g. ``Chrome/153.0.0.0``).
_FALLBACK_CHROME_VERSIONS = ("152.0.0.0", "153.0.0.0")

# Per-OS Chrome/Chromium UA templates for the patchright engine.
# ``full`` is the exact dotted build (``Chrome/153.0.8010.12``).
_CHROME_UA_TEMPLATES = {
    "windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{full} Safari/537.36"
    ),
    "macos": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36"
    ),
    "linux": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{full} Safari/537.36"
    ),
}


def _viewport_for(screen: Tuple[int, int]) -> Dict[str, int]:
    """Return the viewport for ``screen`` (browser chrome subtracted)."""
    width, height = screen
    return {"width": width, "height": height - _CHROME_HEIGHT}


def _aspect_ok(screen: Tuple[int, int]) -> bool:
    """True when ``screen`` yields a validator-legal viewport aspect."""
    vp = _viewport_for(screen)
    if vp["height"] <= 0:
        return False
    return 1.2 <= vp["width"] / vp["height"] <= 2.2


# Resolutions allowed by the validator (common list + legal viewport aspect).
# Derived from the validator itself so the two can never drift apart.
_VALID_RESOLUTIONS = sorted(
    res for res in _COMMON_RESOLUTIONS if _aspect_ok(res)
)

# Per-OS resolution preference lists, weight-weighted towards the common
# ones.  Every entry must be in _VALID_RESOLUTIONS (guarded at import).
_RESOLUTION_PREFERENCES: Dict[str, List[Tuple[Tuple[int, int], int]]] = {
    "windows": [
        ((1920, 1080), 5), ((1366, 768), 4), ((1536, 864), 3),
        ((1440, 900), 3), ((1600, 900), 2), ((2560, 1440), 2),
        ((1280, 720), 2), ((1920, 1200), 2), ((1280, 800), 1),
        ((2560, 1600), 1), ((3840, 2160), 1), ((1024, 768), 1),
        ((1512, 982), 1), ((1728, 1117), 1),
    ],
    "macos": [
        ((1512, 982), 4), ((1728, 1117), 3), ((2560, 1600), 3),
        ((1440, 900), 3), ((1920, 1080), 2), ((2560, 1440), 2),
        ((1280, 800), 2), ((1920, 1200), 2), ((3840, 2160), 1),
        ((1536, 864), 1), ((1600, 900), 1),
    ],
    "linux": [
        ((1920, 1080), 5), ((1366, 768), 3), ((1600, 900), 2),
        ((1440, 900), 2), ((1536, 864), 2), ((1280, 720), 2),
        ((2560, 1440), 2), ((1920, 1200), 1), ((1280, 800), 1),
        ((2560, 1600), 1), ((3840, 2160), 1), ((1024, 768), 1),
    ],
}

_RESOLUTION_POOLS: Dict[str, List[Tuple[Tuple[int, int], int]]] = {}
for _os_name, _prefs in _RESOLUTION_PREFERENCES.items():
    _kept = [(res, w) for res, w in _prefs if res in _VALID_RESOLUTIONS]
    if not _kept:  # defensive: validator list changed drastically
        _kept = [(res, 1) for res in _VALID_RESOLUTIONS]
    _RESOLUTION_POOLS[_os_name] = _kept

# Per-OS base font lists. Marker fonts (validator-required) are present in
# each list and are NEVER dropped by the dropout below.
_FONT_BASES: Dict[str, List[str]] = {
    "windows": [
        "Arial", "Arial Black", "Arial Narrow", "Bahnschrift", "Book Antiqua",
        "Calibri", "Cambria", "Candara", "Cascadia Code", "Consolas",
        "Constantia", "Corbel", "Courier New", "Franklin Gothic Medium",
        "Georgia", "Impact", "Leelawadee UI", "Lucida Console",
        "Microsoft Sans Serif", "Microsoft YaHei", "MS Gothic",
        "Palatino Linotype", "Segoe UI", "Segoe UI Light",
        "Segoe UI Semibold", "SimSun", "Tahoma", "Times New Roman",
        "Trebuchet MS", "Verdana",
    ],
    "macos": [
        "American Typewriter", "Andale Mono", "Arial", "Arial Black",
        "Avenir", "Avenir Next", "Chalkboard", "Comic Sans MS", "Copperplate",
        "Courier", "Courier New", "Futura", "Geneva", "Georgia", "Gill Sans",
        "Helvetica", "Helvetica Neue", "Impact", "Lucida Grande", "Menlo",
        "Monaco", "Optima", "Palatino", "Papyrus", "Skia", "Tahoma",
        "Thonburi", "Times", "Times New Roman", "Trebuchet MS", "Verdana",
    ],
    "linux": [
        "Bitstream Vera Sans", "Bitstream Vera Serif", "Cantarell",
        "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif", "Droid Sans",
        "FreeMono", "FreeSans", "FreeSerif", "Lato", "Liberation Mono",
        "Liberation Sans", "Liberation Serif", "Noto Sans", "Noto Sans Mono",
        "Noto Serif", "Open Sans", "Roboto", "Ubuntu", "Ubuntu Mono",
        "URW Bookman", "URW Gothic",
    ],
}

# Validator-required marker fonts per OS (imported from the validator so the
# requirement and the check can never drift apart).
_MARKER_FONTS: Dict[str, frozenset] = {
    "windows": _WINDOWS_ONLY_FONTS,
    "macos": _APPLE_ONLY_FONTS,
    "linux": frozenset(),  # linux passes with any non-empty list
}

# Per-OS (vendor, renderer) pools. Windows: Firefox-on-Windows reports an
# ANGLE vendor of "Google Inc." OR a GPU vendor token; "Direct3D" renderers
# are windows-only per the validator. macOS: vendor must contain "Apple".
# Linux: Mesa/Intel/AMD/NVIDIA/X.Org vendors only.
_WEBGL_POOLS: Dict[str, List[Tuple[str, str]]] = {
    "windows": [
        ("Google Inc.", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0)"),
        ("Google Inc.", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0)"),
        ("NVIDIA", "NVIDIA GeForce RTX 4070/PCIe/SSE2"),
        ("NVIDIA", "NVIDIA GeForce RTX 3060 Ti/PCIe/SSE2"),
        ("NVIDIA", "NVIDIA GeForce GTX 1650/PCIe/SSE2"),
        ("Intel", "Intel(R) UHD Graphics 770"),
        ("Intel", "Intel(R) Iris(R) Xe Graphics"),
        ("AMD", "AMD Radeon RX 6700 XT"),
        ("AMD", "AMD Radeon(TM) Graphics"),
        ("Qualcomm", "Qualcomm(R) Adreno(TM) X1-85 GPU"),
    ],
    "macos": [
        ("Apple Inc.", "Apple M1"),
        ("Apple Inc.", "Apple M1 Pro"),
        ("Apple Inc.", "Apple M1 Max"),
        ("Apple Inc.", "Apple M2"),
        ("Apple Inc.", "Apple M2 Pro"),
        ("Apple Inc.", "Apple M2 Max"),
        ("Apple Inc.", "Apple M3"),
        ("Apple Inc.", "Apple M3 Pro"),
        ("Apple Inc.", "Apple M4"),
        ("Apple Inc.", "Apple GPU"),
    ],
    "linux": [
        ("Mesa", "Mesa Intel(R) UHD Graphics 620 (KBL GT2)"),
        ("Mesa", "Mesa Intel(R) UHD Graphics 630 (CFL GT2)"),
        ("Mesa", "Mesa Intel(R) UHD Graphics 770 (ADL-S GT1)"),
        ("Intel", "Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2)"),
        ("AMD", "AMD Radeon RX 580 Series"),
        ("AMD", "AMD Radeon RX 6600 (navi23, LLVM 15.0.7, DRM 3.54)"),
        ("AMD", "AMD RAVEN (DRM 3.42.0, LLVM 15.0.7)"),
        ("NVIDIA", "NVIDIA GeForce GTX 1650/PCIe/SSE2"),
        ("NVIDIA", "NVIDIA GeForce RTX 3060/PCIe/SSE2"),
        ("Mesa", "llvmpipe (LLVM 15.0.7, 256 bits)"),
        ("X.Org", "AMD RAVEN (DRM 3.42.0, 5.15.0-91-generic, LLVM 15.0.7)"),
    ],
}

_HARDWARE_CONCURRENCY_POOL = (4, 6, 8, 12, 16)
_DEVICE_MEMORY_POOL = (8, 16, 32)

#: Sorted country codes available for timezone/locale sampling.
_COUNTRY_CODES = sorted(COUNTRY_TIMEZONES.keys())


def _resolve_ua_version(rng: random.Random,
                       firefox_version: Optional[str] = None) -> Tuple[int, str]:
    """Resolve the Firefox version for a persona UA: ``(major, full)``.

    Priority:

    1. ``firefox_version`` when the caller pins one explicitly.
    2. The installed Camoufox browser's version (via
       :func:`src.browser.launcher.installed_firefox_version`) — this is
       the Phase-8 fix: the persona advertises exactly the binary it will
       run on, so the two can never drift apart.
    3. A narrow recent range (``_FALLBACK_UA_VERSIONS``) when the binary is
       not installed and nothing was pinned.
    """
    if firefox_version is not None:
        full = str(firefox_version).strip()
        return (int(full.split(".")[0]), full)
    try:
        from src.browser.launcher import installed_firefox_version
        installed = installed_firefox_version()
    except Exception:
        installed = None
    if installed is not None:
        return installed
    full = rng.choice(_FALLBACK_UA_VERSIONS)
    return (int(full.split(".")[0]), full)


def _resolve_chrome_version(rng: random.Random,
                            chrome_version: Optional[str] = None
                            ) -> Tuple[int, str]:
    """Resolve the Chrome version for a persona UA: ``(major, full)``.

    Priority (mirrors :func:`_resolve_ua_version`):

    1. ``chrome_version`` when the caller pins one explicitly.
    2. The installed Patchright Chromium's version (via
       :func:`src.engines.patchright_engine.installed_chromium_version`) —
       the persona advertises exactly the binary it will run on, so the
       two can never drift apart.
    3. A narrow recent range (``_FALLBACK_CHROME_VERSIONS``) when the
       binary is not installed and nothing was pinned.
    """
    if chrome_version is not None:
        full = str(chrome_version).strip()
        return (int(full.split(".")[0]), full)
    try:
        from src.engines.patchright_engine import installed_chromium_version
        installed = installed_chromium_version()
    except Exception:
        installed = None
    if installed is not None:
        return installed
    full = rng.choice(_FALLBACK_CHROME_VERSIONS)
    return (int(full.split(".")[0]), full)


def chrome_user_agent(os_name: str,
                      version_full: Optional[str] = None,
                      rng: Optional[random.Random] = None) -> str:
    """Build a Chrome UA for ``os_name`` (``'windows'``/``'macos'``/``'linux'``).

    :param version_full: explicit dotted Chrome build (e.g.
        ``"153.0.8010.12"``); when omitted the installed Patchright
        Chromium version is used, falling back to a narrow recent range.
    :raises ValueError: on an unknown ``os_name``.
    """
    if os_name not in _VALID_OS:
        raise ValueError("os must be one of %s" % (_VALID_OS,))
    if version_full is None:
        _rng = rng if rng is not None else random.Random()
        _major, version_full = _resolve_chrome_version(_rng)
    return _CHROME_UA_TEMPLATES[os_name].format(full=version_full)


def _sample_fonts(os_name: str, rng: random.Random) -> List[str]:
    """Sample a font list with random dropout, always keeping a marker font.

    Keeps >= 80% of the per-OS base list and guarantees at least one
    validator-required marker font survives (windows/macos). The surviving
    order is shuffled so signatures differ even when the same subset drops.
    """
    base = _FONT_BASES[os_name]
    markers = [f for f in base if f in _MARKER_FONTS[os_name]]
    if markers and os_name != "linux":
        # Pick one marker to pin; every other font (including the other
        # markers) is eligible for dropout.
        pinned = rng.choice(markers)
    else:
        # linux: no marker requirement; pin any font so the list can never
        # empty out entirely.
        pinned = rng.choice(base)
    rest = [f for f in base if f != pinned]
    max_drops = len(base) - -(-len(base) * 8 // 10)  # floor(20%) droppable
    # Bias towards heavier dropout (still keeping >= 80%): light dropout is
    # what makes font signatures cluster.
    drop_choices = list(range(max_drops + 1))
    drop_weights = [1 + i for i in drop_choices]
    n_drops = rng.choices(drop_choices, weights=drop_weights, k=1)[0]
    dropped = set(rng.sample(rest, n_drops)) if n_drops else set()
    kept = [pinned] + [f for f in rest if f not in dropped]
    rng.shuffle(kept)
    return kept


def _sample_resolution(os_name: str, rng: random.Random) -> Tuple[int, int]:
    """Weighted sample of a per-OS screen resolution."""
    pool = _RESOLUTION_POOLS[os_name]
    resolutions = [res for res, _w in pool]
    weights = [w for _res, w in pool]
    return rng.choices(resolutions, weights=weights, k=1)[0]


def generate_persona(
    name: str,
    os: str = "windows",
    rng: Optional[random.Random] = None,
    firefox_version: Optional[str] = None,
    chrome_version: Optional[str] = None,
    engine: str = "firefox",
    **overrides: Any,
) -> Dict[str, Any]:
    """Generate a high-entropy, validator-consistent persona dict.

    :param name: profile name (persona ``name`` field).
    :param os: one of ``'windows'``, ``'macos'``, ``'linux'``.
    :param rng: optional ``random.Random`` for seedable sampling; a fresh
        instance is used when omitted. ``secrets`` is used for
        ``canvas_seed`` regardless.
    :param firefox_version: optional pinned Firefox version string for the
        UA (e.g. ``"156.0.1"``). When omitted, the installed Camoufox
        browser's version is used; when no browser is installed, a narrow
        recent range is sampled. Only used when ``engine="firefox"``.
    :param chrome_version: optional pinned Chrome version string for the
        UA (e.g. ``"153.0.8010.12"``). When omitted, the installed
        Patchright Chromium's version is used; when no binary is
        installed, a narrow recent range is sampled. Only used when
        ``engine="chromium"``.
    :param engine: ``"firefox"`` (default, for the camoufox engine) or
        ``"chromium"`` (for the patchright engine). Selects the UA family;
        every other field is generated the same way (OS-consistent).
    :param overrides: fields merged over the generated persona; they win on
        any conflict (including ``proxy``).
    :return: dict with exactly the 18 persona contract keys.
    :raises ValueError: on an unknown ``os`` or ``engine`` value.
    """
    os_name = os
    if os_name not in _VALID_OS:
        raise ValueError("os must be one of %s" % (_VALID_OS,))
    if engine not in ("firefox", "chromium"):
        raise ValueError(
            "engine must be 'firefox' or 'chromium', got %r" % (engine,))
    if rng is None:
        rng = random.Random()

    if engine == "chromium":
        _major, chrome_full = _resolve_chrome_version(rng, chrome_version)
        user_agent = _CHROME_UA_TEMPLATES[os_name].format(full=chrome_full)
    else:
        ua_major, ua_full = _resolve_ua_version(rng, firefox_version)
        ua_rv = "%d.0" % ua_major
        user_agent = _UA_TEMPLATES[os_name].format(rv=ua_rv, full=ua_full)
    screen_w, screen_h = _sample_resolution(os_name, rng)
    vendor, renderer = rng.choice(_WEBGL_POOLS[os_name])
    country = rng.choice(_COUNTRY_CODES)

    persona: Dict[str, Any] = {
        "name": name,
        "os": os_name,
        "user_agent": user_agent,
        "platform": _PLATFORM_FOR_OS[os_name],
        "viewport": _viewport_for((screen_w, screen_h)),
        "screen": {"width": screen_w, "height": screen_h},
        "timezone": COUNTRY_TIMEZONES[country],
        "locale": COUNTRY_LOCALES[country],
        "geolocation": None,
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "fonts": _sample_fonts(os_name, rng),
        "hardware_concurrency": rng.choice(_HARDWARE_CONCURRENCY_POOL),
        "device_memory": rng.choice(_DEVICE_MEMORY_POOL),
        "touch_points": 0,
        "color_depth": 24,
        "proxy": None,
        "canvas_seed": secrets.token_hex(8),
    }
    assert set(persona) == set(PERSONA_KEYS), "persona key contract drift"
    persona.update(overrides)
    return persona
