# Phase 5 — Migration + Cookies (notes)

Date: 2026-10-08. Commit: (see git log)

## What was integrated (all open-source, not written from scratch)

Upstream: **anhnnp/persona-studio** (MIT, Copyright 2025 Ahsan, commit
`d46e984`). The dothesung fork was evaluated and found byte-identical —
upstream is the source of truth.

| Piece | Upstream file | Treatment |
|---|---|---|
| Profile import helpers (`_first`, `_norm_os`, `_parse_resolution`, `_parse_proxy`, `_unwrap`, `load_export`) | `engine/persona/importers.py` | Vendored VERBATIM into `src/migrate/importers.py` (+MIT header); `Proxy` model → plain dict |
| `profile_from_export` | same | ADAPTED to `ProfileManager.create` + `ProxyManager.add`; `group_name`/`tags` → `client_tag` |
| Cookie format logic (`normalize`, `parse`, `parse_netscape`, `to_netscape`, `dumps`, `read_file`) | `engine/persona/cookies.py` | Vendored VERBATIM into `src/cookies/manager.py` (+MIT header) |
| Cookie `read`/`write` | same | ADAPTED to `export_cookies`/`import_cookies` via our Camoufox launcher |
| Upstream tests | `engine/tests/test_importers.py`, `test_cookies.py` | Adapted to `tests/test_migrate_p5.py` (28/28 pass) |
| Upstream CLI | `engine/persona/cli.py` | REJECTED as code; wired into our `src/cli.py` instead |

Verbatim reference copies + MIT LICENSE + attribution in `vendor/persona-import/`
(`ATTRIBUTION.md` documents files, commit, and every adaptation).

## Bug fix vs upstream
`_unwrap` now also accepts the real AdsPower Local API nesting
`{"data": {"list": [...]}}` (upstream only checked one level deep). Without
this, real AdsPower `user/list` dumps import zero profiles.

## New modules / endpoints
- `src/migrate/importers.py`: `import_adspower`, `import_gologin` (best-effort),
  `detect_source` → `{'created','skipped','errors'}`; duplicates skipped.
- `src/cookies/manager.py`: `export_cookies(profile, fmt)` → path |
  `{'error'}`; `import_cookies(profile, path, clear=False)` → `{'imported'}`.
  Formats: `cookie-editor` (JSON), `playwright` (storage-state JSON),
  `netscape` (cookies.txt), auto-detected on import.
- GUI: `POST /api/migrate/adspower`, `POST /api/cookies/export`,
  `POST /api/cookies/import` + dashboard Import/Cookies buttons.
- CLI: `import-from <file>`, `cookies <profile> export|import`.
- `src/migrate/README.md`: supported field shapes per source.

## Live test results (this server)
- AdsPower-format import (`{"data":{"list":[...]}}`, 2 profiles): **2 created,
  rerun → 2 skipped**; `client_tag` mapped from `group_name`; proxy
  registered in ProxyManager. PASS.
- Cookie format roundtrips (no browser): netscape/playwright/cookie-editor
  all round-trip values correctly. PASS.
- Regression: `import src.cli` OK; validator 22/22 on sample. PASS.
- Cookie export→import roundtrip through real headless launches: **BLOCKED by
  environment, not code** — server load average was 30+ (other projects'
  ffmpeg workers), Camoufox/Juggler startup took 17s+, Playwright's pipe
  timeout fired before connect. Non-persistent launches still worked;
  persistent-context launches hung consistently under this load. To re-run
  when load is normal: `/tmp/cookie_leg.py` (kept) or the worker's original
  end-to-end (they reported PASS under lower load).
- Firefox quirks found by Worker B and baked into the code: domain-based
  `add_cookies` silently dropped → rewritten url-only; 400-day cookie
  lifetime cap → clamped; proxied launches need `geoip` → retry with proxy
  stripped (jar is proxy-independent).

## Known limitations
- `ANTIDETECT_HOME` env var still not honored (pre-existing, Phase 3).
- GoLogin/Multilogin import is best-effort via tolerant field aliasing;
  verified against upstream fixtures, not real exports.
- Upstream's `fingerprint_config.webgl_vendor/renderer` aliases were missing
  from their alias table — added (their own fixture uses them).
