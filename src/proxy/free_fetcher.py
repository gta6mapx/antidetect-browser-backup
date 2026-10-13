"""Free proxy fetcher for TESTING only.

Fetches free proxies from public lists, tests them, and adds working
ones to the proxy manager. NOT for production use — free proxies are
unreliable and often flagged.

Sources: public free proxy lists (no auth required).
"""

import re
import socket
import threading
import urllib.request


# Free proxy list sources (plain text host:port per line).
SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
]


def _fetch_url(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _parse_proxies(text, ptype):
    out = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$", line)
        if m:
            host, port = m.group(1), int(m.group(2))
            if 1 <= port <= 65535:
                out.append({"host": host, "port": port, "type": ptype})
    return out


def fetch_free_proxies(max_per_source=50):
    """Fetch free proxies from public lists.

    Returns list of {"host", "port", "type"}. Untested — call
    test_proxy() to verify before use.
    """
    proxies = []
    for url in SOURCES:
        ptype = "socks5" if "socks5" in url else "http"
        text = _fetch_url(url)
        parsed = _parse_proxies(text, ptype)[:max_per_source]
        proxies.extend(parsed)
    # Deduplicate.
    seen = set()
    unique = []
    for p in proxies:
        key = (p["host"], p["port"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def test_proxy(host, port, timeout=5):
    """TCP connectivity test. Returns (ok, latency_ms)."""
    import time
    start = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, int((time.monotonic() - start) * 1000)
    except Exception:
        return False, None


def fetch_and_test(max_proxies=20, workers=10, timeout=5):
    """Fetch free proxies and return only working ones.

    Returns list of {"host", "port", "type", "latency_ms"}.
    """
    candidates = fetch_free_proxies(max_per_source=100)
    working = []
    lock = threading.Lock()

    def check(p):
        ok, latency = test_proxy(p["host"], p["port"], timeout=timeout)
        if ok:
            with lock:
                working.append({**p, "latency_ms": latency})

    threads = []
    for p in candidates:
        t = threading.Thread(target=check, args=(p,))
        threads.append(t)
        t.start()
        # Limit concurrency.
        while sum(1 for t in threads if t.is_alive()) >= workers:
            for t in threads:
                t.join(timeout=0.1)
        if len(working) >= max_proxies:
            break
    for t in threads:
        t.join(timeout=2)
    working.sort(key=lambda x: x["latency_ms"])
    return working[:max_proxies]
