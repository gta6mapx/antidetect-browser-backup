# Action Synchronizer (`src/sync/`)

Mirror everything done in one profile (**master**) onto any number of
**follower** profiles: clicks, typing and scrolling are captured in the
master's pages and replayed in each follower's own page, each in its own
way — so a batch of warmed accounts never moves in lockstep.

## Provenance

| Piece | License | What we took |
|---|---|---|
| `nazak-browser-studio` (`vendor/nazak-sync/`) | MIT | Verbatim reference copy of `nazak/core/synchronizer.py` (+ `LICENSE` + `ATTRIBUTION.md`). The capture-binding idea (neutral `__nse_ev` channel, idempotent non-enumerable install flag) informed our capture script. The file is **not imported** by our code. |
| `fury-antidetect-browser` `agent/src/mirror.rs` | AGPL-3.0 | **Design ideas only — no code copied.** Ported concepts: per-window motor profile, ordered per-follower queues, echo guard, element re-location with coordinate fallback, typing toggle, per-window wheel scale. |

Everything else in this package (`manager.py`, the capture script, the
replay logic) was written clean-room for this project's stack.

## Architecture

- **Session thread.** `SyncManager.create()` spawns one `_SyncSession`
  thread per session. The thread launches the master + all followers via
  `src.browser.launcher.launch_profile` and drives every page itself:
  Playwright objects are thread-affine, so creating and using them on one
  thread avoids cross-thread errors (the same constraint that made nazak
  open all its CDP sessions on one pump thread).
- **Capture.** On the master we install, per browser context:
  `context.expose_binding("__nse_ev", cb)` + `context.add_init_script(CAPTURE_JS)`
  (plus an immediate `evaluate` on the current page). The script reports only
  `isTrusted` gestures — real user input — as `{t:'click'|'key'|'wheel', …}`.
  Clicks carry an element selector path (id-first, `nth-of-type` steps; class
  names are avoided because they churn between sessions). The binding
  callback touches only a thread-safe queue.
- **Fan-out.** The pump drains the inbound queue into one ordered FIFO queue
  per follower. Keys are dropped when the session's typing toggle is off.
- **Replay (round-robin, staggered).** One action per follower per turn, with
  a small stagger between followers — two followers never act at identical
  timestamps. Before each action the follower's motor profile applies:
  20–80 ms delay (10–70 ms for keys), ±1–3 px click offset in a fixed random
  direction, 0.88–1.12× wheel scale.
- **Click replay:** re-locate the element by selector in the follower's DOM
  and click its centre (+ offset); fall back to the master's coordinates.
- **Key replay:** real key events — `keyboard.type(char)` for printable
  characters, `keyboard.press(key)` for special keys (Enter, Backspace,
  arrows, …). Unknown non-printables are skipped. Typing is **never**
  replayed by assigning to an input's `value` (that bypasses key events and
  is a known bot signal — this fixes a weakness in nazak's approach, which
  appended to `value`).
- **Echo guard:** after dispatching to a follower it is muted for 0.8 s, so
  its own reaction to the injected action can never be re-captured and
  fanned back out.
- **Failure isolation:** a dispatch exception is logged, the follower's
  failure counter grows, and after 5 consecutive failures it is marked
  `degraded` (it keeps being tried). A single bad follower never raises and
  never stops the pump.

## API (`src.sync.SyncManager`)

```python
from src.sync import SyncManager
mgr = SyncManager()  # resolves names via ProfileManager.get by default

sid = mgr.create("master-profile", ["follower-a", "follower-b"],
                 headless=True, typing_enabled=True)
mgr.status()            # {"sessions": [...]}
mgr.status(sid)         # one session: master, per-follower state/queue/mirrored/dropped,
                        # queue_depth, mirrored_total, typing_enabled, alive, last_error
mgr.set_typing(sid, False)  # mirror clicks/scrolls only (e.g. different logins per window)
mgr.stop(sid)           # stops the pump, closes all session browsers
```

`create()` raises `ValueError` for unknown names, an empty follower list,
the master listed as a follower, or profiles already in a session.
`stop()` / `set_typing()` / `status(sid)` raise `KeyError` for unknown ids.

`stop()` closes every browser the session launched (master included —
deviation from the original brief, which mentioned followers only; leaving
an ownerless master window behind would leak a browser the GUI cannot see).
Profile data directories are **never deleted** — closing tears down the
browser only.

## HTTP API (GUI backend)

- `POST /api/sync/start` — `{master, followers[], headless=true, typing=true}` → `201 {session_id, status}`. Refused (409) if any named profile is already running in the GUI (two instances on one profile dir would collide on the Firefox lock).
- `POST /api/sync/stop` — `{session_id}` → `{stopped: true, status}`.
- `GET /api/sync/status` / `GET /api/sync/status?session_id=…` — session(s).
- `POST /api/sync/typing` — `{session_id, enabled}` — typing toggle.

## Limitations

- Our launcher drives vendored Camoufox (Firefox) through Playwright's sync
  API, so there is **no CDP endpoint** — nazak's `connect_over_cdp` worker
  attachments were replaced by driving the launcher's `page` objects
  directly. Consequence: the session launches its *own* browser instances;
  it cannot attach to profiles already running in the GUI.
- Replay targets each follower's main page (`launched.page`); extra tabs
  opened mid-session are not mirrored into.
- Scroll position sync relies on wheel deltas, not absolute positions, so
  followers may drift a few pixels apart on long pages — intentional (exact
  pixel parity is a farm signal).
- Headless default is `True`; pass `headless=False` to watch the windows.
- Capture needs `expose_binding`, which adds a `window.__nse_ev` function to
  pages — neutral-named, but a page *can* observe it (same trade-off nazak
  and fury accept for sync mode).
