"""Vendored-dependency shim.

Inserts the repository's ``vendor/`` directory at the front of ``sys.path``
so every module can ``import camoufox`` / ``import browserforge`` from the
vendored copies without installing anything from pip.

Usage (must be the FIRST import in any module that needs vendored code)::

    from src import _vendor  # noqa: F401  (or: import src._vendor)
    from camoufox.sync_api import Camoufox
"""

import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
_VENDOR_DIR = os.path.normpath(_VENDOR_DIR)

if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)
