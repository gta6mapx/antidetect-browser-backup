"""RPA job orchestration.

``RPAManager`` ties the recipe store (``recipes.py``) to the run engine
(``engine.py``) and our Camoufox profile launcher. It replaces upstream's
``run_router.py`` / ``job_store.py`` orchestration (thread spawn + job
state) — upstream's disk-backed log files and auth are dropped; job state
lives in an in-memory dict, logs in the engine registry.

Job lifecycle: queued -> starting -> running -> done | error | stopped.
A job whose page comes from our profile launch always closes the launched
profile when the worker thread finishes (success, error, or stop).
"""

import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

from src.rpa import engine, recipes


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RPAManager:
    """Create / run / monitor RPA recipes on our Camoufox profiles."""

    def __init__(self, db_path: Optional[str] = None):
        """
        :param db_path: SQLite file for the ``rpa_recipes`` table; defaults
            to the same DB ``ProfileManager`` uses
            (``~/.antidetect-browser/profiles.db``).
        """
        self.db_path = db_path or recipes._default_db_path()
        self._jobs: Dict[str, dict] = {}
        self._stop_requested: set = set()
        self._lock = threading.Lock()

    # ── recipes ──────────────────────────────────────────────

    def create(self, name: str, recipe_dict: dict) -> str:
        """Validate and store a recipe; return its id."""
        data = dict(recipe_dict)
        data["name"] = name
        return recipes.create_recipe(data, self.db_path)

    def list(self):
        """Return recipe metadata list."""
        return recipes.list_recipes(self.db_path)

    def get(self, recipe_id: str) -> dict:
        """Return the full recipe dict. Raises KeyError if missing."""
        return recipes.get_recipe(recipe_id, self.db_path)

    def update(self, recipe_id: str, recipe_dict: dict) -> dict:
        return recipes.update_recipe(recipe_id, recipe_dict, self.db_path)

    def delete(self, recipe_id: str) -> bool:
        return recipes.delete_recipe(recipe_id, self.db_path)

    def resolve_id(self, recipe_id_or_name: str) -> str:
        """Accept an id or a recipe name; return the id. Raises KeyError."""
        for r in self.list():
            if r["id"] == recipe_id_or_name or r["name"] == recipe_id_or_name:
                return r["id"]
        raise KeyError(recipe_id_or_name)

    # ── jobs ─────────────────────────────────────────────────

    def run(self, recipe_id: str, profile_name: str,
            data_path: Optional[str] = None, headless: bool = True) -> str:
        """Start a recipe run on a profile's browser. Returns the job id.

        The profile is launched via our Camoufox launcher inside a daemon
        worker thread; the engine drives the injected page (no separate
        browser launch — the upstream stock-Chromium path is bypassed).
        """
        recipe = self.get(recipe_id)  # KeyError if missing — fail before launch
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "recipe_id": recipe_id,
                "recipe_name": recipe.get("name", ""),
                "profile_name": profile_name,
                "status": "starting",
                "started_at": _now_iso(),
                "ended_at": None,
            }
        t = threading.Thread(
            target=self._run_worker,
            args=(job_id, recipe, profile_name, data_path, headless),
            name=f"rpa-{job_id}",
            daemon=True,
        )
        t.start()
        return job_id

    def _run_worker(self, job_id: str, recipe: dict, profile_name: str,
                    data_path: Optional[str], headless: bool):
        launched = None
        try:
            # Lazy imports: launching needs the browser stack, which must
            # never load at module import time (matches src/cli.py style).
            from src.profiles.manager import ProfileManager
            from src.browser.launcher import launch_profile

            with self._lock:
                rec = self._jobs.get(job_id)
                if rec is not None:
                    rec["status"] = "running"

            try:
                persona = ProfileManager(self.db_path).get(profile_name)
            except KeyError:
                raise RuntimeError(f"profile not found: {profile_name!r}")

            engine.append_log(job_id, f"🚀 Launching profile '{profile_name}'...")
            launched = launch_profile(persona, headless=headless)
            engine.append_log(job_id, "✓ Profile launched — handing page to the engine.")

            # The key integration: our Camoufox page is injected, so the
            # engine skips its own browser launch/teardown.
            engine.run_job(job_id, recipe, data_path, page=launched.page)

        except Exception as exc:
            engine.append_log(job_id, f"💥 RPA manager error: {exc}")
            engine.set_status(job_id, "error")
            engine.set_summary(job_id, {"error": str(exc)})
        finally:
            if launched is not None:
                try:
                    launched.close()
                except Exception:
                    pass
            with self._lock:
                rec = self._jobs.get(job_id)
                if rec is not None:
                    rec["ended_at"] = _now_iso()
                    eng_status = engine.get_status(job_id)
                    if eng_status == "done" and job_id in self._stop_requested:
                        rec["status"] = "stopped"
                    else:
                        rec["status"] = eng_status

    def job_status(self, job_id: str) -> dict:
        """Return a status snapshot for a job. Raises KeyError if unknown."""
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                raise KeyError(job_id)
            rec = dict(rec)
        logs = engine.get_logs(job_id)
        return {
            **rec,
            "summary": engine.get_summary(job_id),
            "pending_action": engine.get_pending_action(job_id),
            "log_tail": logs[-100:],
            "log_lines": len(logs),
        }

    def jobs(self):
        """Return lightweight status dicts for all known jobs (newest first)."""
        with self._lock:
            recs = sorted(self._jobs.values(),
                          key=lambda r: r["started_at"], reverse=True)
        return [
            {
                "job_id": r["job_id"],
                "recipe_id": r["recipe_id"],
                "recipe_name": r.get("recipe_name", ""),
                "profile_name": r["profile_name"],
                "status": r["status"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "pending_action": engine.get_pending_action(r["job_id"]),
            }
            for r in recs
        ]

    def stop_job(self, job_id: str):
        """Ask a running job to stop after the current row."""
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            self._stop_requested.add(job_id)
            rec = self._jobs[job_id]
            if rec["status"] in ("starting", "running"):
                rec["status"] = "stopping"
        engine.request_stop(job_id)

    def answer_action(self, job_id: str, answer: str) -> bool:
        """Answer a pending human-handoff question (captcha/human_input/ask).

        Returns False when no action is pending for the job.
        """
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
        return engine.answer_action(job_id, answer)
