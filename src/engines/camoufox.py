"""Camoufox engine: thin wrapper around the existing launcher.

All launch logic stays in :mod:`src.browser.launcher` — this class only
adapts it to the :class:`~src.engines.base.Engine` interface so callers
can dispatch on the profile's ``engine`` field without knowing which
browser family is underneath.
"""

from src.engines.base import Engine


class CamoufoxEngine(Engine):
    """Firefox-based anti-detect engine (default)."""

    name = "camoufox"

    def launch_profile(self, profile, headless=False):
        """Launch via :func:`src.browser.launcher.launch_profile`."""
        from src.browser import launcher
        return launcher.launch_profile(profile, headless=headless)

    def installed_version(self):
        """``(major, full)`` of the installed Camoufox browser, or None."""
        from src.browser import launcher
        return launcher.installed_firefox_version()

    def binary_present(self):
        """True when the Camoufox browser binary is installed."""
        from src.browser import launcher
        try:
            return bool(launcher.browser_binary_present())
        except Exception:
            return False

    def ensure_binary(self):
        """Download the Camoufox browser binary if missing (auto-install)."""
        from src.browser import launcher
        return launcher.ensure_browser_binary()
