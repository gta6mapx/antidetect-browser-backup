import src._vendor  # noqa: F401  -- vendored deps shim; must stay first

"""Bulk operations for the anti-detect browser.

CSV import/export of many profiles at once, plus reusable named profile
templates.  The heavy lifting lives in :mod:`src.bulk.importer`; this package
re-exports the public API for convenience.
"""

from src.bulk.importer import (
    bulk_export,
    bulk_import,
    get_template,
    list_templates,
    save_template,
)

__all__ = [
    "bulk_export",
    "bulk_import",
    "get_template",
    "list_templates",
    "save_template",
]
