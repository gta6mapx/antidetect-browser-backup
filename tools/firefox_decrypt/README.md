# firefox_decrypt (vendored, GPL-3.0 — ISOLATED)

Vendored from: https://github.com/unode/firefox_decrypt
Upstream license: **GNU General Public License v3.0** (see `LICENSE` in this directory).
Vendored: 2026-10-12 (single-file snapshot of `firefox_decrypt.py`).

## Isolation rules (mandatory)

1. **Separate process only.** This code is NEVER imported by anything under
   `src/` or the FastAPI app. It is executed only as a standalone script via
   `subprocess` (see `pc/decrypt_logins.py`, which calls
   `python tools/firefox_decrypt/firefox_decrypt.py ...`).
2. **Do not move `firefox_decrypt.py` into `src/`.** Keep the GPL-3.0 code in
   this directory, with its `LICENSE` file alongside it.
3. **Do not `import firefox_decrypt`** from any project module — import would
   create a combined work and pull GPL obligations into the rest of the
   codebase. Subprocess execution keeps the boundary clean.
4. Wrapper scripts in `pc/` may only pass CLI arguments and read stdout/stderr;
   they must not import or inline any code from `firefox_decrypt.py`.

## What it does

Decrypts saved logins from a Firefox profile directory
(`key4.db` + `logins.json`) and prints them in the requested format.
Used to recover credentials stored by Firefox inside our own anti-detect
profiles.

## Upstream update

To refresh: re-clone the upstream repo, verify the LICENSE file header is
still GPL-3.0, and replace `firefox_decrypt.py` here.
