# Phase 1 — Build Notes

**Date:** 2026-10-07
**Status:** Complete (local commit; push handled by parent agent)
**Team:** 1 coordinator + 3 workers (behavior, geo-sync, detection harness)

## What was built

| Module | Files | Status |
|---|---|---|
| Behavior engine | `src/behavior/__init__.py`, `src/behavior/human.py` | ✅ human_type (wpm-distributed cadence + ~2% typo/correction), human_click (bezier path + overshoot), human_scroll (chunked + pauses), micro_pause, idle_break — all seedable via `new_rng`, zero fixed sleeps |
| Proxy geo-sync | `src/proxy/geosync.py` (new), `src/proxy/manager.py` (additive `assign_and_sync`) | ✅ 25-country timezone/locale tables, `suggest_timezone`/`suggest_locale`/`country_from_timezone`, best-effort `sync_persona_to_ip` (deep-copy, never raises) |
| Detection harness | `src/health/detectors.py` (new), `src/health/checker.py` (extended) | ✅ CreepJS + Pixelscan heuristic parsers, `check()` now returns 5 keys, documented 0–100 score |

## Score formula (documented in `HealthChecker.check` docstring)

- `consistency_part = 60 × (passed / total)`
- `detection_part = 30 × avg(clean=1.0, unknown=0.5, suspicious=0.25, bot=0.0)` over parsed sites only; skipped/errored excluded; 15.0 neutral if none parsed
- `network_part = 10` if network ok else 0
- `score = round(sum, 1)`, floored at 0

## Verification (coordinator smoke test, all passed)

- `import src.cli` OK; Phase 0 intact (profile 18 keys, validator 22/22 on windows)
- Geo helpers: `Europe/Berlin`, `ja-JP`, `FR`; no-proxy path returns note, input unmutated
- Behavior signatures match spec; detectors return clean/clean/unknown on test inputs
- `HealthChecker().check({'name':'x'})` → all 5 keys, score 31.4, detection legs skipped with 'run setup_browser.py first' (no binary here)
- `assign_and_sync` end-to-end: proxy attached to profile; geo lookup fails gracefully on this server (ip-api.com 403) with note, no raise

## Known limitations / still stubbed

- **Geo-sync direct-lookup limitation** (documented in `geosync.py`): the ip-api.com lookup is a direct request, so it resolves *this machine's* egress IP, not the proxy's. True proxy-egress resolution (routing the lookup through the proxy) is future work.
- **Detection parsers are heuristic**: CreepJS/Pixelscan are JS-heavy and change markup often; a parse miss yields 'unknown', never a crash. Mid-band Pixelscan scores (21–79) deliberately stay 'unknown'.
- **No real browser legs tested here** (binary absent): detection + network site visits are code-complete but untested against live sites. Test on a machine with `setup_browser.py` done.
- **CLI wiring**: `behavior` primitives and `assign_and_sync` are library-level; no new CLI subcommands added in Phase 1 (optional follow-up: `proxy sync --profile --proxy`).
- Proxy creds still plaintext in local sqlite (Phase 0 documented assumption, unchanged).

## Coordinator fixes applied after worker handoff

- None needed — all three workers met their contracts on first pass. Only verification was run; no code changes.
