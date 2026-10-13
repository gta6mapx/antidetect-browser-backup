"""Cookie import / export for browser profiles.

Public API lives in :mod:`src.cookies.manager`:

* Format logic (vendored verbatim from persona-studio, MIT):
  :func:`normalize`, :func:`parse`, :func:`parse_netscape`,
  :func:`to_netscape`, :func:`dumps`, :func:`read_file`
* Browser round-trip (adapted to our Camoufox launch layer):
  :func:`export_cookies`, :func:`import_cookies`

Formats: Cookie-Editor / EditThisCookie extension JSON, Playwright
storage-state JSON, and Netscape ``cookies.txt`` — auto-detected on import.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

from src.cookies.manager import (
    dumps,
    export_cookies,
    import_cookies,
    normalize,
    parse,
    parse_netscape,
    read_file,
    to_netscape,
)

__all__ = [
    "dumps",
    "export_cookies",
    "import_cookies",
    "normalize",
    "parse",
    "parse_netscape",
    "read_file",
    "to_netscape",
]
