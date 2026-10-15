import src._vendor  # noqa: F401  -- makes vendored deps importable; must stay first

"""Local HTTP control server for the anti-detect browser GUI.

Exposes profile management, launching, health checks, proxies and bulk
import over a JSON REST API, and serves the frontend static bundle at ``/``.

Run from the repo root with::

    python -m src.gui.server

which starts uvicorn on ``127.0.0.1:8765``.
"""

import datetime
import os
import secrets
import string
import threading

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from src.bulk.importer import bulk_import
from src.health.checker import HealthChecker
from src import paths as _paths
from src.profiles.manager import ProfileManager
from src.proxy.manager import ProxyManager
from src.security.session import (
    COOKIE_NAME, SESSION_TTL_HOURS, create_session_token,
    verify_session_token,
)
from src.security.users import ROLES, UserStore
from src.sync import SyncManager

_VALID_OS = ("windows", "macos", "linux")

# Admin seed file location, honoring ANTIDETECT_HOME (see src.paths).
def _admin_seed_file() -> str:
    return _paths.admin_seed_path()

# RBAC: admin-only URL prefixes under /api (user management, and any future
# key-rotation endpoints). member = everything else; viewer = GET only.
_ADMIN_ONLY_PREFIXES = ("/api/users",)

# Paths that never require a session.
_AUTH_PUBLIC = {"/login", "/api/login", "/api/logout", "/favicon.ico"}

# ---------------------------------------------------------------------------
# Shared (module-level) state
# ---------------------------------------------------------------------------
_profile_manager = ProfileManager()
_proxy_manager = ProxyManager()
from src.team.activity import ActivityLog
_activity_log = ActivityLog()


