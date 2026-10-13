"""Profile migration: import profiles from other anti-detect browsers.

Public API lives in :mod:`src.migrate.importers`:

* :func:`src.migrate.importers.import_adspower`
* :func:`src.migrate.importers.import_gologin`
* :func:`src.migrate.importers.detect_source`

Format details: ``src/migrate/README.md``.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

from src.migrate.importers import (
    detect_source,
    import_adspower,
    import_gologin,
    load_export,
    profile_from_export,
)

__all__ = [
    "detect_source",
    "import_adspower",
    "import_gologin",
    "load_export",
    "profile_from_export",
]
