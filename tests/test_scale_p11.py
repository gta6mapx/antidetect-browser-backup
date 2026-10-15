"""Tests for session-status logic (1000-profile session health)."""
import os
import time

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTIDETECT_HOME", str(tmp_path))
    import importlib
    import src.paths as paths
    importlib.reload(paths)
    return paths


def _status_for(paths, name):
    """Mirror of the server's _session_status_for (logic test)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    pdir = paths.profile_dir(safe)
    if not os.path.isdir(pdir):
        return {"status": "never"}
    cookie_file = None
    for root, _dirs, files in os.walk(pdir):
        if "cookies.sqlite" in files:
            cookie_file = os.path.join(root, "cookies.sqlite")
            break
    if not cookie_file or os.path.getsize(cookie_file) < 1024:
        return {"status": "empty"}
    age_days = (time.time() - os.path.getmtime(cookie_file)) / 86400
    if age_days > 30:
        return {"status": "stale"}
    return {"status": "healthy"}


def test_never_launched(home):
    assert _status_for(home, "nope")["status"] == "never"


def test_empty_profile(home):
    os.makedirs(home.profile_dir("empty1"), exist_ok=True)
    assert _status_for(home, "empty1")["status"] == "empty"


def test_healthy(home):
    pdir = home.profile_dir("h1")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "cookies.sqlite"), "wb") as f:
        f.write(b"x" * 2048)
    assert _status_for(home, "h1")["status"] == "healthy"


def test_stale(home):
    pdir = home.profile_dir("s1")
    os.makedirs(pdir, exist_ok=True)
    fp = os.path.join(pdir, "cookies.sqlite")
    with open(fp, "wb") as f:
        f.write(b"x" * 2048)
    old = time.time() - 40 * 86400
    os.utime(fp, (old, old))
    assert _status_for(home, "s1")["status"] == "stale"


def test_nested_cookies(home):
    """cookies.sqlite inside Firefox profile subdir is found."""
    pdir = os.path.join(home.profile_dir("n1"), "abc.default")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "cookies.sqlite"), "wb") as f:
        f.write(b"x" * 2048)
    assert _status_for(home, "n1")["status"] == "healthy"


def test_analytics_overview_shape():
    """Analytics endpoint returns the VIP dashboard payload shape."""
    import importlib
    import src.gui.server as srv
    importlib.reload(srv)
    app = srv.create_app()
    # Find the route function and call it directly (bypass auth).
    fn = None
    for r in app.routes:
        if getattr(r, "path", "") == "/api/analytics/overview":
            fn = r.endpoint
            break
    assert fn is not None
    data = fn()
    for key in ("profiles", "gmail", "sessions", "proxies", "groups",
                "engines", "issues", "issue_count",
                "trust", "disk", "activity"):
        assert key in data, key
    assert data["gmail"]["pct"] == 0
    assert "buckets" in data["trust"]
    assert "top" in data["groups"]


def test_check_endpoint_bad_input():
    """check_endpoint validates host/port without network."""
    from src.proxy.manager import ProxyManager
    from unittest import mock
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        m = ProxyManager(db_path=tmp + "/p.db")
        with mock.patch("socket.create_connection",
                        side_effect=ConnectionRefusedError("refused")):
            r = m.check_endpoint("", 0)
            assert r["ok"] is False
            r = m.check_endpoint("127.0.0.1", 999999)
            assert r["ok"] is False


def test_check_endpoint_unreachable():
    """Unreachable port returns ok=False quickly (no real network)."""
    from src.proxy.manager import ProxyManager
    from unittest import mock
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        m = ProxyManager(db_path=tmp + "/p.db")
        with mock.patch("socket.create_connection",
                        side_effect=ConnectionRefusedError("refused")), \
             mock.patch("urllib.request.urlopen",
                        side_effect=Exception("no route")):
            r = m.check_endpoint("127.0.0.1", 1, timeout=3)
            assert r["ok"] is False
            assert r["latency_ms"] is None


def test_warmup_match_filters():
    """Manual filter logic for the urllib fallback path."""
    from src.proxy.manager import ProxyManager
    m = ProxyManager.__new__(ProxyManager)
    row = {"ptype": "socks5", "proxy": "1.2.3.4:1080", "country": "US",
           "latency": 500, "anonymity": "elite",
           "sites": {"google": True}, "datacenter": False}
    assert m._warmup_match(row, {}) is True
    assert m._warmup_match(row, {"types": ["http"]}) is False
    assert m._warmup_match(row, {"types": ["socks5"]}) is True
    assert m._warmup_match(row, {"countries": ["DE"]}) is False
    assert m._warmup_match(row, {"countries": ["us"]}) is True
    assert m._warmup_match(row, {"anonymity": "transparent"}) is False
    assert m._warmup_match(row, {"anonymity": "elite"}) is True
    assert m._warmup_match(row, {"google_only": True}) is True
    row2 = dict(row, sites={})
    assert m._warmup_match(row2, {"google_only": True}) is False
    assert m._warmup_match(row, {"max_latency": 100}) is False
    assert m._warmup_match(row, {"max_latency": 1000}) is True
    row3 = dict(row, datacenter=True)
    assert m._warmup_match(row3, {"no_datacenter": True}) is False
    assert m._warmup_match(row3, {}) is True


def test_fetch_warmup_negative_limit():
    from src.proxy.manager import ProxyManager
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        m = ProxyManager(db_path=tmp + "/p.db")
        try:
            m.fetch_warmup_proxies(limit=-1)
            assert False, "should raise"
        except ValueError:
            pass


def test_parse_proxy_line():
    from src.proxy.manager import ProxyManager
    p = ProxyManager.parse_proxy_line
    assert p("1.2.3.4:8080") == {"host": "1.2.3.4", "port": 8080, "type": "http"}
    assert p("socks5://1.2.3.4:1080")["type"] == "socks5"
    assert p("socks4://1.2.3.4:1080")["type"] == "socks4"
    assert p("http://1.2.3.4:8080")["type"] == "http"
    d = p("1.2.3.4:8080:user:pw")
    assert d["auth"] == "user:pw" and d["port"] == 8080
    assert p("") is None
    assert p("# comment") is None
    assert p("notaproxy") is None
    assert p("1.2.3.4:99999") is None
    assert p("ftp://1.2.3.4:21") is None


def test_bulk_check_invalid_lines():
    from src.proxy.manager import ProxyManager
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        m = ProxyManager(db_path=tmp + "/p.db")
        out = m.bulk_check_proxies(["badline", "", "127.0.0.1:1"], timeout=3, workers=2)
        assert len(out) == 3  # every line reported
        assert out[0]["ok"] is False and "invalid format" in out[0]["error"]
        assert out[1]["ok"] is False  # blank line
        # 127.0.0.1:1 -> refused; real check attempted, structured result
        assert out[2]["host"] == "127.0.0.1" and "latency_ms" in out[2]


def test_list_profiles_endpoint_shape(home):
    """Regression: coordinator's nested _profile_view shadowed the module one.

    /api/profiles must serialize persona dicts via the module-level
    _profile_view(persona), not pass dicts as names into manager.get().
    """
    import importlib
    import src.gui.server as srv
    importlib.reload(srv)
    app = srv.create_app()
    fn = None
    for r in app.routes:
        if getattr(r, "path", "") == "/api/profiles":
            fn = r.endpoint
            break
    assert fn is not None
    # Create one profile, then call the endpoint (bypass auth).
    mgr = srv._profile_manager
    mgr.create("regression-view-test", os="windows")
    try:
        data = fn()
        assert data["total"] >= 1
        views = data["profiles"]
        assert views, "expected at least one profile view"
        v = views[0]
        assert v["name"] == "regression-view-test"
        assert "proxy_label" in v and "status" in v
    finally:
        mgr.delete("regression-view-test", permanent=True)
