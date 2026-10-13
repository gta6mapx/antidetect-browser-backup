# CreepJS — attribution

Vendored build of the **CreepJS** client-side fingerprint / lie-detection test
page, used self-hosted by `src/fingerprint/creepjs_check.py`.

- Upstream: https://github.com/abrahamjuliot/creepjs
- License: **MIT License**, Copyright (c) 2021 abrahamjuliot
  (full text in `LICENSE` in this directory — required by the MIT license for
  all copies / substantial portions).
- Source commit built from: `10aa6724cd33a1015db1574211890518cd04f0cc`
  (upstream master, 2026-06-11).
- Build: `pnpm install && pnpm build` (rollup → `creep.js`, clean-css →
  `style.min.css`); release files taken from the `docs/` (GitHub Pages) build.
- Files kept: `index.html`, `creep.js`, `style.min.css`, `favicon.ico`
  (lean set — the `./tests/` sub-pages and the 1.9 MB crowdsourced
  `data/samples.json` machine-prediction database are intentionally omitted;
  lie detection works without them).

Trademark note: per the upstream `TRADEMARKS.md`, the MIT license covers the
*code*; the name "CreepJS" and logos are trademarked marks. This vendored copy
retains the name only to identify the upstream project it was built from and
does not claim to be the official CreepJS deployment.
