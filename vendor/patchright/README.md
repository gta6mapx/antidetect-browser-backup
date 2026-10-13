# Vendored patchright 1.63.0

Drop-in Playwright replacement with a patched driver (Apache-2.0; see
`ATTRIBUTION.md`). Used by the `patchright` engine in `src/engines/`.

## The Chromium browser binary is NOT vendored

Patchright ships the automation *driver* only. The Chromium build it
launches (~165 MB download, ~393 MB on disk) is installed separately and
is pinned per patchright release: **1.63.0 expects Chromium build 1243**
(see `driver/package/browsers.json`, entry `"name": "chromium"`).

### Installing the browser

From the repo root, with the repo venv active:

```bash
PYTHONPATH=vendor .venv/bin/python -m patchright install chromium
```

This downloads the pinned build into the standard Playwright browser
registry (`~/.cache/ms-playwright/`, or `$PLAYWRIGHT_BROWSERS_PATH` when
set) — the same location our `src/engines/patchright_engine.py` resolves
when it needs the installed Chromium version.

You can also reuse an existing install: if
`~/.cache/ms-playwright/chromium-1243/` (or the equivalent under
`$PLAYWRIGHT_BROWSERS_PATH`) already exists, no download is needed.

### Notes

- Never run `patchright install` with `--with-deps` on a machine you do
  not administer; on this project's server the plain `install chromium`
  form is enough.
- Do **not** `pip install patchright` into the repo venv: the vendored
  copy is picked up via `src/_vendor.py` (repo `vendor/` on `sys.path`),
  exactly like camoufox/browserforge. Runtime deps (`pyee`, `greenlet`)
  are already in the venv.
- Upgrading patchright: re-download the wheel, replace this directory's
  package files, and update `ATTRIBUTION.md` + the pinned Chromium build
  note above (check `driver/package/browsers.json` for the new revision).