def _actor(request) -> str:
    """Return the username for the current request (for activity log)."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return user.get("username", "unknown")
    return getattr(user, "username", "unknown") or "unknown"

# running[name] = {"status": "starting"|"running"|"stopped",
#                  "launched": LaunchedProfile|None,
#                  "error": str|None}
_running = {}
_running_lock = threading.Lock()

# last_used[name] = ISO-8601 UTC timestamp of the last successful launch
_last_used = {}

# Sync sessions live here so the endpoints stay thin; the manager owns its
# threads and closes session browsers on stop.
_sync_manager = SyncManager(persona_getter=_profile_manager.get)

# User accounts for the local dashboard (RBAC).
_user_store = UserStore()


def _seed_admin_if_empty():
    """First-boot seeding: create the ``admin`` user with a random password.

    Runs once per server start, and only when the users table is empty.
    The password is printed to the console ONCE with a CHANGE THIS warning
    and written to ``<data>/.admin_seed`` with mode 0600 so it is not lost.
    """
    if _user_store.count() > 0:
        return
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(16))
    _user_store.create_user("admin", password, role="admin")
    os.makedirs(os.path.dirname(_admin_seed_file()), exist_ok=True)
    fd = os.open(_admin_seed_file(),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("admin username: admin\nadmin password: %s\n" % password)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(_admin_seed_file(), 0o600)
    print("=" * 64)
    print("FIRST RUN: admin user created for the dashboard.")
    print("  username: admin")
    print("  password: %s" % password)
    print("CHANGE THIS password after first login. The credentials were")
    print("also saved to %s (mode 0600)." % _admin_seed_file())
    print("=" * 64, flush=True)


async def _auth_middleware(request: Request, call_next):
    """Session check + RBAC for every request.

    - Public paths (/login, /api/login, /api/logout, favicon) pass through.
    - Missing/invalid session: 401 JSON for /api/*, 302 to /login for pages.
    - viewer role: GET requests only (any POST/PUT/DELETE -> 403).
    - member role: everything except admin-only prefixes (user management,
      key rotation).
    - admin role: everything.
    """
    path = request.url.path
    if path in _AUTH_PUBLIC:
        return await call_next(request)
    payload = verify_session_token(request.cookies.get(COOKIE_NAME))
    user = None
    if payload:
        try:
            user = _user_store.get_user(payload.get("u"))
        except KeyError:
            user = None
    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"error": "authentication required"},
                                status_code=401)
        return RedirectResponse("/login", status_code=302)
    request.state.user = user
    if path.startswith("/api/"):
        role = user["role"]
        if role == "viewer" and request.method != "GET":
            return JSONResponse(
                {"error": "forbidden: the viewer role is read-only"},
                status_code=403)
        if (role == "member"
                and path.startswith(_ADMIN_ONLY_PREFIXES)):
            return JSONResponse(
                {"error": "forbidden: administrators only"},
                status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _profile_view(persona):
    """Shape a persona dict for the public /api/profiles contract."""
    with _running_lock:
        entry = _running.get(persona["name"])
        status = entry["status"] if entry is not None else "stopped"
        last_used = _last_used.get(persona["name"])
        error = entry.get("error") if entry is not None else None
    proxy = persona.get("proxy") or {}
    if proxy.get("name"):
        proxy_label = proxy["name"]
    elif proxy.get("host") and proxy.get("port") is not None:
        proxy_label = "%s:%s" % (proxy["host"], proxy["port"])
    else:
        proxy_label = None
    return {
        "name": persona.get("name"),
        "os": persona.get("os"),
        "engine": persona.get("engine") or "camoufox",
        "client_tag": persona.get("client_tag"),
        "proxy_label": proxy_label,
        "status": status,
        "last_used": last_used,
        "error": error,
    }


def _engine_for(persona):
    """Return the Engine for a persona dict (dispatch on its engine field)."""
    from src.engines import get_engine
    engine_name = (persona.get("engine") or "camoufox"
                   if isinstance(persona, dict) else "camoufox")
    return get_engine(engine_name)


def _v1_request():
    """Minimal request stand-in for v1 adapters (no HTTP layer)."""
    import types
    return types.SimpleNamespace(state=types.SimpleNamespace(user=None))


def _v1_status(name):
    with _running_lock:
        entry = _running.get(name)
        return entry["status"] if entry is not None else "stopped"


def _launch_worker(name, persona):
    """Daemon-thread body: launch the browser, then record the outcome."""
    try:
        engine = _engine_for(persona)
        # Auto-download the browser binary (and fpgen model) if missing.
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] == "downloading":
                pass  # status already set by the endpoint
        if not engine.binary_present():
            print("[DOWNLOAD] browser binary missing for '%s'; downloading..."
                  % name)
            if not engine.ensure_binary():
                raise RuntimeError(
                    "browser binary download failed; check your internet "
                    "connection and try again")
            print("[DOWNLOAD] browser binary ready for '%s'" % name)
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] == "downloading":
                entry["status"] = "starting"
        launched = engine.launch_profile(persona, headless=False)
    except Exception as exc:
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] == "starting":
                entry["status"] = "stopped"
                entry["launched"] = None
                entry["error"] = str(exc)[:500]
        print("[LAUNCH ERROR] profile '%s': %s" % (name, exc))
        try:
            from src.api import on_profile_failed
            on_profile_failed(name, str(exc)[:500])
        except Exception:
            pass
        return
    with _running_lock:
        entry = _running.get(name)
        if entry is None or entry.get("status") != "starting":
            # The profile was stopped (or removed) while the browser was
            # launching; do not leak a window the user cannot see in the GUI.
            still_wanted = False
        else:
            still_wanted = True
            entry["status"] = "running"
            entry["launched"] = launched
            _last_used[name] = _now_iso()
            try:
                from src.api import on_profile_launched
                on_profile_launched(name)
            except Exception:
                pass
    if not still_wanted:
        try:
            launched.close()
        except Exception:
            pass


def _close_all_running():
    """Best-effort close of every launched browser (shutdown path)."""
    with _running_lock:
        entries = list(_running.values())
        _running.clear()
    for entry in entries:
        launched = entry.get("launched") if entry else None
        if launched is not None:
            try:
                launched.close()
            except Exception:
                pass
        if entry is not None:
            entry["status"] = "stopped"
            entry["launched"] = None
    # Sync sessions own their browsers on their own threads; stop them too.
    try:
        for session in _sync_manager.status().get("sessions", []):
            try:
                _sync_manager.stop(session["session_id"])
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ProfileCreate(BaseModel):
    """Body for POST /api/profiles."""

    name: str
    os: str = "windows"
    proxy_name: str | None = None
    client_tag: str | None = None
    engine: str | None = None  # "camoufox" (default) or "chromium"/"patchright"
    copy_from: str | None = None  # copy fingerprint/settings from this profile


class ProfileUpdate(BaseModel):
    """Body for PUT /api/profiles/{name}. All fields optional."""

    client_tag: str | None = None
    os: str | None = None
    engine: str | None = None
    proxy_name: str | None = None  # set to "" to detach proxy
    group_name: str | None = None  # set to "" or null to ungroup
    timezone: str | None = None
    locale: str | None = None
    user_agent: str | None = None
    platform: str | None = None
    screen_width: int | None = None
    screen_height: int | None = None
    webgl_vendor: str | None = None
    webgl_renderer: str | None = None
    hardware_concurrency: int | None = None
    device_memory: int | None = None
    regenerate_fingerprint: bool = False
    platform_acct: str | None = None
    startup_urls: list | None = None
    custom_proxy: dict | None = None  # {type, host, port, username, password, save_name}
    remark: str | None = None
    cookie_json: str | None = None
    # Advanced tab
    ext_mode: str | None = None
    sync_mode: str | None = None
    bsettings_mode: str | None = None
    random_fingerprint: bool | None = None
    # Fingerprint modes (AdsPower-style)
    timezone_mode: str | None = None
    language_mode: str | None = None
    webrtc_mode: str | None = None
    location_mode: str | None = None
    location_perm: str | None = None
    display_lang_mode: str | None = None
    screen_mode: str | None = None
    fonts_mode: str | None = None
    webgl_mode: str | None = None
    webgpu_mode: str | None = None
    noise_canvas: bool | None = None
    noise_webgl: bool | None = None
    noise_audio: bool | None = None
    noise_mediadevice: bool | None = None
    noise_clientrects: bool | None = None
    noise_speech: bool | None = None


class SyncStart(BaseModel):
    """Body for POST /api/sync/start."""

    master: str
    followers: list[str] = []
    headless: bool = True
    typing: bool = True


class CookieExportRequest(BaseModel):
    """Body for POST /api/cookies/export."""

    profile: str
    fmt: str = "cookie-editor"  # cookie-editor | netscape | playwright


class SyncStop(BaseModel):
    """Body for POST /api/sync/stop."""

    session_id: str


class SyncTyping(BaseModel):
    """Body for POST /api/sync/typing."""

    session_id: str
    enabled: bool


class WarmupRun(BaseModel):
    """Body for POST /api/warmup/run."""

    profile_name: str
    scenario: str = "youtube"  # alias (youtube/ecommerce/crypto/finance) or id


class LoginBody(BaseModel):
    """Body for POST /api/login."""

    username: str
    password: str


class UserCreateBody(BaseModel):
    """Body for POST /api/users (admin only)."""

    username: str
    password: str
    role: str = "member"


class UserRoleBody(BaseModel):
    """Body for POST /api/users/{username}/role (admin only)."""

    role: str


# Warm-up run state: key (profile_name, scenario) -> {"status": "running"|"done"|"error", "result": dict|None, "error": str|None}
_warmup_runs = {}
_warmup_lock = threading.Lock()


def _warmup_worker(profile_name, scenario):
    """Background thread: run a warm-up scenario, never raises out of run()."""
    try:
        from src.warmup import WarmupRunner

        result = WarmupRunner().run(profile_name, scenario=scenario, headless=True)
    except Exception as exc:  # WarmupRunner.run never raises; this is belt-and-suspenders
        result = {"success": False, "errors": ["worker error: %s" % exc]}
    with _warmup_lock:
        _warmup_runs[(profile_name, scenario)] = {
            "status": "done" if result.get("success") else "error",
            "result": result,
        }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Build and return the GUI backend FastAPI application."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        yield
        _close_all_running()

    app = FastAPI(title="Anti-Detect Browser GUI backend", lifespan=lifespan)

    # -- error handlers: every error is JSON {"error": "message"} ----------
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        return JSONResponse(
            {"error": str(exc.detail)},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        return JSONResponse(
            {"error": "invalid request: %s" % exc},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request, exc):
        return JSONResponse(
            {"error": "internal error: %s" % exc},
            status_code=500,
        )

    # -- auth: login page, session, RBAC --------------------------------
    # First-boot admin seeding happens here so a fresh install is never
    # left without a way in.
    _seed_admin_if_empty()
    app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_middleware)

    # Directory shared by the login page below and the static mount.
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static")

    @app.get("/login", include_in_schema=False)
    def login_page():
        """Standalone sign-in page (public)."""
        return FileResponse(os.path.join(static_dir, "login.html"))

    @app.post("/api/login")
    def login(body: LoginBody):
        """Verify credentials and issue the ``ad_session`` cookie."""
        role = _user_store.verify_login(body.username, body.password)
        if role is None:
            raise HTTPException(401, "invalid username or password")
        username = body.username.strip()
        token = create_session_token(username)
        resp = JSONResponse({"username": username, "role": role})
        resp.set_cookie(COOKIE_NAME, token,
                        max_age=int(SESSION_TTL_HOURS * 3600),
                        httponly=True, samesite="lax", path="/")
        return resp

    @app.post("/api/logout")
    def logout():
        """Clear the session cookie."""
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    @app.get("/api/me")
    def me(request: Request):
        """Current session's user (used by the dashboard boot check)."""
        user = request.state.user
        return {"username": user["username"], "role": user["role"]}

    # -- user management (admin only; enforced by the auth middleware) ----
    @app.get("/api/users")
    def list_users():
        """List users (no password hashes)."""
        return {"users": _user_store.list_users()}

    @app.post("/api/users", status_code=201)
    def create_user_endpoint(body: UserCreateBody):
        """Create a user."""
        if body.role not in ROLES:
            raise HTTPException(400, "role must be one of %s"
                                % (list(ROLES),))
        try:
            user = _user_store.create_user(body.username, body.password,
                                           body.role)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except KeyError as exc:
            raise HTTPException(409, str(exc))
        return {"user": user}

    @app.delete("/api/users/{username}")
    def delete_user_endpoint(username: str):
        """Delete a user; refuses to remove the last remaining admin."""
        try:
            target = _user_store.get_user(username)
        except KeyError:
            raise HTTPException(404, "no user '%s'" % username)
        if target["role"] == "admin":
            admins = [u for u in _user_store.list_users()
                      if u["role"] == "admin"]
            if len(admins) <= 1:
                raise HTTPException(
                    409, "cannot delete the last admin user")
        _user_store.delete_user(username)
        return {"deleted": True}

    @app.post("/api/users/{username}/role")
    def set_user_role_endpoint(username: str, body: UserRoleBody):
        """Change a user's role."""
        if body.role not in ROLES:
            raise HTTPException(400, "role must be one of %s"
                                % (list(ROLES),))
        try:
            target = _user_store.get_user(username)
        except KeyError:
            raise HTTPException(404, "no user '%s'" % username)
        if target["role"] == "admin" and body.role != "admin":
            admins = [u for u in _user_store.list_users()
                      if u["role"] == "admin"]
            if len(admins) <= 1:
                raise HTTPException(
                    409, "cannot demote the last admin user")
        _user_store.set_role(username, body.role)
        return {"user": _user_store.get_user(username)}

    # -- activity log -----------------------------------------------------
    @app.get("/api/activity")
    def list_activity(request: Request, limit: int = 100):
        """Recent activity log entries, newest first."""
        limit = max(1, min(limit, 500))
        return {"activity": _activity_log.list(limit=limit)}

    @app.delete("/api/activity")
    def clear_activity(request: Request):
        """Clear the activity log (admin only)."""
        user = request.state.user
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", "")
        if role != "admin":
            raise HTTPException(403, "admin only")
        _activity_log.clear()
        return {"cleared": True}

    # -- profiles ---------------------------------------------------------
    @app.get("/api/profiles")
    def list_profiles(page: int = 1, page_size: int = 100, search: str = "",
                      group: str = ""):
        """List profiles with pagination and search (for 1000+ profiles)."""
        all_profiles = _profile_manager.list()
        # Filter by search term (name).
        if search:
            search_lower = search.lower()
            all_profiles = [p for p in all_profiles
                            if search_lower in p.get("name", "").lower()]
        # Filter by group.
        if group:
            all_profiles = [p for p in all_profiles
                            if p.get("group_name") == group]
        total = len(all_profiles)
        # Paginate.
        page = max(1, page)
        page_size = max(1, min(page_size, 500))
        start = (page - 1) * page_size
        end = start + page_size
        profiles = all_profiles[start:end]
        return {
            "profiles": [_profile_view(p) for p in profiles],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }

    @app.post("/api/profiles", status_code=201)
    def create_profile(request: Request, body: ProfileCreate):
        """Create a new profile; optional proxy attached by name."""
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "name is required and must be "
                                     "non-empty")
        if body.os not in _VALID_OS:
            raise HTTPException(
                400, "os must be one of %s" % (list(_VALID_OS),))
        # Check for existing (non-deleted) profile. get() returns trashed
        # ones too, so filter by deleted_at.
        try:
            existing = _profile_manager.get(name)
            # If get() succeeded, check if it's actually deleted.
            # We need to check the raw DB; for now, try list with filter.
            profiles = _profile_manager.list(include_deleted=False)
            if any(p.get("name") == name for p in profiles):
                raise HTTPException(409, "profile '%s' already exists" % name)
            # It's trashed - allow reuse (will be cleared by _insert).
        except KeyError:
            pass
        proxy = None
        if body.proxy_name:
            try:
                proxy = _proxy_manager.get(body.proxy_name)
            except KeyError:
                raise HTTPException(
                    400, "unknown proxy_name '%s'" % body.proxy_name)
        try:
            from src.engines import normalize_engine_name
            engine = normalize_engine_name(body.engine or "camoufox")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # Copy mode: clone fingerprint/settings from source profile.
        copy_fp = None
        copy_data = {}
        if body.copy_from:
            try:
                src_prof = _profile_manager.get(body.copy_from)
                # Full clone: fingerprint, proxy, platform, tags, etc.
                # Use safe conversion — source fields may not be dicts.
                def _as_dict(v):
                    return dict(v) if isinstance(v, dict) else {}
                def _as_list(v):
                    return list(v) if isinstance(v, (list, tuple)) else []
                copy_data = {
                    "fingerprint": _as_dict(src_prof.get("fingerprint")),
                    "proxy": src_prof.get("proxy"),
                    "platform": _as_dict(src_prof.get("platform")),
                    "tags": _as_list(src_prof.get("tags")),
                    "remark": src_prof.get("remark") or "",
                    "group": src_prof.get("group") or "",
                }
            except KeyError:
                raise HTTPException(404, "source profile '%s' not found" % body.copy_from)
        try:
            # Use source proxy if not explicitly provided.
            if not proxy and copy_data.get("proxy"):
                proxy = copy_data["proxy"]
            persona = _profile_manager.create(
                name, os=body.os, proxy=proxy,
                client_tag=body.client_tag, engine=engine)
            if copy_data:
                updates = {}
                if copy_data.get("fingerprint"):
                    updates["fingerprint"] = copy_data["fingerprint"]
                if copy_data.get("platform"):
                    updates["platform"] = copy_data["platform"]
                if copy_data.get("tags"):
                    updates["tags"] = copy_data["tags"]
                if copy_data.get("remark"):
                    updates["remark"] = copy_data["remark"]
                if copy_data.get("group"):
                    updates["group"] = copy_data["group"]
                if updates:
                    _profile_manager.update(name, updates)
                    persona = _profile_manager.get(name)
        except ValueError as exc:
            # Race-safe: re-check existence to distinguish a duplicate
            # (409) from a bad argument (400).
            try:
                _profile_manager.get(name)
            except KeyError:
                raise HTTPException(400, str(exc))
            raise HTTPException(409, "profile '%s' already exists" % name)
        _activity_log.record(_actor(request), "profile.create", name)
        return {"profile": _profile_view(persona)}

    @app.get("/api/profiles/{name}")
    def get_profile(name: str):
        """Return the full persona for one profile (edit dialog)."""
        try:
            persona = _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        with _running_lock:
            entry = _running.get(name)
            status = entry["status"] if entry is not None else "stopped"
        view = _profile_view(persona)
        view["status"] = status
        # Full fingerprint fields for the edit dialog (AdsPower-style).
        screen = persona.get("screen") or {}
        viewport = persona.get("viewport") or {}
        view["fingerprint"] = {
            "os": persona.get("os"),
            "platform": persona.get("platform"),
            "user_agent": persona.get("user_agent"),
            "timezone": persona.get("timezone"),
            "locale": persona.get("locale"),
            "screen_width": screen.get("width"),
            "screen_height": screen.get("height"),
            "viewport_width": viewport.get("width"),
            "viewport_height": viewport.get("height"),
            "webgl_vendor": persona.get("webgl_vendor"),
            "webgl_renderer": persona.get("webgl_renderer"),
            "hardware_concurrency": persona.get("hardware_concurrency"),
            "device_memory": persona.get("device_memory"),
            "color_depth": persona.get("color_depth"),
            "touch_points": persona.get("touch_points"),
            "geolocation": persona.get("geolocation"),
            "proxy": persona.get("proxy"),
            # Fingerprint modes (AdsPower-style)
            "webrtc_mode": persona.get("webrtc_mode"),
            "timezone_mode": persona.get("timezone_mode"),
            "location_mode": persona.get("location_mode"),
            "location_perm": persona.get("location_perm"),
            "language_mode": persona.get("language_mode"),
            "display_lang_mode": persona.get("display_lang_mode"),
            "screen_mode": persona.get("screen_mode"),
            "fonts_mode": persona.get("fonts_mode"),
            "webgl_mode": persona.get("webgl_mode"),
            "webgpu_mode": persona.get("webgpu_mode"),
            "noise_canvas": persona.get("noise_canvas"),
            "noise_webgl": persona.get("noise_webgl"),
            "noise_audio": persona.get("noise_audio"),
            "noise_mediadevice": persona.get("noise_mediadevice"),
            "noise_clientrects": persona.get("noise_clientrects"),
            "noise_speech": persona.get("noise_speech"),
            "ext_mode": persona.get("ext_mode"),
            "sync_mode": persona.get("sync_mode"),
            "bsettings_mode": persona.get("bsettings_mode"),
            "random_fingerprint": persona.get("random_fingerprint"),
        }
        # Platform tab fields (top-level columns).
        view["platform_acct"] = persona.get("platform_acct")
        view["startup_urls"] = persona.get("startup_urls", [])
        view["remark"] = persona.get("remark")
        view["cookie_json"] = persona.get("cookie_json")
        return {"profile": view}

    @app.put("/api/profiles/{name}")
    def update_profile(name: str, body: ProfileUpdate):
        """Update a profile's fields. Refuses while running/starting."""
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] in (
                    "running", "starting", "downloading"):
                raise HTTPException(
                    409, "profile '%s' is %s; stop it before editing"
                    % (name, entry["status"]))
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        fields: dict = {}
        if body.client_tag is not None:
            fields["client_tag"] = body.client_tag
        if body.os is not None:
            if body.os not in _VALID_OS:
                raise HTTPException(
                    400, "os must be one of %s" % (list(_VALID_OS),))
            fields["os"] = body.os
        if body.engine is not None:
            try:
                from src.engines import normalize_engine_name
                fields["engine"] = normalize_engine_name(body.engine)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        if body.proxy_name is not None:
            if body.proxy_name == "":
                fields["proxy"] = None
            else:
                try:
                    fields["proxy"] = _proxy_manager.get(body.proxy_name)
                except KeyError:
                    raise HTTPException(
                        400, "unknown proxy_name '%s'" % body.proxy_name)
        if body.custom_proxy is not None:
            cp = body.custom_proxy
            ptype = (cp.get("type") or "").strip()
            host = (cp.get("host") or "").strip()
            try:
                port = int(cp.get("port", 0))
            except (ValueError, TypeError):
                port = 0
            if not ptype:
                fields["proxy"] = None
            elif not host or not (1 <= port <= 65535):
                raise HTTPException(400, "custom proxy needs host and port")
            else:
                if ptype not in ("http", "https", "socks5", "socks4"):
                    raise HTTPException(400, "bad proxy type '%s'" % ptype)
                proxy = {"type": ptype, "host": host, "port": port,
                         "username": cp.get("username") or None,
                         "password": cp.get("password") or None,
                         "ip_checker": cp.get("ip_checker") or None,
                         "change_ip_url": cp.get("change_ip_url") or None}
                save_name = (cp.get("save_name") or "").strip()
                if save_name:
                    try:
                        _proxy_manager.add(
                            save_name, host, port, ptype=ptype,
                            username=proxy["username"],
                            password=proxy["password"])
                        proxy["name"] = save_name
                    except ValueError:
                        pass  # name taken — attach without saving
                fields["proxy"] = proxy
        if body.group_name is not None:
            group_name = body.group_name.strip() or None
            if group_name is not None:
                try:
                    _profile_manager.assign_group(name, group_name)
                except ValueError as exc:
                    raise HTTPException(400, str(exc))
            else:
                _profile_manager.assign_group(name, None)
        # Fingerprint fields.
        for key in ("timezone", "locale", "user_agent", "platform",
                    "webgl_vendor", "webgl_renderer", "hardware_concurrency",
                    "device_memory", "timezone_mode", "language_mode",
                    "webrtc_mode", "location_mode", "location_perm",
                    "display_lang_mode",
                    "screen_mode", "fonts_mode", "webgl_mode", "webgpu_mode",
                    "noise_canvas", "noise_webgl", "noise_audio",
                    "noise_mediadevice", "noise_clientrects", "noise_speech",
                    "ext_mode", "sync_mode", "bsettings_mode",
                    "random_fingerprint"):
            val = getattr(body, key)
            if val is not None:
                fields[key] = val
        screen = {}
        if body.screen_width is not None:
            screen["width"] = body.screen_width
        if body.screen_height is not None:
            screen["height"] = body.screen_height
        if screen:
            try:
                persona = _profile_manager.get(name)
            except KeyError:
                raise HTTPException(404, "no profile named '%s'" % name)
            merged = dict(persona.get("screen") or {})
            merged.update(screen)
            fields["screen"] = merged
            # Keep viewport proportional if not separately managed.
            vp = dict(persona.get("viewport") or {})
            if body.screen_width is not None:
                vp["width"] = body.screen_width
            if body.screen_height is not None:
                vp["height"] = max(400, body.screen_height - 80)
            fields["viewport"] = vp
        if body.regenerate_fingerprint:
            try:
                from src.fingerprints.generator import generate_persona
                persona = _profile_manager.get(name)
                eng = fields.get("engine", persona.get("engine") or "camoufox")
                # generator uses firefox/chromium; engines use camoufox/patchright
                gen_engine = {"camoufox": "firefox",
                              "patchright": "chromium"}.get(eng, "firefox")
                fresh = generate_persona(
                    name, os=fields.get("os", persona.get("os", "windows")),
                    engine=gen_engine)
                # Update the nested fingerprint dict (UI reads prof.fingerprint.*).
                fp = dict(persona.get("fingerprint") or {})
                for key in ("user_agent", "platform", "timezone", "locale",
                            "screen", "viewport", "webgl_vendor",
                            "webgl_renderer", "hardware_concurrency",
                            "device_memory", "color_depth", "canvas_seed",
                            "fonts", "touch_points"):
                    if key in fresh:
                        fp[key] = fresh[key]
                fields["fingerprint"] = fp
            except Exception as exc:
                raise HTTPException(
                    500, "fingerprint regeneration failed: %s" % exc)
        try:
            persona = _profile_manager.update(name, **fields)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        # platform_acct, startup_urls, remark, cookie_json are top-level
        # columns, not fingerprint fields — update them directly.
        if (body.platform_acct is not None or body.startup_urls is not None
                or body.remark is not None or body.cookie_json is not None):
            import json as _json
            with _profile_manager._connect() as _conn:
                if body.platform_acct is not None:
                    _conn.execute(
                        "UPDATE profiles SET platform_acct = ? WHERE name = ?",
                        (body.platform_acct or None, name))
                if body.startup_urls is not None:
                    _conn.execute(
                        "UPDATE profiles SET startup_urls = ? WHERE name = ?",
                        (_json.dumps(body.startup_urls), name))
                if body.remark is not None:
                    _conn.execute(
                        "UPDATE profiles SET remark = ? WHERE name = ?",
                        (body.remark or None, name))
                if body.cookie_json is not None:
                    _conn.execute(
                        "UPDATE profiles SET cookie_json = ? WHERE name = ?",
                        (body.cookie_json or None, name))
        return {"profile": _profile_view(persona)}

    @app.delete("/api/profiles/{name}")
    def delete_profile(request: Request, name: str):
        """Delete a profile; refuses while the profile is running/starting."""
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] in ("running",
                                                         "starting"):
                raise HTTPException(
                    409, "profile '%s' is %s; stop it before deleting"
                    % (name, entry["status"]))
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        # Permanent delete so the name can be reused immediately.
        _profile_manager.delete(name, permanent=True)
        _activity_log.record(_actor(request), "profile.delete", name)
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Groups (AdsPower-style profile organization)
    # ------------------------------------------------------------------
    @app.get("/api/groups")
    def list_groups():
        """List all groups with profile counts."""
        return {"groups": _profile_manager.list_groups()}

    @app.post("/api/groups", status_code=201)
    def create_group(payload: dict):
        """Create a new profile group."""
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "group name is required")
        try:
            group = _profile_manager.create_group(
                name,
                remark=payload.get("remark"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return group

    @app.put("/api/groups/{name}")
    def update_group(name: str, payload: dict):
        """Update a group's remark."""
        try:
            _profile_manager.update_group(name, remark=payload.get("remark"))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"updated": True}

    @app.delete("/api/groups/{name}")
    def delete_group(name: str):
        """Delete a group; its profiles become ungrouped."""
        try:
            _profile_manager.delete_group(name)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"deleted": True}

    @app.post("/api/profiles/{name}/group")
    def assign_profile_group(name: str, payload: dict):
        """Assign a profile to a group (null group_name to ungroup)."""
        try:
            _profile_manager.assign_group(name, payload.get("group_name"))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"assigned": True}

    @app.post("/api/profiles/{name}/launch")
    def launch_profile_endpoint(request: Request, name: str):
        """Start the profile's browser (visible window) in a daemon thread."""
        try:
            persona = _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        # Refuse if a sync session holds this profile (it launched its own
        # browser instance; two instances on one profile dir collide on the
        # browser lock).
        try:
            for session in _sync_manager.status().get("sessions", []):
                held = [session.get("master")] + list(
                    session.get("followers", {}).keys())
                if name in held:
                    raise HTTPException(
                        409, "profile '%s' is in active sync session %s; "
                             "stop the sync session first"
                        % (name, session.get("session_id")))
        except HTTPException:
            raise
        except Exception:
            pass
        with _running_lock:
            entry = _running.get(name)
            # True duplicate protection: block only while a launch is
            # actually in progress (downloading binary / starting browser)
            # or the browser is running. No time-based cooldown — the user
            # must be able to relaunch immediately after a failure or stop
            # ("limit na lagao").
            import time
            if entry is not None and entry["status"] in (
                    "running", "starting", "downloading"):
                raise HTTPException(
                    409, "profile '%s' is already %s"
                    % (name, entry["status"]))
            # Auto-install: if the browser binary is missing, the worker thread
            # downloads it (status shows "downloading") instead of failing.
            try:
                engine = _engine_for(persona)
                binary_ok = engine.binary_present()
            except Exception:
                binary_ok = False
                engine = None
            _running[name] = {"status": "downloading" if not binary_ok else "starting",
                              "launched": None, "error": None,
                              "last_launch_at": time.time()}
        threading.Thread(target=_launch_worker, args=(name, persona),
                         daemon=True).start()
        _activity_log.record(_actor(request), "profile.launch", name)
        return {"status": "starting"}

    @app.post("/api/profiles/{name}/stop")
    def stop_profile(request: Request, name: str):
        """Stop a running profile's browser; never raises out of close()."""
        with _running_lock:
            entry = _running.get(name)
            if entry is None or entry["status"] not in ("running",
                                                        "starting"):
                raise HTTPException(
                    404, "profile '%s' is not running" % name)
            launched = entry.get("launched")
            entry["status"] = "stopped"
            entry["launched"] = None
            entry["last_launch_at"] = 0  # Allow immediate relaunch after stop.
        if launched is not None:
            try:
                launched.close()
            except Exception:
                pass
        _activity_log.record(_actor(request), "profile.stop", name)
        try:
            from src.api import on_profile_stopped
            on_profile_stopped(name)
        except Exception:
            pass
        return {"status": "stopped"}

    @app.get("/api/profiles/{name}/health")
    def profile_health(name: str):
        """Run the health checker for a profile persona."""
        try:
            persona = _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        return HealthChecker().check(persona)

    def _session_status_for(name: str) -> dict:
        """Core session-status logic (shared by single + bulk endpoints)."""
        import time
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        from src import paths as _paths
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
        pdir = _paths.profile_dir(safe)
        if not os.path.isdir(pdir):
            return {"status": "never", "detail": "Profile never launched"}
        # Deep check via rookie-cookies (MIT): real Google session cookies?
        try:
            from src.session.audit import google_session_cookies
            audit = google_session_cookies(pdir)
        except Exception:
            audit = {"present": [], "count": 0, "db": None}
        cookie_file = audit.get("db")
        if not cookie_file or os.path.getsize(cookie_file) < 1024:
            return {"status": "empty", "detail": "No saved login cookies"}
        age_days = (time.time() - os.path.getmtime(cookie_file)) / 86400
        size_kb = os.path.getsize(cookie_file) // 1024
        session_cookies = audit.get("present", [])
        gmail = len(session_cookies) > 0
        base = {"cookie_kb": size_kb, "age_days": round(age_days, 1),
                "google_session_cookies": session_cookies,
                "gmail_logged_in": gmail}
        if age_days > 30:
            return {"status": "stale",
                    "detail": "Cookies %.0f days old — may need re-login" % age_days,
                    **base}
        if gmail:
            return {"status": "healthy",
                    "detail": "Gmail session active (%.0f days old)" % age_days,
                    **base}
        return {"status": "no_gmail",
                "detail": "Has cookies but no Gmail session — login needed",
                **base}

    @app.get("/api/profiles/{name}/session-status")
    def profile_session_status(name: str):
        """Lightweight session/login health: checks cookies.sqlite on disk.

        Returns one of: never (never launched), empty (no cookies),
        stale (cookies older than 30 days), healthy.
        No browser launch needed — safe for 1000+ profiles.
        """
        return _session_status_for(name)

    @app.post("/api/profiles/session-status-bulk")
    def profiles_session_status_bulk(body: dict):
        """Session status for many profiles at once (dashboard bulk check)."""
        names = body.get("names", [])[:500]
        out = {}
        for n in names:
            try:
                out[n] = _session_status_for(n)
            except HTTPException as e:
                out[n] = {"status": "error", "detail": str(e.detail)}
            except Exception as e:
                out[n] = {"status": "error", "detail": str(e)}
        return {"statuses": out}

    @app.post("/api/profiles/bulk-assign-group")
    def profiles_bulk_assign_group(body: dict):
        """Assign many profiles to a group at once (1000-profile management)."""
        names = body.get("names", [])[:500]
        group_name = (body.get("group_name") or "").strip() or None
        ok, errors = 0, {}
        for n in names:
            try:
                _profile_manager.assign_group(n, group_name)
                ok += 1
            except (KeyError, ValueError) as e:
                errors[n] = str(e)
        return {"assigned": ok, "errors": errors}

    # -- analytics (VIP dashboard) -----------------------------------------
    _analytics_cache: dict = {"at": 0.0, "data": None}

    @app.get("/api/analytics/overview")
    def analytics_overview():
        """Master analytics: profiles, Gmail sessions, proxies, issues.

        Cached 60s — session audit walks profile dirs (fine at 1000 scale).
        """
        import time
        now = time.time()
        if _analytics_cache["data"] and now - _analytics_cache["at"] < 60:
            return _analytics_cache["data"]
        profiles = _profile_manager.list()
        total = len(profiles)
        running = sum(1 for p in profiles if (p.get("status") == "running"))
        # Session health via rookie-cookies audit (no browser launch).
        health = {"healthy": 0, "no_gmail": 0, "stale": 0,
                  "empty": 0, "never": 0, "error": 0}
        issues = []
        gmail_in = 0
        for p in profiles:
            name = p.get("name", "")
            try:
                st = _session_status_for(name)
            except Exception:
                health["error"] += 1
                continue
            s = st.get("status", "error")
            health[s] = health.get(s, 0) + 1
            if st.get("gmail_logged_in"):
                gmail_in += 1
            if s == "stale":
                issues.append({"type": "stale_session", "profile": name,
                               "detail": st.get("detail", "")})
            elif p.get("error"):
                issues.append({"type": "launch_error", "profile": name,
                               "detail": str(p["error"])[:120]})
        # Proxies + groups.
        try:
            n_proxies = len(_proxy_manager.list())
        except Exception:
            n_proxies = 0
        try:
            groups = _profile_manager.list_groups()
            n_groups = len(groups)
        except Exception:
            n_groups = 0
        # Engine split.
        engines = {}
        for p in profiles:
            e = p.get("engine") or "camoufox"
            engines[e] = engines.get(e, 0) + 1
        # Trust scores (fast consistency checks only, no network).
        trust_buckets = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
        trust_sum, trust_n = 0.0, 0
        try:
            from src.health.checker import HealthChecker
            _hc = HealthChecker()
            for p in profiles:
                try:
                    results = _hc._run_consistency(p)
                    tot = len(results) or 1
                    passed = sum(1 for r in results
                                 if isinstance(r, dict) and r.get("passed"))
                    score = 100.0 * passed / tot
                except Exception:
                    continue
                trust_sum += score
                trust_n += 1
                if score >= 80:
                    trust_buckets["excellent"] += 1
                elif score >= 60:
                    trust_buckets["good"] += 1
                elif score >= 40:
                    trust_buckets["fair"] += 1
                else:
                    trust_buckets["poor"] += 1
        except Exception:
            pass
        # Groups breakdown (top 8).
        group_counts: dict = {}
        for p in profiles:
            g = p.get("group_name") or "Ungrouped"
            group_counts[g] = group_counts.get(g, 0) + 1
        groups_top = sorted(group_counts.items(), key=lambda kv: -kv[1])[:8]
        # Disk usage.
        disk_mb, disk_n = 0.0, 0
        try:
            from src import paths as _paths
            proot = _paths.profiles_dir()
            for p in profiles:
                safe = "".join(c if (c.isalnum() or c in "-_") else "_"
                               for c in p.get("name", ""))
                pdir = os.path.join(proot, safe)
                if not os.path.isdir(pdir):
                    continue
                sz = 0
                for _r, _d, fs in os.walk(pdir):
                    for fn in fs:
                        try:
                            sz += os.path.getsize(os.path.join(_r, fn))
                        except OSError:
                            pass
                disk_mb += sz / (1024 * 1024)
                disk_n += 1
        except Exception:
            pass
        # Recent activity.
        try:
            recent = _activity_log.list(limit=12)
        except Exception:
            recent = []
        data = {
            "profiles": {"total": total, "running": running,
                         "stopped": total - running},
            "gmail": {"logged_in": gmail_in, "total": total,
                      "pct": round(100 * gmail_in / total) if total else 0},
            "sessions": health,
            "proxies": {"total": n_proxies},
            "groups": {"total": n_groups,
                       "top": [{"name": k, "count": v} for k, v in groups_top]},
            "engines": engines,
            "trust": {"avg": round(trust_sum / trust_n, 1) if trust_n else 0,
                      "buckets": trust_buckets, "scored": trust_n},
            "disk": {"total_mb": round(disk_mb, 1),
                     "avg_mb": round(disk_mb / disk_n, 1) if disk_n else 0},
            "activity": [{"action": a.get("action"), "target": a.get("target"),
                          "user": a.get("username"), "ts": a.get("ts"),
                          "detail": a.get("detail")}
                         for a in (recent or [])],
            "issues": issues[:50],
            "issue_count": len(issues),
        }
        _analytics_cache["at"] = now
        _analytics_cache["data"] = data
        return data

    # -- warmup (Phase 6) --------------------------------------------------
    @app.get("/api/warmup/scenarios")
    def warmup_scenarios():
        """List available warm-up scenarios (id/name/description)."""
        from src.warmup import list_scenarios

        return {"scenarios": list_scenarios()}

    @app.post("/api/warmup/run", status_code=202)
    def warmup_run(body: WarmupRun):
        """Start a warm-up scenario for a profile in a background thread.

        Returns immediately with ``started``; poll GET /api/warmup/status for
        the outcome.
        """
        try:
            _profile_manager.get(body.profile_name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % body.profile_name)
        from src.warmup import list_scenarios
        from src.warmup.scenarios import SCENARIO_ALIASES

        key = body.scenario.strip().lower()
        valid = set(SCENARIO_ALIASES) | {s["id"] for s in list_scenarios()}
        if key not in valid:
            raise HTTPException(
                400,
                "unknown scenario '%s'; expected one of %s"
                % (body.scenario, sorted(SCENARIO_ALIASES)),
            )
        with _warmup_lock:
            entry = _warmup_runs.get((body.profile_name, key))
            if entry is not None and entry["status"] == "running":
                raise HTTPException(
                    409,
                    "warm-up already running for profile '%s' (scenario '%s')"
                    % (body.profile_name, body.scenario),
                )
            _warmup_runs[(body.profile_name, key)] = {"status": "running", "result": None}
        threading.Thread(
            target=_warmup_worker, args=(body.profile_name, key), daemon=True
        ).start()
        return {
            "status": "started",
            "profile": body.profile_name,
            "scenario": key,
        }

    @app.get("/api/warmup/status")
    def warmup_status(profile_name: str, scenario: str):
        """Return the status of a warm-up run started via POST /api/warmup/run."""
        with _warmup_lock:
            entry = _warmup_runs.get((profile_name, scenario.strip().lower()))
        if entry is None:
            raise HTTPException(404, "no warm-up run for that profile/scenario")
        return {"profile": profile_name, "scenario": scenario, **entry}

    # -- synchronizer -------------------------------------------------
    @app.post("/api/sync/start", status_code=201)
    def sync_start(body: SyncStart):
        """Start a sync session mirroring the master onto the followers.

        Refuses (409) when any named profile is already running in the GUI:
        a sync session launches its own browser instances, and two
        instances on one profile directory would collide on the browser
        lock.
        """
        names = [body.master, *(body.followers or [])]
        with _running_lock:
            busy = [n for n in names
                    if _running.get(n, {}).get("status")
                    in ("running", "starting")]
        if busy:
            raise HTTPException(
                409, "profiles already running: %s; stop them first"
                % ", ".join(busy))
        try:
            session_id = _sync_manager.create(
                body.master, body.followers or [],
                headless=body.headless, typing_enabled=body.typing)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"session_id": session_id,
                "status": _sync_manager.status(session_id)}

    @app.post("/api/sync/stop")
    def sync_stop(body: SyncStop):
        """Stop a sync session and close its browsers."""
        try:
            final = _sync_manager.stop(body.session_id)
        except KeyError:
            raise HTTPException(
                404, "no sync session '%s'" % body.session_id)
        return {"stopped": True, "status": final}

    @app.get("/api/sync/status")
    def sync_status(session_id: str | None = None):
        """Status of one sync session, or all sessions when omitted."""
        try:
            return _sync_manager.status(session_id)
        except KeyError:
            raise HTTPException(
                404, "no sync session '%s'" % session_id)

    @app.post("/api/sync/typing")
    def sync_typing(body: SyncTyping):
        """Enable/disable keystroke mirroring for a running session."""
        try:
            return _sync_manager.set_typing(body.session_id, body.enabled)
        except KeyError:
            raise HTTPException(
                404, "no sync session '%s'" % body.session_id)

    # -- proxies ----------------------------------------------------------
    @app.get("/api/proxies")
    def list_proxies():
        """List proxies; passwords are never exposed."""
        return {"proxies": [
            {"name": p["name"], "host": p["host"], "port": p["port"],
             "type": p["type"]}
            for p in _proxy_manager.list()
        ]}

    @app.post("/api/proxies", status_code=201)
    def create_proxy(payload: dict):
        """Add a new proxy definition."""
        name = (payload.get("name") or "").strip()
        host = (payload.get("host") or "").strip()
        port = payload.get("port")
        if not name or not host or port is None:
            raise HTTPException(
                400, "name, host and port are required")
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise HTTPException(400, "port must be an integer")
        try:
            proxy = _proxy_manager.add(
                name, host, port,
                username=payload.get("username") or None,
                password=payload.get("password") or None,
                ptype=payload.get("type") or "http",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        proxy.pop("password", None)
        return proxy

    @app.put("/api/proxies/{name}")
    def update_proxy(name: str, payload: dict):
        """Update a proxy definition."""
        fields = {}
        for key in ("host", "port", "username", "password", "type"):
            if key in payload and payload[key] is not None:
                fields["ptype" if key == "type" else key] = payload[key]
        if "port" in fields:
            try:
                fields["port"] = int(fields["port"])
            except (TypeError, ValueError):
                raise HTTPException(400, "port must be an integer")
        try:
            proxy = _proxy_manager.update(name, **fields)
        except KeyError:
            raise HTTPException(404, "no proxy named '%s'" % name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        proxy.pop("password", None)
        return proxy

    @app.delete("/api/proxies/{name}")
    def delete_proxy(name: str):
        """Delete a proxy definition."""
        # Refuse if any profile currently uses this proxy.
        for persona in _profile_manager.list():
            fp = persona.get("fingerprint") or {}
            px = fp.get("proxy") or {}
            if px.get("name") == name:
                raise HTTPException(
                    409, "proxy '%s' is in use by profile '%s'"
                    % (name, persona["name"]))
        if not _proxy_manager.delete(name):
            raise HTTPException(404, "no proxy named '%s'" % name)
        return {"deleted": True}

    @app.post("/api/proxies/bulk-delete")
    def bulk_delete_proxies(request: Request, payload: dict):
        """Delete many proxies at once. Body: {"names": [...]}.

        Skips proxies currently assigned to a profile (reported).
        """
        names = payload.get("names") or []
        deleted, skipped = [], []
        in_use = {}
        for persona in _profile_manager.list():
            fp = persona.get("fingerprint") or {}
            px = fp.get("proxy") or {}
            if px.get("name"):
                in_use[px["name"]] = persona["name"]
        for name in names:
            name = str(name)
            if name in in_use:
                skipped.append({"name": name,
                                "reason": "in use by " + in_use[name]})
                continue
            try:
                if _proxy_manager.delete(name):
                    deleted.append(name)
                else:
                    skipped.append({"name": name, "reason": "not found"})
            except Exception as e:
                skipped.append({"name": name, "reason": str(e)[:80]})
        _activity_log.record(_actor(request), "proxies.bulk_delete",
                             detail="%d deleted" % len(deleted))
        return {"deleted": deleted, "skipped": skipped}

    @app.get("/api/proxies/pool-stats")
    def proxy_pool_stats():
        """Sticky pool health: {total, healthy, benched, pinned}."""
        return _proxy_manager.sticky_pool.pool_stats()

    @app.get("/api/proxies/pins")
    def proxy_pins():
        """All profile->proxy sticky pins."""
        return _proxy_manager.sticky_pool.pins()

    @app.get("/api/profiles/{name}/pinned-proxy")
    def profile_pinned_proxy(name: str):
        """Sticky proxy currently pinned to a profile (404 if none)."""
        proxy = _proxy_manager.sticky_pool.get_pinned(name)
        if proxy is None:
            raise HTTPException(404, "no pinned proxy for profile '%s'" % name)
        return proxy

    @app.post("/api/proxies/{name}/test")
    def test_proxy(name: str):
        """Test proxy reachability and measure latency."""
        try:
            _proxy_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no proxy named '%s'" % name)
        return _proxy_manager.test_with_latency(name)

    @app.get("/api/fingerprint/random-ua")
    def random_ua(os: str = "windows", browser: str = "chrome"):
        """Generate a fresh random User-Agent (AdsPower shuffle button).

        Query params: os (windows/macos/linux), browser (chrome/firefox).
        Returns a new random UA on every call. Chrome versions are picked
        from recent realistic releases (like AdsPower's kernel list).
        """
        import random as _random
        try:
            from src.fingerprints.generator import chrome_user_agent
            rng = _random.Random()
            os = os if os in ("windows", "macos", "linux") else "windows"
            if browser == "firefox":
                rv = rng.randint(120, 135)
                # Firefox UA templates per OS.
                ff_templates = {
                    "windows": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
                                "rv:{rv}) Gecko/20100101 Firefox/{rv}.0"),
                    "macos": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; "
                              "rv:{rv}) Gecko/20100101 Firefox/{rv}.0"),
                    "linux": ("Mozilla/5.0 (X11; Linux x86_64; rv:{rv}) "
                              "Gecko/20100101 Firefox/{rv}.0"),
                }
                ua = ff_templates[os].format(rv=rv)
            else:
                # Random recent Chrome version (AdsPower-style shuffle).
                major = rng.choice([131, 132, 133, 134, 135])
                build = rng.randint(0, 9999)
                patch = rng.randint(0, 200)
                version_full = f"{major}.0.{build}.{patch}"
                ua = chrome_user_agent(os, version_full=version_full)
            return {"user_agent": ua}
        except Exception as exc:
            raise HTTPException(500, "UA generation failed: %s" % exc)

    @app.get("/api/fingerprint/random-renderer")
    def random_renderer():
        """Pick a WebGL renderer from the OSS spoofer's known-good list.

        Uses vendor/fp-spoofer configs (browser-fingerprint-spoofer, MIT),
        not custom-generated strings.
        """
        import random as _random
        renderers = [
            "ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005916) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002487) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER (0x000021C4) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (AMD, AMD Radeon RX 580 (0x000067DF) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (Intel, Intel(R) HD Graphics 630 (0x00005912) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ]
        return {"renderer": _random.choice(renderers)}

    @app.post("/api/proxies/test-custom")
    def test_custom_proxy(payload: dict):
        """Test a custom proxy (host/port) without saving it.

        Body: {"type", "host", "port", "username", "password"}.
        Returns {"ok", "latency_ms"}.
        """
        import socket
        import time
        host = (payload or {}).get("host", "").strip()
        try:
            port = int((payload or {}).get("port", 0))
        except (ValueError, TypeError):
            port = 0
        if not host or not (1 <= port <= 65535):
            raise HTTPException(400, "valid host and port required")
        start = time.monotonic()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            ok = True
        except Exception:
            ok = False
        latency = int((time.monotonic() - start) * 1000) if ok else None
        return {"ok": ok, "latency_ms": latency}

    @app.post("/api/proxies/fetch-free")
    def fetch_free_proxies(request: Request, payload: dict = None):
        """Fetch free proxies for TESTING and add working ones.
        Body: {"max": 20}. WARNING: free proxies are unreliable and
        often flagged — testing only, never for production/client use.
        """
        from src.proxy.free_fetcher import fetch_and_test
        max_n = (payload or {}).get("max", 20)
        max_n = max(1, min(int(max_n), 50))
        working = fetch_and_test(max_proxies=max_n)
        added = []
        existing = {p["name"] for p in _proxy_manager.list()}
        n = 1
        for p in working:
            while f"free-{n}" in existing:
                n += 1
            name = f"free-{n}"
            n += 1
            try:
                _proxy_manager.add(name, p["host"], p["port"],
                                   ptype=p["type"])
                existing.add(name)
                added.append({"name": name, **p})
            except ValueError:
                pass
        _activity_log.record(_actor(request), "proxy.fetch_free",
                             detail="%d added" % len(added))
        return {"added": added, "count": len(added),
                "warning": "Free proxies are for testing only."}

    # ------------------------------------------------------------------
    # Extensions (per-profile)
    # ------------------------------------------------------------------
    @app.get("/api/profiles/{name}/extensions")
    def list_extensions(name: str):
        """List extensions installed for a profile."""
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        from src.extensions.manager import list_extensions as _list
        return {"extensions": _list(name)}

    @app.post("/api/profiles/{name}/extensions")
    async def upload_extension(name: str, file: UploadFile = File(...)):
        """Upload a .crx/.zip extension for a profile."""
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        # Refuse while running.
        with _running_lock:
            entry = _running.get(name)
            if entry is not None and entry["status"] in (
                    "running", "starting", "downloading"):
                raise HTTPException(
                    409, "profile '%s' is %s; stop it before editing"
                    % (name, entry["status"]))
        filename = file.filename or "extension"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".crx", ".zip"):
            raise HTTPException(
                400, "only .crx and .zip files are supported")
        import tempfile
        from src.extensions.manager import add_extension as _add
        with tempfile.NamedTemporaryFile(
                suffix=ext, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        try:
            item = _add(name, tmp_path,
                        name=os.path.splitext(filename)[0])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return item

    @app.delete("/api/profiles/{name}/extensions/{ext_name}")
    def delete_extension(name: str, ext_name: str):
        """Remove an extension from a profile."""
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        from src.extensions.manager import remove_extension as _remove
        try:
            _remove(name, ext_name)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"deleted": True}

    @app.post("/api/profiles/{name}/extensions/{ext_name}/toggle")
    def toggle_extension(name: str, ext_name: str, payload: dict):
        """Enable/disable an extension. Body: {"enabled": bool}."""
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "no profile named '%s'" % name)
        from src.extensions.manager import set_enabled as _set
        try:
            _set(name, ext_name, bool(payload.get("enabled", True)))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"updated": True}

    # -- proxy scraper (dedicated page) ----------------------------------
    @app.post("/api/scraper/fetch")
    def scraper_fetch(payload: dict):
        """Fetch verified free proxies for the warm-up tier.

        Body: {"limit": 20, "types": ["http","socks5"], "countries": ["US"],
               "anonymity": "elite", "google_only": true,
               "no_datacenter": true, "max_latency": 2000}.
        STANDING RULE: warm-up/testing only, never for real Gmail sessions.
        """
        limit = payload.get("limit", 20)
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 20
        types = payload.get("types") or None
        if types:
            types = [str(t).lower() for t in types
                     if str(t).lower() in ("http", "socks4", "socks5")]
            types = types or None
        countries = payload.get("countries") or None
        if countries:
            countries = [str(c).upper()[:2] for c in countries if str(c).strip()]
            countries = countries or None
        anonymity = str(payload.get("anonymity") or "").lower()
        if anonymity not in ("elite", "anonymous", "transparent"):
            anonymity = ""
        try:
            proxies = _proxy_manager.fetch_warmup_proxies(
                limit=limit, types=types, countries=countries,
                anonymity=anonymity,
                google_only=bool(payload.get("google_only")),
                no_datacenter=bool(payload.get("no_datacenter")),
                max_latency=int(payload.get("max_latency") or 0))
        except ImportError:
            raise HTTPException(500, "proxy-scraper-cli not installed")
        except (ConnectionError, ValueError) as e:
            raise HTTPException(502, str(e))
        return {"proxies": proxies, "count": len(proxies),
                "warning": "Warm-up/testing tier only — never for real Gmail sessions."}

    @app.get("/api/scraper/stats")
    def scraper_stats():
        """Hourly feed totals: max extractable proxies + breakdowns."""
        try:
            return _proxy_manager.warmup_feed_stats()
        except ConnectionError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/scraper/bulk-check")
    def scraper_bulk_check(payload: dict):
        """Test a pasted list of proxies in bulk.

        Body: {"proxies": ["host:port", "socks5://host:port", ...],
               "timeout": 10}. Lines: host:port, type://host:port,
        host:port:user:pass. STANDING RULE: warm-up/testing only.
        """
        lines = payload.get("proxies") or []
        if isinstance(lines, str):
            lines = lines.splitlines()
        lines = [str(x) for x in lines][:500]
        try:
            timeout = max(3, min(int(payload.get("timeout") or 10), 30))
        except (TypeError, ValueError):
            timeout = 10
        results = _proxy_manager.bulk_check_proxies(lines, timeout=timeout)
        working = [r for r in results if r.get("ok") and not r.get("error")]
        return {"results": results, "total": len(results),
                "working": len(working),
                "warning": "Warm-up/testing tier only — never for real Gmail sessions."}

    @app.post("/api/scraper/check")
    def scraper_check(payload: dict):
        """Health-check one scraped proxy endpoint. Body: {host, port, type}."""
        host = (payload.get("host") or "").strip()
        try:
            port = int(payload.get("port", 0))
        except (TypeError, ValueError):
            port = 0
        if not host or not (1 <= port <= 65535):
            raise HTTPException(400, "host and valid port required")
        ptype = (payload.get("type") or "http").strip().lower()
        return _proxy_manager.check_endpoint(host, port, ptype)

    @app.post("/api/scraper/save")
    def scraper_save(request: Request, payload: dict):
        """Save checked proxies into the proxy store. Body: {"proxies": [...]}."""
        items = payload.get("proxies", [])[:100]
        added, skipped = [], []
        existing = {p["name"] for p in _proxy_manager.list()}
        n = len(existing)
        for p in items:
            host = (p.get("host") or "").strip()
            try:
                port = int(p.get("port", 0))
            except (TypeError, ValueError):
                skipped.append(p)
                continue
            if not host or not (1 <= port <= 65535):
                skipped.append(p)
                continue
            n += 1
            name = "scraped-%d" % n
            try:
                _proxy_manager.add(name, host, port,
                                   ptype=(p.get("type") or "http"))
                added.append(name)
            except ValueError:
                skipped.append(p)
        _activity_log.record(_actor(request), "scraper.save",
                             detail="%d saved" % len(added))
        return {"added": added, "skipped": len(skipped)}

    @app.post("/api/proxies/bulk-import")
    def bulk_import_proxies(payload: dict):
        """Bulk import proxies from text lines.

        Body: {"text": "host:port:user:pass\\n...", "type": "http"}.
        Lines may be host:port, host:port:user:pass, or
        type://host:port:user:pass. Names auto-generated as proxy-N.
        """
        text = payload.get("text") or ""
        default_type = payload.get("type") or "http"
        import re
        added, skipped, errors = [], 0, []
        existing = {p["name"] for p in _proxy_manager.list()}
        n = 1
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            ptype = default_type
            m = re.match(r"^(socks5|http|https|ssh)://(.+)$", line, re.I)
            if m:
                ptype = m.group(1).lower()
                line = m.group(2)
            parts = line.split(":")
            if len(parts) < 2:
                errors.append(line)
                continue
            host, port_s = parts[0].strip(), parts[1].strip()
            try:
                port = int(port_s)
            except ValueError:
                errors.append(line)
                continue
            username = parts[2].strip() if len(parts) > 2 else None
            password = parts[3].strip() if len(parts) > 3 else None
            # Unique name.
            while f"proxy-{n}" in existing:
                n += 1
            name = f"proxy-{n}"
            n += 1
            try:
                _proxy_manager.add(name, host, port,
                                   username=username or None,
                                   password=password or None,
                                   ptype=ptype)
                existing.add(name)
                added.append(name)
            except ValueError:
                skipped += 1
        return {"added": added, "skipped": skipped,
                "errors": errors[:20], "error_count": len(errors)}

    # -- bulk import ------------------------------------------------------
    @app.post("/api/bulk/import")
    async def bulk_import_endpoint(file: UploadFile = File(...),
                                  client_tag: str | None = Form(None)):
        """Import profiles from an uploaded CSV file.

        ``file`` is the CSV upload; ``client_tag`` (optional form field) is
        the default tag for rows that leave the column blank.
        """
        data = await file.read()
        tmp_path = None
        try:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".csv",
                                            prefix="gui-import-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            result = bulk_import(tmp_path,
                                 client_tag=client_tag or None)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return {"created": result.get("created", 0),
                "skipped": result.get("skipped", []),
                "errors": result.get("errors", [])}

    @app.post("/api/profiles/export")
    def export_profiles(request: Request, body: dict):
        """Export profiles as JSON (for backup/sharing).

        Body: {"names": ["p1", "p2"]} or {"all": true}.
        Passwords are never included.
        """
        from fastapi.responses import JSONResponse
        names = body.get("names") or []
        if body.get("all"):
            profiles = _profile_manager.list()
        else:
            profiles = []
            for n in names:
                try:
                    profiles.append(_profile_manager.get(n))
                except KeyError:
                    pass
        # Strip sensitive fields.
        clean = []
        for p in profiles:
            pc = dict(p)
            fp = dict(pc.get("fingerprint") or {})
            px = fp.get("proxy")
            if isinstance(px, dict):
                px = {k: v for k, v in px.items() if k != "password"}
                fp["proxy"] = px
            pc["fingerprint"] = fp
            clean.append(pc)
        _activity_log.record(_actor(request), "profile.export",
                             detail="%d profiles" % len(clean))
        return JSONResponse({
            "exported_at": time.time(),
            "count": len(clean),
            "profiles": clean,
        })

    @app.post("/api/profiles/import", status_code=201)
    def import_profiles(request: Request, body: dict):
        """Import profiles from a JSON export (see /api/profiles/export).

        Skips names that already exist. Returns created/skipped lists.
        """
        items = body.get("profiles") or []
        created, skipped = [], []
        for item in items:
            name = (item.get("name") or "").strip()
            if not name:
                skipped.append("(unnamed)")
                continue
            try:
                _profile_manager.get(name)
                skipped.append(name)
                continue
            except KeyError:
                pass
            try:
                fp = item.get("fingerprint") or {}
                proxy = fp.get("proxy")
                proxy_name = proxy.get("name") if isinstance(proxy, dict) else None
                _profile_manager.create(
                    name,
                    os=item.get("os", "windows"),
                    proxy=proxy_name,
                    client_tag=item.get("client_tag"),
                    engine=item.get("engine", "camoufox"),
                )
                created.append(name)
            except (ValueError, KeyError):
                skipped.append(name)
        _activity_log.record(_actor(request), "profile.import",
                             detail="%d created" % len(created))
        return {"created": created, "skipped": skipped}

    # -- trash (soft delete) ------------------------------------------------
    @app.get("/api/trash")
    def list_trash():
        """List trashed (soft-deleted) profiles."""
        return {"trash": [_profile_view(p) for p in
                          _profile_manager.list_trash()]}

    @app.post("/api/trash/{name}/restore")
    def restore_profile(request: Request, name: str):
        """Restore a trashed profile."""
        if not _profile_manager.restore(name):
            raise HTTPException(404, "no trashed profile '%s'" % name)
        _activity_log.record(_actor(request), "profile.restore", name)
        return {"restored": True}

    @app.delete("/api/trash/{name}")
    def purge_profile(request: Request, name: str):
        """Permanently delete a trashed profile."""
        if not _profile_manager.delete(name, permanent=True):
            raise HTTPException(404, "no trashed profile '%s'" % name)
        _activity_log.record(_actor(request), "profile.purge", name)
        return {"deleted": True}

    @app.post("/api/trash/purge")
    def purge_old_trash(request: Request):
        """Permanently delete trash older than 30 days."""
        count = _profile_manager.purge_trash(older_than_days=30)
        return {"purged": count}

    # -- global preferences -----------------------------------------------
    @app.get("/api/preferences")
    def get_preferences():
        """Global defaults applied to new profiles."""
        return {"preferences": _profile_manager.all_settings()}

    # -- update check -----------------------------------------------------
    @app.get("/api/update-check")
    def update_check():
        """Compare local HEAD with GitHub main; tell the dashboard if
        a newer build exists. Best-effort: never raises."""
        local_sha = ""
        try:
            import subprocess
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            local_sha = out.stdout.strip()
        except Exception:
            pass
        remote_sha = ""
        try:
            import urllib.request, json as _json
            with urllib.request.urlopen(
                urllib.request.Request(
                    "https://api.github.com/repos/protectvibe14/antidetect-browser"
                    "/commits/main",
                    headers={"User-Agent": "antidetect-browser"}),
                timeout=8,
            ) as resp:
                remote_sha = _json.load(resp)["sha"][:7]
        except Exception:
            pass
        return {
            "local": local_sha,
            "remote": remote_sha,
            "update_available": bool(local_sha and remote_sha
                                     and local_sha != remote_sha),
        }

    @app.post("/api/preferences")
    async def set_preferences(request: Request):
        """Save global defaults. Body: {"preferences": {key: value}}."""
        body = await request.json()
        prefs = body.get("preferences") or {}
        for key, value in prefs.items():
            _profile_manager.set_setting(key, "" if value is None else str(value))
        _activity_log.record(_actor(request), "preferences.save", "")
        return {"saved": True}

    # -- migration: import profiles from other anti-detect browsers -------
    @app.post("/api/migrate/adspower")
    async def migrate_adspower_endpoint(file: UploadFile = File(...),
                                       source: str | None = Form(None)):
        """Import profiles from an AdsPower/GoLogin/Multilogin JSON export.

        ``file`` is the JSON upload (a dump of the AdsPower Local API
        ``{"data": {"list": [...]}}`` response, a plain list, or a
        hand-built file). ``source`` (optional form field, ``"adspower"``
        or ``"gologin"``) selects the importer; both share the same
        tolerant field aliasing, the choice only documents intent.
        """
        from src.migrate import import_adspower, import_gologin
        data = await file.read()
        tmp_path = None
        try:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".json",
                                            prefix="gui-migrate-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            importer = (import_gologin
                        if (source or "").lower() == "gologin"
                        else import_adspower)
            result = importer(tmp_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return {"created": result.get("created", 0),
                "skipped": result.get("skipped", []),
                "errors": result.get("errors", [])}

    # -- cookies: import/export a profile's live cookie jar ---------------
    @app.post("/api/cookies/export")
    def cookies_export_endpoint(body: CookieExportRequest):
        """Export a profile's cookies; needs the Camoufox binary.

        Body: ``{"profile": name, "fmt": "cookie-editor"|"netscape"|
        "playwright"}``. Returns ``{"path": ...}``; the profile is launched
        headless, so this refuses (409) when the browser binary is missing.
        """
        from src.cookies.manager import export_cookies
        result = export_cookies(body.profile, fmt=body.fmt or "cookie-editor")
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(409, result["error"])
        return {"path": result}

    @app.post("/api/cookies/import")
    async def cookies_import_endpoint(file: UploadFile = File(...),
                                      profile: str = Form(...),
                                      clear: bool = Form(False)):
        """Import cookies into a profile's cookie jar.

        ``file`` is the cookie file (Cookie-Editor JSON, Playwright
        storage-state, or Netscape ``cookies.txt`` — auto-detected);
        ``profile`` names the target profile; ``clear`` (form bool)
        empties the jar first when true.
        """
        from src.cookies.manager import import_cookies
        data = await file.read()
        tmp_path = None
        try:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".cookies",
                                            prefix="gui-cookies-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            result = import_cookies(profile, tmp_path, clear=bool(clear))
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        if result.get("error"):
            raise HTTPException(409, result["error"])
        return {"imported": result.get("imported", 0)}

    # -- RPA (Phase 7) ------------------------------------------------------
    # Additive: recipe CRUD, run control, and live page inspector for the
    # RPA engine (src/rpa). Adapted from python_rpa_ui (Apache-2.0);
    # its unsalted-SHA-256 auth.py is NOT used here.
    def _rpa_manager():
        """Return a process-wide RPAManager (lazy; never at import time)."""
        global _rpa_manager_singleton
        try:
            return _rpa_manager_singleton
        except NameError:
            from src.rpa.manager import RPAManager
            _rpa_manager_singleton = RPAManager()
            return _rpa_manager_singleton

    class RpaRunBody(BaseModel):
        """Body for POST /api/rpa/run."""
        recipe_id: str
        profile_name: str
        data_path: str | None = None
        headless: bool = True

    class RpaAnswerBody(BaseModel):
        """Body for POST /api/rpa/jobs/{id}/answer."""
        answer: str

    class RpaInspectBody(BaseModel):
        """Body for POST /api/rpa/inspect."""
        profile_name: str
        url: str
        login_steps: list | None = None
        headless: bool = True

    @app.get("/api/rpa/recipes")
    def rpa_list_recipes():
        """List RPA recipes (metadata)."""
        return {"recipes": _rpa_manager().list()}

    @app.get("/api/rpa/engine")
    def rpa_engine_info():
        """RPA engine info (RPAForge integration)."""
        try:
            from ..rpa.rpaforge import get_engine_info, list_webui_activities
            info = get_engine_info()
            info["activities"] = list_webui_activities()
            return info
        except Exception as e:
            return {"name": "RPAForge", "available": False, "error": str(e)[:200]}

    @app.post("/api/rpa/recipes", status_code=201)
    def rpa_create_recipe(body: dict):
        """Create an RPA recipe from a full recipe dict; returns its id."""
        try:
            rid = _rpa_manager().create(body.get("name", "untitled"),
                                        body)
        except Exception as exc:
            raise HTTPException(422, "invalid recipe: %s" % exc)
        return {"id": rid}

    @app.get("/api/rpa/recipes/{recipe_id}")
    def rpa_get_recipe(recipe_id: str):
        """Return a full recipe dict."""
        try:
            return _rpa_manager().get(recipe_id)
        except KeyError:
            raise HTTPException(404, "recipe not found")

    @app.delete("/api/rpa/recipes/{recipe_id}")
    def rpa_delete_recipe(recipe_id: str):
        """Delete a recipe."""
        if not _rpa_manager().delete(recipe_id):
            raise HTTPException(404, "recipe not found")
        return {"deleted": recipe_id}

    @app.post("/api/rpa/run", status_code=202)
    def rpa_run(body: RpaRunBody):
        """Start a recipe run on a profile. Returns immediately with job_id."""
        try:
            recipe_id = _rpa_manager().resolve_id(body.recipe_id)
        except KeyError:
            raise HTTPException(404, "recipe not found")
        job_id = _rpa_manager().run(recipe_id, body.profile_name,
                                    data_path=body.data_path,
                                    headless=bool(body.headless))
        return {"job_id": job_id}

    @app.get("/api/rpa/jobs")
    def rpa_list_jobs():
        """List known RPA jobs (newest first)."""
        return {"jobs": _rpa_manager().jobs()}

    @app.get("/api/rpa/jobs/{job_id}")
    def rpa_job_status(job_id: str):
        """Return a job status snapshot (status, summary, pending action,
        log tail)."""
        try:
            return _rpa_manager().job_status(job_id)
        except KeyError:
            raise HTTPException(404, "job not found")

    @app.get("/api/geoip/{ip}")
    def geoip_lookup(ip: str):
        """GeoIP lookup for profile table (AdsPower-style flags)."""
        try:
            from ..proxy import geoip
            r = geoip.lookup(ip)
            return {
                "country_code": r.get("country_code"),
                "country": r.get("country"),
                "city": r.get("city"),
                "timezone": r.get("timezone"),
            }
        except Exception as e:
            return {"error": str(e)[:100]}

    @app.get("/api/browsers/status")
    def browsers_status():
        """Browser download status (AdsPower-style download manager)."""
        result = {}
        # Camoufox.
        try:
            from ..engines.camoufox import CamoufoxEngine
            eng = CamoufoxEngine()
            result["camoufox"] = {
                "name": "Firefox (Camoufox)",
                "installed": bool(eng.binary_present()),
                "version": eng.installed_version(),
            }
        except Exception as e:
            result["camoufox"] = {"name": "Firefox (Camoufox)", "installed": False, "error": str(e)[:100]}
        # Patchright/Chromium.
        try:
            from ..engines.patchright_engine import PatchrightEngine
            eng = PatchrightEngine()
            present = eng.binary_present() if hasattr(eng, "binary_present") else False
            result["patchright"] = {
                "name": "Chromium (Patchright)",
                "installed": bool(present),
                "version": eng.installed_version() if hasattr(eng, "installed_version") else None,
            }
        except Exception as e:
            result["patchright"] = {"name": "Chromium (Patchright)", "installed": False, "error": str(e)[:100]}
        return result

    @app.post("/api/browsers/download/{engine}")
    def browsers_download(engine: str, background_tasks: BackgroundTasks):
        """Trigger browser download (AdsPower-style manual install)."""
        if engine not in ("camoufox", "patchright"):
            raise HTTPException(400, "unknown engine")
        def _do():
            try:
                if engine == "camoufox":
                    from ..engines.camoufox import CamoufoxEngine
                    CamoufoxEngine().ensure_binary()
                else:
                    from ..engines.patchright_engine import PatchrightEngine
                    PatchrightEngine().ensure_binary()
            except Exception:
                pass
        background_tasks.add_task(_do)
        return {"started": True, "engine": engine}

    @app.get("/api/profiles/{name}/cache")
    def profile_cache_info(name: str):
        """Cache size info for a profile (AdsPower-style Cache data)."""
        import os
        try:
            prof = _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "profile not found")
        # Profile data dir.
        data_dir = os.path.join(
            os.environ.get("ANTIDETECT_HOME", os.path.expanduser("~/.antidetect")),
            "profiles", name)
        total = 0
        if os.path.isdir(data_dir):
            for root, _, files in os.walk(data_dir):
                for f in files:
                    try: total += os.path.getsize(os.path.join(root, f))
                    except OSError: pass
        return {"size_mb": round(total / 1024 / 1024, 1), "path": data_dir}

    @app.delete("/api/profiles/{name}/cache")
    def profile_cache_clear(name: str):
        """Clear cache data for a profile."""
        import os, shutil
        try:
            _profile_manager.get(name)
        except KeyError:
            raise HTTPException(404, "profile not found")
        data_dir = os.path.join(
            os.environ.get("ANTIDETECT_HOME", os.path.expanduser("~/.antidetect")),
            "profiles", name)
        # Only clear cache-like subdirs, never the whole profile.
        cleared = 0
        for sub in ("cache", "Cache", "shader-cache", "blob_storage"):
            p = os.path.join(data_dir, sub)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                cleared += 1
        return {"cleared": cleared}

    @app.post("/api/rpa/jobs/{job_id}/stop")
    def rpa_job_stop(job_id: str):
        """Ask a running job to stop after the current row."""
        try:
            _rpa_manager().stop_job(job_id)
        except KeyError:
            raise HTTPException(404, "job not found")
        return {"stopped": job_id}

    @app.post("/api/rpa/jobs/{job_id}/answer")
    def rpa_job_answer(job_id: str, body: RpaAnswerBody):
        """Answer a pending human-handoff question (captcha/human_input/ask)."""
        try:
            ok = _rpa_manager().answer_action(job_id, body.answer)
        except KeyError:
            raise HTTPException(404, "job not found")
        if not ok:
            raise HTTPException(409, "no pending action for this job")
        return {"answered": job_id}

    @app.post("/api/rpa/inspect")
    def rpa_inspect(body: RpaInspectBody):
        """Inspect a live page on a profile: launch, navigate, scrape DOM,
        close. Returns inputs/selects/textareas/buttons (+ final_url)."""
        from src.rpa.inspector import inspect_url
        try:
            return inspect_url(body.profile_name, body.url,
                               login_steps=body.login_steps,
                               headless=bool(body.headless))
        except RuntimeError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            raise HTTPException(500, "inspect failed: %s" % exc)

    # -- frontend statics -------------------------------------------------
    # Mounted LAST so /api/* routes always win. The directory may be empty
    # (frontend worker creates the bundle later); makedirs keeps StaticFiles
    # from failing on a missing directory.
    os.makedirs(static_dir, exist_ok=True)

    # No-cache for JS/CSS during active development (prevents stale UI).
    from fastapi.staticfiles import StaticFiles as _SF

    class NoCacheStatic(_SF):
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            if path.endswith((".js", ".css")):
                resp.headers["Cache-Control"] = (
                    "no-store, no-cache, must-revalidate")
            return resp

    app.mount("/", NoCacheStatic(directory=static_dir, html=True),
              name="static")

    # Worker A integration: external automation API (keys + webhooks).
    # Mounted last so its Bearer-auth middleware runs before the dashboard
    # session middleware. Adapters close over the nested endpoints above.
    def _v1_launch(name):
        try:
            return launch_profile_endpoint(_v1_request(), name)["status"]
        except HTTPException as exc:
            if exc.status_code == 404:
                raise KeyError(name)
            raise ValueError(exc.detail)

    def _v1_stop(name):
        try:
            return stop_profile(_v1_request(), name)["status"]
        except HTTPException as exc:
            if exc.status_code == 404:
                raise KeyError(name)
            raise ValueError(exc.detail)

    def _v1_profile_view(name):
        try:
            return _profile_manager.get(name)
        except KeyError:
            return None

    from src.api import mount_api
    mount_api(app, lambda: _profile_manager,
              launch_profile=_v1_launch, stop_profile=_v1_stop,
              profile_status=_v1_status, profile_view=_v1_profile_view)

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
