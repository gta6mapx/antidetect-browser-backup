"""Self-healing RPA recipes: fallback selectors, failure snapshots, sandbox repair, rollback.

Architecture pattern learned from the-ks/self-healing-code-rpa (MIT,
https://github.com/the-ks/self-healing-code-rpa); implementation is
original, no code copied.

What it borrows (architecture only): a YAML recipe with primary + fallback
selectors, failure snapshots on step failure, a repair step constrained to
selector-level fixes that is validated in a sandbox before anything is
promoted, and versioned rollback of the recipe.

What it does NOT borrow: no LLM anywhere (rule — the upstream project is an
experimental MVP and its repair agent is LLM-shaped; here repair is purely
deterministic: re-query by text content, ARIA role, or attribute variations),
and no separate skill registry — the runner wraps our existing engine
(``src/rpa/engine.py``) instead of forking it.

Recipe format (YAML)::

    meta:
      name: Demo login
      version: 3
      description: Logs in and waits for the dashboard.
    steps:
      - id: open_login
        action: goto
        url: https://example.com/login
      - id: fill_username
        action: fill
        value: demo-user
        primary: "#username"
        fallbacks:
          - "[name='username']"
          - "//input[@id='username']"
          - {role: "textbox", name: "Username"}   # ARIA-role selector
        repair:
          text_hint: "Username"                   # re-query by text content
          role: textbox                           # re-query by ARIA role
      - id: submit
        action: click
        primary: "button[type=submit]"
        fallbacks:
          - "input[type=submit]"
          - "//button[contains(., 'Sign in')]"
      - id: wait_dashboard
        action: wait_for
        primary: "#dashboard"
        timeout_ms: 10000

Pipeline per step:
  1. Resolve a selector: probe ``primary`` first (read-only query, no side
     effects), then each ``fallbacks`` entry in order.
  2. Execute the action through the existing engine (``execute_step`` with
     the resolved selector, activity-step format) — wrap/delegate, not fork.
  3. If every selector fails: capture a failure snapshot (page URL, HTML
     excerpt, screenshot path, attempted selectors + errors) and attempt
     deterministic selector repair in the sandbox — every repair candidate
     is validated with read-only DOM probes only; the page is never clicked,
     filled, or mutated by the repair step.
  4. Versioned rollback: the recipe version that was loaded is snapshotted
     before any repair promotion; when consecutive failures cross the
     threshold, the runner reverts to the previous working version.

Selector spec forms (``primary`` / ``fallbacks`` entries):
  - plain string: CSS selector, or XPath when it starts with ``/`` or ``(//``
  - ``{"role": ..., "name": ...}``: ARIA-role query (``get_by_role``)
  - ``{"text": ...}``: text query (``get_by_text``)
  - ``{"testid": ...}``: ``get_by_test_id``
"""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml


# ──────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────

#: Actions that need no selector at all.
_NO_SELECTOR_ACTIONS = {"goto", "press", "screenshot", "evaluate", "dialog"}

#: Actions understood by the engine's activity-step path
#: (see ``engine._execute_activity_step``).
_KNOWN_ACTIONS = {
    "goto", "click", "fill", "get_text", "wait_for", "screenshot",
    "evaluate", "select", "check", "upload", "dialog", "get_attribute",
    "scroll", "hover", "press",
}

_DEFAULT_WORK_DIR = os.path.expanduser("~/.antidetect-browser/rpa/self_healing")
_SNAPSHOT_HTML_CHARS = 4000
_FAILURES_BEFORE_ROLLBACK = 3


# ──────────────────────────────────────────────────────────────
#  Data types
# ──────────────────────────────────────────────────────────────

@dataclass
class SelectorSpec:
    """One selector in normalized form."""
    kind: str                      # "css" | "xpath" | "role" | "text" | "testid"
    value: Any                     # selector string, or dict for role/text/testid
    source: str = "primary"        # "primary" | "fallback[0]" | "repair:text" | ...


@dataclass
class AttemptRecord:
    selector: str
    source: str
    ok: bool
    error: str = ""


