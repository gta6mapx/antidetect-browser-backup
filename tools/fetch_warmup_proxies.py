#!/usr/bin/env python3
"""Print verified free proxies for the warm-up / testing tier.

Uses ProxyManager.fetch_warmup_proxies() (proxy-scraper OSS, MIT).
Read-only: fetches data and prints it — never assigns a proxy to any
profile and never writes to the proxy store.

STANDING RULE: free proxies are NEVER used for real Gmail sessions or
any profile holding a real login. Warm-up and connection-shape testing
only.

Usage:
    python3 tools/fetch_warmup_proxies.py [--limit N] [--country CC]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=20,
                    help="max proxies to print (default 20)")
    ap.add_argument("--country", default="",
                    help="two-letter country filter, e.g. US (optional)")
    args = ap.parse_args()

    from src.proxy.manager import ProxyManager

    try:
        proxies = ProxyManager().fetch_warmup_proxies(limit=args.limit)
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.country:
        cc = args.country.strip().upper()
        proxies = [p for p in proxies if p["country"] == cc]

    if not proxies:
        print("no warm-up proxies returned")
        return 0

    print(f"{len(proxies)} warm-up proxies (warm-up tier only — "
          "never for real Gmail sessions):")
    for p in proxies:
        print(f"  {p['type']:7} {p['host']:>18}:{p['port']:<5} "
              f"country={p['country'] or '?':3} "
              f"latency={p['latency_ms']}ms anon={p['anonymity'] or '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
