"""Tests for the sticky proxy pool (src/proxy/sticky.py).

All tests are offline: no network, fake clock for benching.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.proxy.manager import ProxyManager
from src.proxy.sticky import (
    BAN_CODES, FAIL_STREAK_BENCH, MAX_BENCH_S, BASE_BENCH_S,
    StickyPool,
)


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_pool(tmp, clock=None, with_manager=True):
    clock = clock or FakeClock()
    db = os.path.join(tmp, "proxies.db")
    if with_manager:
        mgr = ProxyManager(db_path=db)
        for i, ptype in enumerate(["http", "socks5", "socks4"]):
            mgr.add(f"px-{ptype}", host="10.0.0.%d" % (i + 1),
                    port=8080 + i, ptype=ptype)
        return StickyPool(db_path=db, manager=mgr, now_fn=clock), mgr, clock
    return StickyPool(db_path=db, now_fn=clock), None, clock


class TestPinning:
    def test_pin_and_get_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, mgr, _ = make_pool(tmp)
            assert pool.pin("client-a", "px-http") == "px-http"
            pinned = pool.get_pinned("client-a")
            assert pinned["name"] == "px-http"
            assert pinned["host"] == "10.0.0.1"
            assert pinned["type"] == "http"

    def test_unpinned_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            assert pool.get_pinned("nobody") is None

    def test_repin_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.pin("client-a", "px-http")
            pool.pin("client-a", "px-socks5")
            assert pool.get_pinned("client-a")["name"] == "px-socks5"

    def test_pin_unknown_proxy_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            try:
                pool.pin("client-a", "nope")
            except KeyError:
                pass
            else:
                raise AssertionError("expected KeyError")

    def test_unpin(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.pin("client-a", "px-http")
            assert pool.unpin("client-a") is True
            assert pool.get_pinned("client-a") is None
            assert pool.unpin("client-a") is False


class TestHealth:
    def test_success_resets_failure_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.record_result("px-http", False)
            pool.record_result("px-http", False)
            assert pool.health("px-http")["failure_streak"] == 2
            pool.record_result("px-http", True, latency_ms=123)
            h = pool.health("px-http")
            assert h["failure_streak"] == 0
            assert h["successes"] == 1
            assert h["last_latency_ms"] == 123

    def test_streak_three_benches_with_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, clock = make_pool(tmp)
            pool.record_result("px-http", False)  # streak 1
            pool.record_result("px-http", False)  # streak 2
            assert pool.is_benched("px-http") is False
            pool.record_result("px-http", False)  # streak 3 -> bench
            assert pool.is_benched("px-http") is True
            h = pool.health("px-http")
            expected = clock.t + BASE_BENCH_S * 2 ** FAIL_STREAK_BENCH
            assert h["benched_until"] == expected

    def test_bench_exponential_growth_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            assert pool._bench_seconds(3) == BASE_BENCH_S * 8
            assert pool._bench_seconds(4) == BASE_BENCH_S * 16
            assert pool._bench_seconds(100) == MAX_BENCH_S

    def test_bench_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, clock = make_pool(tmp)
            for _ in range(FAIL_STREAK_BENCH):
                pool.record_result("px-http", False)
            assert pool.is_benched("px-http") is True
            clock.advance(BASE_BENCH_S * 2 ** FAIL_STREAK_BENCH + 1)
            assert pool.is_benched("px-http") is False

    def test_success_does_not_clear_bench(self):
        # A success resets the streak but an active bench is a real
        # signal — recording a success mid-bench shouldn't un-bench.
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            for _ in range(FAIL_STREAK_BENCH):
                pool.record_result("px-http", False)
            assert pool.is_benched("px-http") is True


class TestBanCodes:
    def test_ban_code_counts_extra_and_benches_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.record_result("px-http", False, http_status=403)
            h = pool.health("px-http")
            assert h["failures"] == 2  # base + extra
            assert h["ban_streak"] == 1
            assert pool.is_benched("px-http") is True  # immediate

    def test_ban_codes_407_and_429(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            for name, code in (("px-http", 407), ("px-socks5", 429)):
                pool.record_result(name, False, http_status=code)
                assert pool.health(name)["ban_streak"] == 1, code
                assert pool.is_benched(name) is True, code
            assert BAN_CODES == {403, 407, 429}

    def test_non_ban_code_no_immediate_bench(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.record_result("px-http", False, http_status=500)
            h = pool.health("px-http")
            assert h["failures"] == 1
            assert h["ban_streak"] == 0
            assert pool.is_benched("px-http") is False


class TestReplacement:
    def test_benched_pinned_proxy_replaced_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, clock = make_pool(tmp)
            pool.pin("client-a", "px-http")
            for _ in range(FAIL_STREAK_BENCH):
                pool.record_result("px-http", False)
            # px-http benched -> get_pinned swaps to a healthy proxy
            pinned = pool.get_pinned("client-a")
            assert pinned["name"] in ("px-socks5", "px-socks4")
            assert pool.pinned_name("client-a") == pinned["name"]

    def test_replacement_picks_healthiest(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.record_result("px-socks4", False)  # streak 1
            pool.record_result("px-socks5", True)   # streak 0 + success
            pool.record_result("px-socks5", True)
            chosen = pool._pick_replacement()
            assert chosen == "px-socks5"

    def test_benched_excluded_from_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            for _ in range(FAIL_STREAK_BENCH):
                pool.record_result("px-http", False)
            chosen = pool._pick_replacement()
            assert chosen in ("px-socks5", "px-socks4")


class TestStats:
    def test_pool_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            stats = pool.pool_stats()
            assert stats == {"total": 3, "healthy": 3, "benched": 0,
                             "pinned": 0}
            pool.pin("client-a", "px-http")
            for _ in range(FAIL_STREAK_BENCH):
                pool.record_result("px-socks5", False)
            stats = pool.pool_stats()
            assert stats["total"] == 3
            assert stats["benched"] == 1
            assert stats["healthy"] == 2
            assert stats["pinned"] == 1

    def test_manager_sticky_pool_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ProxyManager(db_path=os.path.join(tmp, "proxies.db"))
            mgr.add("px-a", host="10.0.0.9", port=8080, ptype="socks5")
            pool = mgr.sticky_pool
            assert isinstance(pool, StickyPool)
            pool.pin("client-a", "px-a")
            pinned = pool.get_pinned("client-a")
            assert pinned["type"] == "socks5"
            stats = pool.pool_stats()
            assert stats["total"] == 1 and stats["pinned"] == 1

    def test_pins_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool, _, _ = make_pool(tmp)
            pool.pin("client-b", "px-socks5")
            pool.pin("client-a", "px-http")
            rows = pool.pins()
            assert [r["profile_name"] for r in rows] == ["client-a",
                                                        "client-b"]