@dataclass
class FailureSnapshot:
    run_id: str
    recipe_name: str
    recipe_version: int
    step_id: str
    action: str
    error: str
    page_url: str = ""
    html_excerpt: str = ""
    screenshot_path: str = ""
    attempted: List[Dict[str, Any]] = field(default_factory=list)
    captured_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairPatch:
    """A selector-only repair proposal. Selector-level changes ONLY —
    no code changes, no action changes, no new steps (no-LLM rule)."""
    patch_id: str
    recipe_name: str
    base_version: int
    step_id: str
    strategy: str                  # "text" | "aria_role" | "attribute_variation" | "xpath_translation"
    proposed: Dict[str, Any]       # normalized selector spec, as dict
    reason: str
    created_at: str = ""
    sandbox_validated: bool = False


class RecipeValidationError(ValueError):
    """Raised when a self-healing recipe YAML fails validation."""


# ──────────────────────────────────────────────────────────────
#  Recipe loading / validation
# ──────────────────────────────────────────────────────────────

def _normalize_selector(entry: Any, source: str) -> SelectorSpec:
    """Normalize one ``primary`` / ``fallbacks`` entry into a SelectorSpec."""
    if isinstance(entry, dict):
        if "role" in entry:
            return SelectorSpec("role", {"role": entry["role"],
                                         "name": entry.get("name", "")}, source)
        if "text" in entry:
            return SelectorSpec("text", {"text": entry["text"]}, source)
        if "testid" in entry:
            return SelectorSpec("testid", {"testid": entry["testid"]}, source)
        raise RecipeValidationError(
            f"bad selector mapping {entry!r} (need role/text/testid keys)")
    if not isinstance(entry, str) or not entry.strip():
        raise RecipeValidationError(f"bad selector entry {entry!r}")
    s = entry.strip()
    kind = "xpath" if s.startswith("/") or s.startswith("(") else "css"
    return SelectorSpec(kind, s, source)


def _selector_label(spec: SelectorSpec) -> str:
    if isinstance(spec.value, dict):
        inner = ", ".join(f"{k}={v!r}" for k, v in spec.value.items() if v)
        return f"{spec.kind}({inner})"
    return f"{spec.kind}:{spec.value}"


