# Phase 0 — Build Notes

**Date:** 2026-10-07
**Status:** Complete (local commit; push handled by parent agent)

## What was built

Modular anti-detect browser core on vendored open-source components.
All browser/fingerprint code is vendored under `vendor/` (camoufox 0.5.8,
browserforge); the vendored libs still need pip deps — see `requirements.txt`
(`.venv/` is the dev virtualenv; it is gitignored).

| Module | File | Owner | Status |
|---|---|---|---|
| Vendor shim | `src/_vendor.py` | coordinator | ✅ inserts `vendor/` into `sys.path`; first import everywhere |
| Profiles | `src/profiles/manager.py` | Worker A | ✅ CRUD, SQLite `~/.antidetect-browser/profiles.db`, browserforge generation + hardcoded per-OS fallback |
| Fingerprints | `src/fingerprints/validator.py` | Worker B | ✅ 22 consistency checks, pure functions, never raises |
| Proxy | `src/proxy/manager.py` | Worker C | ✅ CRUD + TCP `test()`, separate `~/.antidetect-browser/proxies.db` |
| Browser | `src/browser/launcher.py` | Worker C | ✅ `launch_profile()` → `.page`/`.close()`; persistent per-profile user-data dir; `browser_binary_present()` guard |
| Health | `src/health/checker.py` | Worker D | ✅ consistency + best-effort headless IP probe; failures recorded, never raised |
| CLI | `src/cli.py` | Worker D | ✅ `profile/proxy/health/setup` subcommands, `python -m src.cli` |

## What works (verified)

- `profile create --os windows|macos|linux` → persona with all 18 contract keys;
  windows/macos validate **22/22**, linux 21/22 (browserforge occasionally emits a
  resolution outside the validator's strict common-resolution list — by design).
- `proxy add/list/delete/test`, `profile list/show/delete`, `health check`,
  `setup check` — full CLI cycle tested end-to-end, DBs left clean.
- `setup check` correctly reports the browser binary as MISSING here
  (installed on the user's machine via `python setup_browser.py`).

## What's stubbed / not yet done

- **Browser binary not installed in this environment** — real launches and the
  network leg of `health check` are untested here. `health check` skips the
  network probe with a clear message instead of triggering camoufox's
  auto-download.
- **Network probe is shallow** — visits `browserleaks.com/ip` and regexes an
  IPv4; no CreepJS/Pixelscan/IPHey harness yet (Phase 3 in the plan).
- **No GUI** — CLI only (Phase 3).
- **Proxy creds stored plaintext** in local sqlite (documented; single-user
  local machine assumption).
- **Chromium core evaluation** deferred to Phase 4 (plan); v1 is Camoufox/Firefox.

## Coordinator fixes applied after worker handoff

1. Installed missing vendored deps in `.venv` (`orjson`, `requests`, `pyyaml`,
   `ua-parser`, `screeninfo`, `numpy`, `playwright`, `platformdirs`,
   `language-tags`, `rich`, `rich-click`, `apify-fingerprint-datapoints`);
   added all to `requirements.txt`.
2. `cli.py`: `proxy add --type` now accepts all four types
   (`http|https|socks5|socks4`); simplified `_attach_proxy` to use the real
   `update(name, proxy=...)` API.
3. `manager.py`: linux default timezone `Europe/Berlin` → `America/New_York`
   so the default persona is region-consistent with the `en-US` locale.
4. Added `browser_binary_present()` (uses vendored `camoufox_path(
   download_if_missing=False)`); health checker skips the network probe when
   missing; `setup check` uses it instead of the wrong `~/.camoufox` path.

## Try it (on a machine with the binary)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python setup_browser.py          # one-time ~1.3 GB download
.venv/bin/python -m src.cli setup check
.venv/bin/python -m src.cli profile create --name client-1
.venv/bin/python -m src.cli health check --name client-1
```
