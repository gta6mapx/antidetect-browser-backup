"""Profile warm-up: organic scenario execution + history seeding.

Scenario machinery adapted from nazak-browser-studio (MIT, see
``vendor/nazak-warmup/ATTRIBUTION.md``). The executor is sync and takes an
already-open Playwright page from our Camoufox launcher — no CDP.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from src.warmup.engine import (  # noqa: F401
    COOKIE_CONSENT_JS,
    ScenarioExecutor,
    ScenarioStep,
    WarmupScenario,
)
from src.warmup.history import seed_firefox_history  # noqa: F401
from src.warmup.runner import WarmupRunner  # noqa: F401
from src.warmup.scenarios import (  # noqa: F401
    BUILTIN_SCENARIOS,
    SCENARIO_ALIASES,
    get_scenario,
    list_scenarios,
)

__all__ = [
    "COOKIE_CONSENT_JS",
    "BUILTIN_SCENARIOS",
    "SCENARIO_ALIASES",
    "ScenarioExecutor",
    "ScenarioStep",
    "WarmupScenario",
    "WarmupRunner",
    "get_scenario",
    "list_scenarios",
    "seed_firefox_history",
]
