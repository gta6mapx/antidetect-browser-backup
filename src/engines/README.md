# Browser engines

This project supports two browser engines. A profile's ``engine`` field
(``"camoufox"`` default, or ``"patchright"``) selects which one launches it.
Dispatch with :func:`src.engines.get_engine` — never hard-code an engine.

## The two engines

| | Camoufox (`"camoufox"`) | Patchright (`"patchright"`) |
|---|---|---|
| Browser family | Firefox | Chromium |
| What it hides | Automation tells **and** spoofs fingerprints per persona (fpgen: canvas/WebGL/audio/fonts/TLS) | Automation tells at the **protocol/driver layer only** (`navigator.webdriver=false`, no `cdc_` keys, no `--enable-automation`, Console API disabled by design) |
| Fingerprint spoofing | Yes — per-persona generated fingerprints | **No** — stock Chromium signals; our persona layer keeps them *internally consistent* (UA ↔ platform ↔ OS ↔ timezone ↔ locale), which is the honest v1 posture |
| UA handling | fpgen builds the whole identity | We **always** inject the persona UA (the default headless UA leaks a `HeadlessChrome` token) |
| `navigator.platform` | Set natively by camoufox | **Not a context option** — we redefine it via an init script on every page (`platform_spoof_script`) |
| Persistent profiles | Yes (`~/.antidetect-browser/profiles/<name>/`) | Yes (same layout) |
| Proxy support | camoufox `proxy` kwarg + `block_webrtc` | Playwright context `proxy` option |
| License | MPL-2.0 (vendored) | Apache-2.0 (vendored, `vendor/patchright/`) |

## When to use which

- **Camoufox** is the default and the stronger anti-detect posture today:
  per-persona fingerprint spoofing plus automation-tell hiding. Use it
  unless you have a reason not to.
- **Patchright** exists for Chromium coverage: some targets behave
  differently (or only) on Chromium, and a second engine family removes
  our single-engine risk (Camoufox is Firefox-only). Its fingerprints are
  *honest* — real stock-Chromium values, kept coherent by our persona
  generator + consistency validator. Many anti-bot systems prefer
  internally consistent real signals over noisy spoofing, but Patchright
  will never look like "a different GPU per profile" the way Camoufox can.

## What Patchright is NOT

- Not a Chromium fork and not a fingerprint spoofer. If you need
  per-profile canvas/WebGL/audio spoofing on Chromium, that is a known
  gap (not on the roadmap for v1).
- Not a debugging console: patchright **disables the page Console API
  entirely** by design. Use return values / DOM reads for observability,
  not `console.log`.

## Engine-aware persona generation & validation

- `src.fingerprints.generator.generate_persona(..., engine="chromium")`
  builds Chrome UAs whose major matches the *installed* Patchright
  Chromium (mirroring the Firefox UA auto-match), with platform values
  (`Win32` / `MacIntel` / `Linux x86_64`) consistent with the OS.
- `src.fingerprints.validator.validate(profile)` dispatches on
  `profile["engine"]`: the 23 Firefox checks for camoufox profiles, a
  Chromium-appropriate subset (Chrome UA + installed-major match,
  platform↔OS, generic webdriver-false) for patchright profiles.

## Chromium binary

The browser binary is **not** vendored (~393 MB). Install once:

```bash
PYTHONPATH=vendor .venv/bin/python -m patchright install chromium
```

Patchright 1.63.0 pins Chromium build **1243**. `installed_chromium_version()`
in `patchright_engine.py` reads the exact build from the vendored driver's
`browsers.json` pin and reports `(major, full)` from the binary's own
`--version` output — never downloads, never raises.
