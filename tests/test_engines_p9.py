"""Phase 9 tests: Chromium (Patchright) engine integration.

Offline-only: no browser is launched. Covers the engine factory, the
Chromium persona UA contract, validator dispatch on ``profile['engine']``,
the platform-spoof init script, and the patchright context-kwargs builder.
"""

import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import _vendor  # noqa: F401  (vendored patchright importable)

from src.engines import (
    CamoufoxEngine,
    PatchrightEngine,
    get_engine,
    normalize_engine_name,
)
from src.engines.patchright_engine import (
    build_context_kwargs,
    chromium_binary_present,
    installed_chromium_version,
    platform_spoof_script,
)
from src.fingerprints import validator as validator_mod
from src.fingerprints.generator import generate_persona
from src.fingerprints.validator import (
    CHROMIUM_CHECK_NAMES,
    CHECK_NAMES,
    ChromiumConsistencyValidator,
    ConsistencyValidator,
    validate,
)

_CHROME_RE = re.compile(r"Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)")
_FALLBACK_MAJORS = {"152", "153"}


def _chromium_persona(name="p9-chromium", os="windows", seed=11):
    persona = generate_persona(name, os=os, rng=random.Random(seed),
                               engine="chromium")
    persona["engine"] = "patchright"
    return persona


def _firefox_persona(name="p9-firefox", os="windows", seed=12):
    persona = generate_persona(name, os=os, rng=random.Random(seed),
                               engine="firefox")
    persona["engine"] = "camoufox"
    return persona


# -- engine factory -------------------------------------------------------

def test_get_engine_camoufox():
    engine = get_engine("camoufox")
    assert isinstance(engine, CamoufoxEngine)
    assert engine.name == "camoufox"


def test_get_engine_patchright():
    engine = get_engine("patchright")
    assert isinstance(engine, PatchrightEngine)
    assert engine.name == "patchright"


def test_get_engine_aliases():
    assert isinstance(get_engine("chromium"), PatchrightEngine)
    assert isinstance(get_engine("chrome"), PatchrightEngine)
    assert isinstance(get_engine("firefox"), CamoufoxEngine)
    assert normalize_engine_name("chromium") == "patchright"
    assert normalize_engine_name("camoufox") == "camoufox"


