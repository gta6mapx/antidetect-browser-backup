# Anti-Detect Browser

Stronger and more secure than AdsPower. Modular build on an open-source core
(Camoufox + Patchright) for managing multiple client profiles without bans or
spam flags. Target: ~1,000 profiles, login-once persistence, strict isolation.

**Status (Oct 2026):** in testing — 140 automated tests pass; Windows-PC
acceptance testing in progress.

## Features

- **Profiles** — create/edit/delete, groups, bulk import/export (CSV),
  soft-delete with Trash + restore, 100/page server pagination, search
- **Fingerprinting** — 23-check consistency validator, high-entropy persona
  generator, per-profile language/timezone modes (Based on IP / Custom),
  geoip from proxy exit IP (maxminddb)
- **Proxies** — encrypted credential storage, health checks with protocol
  auto-detect (HTTP/SOCKS4/SOCKS5), bulk test/delete, per-profile assignment
- **Proxy Scraper** — 1,500+ free proxies hourly feed (proxy-scraper, MIT):
  filters (type/country/anonymity/Google-verified/latency), bulk test, copy
- **Engines** — Camoufox (Firefox) + Patchright (Chromium), per-profile choice
- **Synchronizer** — master window drives N followers (offsets/delays)
- **Warm-up** — human-behavior scenarios (ghost-cursor mouse, typing jitter)
- **RPA** — Playwright recipes run inside profiles
- **Session health** — Google cookie audit, trust score 0–100
- **Analytics** — premium dashboard (profiles, engines, health, issues)
- **Backup** — restic-based fleet backup/restore
- **Preferences** — global defaults for new profiles
- **In-app update check** — banner when a newer build is on GitHub
- **RBAC** — admin / member / viewer roles; light + dark themes

## Architecture (modular — one job per module)

```
src/
  profiles/      Profile CRUD + SQLite storage (one profile = one client identity)
  fingerprints/  Consistency validator — 23 checks, profile launches only if clean
  proxy/         Per-profile proxy assignment + health checks
  browser/       Camoufox/Patchright launcher (fingerprint + proxy + humanize)
  behavior/      Human behavior engine (typing, ghost-cursor mouse, scroll)
  health/        Detection-test harness (CreepJS, Pixelscan, BrowserLeaks, IPHey)
  gui/           FastAPI backend + dashboard (all features in the sidebar)
  sync/          Multi-window synchronizer
  warmup/        Warm-up scenarios
  rpa/           RPA recipes
  session/       Session backup/restore + cookie audit
  security/      RBAC, encrypted credentials
vendor/
  camoufox/      Vendored open-source browser core (MPL-2.0)
  browserforge/  Vendored fingerprint data generator (MIT)
```

## Quick start (Windows)

```bat
pc\setup.bat     :: first time: venv + deps + browser binary
pc\update.bat    :: pull latest from GitHub
pc\run.bat       :: start dashboard at http://127.0.0.1:8765
```

Linux:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m src.gui.server   # dashboard at http://127.0.0.1:8765
```

## Open-source integrations (license-verified)

| Project | License | Used for |
|---|---|---|
| camoufox | MPL-2.0 | Firefox anti-detect engine (vendored) |
| patchright | Apache-2.0 | Chromium engine (vendored) |
| browserforge / fpgen | MIT | Fingerprint data (vendored) |
| proxy-scraper | MIT | Free proxy feed (warm-up only) |
| python_ghost_cursor | MIT | Humanized mouse |
| maxminddb | Apache-2.0 | GeoIP for proxy exit-IP locale |
| PySocks | BSD | SOCKS proxy support |
| creepjs | MIT | In-browser lie detector (self-hosted) |
| rookie-cookies | MIT | Google session cookie audit |
| restic | BSD | Fleet backup |

## Rules for contributors (agents included)

- Every module owns its files — never edit another module's files.
- Cross-module imports only through each module's public functions.
- Optimized code: no dead code, no duplicated logic, stdlib preferred.
- Every public function has a docstring.
- OSS-only: verify license before integrating; GPL never in core `src/`.
- Every feature belongs in the sidebar. No arbitrary limits.
- Never promise zero bans. Never touch user profiles/sessions without approval.
