# GUI — dashboard frontend

Vanilla JS dashboard (no build step, no CDN, works offline at `http://127.0.0.1:8765`).
Backend serves these files at `/` and the JSON API under `/api/`.

## Decision: faithful vanilla-JS port, NOT a vendored React build

Evaluated **persona-studio** (`https://github.com/anhnnp/persona-studio`,
commit `d46e984cfbdcd7278063e793e6ab1ad95b8cdc78`, MIT licensed,
Copyright (c) 2025 Ahsan) in `/tmp/persona-studio`.

**Why port instead of vendor:** its dashboard is a single 1882-line React app
(`dashboard/src/App.jsx`, Vite + React 18 + lucide-react) that is hard-wired to
its own engine API — id-keyed profiles and many endpoints our backend does not
have and will never have (per the backend spec): `/api/profiles/health`,
`/api/profiles/{id}/trust`, `/tls`, `/cookies`, `/proxy-test`, `/warmup`,
`/align`, `/api/profiles/batch`, `/api/profiles/import`, `/api/proxies/assign`,
`/api/schedules`, `/api/engines`, `/api/config`. Adapting it would have meant a
Vite/npm build plus an effective rewrite of `App.jsx` to drop trust/warmup/
engines/schedules/cookie features and remap `id` → `name` and every
request/response shape. The port keeps the same visual language and behavior
mapped exactly to our endpoints, stays dependency-free and readable for the
backend worker, and works offline with zero config.

Nothing was copied verbatim — this is a fresh implementation that **adapts the
design language** of persona-studio:

- "Ink violet" dark theme tokens (`#0B0B10` bg, `#8B7CFF` violet accent,
  `#4FE0B0` mint, `#F2B84B` amber, `#FF6B7A` red)
- Left sidebar: brand mark → nav → tag/folder filter list with counts →
  connection-status footer (online = pulsing mint dot, offline = red)
- Main: stat cards row (Profiles / Running / Proxies) + primary/ghost action
  buttons, search toolbar, sticky-header table, status dots, pill/tag chips,
  centered modals, health score view with consistency checklist and detection
  cards, slide-in toasts

Attribution per MIT license requirements:

- Repo: https://github.com/anhnnp/persona-studio
- Commit evaluated: `d46e984cfbdcd7278063e793e6ab1ad95b8cdc78`
- License: MIT — full text in the repo's `LICENSE` file; Copyright (c) 2025 Ahsan

## Files

- `static/index.html` — layout: sidebar (brand, Profiles nav, tag filters,
  connection footer), main (stats row, toolbar with search, table),
  three modals (New Profile, Bulk Import, Health), toast container.
  References `style.css` and `app.js` with relative paths.
- `static/style.css` — the ink-violet theme, table, modals, toasts, responsive
  breakpoints. Zero external resources.
- `static/app.js` — API client + state + rendering. No frameworks.

## API endpoint mapping (all relative to the serving origin)

| UI action | Endpoint | Shape |
|---|---|---|
| Load table + sidebar tags + stats | `GET /api/profiles` | `{profiles:[{name,os,client_tag,proxy_label,status,last_used}]}` |
| Create profile | `POST /api/profiles` | `{name, os, proxy_name?, client_tag?}` → 201 |
| Launch / Stop toggle | `POST /api/profiles/{name}/launch` → `{status:"starting"}`; `POST /api/profiles/{name}/stop` → `{status:"stopped"}` | name is `encodeURIComponent`'d |
| Delete (with `confirm()`) | `DELETE /api/profiles/{name}` → `{deleted:true}` | 404/409 show the server's `{"error"}` message |
| Health modal | `GET /api/profiles/{name}/health` | `{score, consistency:[{check,passed,detail}], detection:{site:{verdict,…}}}` |
| Proxy dropdowns | `GET /api/proxies` | `{proxies:[{name,host,port,type}]}` |
| Bulk import | `POST /api/bulk/import` (multipart `file` + optional `client_tag`) | `{created, skipped, errors}` rendered in the modal |

Error handling: any non-2xx or `{"error": msg}` response raises `ApiError` and
surfaces a red toast with the message. Auto-refresh every 5s re-renders the
table/tags/stats from `state` without touching the search box or selected tag;
`Escape` closes modals; the Launch button is disabled while status is
`starting`.
