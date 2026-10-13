"""Proxy rotation pool.

Pattern inspired by proxy-scraper (MIT, Maximilian Feix):
https://github.com/maximilianfeix/proxy-scraper
Our own implementation — no code copied.

A RotationPool holds named proxies, health-checks them, and rotates
to the next working proxy on failure. Supports per-request, sticky,
and timed rotation modes.
"""

import threading
import time

from src.proxy.manager import ProxyManager


class RotationPool:
    """Thread-safe rotating proxy pool.

    Modes:
        per-request: new proxy every call to next()
        sticky: same proxy until it fails or release() is called
        timed: rotate every ``interval`` seconds
    """

    def __init__(self, mode="per-request", interval=60):
        if mode not in ("per-request", "sticky", "timed"):
            raise ValueError("mode must be per-request, sticky or timed")
        self.mode = mode
        self.interval = interval
        self._proxies = []          # list of proxy names
        self._lock = threading.Lock()
        self._idx = 0
        self._sticky_name = None
        self._last_rotate = 0
        self._manager = ProxyManager()

    def set_pool(self, names):
        """Set the proxy names in this pool."""
        with self._lock:
            self._proxies = list(names)
            self._idx = 0
            self._sticky_name = None

    def add(self, name):
        """Add a proxy name to the pool."""
        with self._lock:
            if name not in self._proxies:
                self._proxies.append(name)

    def remove(self, name):
        """Remove a proxy name from the pool."""
        with self._lock:
            self._proxies = [p for p in self._proxies if p != name]
            if self._sticky_name == name:
                self._sticky_name = None

    def next(self):
        """Return the next proxy dict according to the rotation mode.

        Returns None when the pool is empty. Skips proxies that fail
        the health check.
        """
        with self._lock:
            if not self._proxies:
                return None
            if self.mode == "sticky" and self._sticky_name:
                return self._get(self._sticky_name)
            if self.mode == "timed":
                now = time.monotonic()
                if (self._sticky_name and
                        now - self._last_rotate < self.interval):
                    return self._get(self._sticky_name)
            # per-request or timed-expired: advance
            for _ in range(len(self._proxies)):
                name = self._proxies[self._idx % len(self._proxies)]
                self._idx += 1
                proxy = self._get(name)
                if proxy is not None:
                    self._sticky_name = name
                    self._last_rotate = time.monotonic()
                    return proxy
            return None

    def _get(self, name):
        """Return proxy dict if it passes health check, else None."""
        try:
            if not self._manager.test(name):
                return None
            return self._manager.get(name)
        except (KeyError, Exception):
            return None

    def mark_failed(self, name):
        """Mark a proxy as failed (forces rotation on next call)."""
        with self._lock:
            if self._sticky_name == name:
                self._sticky_name = None

    def size(self):
        """Number of proxies in the pool."""
        with self._lock:
            return len(self._proxies)
