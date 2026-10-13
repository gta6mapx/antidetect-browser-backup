"""External automation API v1 (``/api/v1``) — AdsPower-style REST surface.

Pattern learned from J1BON/BZ-Browser (MIT,
https://github.com/j1bon/bz-browser); implementation is original.

Authentication: ``Authorization: Bearer <api-key>`` (see ``src/api/keys.py``).
:func:`src.api.mount_api` installs a middleware that verifies the Bearer
token and lets the request past the GUI's session middleware; the router's
``require_bearer`` then enforces per-route scopes. Key-management and
webhook-management routes on this router are instead guarded by the
*dashboard session* (admin role, Bearer requests rejected outright) — the
same RBAC the rest of the GUI uses — so keys can be issued from the new
"API" dashboard page.

Routes (Bearer-auth, scoped):
    GET    /api/v1/profiles                       profiles:read
    POST   /api/v1/profiles                       profiles:write
    GET    /api/v1/profiles/{name}                profiles:read
    POST   /api/v1/profiles/{name}/launch         profiles:launch
    POST   /api/v1/profiles/{name}/stop           profiles:launch
    GET    /api/v1/profiles/{name}/status         profiles:read

Routes (dashboard admin session):
    GET    /api/v1/keys, POST /api/v1/keys, DELETE /api/v1/keys/{id}
    GET    /api/v1/webhooks, POST /api/v1/webhooks,
           DELETE /api/v1/webhooks/{id}, POST /api/v1/webhooks/{id}/test

The router is configured with a :class:`ApiContext` supplied by
:func:`src.api.mount_api` — it reuses the server's live ProfileManager and,
when the integration snippet wires them, the server's real launch/stop
worker functions so external callers share one ``_running`` registry with
the dashboard. Unwired launch/stop returns 503 instead of forking a second
lifecycle.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import datetime
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from src.api import keys as _keys
from src.api import webhooks as _webhooks

router = APIRouter(prefix="/api/v1")

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Context: supplied by src.api.mount_api()
# ---------------------------------------------------------------------------

class ApiContext:
    """Wiring between the v1 router and the running GUI server.

    ``profile_manager_getter`` must be a zero-arg callable returning the
    live :class:`src.profiles.manager.ProfileManager`. The launch/stop
    callables, when provided, reuse the server's own profile lifecycle
    (single ``_running`` registry shared with the dashboard); otherwise the
    corresponding endpoints answer 503.
    """

    def __init__(self, profile_manager_getter,
                 launch_profile=None, stop_profile=None,
                 profile_status=None, profile_view=None):
        self.profile_manager_getter = profile_manager_getter
        self.launch_profile = launch_profile
        self.stop_profile = stop_profile
        self.profile_status = profile_status
        self.profile_view = profile_view

    @property
    def profile_manager(self):
        return self.profile_manager_getter()


_CTX = ApiContext(lambda: None)  # replaced by mount_api()
_ctx_lock = threading.Lock()

#: Lazily-built stores, shared across requests.
_stores: dict = {}


def configure(context: ApiContext) -> None:
    """Install the live context (called once by ``mount_api``)."""
    global _CTX
    with _ctx_lock:
        _CTX = context


def get_key_store() -> _keys.ApiKeyStore:
    store = _stores.get("keys")
    if store is None:
        store = _keys.ApiKeyStore()
        _stores["keys"] = store
    return store


def get_webhook_store() -> _webhooks.WebhookStore:
    store = _stores.get("webhooks")
    if store is None:
        store = _webhooks.WebhookStore()
        _stores["webhooks"] = store
    return store


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

def require_bearer(scope: str):
    """FastAPI dependency: valid Bearer key with ``scope``.

    The mount middleware (``src.api._bearer_bypass_middleware``) already
    verified the token and stashed the record on ``request.state.api_key``;
    when the router is used without that middleware the header is verified
    here instead. On success sets ``request.state.api_key`` to the key
    record. 401 when the token is missing/invalid/revoked, 403 when the
    scope is missing.
    """

    def _dep(request: Request,
             creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme)):
        record = getattr(request.state, "api_key", None)
        if record is None:
            token = creds.credentials if creds is not None else None
            record = get_key_store().verify_bearer(token) if token else None
        if record is None:
            raise HTTPException(
                401, "invalid or missing API key "
                     "(Authorization: Bearer <key>)")
        if not _keys.has_scope(record["scopes"], scope):
            raise HTTPException(
                403, "API key lacks required scope %r" % scope)
        request.state.api_key = record
        return record

    return _dep


def require_dashboard_admin(request: Request) -> dict:
    """Dashboard-session admin check for key/webhook management routes.

    Requests that arrived via Bearer (``request.state.api_key`` set by the
    mount middleware) are rejected even when a borrowed dashboard session
    rides along — an external API key must never mint or manage keys.
    """
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(
            403, "API key management requires a dashboard admin session")
    user = getattr(request.state, "user", None)
    role = user.get("role") if isinstance(user, dict) else None
    if role != "admin":
        raise HTTPException(403, "forbidden: administrators only")
    return user


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

_VALID_OS = ("windows", "macos", "linux")


class ProfileCreateBody(BaseModel):
    """Body for POST /api/v1/profiles."""

    name: str
    os: str = "windows"
    proxy_name: str | None = None
    client_tag: str | None = None
    engine: str | None = None


class KeyCreateBody(BaseModel):
    """Body for POST /api/v1/keys (dashboard admin)."""

    name: str
    scopes: list[str] = ["profiles:read"]


class WebhookCreateBody(BaseModel):
    """Body for POST /api/v1/webhooks (dashboard admin)."""

    url: str
    events: list[str] | None = None
    secret: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _view(persona: dict) -> dict:
    if _CTX.profile_view is not None:
        try:
            return _CTX.profile_view(persona)
        except Exception:
            pass
    return {
        "name": persona.get("name"),
        "os": persona.get("os"),
        "engine": persona.get("engine") or "camoufox",
        "client_tag": persona.get("client_tag"),
    }


def _status_of(name: str) -> str:
    if _CTX.profile_status is not None:
        try:
            return _CTX.profile_status(name)
        except Exception:
            pass
    return "unknown"


# ---------------------------------------------------------------------------
# Profile routes (Bearer-auth)
# ---------------------------------------------------------------------------

@router.get("/profiles")
def v1_list_profiles(api_key=Depends(require_bearer(_keys.SCOPE_READ))):
    """List profiles (name, engine, client tag, launch status)."""
    manager = _CTX.profile_manager
    if manager is None:
        raise HTTPException(500, "profile manager not configured")
    profiles = manager.list()
    out = []
    for persona in profiles:
        view = _view(persona)
        view["status"] = _status_of(persona.get("name"))
        out.append(view)
    return {"profiles": out, "total": len(out)}


@router.post("/profiles", status_code=201)
def v1_create_profile(body: ProfileCreateBody,
                      api_key=Depends(require_bearer(_keys.SCOPE_WRITE))):
    """Create a new profile (same semantics as the dashboard's create)."""
    manager = _CTX.profile_manager
    if manager is None:
        raise HTTPException(500, "profile manager not configured")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required and must be non-empty")
    if body.os not in _VALID_OS:
        raise HTTPException(400, "os must be one of %s" % (list(_VALID_OS),))
    try:
        from src.engines import normalize_engine_name
        engine = normalize_engine_name(body.engine or "camoufox")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    proxy = None
    if body.proxy_name:
        try:
            from src.proxy.manager import ProxyManager
            proxy = ProxyManager().get(body.proxy_name)
        except KeyError:
            raise HTTPException(
                400, "unknown proxy_name '%s'" % body.proxy_name)
    try:
        persona = manager.create(name, os=body.os, proxy=proxy,
                                 client_tag=body.client_tag, engine=engine)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"profile": _view(persona)}


