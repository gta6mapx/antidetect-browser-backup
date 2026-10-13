"""Master-follower action synchronizer.

A sync session launches one **master** profile and N **follower** profiles
(via :func:`src.browser.launcher.launch_profile`, all on the session's own
thread so every Playwright object is created and used on one thread). A small
capture script installed in the master's pages reports clicks, keystrokes and
wheel ticks through a neutral ``expose_binding`` channel (``__nse_ev``); the
session's pump thread replays each gesture on every follower's page.

Anti-farm-signal measures (the whole point of this module):

* **Per-window motor profile** — every follower gets its own fixed click
  offset (1–3 px in a random direction) and wheel scale (0.88–1.12), so no
  two windows land or scroll identically.
* **Per-window random delays** — 20–80 ms before a mirrored click/scroll,
  10–70 ms before a mirrored keystroke (typists are faster than clickers).
* **Ordered per-follower queues** — each follower has its own FIFO queue
  drained in order, so "hello" can never arrive as "oelhl", and one slow
  follower never blocks the others.
* **Echo guard** — a follower is muted briefly after an action is
  dispatched to it, so its own reaction can never be re-captured and
  fanned back out.
* **Element re-location** — clicks are replayed by finding the element via
  the captured selector in the follower's own DOM and clicking its centre
  (+ offset), falling back to the master's coordinates when the element is
  absent.
* **Real per-key replay** — keystrokes are replayed as genuine key events
  (``keyboard.type`` / ``keyboard.press``), never by appending to an
  input's ``value``. A typing toggle lets an operator mirror navigation
  while entering different credentials per window.
* **Staggered fan-out** — followers are driven sequentially with a small
  stagger, so two followers never act at identical timestamps.

A single follower failure never raises: it is logged, the follower is marked
degraded, and the pump continues with the rest.

Design lineage: the architecture follows the vendored nazak synchronizer
(``vendor/nazak-sync/``, MIT — reference copy, not imported), adapted from
its Chromium-CDP attachments to our launcher's Playwright page objects; the
anti-detection measures above are ideas ported from the fury-antidetect
``mirror.rs`` design spec (AGPL-3.0 — design only, no code copied). See
``src/sync/README.md``.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import json
import logging
import math
import queue
import random
import threading
import time
import uuid

logger = logging.getLogger(__name__)

#: Neutral name of the page->python binding channel (matches the nazak
#: convention; the name itself reveals nothing about the product).
BINDING_NAME = "__nse_ev"

#: Max pending master gestures per follower queue (bounded: a dead/slow
#: follower must not grow memory without limit).
MAX_QUEUE_EVENTS = 2000

#: Echo-guard mute window after dispatching an action to a follower.
ECHO_MUTE_SEC = 0.8

#: Consecutive dispatch failures before a follower is marked degraded.
DEGRADED_AFTER_FAILURES = 5

#: Keys that may be replayed via keyboard.press (printable single chars go
#: through keyboard.type instead).
_SPECIAL_KEYS = {
    "Enter", "Tab", "Backspace", "Delete", "Escape",
    "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
    "Home", "End", "PageUp", "PageDown",
    "Shift", "Control", "Alt", "Meta", " ",
}

#: Capture script installed in the master's pages. Reports only trusted
#: (real-user) gestures; the idem-potence flag is non-enumerable so casual
#: page scripts cannot spot it by enumeration. The selector path uses ids
#: and tag positions only — class names churn between sessions on most
#: sites, structure less so.
CAPTURE_JS = """(() => {
  const FLAG = '__adsb_sync_on';
  if (window[FLAG]) return;
  Object.defineProperty(window, FLAG, {value: true, enumerable: false, configurable: false});
  const BIND = '__nse_ev';
  const send = (o) => {
    try { if (typeof window[BIND] === 'function') window[BIND](JSON.stringify(o)); } catch (e) {}
  };
  const selPath = (el) => {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 8) {
      if (el.id && /^[A-Za-z][\\w-]*$/.test(el.id)) { parts.unshift('#' + el.id); break; }
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) { if (s.tagName === el.tagName) i++; }
      parts.unshift(el.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
      el = el.parentElement;
    }
    return parts.join('>');
  };
  addEventListener('click', (e) => {
    if (!e.isTrusted) return;
    send({t: 'click', x: e.clientX, y: e.clientY,
          button: e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left'),
          sel: selPath(e.target)});
  }, true);
  addEventListener('keydown', (e) => {
    if (!e.isTrusted) return;
    const text = (e.key && e.key.length === 1 && !e.ctrlKey && !e.metaKey) ? e.key : '';
    send({t: 'key', key: e.key || '', text: text});
  }, true);
  let lastWheel = 0, acc = {dx: 0, dy: 0, x: 0, y: 0};
  addEventListener('wheel', (e) => {
    if (!e.isTrusted) return;
    acc.dx += e.deltaX; acc.dy += e.deltaY; acc.x = e.clientX; acc.y = e.clientY;
    const now = Date.now();
    if (now - lastWheel > 120) {
      lastWheel = now;
      send({t: 'wheel', x: acc.x, y: acc.y, dx: acc.dx, dy: acc.dy});
      acc.dx = 0; acc.dy = 0;
    }
  }, {capture: true, passive: true});
})();"""


def _valid_event(event):
    """Return True if ``event`` is a well-formed gesture dict we can replay."""
    if not isinstance(event, dict):
        return False
    etype = event.get("t")
    if etype == "click":
        return isinstance(event.get("x"), (int, float)) and isinstance(event.get("y"), (int, float))
    if etype == "key":
        return isinstance(event.get("key"), str) and bool(event.get("key"))
    if etype == "wheel":
        return isinstance(event.get("dx"), (int, float)) and isinstance(event.get("dy"), (int, float))
    return False


class _Follower:
    """Runtime state for one follower inside a session."""

    def __init__(self, name, rng):
        self.name = name
        self.launched = None
        self.state = "launching"  # launching -> ready | failed ; ready -> degraded
        self.queue = queue.Queue(maxsize=MAX_QUEUE_EVENTS)
        self.mirrored = 0
        self.dropped = 0
        self.failures = 0
        # Per-window motor profile (drawn once per session).
        angle = rng.uniform(0, 2 * math.pi)
        radius = rng.uniform(1.0, 3.0)
        self.offset = (radius * math.cos(angle), radius * math.sin(angle))
        self.wheel_scale = rng.uniform(0.88, 1.12)

    def enqueue(self, event):
        """Append a gesture to this follower's ordered queue (never raises)."""
        try:
            self.queue.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped % 100 == 1:
                logger.warning("sync: follower %s queue full; dropped %d events so far",
                               self.name, self.dropped)
            return False


