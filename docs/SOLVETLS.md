# solvetls — self-hosted TLS/HTTP fingerprinting server

**Upstream:** https://github.com/solvefixed/solvetls — **MIT License** (verified: LICENSE file in the repo root, Copyright (c) 2026 solvefixed).
**Status on this machine:** documented, NOT deployed — this machine has no Docker, and solvetls is designed around a Docker Compose deployment. See "Deployment note" below.

## What it does

solvetls is a small self-hosted server (Python 3.12–3.13, asyncio) whose whole
job is to tell each connecting client what its network fingerprint looks like.
Every request gets a JSON response describing:

- **JA3 hash** of the client's TLS ClientHello
- **JA4** fingerprint of the TLS handshake
- **Akamai HTTP/2 fingerprint** (and its hash) — HTTP/2 SETTINGS frame values, header order, pseudo-headers
- **TLS extensions** with raw hex data plus decoded values where supported (e.g. supported groups / curves)
- Negotiated TLS version and cipher suites, captured HTTP/2 frames (raw hex), request headers with order and casing preserved
- IP/TCP details (on Linux with kernel support; socket addresses only otherwise)

Try it against the upstream's public demo (`tls.solvedev.to`) for a quick look,
but real profile testing should run against your own instance so fingerprints
don't leave your infrastructure. (The demo is fine for casual sanity checks.)

## Why it matters for this project

A browser profile can have a perfect Firefox user-agent and perfect JS
spoofing and still get flagged, because the **TLS layer tells a different
story**: if the ClientHello looks like Chromium's while the navigator claims
to be Firefox, that mismatch is a classic fingerprinting signal and a common
root cause of Google "unusual traffic" challenges.

solvetls gives each profile an independent, ground-truth readout of its TLS
and HTTP/2 fingerprint so we can:

1. **Catch mismatches per profile** — compare what the profile *claims* (UA,
   engine) against what its TLS layer *actually* sends.
2. **Support the TLS/JA3 matching goal** — use the reported JA3/JA4 to verify
   that Camoufox-launched profiles fingerprint as Firefox and Chromium-engine
   profiles fingerprint as Chromium.
3. Feed results into the profile health/trust-score system (`src/fingerprint/`
   and the consistency validator).

Companion code: `src/fingerprint/tls_check.py` automates the check — launches
headless Chromium via Playwright, navigates to a solvetls-compatible URL, and
returns the JA3/JA4 fields as a dict. It works against any solvetls-compatible
endpoint (URL is a parameter).

## Docker Compose setup (from the upstream README)

Prerequisites: a TLS certificate and private key.

1. Generate a self-signed cert for local testing:
   ```
   mkdir -p certs
   openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
     -keyout certs/privkey.pem -out certs/fullchain.pem \
     -subj "/CN=localhost" \
     -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
   ```
   For a public server, use a trusted certificate for your domain (e.g. from Let's Encrypt).

2. Run it:
   ```
   docker compose up -d
   ```

What Compose does (from the upstream `docker-compose.yml`): builds the image
from the repo, maps container ports 80→80 and 443→443, sets
`SOLVETLS_LOG_LEVEL=INFO`, and mounts `./certs` read-only into the container.

Configuration is via environment variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `SOLVETLS_HOST` | `0.0.0.0` | bind address |
| `SOLVETLS_PORT` | `443` | HTTPS port |
| `SOLVETLS_HTTP_PORT` | `80` | HTTP→HTTPS redirect port; empty disables HTTP |
| `SOLVETLS_CERT` | `certs/fullchain.pem` | certificate chain |
| `SOLVETLS_KEY` | `certs/privkey.pem` | private key |
| `SOLVETLS_MONGODB_URL` | *(unset)* | MongoDB URL (with DB name) to persist reports in a `fingerprints` collection |
| `SOLVETLS_LOG_LEVEL` | `INFO` | `DEBUG` adds negotiated cipher + ALPN per connection |

Docker specifics: set `SOLVETLS_*` values in `docker-compose.yml` under
`environment` (shell variables and Compose `.env` files are not forwarded
automatically); use container paths for certificates and adjust `ports` /
volumes to match.

Alternative (no Docker): `python3.12 -m venv .venv && . .venv/bin/activate`,
`pip install -r requirements.txt`, then `python main.py` (needs permission to
bind ports 80/443; for local tests use
`SOLVETLS_PORT=8443 SOLVETLS_HTTP_PORT=8080 python main.py` and
`curl -k https://localhost:8443/`). Plain HTTP requests are redirected to
HTTPS (308).

### Known limitations (upstream)

- Connect directly or use TLS passthrough: a reverse proxy that terminates TLS
  hides the client's real fingerprint. (With passthrough, `ip`/`tcpip` may
  describe the proxy instead.)
- One fingerprint report per connection; the server closes each connection after responding.
- Capacity: up to 32 clients per instance, 4 reports at once; excess connections
  are closed and report-less requests get an empty 503.
- Detailed TCP/IP capture requires Linux kernel support; IPv6 extension headers
  and TCP options are not decoded.

## How to point a profile at it

Once solvetls is running (hosted where your profiles run — see Deployment note):

1. Launch the profile normally (Camoufox or Chromium engine).
2. Navigate to `https://<solvetls-host>/` — the page *is* the report.
3. Read the JSON: `tls.ja3_hash`, `tls.ja4`, `http2.akamai_fingerprint` /
   `http2.akamai_fingerprint_hash`. Compare against the fingerprint expected for
   the profile's engine and persona.
4. Automated: use `check_tls(url)` from `src/fingerprint/tls_check.py`
   (or the CLI: `python src/fingerprint/tls_check.py https://<host>/`),
   which launches headless Chromium via Playwright and returns the parsed fields.

## Deployment note (this machine)

**solvetls is documented here, not deployed here.** Docker is not available on
this server, so no `docker compose up` was run. Recommended route: run solvetls
on Husnain's Windows PC (or any Docker-capable host on the same network as the
profiles — e.g. via Docker Desktop + LAN/Tailscale), then point profiles at
`https://<that-host>/` for per-profile TLS verification. TCP/IP detail
fidelity is best on Linux, but JA3/JA4/Akamai fingerprints (the fields that
matter for profile matching) do not depend on it.

## License

MIT — https://github.com/solvefixed/solvetls/blob/main/LICENSE
