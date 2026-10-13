"""Proxy storage and reachability testing.

Storage choice
--------------
Proxies live in their OWN SQLite file: ``~/.antidetect-browser/proxies.db``
(table ``proxies``), separate from the profiles database
(``~/.antidetect-browser/profiles.db``). Rationale: proxy credentials are a
separate concern from profile fingerprints; a dedicated file lets a user back
up, wipe, or restrict permissions on proxy credentials independently, and
avoids any schema lock-in between the two workers. Both databases live under
the same ``~/.antidetect-browser/`` home directory.

Security note
-------------
Proxy AUTH passwords are encrypted at rest with the master Fernet key
(``~/.antidetect-browser/.master.key``, see :mod:`src.security.crypto`).
:meth:`ProxyManager.add` stores a password as ``"enc:" + encrypt_str(...)``
unless the value already carries the ``"enc:"`` marker; :meth:`get` and
:meth:`list` decrypt it (marker stripped) before returning, so callers
always see plaintext. Rows written before this change (plaintext, no
marker) are returned as-is — legacy compatibility, not a guarantee going
forward. Rotating the master key orphans all ``"enc:"`` passwords.

The store is meant for a single-user local machine; do not copy the file
to shared hosts.

The :meth:`ProxyManager.test` method only checks TCP reachability of the
proxy host:port with a 5-second timeout. It does NOT perform a proxy-protocol
handshake, so a passing test means the endpoint accepts TCP connections, not
that it is a working proxy.
"""

import os
import socket
import sqlite3
import threading

import src._vendor  # noqa: F401  (first import: keep stdlib + venv only after this)

from src import paths as _paths

#: Default proxies DB path, resolved at import time for backward
#: compatibility. ``ProxyManager()`` with no argument resolves the path at
#: call time instead, so a later ``ANTIDETECT_HOME`` change is honored.
DB_PATH = _paths.proxies_db()

_PROXY_TYPES = {"http", "https", "socks5", "socks4"}

