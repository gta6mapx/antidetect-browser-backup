#!/usr/bin/env python3
"""Self-healing RPA tests — all offline.

No real browser is launched. A fake Playwright page double records which
selectors were probed and which actions were executed, so we can verify:
  - fallback selector ordering (primary -> fallbacks in order),
  - failure snapshot capture (URL, HTML excerpt, attempted selectors),
  - sandbox repair is side-effect free (no clicks/fills during repair),
  - versioned rollback (manual + automatic after repeated failures),
  - recipe validation.

Runnable without pytest: ``.venv/bin/python tests/test_self_healing.py``.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

from src.rpa.self_healing import (
    SelfHealingRunner,
    SelfHealingRecipe,
    RecipeVersionStore,
    RecipeValidationError,
    promote_patch,
    rollback_recipe,
)


# ---------------------------------------------------------------------------
# Fake Playwright page double
# ---------------------------------------------------------------------------

class FakeLocator:
    """Read-only probes (count) never mutate; actions record side effects."""

    def __init__(self, page, key):
        self._page = page
        self._key = key

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._key in self._page.hits else 0

    def wait_for(self, **kwargs):
        if self._key not in self._page.hits:
            raise TimeoutError("fake timeout waiting for " + self._key)
        return None

    def click(self, **kwargs):
        if self._key not in self._page.hits:
            raise RuntimeError("click on missing element " + self._key)
        self._page.clicked.append(self._key)

    def fill(self, value, **kwargs):
        if self._key not in self._page.hits:
            raise RuntimeError("fill on missing element " + self._key)
        self._page.filled[self._key] = value

    def inner_text(self, **kwargs):
        if self._key not in self._page.hits:
            raise RuntimeError("inner_text on missing element " + self._key)
        return "fake result text"

    def scroll_into_view_if_needed(self, **kwargs):
        return None

    def hover(self, **kwargs):
        return None


class FakeKeyboard:
    def __init__(self, page):
        self._page = page

    def press(self, key):
        self._page.pressed.append(key)


class FakePage:
    """Keys are canonical selector keys the runner probes produce:

    - css: the raw css string, e.g. "#username"
    - xpath: "xpath=//input[@id='username']" (prefix stripped for lookup)
    - role: "role=textbox[name='Username']"
    - text: "text=Sign in"
    - testid: "data-testid=submit"
    """

    def __init__(self, hits, url="https://example.com/login",
                 html="<html><body>demo</body></html>"):
        self.hits = set(hits)
        self.url = url
        self._html = html
        self.clicked = []
        self.filled = {}
        self.pressed = []
        self.screenshots = []
        self.keyboard = FakeKeyboard(self)

    # -- locator factories -------------------------------------------------
    def locator(self, selector):
        key = selector[6:] if selector.startswith("xpath=") else selector
        return FakeLocator(self, key)

    def get_by_role(self, role, name=None):
        key = f"role={role}[name={name!r}]" if name else f"role={role}"
        return FakeLocator(self, key)

    def get_by_text(self, text):
        return FakeLocator(self, text if text.startswith("text=") else f"text={text}")

    def get_by_test_id(self, testid):
        return FakeLocator(self, f"data-testid={testid}")

    # -- page state --------------------------------------------------------
    def content(self):
        return self._html

    def screenshot(self, path=None, **kwargs):
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"FAKEPNG")
            self.screenshots.append(path)
        return b"FAKEPNG"

    def goto(self, url):
        self.url = url

    def wait_for_load_state(self, state, timeout=None):
        return None


def _recipe_dict(**kw):
    d = {
        "meta": {"name": kw.pop("name", "test-recipe"), "version": kw.pop("version", 1)},
        "steps": kw.pop("steps"),
    }
    d.update(kw)
    return d


def _tmp_workdir():
    return tempfile.mkdtemp(prefix="sh_test_")


# ---------------------------------------------------------------------------
# 1. Fallback selector ordering
# ---------------------------------------------------------------------------

def test_fallback_selector_ordering():
    page = FakePage(hits={"[name='username']"})  # primary + fallback[0] miss
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "fill_username", "action": "fill", "value": "u",
        "primary": "#username",
        "fallbacks": ["#user-field", "[name='username']"],
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=_tmp_workdir(),
                               log_cb=lambda m: None)
    ok = runner.run_step(recipe.steps[0])
    assert ok, "step should succeed via fallback[1]"
    order = [a.source for a in runner.attempts]
    assert order == ["primary", "fallback[0]", "fallback[1]"], order
    assert page.filled == {"[name='username']": "u"}
    print("ok - fallback ordering")


def test_primary_hit_skips_fallbacks():
    page = FakePage(hits={"#username"})
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "fill_username", "action": "fill", "value": "u",
        "primary": "#username",
        "fallbacks": ["[name='username']"],
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=_tmp_workdir(),
                               log_cb=lambda m: None)
    assert runner.run_step(recipe.steps[0])
    assert [a.source for a in runner.attempts] == ["primary"]
    print("ok - primary hit skips fallbacks")


# ---------------------------------------------------------------------------
# 2. Snapshot capture on total selector failure
# ---------------------------------------------------------------------------

def test_snapshot_capture():
    page = FakePage(hits=set(), html="<html><body>login page v2</body></html>")
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "click_signin", "action": "click",
        "primary": "#signin", "fallbacks": ["#sign-in"]},
    ]))
    wd = _tmp_workdir()
    runner = SelfHealingRunner(recipe, page, work_dir=wd,
                               log_cb=lambda m: None)
    summary = runner.run()
    assert summary["success"] is False
    assert summary["failed_step"] == "click_signin"
    assert summary["snapshots"] == 1
    snap = runner.snapshots[0]
    assert snap.step_id == "click_signin"
    assert "login page v2" in snap.html_excerpt
    assert snap.page_url == "https://example.com/login"
    assert [a["source"] for a in snap.attempted] == ["primary", "fallback[0]"]
    # snapshot JSON persisted to disk
    snap_files = [f for f in os.listdir(runner.snapshots_dir)
                  if f.endswith(".json")]
    assert len(snap_files) == 1
    with open(os.path.join(runner.snapshots_dir, snap_files[0])) as fh:
        disk = json.load(fh)
    assert disk["step_id"] == "click_signin"
    assert len(disk["attempted"]) == 2
    print("ok - snapshot capture")


# ---------------------------------------------------------------------------
# 3. Sandbox repair: validated, side-effect free, promotable
# ---------------------------------------------------------------------------

def test_repair_sandbox_no_side_effects():
    # No listed selector matches, but the text-hint repair candidate does.
    page = FakePage(hits={"text=Sign in"})
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "click_signin", "action": "click",
        "primary": "#signin",
        "repair": {"text_hint": "Sign in"},
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=_tmp_workdir(),
                               log_cb=lambda m: None,
                               promote_repairs=False)
    ok = runner.run_step(recipe.steps[0])
    assert ok is False, "step fails: no listed selector resolves"
    assert len(runner.patches) == 1
    patch = runner.patches[0]
    assert patch.sandbox_validated is True
    assert patch.strategy == "text"
    # Repair probed the DOM but never clicked / filled / pressed anything.
    assert page.clicked == [] and page.filled == {} and page.pressed == []
    # Not auto-promoted: recipe version unchanged.
    assert runner.recipe.version == 1
    print("ok - repair sandbox (no side effects)")


def test_promote_patch_bumps_version():
    page = FakePage(hits={"text=Sign in"})
    wd = _tmp_workdir()
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "click_signin", "action": "click",
        "primary": "#signin",
        "repair": {"text_hint": "Sign in"},
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=wd,
                               log_cb=lambda m: None)
    assert runner.run_step(recipe.steps[0]) is False
    patch = runner.patches[0]
    new_recipe = promote_patch(patch, work_dir=wd)
    assert new_recipe.version == 2
    step = new_recipe.steps[0]
    assert step["primary"].kind == "text"          # repaired selector promoted
    assert step["fallbacks"][0].value == "#signin"  # old primary kept as fallback
    assert RecipeVersionStore(wd).current_version("test-recipe") == 2
    print("ok - promote patch")


def test_aria_role_repair_candidate():
    page = FakePage(hits={"role=button[name='Search']"})
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "submit_search", "action": "click",
        "primary": "#search-button",
        "repair": {"role": "button", "name": "Search"},
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=_tmp_workdir(),
                               log_cb=lambda m: None)
    assert runner.run_step(recipe.steps[0]) is False
    assert runner.patches[0].strategy == "aria_role"
    assert page.clicked == []
    print("ok - aria role repair")


# ---------------------------------------------------------------------------
# 4. Versioned rollback (manual + automatic)
# ---------------------------------------------------------------------------

def test_manual_rollback():
    page = FakePage(hits={"text=Sign in"})
    wd = _tmp_workdir()
    recipe = SelfHealingRecipe.from_dict(_recipe_dict(steps=[{
        "id": "click_signin", "action": "click",
        "primary": "#signin",
        "repair": {"text_hint": "Sign in"},
    }]))
    runner = SelfHealingRunner(recipe, page, work_dir=wd,
                               log_cb=lambda m: None)
    runner.run_step(recipe.steps[0])
    promote_patch(runner.patches[0], work_dir=wd)   # -> v2
    store = RecipeVersionStore(wd)
    assert store.list_versions("test-recipe") == [1, 2]
    rolled = rollback_recipe("test-recipe", work_dir=wd)
    assert rolled.version == 1
    assert rolled.steps[0]["primary"].value == "#signin"
    assert store.current_version("test-recipe") == 1
    print("ok - manual rollback")


def test_auto_rollback_on_repeated_failures():
    wd = _tmp_workdir()
    store = RecipeVersionStore(wd)
    # v1 (working) then v2 (broken): snapshots on disk, current = v2.
    v1 = SelfHealingRecipe.from_dict(_recipe_dict(
        name="flaky", version=1, steps=[{
            "id": "s", "action": "click",
            "primary": "#ok", "fallbacks": []}]))
    v2 = SelfHealingRecipe.from_dict(_recipe_dict(
        name="flaky", version=2, steps=[{
            "id": "s", "action": "click",
            "primary": "#broken", "fallbacks": []}]))
    store.snapshot(v1)
    store.snapshot(v2)
    page = FakePage(hits=set())  # nothing matches: every run fails

    r1 = SelfHealingRunner(v2, page, work_dir=wd, log_cb=lambda m: None,
                           failures_before_rollback=2)
    assert r1.run()["success"] is False
    assert r1.rolled_back_to is None          # 1st failure: no rollback yet
    assert store.consecutive_failures("flaky") == 1

    r2 = SelfHealingRunner(v2, page, work_dir=wd, log_cb=lambda m: None,
                           failures_before_rollback=2)
    summary = r2.run()
    assert summary["success"] is False
    assert summary["rolled_back_to"] == 1     # 2nd failure: auto rollback
    assert r2.recipe.version == 1
    assert store.current_version("flaky") == 1
    assert store.consecutive_failures("flaky") == 0
    print("ok - auto rollback on repeated failures")


# ---------------------------------------------------------------------------
# 5. Recipe validation + demo YAMLs load
# ---------------------------------------------------------------------------

def test_recipe_validation():
    # duplicate step id
    try:
        SelfHealingRecipe.from_dict(_recipe_dict(steps=[
            {"id": "a", "action": "goto", "url": "https://x/"},
            {"id": "a", "action": "goto", "url": "https://x/"},
        ]))
        raise AssertionError("expected RecipeValidationError")
    except RecipeValidationError:
        pass
    # unknown action
    try:
        SelfHealingRecipe.from_dict(_recipe_dict(steps=[
            {"id": "a", "action": "teleport", "primary": "#x"}]))
        raise AssertionError("expected RecipeValidationError")
    except RecipeValidationError:
        pass
    # selector action without primary
    try:
        SelfHealingRecipe.from_dict(_recipe_dict(steps=[
            {"id": "a", "action": "click"}]))
        raise AssertionError("expected RecipeValidationError")
    except RecipeValidationError:
        pass
    print("ok - recipe validation")


def test_demo_recipes_load():
    repo = os.path.join(os.path.dirname(__file__), "..")
    for fn in ("self_healing_demo_login.yaml", "self_healing_demo_search.yaml"):
        path = os.path.join(repo, "src", "rpa", "recipes", fn)
        recipe = SelfHealingRecipe.from_yaml(path)
        assert recipe.version >= 1 and len(recipe.steps) >= 2, fn
    login = SelfHealingRecipe.from_yaml(
        os.path.join(repo, "src", "rpa", "recipes",
                     "self_healing_demo_login.yaml"))
    fill = next(s for s in login.steps if s["id"] == "fill_username")
    assert len(fill["fallbacks"]) == 3
    assert fill["fallbacks"][2].kind == "role"
    print("ok - demo recipes load")


def test_full_run_success_path():
    page = FakePage(hits={"#username", "[name='password']",
                          "role=button[name='Sign in']", "#dashboard"})
    repo = os.path.join(os.path.dirname(__file__), "..")
    runner = SelfHealingRunner.from_yaml(
        os.path.join(repo, "src", "rpa", "recipes",
                     "self_healing_demo_login.yaml"),
        page, work_dir=_tmp_workdir(), log_cb=lambda m: None)
    summary = runner.run()
    assert summary["success"] is True, summary
    assert summary["failed_step"] is None
    assert page.url == "https://example.com/login"
    assert page.filled == {"#username": "demo-user", "[name='password']": "s3cret"}
    assert "role=button[name='Sign in']" in page.clicked
    assert summary["selector_attempts"] > 0
    print("ok - full run delegates to engine")


if __name__ == "__main__":
    test_fallback_selector_ordering()
    test_primary_hit_skips_fallbacks()
    test_snapshot_capture()
    test_repair_sandbox_no_side_effects()
    test_promote_patch_bumps_version()
    test_aria_role_repair_candidate()
    test_manual_rollback()
    test_auto_rollback_on_repeated_failures()
    test_recipe_validation()
    test_demo_recipes_load()
    test_full_run_success_path()
    print("ALL SELF-HEALING TESTS PASSED")
