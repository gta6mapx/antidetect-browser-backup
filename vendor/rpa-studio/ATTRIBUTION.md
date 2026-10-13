# Attribution — python_rpa_ui (RPA Studio)

- **Upstream:** https://github.com/anandhu1228/python_rpa_ui
- **License:** Apache License 2.0 (see `LICENSE` in this directory)
- **Vendored commit:** `d00f17902805583a68a1a099983642aa24b00e33`
  (cloned `--depth 1` on 2026-10-08; commit date 2026-06-24 +0530)

## What is verbatim (copied unmodified for reference)

| File here | Upstream source |
|---|---|
| `playwright_worker.py` | `backend/workers/playwright_worker.py` (918 lines) |
| `flow.js` | `frontend/js/flow.js` (964 lines) |
| `inspector.js` | `frontend/js/inspector.js` (312 lines) |
| `inspect_router_reference.py` | `inspect_page(page)` function extracted verbatim from `backend/routers/inspect_router.py` |
| `LICENSE` | upstream `LICENSE` (Apache-2.0, unchanged) |

These files are **reference only** — they are not imported by our runtime code.

## What was adapted (not vendored verbatim)

- `src/rpa/engine.py` — adapts the engine (`fill_field`, `execute_step`,
  `execute_login_steps`, `resolve_frame`, `check_error`, `load_data_file`,
  `human_delay`, `FieldError`) with page injection (`page=None` bypass) and a
  callback-based job registry replacing `backend/workers/job_store.py`.
- `src/rpa/recipes.py` — Pydantic models `Recipe` / `FlowStep` / `DelayConfig`
  ported from `backend/routers/recipe_router.py`; flat-file storage replaced
  with our SQLite `rpa_recipes` table.
- `src/rpa/inspector.py` — `inspect_page(page)` logic adapted from
  `inspect_router.py` against a live Playwright page (upstream's
  `INSPECTOR_SCRIPT` subprocess + own-Chromium launch dropped).
- `src/rpa/manager.py` — `RPAManager` orchestration (replaces
  `run_router.py` / `job_store.py`; drives OUR Camoufox profile pages).
- `src/gui/server.py` RPA endpoints — replaces the `/api/*` routers of
  upstream `backend/main.py`; upstream `backend/auth.py` (unsalted SHA-256)
  is dropped entirely and NOT used.

Per the Apache-2.0 license terms, this file and `LICENSE` provide the
required attribution. A modification notice for the adapted code lives in
each adapted file's docstring.
