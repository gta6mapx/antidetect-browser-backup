"""Sticky proxy pool: one proxy pinned per profile until it dies.

Sticky strategy pattern learned from gproxynet/proxyspin (MIT,
https://github.com/gproxynet/proxyspin); implementation is original
(package not depended upon — too new/unverified).

What "sticky" means here
------------------------
A profile keeps the SAME proxy across launches (same exit IP, better
trust signals) until the proxy stops working. Health is tracked per
proxy:

- ``record_result(proxy_name, ok, http_status)`` feeds successes and
  failures into per-proxy counters. A success resets the failure
  streak; a failure increments it.
- Failure streak >= ``FAIL_STREAK_BENCH`` (3) benches the proxy with
  exponential backoff: ``BASE_BENCH_S * 2**streak`` seconds, capped at
  ``MAX_BENCH_S`` (24h). The proxy rejoins the pool automatically when
  the bench expires.
- Ban codes (HTTP 403/407/429) count extra against health
  (``ban_streak`` + an extra failure) and bench the proxy immediately.

When the pinned proxy for a profile is benched or dead, the pool
automatically picks the healthiest available replacement and re-pins
the profile to it. SOCKS4/5 proxies are fully supported: health probes
go through :meth:`ProxyManager.check_endpoint`, which speaks SOCKS via
PySocks and HTTP over raw sockets (see ``src/proxy/manager.py``).

Storage: two tables (``sticky_pins``, ``proxy_health``) inside the same
``proxies.db`` SQLite file the :class:`ProxyManager` uses — separate
from the profiles database, per repo convention. Accessed through
``ProxyManager.sticky_pool``.
"""

import os
import sqlite3
import threading
import time

import src._vendor  # noqa: F401  (first import: keep stdlib + venv only after this)

from src import paths as _paths

#: HTTP statuses treated as bans: they count extra against health and
#: bench the proxy immediately (mirrors proxyspin's ban-code idea).
BAN_CODES = {403, 407, 429}

#: Failure streak length that triggers a bench.
FAIL_STREAK_BENCH = 3

#: Bench base: 5 minutes, doubled per streak step, capped at 24h.
BASE_BENCH_S = 300
MAX_BENCH_S = 24 * 3600