#: Hourly verified proxy feed published by the proxy-scraper project
#: (MIT, https://github.com/maximilianfeix/proxy-scraper) — the same data
#: that ``proxyscraper.live_proxies()`` reads. Used as a fallback when the
#: library's own HTTP client cannot reach the network (e.g. behind a
#: proxy-tunneling MITM gateway, whose raw-asyncio client it does not
#: honor). stdlib ``urllib`` honors ``https_proxy`` env vars there.
_WARMUP_FEED_URL = (
    "https://maximilianfeix.github.io/proxy-scraper/proxies.json"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proxies (
    name     TEXT PRIMARY KEY,
    host     TEXT NOT NULL,
    port     INTEGER NOT NULL,
    username TEXT,
    password TEXT,
    ptype    TEXT NOT NULL DEFAULT 'http'
)
"""

_COLUMNS = ("name", "host", "port", "username", "password", "ptype")


class ProxyManager:
    """SQLite-backed store for named proxy definitions.

    Proxy definitions are plain dicts::

        {'name': str, 'host': str, 'port': int,
         'username': str | None, 'password': str | None, 'type': str}

    where ``type`` is one of ``http``, ``https``, ``socks5``, ``socks4``.
    """

    def __init__(self, db_path: str = None):
        """Open (creating if needed) the proxies database.

        Args:
            db_path: Path to the SQLite file. Defaults to
                ``src.paths.proxies_db()`` (honors ``ANTIDETECT_HOME``);
                tests may pass a temporary path instead.
        """
        self._db_path = db_path or _paths.proxies_db()
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)),
                    exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    # -- internals ------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        # Passwords come back decrypted: values stored as "enc:<token>"
        # are decrypted with the master key; legacy plaintext rows (no
        # marker) are returned as-is. InvalidToken propagates for
        # tampered / wrong-key data — that is a real failure, not noise.
        password = row["password"]
        if password is not None:
            from src.security.crypto import decrypt_str, is_encrypted

            if is_encrypted(password):
                password = decrypt_str(password[len("enc:"):])
        return {
            "name": row["name"],
            "host": row["host"],
            "port": row["port"],
            "username": row["username"],
            "password": password,
            "type": row["ptype"],
        }

    # -- public API -----------------------------------------------------
    def add(self, name, host, port, username=None, password=None,
            ptype="http") -> dict:
        """Add a named proxy definition and return it as a dict.

        Args:
            name: Unique label for the proxy.
            host: Proxy hostname or IP.
            port: Proxy port, must be 1..65535.
            username: Optional proxy-auth username.
            password: Optional proxy-auth password.
            ptype: One of ``http``, ``https``, ``socks5``, ``socks4``.

        Returns:
            ``{'name','host','port','username','password','type'}``.
            ``password`` is returned in plaintext (decrypted if stored
            encrypted).

        Raises:
            ValueError: If ``name`` already exists, ``port`` is out of
                range, or ``ptype`` is unknown.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("proxy name must be a non-empty string")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"port must be an integer in 1..65535, got {port!r}")
        if ptype not in _PROXY_TYPES:
            raise ValueError(
                f"ptype must be one of {sorted(_PROXY_TYPES)}, got {ptype!r}"
            )
        name = name.strip()
        if password is not None:
            # Encrypt auth passwords at rest (lazy import: crypto lives in
            # another package and must not be loaded at module import).
            from src.security.crypto import encrypt_str, is_encrypted

            if not is_encrypted(password):
                password = "enc:" + encrypt_str(password)
        with self._lock, self._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM proxies WHERE name = ?", (name,)
            ).fetchone():
                raise ValueError(f"proxy '{name}' already exists")
            conn.execute(
                "INSERT INTO proxies (name, host, port, username, password, ptype)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, host, port, username, password, ptype),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM proxies WHERE name = ?", (name,)
            ).fetchone()
        return self._row_to_dict(row)

    def get(self, name) -> dict:
        """Return the proxy definition for ``name``.

        ``password`` comes back in plaintext (decrypted if stored
        encrypted).

        Raises:
            KeyError: If no proxy with ``name`` exists.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proxies WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no proxy named '{name}'")
        return self._row_to_dict(row)

    def list(self) -> list:
        """Return all stored proxy definitions, ordered by name.

        ``password`` values come back in plaintext (decrypted if stored
        encrypted).
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM proxies ORDER BY name").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete(self, name) -> bool:
        """Delete the proxy named ``name``.

        Returns:
            True if a row was deleted, False if ``name`` did not exist.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM proxies WHERE name = ?", (name,))
            conn.commit()
        return cur.rowcount > 0

    def test(self, name) -> bool:
        """TCP-connect to the proxy's (host, port) with a 5s timeout.

        This checks TCP reachability ONLY — it performs no proxy-protocol
        handshake, so ``True`` means the endpoint accepts connections, not
        that it is a working proxy.

        Returns:
            True if the TCP connection succeeded, False on any error.
            Never raises.
        """
        result = self.test_with_latency(name)
        return result["ok"]

    @staticmethod
    def parse_proxy_line(line: str) -> dict | None:
        """Parse one proxy line into {host, port, type}.

        Accepts: ``host:port``, ``type://host:port``,
        ``host:port:user:pass``, ``type://user:pass@host:port``.
        Returns None for blank/invalid lines.
        """
        line = (line or "").strip()
        if not line or line.startswith("#"):
            return None
        ptype = "http"
        rest = line
        if "://" in line:
            scheme, rest = line.split("://", 1)
            scheme = scheme.lower()
            if scheme in ("http", "https"):
                ptype = "http"
            elif scheme in ("socks4", "socks4a"):
                ptype = "socks4"
            elif scheme in ("socks5", "socks5h"):
                ptype = "socks5"
            else:
                return None
        auth = ""
        if "@" in rest:
            auth, rest = rest.rsplit("@", 1)
        parts = rest.split(":")
        if len(parts) == 4:  # host:port:user:pass
            host, port_s, user, pw = parts
            auth = f"{user}:{pw}"
        elif len(parts) == 2:
            host, port_s = parts
        else:
            return None
        host = host.strip()
        if not host or not port_s.strip().isdigit():
            return None
        port = int(port_s.strip())
        if not 1 <= port <= 65535:
            return None
        d = {"host": host, "port": port, "type": ptype}
        if auth:
            d["auth"] = auth
        return d

    def bulk_check_proxies(self, lines: list, timeout: int = 10,
                           workers: int = 10) -> list:
        """Check many proxy lines concurrently.

        Returns [{host, port, type, ok, latency_ms, exit_ip, error}].
        Invalid lines are reported with ok=False and an error.
        """
        import concurrent.futures
        parsed = []
        for ln in lines or []:
            p = self.parse_proxy_line(ln)
            if p is None:
                parsed.append({"line": (ln or "").strip(), "ok": False,
                               "latency_ms": None, "exit_ip": None,
                               "error": "invalid format (use host:port)"})
            else:
                parsed.append(p)
        valid = [p for p in parsed if "host" in p]

        def _one(p):
            r = self.check_endpoint(p["host"], p["port"], p["type"],
                                    timeout=timeout,
                                    auth=p.get("auth", ""))
            return {**p, **r}

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, min(workers, 20))) as ex:
            results = list(ex.map(_one, valid))
        out, it = [], iter(results)
        for p in parsed:
            out.append(next(it) if "host" in p else p)
        return out

    def check_endpoint(self, host: str, port: int, ptype: str = "http",
                       timeout: int = 10, auth: str = "",
                       check_host: str = "httpbin.org",
                       check_port: int = 80) -> dict:
        """Health-check an arbitrary proxy endpoint (not in the store).

        Used by the Proxy Scraper page to test scraped proxies before
        saving. Both HTTP and SOCKS4/5 go through raw sockets (no urllib
        proxy dispatch — urllib silently bypassed the proxy on some
        machines, producing "TCP ok, HTTP failed: getaddrinfo" on every
        proxy). SOCKS uses PySocks for the handshake.

        If the configured type fails at the protocol level (e.g. the
        proxy RSTs an HTTP request because it is really SOCKS), the other
        types are tried automatically and the working one is reported as
        ``detected_type``.

        Returns {"ok", "latency_ms", "exit_ip", "error",
        "detected_type"}. Never raises.
        """
        import time
        import socket
        import json as _json
        import base64
        ptype = (ptype or "http").lower()
        start = time.monotonic()

        def _tcp_ok():
            try:
                s = socket.create_connection((host, port), timeout=5)
                s.close()
                return True
            except Exception:
                return False

        def _try_http():
            """Return (exit_ip, error, head)."""
            s = socket.create_connection((host, port), timeout=timeout)
            s.settimeout(timeout)
            headers = [
                f"GET http://{check_host}:{check_port}/ip HTTP/1.0",
                f"Host: {check_host}",
                "User-Agent: Mozilla/5.0",
                "Connection: close",
            ]
            if auth:
                token = base64.b64encode(auth.encode()).decode()
                headers.append(f"Proxy-Authorization: Basic {token}")
            s.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
            head, body = self._read_http_body(s, timeout)
            s.close()
            try:
                exit_ip = _json.loads(body.decode()).get("origin", "")
            except Exception:
                status = head.split(b"\r\n", 1)[0].decode("latin1")[:60]
                preview = body[:80].decode("latin1", "replace").strip()
                return None, f"{status} | {preview}", head
            if not exit_ip:
                return None, "empty exit IP in proxy reply", head
            return exit_ip, None, head

        def _try_socks(stype):
            """Return (exit_ip, error, head)."""
            try:
                import socks as _socks
            except ImportError:
                return None, "PySocks not installed", b""
            user, pwd = (auth.split(":", 1) + [""])[:2] if auth else ("", "")
            s = _socks.socksocket()
            s.set_proxy(_socks.SOCKS5 if stype == "socks5" else _socks.SOCKS4,
                        host, port, username=user or None,
                        password=pwd or None)
            s.settimeout(timeout)
            s.connect((check_host, check_port))
            s.sendall(b"GET /ip HTTP/1.0\r\nHost: " + check_host.encode() + b"\r\n"
                      b"User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n")
            _head, body = self._read_http_body(s, timeout)
            s.close()
            exit_ip = _json.loads(body.decode()).get("origin", "")
            if not exit_ip:
                return None, "empty exit IP in proxy reply", _head
            return exit_ip, None, _head

        def _try(t):
            if "socks" in t:
                return _try_socks("socks5" if "socks5" in t else "socks4")
            return _try_http()

        def _finish(exit_ip, err, detected=None):
            latency = int((time.monotonic() - start) * 1000)
            d = {"detected_type": detected}
            if err is None:
                return {"ok": True, "latency_ms": latency,
                        "exit_ip": exit_ip, "error": None, **d}
            if _tcp_ok():
                return {"ok": True, "latency_ms": latency, "exit_ip": None,
                        "error": "TCP ok, HTTP failed: %s" % str(err)[:120],
                        **d}
            return {"ok": False, "latency_ms": None, "exit_ip": None,
                    "error": str(err)[:200], **d}

        # Primary attempt with the configured type.
        try:
            exit_ip, err, _ = _try(ptype)
        except Exception as exc:
            exit_ip, err = None, exc
        if err is None:
            return _finish(exit_ip, None)

        # Protocol-level failure but TCP works -> maybe wrong type.
        # Auto-detect: try the other protocols before giving up.
        primary_err = err
        if _tcp_ok():
            for alt in ("http", "socks5", "socks4"):
                if alt == ptype or ("socks" in ptype and "socks" in alt):
                    continue
                try:
                    exit_ip2, err2, _ = _try(alt)
                except Exception:
                    continue
                if err2 is None:
                    return _finish(
                        exit_ip2, None, detected=alt)
        return _finish(None, primary_err)

    @staticmethod
    def _read_http_body(s, timeout: int) -> tuple:
        """Read an HTTP/1.0 response off a proxied socket.

        Returns (head, body). Uses Content-Length / chunked framing so we
        never wait for EOF on servers that keep the connection open.
        """
        import socket as _socket
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = s.recv(4096)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 65536:
                break
        head, _, body = raw.partition(b"\r\n\r\n")
        clen, chunked = None, False
        for line in head.split(b"\r\n")[1:]:
            ln = line.lower()
            if ln.startswith(b"content-length:"):
                try:
                    clen = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    pass
            elif ln.startswith(b"transfer-encoding:") and b"chunked" in ln:
                chunked = True
        if chunked:
            while b"\r\n0\r\n\r\n" not in body and b"\n0\r\n\r\n" not in body:
                chunk = s.recv(4096)
                if not chunk:
                    break
                body += chunk
                if len(body) > 65536:
                    break
        elif isinstance(clen, int):
            while len(body) < clen:
                chunk = s.recv(4096)
                if not chunk:
                    break
                body += chunk
        else:
            s.settimeout(3)  # no framing: brief drain, don't hang
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    body += chunk
                    if len(body) > 65536:
                        break
            except _socket.timeout:
                pass
            finally:
                s.settimeout(timeout)
        return head, body

    def test_with_latency(self, name) -> dict:
        """Test a proxy with real HTTP request through it.

        Verifies: TCP connect, auth, protocol, and exit IP.
        Returns:
            ``{"ok": bool, "latency_ms": int|None, "exit_ip": str|None,
               "error": str|None}``. Never raises.
        """
        import time
        try:
            proxy = self.get(name)
        except (KeyError, Exception):
            return {"ok": False, "latency_ms": None, "exit_ip": None,
                    "error": "no proxy named '%s'" % name}

        host = proxy.get("host", "")
        port = proxy.get("port", 0)
        username = proxy.get("username", "")
        password = proxy.get("password", "")
        ptype = (proxy.get("ptype") or "http").lower()

        # Delegate to check_endpoint — the single correct implementation
        # (PySocks for SOCKS4/5, urllib for HTTP). The old inline urllib
        # code here could never speak SOCKS, so every SOCKS proxy showed
        # "TCP ok, HTTP failed" in the Proxies section.
        auth = f"{username}:{password}" if username else ""
        return self.check_endpoint(host, port, ptype, timeout=10, auth=auth)

    def update(self, name, **fields) -> dict:
        """Update a proxy's fields (host, port, username, password, ptype).

        Raises:
            KeyError: If no proxy with ``name`` exists.
            ValueError: On invalid port or ptype.
        """
        allowed = {"host", "port", "username", "password", "ptype"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "port" in updates:
            port = updates["port"]
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError(
                    f"port must be an integer in 1..65535, got {port!r}")
        if "ptype" in updates and updates["ptype"] not in _PROXY_TYPES:
            raise ValueError(
                f"ptype must be one of {sorted(_PROXY_TYPES)}, "
                f"got {updates['ptype']!r}")
        if "password" in updates and updates["password"] is not None:
            from src.security.crypto import encrypt_str, is_encrypted
            pw = updates["password"]
            if not is_encrypted(pw):
                updates["password"] = "enc:" + encrypt_str(pw)
        if not updates:
            return self.get(name)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE proxies SET {set_clause} WHERE name = ?",
                (*updates.values(), name),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise KeyError(f"no proxy named '{name}'")
        return self.get(name)

    # -- Worker D: sticky proxy pool (additive) -----------------------
    @property
    def sticky_pool(self):
        """Sticky proxy pool bound to this manager's database.

        One pinned proxy per profile until it dies, with health
        tracking, exponential-backoff benching, and ban-code detection.
        See :mod:`src.proxy.sticky`. The pool is created on demand;
        nothing about the store changes by accessing this property.
        """
        from src.proxy.sticky import StickyPool
        return StickyPool(db_path=self._db_path, manager=self)

    # -- Worker B: proxy geo-sync (additive) --------------------------
    def assign_and_sync(self, profile_name, proxy_name):
        """Assign a proxy to a profile and geo-sync the profile's persona.

        Lazily imports :class:`ProfileManager` to avoid import cycles.

        Steps:
            1. Fetch the proxy dict via :meth:`get`.
            2. Attach it to the profile via ``pm.update(profile_name,
               proxy=proxy)``.
            3. Run :meth:`GeoSync.sync_persona_to_ip` on the updated persona.
            4. Persist any geo changes (timezone/locale/geolocation).

        Args:
            profile_name: name of an existing profile.
            proxy_name: name of an existing proxy.

        Returns:
            ``{'profile', 'proxy', 'persona', 'note'}`` where ``persona``
            is the synced persona dict and ``note`` is the geo-sync status
            string from :meth:`GeoSync.sync_persona_to_ip`.

        Raises:
            KeyError: If no proxy named ``proxy_name`` or no profile named
                ``profile_name`` exists. These are caller errors, so they
                propagate (unlike best-effort network failures).
        """
        from src.profiles.manager import ProfileManager
        from src.proxy.geosync import GeoSync

        proxy = self.get(proxy_name)  # KeyError on unknown proxy
        pm = ProfileManager()
        persona = pm.update(profile_name, proxy=proxy)  # KeyError unknown profile
        synced, note = GeoSync.sync_persona_to_ip(persona)
        pm.update(
            profile_name,
            timezone=synced.get("timezone"),
            locale=synced.get("locale"),
            geolocation=synced.get("geolocation"),
        )
        return {
            "profile": profile_name,
            "proxy": proxy_name,
            "persona": synced,
            "note": note,
        }

    def fetch_warmup_proxies(self, limit: int = 20, types=None,
                             countries=None,
                             anonymity: str = "", google_only: bool = False,
                             no_datacenter: bool = False,
                             max_latency: int = 0) -> list:
        """Fetch verified free proxies for the warm-up / testing tier.

        STANDING RULE (project policy): free proxies are NEVER used for
        real Gmail sessions or any profile holding a real login. This
        feed is for warm-up runs, connection-shape testing, and
        throwaway browsing only.

        This method only RETURNS data — it never assigns a proxy to any
        profile and never writes to the proxy store. The caller decides
        what to do with the result (print it, log it, run a warm-up
        scenario through it).

        Source: proxy-scraper (MIT, maximilianfeix/proxy-scraper) —
        prefers the ``proxyscraper.live_proxies()`` Python API (the
        hourly verified list); if the library's own HTTP client cannot
        reach the network, falls back to downloading the same published
        feed with stdlib ``urllib`` (which honors ``https_proxy`` env).

        Args:
            limit: Maximum number of proxies to return (0 = no limit).
                Must be non-negative.
            types: e.g. ["http", "socks5"]. None = all.
            countries: e.g. ["US", "DE"]. None = all.
            anonymity: "elite" | "anonymous" | "transparent" | "" (any).
            google_only: only proxies verified to reach google.com.
            no_datacenter: exclude datacenter exits.
            max_latency: max ms (0 = no cap).

        Returns:
            List of dicts, best first::

                {'host': str, 'port': int, 'type': 'http'|'socks5'|'socks4',
                 'country': str, 'latency_ms': int, 'anonymity': str,
                 'https_capable': bool|None, 'google_ok': bool|None}

        Raises:
            ValueError: If ``limit`` is negative.
            ImportError: If ``proxy-scraper-cli`` is not installed
                (``pip install proxy-scraper-cli``).
            ConnectionError: If neither the library API nor the feed
                fallback could reach the network.
        """
        if not isinstance(limit, int) or limit < 0:
            raise ValueError(f"limit must be a non-negative int, got {limit!r}")
        filters = {"types": types, "countries": countries,
                   "anonymity": anonymity, "google_only": google_only,
                   "no_datacenter": no_datacenter, "max_latency": max_latency}
        rows = self._warmup_live_rows(limit=limit, filters=filters)
        return [self._warmup_row_to_dict(r) for r in rows]

    def warmup_feed_stats(self) -> dict:
        """Totals for the hourly feed (Scraper page header)."""
        import json
        import urllib.request
        from collections import Counter
        try:
            req = urllib.request.Request(
                _WARMUP_FEED_URL, headers={"User-Agent": "antidetect-browser/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ConnectionError(f"feed unreachable: {exc}")
        google_n = sum(1 for x in payload if (x.get("sites") or {}).get("google"))
        countries = sorted({x.get("country") for x in payload if x.get("country")})
        return {"total": len(payload),
                "types": dict(Counter(x.get("ptype") for x in payload)),
                "anonymity": dict(Counter(x.get("anonymity") for x in payload)),
                "google_verified": google_n, "countries": countries}

    # -- warm-up feed internals -----------------------------------------
    @staticmethod
    def _warmup_row_to_dict(row: dict) -> dict:
        """Normalize one feed row to the warm-up proxy dict format.

        ``row`` is ``{'ptype', 'proxy' ('host:port'), 'country',
        'latency', 'https', 'anonymity'}``.
        """
        host, _, port_str = str(row.get("proxy", "")).rpartition(":")
        if not host or not port_str.isdigit():
            raise ValueError(f"bad proxy entry: {row.get('proxy')!r}")
        ptype = str(row.get("ptype", "http")).lower()
        if ptype not in _PROXY_TYPES:
            ptype = "http"
        return {
            "host": host,
            "port": int(port_str),
            "type": ptype,
            "country": str(row.get("country") or ""),
            "latency_ms": int(row.get("latency") or 0),
            "anonymity": str(row.get("anonymity") or ""),
            "https_capable": row.get("https"),
            "google_ok": row.get("google_ok"),
        }

    @staticmethod
    def _warmup_match(row: dict, filters: dict) -> bool:
        """Manual filter for the urllib fallback path."""
        f = filters or {}
        types = f.get("types")
        if types and str(row.get("ptype", "")).lower() not in types:
            return False
        countries = f.get("countries")
        if countries:
            want = {str(c).upper() for c in countries}
            if str(row.get("country", "")).upper() not in want:
                return False
        anon = (f.get("anonymity") or "").lower()
        if anon and str(row.get("anonymity", "")).lower() != anon:
            return False
        if f.get("google_only") and not (row.get("sites") or {}).get("google"):
            return False
        if f.get("no_datacenter") and row.get("datacenter"):
            return False
        ml = f.get("max_latency") or 0
        try:
            if ml and int(row.get("latency") or 0) > ml:
                return False
        except (TypeError, ValueError):
            pass
        return True

    def _warmup_live_rows(self, limit: int, filters=None) -> list:
        """Return normalized feed rows via the proxy-scraper API.

        Tries ``proxyscraper.live_proxies()`` first; on network failure
        falls back to the same published hourly feed over stdlib
        ``urllib``. Raises ImportError / ConnectionError as documented.
        """
        filters = filters or {}

        # Fast path first: the same hourly feed over stdlib urllib with a
        # hard 30s timeout. This supports all filters via _warmup_match
        # and never hangs the dashboard on slow networks.
        try:
            return self._warmup_fallback_rows(limit, filters)
        except ConnectionError:
            pass  # fall through to the library path below

        # Slow path: the proxy-scraper library's own HTTP client, run in a
        # worker thread so a stall can never hang the dashboard.
        import concurrent.futures
        try:
            from proxyscraper import live_proxies
        except ImportError as exc:
            raise ImportError(
                "proxy-scraper-cli is not installed; "
                "run: pip install proxy-scraper-cli"
            ) from exc

        kwargs: dict = {}
        if filters.get("types"):
            kwargs["types"] = filters["types"]
        if filters.get("countries"):
            kwargs["countries"] = [c.upper() for c in filters["countries"]]
        if filters.get("anonymity"):
            kwargs["anonymity"] = filters["anonymity"]
        if filters.get("google_only"):
            kwargs["works_on"] = ["google"]
        if filters.get("no_datacenter"):
            kwargs["no_datacenter"] = True
        if filters.get("max_latency"):
            kwargs["max_latency"] = filters["max_latency"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(live_proxies, limit or 0, **kwargs)
            try:
                proxies = fut.result(timeout=45)
            except concurrent.futures.TimeoutError:
                raise ConnectionError(
                    "proxy-scraper library timed out after 45s; "
                    "check your network and retry")
        if not proxies:
            raise ConnectionError("no proxies returned by the feed")
        return [
            {
                "ptype": p.ptype,
                "proxy": p.proxy,
                "country": p.country,
                "latency": p.latency,
                "https": p.https,
                "anonymity": p.anonymity,
                "google_ok": (getattr(p, "sites", None) or {}).get("google")
                             if hasattr(p, "sites") else None,
            }
            for p in proxies
        ]

    def _warmup_fallback_rows(self, limit: int, filters: dict) -> list:
        """Same hourly verified feed, fetched with urllib (30s timeout).

        Honors the ``https_proxy``/``HTTPS_PROXY`` environment and applies
        all filters manually via ``_warmup_match``.
        """
        import json
        import urllib.request

        try:
            req = urllib.request.Request(
                _WARMUP_FEED_URL, headers={"User-Agent": "antidetect-browser/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ConnectionError(
                "warm-up proxy feed unreachable: "
                f"{exc}"
            ) from exc

        rows = []
        for row in payload:
            if not self._warmup_match(row, filters):
                continue
            try:
                d = self._warmup_row_to_dict(
                    {
                        "ptype": row.get("ptype"),
                        "proxy": row.get("proxy"),
                        "country": row.get("country"),
                        "latency": row.get("latency"),
                        "https": row.get("https"),
                        "anonymity": row.get("anonymity"),
                        "google_ok": (row.get("sites") or {}).get("google"),
                    }
                )
            except ValueError:
                continue  # skip malformed rows
            rows.append(d)
            if limit and len(rows) >= limit:
                break
        # Feed is published fastest-first; keep that order.
        return [
            {
                "ptype": d["type"],
                "proxy": f"{d['host']}:{d['port']}",
                "country": d["country"],
                "latency": d["latency_ms"],
                "https": d["https_capable"],
                "anonymity": d["anonymity"],
                "google_ok": d["google_ok"],
            }
            for d in rows
        ]