class SelfHealingRecipe:
    """A parsed + validated self-healing recipe."""

    def __init__(self, meta: Dict[str, Any], steps: List[Dict[str, Any]]):
        self.meta = meta
        self.steps = steps

    # ── constructors ──────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfHealingRecipe":
        if not isinstance(data, dict):
            raise RecipeValidationError("recipe must be a mapping")
        meta = data.get("meta") or {}
        steps = data.get("steps") or []
        if not isinstance(meta, dict) or not isinstance(steps, list):
            raise RecipeValidationError("recipe needs 'meta' mapping and 'steps' list")
        meta = {
            "name": str(meta.get("name", "unnamed")),
            "version": int(meta.get("version", 1)),
            "description": str(meta.get("description", "")),
        }
        if not steps:
            raise RecipeValidationError("recipe needs at least one step")
        seen = set()
        norm_steps = []
        for i, raw in enumerate(steps):
            if not isinstance(raw, dict):
                raise RecipeValidationError(f"step #{i} must be a mapping")
            step_id = str(raw.get("id") or f"step_{i + 1}")
            if step_id in seen:
                raise RecipeValidationError(f"duplicate step id: {step_id!r}")
            seen.add(step_id)
            action = str(raw.get("action", "")).strip().lower()
            if action not in _KNOWN_ACTIONS:
                raise RecipeValidationError(
                    f"step {step_id!r}: unknown action {action!r} "
                    f"(known: {sorted(_KNOWN_ACTIONS)})")
            step = {"id": step_id, "action": action}
            for key in ("url", "value", "key", "script", "path", "attribute",
                        "timeout_ms", "timeout", "checked", "file", "label"):
                if key in raw and raw[key] is not None:
                    step[key] = raw[key]
            if action not in _NO_SELECTOR_ACTIONS:
                if not raw.get("primary"):
                    raise RecipeValidationError(
                        f"step {step_id!r}: action {action!r} needs 'primary' selector")
                step["primary"] = _normalize_selector(raw["primary"], "primary")
                fallbacks = []
                for n, fb in enumerate(raw.get("fallbacks") or []):
                    fallbacks.append(
                        _normalize_selector(fb, f"fallback[{n}]"))
                step["fallbacks"] = fallbacks
            if isinstance(raw.get("repair"), dict):
                step["repair"] = dict(raw["repair"])
            norm_steps.append(step)
        return cls(meta, norm_steps)

    @classmethod
    def from_yaml(cls, path: str) -> "SelfHealingRecipe":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    # ── helpers ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def version(self) -> int:
        return self.meta["version"]

    def selectors_for(self, step: Dict[str, Any]) -> List[SelectorSpec]:
        """Primary + fallbacks in execution order."""
        return [step["primary"]] + list(step.get("fallbacks", []))

    def to_yaml(self) -> str:
        """Serialize back to YAML (selectors denormalized to author form)."""
        data = {"meta": dict(self.meta), "steps": []}
        for step in self.steps:
            raw = {"id": step["id"], "action": step["action"]}
            for key in ("url", "value", "key", "script", "path", "attribute",
                        "timeout_ms", "timeout", "checked", "file", "label",
                        "repair"):
                if key in step:
                    raw[key] = step[key]
            if "primary" in step:
                raw["primary"] = _denormalize(step["primary"])
                raw["fallbacks"] = [_denormalize(f)
                                    for f in step.get("fallbacks", [])]
            data["steps"].append(raw)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _denormalize(spec: SelectorSpec) -> Any:
    if spec.kind in ("css", "xpath"):
        return spec.value
    return dict(spec.value)


# ──────────────────────────────────────────────────────────────
#  Selector probing (read-only — the "sandbox")
# ──────────────────────────────────────────────────────────────
#
# Every probe is a non-mutating DOM query. Repair candidates are validated
# ONLY through these probes: no clicks, fills, keypresses, or navigation
# happen during repair. That is the sandbox guarantee.

def _probe_locator(page, spec: SelectorSpec, timeout_ms: int = 1500) -> Tuple[bool, str]:
    """Return (found, error). Read-only: uses count()/wait_for(state=attached)."""
    try:
        if spec.kind == "css":
            loc = page.locator(spec.value)
        elif spec.kind == "xpath":
            loc = page.locator(f"xpath={spec.value}")
        elif spec.kind == "role":
            loc = page.get_by_role(spec.value["role"],
                                   name=spec.value.get("name") or None)
        elif spec.kind == "text":
            loc = page.get_by_text(spec.value["text"])
        elif spec.kind == "testid":
            loc = page.get_by_test_id(spec.value["testid"])
        else:
            return False, f"unknown selector kind {spec.kind!r}"
        n = loc.count()
        if n and n > 0:
            return True, ""
        return False, "no matching element"
    except Exception as e:  # noqa: BLE001 — probe must never raise
        return False, f"{type(e).__name__}: {e}"


# ──────────────────────────────────────────────────────────────
#  Deterministic repair strategies (no LLM)
# ──────────────────────────────────────────────────────────────