@router.get("/profiles/{name}")
def v1_get_profile(name: str,
                   api_key=Depends(require_bearer(_keys.SCOPE_READ))):
    """Return one profile's public view."""
    manager = _CTX.profile_manager
    if manager is None:
        raise HTTPException(500, "profile manager not configured")
    try:
        persona = manager.get(name)
    except KeyError:
        raise HTTPException(404, "no profile named '%s'" % name)
    view = _view(persona)
    view["status"] = _status_of(name)
    return {"profile": view}


@router.post("/profiles/{name}/launch")
def v1_launch_profile(name: str,
                      api_key=Depends(require_bearer(_keys.SCOPE_LAUNCH))):
    """Launch a profile's browser (async — returns immediately).

    The webhook ``profile.launched`` / ``profile.failed`` events report the
    real outcome; poll ``GET /profiles/{name}/status`` meanwhile.
    """
    manager = _CTX.profile_manager
    if manager is None:
        raise HTTPException(500, "profile manager not configured")
    if _CTX.launch_profile is None:
        raise HTTPException(
            503, "profile launching is not wired in this build")
    try:
        manager.get(name)
    except KeyError:
        raise HTTPException(404, "no profile named '%s'" % name)
    try:
        result = _CTX.launch_profile(name)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"profile": name, "status": result or "starting"}