#: Immediate bench on a ban code: 30 minutes, doubled per ban streak.
BAN_BENCH_BASE_S = 1800

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sticky_pins (
    profile_name TEXT PRIMARY KEY,
    proxy_name   TEXT NOT NULL,
    pinned_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_health (
    proxy_name      TEXT PRIMARY KEY,
    successes       INTEGER NOT NULL DEFAULT 0,
    failures        INTEGER NOT NULL DEFAULT 0,
    failure_streak  INTEGER NOT NULL DEFAULT 0,
    ban_streak      INTEGER NOT NULL DEFAULT 0,
    last_failure    INTEGER,
    last_success    INTEGER,
    benched_until   INTEGER NOT NULL DEFAULT 0,
    last_latency_ms INTEGER
);
"""


class StickyPool:
    """SQLite-backed sticky proxy pool.

    Args:
        db_path: Path to the proxies SQLite file. Defaults to
            ``src.paths.proxies_db()`` (honors ``ANTIDETECT_HOME``);
            tests may pass a temporary path instead.
        manager: Optional :class:`ProxyManager` bound to the same
            database. When given, the pool can resolve proxy dicts,
            run health probes, and build stats over the real store.
        now_fn: Clock injection for tests (defaults to
            ``time.time``).
    """

    def __init__(self, db_path=None, manager=None, now_fn=None):
        self._db_path = db_path or _paths.proxies_db()
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)),
                    exist_ok=True)
        self._manager = manager
        self._now = now_fn or time.time
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # -- internals ------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_health_row(self, conn, proxy_name: str):
        conn.execute(
            "INSERT OR IGNORE INTO proxy_health (proxy_name)"
            " VALUES (?)", (proxy_name,))

    @staticmethod
    def _bench_seconds(failure_streak: int) -> int:
        return min(BASE_BENCH_S * (2 ** failure_streak), MAX_BENCH_S)

    @staticmethod
    def _ban_bench_seconds(ban_streak: int) -> int:
        return min(BAN_BENCH_BASE_S * (2 ** (ban_streak - 1)), MAX_BENCH_S)

    def _health_row(self, proxy_name):
        with self._lock, self._connect() as conn:
            self._ensure_health_row(conn, proxy_name)
            return conn.execute(
                "SELECT * FROM proxy_health WHERE proxy_name = ?",
                (proxy_name,)).fetchone()

    # -- pinning --------------------------------------------------------
    def pin(self, profile_name: str, proxy_name: str) -> str:
        """Pin one proxy for ``profile_name``. Returns ``proxy_name``.

        Re-pinning a profile to a different proxy replaces the old pin.
        If a manager is bound, the proxy must exist in the store
        (KeyError otherwise).
        """
        if self._manager is not None:
            self._manager.get(proxy_name)  # KeyError on unknown proxy
        with self._lock, self._connect() as conn:
            self._ensure_health_row(conn, proxy_name)
            conn.execute(
                "INSERT INTO sticky_pins (profile_name, proxy_name, pinned_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(profile_name) DO UPDATE SET"
                " proxy_name = excluded.proxy_name,"
                " pinned_at = excluded.pinned_at",
                (profile_name, proxy_name, int(self._now())))
            conn.commit()
        return proxy_name

    def unpin(self, profile_name: str) -> bool:
        """Remove the pin for ``profile_name``. True if one existed."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM sticky_pins WHERE profile_name = ?",
                (profile_name,))
            conn.commit()
        return cur.rowcount > 0

    def pinned_name(self, profile_name: str) -> str | None:
        """Return the proxy name pinned for ``profile_name`` (no checks)."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT proxy_name FROM sticky_pins WHERE profile_name = ?",
                (profile_name,)).fetchone()
        return row["proxy_name"] if row else None

    def get_pinned(self, profile_name: str) -> dict | None:
        """Return the pinned proxy dict for ``profile_name``.

        Returns None when nothing is pinned (or no manager is bound).
        If the pinned proxy is currently benched, the profile is
        automatically re-pinned to the healthiest available proxy.
        """
        name = self.pinned_name(profile_name)
        if name is None or self._manager is None:
            return None
        if self.is_benched(name):
            replacement = self._pick_replacement(exclude=name)
            if replacement is not None:
                self.pin(profile_name, replacement)
                name = replacement
        try:
            return self._manager.get(name)
        except KeyError:
            # Pinned proxy was deleted from the store: clean the pin.
            self.unpin(profile_name)
            return None

    # -- health ---------------------------------------------------------
    def record_result(self, proxy_name: str, ok: bool,
                      http_status: int | None = None,
                      latency_ms: int | None = None) -> dict:
        """Record one health-probe result for a proxy.

        Args:
            proxy_name: proxy whose result is recorded.
            ok: True on success; resets the failure streak.
            http_status: response status when available. A ban code
                (403/407/429) counts as an extra failure, bumps
                ``ban_streak``, and benches the proxy immediately.
            latency_ms: last observed latency (recorded on success).

        Returns the updated health row as a dict. Never raises.
        """
        now = int(self._now())
        with self._lock, self._connect() as conn:
            self._ensure_health_row(conn, proxy_name)
            if ok:
                conn.execute(
                    "UPDATE proxy_health SET successes = successes + 1,"
                    " failure_streak = 0, last_success = ?,"
                    " last_latency_ms = ? WHERE proxy_name = ?",
                    (now, latency_ms, proxy_name))
            else:
                ban = (http_status is not None) and (http_status in BAN_CODES)
                conn.execute(
                    "UPDATE proxy_health SET failures = failures + ?"
                    " + ?,"  # ban codes count extra against health
                    " failure_streak = failure_streak + 1,"
                    " ban_streak = ban_streak + ?,"
                    " last_failure = ? WHERE proxy_name = ?",
                    (1, 1 if ban else 0, 1 if ban else 0, now, proxy_name))
                row = conn.execute(
                    "SELECT failure_streak, ban_streak FROM proxy_health"
                    " WHERE proxy_name = ?", (proxy_name,)).fetchone()
                benched_until = 0
                if row["failure_streak"] >= FAIL_STREAK_BENCH:
                    benched_until = now + self._bench_seconds(
                        row["failure_streak"])
                if ban:
                    # Ban codes bench immediately, whatever the streak.
                    ban_bench = now + self._ban_bench_seconds(row["ban_streak"])
                    benched_until = max(benched_until, ban_bench)
                conn.execute(
                    "UPDATE proxy_health SET benched_until = ?"
                    " WHERE proxy_name = ?",
                    (benched_until, proxy_name))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM proxy_health WHERE proxy_name = ?",
                (proxy_name,)).fetchone()
        return dict(row)

    def health(self, proxy_name: str) -> dict:
        """Return the health row for ``proxy_name`` as a dict."""
        return dict(self._health_row(proxy_name))

    def is_benched(self, proxy_name: str, now: float | None = None) -> bool:
        """True when the proxy is currently benched."""
        row = self._health_row(proxy_name)
        return (row["benched_until"] or 0) > (now if now is not None
                                              else self._now())

    def probe(self, proxy_name: str) -> dict:
        """Health-probe one proxy and record the result.

        Goes through :meth:`ProxyManager.check_endpoint` — raw sockets
        for HTTP, PySocks for SOCKS4/5 — so SOCKS5 proxies are probed
        end to end (TCP + protocol handshake + exit IP).

        Requires a bound manager. Returns the check result dict
        (``{"ok", "latency_ms", "exit_ip", "error", "detected_type"}``).
        """
        if self._manager is None:
            raise RuntimeError("probe() needs a StickyPool with manager")
        proxy = self._manager.get(proxy_name)  # KeyError on unknown
        result = self._manager.check_endpoint(
            proxy["host"], proxy["port"],
            proxy.get("type") or "http", timeout=10,
            auth=(f"{proxy['username']}:{proxy['password']}"
                  if proxy.get("username") else ""))
        self.record_result(proxy_name, result["ok"],
                           latency_ms=result.get("latency_ms"))
        return result

    # -- replacement selection ------------------------------------------
    def _pick_replacement(self, exclude: str | None = None) -> str | None:
        """Name of the healthiest non-benched proxy, or None.

        Ranked by lowest failure streak, then most successes.
        """
        if self._manager is None:
            return None
        candidates = []
        for p in self._manager.list():
            name = p["name"]
            if name == exclude:
                continue
            row = self._health_row(name)
            if row["benched_until"] > self._now():
                continue
            candidates.append((row["failure_streak"],
                               -row["successes"], name))
        candidates.sort()
        return candidates[0][2] if candidates else None

    # -- stats ----------------------------------------------------------
    def pool_stats(self) -> dict:
        """``{total, healthy, benched, pinned}`` for the dashboard."""
        now = self._now()
        if self._manager is not None:
            names = [p["name"] for p in self._manager.list()]
        else:
            with self._lock, self._connect() as conn:
                names = [r["proxy_name"] for r in conn.execute(
                    "SELECT proxy_name FROM proxy_health").fetchall()]
        total = len(names)
        benched = sum(1 for n in names if self.is_benched(n, now=now))
        with self._lock, self._connect() as conn:
            pinned = conn.execute(
                "SELECT COUNT(*) AS c FROM sticky_pins").fetchone()["c"]
        return {"total": total,
                "healthy": total - benched,
                "benched": benched,
                "pinned": pinned}

    def pins(self) -> list:
        """All pins as ``[{profile_name, proxy_name, pinned_at}]``."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sticky_pins ORDER BY profile_name").fetchall()
        return [dict(r) for r in rows]