class _SyncSession(threading.Thread):
    """One sync session: owns its thread, browsers, queues and pump loop.

    Everything Playwright touches (launch, capture install, replay) happens
    on this thread: Playwright objects are thread-affine, so creating and
    driving them on one thread avoids cross-thread greenlet errors.
    """

    def __init__(self, manager, session_id, master_name, follower_names,
                 headless=True, typing_enabled=True):
        super().__init__(name="SyncSession-%s" % session_id[:8], daemon=True)
        self.manager = manager
        self.session_id = session_id
        self.master_name = master_name
        self.follower_names = list(follower_names)
        self.headless = headless
        self.typing_enabled = typing_enabled
        self.started_at = time.time()
        self.rng = random.Random()
        self.inbound = queue.Queue(maxsize=MAX_QUEUE_EVENTS)
        self.followers = {n: _Follower(n, self.rng) for n in self.follower_names}
        self.master_launched = None
        self.master_ready = False
        self.mirrored_total = 0
        self.dropped_total = 0
        self.last_error = None
        self._stop_event = threading.Event()
        # Echo guard: profile name -> monotonic() timestamp until which its
        # reported gestures are dropped.
        self._muted_until = {}
        self._lock = threading.Lock()
        # Drive commands: (callable, done_event, result_box). Lets other
        # threads run callables against the master's page ON this thread
        # (Playwright objects are thread-affine). Serviced each pump turn.
        self._drive = queue.Queue()
        # One shared Playwright for every browser in this session. Entering a
        # second sync_playwright()/Camoufox() on one thread while the first is
        # still open raises "Sync API inside the asyncio loop", so all of the
        # session's browsers share a single entered instance (see
        # src.browser.launcher.launch_profile_on).
        self._pw_manager = None

    # ------------------------------------------------------------ lifecycle
    def run(self):
        """Launch browsers, install capture, pump until stopped."""
        try:
            self._launch_all()
            if self.master_ready:
                self._install_capture()
            self._pump_loop()
        except Exception as exc:  # defensive: a dead pump must be visible
            logger.warning("sync session %s pump stopped: %s", self.session_id, exc)
            self.last_error = str(exc)[:500]
        finally:
            self._close_all()

    def request_stop(self, timeout=10.0):
        """Signal the session thread to stop and wait for it (never raises)."""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)

    # -------------------------------------------------------------- launching
    def _launch_all(self):
        """Launch master + followers on this thread (best effort each).

        All browsers share one entered ``sync_playwright()`` — see
        :func:`src.browser.launcher.launch_profile_on` for why.
        """
        from playwright.sync_api import sync_playwright
        from src.browser.launcher import launch_profile_on

        try:
            self._pw_manager = sync_playwright()
            pw = self._pw_manager.__enter__()
        except Exception as exc:
            self.last_error = "playwright start failed: %s" % exc
            logger.warning("sync session %s: %s", self.session_id, self.last_error)
            return
        try:
            persona = self.manager.persona_getter(self.master_name)
        except Exception as exc:
            self.last_error = "master persona not found: %s" % exc
            logger.warning("sync session %s: %s", self.session_id, self.last_error)
            return
        try:
            self.master_launched = launch_profile_on(pw, persona, headless=self.headless)
            self.master_ready = True
        except Exception as exc:
            self.last_error = "master launch failed: %s" % exc
            logger.warning("sync session %s: %s", self.session_id, self.last_error)
        for name, follower in self.followers.items():
            if self._stop_event.is_set():
                follower.state = "failed"
                continue
            try:
                persona = self.manager.persona_getter(name)
            except Exception as exc:
                follower.state = "failed"
                logger.warning("sync session %s: follower %s persona not found: %s",
                               self.session_id, name, exc)
                continue
            try:
                follower.launched = launch_profile_on(pw, persona, headless=self.headless)
                follower.state = "ready"
            except Exception as exc:
                follower.state = "failed"
                logger.warning("sync session %s: follower %s launch failed: %s",
                               self.session_id, name, exc)

    def _install_capture(self):
        """Install the gesture-capture binding + script in the master context."""
        launched = self.master_launched
        try:
            context = launched.browser  # a BrowserContext for persistent launches
        except Exception:
            context = None
        if context is None:
            logger.warning("sync session %s: master has no browser context; capture unavailable",
                           self.session_id)
            return

        def _on_binding(source, payload):
            # Runs on Playwright's internal thread: touch nothing but the
            # thread-safe queue here.
            try:
                event = json.loads(payload) if isinstance(payload, str) else None
            except (ValueError, TypeError):
                return
            if not _valid_event(event):
                return
            # Echo guard: never re-ingest gestures from a muted profile.
            if time.monotonic() < self._muted_until.get(self.master_name, 0.0):
                return
            try:
                self.inbound.put_nowait(event)
            except queue.Full:
                self.dropped_total += 1

        try:
            context.expose_binding(BINDING_NAME, _on_binding)
            context.add_init_script(CAPTURE_JS)
            # Active immediately on the current page, not just future documents.
            launched.page.evaluate(CAPTURE_JS)
        except Exception as exc:
            self.last_error = "capture install failed: %s" % exc
            logger.warning("sync session %s: %s", self.session_id, self.last_error)

    # ------------------------------------------------------------ pump loop
    def _pump_loop(self):
        """Fan master gestures out to per-follower queues, then replay them
        round-robin — one action per follower per turn, staggered, so two
        followers never act at identical timestamps."""
        while not self._stop_event.is_set():
            self._drain_drive()
            self._drain_inbound()
            acted = False
            for name in self.follower_names:
                follower = self.followers[name]
                if follower.state not in ("ready", "degraded"):
                    continue
                try:
                    action = follower.queue.get_nowait()
                except queue.Empty:
                    continue
                self._dispatch(name, follower, action)
                acted = True
                # Stagger: followers never fire at identical timestamps.
                time.sleep(self.rng.uniform(0.005, 0.025))
            if not acted:
                time.sleep(0.02)

    def _drain_drive(self):
        """Run queued drive commands on this thread (Playwright is
        thread-affine). Each item is (fn, done_event, result_box)."""
        while True:
            try:
                fn, done, box = self._drive.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = fn(self)
            except Exception as exc:  # noqa: BLE001 - reported to caller
                box["error"] = exc
            finally:
                done.set()

    def drive(self, fn, timeout=30.0):
        """Run ``fn(session)`` on the session thread and return its result.

        Lets other threads drive the master's page (Playwright objects must
        be touched on the thread that created them). Raises the callable's
        exception, or ``TimeoutError`` if the pump doesn't pick it up.
        """
        done = threading.Event()
        box = {}
        self._drive.put((fn, done, box))
        if not done.wait(timeout):
            raise TimeoutError("drive command not serviced within %.1fs" % timeout)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _drain_inbound(self):
        """Move master gestures into each ready follower's ordered queue."""
        while True:
            try:
                event = self.inbound.get_nowait()
            except queue.Empty:
                return
            if event.get("t") == "key" and not self.typing_enabled:
                continue  # typing toggle: mirror clicks/scrolls only
            for follower in self.followers.values():
                if follower.state not in ("ready", "degraded"):
                    continue
                if not follower.enqueue(dict(event)):
                    self.dropped_total += 1

    # -------------------------------------------------------------- dispatch
    def _dispatch(self, name, follower, event):
        """Replay one gesture on one follower (never raises)."""
        page = follower.launched.page if follower.launched else None
        if page is None:
            return
        # Per-window delay before the mirrored action (keys are quicker).
        if event.get("t") == "key":
            time.sleep(self.rng.uniform(0.010, 0.070))
        else:
            time.sleep(self.rng.uniform(0.020, 0.080))
        try:
            ok = self._replay(page, follower, event)
        except Exception as exc:
            ok = False
            logger.warning("sync: replay %s failed on %s: %s",
                           event.get("t"), name, exc)
        # Echo guard: mute this follower briefly so its own reaction to our
        # injected action can never be re-captured and fanned back out.
        self._muted_until[name] = time.monotonic() + ECHO_MUTE_SEC
        if ok:
            follower.mirrored += 1
            follower.failures = 0
            self.mirrored_total += 1
        else:
            follower.failures += 1
            if (follower.failures >= DEGRADED_AFTER_FAILURES
                    and follower.state == "ready"):
                follower.state = "degraded"
                logger.warning("sync: follower %s marked degraded after %d failures",
                               name, follower.failures)

    def _replay(self, page, follower, event):
        """Replay one gesture; returns True on success (never raises)."""
        etype = event.get("t")
        try:
            if etype == "click":
                return self._replay_click(page, follower, event)
            if etype == "key":
                return self._replay_key(page, event)
            if etype == "wheel":
                return self._replay_wheel(page, follower, event)
        except Exception as exc:
            logger.warning("sync: replay %s failed: %s", etype, exc)
        return False

    def _locate_center(self, page, selector):
        """Centre of the element ``selector`` names in this page's own DOM.

        Returns None when the element cannot be found (caller falls back
        to coordinates). Uses a short timeout — this is a lookup, not a
        wait; the pump must never block on an absent element.
        """
        if not selector:
            return None
        try:
            box = page.locator(selector).first.bounding_box(timeout=1500)
        except Exception:
            return None
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return None
        return (box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)

    def _replay_click(self, page, follower, event):
        # Element re-location first: click the element's own centre in the
        # follower's layout; fall back to the master's coordinates.
        center = self._locate_center(page, event.get("sel") or "")
        if center is None:
            center = (float(event.get("x", 0)), float(event.get("y", 0)))
        dx, dy = follower.offset
        px, py = center[0] + dx, center[1] + dy
        button = event.get("button") or "left"
        if button not in ("left", "right", "middle"):
            button = "left"
        page.mouse.move(px, py)
        page.mouse.down(button=button)
        time.sleep(self.rng.uniform(0.04, 0.11))
        page.mouse.up(button=button)
        return True

    def _replay_key(self, page, event):
        text = event.get("text") or ""
        key = event.get("key") or ""
        if text:
            # Real key events per character (keydown/keypress/keyup) —
            # never value assignment.
            page.keyboard.type(text)
            return True
        if key in _SPECIAL_KEYS:
            page.keyboard.press("Space" if key == " " else key)
            return True
        return False  # unknown non-printable key: skip rather than mistype

    def _replay_wheel(self, page, follower, event):
        dx = max(-2000.0, min(2000.0, float(event.get("dx", 0))))
        dy = max(-2000.0, min(2000.0, float(event.get("dy", 0))))
        if dx == 0 and dy == 0:
            return False
        # Move the cursor near the gesture point first, like a real wheel user.
        try:
            page.mouse.move(float(event.get("x", 0)) + follower.offset[0],
                            float(event.get("y", 0)) + follower.offset[1])
        except Exception:
            pass
        page.mouse.wheel(dx * follower.wheel_scale, dy * follower.wheel_scale)
        return True

    # --------------------------------------------------------------- teardown
    def _close_all(self):
        """Close every browser this session launched (never raises, never
        deletes profile data — closing only tears down the browser), then
        stop the shared Playwright."""
        launched = [self.master_launched] + [f.launched for f in self.followers.values()]
        for item in launched:
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        self.master_launched = None
        for follower in self.followers.values():
            follower.launched = None
        if self._pw_manager is not None:
            try:
                self._pw_manager.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_manager = None

    # ----------------------------------------------------------------- status
    def status_dict(self):
        """Snapshot of this session's state (safe to call from any thread)."""
        with self._lock:
            followers = {
                name: {
                    "state": f.state,
                    "queue_depth": f.queue.qsize(),
                    "mirrored": f.mirrored,
                    "dropped": f.dropped,
                }
                for name, f in self.followers.items()
            }
            return {
                "session_id": self.session_id,
                "master": self.master_name,
                "followers": followers,
                "master_ready": self.master_ready,
                "headless": self.headless,
                "typing_enabled": self.typing_enabled,
                "queue_depth": self.inbound.qsize(),
                "mirrored_total": self.mirrored_total,
                "dropped_total": self.dropped_total,
                "alive": self.is_alive() and not self._stop_event.is_set(),
                "started_at": self.started_at,
                "last_error": self.last_error,
            }


