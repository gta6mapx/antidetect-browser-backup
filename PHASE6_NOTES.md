# Phase 6 — Warm-up Scenarios (anti-ban) — Notes

## What was integrated, from where
- **Upstream:** `wwewtech/nazak-browser-studio` @ `de06b50`, **MIT** ("Copyright (c) 2026 Nazak Browser Studio Contributors").
- **Vendored verbatim** → `vendor/nazak-warmup/` (byte-verified): `warmup_engine.py`, `history_seeder.py`, both test files, `LICENSE.MIT`, `ATTRIBUTION.md` (verbatim-vs-adapted table).
- **Adapted** → `src/warmup/`:
  - `engine.py` — sync executor, wire-compatible dataclasses + JSON round-trip; CDP page attach → page injection (takes our Playwright page); scroll delegates to `src.behavior.human.human_scroll`; URL sanitizer (http/https/about); `google_search` URL re-pointable via `search_url_template`.
  - `history.py` — Chromium History schema → Firefox `places.sqlite` (`moz_places`/`moz_historyvisits`, PRTime microseconds, visit types 1–9); writes profile dir root.
  - `scenarios.py` — 4 built-ins (youtube/ecommerce/crypto/finance) with URL params; aliases.
  - `runner.py` — `WarmupRunner.run(profile_name, scenario, headless, seed)` → result dict, never raises.
- **Wired (additive):** `src/gui/server.py` (+ `GET /api/warmup/scenarios`, `POST /api/warmup/run` → 202 background, `GET /api/warmup/status`), `src/gui/static/` ("Warm up" button + modal), `src/cli.py` (`warmup <profile> [--scenario] [--headless/--no-headless]`), `src/warmup/README.md`.
- Upstream PyQt6 GUI + REST server used as design reference only.

## Test results (coordinator-verified, this server)
- **Offline:** `tests/test_warmup_p6.py` — **28/28 pass** (fake-page double, dwell clamping, JSON round-trip, aliases, URL re-pointing, never-raises paths, places.sqlite schema).
- **Live (real headless Camoufox 156.0.1, local dummy HTTP server — no external sites touched):**
  - 7-step localhost scenario: **7/7 steps OK** (open_url ×2, accept_cookie_dialog, human_scroll ×2, dwell ×2)
  - Server observed both page visits in order; consent button clicked; total 30.8s (dwell times respected)
  - `WarmupRunner.run` end-to-end: 7/7, success, zero errors; profile session dir persisted
- **History seeder:** 20 URLs → 54 visits, valid Firefox visit types (1,2,8,9), PRTime correct.
- **Regression:** `import src.cli` OK; ConsistencyValidator **22/22** on fresh persona.

## Known limitations
- Built-in scenario defaults still point at real sites (google/youtube/amazon/...) — by design; re-point via URL params for offline use. Never hammer real sites from automation infra.
- `ANTIDETECT_HOME` env var still not honored (pre-existing, all phases).
- GUI warmup endpoints compile-checked only (fastapi runs where the server runs).
- Pre-existing unrelated failure: `tests/test_migrate_p5.py::test_adspower_nested_and_group_becomes_client_tag` (fails on clean tree too).

## Try it
```bash
python -m src.cli warmup my-profile --scenario youtube --headless
# or: dashboard → profile row → "Warm up" → pick scenario
```
