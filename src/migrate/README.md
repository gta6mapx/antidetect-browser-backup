# Phase 5 — Migration & Cookies

Import browser profiles from other anti-detect tools and move cookie
sessions between profiles. The field-matching helpers are vendored
(verbatim) from [persona-studio](https://github.com/anhnnp/persona-studio)
(MIT, Copyright 2025 Ahsan, commit `d46e984`) — see
`vendor/persona-import/ATTRIBUTION.md`. The mapping onto *our* stack
(`ProfileManager` / `ProxyManager` / Camoufox launch layer) is adapted.

## Profile import (`src/migrate`)

### Supported input shapes

The importer is deliberately *tolerant*: it looks a value up under every
name a field is known by (`_first` supports dotted paths like
`navigator.userAgent`) and fills gaps from our own coherent persona
generator. Supported aliases (from the persona-studio evaluation):

| Field | Accepted names |
|-------|----------------|
| Name | `name`, `title`, `profile_name` |
| OS | `os`, `os_type`, `osType`, `navigator.platform` (mac→macos, android→linux; inferred from UA when missing) |
| User agent | `navigator.userAgent`, `user_agent`, `ua`, `useragent`, `fingerprint.ua`, `fingerprint.userAgent` |
| Resolution | `navigator.resolution`, `screen_resolution`, `resolution`, `screen` (`"1920x1080"`) |
| Locale | `navigator.language`, `language`, `locale` |
| Timezone | `timezone.timezone`, `timezone`, `time_zone`, `fingerprint.timezone` |
| CPU cores | `navigator.hardwareConcurrency`, `hardware_concurrency`, `cores` |
| Memory | `navigator.deviceMemory`, `device_memory`, `memory` |
| WebGL | `webGLMetadata.vendor`/`renderer`, `webgl_vendor`/`webgl_renderer`, `fingerprint.webgl_vendor`/`webgl_renderer`, plus AdsPower's real `fingerprint_config.webgl_vendor`/`webgl_renderer` (added here — upstream's table missed it) |
| Proxy block | `proxy`, `proxyConfig`, `user_proxy_config` → host: `host`/`ip`/`proxy_host`/`addr`; port: `port`/`proxy_port`; user: `username`/`user`/`proxy_user`/`login`; pass: `password`/`pass`/`proxy_password`; mode: `mode`/`type`/`proxy_type`/`proxy_soft` (`"socks"` in mode → socks5, else http; `none`/`no_proxy`/`direct` → direct) |
| Tags/group | `tags` (list or comma string); `group_name`/`group`/`folder` → appended, and used as our `client_tag` |
| Notes | `notes`, `description` |

The export may be wrapped as: a bare list, `{"data"|"profiles"|"list"|
"items"|"rows": [...]}`, a single profile object, **or the real AdsPower
Local API nesting `{"data": {"list": [...]}}`** (`GET /api/v1/user/list` —
one level deeper than upstream handled; patched here).

### What gets mapped to what

* `ProfileManager.create(name, os, proxy_dict, client_tag=group_name, **overrides)`
  where `proxy_dict = {host, port, username, password, type}` and overrides
  are the export's own `user_agent`, `viewport`, `screen`, `timezone`,
  `locale`, `hardware_concurrency`, `device_memory`, `webgl_vendor`,
  `webgl_renderer`, `platform`, `notes`.
* Proxy also registered with `ProxyManager.add("import-<profile>", ...)`
  when it has a host (best-effort; duplicates/invalid entries are recorded
  as import notes, never fatal).
* `group_name`/`tags` → our `client_tag` (group = client). Export `notes`
  plus the tag list land in the persona's `notes` field.
* Duplicate profile name → **skipped** (not an error). Result shape:
  `{'created': int, 'skipped': [names], 'errors': [{'name','error'}]}`.

GoLogin (`navigator.userAgent`, `timezone.timezone`, `webGLMetadata`,
`proxy.mode/http`) and Multilogin (`os_type`, snake_case `navigator.*`)
shapes are handled best-effort through the same aliasing — `import_gologin`
documents that intent. Anything unmapped is filled from the generated base
and reported in the per-profile `issues` list (a starting point to review,
not a black box).

### Example usage

```python
from src.migrate import import_adspower, import_gologin, detect_source

# A dump of the AdsPower Local API, a plain list, a dict, or a JSON string:
result = import_adspower("adspower-export.json")
# {'created': 3, 'skipped': ['ADS-Store-7'], 'errors': []}

result = import_gologin("gologin-export.json")

# CLI (auto-detects source from the file):
#   python -m src.cli import-from export.json
#   python -m src.cli import-from export.json --source gologin

# GUI: dashboard → "Import" button → upload the JSON file.
```

## Cookies (`src/cookies`)

Format logic (`normalize`, `parse`, `parse_netscape`, `to_netscape`,
`dumps`, `read_file`) is vendored verbatim from persona-studio. Three
formats, auto-detected on import:

1. **Cookie-Editor / EditThisCookie extension JSON** — flat list
   (`expirationDate`, `sameSite: "no_restriction"`, …).
2. **Playwright storage state** — `{"cookies": [...], "origins": [...]}`.
3. **Netscape `cookies.txt`** — curl/wget/yt-dlp format.

Everything is normalised to Playwright's cookie shape on the way in
(session cookies → `expires=-1`; `SameSite=None` downgraded to `Lax` when
not `Secure`, because Chromium would reject it).

### Browser round-trip (adapted)

`export_cookies(profile_name, fmt='cookie-editor'|'netscape'|'playwright')`
launches the profile **headless** via `src.browser.launcher`, reads
`context.cookies()`, and writes to
`~/.antidetect-browser/cookie-exports/<name>.<ext>`. Returns the file path,
or `{'error': msg}` when the profile is unknown, the format is unknown, or
the Camoufox binary is missing (checked *before* launching — no multi-GB
download is ever triggered).

`import_cookies(profile_name, path, clear=False)` parses the file
(auto-detect), launches headless, optionally `clear_cookies()` first, then
`add_cookies()`. Returns `{'imported': n}` or `{'error': msg}`. Importing an
empty file is a no-op (no browser launch, `{'imported': 0}`).

Three browser-layer quirks, all verified against our Camoufox/Firefox build
and handled inside `src/cookies/manager.py` (shared `src/browser/launcher`
is untouched):

* **url-form writes** — this build silently drops *domain*-based
  `add_cookies` calls and rejects `url` combined with `domain`/`path`, so
  cookies are rewritten as `url`-only (path embedded in the URL).
* **400-day expiry cap** — Firefox rejects any cookie `expires` beyond
  ~400 days; larger values are clamped to `now + 34560000` s.
* **geoip retry** — cookie I/O never visits a site, so when a launch fails
  only because camoufox's optional `geoip` extra is missing (proxied
  profiles set `geoip=True`), the launch is retried with the proxy stripped
  from a persona *copy*. The cookie jar is identical either way.
* **Session cookies** (`expires <= 0`) do not survive a browser restart —
  standard browser semantics, not an import bug.

```python
from src.cookies.manager import export_cookies, import_cookies

path = export_cookies("client-acme", fmt="netscape")   # -> str path
res = import_cookies("client-acme", "cookies.txt", clear=True)  # {'imported': 12}

# CLI:
#   python -m src.cli cookies client-acme export --format netscape
#   python -m src.cli cookies client-acme import cookies.txt --clear

# GUI: profile row → "Cookies" button → export/import in the modal.
```

Note: cookie read/write goes through the browser rather than the profile's
data directory, because cookie values are only portably readable from the
live cookie store — a headless launch is required for both directions.
