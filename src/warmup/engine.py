"""Sync warm-up scenario executor (Phase 6).

Scenario machinery adapted from nazak-browser-studio ``nazak/core/warmup_engine.py``
(MIT; see ``vendor/nazak-warmup/ATTRIBUTION.md``) for our stack:

* The dataclasses ``ScenarioStep`` / ``WarmupScenario`` (incl. their JSON
  round-trip shape) are kept wire-compatible with upstream, so a JSON
  scenario file accepted by upstream ``--scenario-file`` loads here too.
* Page acquisition changed: upstream attaches over CDP to Chromium
  (``chromium.connect_over_cdp``). We take an **already-open sync
  Playwright page** from ``src.browser.launcher.launch_profile`` — no CDP,
  no ``browser_launcher`` coupling.
* Sync (not asyncio): our Camoufox launcher is sync; the executor uses the
  same sync Playwright page.
* Scroll steps delegate to ``src.behavior.human.human_scroll`` instead of
  duplicating the scroll logic.
* Navigation targets are sanitized (http/https/about only); ``google_search``
  builds its URL from a ``search_url_template`` param (default Google) so
  tests can re-point it at a local server.

Step actions: ``open_url``, ``google_search``, ``human_scroll``, ``dwell``,
``watch_youtube``, ``accept_cookie_dialog``. Unknown actions return False.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from src.behavior.human import human_scroll, new_rng

logger = logging.getLogger(__name__)

# Consent-button clicker shared by the cookie step (adapted from upstream
# nazak-browser-studio, MIT). Runs harmlessly when no dialog is present.
COOKIE_CONSENT_JS = """
() => {
  const selectors = [
    '#onetrust-accept-btn-handler',
    '#sp-cc-accept',
    'button#L2AGLb',
    'button[jsname="b3VHJd"]',
    'button[aria-label*="accept" i]',
    'button[aria-label*="agree" i]',
    '[data-testid="cookie-accept"]',
    '[data-cy="cookieAccept"]'
  ];
  for (const s of selectors) {
    const el = document.querySelector(s);
    if (el) { el.click(); return 'selector:' + s; }
  }
  const words = ['accept all', 'accept cookies', 'accept', 'i agree', 'agree', 'allow all', 'got it'];
  const candidates = document.querySelectorAll('button, a[role="button"], div[role="button"]');
  for (const b of Array.from(candidates)) {
    const t = (b.textContent || '').trim().toLowerCase();
    if (t && t.length < 40 && words.some((w) => t === w || t.startsWith(w))) {
      b.click();
      return 'text:' + t;
    }
  }
  return '';
}
"""

_DEFAULT_SEARCH_URL_TEMPLATE = "https://www.google.com/search?q={q}&hl=en"

_ALLOWED_SCHEMES = {"http", "https", "about"}

_MAX_STEP_SECONDS = 600.0


def _clamped_seconds(value: Any, default: float, *, lo: float = 0.5, hi: float = _MAX_STEP_SECONDS) -> float:
    """Clamp a step duration from JSON/API params to sane finite bounds.

    Audit-hardened (upstream R3): ``min_sec``/``duration_sec`` arrived
    unvalidated, and a scenario with ``min_sec=1e12`` held its slot forever.
    NaN/inf/non-numeric values fall back to ``default``.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(default)
    if num != num or num in (float("inf"), float("-inf")):  # NaN/inf
        num = float(default)
    return max(lo, min(hi, num))


def sanitize_url(url: str) -> str:
    """Allow only http(s) and about: navigation targets.

    Raises:
        ValueError: If the URL uses any other scheme (e.g. file:,
            javascript:, data:) — these are never valid warm-up targets.
    """
    scheme = urlparse(str(url)).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"refused navigation target with scheme {scheme!r}: {url!r}")
    return str(url)


