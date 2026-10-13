# Phase 4 — Synchronizer: build notes

**Winner (vendored):** `wwewtech/nazak-browser-studio` @ `de06b50c6d447e16c6e6ef2fb964c0b5f9177bbc`, **MIT**.
`fury-antidetect-browser` is AGPL-3.0 → design ideas only (no code copied).
`persona-studio`'s "synchronizer" is bulk config patching, not live mirroring → rejected.
Full evaluation: `/tmp/EVALUATION.md` (not in repo).

## What was built

- `vendor/nazak-sync/` — verbatim vendored `nazak/core/synchronizer.py` + MIT LICENSE +
  ATTRIBUTION.md (reference only; our code does not import it).
- `src/sync/` — clean-room `SyncManager`:
  - `create(master_name, follower_names, headless=True, typing_enabled=True) -> session_id`
  - `stop(session_id)`, `status(session_id=None)`, `set_typing(session_id, enabled)`
  - `navigate(session_id, url)` — explicit mirrored navigation (programmatic
    `page.goto` dispatches no DOM events, so capture can never see it)
  - `drive_master(session_id, fn)` — run a callable on the session thread
    (Playwright objects are thread-affine)
  - Anti-farm measures: per-window motor profile (1–3px click offset, 0.88–1.12x
    wheel scale), 20–80ms per-action delays (10–70ms keys), ordered per-follower
    FIFO queues, 0.8s echo-guard mute after each dispatch, element re-location
    (id-first selector path, coordinate fallback), typing toggle, staggered
    round-robin replay. Single-follower failures degrade, never raise.
  - Typing replays as **real per-key events** (fixes nazak's value-append weakness).
- `src/browser/launcher.py` — added `launch_profile_on(playwright, persona, ...)`
  + `SharedBrowser`: several browsers share one entered `sync_playwright()`.
- GUI: `POST /api/sync/start|stop|typing`, `GET /api/sync/status`; dashboard
  "Sync" button + modal (master select, follower checkboxes, headless/typing,
  live status).

## Coordinator fixes during live testing (real bugs found)

1. **Event-loop collision**: entering a 2nd `Camoufox()`/`sync_playwright()` on one
   thread while the first browser is open raises "Sync API inside the asyncio
   loop" (each enter leaves its loop running). Fixed via shared Playwright
   (`launch_profile_on`).
2. **Thread-affinity**: Playwright sync objects must be driven on their creating
   thread (`greenlet.error` otherwise). Added `drive_master()` / `navigate()`.
3. **`_stop` name collision**: worker named a `threading.Event` `_stop`,
   shadowing `threading.Thread._stop()` → `TypeError` on `join()`. Renamed to
   `_stop_event`.
4. **`bounding_box()` 30s default wait** in element re-location could stall the
   pump → capped at 1500ms (lookup, not a wait).

## Live test (this server, headless Camoufox, 3 profiles)

- Navigation mirrored: 2/2 followers
- Click mirrored (title change): 2/2
- Typing mirrored (input value): 2/2
- `mirrored_total: 24`, `dropped: 0`, per-follower 12/12
- Regression: `import src.cli` OK, validator 22/22, launcher both paths OK,
  sync endpoints present.

## Limitations

- No-leader mode (fury's model) not implemented — master-follower only.
- External HTTPS sites untestable here (server MITM proxy); data: URLs used.
- `drive_master` callables must be quick; a hanging callable stalls the pump
  until its timeout.