def _repair_candidates(step: Dict[str, Any]) -> List[Tuple[str, SelectorSpec, str]]:
    """Yield (strategy, SelectorSpec, reason) repair candidates.

    Pure function of the step config — no page access here. Each candidate
    is validated later by the sandbox probe.
    """
    cands: List[Tuple[str, SelectorSpec, str]] = []
    hints = step.get("repair") or {}
    primary = step.get("primary")

    # 1. Re-query by visible text content.
    text_hint = hints.get("text_hint") or hints.get("text")
    if text_hint:
        cands.append((
            "text",
            SelectorSpec("text", {"text": str(text_hint)}, "repair:text"),
            f"re-query by visible text {text_hint!r}",
        ))

    # 2. Re-query by ARIA role.
    role = hints.get("role")
    if role:
        cands.append((
            "aria_role",
            SelectorSpec("role", {"role": str(role),
                                  "name": str(hints.get("name", ""))},
                         "repair:aria_role"),
            f"re-query by ARIA role={role!r}",
        ))

    # 3. Attribute variations of a CSS primary.
    if primary is not None and primary.kind == "css":
        css = primary.value
        # #id  ->  [id="id"] ;  .cls  ->  [class*="cls"]
        m = re.fullmatch(r"#([\w\-:]+)", css)
        if m:
            cands.append((
                "attribute_variation",
                SelectorSpec("css", f'[id="{m.group(1)}"]',
                             "repair:attribute_variation"),
                f"attribute variation of {css!r}",
            ))
        m = re.fullmatch(r"\.([\w\-:]+)", css)
        if m:
            cands.append((
                "attribute_variation",
                SelectorSpec("css", f'[class*="{m.group(1)}"]',
                             "repair:attribute_variation"),
                f"attribute variation of {css!r}",
            ))
        # [name="x"] with different quoting / partial match
        m = re.fullmatch(r'\[name=["\']?([\w\-]+)["\']?\]', css)
        if m:
            cands.append((
                "attribute_variation",
                SelectorSpec("css", f'[name*="{m.group(1)}"]',
                             "repair:attribute_variation"),
                f"partial-match variation of {css!r}",
            ))

    # 4. XPath translation of a simple CSS primary.
    if primary is not None and primary.kind == "css":
        css = primary.value
        m = re.fullmatch(r"#([\w\-:]+)", css)
        if m:
            cands.append((
                "xpath_translation",
                SelectorSpec("xpath", f"//*[@id='{m.group(1)}']",
                             "repair:xpath_translation"),
                f"XPath translation of {css!r}",
            ))
        m = re.fullmatch(r"\.([\w\-:]+)", css)
        if m:
            cands.append((
                "xpath_translation",
                SelectorSpec("xpath",
                             f"//*[contains(@class,'{m.group(1)}')]",
                             "repair:xpath_translation"),
                f"XPath translation of {css!r}",
            ))

    return cands


# ──────────────────────────────────────────────────────────────
#  Version store (snapshots + rollback)
# ──────────────────────────────────────────────────────────────