@router.post("/profiles/{name}/stop")
def v1_stop_profile(name: str,
                    api_key=Depends(require_bearer(_keys.SCOPE_LAUNCH))):
    """Stop a running profile's browser."""
    if _CTX.stop_profile is None:
        raise HTTPException(
            503, "profile stopping is not wired in this build")
    try:
        result = _CTX.stop_profile(name)
    except KeyError:
        raise HTTPException(404, "profile '%s' is not running" % name)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"profile": name, "status": result or "stopped"}


@router.get("/profiles/{name}/status")
def v1_profile_status(name: str,
                      api_key=Depends(require_bearer(_keys.SCOPE_READ))):
    """Current launch status of a profile: starting/running/stopped/..."""
    manager = _CTX.profile_manager
    if manager is None:
        raise HTTPException(500, "profile manager not configured")
    try:
        manager.get(name)
    except KeyError:
        raise HTTPException(404, "no profile named '%s'" % name)
    return {"profile": name, "status": _status_of(name)}


# ---------------------------------------------------------------------------
# Key management (dashboard admin session)
# ---------------------------------------------------------------------------

@router.get("/keys")
def v1_list_keys(admin=Depends(require_dashboard_admin)):
    """List API keys (hashes never leave the server)."""
    return {"keys": get_key_store().list()}


@router.post("/keys", status_code=201)
def v1_issue_key(body: KeyCreateBody,
                 admin=Depends(require_dashboard_admin)):
    """Issue an API key. The raw key is returned exactly once."""
    try:
        raw, record = _keys.issue_api_key(
            get_key_store(), body.name, body.scopes,
            actor_role="admin")
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc))
    return {"key": raw, "record": record}


@router.delete("/keys/{key_id}")
def v1_revoke_key(key_id: int, admin=Depends(require_dashboard_admin)):
    """Revoke an API key by id."""
    try:
        revoked = _keys.revoke_api_key(
            get_key_store(), key_id, actor_role="admin")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    if not revoked:
        raise HTTPException(404, "no API key with id %d" % key_id)
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Webhook management (dashboard admin session)
# ---------------------------------------------------------------------------

@router.get("/webhooks")
def v1_list_webhooks(admin=Depends(require_dashboard_admin)):
    """List registered webhooks (secrets never returned)."""
    return {"webhooks": get_webhook_store().list()}


@router.post("/webhooks", status_code=201)
def v1_register_webhook(body: WebhookCreateBody,
                        admin=Depends(require_dashboard_admin)):
    """Register a webhook URL for one or more events."""
    try:
        record = get_webhook_store().register(
            body.url, events=body.events, secret=body.secret)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"webhook": record}


@router.delete("/webhooks/{webhook_id}")
def v1_unregister_webhook(webhook_id: int,
                          admin=Depends(require_dashboard_admin)):
    """Remove a webhook registration."""
    if not get_webhook_store().unregister(webhook_id):
        raise HTTPException(404, "no webhook with id %d" % webhook_id)
    return {"deleted": True}


@router.post("/webhooks/{webhook_id}/test")
def v1_test_webhook(webhook_id: int,
                    admin=Depends(require_dashboard_admin)):
    """Fire a synthetic ``profile.launched`` event at one webhook.

    Synchronous delivery so the dashboard can report success/failure
    immediately. Returns per-attempt result, never raises.
    """
    store = get_webhook_store()
    targets = [w for w in store.list() if w["id"] == webhook_id]
    if not targets:
        raise HTTPException(404, "no webhook with id %d" % webhook_id)
    # Re-read the secret server-side (list() deliberately omits it).
    row = store._conn.execute(
        "SELECT url, secret FROM webhooks WHERE id = ?",
        (webhook_id,)).fetchone()
    ok = _webhooks._deliver(row["url"], row["secret"], {
        "event": "webhook.test",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": "test delivery from the antidetect dashboard",
    })
    return {"delivered": ok}
