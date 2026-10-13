#!/usr/bin/env python3
"""Download the MaxMind GeoLite2-City database (free, CC BY-SA 4.0).

Required for IP geolocation ("Based on IP" fingerprint modes).
Run once after cloning: python3 bin/download_geolite2.py

Attribution: https://www.maxmind.com/en/geolite2/eula
"""

import os
import sys
import urllib.request

URL = ("https://github.com/P3TERX/GeoLite.mmdb/raw/download/"
       "GeoLite2-City.mmdb")
DEST = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "GeoLite2-City.mmdb")


def main():
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    if os.path.exists(DEST) and os.path.getsize(DEST) > 10_000_000:
        print("GeoLite2 database already present: %s" % DEST)
        return 0
    print("Downloading GeoLite2-City.mmdb (~50MB)...")
    try:
        urllib.request.urlretrieve(URL, DEST)
    except Exception as exc:
        print("Download failed: %s" % exc, file=sys.stderr)
        return 1
    size = os.path.getsize(DEST)
    print("Saved %s (%d bytes)" % (DEST, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
