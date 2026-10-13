# Warm-up scenarios (Phase 6)

Profile warm-up executes **scenarios** — ordered lists of organic actions —
inside a real profile browser so profiles accumulate history, cookies, and
realistic behavior before being used on client accounts.

## Scenario format

A scenario is a list of steps with JSON round-trip (`to_dict` / `from_dict`),
wire-compatible with upstream nazak-browser-studio:

```json
{
  "id": "scen_youtube_viewer",
  "name": "YouTube & Shorts Audience Warmup",
  "description": "...",
  "niche": "tech",
  "steps": [
    {"action": "open_url", "params": {"url": "https://www.youtube.com"}, "description": "Navigate to YouTube homepage"},
    {"action": "human_scroll", "params": {"duration_sec": 5, "direction": "down"}, "description": "..."},
    {"action": "google_search", "params": {"query": "site:youtube.com tech review 2026"}, "description": "..."},
    {"action": "watch_youtube", "params": {"watch_seconds": 15}, "description": "..."},
    {"action": "dwell", "params": {"min_sec": 4, "max_sec": 8}, "description": "..."}
  ]
}
```

A JSON file in this shape can be loaded with
`WarmupScenario.from_dict(json.load(open(path)))` and executed directly.

## Step actions

| Action | Params | Behavior |
|---|---|---|
| `open_url` | `url` | Sanitized navigation (http/https/about only) + post-navigation settle. |
| `google_search` | `query`, `search_url_template` (default Google `https://www.google.com/search?q={q}&hl=en`) | Builds the search URL from the template — re-point the template at localhost in tests. |
| `human_scroll` | `duration_sec` (clamped 0.5–600), `direction` (`down`/`up`) | Repeated calls to `src.behavior.human.human_scroll` until the duration budget is spent, with occasional reader micro-pauses. |
| `dwell` | `min_sec`, `max_sec` (both clamped 0.5–600) | Sleeps a random time in range. |
| `watch_youtube` | `watch_seconds` (clamped ≤ 120), optional `url` | Navigates to `url` if given, then sleeps the watch time. |
| `accept_cookie_dialog` | — | Runs the shared consent-clicker JS (selector list + accept/agree text match); harmless when no dialog is present. |

Unknown actions return `False`; step failures are recorded per-step, never
raised.

## Aliases

The CLI and GUI use short aliases → scenario id:

| Alias | Scenario |
|---|---|
| `youtube` | `scen_youtube_viewer` — YouTube & Shorts Audience Warmup |
| `ecommerce` | `scen_ecom_trust` — E-Commerce & Google Ads Trust Booster |
| `crypto` | `scen_crypto_web3` — Crypto & Web3 Investor Farming |
| `finance` | `scen_finance_banking` — Finance & High-CPC Banking Footprint |

## URL configurability

Built-in scenarios ship with real default URLs, but every navigation target
comes from the step's `params` — override `url` (for `open_url` /
`watch_youtube`) or `search_url_template` (for `google_search`) to re-point a
scenario at a local test server. The engine never hardcodes external sites.

## Usage

```python
from src.warmup import WarmupRunner
result = WarmupRunner().run("my-profile", scenario="youtube", headless=True)
print(result)
# {'profile': 'my-profile', 'scenario': 'scen_youtube_viewer', ...,
#  'steps_executed': 5, 'steps_ok': 5, 'duration_sec': 61.2,
#  'success': True, 'errors': []}
```

CLI: `python -m src.cli warmup my-profile --scenario youtube --headless`

API: `POST /api/warmup/run` with `{"profile_name": "...", "scenario": "youtube"}` (runs in a background thread).

History seeding (offline, no browser): `seed_profile_history("my-profile")`
writes a Firefox `places.sqlite` with randomized visits over the past 1–14
days into the profile's persistent dir.

## License attribution

Scenario machinery adapted from **nazak-browser-studio** (MIT, © 2026 Nazak
Browser Studio Contributors). Verbatim upstream files live in
`vendor/nazak-warmup/` with `ATTRIBUTION.md`; `src/warmup/engine.py` and
`src/warmup/history.py` were adapted for Camoufox/Firefox (sync page
injection, Firefox `places.sqlite` schema).
