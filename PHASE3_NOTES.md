# Phase 3 Notes — AdsPower-style GUI + high-entropy persona generator

## What was built (coordinator + 3 workers)

### 1. GUI backend — `src/gui/server.py` (Worker A)
FastAPI app, `create_app() -> FastAPI`; `python -m src.gui.server` serves on
127.0.0.1:8765. Endpoints: `GET /api/profiles` (with live status +
`proxy_label` + `last_used`), `POST /api/profiles` (201/400/409),
`DELETE /api/profiles/{name}` (404/409-if-running),
`POST /api/profiles/{name}/launch` (headful, daemon thread, status
`starting`→`running`), `POST .../stop`, `GET .../health` (full
HealthChecker dict), `GET /api/proxies` (no passwords),
`POST /api/bulk/import` (multipart CSV). All errors are JSON
`{"error": msg}`. Statics mounted at `/` from `src/gui/static/`.
Deliberate guard: `/launch` returns 409 when the Camoufox binary is absent
instead of triggering a ~1.3 GB surprise download. Deps added:
fastapi, uvicorn, python-multipart (in requirements.txt + repo .venv).

### 2. GUI frontend — `src/gui/static/` (Worker B, redirected mid-phase)
Evaluated `anhnnp/persona-studio` (MIT, commit
`d46e984cfbdcd7278063e793e6ab1ad95b8cdc78`): its React dashboard is hard-wired
to its own engine API (id-keyed profiles, trust/warmup/schedules/engines
endpoints we will never have), so vendoring would have meant a Vite build +
effective rewrite. Decision: faithful **vanilla-JS port of its design
language** (ink-violet dark theme), no build step, no CDNs, offline-capable.
Full mapping in `src/gui/README.md` (includes MIT attribution).
UI: top bar (search, + New Profile, Bulk Import), sidebar tag filters, stats
row, profile table (status dot, name, client, OS, proxy, last used,
Launch/Stop toggle, Health modal with score + consistency checklist,
Delete with confirm), 5s auto-refresh preserving UI state, error toasts.

### 3. High-entropy persona generator — `src/fingerprints/generator.py` (Worker C)
Fixes the Phase 2 finding (browserforge's per-OS pool too small: 23
collisions per 10 same-OS personas). `generate_persona(name, os, rng=None,
**overrides)` returns exactly the 18 contract keys. **Firefox UAs only**
(Camoufox = Firefox engine; Chrome UAs would fail validation). ~20 realistic
Firefox version strings per OS (majors 128–142), 14 validator-legal screen
resolutions, per-OS font bases (23–31) with dropout keeping ≥80% + a pinned
validator marker font, per-OS WebGL pools, 25-country timezone/locale from
`geosync` tables, `canvas_seed=secrets.token_hex(8)`.
`ProfileManager.create(..., generator="high_entropy")` (default);
`"browserforge"` keeps the old path. Additive only — CLI/bulk/GUI unaffected.

## Verification (coordinator, independent re-runs)
- 60/60 generated personas (20 per OS) pass ConsistencyValidator **22/22**.
- 100 windows personas, pairwise duplicates of 4950 pairs (own run):
  user_agent 164 (29 distinct), viewport 549 (14), **canvas_seed 0 (100)**,
  webgl_renderer 321 (16), fonts 3 (97), timezone 219 (25). macOS spot-check
  consistent. Remaining duplicates are the statistical floor of each
  realistic pool (UA/viewport/timezone pools are inherently small — real
  users share them too); high-signal fields (canvas_seed, fonts) are ~unique.
- FastAPI: all endpoints live-tested against a running server — create,
  duplicate→409, bad-os→400/422, static `/` (title + css/js refs), health
  (consistency 22/22, browser legs skipped without binary), launch→409 guard,
  404 JSON, bulk import (2 created), proxies (no passwords), delete.
- `src/gui/static/app.js` calls match backend endpoint shapes; index.html
  references style.css + app.js relatively. `import src.cli` still works.
- Test profiles cleaned from the DB after verification.

## Known limitations / follow-ups
- Server cold start is slow (~60s: heavy vendored imports). Consider lazy
  imports in server.py later.
- `ANTIDETECT_HOME` env var is not honored; DB paths hardcode
  `~/.antidetect-browser`. Minor; flag for a config pass.
- Real browser legs (launch, detection site visits) still untested here —
  needs a machine with `setup_browser.py` done.
- LaunchPool (Phase 2) not yet wired into the GUI; GUI launches directly.
  Fine for on-demand UX, revisit if bulk-launch is ever needed.
