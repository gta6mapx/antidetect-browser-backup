"""Built-in warm-up scenarios (Phase 6).

Scenario *content* adapted from nazak-browser-studio ``nazak/core/warmup_engine.py``
(MIT; see ``vendor/nazak-warmup/ATTRIBUTION.md``): same 4 scenarios, same step
actions. Differences vs upstream:

* URLs live in each step's ``params`` and can be overridden per step
  (e.g. re-pointed at ``http://localhost:...`` in tests) — the engine only
  ever navigates to what the step says.
* ``google_search`` steps use ``search_url_template`` (default Google) so
  tests can swap the search backend without touching scenario content.

CLI/GUI-facing aliases: ``youtube``, ``ecommerce``, ``crypto``, ``finance``.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from src.warmup.engine import ScenarioStep, WarmupScenario

BUILTIN_SCENARIOS: list[WarmupScenario] = [
    WarmupScenario(
        id="scen_ecom_trust",
        name="E-Commerce & Google Ads Trust Booster",
        description=(
            "Organic Google searches, product page visits, and natural dwell "
            "times to maximize cookie trust score."
        ),
        niche="ecommerce",
        steps=[
            ScenarioStep(
                "google_search",
                {"query": "best noise cancelling headphones 2026 review"},
                "Search Google for top retail electronics",
            ),
            ScenarioStep(
                "human_scroll",
                {"duration_sec": 4, "direction": "down"},
                "Natural scroll through SERP results",
            ),
            ScenarioStep(
                "dwell",
                {"min_sec": 3, "max_sec": 7},
                "Simulate reading organic search results",
            ),
            ScenarioStep(
                "open_url",
                {"url": "https://www.amazon.com/s?k=wireless+headphones"},
                "Visit Amazon product catalog",
            ),
            ScenarioStep(
                "human_scroll",
                {"duration_sec": 6, "direction": "down"},
                "Browse product listings",
            ),
            ScenarioStep("accept_cookie_dialog", {}, "Accept cookie consent dialog"),
            ScenarioStep(
                "dwell",
                {"min_sec": 5, "max_sec": 10},
                "Dwell on marketplace page",
            ),
        ],
    ),
    WarmupScenario(
        id="scen_youtube_viewer",
        name="YouTube & Shorts Audience Warmup",
        description=(
            "Search YouTube, watch video previews, scroll recommendations to "
            "build a real viewer footprint."
        ),
        niche="tech",
        steps=[
            ScenarioStep(
                "open_url",
                {"url": "https://www.youtube.com"},
                "Navigate to YouTube homepage",
            ),
            ScenarioStep(
                "human_scroll",
                {"duration_sec": 5, "direction": "down"},
                "Scroll YouTube homepage recommendations",
            ),
            ScenarioStep(
                "google_search",
                {"query": "site:youtube.com tech review 2026"},
                "Search top tech review videos",
            ),
            ScenarioStep(
                "watch_youtube",
                {"watch_seconds": 15, "topic": "technology"},
                "Watch video session with natural pauses",
            ),
            ScenarioStep(
                "dwell",
                {"min_sec": 4, "max_sec": 8},
                "Finish session and persist cookies",
            ),
        ],
    ),
    WarmupScenario(
        id="scen_crypto_web3",
        name="Crypto & Web3 Investor Farming",
        description=(
            "Search DeFi protocols, market prices on CoinMarketCap, and tech "
            "whitepapers."
        ),
        niche="crypto",
        steps=[
            ScenarioStep(
                "open_url",
                {"url": "https://coinmarketcap.com"},
                "Open CoinMarketCap crypto rankings",
            ),
            ScenarioStep(
                "human_scroll",
                {"duration_sec": 8, "direction": "down"},
                "Inspect top 100 cryptocurrencies table",
            ),
            ScenarioStep(
                "google_search",
                {"query": "bitcoin halving historical price cycle 2026"},
                "Search deep crypto analysis",
            ),
            ScenarioStep(
                "dwell",
                {"min_sec": 6, "max_sec": 12},
                "Read analytics article",
            ),
        ],
    ),
    WarmupScenario(
        id="scen_finance_banking",
        name="Finance & High-CPC Banking Footprint",
        description=(
            "Accumulate highest Tier-1 advertising cookies in banking, credit, "
            "and ETF investments."
        ),
        niche="finance",
        steps=[
            ScenarioStep(
                "google_search",
                {"query": "best high yield savings accounts rates 2026"},
                "Google search for banking rates",
            ),
            ScenarioStep(
                "human_scroll",
                {"duration_sec": 5, "direction": "down"},
                "Scroll organic financial comparisons",
            ),
            ScenarioStep(
                "open_url",
                {"url": "https://www.investopedia.com"},
                "Read Investopedia financial guides",
            ),
            ScenarioStep(
                "dwell",
                {"min_sec": 8, "max_sec": 15},
                "Accumulate high-CPC finance tracking cookies",
            ),
        ],
    ),
]

# Short aliases used by the CLI and GUI picker -> scenario id.
SCENARIO_ALIASES: dict[str, str] = {
    "youtube": "scen_youtube_viewer",
    "ecommerce": "scen_ecom_trust",
    "crypto": "scen_crypto_web3",
    "finance": "scen_finance_banking",
}

_SCENARIO_BY_ID = {s.id: s for s in BUILTIN_SCENARIOS}


def list_scenarios() -> list[dict]:
    """Return id/name/description for every built-in scenario (GUI list)."""
    return [
        {"id": s.id, "name": s.name, "description": s.description, "niche": s.niche}
        for s in BUILTIN_SCENARIOS
    ]


def get_scenario(alias_or_id: str) -> WarmupScenario:
    """Resolve a CLI/GUI alias (``youtube``/``ecommerce``/``crypto``/``finance``)
    or a scenario id to its :class:`WarmupScenario`.

    Raises:
        KeyError: If the alias/id is unknown.
    """
    key = str(alias_or_id).strip().lower()
    scenario_id = SCENARIO_ALIASES.get(key, key)
    try:
        return _SCENARIO_BY_ID[scenario_id]
    except KeyError:
        raise KeyError(
            "unknown scenario %r; expected one of %s" % (alias_or_id, sorted(SCENARIO_ALIASES))
        ) from None
