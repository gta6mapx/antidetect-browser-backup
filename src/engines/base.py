"""Base interface for browser engines."""


class Engine:
    """How to launch a browser for a stored persona.

    Concrete engines implement:

    - :attr:`name` — canonical engine name (``"camoufox"`` / ``"patchright"``).
    - :meth:`launch_profile` — launch a browser for a persona dict and
      return a launched-profile object exposing ``.page``, ``.browser``
      and ``.close()`` (plus context-manager support).
    - :meth:`installed_version` — ``(major, full)`` of the browser binary
      this engine would launch, or ``None`` when it is not installed or
      the version cannot be determined. Never downloads, never raises.
    - :meth:`binary_present` — whether a usable browser binary is
      installed. Never downloads, never raises.
    """

    name = "base"

    def launch_profile(self, profile, headless=False):
        """Launch a browser for ``profile`` (a persona dict).

        :param profile: persona dict as returned by
            :meth:`src.profiles.manager.ProfileManager.get`.
        :param headless: run the browser headless.
        :return: launched-profile object with ``.page`` / ``.browser`` /
            ``.close()``.
        """
        raise NotImplementedError

    def installed_version(self):
        """Return ``(major, full)`` of the installed browser, or None.

        ``major`` is an int, ``full`` the version string. ``None`` means
        "not installed or version unknown" — callers must treat it as
        unknown, never as a failure.
        """
        raise NotImplementedError

    def binary_present(self):
        """Return True if a browser binary usable by this engine exists."""
        return self.installed_version() is not None

    def ensure_binary(self):
        """Download the browser binary if missing (auto-install).

        Default: no-op that re-checks presence. Engines override with a real
        downloader. Returns True when a usable binary exists afterwards.
        """
        return self.binary_present()
