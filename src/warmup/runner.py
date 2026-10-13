"""Warm-up runner: profile -> launch -> execute -> close (Phase 6).

Original code (not upstream-derived): wires our ``ProfileManager`` and
sync Camoufox ``launch_profile`` to the adapted ``ScenarioExecutor``.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import logging
import time
from typing import Any

from src.warmup.engine import ScenarioExecutor
from src.warmup.scenarios import get_scenario

logger = logging.getLogger(__name__)


class WarmupRunner:
    """Runs a warm-up scenario on a named profile. Never raises from ``run``.

    Managers/launchers are injectable so tests can substitute fakes; by
    default the real ``ProfileManager`` and ``launch_profile`` are used.
    """

    def __init__(self, profile_manager=None, launch_function=None, executor=None):
        """Create a runner.

        Args:
            profile_manager: Object with ``get(name) -> persona dict``;
                defaults to :class:`src.profiles.manager.ProfileManager`.
            launch_function: Callable ``(persona, headless) -> LaunchedProfile``;
                defaults to ``src.browser.launcher.launch_profile``.
            executor: :class:`ScenarioExecutor` to use; defaults to a fresh one.
        """
        if profile_manager is None:
            from src.profiles.manager import ProfileManager

            profile_manager = ProfileManager()
        if launch_function is None:
            from src.browser.launcher import launch_profile

            launch_function = launch_profile
        self._profile_manager = profile_manager
        self._launch = launch_function
        self._executor = executor or ScenarioExecutor()

    def run(
        self,
        profile_name: str,
        scenario: str = "youtube",
        headless: bool = True,
        seed: int | None = None,
        progress_callback=None,
    ) -> dict:
        """Run the warm-up scenario on ``profile_name``.

        Args:
            profile_name: Profile name as stored in ``ProfileManager``.
            scenario: Scenario alias (``youtube``/``ecommerce``/``crypto``/``finance``)
                or a scenario id.
            headless: Launch the browser headless.
            seed: Optional executor seed for reproducible step timing.
            progress_callback: Optional ``(idx, total, label)`` reporter.

        Returns:
            Dict with ``profile``, ``scenario``, ``scenario_name``,
            ``steps_executed``, ``steps_ok``, ``duration_sec``,
            ``success`` and ``errors`` (list of strings). **Never raises** —
            every failure is collected into ``errors``.
        """
        started = time.monotonic()
        errors: list[str] = []
        steps_executed = 0
        steps_ok = 0
        scenario_name = ""
        scenario_id = ""

        try:
            try:
                warmup_scenario = get_scenario(scenario)
            except KeyError as exc:
                errors.append(str(exc))
                warmup_scenario = None

            persona = None
            launched = None
            if warmup_scenario is not None:
                scenario_id = warmup_scenario.id
                scenario_name = warmup_scenario.name
                try:
                    persona = self._profile_manager.get(profile_name)
                except (KeyError, LookupError) as exc:
                    errors.append("profile '%s' not found" % profile_name)
                except Exception as exc:
                    errors.append("profile lookup failed: %s" % exc)

            if persona is not None:
                try:
                    launched = self._launch(persona, headless=headless)
                except Exception as exc:
                    errors.append("launch failed: %s" % exc)
                    launched = None

            if launched is not None:
                try:
                    executor = self._executor if seed is None else ScenarioExecutor(seed=seed)
                    outcome = executor.execute(
                        launched.page, warmup_scenario, progress_callback=progress_callback
                    )
                    steps_executed = outcome["total_steps"]
                    steps_ok = sum(1 for r in outcome["results"] if r["success"])
                    for r in outcome["results"]:
                        if not r["success"]:
                            errors.append("step %d (%s) failed" % (r["step"], r["action"]))
                except Exception as exc:
                    errors.append("scenario execution failed: %s" % exc)
                finally:
                    try:
                        launched.close()
                    except Exception as exc:
                        errors.append("close failed: %s" % exc)
        except Exception as exc:  # belt-and-suspenders: run() never raises
            logger.exception("warmup run crashed for %s", profile_name)
            errors.append("internal error: %s" % exc)

        return {
            "profile": profile_name,
            "scenario": scenario_id or scenario,
            "scenario_name": scenario_name,
            "steps_executed": steps_executed,
            "steps_ok": steps_ok,
            "duration_sec": round(time.monotonic() - started, 2),
            "success": bool(steps_executed) and not errors,
            "errors": errors,
        }
