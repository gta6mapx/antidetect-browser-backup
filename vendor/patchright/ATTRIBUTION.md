# Attribution — patchright

- **Package:** patchright
- **Version:** 1.63.0 (wheel `patchright-1.63.0-py3-none-manylinux1_x86_64.whl`)
- **License:** Apache License 2.0 — see `LICENSE` in this directory
  (copied verbatim from the wheel's `licenses/LICENSE`; upstream
  `METADATA` declares `License-Expression: Apache-2.0`).
- **Upstream:** https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python
  (Python port of https://github.com/Kaliiiiiiiiii-Vinyzu/patchright,
  a patched Playwright; Playwright source:
  https://github.com/microsoft/playwright-python/tree/v1.63.0)
- **Vendored on:** 2026-10-08 — extracted from the official PyPI wheel,
  unmodified. The `METADATA` file in this directory is the wheel's own
  metadata, kept for version/license provenance.

## What it is

Patchright is a drop-in replacement for the Playwright Python library whose
*driver* (not the browser) is patched at the protocol level to remove
automation tells (avoids `Runtime.enable`, disables the Console API,
removes `--enable-automation`, sets
`--disable-blink-features=AutomationControlled`, no `cdc_` variables).
It is **not** a Chromium fork: it launches stock Chromium builds.

## What we changed

Nothing. The package is used exactly as shipped upstream. All of our
engine-specific behavior (persona UA injection, `navigator.platform`
spoofing, proxy wiring, persistent profile dirs) lives in our own
`src/engines/` code, not in this copy.
