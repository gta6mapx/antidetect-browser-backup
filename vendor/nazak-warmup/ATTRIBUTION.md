# Attribution — vendored upstream code (Phase 6 warmup)

## Source

- **Upstream project:** nazak-browser-studio (https://github.com/wwewtech/nazak-browser-studio)
- **License:** MIT — "Copyright (c) 2026 Nazak Browser Studio Contributors" (full text in `LICENSE.MIT`)
- **Cloned:** 2026-10-08 (shallow clone); **commit:** `de06b50c6d447e16c6e6ef2fb964c0b5f9177bbc` (2026-10-06)
- **Evaluated in:** `/tmp/EVALUATION_P6.md` (Worker A, 2026-10-08)

MIT terms: verbatim copying is allowed provided the copyright + license
notice stay with any copied code. Attribution is therefore recorded here
and in the module docstrings of every adapted file under `src/warmup/`.

## What was taken VERBATIM (byte-for-byte copies of upstream files)

| File in `vendor/nazak-warmup/`            | Upstream source                                     |
|-------------------------------------------|-----------------------------------------------------|
| `warmup_engine.py`                        | `nazak/core/warmup_engine.py` (514 lines)          |
| `history_seeder.py`                       | `nazak/core/history_seeder.py` (229 lines)         |
| `test_scenario_engine_and_warmup.py`      | `tests/test_scenario_engine_and_warmup.py`          |
| `test_warmup_engine.py`                   | `tests/test_warmup_engine.py`                       |
| `LICENSE.MIT`                             | upstream `LICENSE` (renamed to avoid confusion)     |

## What was ADAPTED (not copied; rewritten for our stack)

Upstream code was NOT used as-is in `src/warmup/` because it assumes
**Chromium + CDP page attach** (`chromium.connect_over_cdp`) and a Chromium
`History` SQLite schema. Our stack is **Camoufox/Firefox with the sync
Playwright API**. The adaptations (all in `src/warmup/`):

1. **`src/warmup/engine.py`** — adapted executor:
   - Kept (wire-compatible): `ScenarioStep` / `WarmupScenario` dataclasses
     with identical `to_dict()`/`from_dict()` JSON shapes, the 6 step
     actions, `_clamped_seconds` dwell bounds, the cookie-consent JS.
   - Replaced: CDP page acquisition (`_PageSession`) → the executor now
     takes an already-open Playwright **page** (`execute(page, scenario)`).
     Async → sync API (our launcher is sync Camoufox).
   - Added: per-step `search_url_template` / `url` params so scenarios can
     be re-pointed at localhost in tests; uses `src.behavior.human` for
     scrolling instead of duplicating scroll logic.
2. **`src/warmup/history.py`** — adapted seeder:
   - Kept: the design (randomized visits over the past 1–14 days, realistic
     dwell, high-trust site list, visit chaining).
   - Rewrote: Chromium `History` SQLite → **Firefox `places.sqlite`**
     (`moz_places` / `moz_historyvisits`, PRTime = microseconds since the
     Unix epoch, Firefox `visit_type` codes LINK=1/TYPED=2/RELOAD=9).
3. **`src/warmup/scenarios.py`, `src/warmup/runner.py`** — original code
   (not upstream-derived): the 4 built-in scenarios ported with
   configurable URLs, scenario aliases, and the profile→launch→run→close
   runner built on our `launch_profile` / `ProfileManager`.

The upstream `nazak/gui/views/warmup_view.py` (PyQt6) and
`nazak/api/server.py` were used for **design reference only** (scenario
list/run endpoint shapes) — no code was copied from them.