class SyncManager:
    """Owns sync sessions: create/stop/status plus a typing toggle.

    Args:
        persona_getter: Callable ``(name) -> persona dict`` used to resolve
            profile names. Defaults to a fresh
            :class:`src.profiles.manager.ProfileManager` ``get``.
    """

    def __init__(self, persona_getter=None):
        if persona_getter is None:
            from src.profiles.manager import ProfileManager
            persona_getter = ProfileManager().get
        self.persona_getter = persona_getter
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, master_name, follower_names, headless=True,
               typing_enabled=True):
        """Start a sync session mirroring ``master_name`` onto ``follower_names``.

        Browsers are launched on the session's own thread (headless by
        default; pass ``headless=False`` for visible windows). Capture is
        installed on the master; the pump thread replays gestures on each
        follower with its own motor profile and delays.

        Args:
            master_name: Profile whose actions drive the session.
            follower_names: Profiles that mirror the master (must not
                include the master).
            headless: Launch browsers headless (default True, for servers).
            typing_enabled: Mirror keystrokes too (default True).

        Returns:
            The new ``session_id`` (str).

        Raises:
            ValueError: On unknown profile names, an empty follower list,
                the master listed as a follower, or a profile already in an
                active session.
        """
        followers = list(dict.fromkeys(follower_names or []))
        if not master_name:
            raise ValueError("master_name is required")
        if not followers:
            raise ValueError("at least one follower is required")
        if master_name in followers:
            raise ValueError("master %r cannot also be a follower" % master_name)
        # Resolve personas up front so bad names fail fast, before launch.
        # Synchronizer is Camoufox-only: refuse non-Camoufox profiles.
        try:
            master_persona = self.persona_getter(master_name)
        except Exception as exc:
            raise ValueError("unknown master profile %r: %s" % (master_name, exc))
        if (master_persona.get("engine") or "camoufox") != "camoufox":
            raise ValueError(
                "sync is Camoufox-only; master %r uses engine %r"
                % (master_name, master_persona.get("engine")))
        for name in followers:
            try:
                persona = self.persona_getter(name)
            except Exception as exc:
                raise ValueError("unknown follower profile %r: %s" % (name, exc))
            if (persona.get("engine") or "camoufox") != "camoufox":
                raise ValueError(
                    "sync is Camoufox-only; follower %r uses engine %r"
                    % (name, persona.get("engine")))
        with self._lock:
            busy = {s.master_name for s in self._sessions.values()}
            busy.update(n for s in self._sessions.values() for n in s.follower_names)
            clash = [n for n in [master_name, *followers] if n in busy]
            if clash:
                raise ValueError("profiles already in a sync session: %s" % ", ".join(clash))
            session_id = uuid.uuid4().hex[:12]
            session = _SyncSession(self, session_id, master_name, followers,
                                   headless=headless, typing_enabled=typing_enabled)
            self._sessions[session_id] = session
        session.start()
        logger.info("sync session %s started: master=%s followers=%s",
                    session_id, master_name, followers)
        return session_id

    def stop(self, session_id):
        """Stop a session: halts the pump and closes every browser the
        session launched. Profile data dirs are never touched.

        Returns:
            The session's final status dict.

        Raises:
            KeyError: If no session has ``session_id``.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError("no sync session %r" % session_id)
        session.request_stop()
        final = session.status_dict()
        final["alive"] = False
        logger.info("sync session %s stopped (mirrored=%d)",
                    session_id, final["mirrored_total"])
        return final

    def set_typing(self, session_id, enabled):
        """Enable/disable keystroke mirroring for a running session.

        Useful when followers must enter different credentials: leave
        clicks/scrolls mirroring on, turn typing off.

        Raises:
            KeyError: If no session has ``session_id``.
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("no sync session %r" % session_id)
        session.typing_enabled = bool(enabled)
        return session.status_dict()

    def navigate(self, session_id, url, timeout=60.0):
        """Navigate master AND followers to ``url`` (mirrored navigation).

        Programmatic navigation (``page.goto``) dispatches no DOM events, so
        the gesture-capture binding can never see it — navigation is
        therefore an explicit mirrored action, not a captured one (same as
        nazak's ``mirror_navigation``). Followers navigate with a small
        stagger so they don't hit the server at the identical millisecond.

        Raises:
            KeyError: If ``session_id`` names no active session.
        """
        def _go(session):
            session.master_launched.page.goto(url)
            for name in session.follower_names:
                follower = session.followers[name]
                if follower.launched is not None:
                    time.sleep(session.rng.uniform(0.15, 0.6))
                    follower.launched.page.goto(url)
            return url
        return self.drive_master(session_id, _go, timeout=timeout)

    def drive_master(self, session_id, fn, timeout=30.0):
        """Run ``fn(session)`` on the session's thread and return its result.

        The session owns its Playwright objects (thread-affine); this is the
        supported way to drive the master's page programmatically, e.g.
        ``mgr.drive_master(sid, lambda s: s.master_launched.page.goto(url))``.

        Raises:
            KeyError: If ``session_id`` names no active session.
            TimeoutError: If the pump doesn't service the command in time.
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("no sync session %r" % session_id)
        return session.drive(fn, timeout=timeout)

    def status(self, session_id=None):
        """Return status for one session, or all sessions.

        Args:
            session_id: Optional; when omitted, returns
                ``{"sessions": [...]}``.

        Raises:
            KeyError: If ``session_id`` names no active session.
        """
        with self._lock:
            if session_id is not None:
                session = self._sessions.get(session_id)
                if session is None:
                    raise KeyError("no sync session %r" % session_id)
                return session.status_dict()
            return {"sessions": [s.status_dict() for s in self._sessions.values()]}
