# Vendored component: nazak synchronizer (reference copy)

## What this is

A **verbatim, unmodified copy** of `nazak/core/synchronizer.py` from
**nazak-browser-studio** (MIT license — vendoring permitted), kept here as a
reference for the design that `src/sync/` adapts. It is **not imported or
executed** by our code.

- Upstream repo: https://github.com/wwewtech/nazak-browser-studio
- Commit vendored: `de06b50c6d447e16c6e6ef2fb964c0b5f9177bbc`
  (`fix(api): Origin must be same-origin, not from a fixed port list`)
- File taken: `nazak/core/synchronizer.py` (597 lines) — the action
  synchronizer (master-follower model, JS event capture via neutral
  `Runtime.addBinding` named `__nse_ev`, bounded thread-safe queue, pump
  thread replaying over Playwright `connect_over_cdp`, per-event coordinate
  jitter + 20–80ms delays).
- License: MIT — full text in `LICENSE` in this directory
  (Copyright (c) 2026 Nazak Browser Studio Contributors).

Also consulted (not copied): `nazak/core/cdp_injector.py` for the
`Runtime.addBinding` registration pattern (`SYNC_BINDING_NAME =
"__nse_ev"`, binding accepted only from the main world of top-level pages).

## How our code relates to it

`src/sync/manager.py` is a **clean-room rewrite**, not a port of this file:

- Our launcher (`src/browser/launcher.py`) drives vendored Camoufox (Firefox)
  through Playwright's sync API — there is no Chromium CDP endpoint, so
  nazak's `connect_over_cdp` worker attachments cannot work here. Instead of
  the `get_cdp_info(profile_id) -> endpoint` bridge, our adapter drives the
  launcher's Playwright `page` objects directly (`page.mouse` /
  `page.keyboard`), all on the sync session's own thread.
- Capture uses Playwright-native `context.expose_binding("__nse_ev", …)` +
  `add_init_script(...)` instead of raw CDP `Runtime.addBinding`.
- Anti-detection design ideas (per-window motor profiles, ordered
  per-follower queues, echo guard, element re-location, typing toggle) were
  taken from the fury-antidetect-browser `mirror.rs` **design spec**
  (AGPL-3.0 — ideas only, no code copied).
- Known nazak weakness fixed here: nazak replays typing by appending to the
  input's `value` (bypasses real key events — detectable). We replay real
  per-key events (`keyboard.type` for printable chars, `keyboard.press`
  for special keys).

## Updating

To refresh from upstream: copy `nazak/core/synchronizer.py` and `LICENSE`
from the upstream repo at the desired commit, then update the commit hash
above.
