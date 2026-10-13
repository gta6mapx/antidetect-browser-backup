"""Fingerprint spoofer integration.

Vendored from browser-fingerprint-spoofer (MIT, Devansh Mishra):
https://github.com/wchenge/browser-fingerprint-spoofer
Files: vendor/fp-spoofer/spoof-core.js, spoof-config.js, utils.js
License: vendor/fp-spoofer/LICENSE

This module maps a profile's fingerprint dict to the spoofer's config
and builds a self-contained injection script. The spoofer covers:
navigator, screen, WebGL, canvas, WebRTC, sensors, timezone, fonts.
"""

import json
import os

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "vendor", "fp-spoofer")


def _read_vendor(name):
    with open(os.path.join(_VENDOR_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def build_config(fingerprint):
    """Map a profile fingerprint dict to spoofer config.

    :param fingerprint: dict with keys like user_agent, platform, locale,
        timezone, screen, webgl_vendor, webgl_renderer,
        hardware_concurrency, device_memory, etc.
    :return: config dict for BrowserFingerprintSpoofer.
    """
    fp = fingerprint or {}
    screen = fp.get("screen") or {}
    viewport = fp.get("viewport") or {}
    languages = fp.get("languages") or [fp.get("locale") or "en-US"]

    # Timezone offset in minutes (for Date spoofing).
    tz_offset = fp.get("timezone_offset")
    if tz_offset is None:
        # Best-effort: derive from common timezones.
        tz_offsets = {
            "America/New_York": -300, "America/Chicago": -360,
            "America/Denver": -420, "America/Los_Angeles": -480,
            "Europe/London": 0, "Europe/Berlin": -60,
            "Europe/Paris": -60, "Asia/Dubai": -240,
            "Asia/Karachi": -300, "Asia/Kolkata": -330,
            "Australia/Sydney": -600,
        }
        tz_offset = tz_offsets.get(fp.get("timezone"), 0)

    return {
        "userAgent": fp.get("user_agent") or "",
        "platform": fp.get("platform") or "Win32",
        "language": fp.get("locale") or "en-US",
        "languages": languages,
        "hardwareConcurrency": fp.get("hardware_concurrency") or 8,
        "deviceMemory": fp.get("device_memory") or 8,
        "screenWidth": screen.get("width") or 1920,
        "screenHeight": screen.get("height") or 1080,
        "availWidth": screen.get("width") or 1920,
        "availHeight": (screen.get("height") or 1080) - 40,
        "colorDepth": fp.get("color_depth") or 24,
        "pixelDepth": fp.get("color_depth") or 24,
        "webglVendor": fp.get("webgl_vendor") or "Google Inc. (Intel)",
        "webglRenderer": fp.get("webgl_renderer") or "",
        "timezone": fp.get("timezone") or "America/New_York",
        "timezoneOffset": tz_offset,
        # Feature flags — all on for full coverage.
        "spoofNavigator": True,
        "spoofScreen": True,
        "spoofWebGL": True,
        "spoofCanvas": True,
        "spoofWebRTC": bool(fp.get("proxy")),
        "spoofSensors": True,
        "spoofFonts": True,
        "clearStorage": False,  # never wipe profile storage
        "randomizeValues": False,  # deterministic per profile
    }


def build_injection_script(fingerprint):
    """Build a self-contained JS injection script.

    Embeds the vendored spoofer + config, then enables it. Designed
    for add_init_script / evaluate_on_new_document.
    """
    config = build_config(fingerprint)
    core_js = _read_vendor("spoof-core.js")
    script = (
        core_js
        + "\n;(function(){\n"
        + "  var cfg = " + json.dumps(config) + ";\n"
        + "  try {\n"
        + "    var spoofer = new BrowserFingerprintSpoofer(cfg);\n"
        + "    spoofer.enable();\n"
        + "  } catch (e) { console.warn('spoofer failed:', e); }\n"
        + "})();\n"
    )
    return script


def attribution():
    """Return attribution string for the vendored library."""
    return ("Fingerprint spoofing core vendored from "
            "browser-fingerprint-spoofer (MIT, (c) 2025 Devansh Mishra): "
            "https://github.com/wchenge/browser-fingerprint-spoofer")
