# Phase 7 — RPA / Automation Recipes (python_rpa_ui integration)

**Date:** 2026-10-08 | **Upstream:** anandhu1228/python_rpa_ui @ d00f17902805583a68a1a099983642aa24b00e33, **Apache-2.0** (safe to adapt; attribution in vendor/rpa-studio/ATTRIBUTION.md)
**Strategy honored:** engine adapted from upstream, zero new third-party deps.

## What was built
- `vendor/rpa-studio/` — verbatim reference: `playwright_worker.py` (918-line engine), `flow.js`, `inspector.js`, inspector DOM-scraper extract, Apache-2.0 LICENSE, ATTRIBUTION.md.
- `src/rpa/`:
  - `engine.py` (1138 lines) — adapted engine; **key change: `run_job(..., page=None)` page injection** — when our Camoufox profile page is passed, the stock-Chromium launch AND teardown are skipped (the upstream `p.chromium.launch(headless=True)` bypass). All 13 field types, iframe resolution, error selectors, retries, new-tab handling, char-delay typing kept. In-process thread-safe job registry replaces upstream's disk `job_store.py`; human-handoff protocol (≤5min pending-action poll) kept.
  - `recipes.py` — verbatim Pydantic `Recipe`/`FlowStep`/`DelayConfig`; SQLite `rpa_recipes` table (same DB as ProfileManager).
  - `manager.py` — `RPAManager`: create/list/delete/resolve, `run(recipe, profile)` in daemon thread (launches OUR profile, injects its page, closes profile when done), `job_status`, `stop_job`, `answer_action`.
  - `inspector.py` — `inspect_page(page)` re-implemented on a live page (upstream's subprocess + string-templated script dropped as a security smell).
  - `README.md` — recipe format + deviation list.
- GUI: 8 additive endpoints (`/api/rpa/recipes`, `/run`, `/jobs`, `/inspect`…), dashboard "RPA" modal (recipe list/run, job polling, pending-action answer box, log viewer).
- CLI: `rpa list`, `rpa run <id|name> --profile <n> [--data f] [--visible]`, `rpa status <job_id>`, `rpa answer <job_id> <text>`.
- Dropped: upstream `auth.py` (unsalted SHA-256), disk log files, Docker storage paths.

## Live test (this server, real headless Camoufox profile) — PASS
Recipe: 1-step form fill (fullname + email literals) → submit → `#result-done` wait. Result:
- Job status `done`, summary `success: 1, failed: 0`
- Engine log: "Using injected page (browser launch/teardown skipped)", navigate + click observed
- Local server received exactly `{'fullname': ['Husnain Test'], 'email': ['test@example.com']}`
- Test profile + recipe cleaned up afterwards.

## Regression
- Full suite: **69/69 pass** (56 pre-existing + 13 new `test_rpa_p7.py`)
- `import src.cli` OK; ConsistencyValidator **22/22**; 8 RPA routes registered on `create_app()`.

## Honest flags
- Worker B's report claimed "58/58" tests; the file actually contains **13 tests** (all pass). Count corrected here.
- Video recording is lost on injected pages (upstream tied it to its own context creation); our launcher doesn't set `record_video_dir` yet.
- `data_path=None` runs the flow once with an empty row (upstream required a CSV).
- External HTTPS sites untestable here (server MITM proxy); live test used a local HTTP server.
- Pre-existing: `ANTIDETECT_HOME` not honored by new code either (same convention as rest of codebase).

## Commit
Local commit (parent pushes): Phase 7 changes staged for push.
