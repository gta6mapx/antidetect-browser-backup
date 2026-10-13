"""Wire-level checks: DNS-leak detection + JA3/TLS-profile verification.

Why this module exists
---------------------
Two things the in-browser fingerprint can never prove on its own:

1. **TLS profile on the wire** — the proxy-section tooling (and any
   server-side fetch the app makes) historically used Python's own TLS
   stack, whose ClientHello (JA3) is trivially distinguishable from a
   real browser's. :func:`fetch_via_browser_tls` fixes that by routing
   fetches through a real browser TLS profile, and
   :func:`tls_profile_check` verifies the handshake a proxy path
   actually presents against the expected profile.

2. **DNS leaks** — a proxy that tunnels TCP but lets the client resolve
   hostnames locally sends every hostname to the local ISP resolver.
   :func:`dns_leak_check` detects that empirically.

Engine
------
TLS impersonation is done with **tlsmask** (MIT License, Copyright (c)
2026 Ubaidullah71, https://github.com/ubaidullah71/tlsmask), which wraps
**bogdanfinn/tls-client** v1.16.0 (BSD 4-Clause, Copyright (c) 2023
Bogdan Finn, https://github.com/bogdanfinn/tls-client) — the Go library
that replays real browsers' ClientHello + HTTP/2 fingerprints. The
native library file shipped in the wheel is SHA-256-pinned and
re-verified every time it loads (``python -m tlsmask`` prints the
check); the Python wrapper was audited 2026-10-13 and performs no
network/subprocess/eval of its own — all I/O goes through the Go
library via ctypes.

Caution (from the OSS evaluation): tlsmask is a 1-star, single-
maintainer project whose repo was created 2026-09-26. It is used here
only for its narrow, verifiable job (browser-TLS handshakes), the
bundled native binary is bogdanfinn's own release file byte-for-byte
(hash-checked at build AND at load), and the expected JA3 values below
were measured empirically against a local capture server rather than
taken on trust. If the package ever becomes unmaintained, the natural
fallback is stdlib-only JA3 *capture* (parse our own ClientHello) —
stdlib ``ssl`` can never *impersonate* a browser handshake, so there is
no stdlib fallback for the impersonation itself.

Result shape
------------
Both checks return the validator's canonical dict::

    {"check": <name>, "passed": <bool>, "detail": <str>, ...extra keys...}

so they can be appended to the per-check lists produced by
``src.fingerprints.validator.validate`` / ``src.health.checker``.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

import contextlib
import ipaddress
import json
import socket
import threading
import uuid
from urllib.parse import quote

try:
    import tlsmask
    from tlsmask.exceptions import TLSClientException  # noqa: F401  (re-exported)
except Exception as _import_exc:  # pragma: no cover - import guard
    tlsmask = None  # type: ignore[assignment]
    TLSClientException = Exception  # type: ignore[assignment,misc]
    _TLSMASK_IMPORT_ERROR = _import_exc
else:
    _TLSMASK_IMPORT_ERROR = None

__all__ = [
    "EXPECTED_JA3",
    "dns_leak_check",
    "fetch_via_browser_tls",
    "profile_for_engine",
    "tls_profile_check",
]

# ---------------------------------------------------------------------------
# Expected JA3 fingerprints (GREASE-stripped), measured 2026-10-13 with a
# local loopback TLS server that captured the raw ClientHello bytes
# (MSG_PEEK) of tlsmask sessions. Deterministic per tls-client release;
# recorded against tls-client v1.16.0 (bundled in tlsmask 1.16.0).
# Re-measure with the loopback harness if the bundled tls-client changes.
# ---------------------------------------------------------------------------
EXPECTED_JA3 = {
    "firefox_148": (
        "771,4865-4867-4866-49195-49199-52393-52392-49196-49200-49162-49161"
        "-49171-49172-51-57-156-157-47-53,"
        "23-65281-10-11-35-16-5-34-18-51-43-13-45-28-27-65037,"
        "4588-29-23-24-25-256-257,0"
    ),
    "chrome_150": (
        "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172"
        "-156-157-47-53,"
        "51-17613-65281-10-27-35-5-23-43-13-18-11-65037-16-45,"
        "4588-29-23-24,0"
    ),
}

#: Engine name (persona["engine"]) -> tlsmask client profile.
_ENGINE_PROFILES = {
    "camoufox": "firefox_148",
    "firefox": "firefox_148",
    "patchright": "chrome_150",
    "chromium": "chrome_150",
    "chrome": "chrome_150",
}
DEFAULT_PROFILE = "firefox_148"

#: JA3 echo services (first working one wins). Each returns JSON holding
#: the JA3 string the server observed for our connection.
_ECHO_URLS = (
    "https://tls.peet.ws/api/all",   # {"tls": {"ja3": "771,...", ...}}
    "https://ja3er.com/json",        # {"ja3": "771,...", "ja3_hash": ...}
)

#: DNS-over-HTTPS endpoints used by the leak check (first working wins).
_DOH_URLS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)
_DOH_HOSTS = ("cloudflare-dns.com", "dns.google")

#: RFC 8701 GREASE values — stripped before JA3 comparison because some
#: echo services strip them and some do not.
_GREASE = frozenset({
    0x0A0A, 0x1A1A, 0x2A2A, 0x3A3A, 0x4A4A, 0x5A5A, 0x6A6A, 0x7A7A,
    0x8A8A, 0x9A9A, 0xAAAA, 0xBABA, 0xCACA, 0xDADA, 0xEAEA, 0xFAFA,
})


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def profile_for_engine(engine) -> str:
    """Map a persona engine name to a tlsmask client profile.

    Camoufox is Firefox-based -> ``firefox_148``; Patchright drives
    Chromium -> ``chrome_150``. Unknown/None defaults to Firefox.
    """
    return _ENGINE_PROFILES.get((engine or "").strip().lower(),
                                DEFAULT_PROFILE)


def _strip_grease_ja3(ja3: str) -> str:
    """Remove GREASE code points from each comma-separated JA3 field."""
    if not isinstance(ja3, str) or "," not in ja3:
        return ja3
    out_fields = []
    for field in ja3.split(","):
        vals = []
        for token in field.split("-"):
            token = token.strip()
            try:
                if int(token) not in _GREASE:
                    vals.append(token)
            except (ValueError, TypeError):
                vals.append(token)  # keep unparseable tokens untouched
        out_fields.append("-".join(vals))
    return ",".join(out_fields)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _proxy_url(proxy: dict) -> str:
    """Build a tls-client proxy URL from a proxy-manager dict.

    ``proxy`` uses the :class:`src.proxy.manager.ProxyManager` shape::

        {'host', 'port', 'username', 'password', 'type'}

    with ``type`` in http/https/socks5/socks4. tls-client accepts
    http://, https://, socks5:// and socks5h:// — SOCKS4 has no
    equivalent and raises :exc:`ValueError` (never silently downgraded).

    A stored ``type`` of ``"https"`` means "HTTP proxy used for HTTPS
    targets" (the same meaning it has in
    :meth:`ProxyManager.check_endpoint`), so it maps to ``http://`` —
    an ``https://`` proxy URL would mean TLS *to the proxy itself*,
    which is not what the stored type means.
    """
    ptype = (proxy.get("type") or "http").lower()
    if ptype == "socks4":
        raise ValueError(
            "socks4 proxies are not supported by the tlsmask engine "
            "(tls-client accepts http/https/socks5 only); use socks5 or http"
        )
    scheme = {"http": "http", "https": "http", "socks5": "socks5"}.get(ptype)
    if scheme is None:
        raise ValueError(f"unknown proxy type {ptype!r}")
    host = proxy.get("host") or ""
    port = proxy.get("port") or 0
    if not host or not port:
        raise ValueError("proxy needs 'host' and 'port'")
    user = proxy.get("username") or ""
    password = proxy.get("password") or ""
    auth = ""
    if user:
        auth = "%s:%s@" % (quote(str(user), safe=""),
                           quote(str(password), safe=""))
    return f"{scheme}://{auth}{host}:{port}"


def _proxy_mapping(proxy: dict | None) -> dict | None:
    """tlsmask per-request ``proxy=`` mapping (or None for direct)."""
    if not proxy:
        return None
    url = _proxy_url(proxy)  # ValueError propagates: explicit, not silent
    return {"http": url, "https": url}


def _check_tlsmask() -> str | None:
    """Return an error string if tlsmask is unusable, else None."""
    if tlsmask is None:
        return f"tlsmask is not installed ({_TLSMASK_IMPORT_ERROR})"
    return None


@contextlib.contextmanager
def _dns_spy():
    """Record hostnames resolved via :func:`socket.getaddrinfo`.

    Only resolutions made by the *calling thread* are recorded; the
    original function is always restored. Other threads keep working
    normally during the window. Yields the list of recorded hostnames.
    """
    seen: list = []
    ident = threading.get_ident()
    real = socket.getaddrinfo

    def spy(host, port, *args, **kwargs):
        if threading.get_ident() == ident and isinstance(host, str):
            seen.append(host)
        return real(host, port, *args, **kwargs)

    socket.getaddrinfo = spy  # type: ignore[assignment]
    try:
        yield seen
    finally:
        socket.getaddrinfo = real  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def fetch_via_browser_tls(url: str, proxy: dict | None = None,
                          profile: str = DEFAULT_PROFILE,
                          timeout: int = 20, headers: dict | None = None,
                          method: str = "GET", verify: bool = True) -> dict:
    """Fetch ``url`` with a real browser's TLS + HTTP/2 fingerprint.

    Drop-in replacement for ``urllib``-based fetches (e.g. the proxy
    section's feed/health fetches) whose Python-TLS ClientHello is
    detectable on the wire. ``proxy`` is a proxy-manager dict
    (``{'host','port','username','password','type'}``); ``None`` goes
    direct. ``verify=False`` skips TLS certificate verification (only
    for local/test targets — never for production fetches).

    Returns ``{"ok", "status", "body", "error", "profile",
    "final_url"}``. ``body`` is bytes. Never raises.
    """
    result = {"ok": False, "status": None, "body": None, "error": None,
              "profile": profile, "final_url": None}
    err = _check_tlsmask()
    if err:
        result["error"] = err
        return result
    try:
        pmap = _proxy_mapping(proxy)
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    try:
        with tlsmask.Session(profile) as session:
            resp = session.execute_request(
                method.upper(), url, headers=headers, proxy=pmap,
                timeout_seconds=timeout, allow_redirects=True,
                insecure_skip_verify=not verify)
        result.update(ok=True, status=resp.status_code,
                      body=bytes(resp.content or b""),
                      final_url=resp.url)
    except Exception as exc:  # noqa: BLE001 - surfaced in result, never raised
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result


def _extract_ja3(data) -> str | None:
    """Pull the observed JA3 string out of an echo-service JSON payload."""
    if not isinstance(data, dict):
        return None
    for container in (data, data.get("tls")):
        if isinstance(container, dict):
            val = container.get("ja3")
            if isinstance(val, str) and "," in val:
                return val
    return None


def tls_profile_check(proxy: dict | None = None,
                      profile: str = DEFAULT_PROFILE,
                      timeout: int = 25) -> dict:
    """Verify the TLS handshake a path presents matches a browser profile.

    Performs a TLS handshake to a JA3 echo host — *through* ``proxy``
    when given (proxy-manager dict shape), direct otherwise — using the
    tlsmask ``profile`` (e.g. ``"firefox_148"``; see
    :func:`profile_for_engine` for engine mapping), and compares the
    server-observed JA3 against the recorded fingerprint for that
    profile.

    Returns the validator check dict ``{"check", "passed", "detail"}``
    plus ``profile``, ``observed_ja3``, ``expected_ja3`` and
    ``echo_url``. ``passed`` is False (fail-closed) when the echo
    service is unreachable, the proxy rejects the tunnel, or the
    profile is unknown — a check that cannot complete is not a pass.
    Never raises.
    """
    out = {"check": "tls-profile", "passed": False, "detail": "",
           "profile": profile, "observed_ja3": None,
           "expected_ja3": EXPECTED_JA3.get(profile), "echo_url": None}
    err = _check_tlsmask()
    if err:
        out["detail"] = f"{err}; cannot verify TLS profile"
        return out
    if out["expected_ja3"] is None:
        out["detail"] = (
            f"no recorded JA3 for profile {profile!r} "
            f"(known: {sorted(EXPECTED_JA3)}); cannot verify")
        return out
    expected = _strip_grease_ja3(out["expected_ja3"])
    per = max(5, timeout // max(1, len(_ECHO_URLS)))
    last_err = "no echo endpoint attempted"
    for echo_url in _ECHO_URLS:
        resp = fetch_via_browser_tls(
            echo_url, proxy=proxy, profile=profile, timeout=per,
            headers={"Accept": "application/json"})
        if not resp["ok"]:
            last_err = resp["error"] or "fetch failed"
            continue
        try:
            data = json.loads((resp["body"] or b"").decode("utf-8",
                                                           "replace"))
        except Exception as exc:
            last_err = f"bad JSON from {echo_url}: {exc}"
            continue
        ja3 = _extract_ja3(data)
        if not ja3:
            last_err = f"no JA3 field in response from {echo_url}"
            continue
        observed = _strip_grease_ja3(ja3)
        out["echo_url"] = echo_url
        out["observed_ja3"] = observed
        via = ("through proxy %s:%s" % (proxy.get("host"),
                                        proxy.get("port"))
               if proxy else "direct")
        if observed == expected:
            out["passed"] = True
            out["detail"] = (
                f"TLS handshake {via} matches the {profile} profile "
                f"(JA3 {observed[:56]}..., verified via {echo_url})")
        else:
            out["detail"] = (
                f"JA3 mismatch for {profile} {via}: observed "
                f"{observed[:64]}... expected {expected[:64]}... — the "
                f"path is not presenting the {profile} handshake (the "
                f"proxy may re-terminate TLS or the profile may be "
                f"outdated vs the bundled tls-client)")
        return out
    out["detail"] = f"TLS-profile check incomplete: {last_err}"[:300]
    return out


def _summarize_doh(payload) -> str:
    """One-line summary of a DNS-over-HTTPS JSON answer."""
    if not isinstance(payload, dict):
        return "no answer"
    status = payload.get("Status")
    answers = payload.get("Answer") or []
    ips = [a.get("data") for a in answers
           if isinstance(a, dict) and a.get("type") == 1 and a.get("data")]
    if status == 3 or (status == 0 and not answers):
        return "NXDOMAIN (canary correctly unresolvable)"
    return f"Status={status} A={ips[:3]}"


def dns_leak_check(proxy: dict | None = None, timeout: int = 25) -> dict:
    """Check whether DNS for proxied traffic leaks to the local resolver.

    Method: a unique canary hostname (``leak-<hex>.example.com``,
    unresolvable by design) is looked up via DNS-over-HTTPS *through*
    the proxy using browser TLS, while a thread-local spy records every
    hostname the calling thread resolves via :func:`socket.getaddrinfo`.
    The same canary is also resolved with the direct local resolver for
    comparison.

    Verdict:

    * no proxy -> ``passed=True`` (nothing to leak; local DNS by design)
    * SOCKS4 -> ``passed=False`` — the base SOCKS4 request carries only
      a 4-byte IP, no hostname field, so the client *must* resolve the
      target locally: DNS bypasses the proxy by protocol design
    * http/https/socks5 -> ``passed=True`` iff the DoH query completed
      through the proxy and neither the DoH endpoint nor the canary
      (nor any other non-proxy hostname) was resolved locally;
      otherwise ``passed=False`` with the leaked hostname named

    The check verifies the *client stack's* DNS path: for http/https
    the hostname travels inside CONNECT / the absolute URI, and for
    SOCKS5 tls-client sends the domain name (ATYP=0x03) for remote
    resolution — both verified empirically 2026-10-13 against local
    test proxies. A proxy that itself forwards DNS upstream oddly is
    outside what any client can observe.

    Returns the validator check dict ``{"check", "passed", "detail"}``
    plus ``canary``, ``doh``, ``direct`` and ``locally_resolved``.
    Never raises.
    """
    out = {"check": "dns-leak", "passed": False, "detail": "",
           "canary": None, "doh": None, "direct": None,
           "locally_resolved": []}
    if not proxy:
        out["passed"] = True
        out["detail"] = ("no proxy configured: DNS resolves via the local "
                         "resolver by design; nothing to leak")
        return out
    ptype = (proxy.get("type") or "http").lower()
    proxy_host = proxy.get("host") or ""
    proxy_port = proxy.get("port") or 0
    if ptype == "socks4":
        out["detail"] = (
            "SOCKS4 requests carry only a 4-byte destination IP — the "
            "base protocol has no hostname field — so the client must "
            "resolve every target locally: DNS bypasses the proxy "
            "(LEAK by protocol design). Use socks5 or http to keep DNS "
            "inside the tunnel.")
        return out
    if ptype not in ("http", "https", "socks5"):
        out["detail"] = f"unknown proxy type {ptype!r}; cannot assess DNS path"
        return out
    err = _check_tlsmask()
    if err:
        out["detail"] = f"{err}; cannot run DNS-leak check"
        return out

    canary = f"leak-{uuid.uuid4().hex[:12]}.example.com"
    out["canary"] = canary
    doh_payload = None
    doh_via = None
    per = max(5, timeout // max(1, len(_DOH_URLS)))
    with _dns_spy() as seen:
        for doh_base in _DOH_URLS:
            resp = fetch_via_browser_tls(
                f"{doh_base}?name={canary}&type=A", proxy=proxy,
                profile=DEFAULT_PROFILE, timeout=per,
                headers={"Accept": "application/dns-json"})
            if not resp["ok"]:
                continue
            try:
                doh_payload = json.loads(
                    (resp["body"] or b"").decode("utf-8", "replace"))
                doh_via = doh_base
                break
            except Exception:
                continue
        resolved = list(seen)
    out["locally_resolved"] = sorted(set(resolved))
    out["doh"] = _summarize_doh(doh_payload)
    if doh_via:
        out["doh_via"] = doh_via

    # Direct resolver comparison (expected: NXDOMAIN for the canary).
    try:
        socket.getaddrinfo(canary, 443)
        out["direct"] = "resolved (unexpected for an .example.com canary)"
    except Exception as exc:  # noqa: BLE001 - outcome recorded, not raised
        out["direct"] = f"{type(exc).__name__}: {exc}"[:120]

    if doh_payload is None:
        out["detail"] = (
            f"DNS-leak check incomplete: DoH query for {canary} through "
            f"proxy {proxy_host}:{proxy_port} ({ptype}) failed. Locally "
            f"resolved during the attempt: "
            f"{out['locally_resolved'] or 'none'}.")
        return out

    leaked = sorted({h for h in resolved
                     if h and h != proxy_host and not _is_ip_literal(h)})
    if leaked:
        out["detail"] = (
            f"LEAK: {', '.join(leaked)} was resolved via the local "
            f"resolver instead of through proxy "
            f"{proxy_host}:{proxy_port} ({ptype}) — DNS exits via the "
            f"local ISP, not the proxy.")
        return out
    out["passed"] = True
    out["detail"] = (
        f"no DNS leak: DoH for {canary} completed through proxy "
        f"{proxy_host}:{proxy_port} ({ptype}) via {doh_via} "
        f"({out['doh']}); no target hostname was resolved locally "
        f"(locally resolved: {out['locally_resolved'] or 'none'}); "
        f"direct resolver: {out['direct']}")
    return out
