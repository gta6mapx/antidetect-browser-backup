# Phase 8 — Theme + Team + Hardening

**Date:** Oct 8, 2026 | **Team:** coordinator + 2 workers (theme+RBAC / hardening)

## 1. Light theme
- `src/gui/static/style.css` refactored to CSS variables (48 vars). Dark violet stays the `:root` default.
- Light AdsPower-style theme (white surfaces, `#1a73e8` accents, dark text) via `data-theme="light"` on `<html>`.
- Header sun/moon toggle button; choice persisted in `localStorage` (`ad_theme`); applied before first paint via inline `<head>` script.

## 2. RBAC
- `src/security/users.py` — `UserStore` (SQLite `users` table; pbkdf2-sha256 260k iters, stdlib only; roles admin|member|viewer; last-admin delete/demote guard → 409).
- `src/security/session.py` — HMAC-signed `ad_session` cookie (HttpOnly, SameSite=Lax; 12h TTL; 0600 secret file).
- `src/gui/server.py` — auth middleware: public = /login, /api/login, /api/logout, favicon; unauthenticated → 401 JSON for /api/*, 302 → /login for pages.
- Role matrix: **admin** = everything; **member** = everything except `/api/users*`; **viewer** = GET only (POST/PUT/DELETE → 403).
- Routes: `GET /login` (new `src/gui/static/login.html`), `POST /api/login|/api/logout`, `GET /api/me`, admin-only `GET/POST /api/users`, `DELETE /api/users/{u}`, `POST /api/users/{u}/role`.
- First-boot seed: empty users table → random 16-char admin password printed once + saved to `<data>/.admin_seed` (0600). Dashboard header shows user chip (username + role) with Logout.

## 3. Hardening
- **UA version auto-match** (fixes Phase-3 bug: persona said Firefox 135, binary was 156): `installed_firefox_version()` in `src/browser/launcher.py` reads the installed Camoufox dir (version.json → folder name fallback; never downloads, never raises). `generate_persona()` uses the installed major (narrow recent pool fallback when no binary). New validator check **#23 `ua_matches_installed_browser`** — fails on UA-major ≠ installed-major, passes-with-note when binary unknown.
- **ANTIDETECT_HOME everywhere:** new `src/paths.py` (`data_dir()` + 13 helpers; default legacy `~/.antidetect-browser` if it exists, else `~/.antidetect` — kept legacy to avoid orphaning existing data). Migrated 11 offenders incl. the new users/session DBs and admin seed.
- **Key rotation:** `crypto.rotate_key(old,new)` re-encrypts all `enc:` proxy passwords (idempotent; InvalidToken on wrong old key); `replace_master_key()`; CLI `key-rotate` (flags or hidden prompts, never logs keys); `SECURITY.md` documents the key model + recovery.
- **CLI parity:** added `sync start/stop/status` (pidfiles + SIGINT), `sessions backup/restore/list/health`, `key-rotate`.

## 4. Live test results (coordinator-verified)
- Theme: light block + toggle + localStorage + before-paint script all present in served files.
- RBAC (TestClient): `/` unauth → 302; viewer login → cookie; viewer GET 200 / POST **403**; admin `/api/users` 200; member `/api/users` **403**; member POST /api/profiles 201; unauth API → 401.
- UA: installed = (156, '156.0.1'); generated persona UA major = 156; validator **23/23**; check #23 passes.
- ANTIDETECT_HOME=temp: `profiles.db` + all helper paths resolve under the temp dir.
- Key rotation: raw DB shows `enc:` marker; rotate → raw bytes changed; decrypts with NEW key; `get()` still returns plaintext.
- Full suite: **89/89 pass** (69 pre-existing + 20 new `tests/test_hardening_p8.py`).

## 5. Known limitations / follow-ups
- `sync stop` uses POSIX SIGINT — Windows needs a different mechanism.
- GUI key-rotation endpoint not wired (RBAC stub reserves it); CLI is the supported path.
- Bump `_FALLBACK_UA_VERSIONS` as new Firefox releases ship (installed-binary path needs no maintenance).
- No user-management UI (API only).
- Cookie lacks `Secure` flag (plain HTTP on 127.0.0.1 would break otherwise).
