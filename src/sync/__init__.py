"""Action synchronizer: mirror one profile's actions onto follower profiles.

See :mod:`src.sync.manager` for the implementation and ``README.md`` in this
directory for the design, API and attribution notes.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from src.sync.manager import SyncManager

__all__ = ["SyncManager"]
