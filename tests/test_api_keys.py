#!/usr/bin/env python3
"""Tests for the external API surface: API keys, webhooks, v1 router.

Covers issuance (raw-once, hashed storage), Bearer verification, revocation,
scope implication, admin-only issuance, webhook signing/registration, and
an end-to-end v1 router check via FastAPI's TestClient with a fake profile
manager (no browser launch, no dashboard server).

Runnable without pytest: ``.venv/bin/python tests/test_api_keys.py``.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import pytest

from src.api import keys as api_keys
from src.api import webhooks as api_webhooks


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture()
def store(db_path):
    return api_keys.ApiKeyStore(db_path)


@pytest.fixture()
def webhook_store(db_path):
    return api_webhooks.WebhookStore(db_path)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def test_issue_returns_raw_once_and_stores_only_hash(store, db_path):
    raw, record = store.issue("automation-1",
                             [api_keys.SCOPE_READ, api_keys.SCOPE_LAUNCH])
    assert raw.startswith("sk_") and len(raw) > 20
    assert record["name"] == "automation-1"
    assert record["prefix"] and raw.startswith(record["prefix"])
    assert set(record["scopes"]) == {"profiles:read", "profiles:launch"}
    # Raw token must not appear anywhere in the SQLite file.
    with open(db_path, "rb") as fh:
        blob = fh.read()
    assert raw.encode("utf-8") not in blob
    # ...but its SHA-256 hash must.
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().encode()
    assert digest in blob
    # Public list() never exposes the hash.
    listed = store.list()[0]
    assert "key_hash" not in listed


def test_verify_bearer_ok_and_failures(store):
    raw, _ = store.issue("k1", [api_keys.SCOPE_READ])
    rec = store.verify_bearer(raw)
    assert rec is not None and rec["name"] == "k1"
    assert rec["last_used_at"] is None  # set only after a verify...
    rec2 = store.verify_bearer(raw)
    assert rec2["last_used_at"]  # ...now stamped
    assert store.verify_bearer("sk_" + "0" * 43) is None  # wrong token
    assert store.verify_bearer("not-a-key") is None       # malformed
    assert store.verify_bearer("") is None
    assert store.verify_bearer(None) is None


def test_revoke_kills_token(store):
    raw, _ = store.issue("k2", [api_keys.SCOPE_READ])
    assert store.verify_bearer(raw) is not None
    assert store.revoke("k2") is True
    assert store.verify_bearer(raw) is None
    assert store.revoke("k2") is False  # already revoked
    assert store.revoke(999999) is False


def test_scope_implications():
    assert api_keys.has_scope(["profiles:read"], "profiles:read")
    assert api_keys.has_scope(["profiles:write"], "profiles:read")  # implied
    assert api_keys.has_scope(["profiles:write"], "profiles:write")
    assert not api_keys.has_scope(["profiles:read"], "profiles:write")
    assert not api_keys.has_scope(["profiles:read"], "profiles:launch")
    assert api_keys.has_scope(["admin"], "profiles:launch")  # admin = all
    assert not api_keys.has_scope([], "profiles:read")


def test_issue_validation(store):
    with pytest.raises(ValueError):
        store.issue("", [api_keys.SCOPE_READ])
    with pytest.raises(ValueError):
        store.issue("bad-scope", ["nonsense:scope"])
    store.issue("dup", [api_keys.SCOPE_READ])
    with pytest.raises(ValueError):
        store.issue("dup", [api_keys.SCOPE_READ])


def test_issuance_is_admin_only(store):
    with pytest.raises(PermissionError):
        api_keys.issue_api_key(store, "x", [api_keys.SCOPE_READ],
                               actor_role="member")
    with pytest.raises(PermissionError):
        api_keys.revoke_api_key(store, "x", actor_role="viewer")
    raw, _ = api_keys.issue_api_key(store, "ok", [api_keys.SCOPE_READ],
                                    actor_role="admin")
    assert store.verify_bearer(raw) is not None


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def test_sign_payload_format():
    sig = api_webhooks.sign_payload("s3cret", b'{"a":1}')
    assert sig.startswith("sha256=")
    expected = "sha256=" + hmac.new(
        b"s3cret", b'{"a":1}', hashlib.sha256).hexdigest()
    assert sig == expected


def test_register_list_unregister(webhook_store):
    with pytest.raises(ValueError):
        webhook_store.register("ftp://x.example/hook")  # bad scheme
    with pytest.raises(ValueError):
        webhook_store.register("https://x.example/hook",
                               events=["nope.event"])  # bad event
    rec = webhook_store.register(
        "https://x.example/hook",
        events=[api_webhooks.EVENT_PROFILE_LAUNCHED])
    assert rec["url"] == "https://x.example/hook"
    assert rec["events"] == ["profile.launched"]
    assert "secret" not in rec  # never returned
    assert len(webhook_store.list()) == 1
    assert webhook_store.unregister(rec["id"]) is True
    assert webhook_store.list() == []


class _CaptureHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CaptureHandler.received.append(
            {"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_fire_delivers_signed_payload(webhook_store):
    _CaptureHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rec = webhook_store.register(
            "http://127.0.0.1:%d/hook" % port,
            events=[api_webhooks.EVENT_PROFILE_STOPPED],
            secret="test-secret")
        assert rec["id"]
        # Read the secret server-side (list() deliberately omits it).
        row = webhook_store._conn.execute(
            "SELECT secret FROM webhooks WHERE id = ?",
            (rec["id"],)).fetchone()
        api_webhooks.fire_event(
            webhook_store, api_webhooks.EVENT_PROFILE_STOPPED,
            {"profile": "demo"}, block=True)
        assert len(_CaptureHandler.received) == 1
        hit = _CaptureHandler.received[0]
        body = json.loads(hit["body"])
        assert body["event"] == "profile.stopped"
        assert body["profile"] == "demo"
        assert "at" in body
        expected = api_webhooks.sign_payload(row["secret"], hit["body"])
        assert hit["headers"].get("X-Webhook-Signature") == expected
    finally:
        server.shutdown()


def test_fire_skips_unsubscribed_events(webhook_store):
    _CaptureHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webhook_store.register(
            "http://127.0.0.1:%d/hook" % port,
            events=[api_webhooks.EVENT_PROFILE_LAUNCHED])
        api_webhooks.fire_event(
            webhook_store, api_webhooks.EVENT_PROFILE_STOPPED,
            {"profile": "demo"}, block=True)
        assert _CaptureHandler.received == []
    finally:
        server.shutdown()


def test_dispatcher_singleton_never_raises():
    # No webhooks registered and no DB contention: dispatch must not raise.
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "api.db")
        api_webhooks.get_dispatcher(db)
        assert api_webhooks.dispatch(
            api_webhooks.EVENT_PROFILE_LAUNCHED, "ghost",
            block=True) is not None


# ---------------------------------------------------------------------------
# v1 router end-to-end (fake profile manager, no browser)
# ---------------------------------------------------------------------------

class _FakeProfileManager:
    def __init__(self):
        self._profiles = {}

    def list(self):
        return list(self._profiles.values())

    def get(self, name):
        try:
            return self._profiles[name]
        except KeyError:
            raise KeyError(name)

    def create(self, name, os="windows", proxy=None, client_tag=None,
               engine="camoufox", **kw):
        if name in self._profiles:
            raise ValueError("duplicate")
        persona = {"name": name, "os": os, "engine": engine,
                   "client_tag": client_tag}
        self._profiles[name] = persona
        return persona


def _launch(name):
    return "starting"


def _stop(name):
    return "stopped"


def _status(name):
    return "stopped"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTIDETECT_HOME", str(tmp_path))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.base import BaseHTTPMiddleware
    from src import api as api_pkg
    from src.api import v1 as v1mod

    # Reset module-level singletons so tests are isolated.
    v1mod._stores.clear()
    api_pkg._bearer_store = None

    fake = _FakeProfileManager()
    app = FastAPI()

    async def _fake_session(request, call_next):
        # Simulate the GUI's dashboard session (admin) for non-Bearer calls.
        if not request.headers.get("authorization", "").lower().startswith(
                "bearer "):
            request.state.user = {"username": "admin", "role": "admin"}
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=_fake_session)
    api_pkg.mount_api(
        app, lambda: fake,
        launch_profile=_launch, stop_profile=_stop,
        profile_status=_status,
        profile_view=lambda p: {"name": p["name"], "engine": p["engine"]},
    )
    return TestClient(app, raise_server_exceptions=False)


def _bearer(client, name="e2e", scopes=("profiles:read", "profiles:write",
                                       "profiles:launch")):
    from src.api import v1 as v1mod
    raw, _ = v1mod.get_key_store().issue(name, list(scopes))
    return {"Authorization": "Bearer " + raw}, raw


def test_v1_profiles_crud_flow(client):
    headers, _ = _bearer(client)
    r = client.get("/api/v1/profiles", headers=headers)
    assert r.status_code == 200 and r.json()["total"] == 0

    r = client.post("/api/v1/profiles",
                    json={"name": "p1", "os": "windows"}, headers=headers)
    assert r.status_code == 201
    assert r.json()["profile"]["name"] == "p1"

    r = client.get("/api/v1/profiles/p1", headers=headers)
    assert r.status_code == 200
    assert r.json()["profile"]["status"] == "stopped"

    r = client.get("/api/v1/profiles/missing", headers=headers)
    assert r.status_code == 404

    r = client.get("/api/v1/profiles/p1/status", headers=headers)
    assert r.json() == {"profile": "p1", "status": "stopped"}


def test_v1_launch_stop_flow(client):
    headers, _ = _bearer(client)
    client.post("/api/v1/profiles", json={"name": "p2"}, headers=headers)
    r = client.post("/api/v1/profiles/p2/launch", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "starting"
    r = client.post("/api/v1/profiles/p2/stop", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    r = client.post("/api/v1/profiles/nope/launch", headers=headers)
    assert r.status_code == 404


def test_v1_auth_matrix(client):
    # No key at all -> 401.
    assert client.get("/api/v1/profiles").status_code == 401
    # Garbage key -> 401.
    bad = {"Authorization": "Bearer sk_" + "z" * 43}
    assert client.get("/api/v1/profiles", headers=bad).status_code == 401

    # Read-only key: GET ok, POST forbidden, launch forbidden.
    ro, _ = _bearer(client, name="ro", scopes=("profiles:read",))
    assert client.get("/api/v1/profiles", headers=ro).status_code == 200
    assert client.post("/api/v1/profiles", json={"name": "x"},
                       headers=ro).status_code == 403
    assert client.post("/api/v1/profiles/x/launch",
                       headers=ro).status_code == 403

    # Write implies read, but not launch.
    w, _ = _bearer(client, name="w",
                   scopes=("profiles:write", "profiles:launch"))
    assert client.get("/api/v1/profiles", headers=w).status_code == 200

    # Revoked key -> 401.
    from src.api import v1 as v1mod
    _, raw = _bearer(client, name="dead")
    v1mod.get_key_store().revoke("dead")
    assert client.get("/api/v1/profiles",
                      headers={"Authorization": "Bearer " + raw}
                      ).status_code == 401


def test_v1_key_management_requires_dashboard_session(client):
    headers, _ = _bearer(client)
    # Bearer key (even full-scope) cannot touch key management.
    assert client.get("/api/v1/keys", headers=headers).status_code == 403
    assert client.post("/api/v1/keys", json={"name": "n"},
                       headers=headers).status_code == 403
    # Dashboard admin session (simulated middleware) can.
    r = client.get("/api/v1/keys")
    assert r.status_code == 200
    r = client.post("/api/v1/keys",
                    json={"name": "dash-1", "scopes": ["profiles:read"]})
    assert r.status_code == 201
    raw = r.json()["key"]
    assert raw.startswith("sk_")
    key_id = r.json()["record"]["id"]
    # The issued key works immediately.
    kh = {"Authorization": "Bearer " + raw}
    assert client.get("/api/v1/profiles", headers=kh).status_code == 200
    # Revoke via dashboard session.
    r = client.delete("/api/v1/keys/%d" % key_id)
    assert r.status_code == 200 and r.json()["revoked"] is True
    assert client.get("/api/v1/profiles", headers=kh).status_code == 401


def test_v1_webhook_management_requires_dashboard_session(client):
    headers, _ = _bearer(client)
    assert client.get("/api/v1/webhooks", headers=headers).status_code == 403
    # Dashboard admin session can register / list / delete.
    r = client.post("/api/v1/webhooks",
                    json={"url": "https://example.com/hook",
                          "events": ["profile.launched"]})
    assert r.status_code == 201
    wid = r.json()["webhook"]["id"]
    assert client.get("/api/v1/webhooks").status_code == 200
    r = client.delete("/api/v1/webhooks/%d" % wid)
    assert r.json()["deleted"] is True
    # Bad URL rejected.
    r = client.post("/api/v1/webhooks", json={"url": "notaurl"})
    assert r.status_code == 400


def test_v1_create_validation(client):
    headers, _ = _bearer(client)
    r = client.post("/api/v1/profiles", json={"name": "", "os": "windows"},
                    headers=headers)
    assert r.status_code == 400
    r = client.post("/api/v1/profiles", json={"name": "p", "os": "amiga"},
                    headers=headers)
    assert r.status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
