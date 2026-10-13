# Attribution — persona-import vendor tree

Files in this directory are vendored from **anhnnp/persona-studio**
(MIT License, Copyright (c) 2025 Ahsan), upstream commit
`d46e984cfbdcd7278063e793e6ab1ad95b8cdc78` (evaluated 2026-10-08).

| File | Source | Changes |
|------|--------|---------|
| `importers.py` | `engine/persona/importers.py` | **Verbatim copy** — profile-import helpers from AdsPower / GoLogin / Multilogin. Not imported by our code (reference + provenance record). The helpers we actually use were re-vendored into `src/migrate/importers.py` with our own adaptation. |
| `cookies.py` | `engine/persona/cookies.py` | **Verbatim copy** — cookie import/export (Cookie-Editor JSON, Playwright storage-state, Netscape `cookies.txt`). Not imported by our code (reference + provenance record). The format logic we actually use was re-vendored into `src/cookies/manager.py`. |
| `LICENSE` | repo root `LICENSE` | **Verbatim copy** — the MIT license text whose copyright + permission notice must accompany copies/substantial portions. |

## Adaptations made elsewhere (not in this directory)

1. `src/migrate/importers.py` — vendors the *pure helpers* (`_first`,
   `_norm_os`, `_parse_resolution`, `_parse_proxy`, `_unwrap`, `load_export`)
   verbatim with an MIT attribution header, replacing the persona-studio
   `Proxy` model with a plain dict. **One bug fix vs upstream**: `_unwrap`
   now also handles the real AdsPower API nesting `{"data": {"list": [...]}}`
   (upstream only checked one level). `profile_from_export` was *adapted*
   (not vendored) to build personas through our `ProfileManager.create()`.
2. `src/cookies/manager.py` — vendors `normalize`, `parse`,
   `parse_netscape`, `to_netscape`, `dumps`, `read_file` verbatim with an MIT
   attribution header. `read`/`write` were *adapted* (not vendored) into
   `export_cookies`/`import_cookies`, which launch our Camoufox profile
   session via `src.browser.launcher` instead of persona-studio's
   `drivers.session`.
3. `tests/test_migrate_p5.py` — adapted from upstream's
   `engine/tests/test_importers.py` + `engine/tests/test_cookies.py`
   (fixtures documenting GoLogin/AdsPower/Multilogin shapes kept; imports
   repointed at our modules).
