"""Regression tests for 127.0.0.1 launch issue.

The browser was navigating to 127.0.0.1 (dashboard URL) due to
Firefox session restore. These tests verify the surgical fix.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestLaunch127001:
    """Verify 127.0.0.1 handling in launcher."""

    def test_build_kwargs_has_no_dashboard_url(self):
        """build_launch_kwargs must never include 127.0.0.1 or :8765."""
        from src.browser.launcher import build_launch_kwargs
        persona = {"name": "test", "os": "windows"}
        kwargs = build_launch_kwargs(persona)
        import json
        kwargs_str = json.dumps(kwargs, default=str)
        assert "127.0.0.1" not in kwargs_str, "kwargs contain 127.0.0.1!"
        assert "8765" not in kwargs_str, "kwargs contain dashboard port!"

    def test_build_kwargs_no_proxy_by_default(self):
        """Without proxy in persona, no proxy kwarg should be set."""
        from src.browser.launcher import build_launch_kwargs
        persona = {"name": "test", "os": "windows"}
        kwargs = build_launch_kwargs(persona)
        assert "proxy" not in kwargs, "proxy should not be set by default"

    def test_user_data_dir_isolated(self):
        """Each profile gets its own user_data_dir."""
        from src.browser.launcher import build_launch_kwargs
        k1 = build_launch_kwargs({"name": "prof-a", "os": "windows"})
        k2 = build_launch_kwargs({"name": "prof-b", "os": "windows"})
        assert k1["user_data_dir"] != k2["user_data_dir"]
        assert "prof-a" in k1["user_data_dir"]
        assert "prof-b" in k2["user_data_dir"]

    def test_no_session_restore_deletion_function(self):
        """_clear_session_restore should not exist (user forbade data deletion)."""
        import src.browser.launcher as launcher
        assert not hasattr(launcher, "_clear_session_restore"), \
            "_clear_session_restore should be removed - do not delete profile data"

    def test_stale_lock_clear_exists(self):
        """_clear_stale_locks should exist for crash recovery."""
        import src.browser.launcher as launcher
        assert hasattr(launcher, "_clear_stale_locks")

    def test_fix_broken_search_engine_exists(self):
        """_fix_broken_search_engine should exist."""
        import src.browser.launcher as launcher
        assert hasattr(launcher, "_fix_broken_search_engine")

    def test_fix_broken_search_engine_installs_google(self):
        """Should install Enterprise Policy with Google search engine."""
        import tempfile, os, json
        import src.browser.launcher as launcher
        tmpdir = tempfile.mkdtemp()
        launcher._fix_broken_search_engine(tmpdir)
        policies_file = os.path.join(tmpdir, "distribution", "policies.json")
        assert os.path.isfile(policies_file), "policies.json should be installed"
        with open(policies_file) as f:
            policies = json.load(f)
            engines = policies["policies"]["SearchEngines"]
            assert engines["Default"] == "Google"
            google = engines["Add"][0]
            assert "google.com/search" in google["URLTemplate"]
            assert "127.0.0.1" not in google["URLTemplate"]

    def test_fix_broken_search_engine_removes_search_json(self):
        """Should delete search.json.mozlz4 to force rebuild."""
        import tempfile, os
        import src.browser.launcher as launcher
        tmpdir = tempfile.mkdtemp()
        search_json = os.path.join(tmpdir, "search.json.mozlz4")
        with open(search_json, "wb") as f:
            f.write(b"fake corrupted data")
        launcher._fix_broken_search_engine(tmpdir)
        assert not os.path.exists(search_json), "search.json.mozlz4 should be deleted"

