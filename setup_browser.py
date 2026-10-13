#!/usr/bin/env python3
"""Download the Camoufox browser binary (once). The binary (~200MB) is NOT
kept in git — this script fetches the official release via the vendored
package's own fetch routine into ~/.camoufox/."""
import sys

sys.path.insert(0, "vendor")
from camoufox.pkgman import CamoufoxFetcher  # type: ignore

if __name__ == "__main__":
    fetcher = CamoufoxFetcher()
    fetcher.fetch_latest()
    fetcher.install()
    print("Camoufox browser binary ready.")
