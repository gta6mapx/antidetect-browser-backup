#!/usr/bin/env python3
"""Phase 5 tests: profile migration (AdsPower/GoLogin/Multilogin) + cookies.

Adapted from persona-studio's ``engine/tests/test_importers.py`` and
``engine/tests/test_cookies.py`` (MIT, Copyright 2025 Ahsan) — the input
fixtures documenting the real export shapes are kept verbatim; only the
imports were repointed at our modules, and the assertions were rewritten
against our adapted API (ProfileManager/ProxyManager personas instead of
persona-studio Profile objects).

Runnable without pytest: ``.venv/bin/python tests/test_migrate_p5.py``.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.migrate import import_adspower, import_gologin, detect_source
from src.migrate.importers import (
    _first, _norm_os, _parse_proxy, _parse_resolution, _unwrap, load_export,
    profile_from_export,
)
from src.profiles.manager import ProfileManager
from src.proxy.manager import ProxyManager
from src.cookies import manager as ck


# ---------------------------------------------------------------------------
# Fixtures (verbatim from upstream test_importers.py)
# ---------------------------------------------------------------------------

GOLOGIN = {
    "name": "GL-Account-1",
    "os": "mac",
    "navigator": {
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128.0.0.0 Safari/537.36",
        "resolution": "1440x900", "language": "en-US",
        "hardwareConcurrency": 10, "deviceMemory": 16, "platform": "MacIntel",
    },
    "timezone": {"timezone": "America/New_York"},
    "webGLMetadata": {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M2, OpenGL 4.1)"},
    "proxy": {"mode": "http", "host": "1.2.3.4", "port": 8080, "username": "u", "password": "p"},
}

ADSPOWER = {
    "name": "ADS-Store-7",
    "group_name": "Amazon",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/127.0.0.0 Safari/537.36",
    "fingerprint_config": {"webgl_vendor": "Google Inc. (NVIDIA)",
                           "webgl_renderer": "ANGLE (NVIDIA GeForce RTX 3060)"},
    "screen_resolution": "1920x1080",
    "timezone": "Europe/Berlin", "language": "de-DE",
    "user_proxy_config": {"proxy_type": "socks5", "proxy_host": "9.9.9.9",
                          "proxy_port": "1080", "proxy_user": "x", "proxy_password": "y"},
}

MULTILOGIN = {
    "name": "ML-QA",
    "os_type": "linux",
    "navigator": {"user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0.0.0 Safari/537.36",
                  "resolution": "1920x1080", "hardware_concurrency": 8, "platform": "Linux x86_64"},
    "timezone": "Europe/London",
}

EXTENSION_EXPORT = [
    {
        "domain": ".facebook.com", "expirationDate": 1893456000.5, "hostOnly": False,
        "httpOnly": True, "name": "c_user", "path": "/", "sameSite": "no_restriction",
        "secure": True, "session": False, "storeId": "0", "value": "1000123",
    },
    {
        "domain": "www.facebook.com", "name": "sb", "path": "/", "sameSite": "lax",
        "secure": False, "session": True, "value": "abc",
    },
]


def _tmp_managers():
    """ProfileManager + ProxyManager on throwaway SQLite files."""
    tmp = tempfile.mkdtemp(prefix="p5-test-")
    pm = ProfileManager(db_path=os.path.join(tmp, "profiles.db"))
    pxm = ProxyManager(db_path=os.path.join(tmp, "proxies.db"))
    return pm, pxm


# ---------------------------------------------------------------------------
# Migration: adapted importer tests
# ---------------------------------------------------------------------------

def test_gologin_core_fields():
    pm, pxm = _tmp_managers()
    res = import_gologin(GOLOGIN, profile_manager=pm, proxy_manager=pxm)
    assert res == {"created": 1, "skipped": [], "errors": []}, res
    p = pm.get("GL-Account-1")
    assert p["os"] == "macos"
    assert (p["screen"]["width"], p["screen"]["height"]) == (1440, 900)
    assert p["hardware_concurrency"] == 10
    assert p["proxy"]["host"] == "1.2.3.4"
    assert p["proxy"]["port"] == 8080
    assert p["proxy"]["type"] == "http"
    assert p["proxy"]["password"] == "p"
    assert p["platform"] == "MacIntel"
    assert p["timezone"] == "America/New_York"
    assert p["locale"] == "en-US"


def test_adspower_nested_and_group_becomes_client_tag():
    pm, pxm = _tmp_managers()
    # Real AdsPower Local API nesting {"data": {"list": [...]}}.
    res = import_adspower({"data": {"list": [ADSPOWER]}},
                          profile_manager=pm, proxy_manager=pxm)
    assert res == {"created": 1, "skipped": [], "errors": []}, res
    p = pm.get("ADS-Store-7")
    assert p["os"] == "windows"
    assert p["timezone"] == "Europe/Berlin"
    assert p["client_tag"] == "Amazon"          # group_name -> client_tag
    assert p["proxy"]["host"] == "9.9.9.9"
    assert p["proxy"]["port"] == 1080
    assert p["proxy"]["type"] == "socks5"
    assert p["proxy"]["username"] == "x"
    assert p["webgl_renderer"] == "ANGLE (NVIDIA GeForce RTX 3060)"
    # The proxy was also registered in the proxy inventory.
    proxies = pxm.list()
    assert len(proxies) == 1
    assert proxies[0]["host"] == "9.9.9.9"
    assert proxies[0]["type"] == "socks5"


def test_multilogin_without_proxy():
    pm, pxm = _tmp_managers()
    res = import_gologin(MULTILOGIN, profile_manager=pm, proxy_manager=pxm)
    assert res["created"] == 1, res
    p = pm.get("ML-QA")
    assert p["os"] == "linux"
    assert p["proxy"] is None
    assert pxm.list() == []


def test_os_inferred_from_user_agent_when_field_missing():
    pm, pxm = _tmp_managers()
    persona, _ = profile_from_export(
        {"name": "x", "user_agent":
         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128 Safari/537.36"},
        profile_manager=pm, proxy_manager=pxm)
    assert persona["os"] == "macos"


def test_missing_fields_fall_back_to_a_coherent_base():
    # An almost-empty export must still yield a launchable, coherent profile.
    pm, pxm = _tmp_managers()
    persona, _ = profile_from_export({"name": "sparse"},
                                     profile_manager=pm, proxy_manager=pxm)
    assert persona["user_agent"]            # filled from the generated base
    assert persona["screen"]["width"] > 0
    assert persona["timezone"]


def test_direct_proxy_mode_means_no_proxy():
    pm, pxm = _tmp_managers()
    persona, _ = profile_from_export(
        {"name": "x", "proxy": {"mode": "none", "host": "1.1.1.1"}},
        profile_manager=pm, proxy_manager=pxm)
    assert persona["proxy"] is None
    assert pxm.list() == []


def test_duplicate_name_is_skipped_not_error():
    pm, pxm = _tmp_managers()
    first = import_adspower([ADSPOWER], profile_manager=pm, proxy_manager=pxm)
    assert first["created"] == 1
    second = import_adspower([ADSPOWER], profile_manager=pm, proxy_manager=pxm)
    assert second == {"created": 0, "skipped": ["ADS-Store-7"], "errors": []}


def test_unwrap_handles_wrappers():
    assert len(_unwrap({"data": [{"a": 1}, {"b": 2}]})) == 2
    assert len(_unwrap({"data": {"list": [{"a": 1}]}})) == 1  # real AdsPower
    assert len(_unwrap([{"a": 1}])) == 1
    assert len(_unwrap({"name": "single"})) == 1
    assert _unwrap("garbage") == []


def test_load_export_from_file():
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        fh.write(json.dumps([GOLOGIN, ADSPOWER, MULTILOGIN]))
        path = fh.name
    try:
        items = load_export(path)
        assert len(items) == 3
        assert {i["name"] for i in items} == {"GL-Account-1", "ADS-Store-7",
                                             "ML-QA"}
    finally:
        os.unlink(path)


def test_detect_source():
    assert detect_source(ADSPOWER) == "adspower"
    assert detect_source({"data": {"list": [ADSPOWER]}}) == "adspower"
    assert detect_source(GOLOGIN) == "gologin"
    assert detect_source(MULTILOGIN) == "gologin"
    assert detect_source({"name": "unknown-shape"}) == "adspower"  # default


def test_helpers():
    assert _first({"a": {"b": 1}}, "a.b") == 1
    assert _first({"x": ""}, "x", "y", default="d") == "d"
    assert _norm_os("mac") == "macos"
    assert _norm_os(None, "Mozilla/5.0 (X11; Linux x86_64)") == "linux"
    assert _parse_resolution("1440x900") == (1440, 900)
    assert _parse_resolution("garbage") == (1920, 1080)
    px = _parse_proxy({"proxy": {"mode": "http", "host": "1.2.3.4",
                                 "port": 8080}})
    assert px["server"] == "http://1.2.3.4:8080"   # plain dict, not a model
    assert _parse_proxy({"proxy": {"proxy_soft": "no_proxy"}}) is None


# ---------------------------------------------------------------------------
# Cookies: adapted from upstream test_cookies.py (pure format logic only)
# ---------------------------------------------------------------------------

def test_normalize_extension_cookie():
    c = ck.normalize(EXTENSION_EXPORT[0])
    assert c == {
        "name": "c_user", "value": "1000123", "domain": ".facebook.com", "path": "/",
        "expires": 1893456000.5, "httpOnly": True, "secure": True, "sameSite": "None",
    }


def test_session_cookie_gets_minus_one():
    assert ck.normalize(EXTENSION_EXPORT[1])["expires"] == -1


def test_samesite_none_requires_secure():
    # Chromium rejects SameSite=None on a non-secure cookie, so we downgrade it
    # rather than hand the browser something it will refuse.
    c = ck.normalize({"name": "a", "value": "1", "domain": "x.com",
                      "sameSite": "no_restriction", "secure": False})
    assert c["sameSite"] == "Lax"


def test_incomplete_cookies_are_dropped():
    assert ck.normalize({"value": "no name", "domain": "x.com"}) is None
    assert ck.normalize({"name": "no domain"}) is None
    assert ck.normalize("not a dict") is None


def test_parse_extension_json_list():
    jar = ck.parse(json.dumps(EXTENSION_EXPORT))
    assert [c["name"] for c in jar] == ["c_user", "sb"]


def test_parse_playwright_storage_state():
    state = {"cookies": EXTENSION_EXPORT, "origins": []}
    assert len(ck.parse(json.dumps(state))) == 2


def test_parse_single_cookie_object():
    assert ck.parse(json.dumps(EXTENSION_EXPORT[0]))[0]["name"] == "c_user"


def test_parse_empty_input():
    assert ck.parse("") == []
    assert ck.parse("   \n ") == []


def test_parse_netscape():
    text = (
        "# Netscape HTTP Cookie File\n"
        "\n"
        ".facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t1000123\n"
        "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\tsecret\n"
        "# a real comment\n"
    )
    jar = ck.parse(text)
    assert [c["name"] for c in jar] == ["c_user", "xs"]
    assert jar[0]["httpOnly"] is False
    assert jar[1]["httpOnly"] is True      # the #HttpOnly_ prefix is not a comment
    assert jar[1]["domain"] == ".facebook.com"


def test_netscape_round_trip():
    jar = ck.parse(json.dumps(EXTENSION_EXPORT))
    again = ck.parse(ck.to_netscape(jar))
    assert [(c["name"], c["value"], c["domain"]) for c in again] == \
           [(c["name"], c["value"], c["domain"]) for c in jar]


def test_dumps_json_is_storage_state():
    out = json.loads(ck.dumps(ck.parse(json.dumps(EXTENSION_EXPORT))))
    assert set(out) == {"cookies", "origins"}
    assert out["cookies"][0]["name"] == "c_user"


def test_read_file():
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        fh.write(json.dumps(EXTENSION_EXPORT))
        path = fh.name
    try:
        assert len(ck.read_file(path)) == 2
    finally:
        os.unlink(path)


def test_import_empty_cookie_file_is_noop():
    # No cookies means no browser launch at all — safe without an engine.
    pm, _ = _tmp_managers()
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        fh.write("[]")
        path = fh.name
    try:
        assert ck.import_cookies("any-profile", path,
                                 profile_manager=pm) == {"imported": 0}
    finally:
        os.unlink(path)


def test_export_unknown_profile_returns_error_dict():
    pm, _ = _tmp_managers()
    res = ck.export_cookies("no-such-profile", profile_manager=pm)
    assert isinstance(res, dict) and "error" in res


def test_export_unknown_format_returns_error_dict():
    pm, _ = _tmp_managers()
    res = ck.export_cookies("no-such-profile", fmt="bogus",
                            profile_manager=pm)
    assert isinstance(res, dict) and "error" in res


def test_import_missing_file_returns_error_dict():
    pm, _ = _tmp_managers()
    res = ck.import_cookies("no-such-profile", "/tmp/p5-missing-cookies.txt",
                            profile_manager=pm)
    assert isinstance(res, dict) and "error" in res


def test_to_playwright_add_url_form_and_expiry_cap():
    import time
    now = int(time.time())
    entries = ck._to_playwright_add([
        {"name": "a", "value": "1", "domain": ".example.com", "path": "/sub",
         "expires": 1893456000.0, "httpOnly": True, "secure": True,
         "sameSite": "None"},
        {"name": "b", "value": "2", "domain": "example.org", "path": "/",
         "expires": -1, "httpOnly": False, "secure": False,
         "sameSite": "Lax"},
        {"name": "", "value": "x", "domain": "example.org"},  # dropped
    ])
    assert len(entries) == 2
    a = entries[0]
    # url-only form: leading dot stripped, path embedded, https for secure
    assert a["url"] == "https://example.com/sub"
    assert "domain" not in a and "path" not in a
    # 2030 expiry clamped to Firefox's ~400-day cap
    assert a["expires"] <= now + 34560000
    assert a["expires"] > now
    assert a["httpOnly"] is True and a["secure"] is True
    assert a["sameSite"] == "None"
    b = entries[1]
    assert b["url"] == "http://example.org/"
    assert "expires" not in b  # session cookie omits expires


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- report, keep going
            failed += 1
            print("FAIL %s: %r" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
