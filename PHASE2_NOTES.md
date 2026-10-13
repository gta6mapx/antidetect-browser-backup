# Phase 2 — Agency Scale (500–1000 client profiles)

Built by 1 coordinator + 3 workers, Oct 7 2026. Committed locally; parent pushes.

## What was built

### 1. Bulk ops — `src/bulk/` (Worker A)
- `src/bulk/importer.py`: `bulk_import(csv_path, client_tag=None, template=None, db_path=None)`
  → `{'created','skipped','errors'}`. CSV columns: `name` (required), `os`,
  `proxy_name`, `client_tag`, `template` + any extra columns as persona overrides
  (int-coerced for hardware_concurrency/device_memory/touch_points/color_depth).
  Duplicate name → `skipped`, never raises; row errors recorded, never raised.
  Precedence: row value > template field > function arg.
- `bulk_export(csv_path, client_tag=None, db_path=None) -> int` rows written.
- Templates: `save_template / list_templates / get_template` → JSON in
  `~/.antidetect-browser/templates/<name>.json` (name restricted to `[A-Za-z0-9_-]`).
- `src/profiles/manager.py` (additive): `client_tag` + `template` columns via
  `PRAGMA table_info` + `ALTER TABLE` migration (existing DBs migrate on first
  `ProfileManager()` construction); `create()` accepts/persists both as columns
  (never duplicated inside `fingerprint_json`); new `bulk_create(items)` and
  `list_by_tag(tag)`. Fully backward compatible.

### 2. Sessions + security — `src/sessions/`, `src/security/` (Worker B)
- `src/security/crypto.py`: Fernet (`cryptography` package, added to
  requirements.txt and installed in repo `.venv`). `get_or_create_key()` —
  key file `~/.antidetect-browser/.master.key`, created with `0o600`.
  `encrypt_str`/`decrypt_str` (None passes through; raw token has NO marker —
  the caller adds `"enc:"` when persisting, documented). `is_encrypted()`
  checks the marker. Rotation is NOT supported in Phase 2 (documented: rotating
  orphans all encrypted data).
- `src/proxy/manager.py` (additive, signatures unchanged): `add()` encrypts
  passwords as `"enc:"+token` (no double-encryption); `get()`/`list()` decrypt
  on read and return plaintext; legacy plaintext rows (no prefix) still work.
- `src/sessions/backup.py`: `backup_profile(name)->zip path`
  (`~/.antidetect-browser/backups/<name>/<UTC-timestamp>.zip`, `-N` suffix on
  same-second collisions), `restore_profile(name, backup_path)` (refuses
  non-empty target with `FileExistsError` — never overwrites a live session;
  rejects paths outside the backups tree + zip-slip members), `list_backups`,
  `session_health(name)->{'ok','size_mb','detail'}` (ok = dir exists AND has
  Cookies/Default/*.sqlite/*.ldb marker; strictly read-only).

### 3. Uniqueness + launch pool — `src/fingerprints/uniqueness.py`, `src/browser/pool.py` (Worker C)
- `find_collisions(persona, existing)` → `[{'field','value','with'}]` over 6
  signals: user_agent, canvas_seed, webgl_renderer, viewport, fonts-signature,
  timezone. Pure, never raises on missing keys.
- `ensure_unique(make_persona_fn, existing, max_attempts=10)` → first
  collision-free persona (checks against existing + previous attempts);
  raises `RuntimeError` with colliding field details on exhaustion.
- `LaunchPool(max_concurrent=5, headless=False)`: `submit(persona)` →
  contextmanager; strict FIFO (ticket queue + Condition, no busy-wait);
  semaphore-guarded; `.close()` always called in `finally` (close errors
  swallowed); launch errors propagate but the slot is released and the next
  waiter notified; `pool_status()->{'running','queued'}`; `shutdown()` is a
  documented no-op. `launch_profile` imported lazily — importing pool does not
  pull in camoufox.

## Verification (coordinator smoke test, repo `.venv`) — 26/26 pass
- Bulk import 5 profiles from CSV → created=5, `list_by_tag` =5, duplicate
  re-import → all skipped, export → 5 rows, template roundtrip; all deleted after.
- 20 generated personas: `canvas_seed` never collides; `ensure_unique`
  raises `RuntimeError` on an impossible generator (contract).
- Backup/restore roundtrip on dummy profile dir: zip created, health ok,
  restore refused while non-empty, succeeded after removal, healthy again.
- Fernet roundtrip; proxy password stored as `enc:` ciphertext, read back plaintext.
- LaunchPool with stub launcher: 4 jobs through `max_concurrent=2`, all closed,
  FIFO order, launch-error propagation with slot release, idle status at end.

## Known limitations (honest)
- **Uniqueness signals vs browserforge**: across same-OS generated personas,
  `timezone`, `fonts-signature`, `webgl_renderer`, `viewport`, and often
  `user_agent` WILL collide (browserforge emits a small pool per OS; defaults
  share timezone). Only `canvas_seed` is guaranteed unique. `find_collisions`
  is therefore a *detection/reporting* tool — `ensure_unique` on same-OS
  browserforge personas will exhaust attempts and raise. True per-profile
  uniqueness at 500–1000 scale needs either varied OS mix or a custom
  high-entropy persona generator (future work).
- Geo-sync resolves the *direct* egress IP, not the proxy's (documented).
- Proxy creds of pre-Phase-2 rows stay plaintext until re-added.
- No real browser launch tested here (no binary); pool validated against a stub.
- No new CLI subcommands for bulk/geo-sync yet (library-level; suggested:
  `bulk import/export`, `proxy sync --profile --proxy`, `session backup`).

## Try it
```bash
.venv/bin/python -m src.cli profile create --name "client-acme-01"
# bulk:
.venv/bin/python -c "
from src.bulk.importer import bulk_import
print(bulk_import('clients.csv', client_tag='acme'))"
```