@dataclass
class ScenarioStep:
    """One warm-up action with parameters. JSON shape is upstream-compatible."""

    action: str  # "open_url", "google_search", "human_scroll", "dwell", "watch_youtube", "accept_cookie_dialog"
    params: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        """Serialize to the upstream JSON shape."""
        return {"action": self.action, "params": self.params, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioStep":
        """Deserialize from the upstream JSON shape."""
        return cls(
            action=data.get("action", "open_url"),
            params=data.get("params", {}),
            description=data.get("description", ""),
        )


@dataclass
class WarmupScenario:
    """An ordered list of steps. JSON shape is upstream-compatible."""

    id: str = field(default_factory=lambda: f"scen_{uuid.uuid4().hex[:8]}")
    name: str = "Custom Scenario"
    description: str = ""
    niche: str = "ecommerce"
    steps: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to the upstream JSON shape."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "niche": self.niche,
            "steps": [s.to_dict() for s in self.steps],
            "total_steps": len(self.steps),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WarmupScenario":
        """Deserialize from the upstream JSON shape."""
        steps_raw = data.get("steps", [])
        steps = [ScenarioStep.from_dict(s) if isinstance(s, dict) else s for s in steps_raw]
        return cls(
            id=data.get("id", f"scen_{uuid.uuid4().hex[:8]}"),
            name=data.get("name", "Custom Scenario"),
            description=data.get("description", ""),
            niche=data.get("niche", "ecommerce"),
            steps=steps,
        )


class ScenarioExecutor:
    """Executes warm-up scenarios on an already-open Playwright page.

    Unlike upstream, this executor launches nothing and attaches to nothing:
    the caller (``src.warmup.runner.WarmupRunner``) opens the page via
    ``launch_profile`` and hands it in.
    """

    def __init__(self, seed: int | None = None):
        """Create an executor.

        Args:
            seed: Optional seed for reproducible step timing in tests.
        """
        self._rng = random.Random(seed)

    # ------------------------------------------------------------ public API
    def execute_step(self, page: Any, step: ScenarioStep) -> bool:
        """Execute a single scenario step on ``page``.

        Args:
            page: Sync Playwright ``Page`` (or a test double with the same
                surface used by the step).
            step: The step to execute.

        Returns:
            True on success, False on failure (including unknown actions).
        """
        if step.action in ("open_url", "google_search"):
            return self._navigate_step(page, step)
        if step.action == "human_scroll":
            return self._scroll_step(page, step)
        if step.action == "dwell":
            return self._dwell_step(step)
        if step.action == "watch_youtube":
            return self._watch_step(page, step)
        if step.action == "accept_cookie_dialog":
            return self._cookie_step(page)
        logger.warning("warmup: unknown action %r", step.action)
        return False

    def execute(
        self,
        page: Any,
        scenario: WarmupScenario,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict:
        """Run every step of ``scenario`` on ``page`` sequentially.

        Args:
            page: Sync Playwright ``Page``.
            scenario: Scenario to execute.
            progress_callback: Optional ``(idx, total, label)`` reporter.

        Returns:
            Dict with ``scenario_id``, ``scenario_name``, ``total_steps``,
            ``completed_steps``, ``results`` (per-step dicts) and ``success``.
            Never raises for step failures — they are recorded.
        """
        results: list[dict] = []
        for idx, step in enumerate(scenario.steps, start=1):
            if progress_callback is not None:
                try:
                    progress_callback(idx, len(scenario.steps), step.description or step.action)
                except Exception:
                    pass
            ok = self.execute_step(page, step)
            results.append({"step": idx, "action": step.action, "success": ok})
            time.sleep(0.5)
        return {
            "scenario_id": scenario.id,
            "scenario_name": scenario.name,
            "total_steps": len(scenario.steps),
            "completed_steps": len(results),
            "results": results,
            "success": all(r["success"] for r in results),
        }

    # ------------------------------------------------------------ step logic
    def _navigate_step(self, page: Any, step: ScenarioStep) -> bool:
        if page is None:
            return False
        if step.action == "open_url":
            url = step.params.get("url", "https://www.google.com")
        else:  # google_search — URL template is configurable for tests
            query = str(step.params.get("query", "tech news 2026"))
            template = str(step.params.get("search_url_template", _DEFAULT_SEARCH_URL_TEMPLATE))
            url = template.replace("{q}", quote_plus(query))
        return self._navigate(page, url)

    def _navigate(self, page: Any, url: str) -> bool:
        try:
            safe_url = sanitize_url(url)
        except ValueError as exc:
            logger.warning("warmup: refused navigation target: %s", exc)
            return False
        try:
            page.goto(safe_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(self._rng.uniform(0.6, 1.6))  # post-navigation settle
            return True
        except Exception as exc:
            logger.warning("warmup navigation failed for %s: %s", url, exc)
            return False

    def _scroll_step(self, page: Any, step: ScenarioStep) -> bool:
        if page is None:
            return False
        duration = _clamped_seconds(step.params.get("duration_sec", 3), 3.0)
        direction = str(step.params.get("direction", "down")).lower()
        sign = -1 if direction == "up" else 1
        seed = self._rng.randrange(1 << 31)
        deadline = time.monotonic() + max(0.5, duration)
        try:
            # human_scroll() is our shared human-behavior primitive (2–5
            # uneven wheel chunks with random pauses); call it repeatedly
            # until the step's duration budget is spent.
            while time.monotonic() < deadline:
                distance = sign * self._rng.uniform(160, 480)
                human_scroll(page, distance=distance, seed=seed)
                seed = self._rng.randrange(1 << 31)
                if self._rng.random() < 0.15:  # occasional reader micro-pause
                    time.sleep(self._rng.uniform(0.4, 1.1))
            return True
        except Exception as exc:
            logger.warning("warmup scroll failed: %s", exc)
            return False

    def _dwell_step(self, step: ScenarioStep) -> bool:
        min_s = _clamped_seconds(step.params.get("min_sec", 2), 2.0)
        max_s = _clamped_seconds(step.params.get("max_sec", 5), 5.0)
        if max_s < min_s:
            min_s, max_s = max_s, min_s
        time.sleep(self._rng.uniform(min_s, max_s))
        return True

    def _watch_step(self, page: Any, step: ScenarioStep) -> bool:
        watch_s = _clamped_seconds(step.params.get("watch_seconds", 10), 10.0, hi=120.0)
        url = step.params.get("url")
        if url and page is not None:
            if not self._navigate(page, url):
                return False
        time.sleep(watch_s)
        return True

    def _cookie_step(self, page: Any) -> bool:
        if page is None:
            return False
        try:
            clicked = page.evaluate(COOKIE_CONSENT_JS)
            time.sleep(self._rng.uniform(0.5, 1.2))
            logger.debug("warmup: cookie dialog click result: %s", clicked)
            return True
        except Exception as exc:
            logger.warning("warmup: cookie dialog handling failed: %s", exc)
            return False
