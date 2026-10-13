"""Tests for src/fingerprint/wire_checks.py.

Network policy: nothing here may depend on external network for
pass/fail. All wire I/O is mocked; the two tests that touch real
sockets (loopback only, plus one optional public echo) are guarded by
``WIRE_CHECKS_LIVE=1`` and skip otherwise.
"""
import json
import os
import socket

import pytest

from src.fingerprint import wire_checks as wc

LIVE = os.environ.get("WIRE_CHECKS_LIVE") == "1"
needs_live = pytest.mark.skipif(not LIVE, reason="needs WIRE_CHECKS_LIVE=1")


# ---------------------------------------------------------------------------
# small fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, content=b"", url="https://x/"):
        self.status_code = status_code
        self.content = content
        self.url = url


class FakeSession:
    """Stand-in for tlsmask.Session. ``handler`` maps method+url -> FakeResponse
    or raises; every call is recorded on ``calls``."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        outcome = self._handler(method, url, kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch_session(monkeypatch, handler):
    made = {}

    def factory(profile):
        sess = FakeSession(handler)
        made["session"] = sess
        made["profile"] = profile
        return sess

    monkeypatch.setattr(wc.tlsmask, "Session", factory)
    return made


# ---------------------------------------------------------------------------
# profile / proxy mapping / JA3 helpers
# ---------------------------------------------------------------------------

def test_profile_for_engine():
    assert wc.profile_for_engine("camoufox") == "firefox_148"
    assert wc.profile_for_engine("patchright") == "chrome_150"
    assert wc.profile_for_engine("chromium") == "chrome_150"
    assert wc.profile_for_engine(None) == "firefox_148"
    assert wc.profile_for_engine("weird-engine") == "firefox_148"


def test_proxy_url_mapping():
    p = {"host": "1.2.3.4", "port": 8080, "username": None,
         "password": None, "type": "http"}
    assert wc._proxy_url(p) == "http://1.2.3.4:8080"
    assert wc._proxy_mapping(p) == {"http": "http://1.2.3.4:8080",
                                    "https": "http://1.2.3.4:8080"}
    # "https" type = HTTP proxy for HTTPS targets (manager.py semantics)
    p2 = dict(p, type="https")
    assert wc._proxy_url(p2) == "http://1.2.3.4:8080"
    p3 = dict(p, type="socks5")
    assert wc._proxy_url(p3) == "socks5://1.2.3.4:8080"
    # auth is URL-quoted
    p4 = dict(p, username="us@er", password="p:a/ss")
    assert wc._proxy_url(p4) == "http://us%40er:p%3Aa%2Fss@1.2.3.4:8080"
    assert wc._proxy_mapping(None) is None


def test_proxy_url_socks4_rejected():
    with pytest.raises(ValueError, match="socks4"):
        wc._proxy_url({"host": "h", "port": 1, "type": "socks4"})
    with pytest.raises(ValueError, match="unknown proxy type"):
        wc._proxy_url({"host": "h", "port": 1, "type": "gopher"})
    with pytest.raises(ValueError, match="'host' and 'port'"):
        wc._proxy_url({"host": "", "port": 0, "type": "http"})


def test_strip_grease_ja3():
    ja3 = "771,4865-2570-6682,23-65281-10,29-23,0"  # 2570/6682 are GREASE
    assert wc._strip_grease_ja3(ja3) == "771,4865,23-65281-10,29-23,0"
    assert wc._strip_grease_ja3("not-a-ja3") == "not-a-ja3"
    # recorded fingerprints are already GREASE-free -> stable
    for name, val in wc.EXPECTED_JA3.items():
        assert wc._strip_grease_ja3(val) == val, name


def test_extract_ja3_shapes():
    assert wc._extract_ja3({"ja3": "771,1,2,3,4"}) == "771,1,2,3,4"
    assert wc._extract_ja3({"tls": {"ja3": "771,1,2,3,4"}}) == "771,1,2,3,4"
    assert wc._extract_ja3({"tls": {"ja3_hash": "abc"}}) is None
    assert wc._extract_ja3({}) is None
    assert wc._extract_ja3("nope") is None


# ---------------------------------------------------------------------------
# fetch_via_browser_tls
# ---------------------------------------------------------------------------

def test_fetch_ok_and_proxy_mapping(monkeypatch):
    made = _patch_session(
        monkeypatch,
        lambda m, u, kw: FakeResponse(200, b'{"a":1}', u))
    proxy = {"host": "10.0.0.1", "port": 8888, "username": "u",
             "password": "p", "type": "socks5"}
    out = wc.fetch_via_browser_tls("https://example.com/x", proxy=proxy,
                                   profile="chrome_150", timeout=7,
                                   headers={"Accept": "text/html"})
    assert out["ok"] is True
    assert out["status"] == 200
    assert out["body"] == b'{"a":1}'
    assert out["profile"] == "chrome_150"
    assert out["error"] is None
    assert made["profile"] == "chrome_150"
    call = made["session"].calls[0]
    assert call["proxy"] == {"http": "socks5://u:p@10.0.0.1:8888",
                             "https": "socks5://u:p@10.0.0.1:8888"}
    assert call["timeout_seconds"] == 7
    assert call["headers"] == {"Accept": "text/html"}


def test_fetch_direct_no_proxy_arg(monkeypatch):
    made = _patch_session(
        monkeypatch, lambda m, u, kw: FakeResponse(200, b"ok", u))
    out = wc.fetch_via_browser_tls("https://example.com/")
    assert out["ok"] is True
    assert made["session"].calls[0]["proxy"] is None


def test_fetch_socks4_reports_error(monkeypatch):
    _patch_session(monkeypatch, lambda m, u, kw: FakeResponse(200, b"ok", u))
    out = wc.fetch_via_browser_tls(
        "https://example.com/",
        proxy={"host": "h", "port": 1, "type": "socks4"})
    assert out["ok"] is False
    assert "socks4" in (out["error"] or "")


def test_fetch_exception_never_raises(monkeypatch):
    def boom(m, u, kw):
        raise RuntimeError("tunnel exploded")
    _patch_session(monkeypatch, boom)
    out = wc.fetch_via_browser_tls("https://example.com/")
    assert out["ok"] is False
    assert "RuntimeError" in out["error"]


def test_fetch_tlsmask_missing(monkeypatch):
    monkeypatch.setattr(wc, "tlsmask", None)
    out = wc.fetch_via_browser_tls("https://example.com/")
    assert out["ok"] is False
    assert "tlsmask" in (out["error"] or "")


# ---------------------------------------------------------------------------
# tls_profile_check
# ---------------------------------------------------------------------------


def test_tls_profile_match():
    import unittest.mock as mock
    ja3 = wc.EXPECTED_JA3["firefox_148"]
    body = json.dumps({"tls": {"ja3": ja3, "ja3_hash": "x"}}).encode()
    with mock.patch.object(wc, "fetch_via_browser_tls",
                           return_value={"ok": True, "status": 200,
                                         "body": body, "error": None,
                                         "profile": "firefox_148",
                                         "final_url": wc._ECHO_URLS[0]}):
        out = wc.tls_profile_check()
    assert out["check"] == "tls-profile"
    assert out["passed"] is True
    assert out["observed_ja3"] == ja3
    assert "matches" in out["detail"]


def test_tls_profile_match_with_grease_in_echo():
    """Echo services that do NOT strip GREASE still compare equal."""
    import unittest.mock as mock
    ja3 = wc.EXPECTED_JA3["firefox_148"]
    # re-insert a GREASE value into the cipher field
    cipher_field = ja3.split(",")[1]
    greasy = ja3.replace(cipher_field, "2570-" + cipher_field, 1)
    body = json.dumps({"ja3": greasy}).encode()
    with mock.patch.object(wc, "fetch_via_browser_tls",
                           return_value={"ok": True, "status": 200,
                                         "body": body, "error": None,
                                         "profile": "firefox_148",
                                         "final_url": wc._ECHO_URLS[1]}):
        out = wc.tls_profile_check()
    assert out["passed"] is True, out["detail"]


def test_tls_profile_mismatch():
    import unittest.mock as mock
    # Python's stdlib JA3 (captured 2026-10-13) must NOT match firefox_148
    py_ja3 = ("771,4866-4867-4865-49196-49200-159-52393-52392-52394-49195-"
              "49199-158-49188-49192-107-49187-49191-103-49162-49172-57-"
              "49161-49171-51-157-156-61-60-53-47-255,"
              "11-10-35-22-23-13-43-45-51-21,29-23-30-25-24-256-257-258-259-260,"
              "0-1-2")
    body = json.dumps({"tls": {"ja3": py_ja3}}).encode()
    proxy = {"host": "9.9.9.9", "port": 1080, "type": "socks5"}
    with mock.patch.object(wc, "fetch_via_browser_tls",
                           return_value={"ok": True, "status": 200,
                                         "body": body, "error": None,
                                         "profile": "firefox_148",
                                         "final_url": wc._ECHO_URLS[0]}):
        out = wc.tls_profile_check(proxy=proxy)
    assert out["passed"] is False
    assert "mismatch" in out["detail"]
    assert out["observed_ja3"] == py_ja3


def test_tls_profile_incomplete():
    import unittest.mock as mock
    with mock.patch.object(
            wc, "fetch_via_browser_tls",
            return_value={"ok": False, "status": None, "body": None,
                          "error": "Timeout", "profile": "firefox_148",
                          "final_url": None}):
        out = wc.tls_profile_check()
    assert out["passed"] is False
    assert "incomplete" in out["detail"]


def test_tls_profile_unknown_profile():
    out = wc.tls_profile_check(profile="netscape_4")
    assert out["passed"] is False
    assert "no recorded JA3" in out["detail"]


def test_tls_profile_second_echo_used_on_first_failure():
    import unittest.mock as mock
    ja3 = wc.EXPECTED_JA3["chrome_150"]
    body = json.dumps({"ja3": ja3}).encode()
    calls = []

    def fake(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return {"ok": False, "status": None, "body": None,
                    "error": "boom", "profile": kw["profile"],
                    "final_url": None}
        return {"ok": True, "status": 200, "body": body, "error": None,
                "profile": kw["profile"], "final_url": url}

    with mock.patch.object(wc, "fetch_via_browser_tls", side_effect=fake):
        out = wc.tls_profile_check(profile="chrome_150")
    assert out["passed"] is True
    assert calls == list(wc._ECHO_URLS)
    assert out["echo_url"] == wc._ECHO_URLS[1]


# ---------------------------------------------------------------------------
# dns_leak_check
# ---------------------------------------------------------------------------

def _doh_body(status=3):
    return json.dumps({"Status": status, "Answer": []}).encode()


def test_dns_leak_no_proxy():
    out = wc.dns_leak_check(None)
    assert out["check"] == "dns-leak"
    assert out["passed"] is True
    assert "no proxy" in out["detail"]


def test_dns_leak_socks4_is_protocol_leak():
    out = wc.dns_leak_check({"host": "h", "port": 1080, "type": "socks4"})
    assert out["passed"] is False
    assert "SOCKS4" in out["detail"]
    assert "leak" in out["detail"].lower()


def test_dns_leak_unknown_type():
    out = wc.dns_leak_check({"host": "h", "port": 1, "type": "gopher"})
    assert out["passed"] is False
    assert "unknown proxy type" in out["detail"]


def test_dns_leak_no_leak(monkeypatch):
    """DoH completes through the proxy; nothing resolved locally."""
    import unittest.mock as mock

    def fake_gai(host, port, *a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    proxy = {"host": "10.9.9.9", "port": 8080, "type": "http"}
    with mock.patch.object(
            wc, "fetch_via_browser_tls",
            return_value={"ok": True, "status": 200, "body": _doh_body(),
                          "error": None, "profile": "firefox_148",
                          "final_url": wc._DOH_URLS[0]}):
        out = wc.dns_leak_check(proxy)
    assert out["passed"] is True, out["detail"]
    assert "no DNS leak" in out["detail"]
    assert out["locally_resolved"] == []
    assert "NXDOMAIN" in out["doh"]
    assert "gaierror" in out["direct"]
    # spy restored: socket.getaddrinfo is our monkeypatched fake again
    assert socket.getaddrinfo is fake_gai


def test_dns_leak_detected(monkeypatch):
    """A leaking client resolves the DoH endpoint locally -> LEAK."""
    import unittest.mock as mock

    def fake_gai(host, port, *a, **k):
        # pretend every name resolves: the "client" did local DNS
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)

    def leaking_fetch(url, **kw):
        # simulate a client that resolves the DoH host itself (the leak)
        socket.getaddrinfo("cloudflare-dns.com", 443)
        return {"ok": True, "status": 200, "body": _doh_body(),
                "error": None, "profile": kw.get("profile"),
                "final_url": url}

    proxy = {"host": "10.9.9.9", "port": 1080, "type": "socks5"}
    with mock.patch.object(wc, "fetch_via_browser_tls",
                           side_effect=leaking_fetch):
        out = wc.dns_leak_check(proxy)
    assert out["passed"] is False
    assert "LEAK" in out["detail"]
    assert "cloudflare-dns.com" in out["detail"]
    assert out["locally_resolved"] == ["cloudflare-dns.com"]


def test_dns_leak_incomplete_when_doh_fails(monkeypatch):
    import unittest.mock as mock

    def fake_gai(host, port, *a, **k):
        raise socket.gaierror(-2, "nope")

    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    with mock.patch.object(
            wc, "fetch_via_browser_tls",
            return_value={"ok": False, "status": None, "body": None,
                          "error": "proxy refused", "profile": "firefox_148",
                          "final_url": None}):
        out = wc.dns_leak_check({"host": "h", "port": 1, "type": "http"})
    assert out["passed"] is False
    assert "incomplete" in out["detail"]


def test_dns_spy_restores_getaddrinfo():
    real = socket.getaddrinfo
    with wc._dns_spy():
        pass
    assert socket.getaddrinfo is real


def test_dns_spy_ignores_other_threads():
    import threading
    seen_holder = []

    def worker(spy_cm):
        with spy_cm as seen:
            seen_holder.append(seen)
            ev_set.set()
            ev_go.wait(timeout=5)

    ev_set, ev_go = threading.Event(), threading.Event()
    ctx = wc._dns_spy()
    t = threading.Thread(target=worker, args=(ctx,))
    t.start()
    assert ev_set.wait(timeout=5)
    # main thread resolves; the worker thread's spy must not record it
    try:
        socket.getaddrinfo("127.0.0.1", 80)
    except OSError:
        pass
    ev_go.set()
    t.join(timeout=5)
    assert seen_holder[0] == [] or "127.0.0.1" not in seen_holder[0]
    assert socket.getaddrinfo is not None


# ---------------------------------------------------------------------------
# result-shape contract (validator integration)
# ---------------------------------------------------------------------------

def test_result_shape_contract():
    for out in (wc.dns_leak_check(None),
                wc.tls_profile_check(profile="netscape_4")):
        assert set(("check", "passed", "detail")) <= set(out)
        assert isinstance(out["check"], str)
        assert isinstance(out["passed"], bool)
        assert isinstance(out["detail"], str)


# ---------------------------------------------------------------------------
# live tests (opt-in only)
# ---------------------------------------------------------------------------

def _parse_clienthello_ja3(data: bytes) -> str:
    assert data[0] == 0x16 and data[5] == 0x01
    body = data[9:]
    ver = int.from_bytes(body[0:2], "big")
    p = 2 + 32
    p += 1 + body[p]
    cs_len = int.from_bytes(body[p:p + 2], "big")
    p += 2
    ciphers = [int.from_bytes(body[p + i:p + i + 2], "big")
               for i in range(0, cs_len, 2)]
    p += cs_len
    p += 1 + body[p]
    ext_total = int.from_bytes(body[p:p + 2], "big")
    p += 2
    exts, curves, fmts = [], [], []
    end = p + ext_total
    while p < end:
        et = int.from_bytes(body[p:p + 2], "big")
        el = int.from_bytes(body[p + 2:p + 4], "big")
        exts.append(et)
        ed = body[p + 4:p + 4 + el]
        if et == 10 and el >= 2:
            gl = int.from_bytes(ed[0:2], "big")
            curves = [int.from_bytes(ed[2 + i:4 + i], "big")
                      for i in range(0, gl, 2)]
        elif et == 11 and el >= 1:
            fmts = list(ed[1:1 + ed[0]])
        p += 4 + el
    return ",".join([str(ver), "-".join(map(str, ciphers)),
                     "-".join(map(str, exts)), "-".join(map(str, curves)),
                     "-".join(map(str, fmts))])


@needs_live
def test_live_firefox_ja3_matches_recorded(tmp_path):
    """Loopback TLS server captures tlsmask's real ClientHello; the
    GREASE-stripped JA3 must equal the recorded firefox_148 value."""
    import shutil
    import ssl
    import subprocess
    import threading
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", key, "-out", cert, "-days", "1", "-nodes",
                    "-subj", "/CN=127.0.0.1"],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    captured = {}

    def serve():
        conn, _ = srv.accept()
        buf = conn.recv(8192, socket.MSG_PEEK)
        captured["ja3"] = _parse_clienthello_ja3(buf)
        tls = ctx.wrap_socket(conn, server_side=True)
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = tls.recv(4096)
            if not chunk:
                break
            req += chunk
        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: close\r\n\r\nok")
        tls.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        out = wc.fetch_via_browser_tls(
            f"https://127.0.0.1:{port}/", profile="firefox_148", timeout=15,
            verify=False)  # self-signed loopback cert
    finally:
        t.join(timeout=10)
        srv.close()
    assert out["ok"] is True, out["error"]
    observed = wc._strip_grease_ja3(captured["ja3"])
    assert observed == wc.EXPECTED_JA3["firefox_148"], observed


@needs_live
def test_live_public_echo_direct():
    """Real JA3 echo over the public internet (no proxy)."""
    out = wc.tls_profile_check(proxy=None, timeout=30)
    assert out["passed"] is True, out["detail"]