def test_get_engine_unknown_raises():
    for bad in ("safari", "", None, 123):
        try:
            get_engine(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for %r" % (bad,))


def test_engines_binary_present_never_raises():
    for engine in (get_engine("camoufox"), get_engine("patchright")):
        assert isinstance(engine.binary_present(), bool)
        # installed_version() returns None or a (major, full) tuple.
        version = engine.installed_version()
        assert version is None or (
            isinstance(version, tuple) and len(version) == 2
            and isinstance(version[0], int) and isinstance(version[1], str)
        )


# -- Chromium persona UA ---------------------------------------------------

def test_chromium_persona_ua_format():
    persona = _chromium_persona()
    ua = persona["user_agent"]
    assert "Chrome/" in ua
    assert "HeadlessChrome" not in ua
    assert "Firefox/" not in ua
    assert "Windows NT" in ua
    match = _CHROME_RE.search(ua)
    assert match, "UA lacks a dotted Chrome/<build> token: %r" % ua


def test_chromium_persona_ua_major_matches_installed():
    installed = installed_chromium_version()
    persona = _chromium_persona()
    major = _CHROME_RE.search(persona["user_agent"]).group(1)
    if installed is None:
        # Binary absent: generator falls back to a narrow recent range.
        assert major in _FALLBACK_MAJORS
    else:
        assert int(major) == installed[0]


def test_chromium_persona_platform_consistent():
    assert _chromium_persona(os="windows")["platform"] == "Win32"
    assert _chromium_persona(os="macos")["platform"] == "MacIntel"
    assert _chromium_persona(os="linux")["platform"] == "Linux x86_64"


def test_firefox_persona_unchanged_by_default():
    persona = generate_persona("p9-default", os="windows",
                               rng=random.Random(13))
    assert "Firefox/" in persona["user_agent"]
    assert "Chrome/" not in persona["user_agent"]
    try:
        generate_persona("p9-bad-engine", engine="safari")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for engine='safari'")


# -- validator dispatch -----------------------------------------------------

def test_validate_dispatch_patchright_uses_chromium_checks():
    persona = _chromium_persona()
    results = validate(persona)
    names = [r["check"] for r in results]
    assert names == CHROMIUM_CHECK_NAMES
    assert "ua_chrome_sane" in names
    assert "ua_firefox_sane" not in names
    assert "ua_matches_installed_chromium" in names
    assert "webdriver_false" in names


def test_validate_dispatch_camoufox_uses_firefox_checks():
    for persona in (_firefox_persona(),
                    dict(_firefox_persona(), engine="camoufox")):
        results = validate(persona)
        names = [r["check"] for r in results]
        assert names == CHECK_NAMES
        assert len(results) == 23


def test_validate_dispatch_missing_engine_defaults_to_firefox():
    persona = _firefox_persona()
    del persona["engine"]
    names = [r["check"] for r in validate(persona)]
    assert names == CHECK_NAMES


def test_chromium_persona_passes_chromium_checks():
    persona = _chromium_persona()
    results = validate(persona)
    failed = [r for r in results if not r["passed"]]
    assert not failed, "chromium persona failed checks: %s" % failed


def test_chromium_validator_rejects_headless_token():
    persona = _chromium_persona()
    persona["user_agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36"
    )
    by_name = {r["check"]: r
               for r in ChromiumConsistencyValidator().validate(persona)}
    assert not by_name["ua_chrome_sane"]["passed"]
    assert not by_name["ua_matches_installed_chromium"]["passed"]


def test_chromium_validator_rejects_firefox_ua():
    persona = _chromium_persona()
    persona["user_agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) "
        "Gecko/20100101 Firefox/156.0.1"
    )
    by_name = {r["check"]: r
               for r in ChromiumConsistencyValidator().validate(persona)}
    assert not by_name["ua_chrome_sane"]["passed"]


def test_webdriver_false_check():
    persona = _chromium_persona()
    ok = ChromiumConsistencyValidator().check_webdriver_false(persona)
    assert ok["passed"]
    persona["webdriver"] = True
    bad = ChromiumConsistencyValidator().check_webdriver_false(persona)
    assert not bad["passed"]


def test_firefox_checks_untouched():
    # The 22 original checks + #23 keep their names and order.
    assert CHECK_NAMES[-1] == "ua_matches_installed_browser"
    assert len(CHECK_NAMES) == 23
    persona = _firefox_persona()
    results = ConsistencyValidator().validate(persona)
    failed = [r for r in results if not r["passed"]]
    assert not failed, "firefox persona failed checks: %s" % failed


# -- platform spoof init script ----------------------------------------------

def test_platform_spoof_script_content():
    script = platform_spoof_script("Win32")
    assert "Object.defineProperty" in script
    assert "navigator" in script
    assert "platform" in script
    assert '"Win32"' in script  # safely quoted JS string
    assert "MacIntel" in platform_spoof_script("MacIntel")


# -- patchright context kwargs builder ----------------------------------------

def test_build_context_kwargs_injects_persona_ua():
    persona = _chromium_persona()
    kwargs = build_context_kwargs(persona, headless=True)
    assert kwargs["user_agent"] == persona["user_agent"]
    assert "HeadlessChrome" not in kwargs["user_agent"]
    assert kwargs["headless"] is True
    assert kwargs["user_data_dir"].endswith(
        os.path.join("profiles", persona["name"]))
    assert kwargs["viewport"] == persona["viewport"]
    assert kwargs["timezone_id"] == persona["timezone"]
    assert kwargs["locale"] == persona["locale"]
    # platform is NOT a context option: it travels via the init script.
    assert "platform" not in kwargs


def test_build_context_kwargs_proxy_and_geo():
    persona = _chromium_persona()
    persona["proxy"] = {"host": "127.0.0.1", "port": 8080,
                        "username": "u", "password": "p", "type": "http"}
    persona["geolocation"] = {"latitude": 40.7, "longitude": -74.0}
    kwargs = build_context_kwargs(persona)
    assert kwargs["proxy"]["server"] == "http://127.0.0.1:8080"
    assert kwargs["proxy"]["username"] == "u"
    assert kwargs["geolocation"] == {"latitude": 40.7, "longitude": -74.0}
    assert kwargs["permissions"] == ["geolocation"]


def test_build_context_kwargs_refuses_headless_token():
    persona = _chromium_persona()
    persona["user_agent"] = "Mozilla/5.0 HeadlessChrome/153.0.0.0 Safari/537.36"
    try:
        build_context_kwargs(persona)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for HeadlessChrome UA")


def test_build_context_kwargs_bad_proxy_type():
    persona = _chromium_persona()
    persona["proxy"] = {"host": "h", "port": 1, "type": "socks9"}
    try:
        build_context_kwargs(persona)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bad proxy type")


def test_chromium_binary_present_is_bool():
    assert isinstance(chromium_binary_present(), bool)


# -- profile engine field ------------------------------------------------------

def test_profile_engine_field(tmp_path):
    from src.profiles.manager import ProfileManager
    pm = ProfileManager(db_path=str(tmp_path / "profiles.db"))
    default = pm.create("eng-default", os="windows")
    assert default["engine"] == "camoufox"
    assert pm.get("eng-default")["engine"] == "camoufox"
    chrom = pm.create("eng-chrom", os="linux", engine="patchright")
    assert chrom["engine"] == "patchright"
    assert pm.get("eng-chrom")["engine"] == "patchright"
    assert "Chrome/" in chrom["user_agent"]
    updated = pm.update("eng-default", engine="patchright")
    assert updated["engine"] == "patchright"
    # Switching engine on an existing persona keeps its old UA, so the
    # engine-aware validator now (correctly) flags the mismatch.
    mismatched = [r for r in validator_mod.validate(pm.get("eng-default"))
                  if not r["passed"]]
    assert any(r["check"] == "ua_chrome_sane" for r in mismatched)
    try:
        pm.create("eng-bad", engine="safari")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for engine='safari'")
    # Engine-aware validation passes for both freshly created profiles.
    for name, expect_chrome in (("eng-chrom", True),):
        persona = pm.get(name)
        failed = [r for r in validator_mod.validate(persona)
                  if not r["passed"]]
        assert not failed, "%s failed: %s" % (name, failed)
        assert ("Chrome/" in persona["user_agent"]) == expect_chrome
    # A camoufox profile created before the switch still validates clean.
    pm2 = ProfileManager(db_path=str(tmp_path / "p2.db"))
    fox = pm2.create("eng-fox", os="windows")
    failed = [r for r in validator_mod.validate(fox) if not r["passed"]]
    assert not failed, "firefox profile failed: %s" % failed
