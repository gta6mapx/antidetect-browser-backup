# Vendored open-source components

These are downloaded copies of open-source packages used by this project.
They are vendored (not pip-installed) so the project is self-contained.

| Package | Version | License | Upstream |
|---|---|---|---|
| camoufox | 0.5.8 | MPL-2.0 | https://github.com/daijro/camoufox |
| patchright | 1.63.0 | Apache-2.0 | https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python (patched Playwright driver; launches stock Chromium — the browser binary itself is NOT vendored, see `patchright/README.md`) |
| browserforge | (see METADATA) | MIT | https://github.com/daijro/browserforge |
| fpgen | 1.3.0 | (see METADATA) | https://github.com/daijro/fpgen (fingerprint ML model; model data downloads on first run via `ensure_fpgen_model()`) |
| rpaforge | (vendored 2026-10-09) | Apache-2.0 | https://github.com/chelslava/rpaforge — visual RPA studio, WebUI Playwright library, smart selectors. See `rpaforge/LICENSE`. Integrated via `src/rpa/rpaforge.py` + `src/rpa/bridge.py`. |
| rpa-studio | d00f179 | Apache-2.0 | https://github.com/anandhu1228/python_rpa_ui — recipe-driven browser automation. See `rpa-studio/ATTRIBUTION.md`. Adapted in `src/rpa/engine.py`. |
| nazak-sync (reference copy only, not executed) | de06b50 | MIT | https://github.com/wwewtech/nazak-browser-studio — `nazak/core/synchronizer.py` verbatim copy; see `nazak-sync/ATTRIBUTION.md`. Our `src/sync/` is a clean-room rewrite of its design adapted to our Playwright page objects. |
| nazak-warmup | (see ATTRIBUTION) | MIT | https://github.com/wwewtech/nazak-browser-studio — warm-up scenarios. |
| persona-import | (see ATTRIBUTION) | MIT | AdsPower/GoLogin profile import. |

## Updating a vendored package

```bash
pip download <pkg>==<version> --no-deps -d /tmp/vendor_dl
unzip -o /tmp/vendor_dl/<pkg>-<version>-*.whl -d /tmp/vendor_dl/x
rm -rf vendor/<pkg> && cp -r /tmp/vendor_dl/x/<pkg> vendor/<pkg>
```

Then update the version table above.

## Browser binary

The Camoufox *browser binary* (~200MB) is NOT vendored in git (too large).
Run `python setup_browser.py` once — it downloads the official release
binary via the vendored package's fetch routine.
