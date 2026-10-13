# Phase 9 — Chromium Core Evaluation — Notes

**Date:** 2026-10-08
**Verdict: GO** — Patchright integrated as second engine alongside Camoufox.

## Evaluation (Worker A, /tmp/EVALUATION_P9.md)

- **Patchright 1.63.0, Apache-2.0** — vendoring-safe, 4.8k stars, actively maintained, auto-tracks Playwright.
- `navigator.webdriver = false` out of the box (driver-level patches); no `cdc_` keys; plugins non-empty.
- Persistent contexts work → login-once profile sessions OK.
- Chromium pinned build 1243 ≈ Chrome 153; 393MB unpacked.
- Rejected alternatives: nodriver (AGPL — license fail), undetected-chromedriver (Selenium stack — wrong fit), CloakBrowser (paywalled — fails $0 boundary).

## What was integrated (Worker B + coordinator fixes)

- `vendor/patchright/` — 1.63.0 extracted from PyPI wheel (137MB), Apache-2.0 LICENSE + ATTRIBUTION.md.
- `src/engines/` — `Engine` interface, `CamoufoxEngine` (thin wrapper, launcher untouched), `PatchrightEngine`, `get_engine()` factory with `chromium`/`chrome`/`firefox` aliases.
- Profile `engine` column (default `'camoufox'`, legacy rows backfilled).
- `generate_persona(..., engine="chromium")` — Chrome UA auto-matching installed Chromium major (mirrors Phase 8 logic).
- Engine-aware validator: 23 Firefox checks / 21 Chromium checks (+`ua_chrome_sane` rejects `HeadlessChrome`).
- CLI `profile create --engine`, GUI engine selector, health checker uses dispatch.

## Real bugs found & fixed during live testing (coordinator)

1. **Headless-shell missing**: `launch_persistent_context(headless=True)` looks for the
   `chromium_headless_shell` build; only full Chromium was cached. Fixed: `build_context_kwargs`
   now passes `executable_path` pointing at the resolved full binary — headless works either way.
2. **`add_init_script` silently no-op in patchright 1.63.0** (their anti-detection patches neuter
   the CDP-backed mechanism; page-level too). Fixed with `install_platform_spoof()`: page-level
   `evaluate` on existing + future pages (`context.on("page")`) and re-injection on every
   `framenavigated` (navigations reset the JS context).
3. Niche bug in my own edit (dropped a `def` line) — caught by import test, fixed immediately.

## Live test results (this server, headless, real binaries)

| Signal | Camoufox (Firefox 156) | Patchright (Chrome 153) |
|---|---|---|
| UA | Firefox/156 ✅ | Chrome/153, no HeadlessChrome token ✅ |
| navigator.webdriver | False ✅ | False ✅ |
| navigator.platform | Win32 (native) ✅ | Win32 (spoofed) ✅ |
| plugins.length | — | 5 ✅ |
| Validator | 23/23 ✅ | 21/21 ✅ |

Full suite: **113/113 green** (89 baseline + 24 new `tests/test_engines_p9.py`).

## Honest limitations

- Patchright is a **driver patch on stock Chromium, not a fingerprint-spoofing fork**. It hides
  automation tells; it does NOT spoof canvas/WebGL/fonts. Our consistency layer keeps personas
  coherent, but a Chromium fork would be needed for C++-level spoofing (none viable found under
  our license/$0 constraints).
- Platform spoof is best-effort: scripts reading `navigator.platform` at `document_start`,
  before our post-navigation re-injection, may see the host platform. Cross-OS personas are
  safest on Camoufox; on the Windows PC, Windows personas need no spoof at all.
- Sync sessions remain Camoufox-only (no shared-Playwright path for patchright yet).
- Switching an existing profile's engine does not regenerate its UA (validator correctly flags it).

## Recommendation: when to use which engine

- **Camoufox (default):** maximum fingerprint spoofing, cross-OS personas, sync sessions.
- **Patchright:** sites that block/flag Firefox, Chrome-only features, expanding the UA pool
  (Chrome is ~65% of real traffic — a second realistic pool reduces monoculture risk).
