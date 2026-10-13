"""Phase 8 (Worker B) hardening tests: UA version auto-match, ANTIDETECT_HOME
paths, and encryption key rotation.

All tests are offline and hermetic: ANTIDETECT_HOME is pointed at tmp dirs
so the real ~/.antidetect-browser is never touched.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import os
import random
import sqlite3

import pytest
from cryptography.fernet import Fernet, InvalidToken

from src import paths
from src.fingerprints import validator as validator_mod
from src.fingerprints.generator import generate_persona
from src.fingerprints.validator import ConsistencyValidator


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point ANTIDETECT_HOME at a fresh tmp dir."""
    monkeypatch.setenv("ANTIDETECT_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# src.paths
# ---------------------------------------------------------------------------

def test_data_dir_honors_env(home):
    assert paths.data_dir() == str(home)
    assert paths.profiles_db() == os.path.join(str(home), "profiles.db")
    assert paths.proxies_db() == os.path.join(str(home), "proxies.db")
    assert paths.master_key_path() == os.path.join(str(home), ".master.key")
    assert paths.users_db() == os.path.join(str(home), "users.db")
    assert paths.profile_dir("p1") == os.path.join(str(home), "profiles", "p1")


def test_data_dir_legacy_fallback(tmp_path, monkeypatch):
    """Existing ~/.antidetect-browser keeps working when ANTIDETECT_HOME unset."""
    monkeypatch.delenv("ANTIDETECT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".antidetect-browser"
    legacy.mkdir()
    assert paths.data_dir() == str(legacy)


def test_data_dir_fresh_default(tmp_path, monkeypatch):
    """Fresh installs (no legacy dir, no env) use ~/.antidetect."""
    monkeypatch.delenv("ANTIDETECT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == os.path.join(str(tmp_path), ".antidetect")


# ---------------------------------------------------------------------------
# installed_firefox_version
# ---------------------------------------------------------------------------

def test_installed_firefox_version_shape(monkeypatch):
    """On a machine without a browser it must return None, never raise."""
    import src.browser.launcher as launcher

    def _boom(download_if_missing=False):
        raise RuntimeError("nope")

    monkeypatch.setattr("camoufox.pkgman.camoufox_path", _boom)
    assert launcher.installed_firefox_version() is None


def test_installed_firefox_version_parses_version_json(tmp_path, monkeypatch):
    import src.browser.launcher as launcher

    browser_dir = tmp_path / "156.0.1-beta.36-72637885"
    browser_dir.mkdir()
    (browser_dir / "version.json").write_text(
        '{"version": "156.0.1", "build": "beta.36"}', encoding="utf-8")
    monkeypatch.setattr("camoufox.pkgman.camoufox_path",
                        lambda download_if_missing=False: browser_dir)
    assert launcher.installed_firefox_version() == (156, "156.0.1")


def test_installed_firefox_version_dirname_fallback(tmp_path, monkeypatch):
    import src.browser.launcher as launcher

    browser_dir = tmp_path / "155.0.3-abcdef12"
    browser_dir.mkdir()  # no version.json
    monkeypatch.setattr("camoufox.pkgman.camoufox_path",
                        lambda download_if_missing=False: browser_dir)
    assert launcher.installed_firefox_version() == (155, "155.0.3")


# ---------------------------------------------------------------------------
# generator UA auto-match
# ---------------------------------------------------------------------------

def _persona_ua(persona):
    return persona["user_agent"]


def test_generate_persona_uses_installed_version(monkeypatch):
    import src.browser.launcher as launcher
    monkeypatch.setattr(launcher, "installed_firefox_version",
                        lambda: (156, "156.0.1"))
    persona = generate_persona("t1", os="windows", rng=random.Random(42))
    assert "Firefox/156.0.1" in persona["user_agent"]
    assert "rv:156.0" in persona["user_agent"]


def test_generate_persona_pinned_version(monkeypatch):
    import src.browser.launcher as launcher
    monkeypatch.setattr(launcher, "installed_firefox_version",
                        lambda: (156, "156.0.1"))
    persona = generate_persona("t2", os="linux", rng=random.Random(1),
                               firefox_version="159.2")
    assert "Firefox/159.2" in persona["user_agent"]
    assert "rv:159.0" in persona["user_agent"]


def test_generate_persona_fallback_narrow_range(monkeypatch):
    import src.browser.launcher as launcher
    monkeypatch.setattr(launcher, "installed_firefox_version", lambda: None)
    rng = random.Random(7)
    for i in range(20):
        persona = generate_persona("t3-%d" % i, os="macos", rng=rng)
        ua = persona["user_agent"]
        assert "Macintosh" in ua and "Firefox/" in ua
        import re
        major = int(re.search(r"Firefox/(\d+)", ua).group(1))
        assert major in (155, 156, 157), ua


def test_generate_persona_signature_backward_compatible():
    """Old positional call style still works."""
    persona = generate_persona("t4", "windows", random.Random(3))
    assert set(persona) >= {"name", "os", "user_agent"}


# ---------------------------------------------------------------------------
# validator check #23
# ---------------------------------------------------------------------------

def _minimal_persona(ua):
    return {"user_agent": ua, "os": "windows", "name": "x"}


def test_check23_registered():
    assert "ua_matches_installed_browser" in validator_mod.CHECK_NAMES
    assert len(validator_mod.CHECK_NAMES) == 23


def _run_check23(monkeypatch, ua, installed):
    import src.browser.launcher as launcher
    monkeypatch.setattr(launcher, "installed_firefox_version",
                        lambda: installed)
    v = ConsistencyValidator()
    return v.check_ua_matches_installed_browser(_minimal_persona(ua))


def test_check23_pass_when_match(monkeypatch):
    r = _run_check23(monkeypatch, "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
                                  "rv:156.0) Gecko/20100101 Firefox/156.0.1",
                     (156, "156.0.1"))
    assert r["passed"] is True


def test_check23_fail_when_mismatch(monkeypatch):
    r = _run_check23(monkeypatch, "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
                                  "rv:135.0) Gecko/20100101 Firefox/135.0.1",
                     (156, "156.0.1"))
    assert r["passed"] is False
    assert "156.0.1" in r["detail"]


def test_check23_pass_with_note_when_no_binary(monkeypatch):
    r = _run_check23(monkeypatch, "Mozilla/5.0 Firefox/135.0.1", None)
    assert r["passed"] is True
    assert "not installed" in r["detail"]


def test_generated_persona_passes_check23(monkeypatch):
    """End-to-end: generated persona + check #23 agree on the version."""
    import src.browser.launcher as launcher
    monkeypatch.setattr(launcher, "installed_firefox_version",
                        lambda: (156, "156.0.1"))
    persona = generate_persona("e2e", os="windows", rng=random.Random(9))
    results = ConsistencyValidator().validate(persona)
    by_name = {r["check"]: r for r in results}
    assert by_name["ua_matches_installed_browser"]["passed"] is True
    assert sum(1 for r in results if r["passed"]) == len(results)


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------

def _add_proxy_with_password(db_path, name, password):
    from src.proxy.manager import ProxyManager
    pm = ProxyManager(db_path=db_path)
    pm.add(name, host="127.0.0.1", port=8080, username="u",
           password=password, ptype="http")
    return pm


def test_rotate_key_roundtrip(home):
    from src.security.crypto import (
        decrypt_str, get_or_create_key, is_encrypted, rotate_key,
        replace_master_key)
    from src.proxy.manager import ProxyManager

    db_path = os.path.join(str(home), "proxies.db")
    old_key = get_or_create_key()
    _add_proxy_with_password(db_path, "p1", "s3cret!")
    _add_proxy_with_password(db_path, "p2", "another")

    new_key = Fernet.generate_key()
    rotated = rotate_key(old_key, new_key)
    assert rotated == 2

    # New key decrypts the stored values; old key no longer does.
    pm = ProxyManager(db_path=db_path)
    stored = pm._connect().execute(
        "SELECT password FROM proxies WHERE name='p1'").fetchone()[0]
    assert is_encrypted(stored)
    with pytest.raises(InvalidToken):
        decrypt_str(stored[len("enc:"):])  # old key file still active
    assert Fernet(new_key).decrypt(
        stored[len("enc:"):].encode("ascii")).decode() == "s3cret!"

    # Swapping the key file makes the normal decrypt path work again.
    replace_master_key(new_key)
    assert pm.get("p1")["password"] == "s3cret!"
    assert pm.get("p2")["password"] == "another"


def test_rotate_key_skips_plaintext_and_missing_db(home):
    from src.security.crypto import rotate_key
    db_path = os.path.join(str(home), "proxies.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE proxies (name TEXT PRIMARY KEY, host TEXT, "
                 "port INTEGER, username TEXT, password TEXT, ptype TEXT)")
    conn.execute("INSERT INTO proxies VALUES ('plain','h',1,'u','not-secret','http')")
    conn.execute("INSERT INTO proxies VALUES ('nullpw','h',1,'u',NULL,'http')")
    conn.commit()
    conn.close()
    assert rotate_key(Fernet.generate_key(), Fernet.generate_key()) == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT password FROM proxies WHERE name='plain'").fetchone()[0] == "not-secret"
    conn.close()


def test_rotate_key_wrong_old_key_raises(home):
    from src.security.crypto import get_or_create_key, rotate_key
    db_path = os.path.join(str(home), "proxies.db")
    get_or_create_key()
    _add_proxy_with_password(db_path, "p1", "s3cret!")
    with pytest.raises(InvalidToken):
        rotate_key(Fernet.generate_key(), Fernet.generate_key())


def test_rotate_key_rejects_bad_keys():
    from src.security.crypto import rotate_key
    with pytest.raises(ValueError):
        rotate_key(b"not-a-key", Fernet.generate_key())
    with pytest.raises(ValueError):
        rotate_key(Fernet.generate_key(), "short")


def test_replace_master_key_roundtrip(home):
    from src.security.crypto import (
        decrypt_str, encrypt_str, get_or_create_key, replace_master_key)
    get_or_create_key()
    token = "enc:" + encrypt_str("hello")
    new_key = Fernet.generate_key()
    replace_master_key(new_key)
    assert get_or_create_key() == new_key
    with pytest.raises(InvalidToken):
        decrypt_str(token[len("enc:"):])  # old ciphertext orphaned by design
    assert decrypt_str(("enc:" + encrypt_str("world"))[len("enc:"):]) == "world"
    st = os.stat(os.path.join(str(home), ".master.key"))
    assert oct(st.st_mode & 0o777) == "0o600"