class RecipeVersionStore:
    """Keeps versioned copies of a recipe YAML and a current-version pointer.

    Layout under ``work_dir``::

        versions/<recipe_name>/v1.yaml, v2.yaml, ...
        versions/<recipe_name>/current.json   {"version": N}
        versions/<recipe_name>/failures.json {"consecutive": n}
    """

    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = work_dir or _DEFAULT_WORK_DIR
        self._versions_dir = os.path.join(self.work_dir, "versions")
        os.makedirs(self._versions_dir, exist_ok=True)

    # ── internals ─────────────────────────────────────────────

    def _recipe_dir(self, name: str) -> str:
        d = os.path.join(self._versions_dir, _safe_name(name))
        os.makedirs(d, exist_ok=True)
        return d

    def _read_json(self, path: str, default: Any) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _write_json(self, path: str, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    # ── public API ────────────────────────────────────────────

    def snapshot(self, recipe: SelfHealingRecipe) -> int:
        """Store the recipe's current YAML as its declared version.
        Returns the version stored."""
        d = self._recipe_dir(recipe.name)
        v = recipe.version
        with open(os.path.join(d, f"v{v}.yaml"), "w", encoding="utf-8") as fh:
            fh.write(recipe.to_yaml())
        self._write_json(os.path.join(d, "current.json"), {"version": v})
        return v

    def current_version(self, name: str) -> Optional[int]:
        cur = self._read_json(
            os.path.join(self._recipe_dir(name), "current.json"), None)
        return cur.get("version") if isinstance(cur, dict) else None

    def list_versions(self, name: str) -> List[int]:
        d = self._recipe_dir(name)
        out = []
        for fn in os.listdir(d):
            m = re.fullmatch(r"v(\d+)\.yaml", fn)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def load_version(self, name: str, version: int) -> SelfHealingRecipe:
        path = os.path.join(self._recipe_dir(name), f"v{version}.yaml")
        if not os.path.exists(path):
            raise KeyError(f"no snapshot of {name!r} at version {version}")
        return SelfHealingRecipe.from_yaml(path)

    def promote_patch(self, patch: RepairPatch) -> SelfHealingRecipe:
        """Apply a sandbox-validated patch → new recipe version. The prior
        version is kept on disk, so rollback is always possible."""
        if not patch.sandbox_validated:
            raise ValueError("refusing to promote an unvalidated patch")
        recipe = self.load_version(patch.recipe_name, patch.base_version)
        updated = False
        for step in recipe.steps:
            if step["id"] != patch.step_id:
                continue
            spec = SelectorSpec(patch.proposed["kind"],
                                patch.proposed["value"],
                                f"repair:{patch.strategy}")
            # Repaired selector becomes primary; old primary demotes to
            # the front of the fallback list (never lost).
            old_primary = step.get("primary")
            step["primary"] = spec
            fbs = list(step.get("fallbacks", []))
            if old_primary is not None:
                old_primary.source = "fallback[demoted]"
                fbs.insert(0, old_primary)
            step["fallbacks"] = fbs
            updated = True
        if not updated:
            raise KeyError(f"step {patch.step_id!r} not in recipe")
        recipe.meta["version"] = patch.base_version + 1
        self.snapshot(recipe)
        self.reset_failures(patch.recipe_name)
        return recipe

    def rollback(self, name: str, to_version: Optional[int] = None) -> SelfHealingRecipe:
        """Revert to a previous working version (default: latest before
        current). The rolled-back version becomes current again."""
        versions = self.list_versions(name)
        if not versions:
            raise KeyError(f"no snapshots for {name!r}")
        current = self.current_version(name)
        if to_version is None:
            older = [v for v in versions if current is None or v < current]
            if not older:
                raise ValueError(f"no older version to roll back to for {name!r}")
            to_version = max(older)
        recipe = self.load_version(name, to_version)
        # Point current at the restored version (keep all snapshots).
        self._write_json(os.path.join(self._recipe_dir(name), "current.json"),
                         {"version": to_version})
        self.reset_failures(name)
        return recipe

    # ── failure accounting (drives automatic rollback) ────────

    def record_failure(self, name: str) -> int:
        """Increment consecutive-failure counter. Returns the new count."""
        path = os.path.join(self._recipe_dir(name), "failures.json")
        n = int(self._read_json(path, {}).get("consecutive", 0)) + 1
        self._write_json(path, {"consecutive": n})
        return n

    def reset_failures(self, name: str) -> None:
        self._write_json(os.path.join(self._recipe_dir(name), "failures.json"),
                         {"consecutive": 0})

    def consecutive_failures(self, name: str) -> int:
        return int(self._read_json(
            os.path.join(self._recipe_dir(name), "failures.json"), {}
        ).get("consecutive", 0))


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "unnamed"


# ──────────────────────────────────────────────────────────────
#  Default step executor — delegates to the existing engine
# ──────────────────────────────────────────────────────────────
#
# Wrap, don't fork: our steps are translated into the engine's existing
# activity-step format (``{"type": "click", "selector": ...}``) and run via
# ``engine.execute_step``. All action semantics stay in engine.py.

def _default_step_executor(page, activity_step: Dict[str, Any],
                           delay: Dict[str, Any], job_id: str) -> Tuple[bool, Any]:
    from src.rpa import engine as _engine
    return _engine.execute_step(page, activity_step, {}, delay, job_id)


def _step_to_activity(step: Dict[str, Any], selector: Optional[str]) -> Dict[str, Any]:
    """Translate one recipe step + resolved selector to engine activity format."""
    act: Dict[str, Any] = {"type": step["action"]}
    if selector:
        act["selector"] = selector
    for key in ("url", "value", "key", "script", "path", "attribute",
                "timeout", "timeout_ms", "checked", "file"):
        if key in step:
            act[key] = step[key]
    # Engine reads ``timeout`` for wait_for; accept ``timeout_ms`` too.
    if "timeout_ms" in act and "timeout" not in act:
        act["timeout"] = act["timeout_ms"]
    return act


# ──────────────────────────────────────────────────────────────
#  The runner
# ──────────────────────────────────────────────────────────────

class SelfHealingRunner:
    """Runs a self-healing recipe against an injected Playwright page.

    Selector resolution (primary → fallbacks in order) and repair happen
    here; action execution delegates to the existing engine via
    ``step_executor`` (default: ``engine.execute_step`` in activity format).

    :param recipe: a ``SelfHealingRecipe`` (or YAML path via ``from_yaml``).
    :param page: injected Playwright ``Page`` (our Camoufox profile page).
    :param job_id: engine log namespace; defaults to ``sh-<run_id>``.
    :param work_dir: snapshots / versions root (default
        ``~/.antidetect-browser/rpa/self_healing``).
    :param delay: engine delay dict (``action_timeout_ms`` etc.).
    :param step_executor: ``fn(page, activity_step, delay, job_id) ->
        (ok, page)``. Defaults to the existing engine.
    :param log_cb: optional ``callable(str)`` for log lines.
    :param promote_repairs: when True, a sandbox-validated repair patch is
        automatically promoted to a new recipe version. Default False —
        repairs are reported for operator review instead.
    :param failures_before_rollback: consecutive all-selector failures that
        trigger automatic rollback to the previous version (default 3).
        0 disables automatic rollback.
    """

    def __init__(self, recipe: SelfHealingRecipe, page,
                 job_id: Optional[str] = None,
                 work_dir: Optional[str] = None,
                 delay: Optional[Dict[str, Any]] = None,
                 step_executor: Optional[Callable] = None,
                 log_cb: Optional[Callable[[str], None]] = None,
                 promote_repairs: bool = False,
                 failures_before_rollback: int = _FAILURES_BEFORE_ROLLBACK):
        self.recipe = recipe
        self.page = page
        self.run_id = f"sh-{int(time.time() * 1000)}"
        self.job_id = job_id or self.run_id
        self.work_dir = work_dir or _DEFAULT_WORK_DIR
        self.delay = delay or {}
        self.step_executor = step_executor or _default_step_executor
        self.promote_repairs = promote_repairs
        self.failures_before_rollback = failures_before_rollback
        self.versions = RecipeVersionStore(self.work_dir)
        self.snapshots_dir = os.path.join(self.work_dir, "snapshots")
        os.makedirs(self.snapshots_dir, exist_ok=True)

        self._log_cb = log_cb
        self.attempts: List[AttemptRecord] = []   # every selector probe, in order
        self.snapshots: List[FailureSnapshot] = []
        self.patches: List[RepairPatch] = []      # sandbox-validated repair proposals
        self.rolled_back_to: Optional[int] = None

        # Keep the recipe's declared version on disk BEFORE the run, so a
        # previous working version always exists to roll back to.
        self.versions.snapshot(recipe)

    # ── constructors ──────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str, page, **kwargs) -> "SelfHealingRunner":
        return cls(SelfHealingRecipe.from_yaml(path), page, **kwargs)

    # ── logging ───────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:
                pass
        try:
            from src.rpa import engine as _engine
            _engine.log(self.job_id, msg)
        except Exception:
            pass

    # ── selector resolution ───────────────────────────────────

    def _resolve(self, step: Dict[str, Any]) -> Tuple[Optional[SelectorSpec], List[AttemptRecord]]:
        """Probe primary then fallbacks in order. Returns (working spec, attempts)."""
        tried: List[AttemptRecord] = []
        for spec in self.recipe.selectors_for(step):
            label = _selector_label(spec)
            found, err = _probe_locator(self.page, spec)
            rec = AttemptRecord(selector=label, source=spec.source,
                                ok=found, error=err)
            tried.append(rec)
            self.attempts.append(rec)
            if found:
                self._log(f"    ✓ [{step['id']}] selector OK via {spec.source}: {label}")
                return spec, tried
            self._log(f"    ✗ [{step['id']}] {spec.source} missed: {label} ({err})")
        return None, tried

    # ── failure snapshot ──────────────────────────────────────

    def _capture_snapshot(self, step: Dict[str, Any],
                          tried: List[AttemptRecord], error: str) -> FailureSnapshot:
        url, html = "", ""
        shot_path = ""
        try:
            url = self.page.url or ""
        except Exception:
            pass
        try:
            html = (self.page.content() or "")[:_SNAPSHOT_HTML_CHARS]
        except Exception:
            pass
        shot_name = f"{self.run_id}_{step['id']}.png"
        shot_path = os.path.join(self.snapshots_dir, shot_name)
        try:
            self.page.screenshot(path=shot_path)
        except Exception:
            shot_path = ""  # screenshot unavailable (offline/closed page)
        snap = FailureSnapshot(
            run_id=self.run_id,
            recipe_name=self.recipe.name,
            recipe_version=self.recipe.version,
            step_id=step["id"],
            action=step["action"],
            error=error,
            page_url=url,
            html_excerpt=html,
            screenshot_path=shot_path,
            attempted=[asdict(a) for a in tried],
            captured_at=datetime.now().isoformat(timespec="seconds"),
        )
        out = os.path.join(self.snapshots_dir, f"{self.run_id}_{step['id']}.json")
        try:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(snap.to_dict(), fh, indent=2)
        except OSError:
            pass
        self.snapshots.append(snap)
        self._log(f"    📸 snapshot → {out}")
        return snap

    # ── sandbox repair (read-only probes only) ─────────────────

    def _attempt_repair(self, step: Dict[str, Any]) -> Optional[RepairPatch]:
        """Try deterministic repair strategies. Each candidate is validated
        with read-only DOM probes — no clicks, fills, or navigation."""
        for strategy, spec, reason in _repair_candidates(step):
            found, err = _probe_locator(self.page, spec)
            self._log(f"    🔧 repair probe [{strategy}]: "
                      f"{_selector_label(spec)} → "
                      f"{'HIT' if found else 'miss (' + err + ')'}")
            if not found:
                continue
            patch = RepairPatch(
                patch_id=f"{self.run_id}-{step['id']}-{strategy}",
                recipe_name=self.recipe.name,
                base_version=self.recipe.version,
                step_id=step["id"],
                strategy=strategy,
                proposed={"kind": spec.kind, "value": spec.value},
                reason=reason,
                created_at=datetime.now().isoformat(timespec="seconds"),
                sandbox_validated=True,
            )
            self.patches.append(patch)
            self._log(f"    ✅ repair validated (sandbox, no side effects): "
                      f"{_selector_label(spec)}")
            return patch
        return None

    # ── step execution ────────────────────────────────────────

    def run_step(self, step: Dict[str, Any]) -> bool:
        """Execute one step with self-healing. Returns True on success."""
        label = step.get("label", step["id"])
        action = step["action"]
        self._log(f"  → [{step['id']}] {action} {label}")

        if action in _NO_SELECTOR_ACTIONS:
            activity = _step_to_activity(step, None)
            ok, _ = self.step_executor(self.page, activity, self.delay, self.job_id)
            self._log(f"    {'✓' if ok else '✗'} [{step['id']}] {action}")
            return ok

        spec, tried = self._resolve(step)
        if spec is None:
            # All selectors failed → snapshot + sandbox repair attempt.
            err = "all selectors failed: " + "; ".join(
                f"{a.source} ({a.error})" for a in tried)
            self._capture_snapshot(step, tried, err)
            patch = self._attempt_repair(step)
            if patch is not None and self.promote_repairs:
                self.recipe = self.versions.promote_patch(patch)
                self._log(f"    ⬆ promoted repair → recipe v{self.recipe.version}")
                # Retry the step once with the repaired selector from the
                # new recipe version (not the stale step object).
                fresh = next(s for s in self.recipe.steps
                             if s["id"] == step["id"])
                return self.run_step(fresh)
            self._log(f"    ✗ [{step['id']}] FAILED "
                      f"({'repair proposed' if patch else 'no repair found'})")
            return False

        # Selector resolved — delegate the action to the existing engine.
        engine_selector = (spec.value if spec.kind in ("css", "xpath")
                           else None)
        activity = _step_to_activity(step, engine_selector)
        if engine_selector is None:
            # ARIA/text/testid selectors: express as Playwright selector
            # strings the engine's locator path understands.
            activity["selector"] = _playwright_selector_string(spec)
        ok, _ = self.step_executor(self.page, activity, self.delay, self.job_id)
        if not ok:
            # Selector probed fine but the action itself failed (e.g. click
            # intercepted). Snapshot it, but there is nothing to repair at
            # the selector level.
            self._capture_snapshot(
                step, tried, "action failed after selector resolved")
        return ok

    # ── full run ──────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Run all steps in order. Returns a summary dict.

        Stops at the first failed step (like the engine's row processing);
        use ``run_step`` directly for per-step control.
        """
        self._log(f"🤖 self-healing run {self.run_id}: "
                  f"{self.recipe.name} v{self.recipe.version} "
                  f"({len(self.recipe.steps)} steps)")
        failed_step: Optional[str] = None
        for step in self.recipe.steps:
            if not self.run_step(step):
                failed_step = step["id"]
                break

        success = failed_step is None
        if success:
            self.versions.reset_failures(self.recipe.name)
        else:
            n = self.versions.record_failure(self.recipe.name)
            self._log(f"  ⚠ consecutive failures for {self.recipe.name!r}: {n}")
            if (self.failures_before_rollback > 0
                    and n >= self.failures_before_rollback):
                older = [v for v in self.versions.list_versions(self.recipe.name)
                         if v < self.recipe.version]
                if older:
                    target = max(older)
                    self.recipe = self.versions.rollback(self.recipe.name,
                                                         target)
                    self.rolled_back_to = target
                    self._log(f"  ⏪ rolled back to v{target} "
                              f"after {n} consecutive failures")

        summary = {
            "run_id": self.run_id,
            "recipe": self.recipe.name,
            "version": self.recipe.version,
            "success": success,
            "failed_step": failed_step,
            "steps_total": len(self.recipe.steps),
            "selector_attempts": len(self.attempts),
            "snapshots": len(self.snapshots),
            "repairs_proposed": len(self.patches),
            "rolled_back_to": self.rolled_back_to,
        }
        self._log(f"{'✅' if success else '❌'} run {self.run_id}: "
                  f"{'OK' if success else 'FAILED @ ' + str(failed_step)}")
        return summary


def _playwright_selector_string(spec: SelectorSpec) -> str:
    """Express an ARIA/text/testid spec as a Playwright selector string so the
    engine's ``page.locator(...)`` path can use it."""
    if spec.kind == "role":
        name = spec.value.get("name") or ""
        return f"role={spec.value['role']}[name={name!r}]" if name \
            else f"role={spec.value['role']}"
    if spec.kind == "text":
        return f"text={spec.value['text']}"
    if spec.kind == "testid":
        return f"data-testid={spec.value['testid']}"
    return str(spec.value)


# ──────────────────────────────────────────────────────────────
#  Convenience: promote a patch by hand (operator review lane)
# ──────────────────────────────────────────────────────────────

def promote_patch(patch: RepairPatch,
                  work_dir: Optional[str] = None) -> SelfHealingRecipe:
    """Promote a sandbox-validated patch to a new recipe version.
    The previous version is kept for rollback."""
    return RecipeVersionStore(work_dir).promote_patch(patch)


def rollback_recipe(name: str, to_version: Optional[int] = None,
                    work_dir: Optional[str] = None) -> SelfHealingRecipe:
    """Revert a recipe to a previous working version."""
    return RecipeVersionStore(work_dir).rollback(name, to_version)
