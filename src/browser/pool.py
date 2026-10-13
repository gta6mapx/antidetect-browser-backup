"""Concurrency-limited launch pool for anti-detect browser profiles.

Launching hundreds of browser profiles at once would thrash the machine, so
:class:`LaunchPool` caps how many profiles may be live at the same time.
Waiters queue in strict FIFO (first-come, first-served) order via a ticket
system: a ``threading.Condition`` hands the turn to the head of the queue,
and a ``threading.Semaphore`` enforces the concurrency cap. No busy-waiting
anywhere -- waiters block on the condition and the semaphore.

Usage::

    pool = LaunchPool(max_concurrent=5, headless=True)
    with pool.submit(persona) as profile:
        profile.page.goto("https://example.com")
        ...

The pool launches through :func:`src.browser.launcher.launch_profile`, which
is imported lazily inside the launch path so importing this module stays
light (no camoufox import at import time).
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import collections
import contextlib
import threading
from typing import Any, ContextManager, Dict


class LaunchPool:
    """FIFO, concurrency-limited pool of launched browser profiles.

    Thread-safe: all shared state is guarded by a single lock.
    """

    def __init__(self, max_concurrent: int = 5, headless: bool = False):
        """Create a launch pool.

        Args:
            max_concurrent: Maximum number of profiles launched (holding a
                slot) at the same time. Must be >= 1.
            headless: Passed through to
                :func:`src.browser.launcher.launch_profile` for every
                launch.

        Raises:
            ValueError: If ``max_concurrent`` is less than 1.
        """
        if max_concurrent < 1:
            raise ValueError(
                "max_concurrent must be >= 1, got %r" % (max_concurrent,)
            )
        self._max_concurrent = max_concurrent
        self._headless = headless
        self._semaphore = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._turn = threading.Condition(self._lock)
        self._tickets = collections.deque()  # FIFO queue of waiting tickets
        self._next_ticket = 0
        self._running = 0  # profiles currently holding a slot

    def submit(self, persona: Dict[str, Any]) -> ContextManager[Any]:
        """Queue a persona for launch and return a context manager for it.

        Nothing blocks here: the ticket is taken when the returned context
        manager is entered, so "submission order" is the order the returned
        context managers are entered (identical for the normal
        ``with pool.submit(persona):`` idiom).

        Entering the context manager waits for this submission's FIFO turn,
        takes a concurrency slot, launches the profile via
        :func:`src.browser.launcher.launch_profile`, and yields the
        ``LaunchedProfile``. On exit the profile is always closed (close
        errors are swallowed), the slot is released, and the next waiter is
        woken. If the launch itself raises (e.g. the browser binary is
        absent), the error propagates -- it is never swallowed -- but the
        slot is still released and the next waiter still woken.

        Args:
            persona: Persona dict accepted by
                :func:`src.browser.launcher.launch_profile`.

        Returns:
            A context manager yielding the launched profile.
        """
        return self._guarded_launch(persona)

    def pool_status(self) -> Dict[str, int]:
        """Return current pool load.

        Returns:
            ``{'running': int, 'queued': int}`` where ``running`` is the
            number of profiles currently holding a concurrency slot and
            ``queued`` is the number of submissions waiting for their turn.
        """
        with self._lock:
            return {"running": self._running, "queued": len(self._tickets)}

    def shutdown(self) -> None:
        """Shut the pool down.

        No-op registry for API completeness in Phase 2: the pool holds no
        background threads or persistent resources of its own, so there is
        nothing to tear down. In-flight launches and already-launched
        profiles are unaffected; callers close those via their own context
        managers as usual.
        """
        return None

    # -- internal machinery ------------------------------------------------

    def _take_ticket(self) -> int:
        """Append a new FIFO ticket and return it. Caller must hold no lock."""
        with self._lock:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._tickets.append(ticket)
            self._turn.notify_all()
            return ticket

    def _wait_for_turn(self, ticket: int) -> None:
        """Block until ``ticket`` is at the head of the FIFO queue."""
        with self._turn:
            self._turn.wait_for(
                lambda: bool(self._tickets) and self._tickets[0] == ticket
            )

    def _drop_ticket(self, ticket: int) -> None:
        """Remove ``ticket`` from the queue if still present; wake others."""
        with self._lock:
            try:
                self._tickets.remove(ticket)
            except ValueError:
                pass
            else:
                self._turn.notify_all()

    @contextlib.contextmanager
    def _guarded_launch(self, persona: Dict[str, Any]):
        """Implement the submit() contract; see :meth:`submit`."""
        ticket = self._take_ticket()
        try:
            # 1. Wait for our FIFO turn (strict submission order).
            self._wait_for_turn(ticket)
            # 2. Take a concurrency slot; only the queue head reaches here.
            self._semaphore.acquire()
            try:
                # 3. Leave the queue, mark ourselves running, wake the next
                #    waiter so it can start blocking on the semaphore.
                with self._lock:
                    if self._tickets and self._tickets[0] == ticket:
                        self._tickets.popleft()
                    else:  # defensive: ticket moved by an interrupted waiter
                        try:
                            self._tickets.remove(ticket)
                        except ValueError:
                            pass
                    self._running += 1
                    self._turn.notify_all()
                # 4. Launch. Lazy import keeps this module's import light.
                #    Launch errors propagate (never swallowed); the finally
                #    below still releases the slot.
                from src.browser.launcher import launch_profile

                launched = launch_profile(persona, headless=self._headless)
                try:
                    yield launched
                finally:
                    # Always close on exit; swallow close errors so a
                    # failing close cannot mask the with-body's exception.
                    try:
                        launched.close()
                    except Exception:
                        pass
            finally:
                with self._lock:
                    self._running -= 1
                    self._turn.notify_all()
                self._semaphore.release()
        finally:
            # Ticket still queued only if we never reached step 3 (e.g. the
            # waiter was interrupted); remove it so the queue cannot stall.
            self._drop_ticket(ticket)
