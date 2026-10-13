"""Tests for recent improvements: proxy checker, copy clone, cache clean."""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.proxy.manager import ProxyManager
from src.rpa.bridge import activity_to_step, activities_to_recipe


class TestProxyChecker:
    def test_returns_exit_ip_field(self):
        """New checker returns exit_ip field (None if HTTP fails)."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ProxyManager(db_path=os.path.join(tmp, "test.db"))
            mgr.add("test-px", host="127.0.0.1", port=9999, ptype="http")
            result = mgr.test_with_latency("test-px")
            assert "exit_ip" in result
            assert "ok" in result
            assert "latency_ms" in result

    def test_nonexistent_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ProxyManager(db_path=os.path.join(tmp, "test.db"))
            result = mgr.test_with_latency("nope")
            assert result["ok"] is False
            assert result["exit_ip"] is None


class TestRpaBridge:
    def test_all_activities_map(self):
        """All 15 executable activities convert to steps."""
        from src.rpa.bridge import ACTIVITY_TO_STEP
        assert len(ACTIVITY_TO_STEP) == 15

    def test_open_browser_returns_none(self):
        """Browser lifecycle activities return None (handled by launcher)."""
        assert activity_to_step("Open Browser") is None
        assert activity_to_step("Close Browser") is None

    def test_recipe_has_engine_marker(self):
        recipe = activities_to_recipe("T", [
            {"name": "Click", "params": {"selector": "#b"}},
        ])
        assert recipe["engine"] == "rpaforge"
        assert len(recipe["steps"]) == 1
        assert recipe["steps"][0]["type"] == "click"


class TestCacheClean:
    def test_clean_removes_cache_dirs(self):
        """Cache dirs deleted, cookies preserved."""
        from src.browser.launcher import _clean_profile_cache, _CACHE_DIRS
        with tempfile.TemporaryDirectory() as tmp:
            # Create fake profile structure.
            for d in _CACHE_DIRS[:3]:
                os.makedirs(os.path.join(tmp, d))
            # Create fake cookies file (should survive).
            cookies = os.path.join(tmp, "cookies.sqlite")
            open(cookies, "w").write("fake")
            _clean_profile_cache(tmp)
            for d in _CACHE_DIRS[:3]:
                assert not os.path.exists(os.path.join(tmp, d))
            assert os.path.exists(cookies)

    def test_clean_handles_missing_dir(self):
        from src.browser.launcher import _clean_profile_cache
        _clean_profile_cache("/nonexistent/path/xyz")  # Should not raise.
        _clean_profile_cache(None)  # Should not raise.
        _clean_profile_cache("")  # Should not raise.
