"""External automation API package (``/api/v1`` + webhooks).

Pattern learned from J1BON/BZ-Browser (MIT,
https://github.com/j1bon/bz-browser); implementation is original.

Public surface
--------------
- :func:`mount_api` — mount the v1 router and the Bearer-bypass middleware
  on the GUI's FastAPI app. The GUI's own session auth stays the default;
  only ``/api/v1/*`` requests carrying ``Authorization: Bearer <key>`` skip
  the session check. Because the GUI's session middleware cannot be edited
  from here, the bypass middleware verifies the key itself and then injects
  a short-lived dashboard session cookie for an existing admin user so the
  request reaches the router. The v1 router enforces the key's scopes
  itself, and its key/webhook-management endpoints reject any request that
  arrived via Bearer (``request.state.api_key`` marker) — an external key
  can never mint new keys, whatever session rides along.
- :func:`on_profile_launched`, :func:`on_profile_stopped`,
  :func:`on_profile_failed` — webhook fire hooks. Wire these into the
  profile lifecycle (launch success / stop / launch failure) so external
  automation receives ``profile.launched`` / ``profile.stopped`` /
  ``profile.failed`` events.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _StarletteRequest

from src.api import keys, v1, webhooks

__all__ = [
    "keys", "v1", "webhooks",
    "mount_api",
    "on_profile_launched", "on_profile_stopped", "on_profile_failed",
]


# ---------------------------------------------------------------------------
# Webhook fire hooks (server calls these from the profile lifecycle)
# ---------------------------------------------------------------------------

def on_profile_launched(name: str) -> None:
    """Fire ``profile.launched`` — call after a profile's browser is up."""
    webhooks.dispatch(webhooks.EVENT_PROFILE_LAUNCHED, name)


def on_profile_stopped(name: str) -> None:
    """Fire ``profile.stopped`` — call after a profile's browser is closed."""
    webhooks.dispatch(webhooks.EVENT_PROFILE_STOPPED, name)


def on_profile_failed(name: str, error: str) -> None:
    """Fire ``profile.failed`` — call when a profile launch fails."""
    webhooks.dispatch(webhooks.EVENT_PROFILE_FAILED, name,
                      extra={"error": str(error)[:500]})


# ---------------------------------------------------------------------------
# Mount helper
# ---------------------------------------------------------------------------

_bearer_store: keys.ApiKeyStore | None = None


def _bearer_key_store() -> keys.ApiKeyStore:
    """Shared ApiKeyStore for the bypass middleware (lazy)."""
    global _bearer_store
    if _bearer_store is None:
        _bearer_store = keys.ApiKeyStore()
    return _bearer_store


def _api_session_username() -> str | None:
    """Username of an existing admin dashboard user (for session injection).

    The GUI's auth middleware only lets requests through with a valid
    session cookie; Bearer-authenticated /api/v1 calls borrow a session
    for an existing admin so the request reaches the router. The router's
    own ``require_bearer`` enforces the key's scopes, and
    ``require_dashboard_admin`` rejects any Bearer request outright, so the
    borrowed session grants nothing beyond the v1 routes.
    """
    try:
        from src.gui import server as _srv
        # Mirror create_app(): a fresh install seeds the admin user first.
        _srv._seed_admin_if_empty()
        users = _srv._user_store.list_users()
    except Exception:
        return None
    for user in users:
        if user.get("role") == "admin":
            return user.get("username")
    return users[0].get("username") if users else None


async def _bearer_bypass_middleware(request, call_next):
    """Verify ``Authorization: Bearer`` on ``/api/v1/*`` and let it through.

    Steps: (1) non-/api/v1/ requests, or /api/v1/ without a Bearer header,
    pass through untouched (dashboard session flow). (2) With a Bearer
    header, the key is verified here; invalid keys get an immediate 401.
    (3) Valid keys are stashed on ``request.state.api_key`` and a session
    cookie for an existing admin user is injected into the request headers
    so the GUI's session middleware passes the request to the router.
    """
    path = request.url.path
    auth = request.headers.get("authorization", "")
    if not (path.startswith("/api/v1/") and auth.lower().startswith("bearer ")):
        return await call_next(request)

    token = auth[7:].strip()
    try:
        record = _bearer_key_store().verify_bearer(token)
    except Exception:
        record = None
    if record is None:
        return JSONResponse({"error": "invalid or revoked API key"},
                            status_code=401)
    # Marker read by v1.require_bearer (skip re-verify) and
    # v1.require_dashboard_admin (reject Bearer outright).
    request.state.api_key = record

    username = _api_session_username()
    if not username:
        return JSONResponse({"error": "no dashboard user available"},
                            status_code=503)
    from src.security.session import COOKIE_NAME, create_session_token
    session = create_session_token(username)
    existing_cookie = request.headers.get("cookie")
    cookie_val = "%s=%s" % (COOKIE_NAME, session)
    if existing_cookie:
        cookie_val += "; " + existing_cookie
    scope = dict(request.scope)
    raw_headers = [(k, v) for k, v in scope.get("headers", [])
                   if k.lower() != b"cookie"]
    raw_headers.append((b"cookie", cookie_val.encode("latin-1")))
    scope["headers"] = raw_headers
    # Same scope dict -> request.state (api_key marker) is shared.
    request = _StarletteRequest(scope, request.receive)
    return await call_next(request)


def mount_api(app, profile_manager_getter, *,
              launch_profile=None, stop_profile=None,
              profile_status=None, profile_view=None):
    """Mount the external API on a FastAPI app.

    Args:
        app: the FastAPI application (from ``src.gui.server.create_app``).
        profile_manager_getter: zero-arg callable returning the live
            :class:`src.profiles.manager.ProfileManager`.
        launch_profile: optional ``fn(name) -> status`` that starts a
            profile's browser via the server's own lifecycle (shares the
            dashboard's ``_running`` registry). When omitted,
            ``POST /api/v1/profiles/{name}/launch`` answers 503.
        stop_profile: optional ``fn(name) -> status`` that stops a profile.
        profile_status: optional ``fn(name) -> str`` launch status lookup.
        profile_view: optional ``fn(persona) -> dict`` profile serializer.

    Returns:
        The mounted ``APIRouter``.
    """
    v1.configure(v1.ApiContext(
        profile_manager_getter,
        launch_profile=launch_profile,
        stop_profile=stop_profile,
        profile_status=profile_status,
        profile_view=profile_view,
    ))
    # NOTE: FastAPI >= 0.142 changed include_router() to a lazy
    # _IncludedRouter that does not resolve routes (all /api/v1/* 404).
    # Register the routes directly, BEFORE the "/" static Mount —
    # Starlette matches in order and the Mount would swallow /api/v1/*.
    from starlette.routing import Mount
    mount_idx = next(
        (i for i, r in enumerate(app.router.routes)
         if isinstance(r, Mount)), len(app.router.routes))
    for j, route in enumerate(v1.router.routes):
        app.router.routes.insert(mount_idx + j, route)
    # Added LAST so it runs FIRST (Starlette middleware ordering) — the
    # Bearer check must precede the session middleware's 401/302.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_bearer_bypass_middleware)
    return v1.router
