"""Offline tests for the Phase 6 warm-up package (no network, no browser).

Uses a ``_FakePage`` double (the same pattern upstream nazak-browser-studio's
own tests use — adapted here to the **sync** Playwright API and page
injection). ``time.sleep`` is stubbed out so the suite stays fast.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# The executor sleeps a lot (post-navigation settle, dwell, scroll pauses).
# Stub it out for the whole suite so tests stay fast; behavior is identical,
# only wall-clock time is removed.
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

from src.warmup import (  # noqa: E402
    BUILTIN_SCENARIOS,
    SCENARIO_ALIASES,
    ScenarioExecutor,
    ScenarioStep,
    WarmupRunner,
    WarmupScenario,
    get_scenario,
    list_scenarios,
    seed_firefox_history,
)


class _FakePage:
    """Minimal sync Playwright-page double (upstream mock-page pattern)."""

    def __init__(self) -> None:
        self.gotos: list = []
        self.scrolls: list = []
        self.evals: list = []
        self.mouse = SimpleNamespace(wheel=self._wheel)

    def _wheel(self, dx, dy):
        self.scrolls.append((dx, dy))

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        return None

    def evaluate(self, expr, *args):
        self.evals.append((expr, args))
        return ""


@pytest.fixture()
def page():
    return _FakePage()


@pytest.fixture()
def executor():
    return ScenarioExecutor(seed=1234)


# ---------------------------------------------------------------------------
# Step dispatch
# ---------------------------------------------------------------------------

def test_open_url_navigates(page, executor):
    step = ScenarioStep("open_url", {"url": "http://localhost:9999/a.html"}, "x")
    assert executor.execute_step(page, step) is True
    assert page.gotos == ["http://localhost:9999/a.html"]


def test_open_url_refuses_unsafe_scheme(page, executor):
    step = ScenarioStep("open_url", {"url": "javascript:alert(1)"}, "x")
    assert executor.execute_step(page, step) is False
    assert page.gotos == []


def test_google_search_uses_template_and_repoints(page, executor):
    step = ScenarioStep(
        "google_search",
        {"query": "best widgets", "search_url_template": "http://localhost:9999/search?q={q}"},
        "x",
    )
    assert executor.execute_step(page, step) is True
    assert page.gotos == ["http://localhost:9999/search?q=best+widgets"]


def test_google_search_defaults_to_google_template(page, executor):
    step = ScenarioStep("google_search", {"query": "hello world"}, "x")
    assert executor.execute_step(page, step) is True
    assert page.gotos == ["https://www.google.com/search?q=hello+world&hl=en"]


def test_human_scroll_emits_wheel_events(page, executor):
    step = ScenarioStep("human_scroll", {"duration_sec": 1.5, "direction": "down"}, "x")
    # Deterministic budget: first tick (deadline) = 1000, loop sees 1000 < 1001.5
    # (one human_scroll call), then 2000 (exit).
    import src.warmup.engine as eng

    real_mono = eng.time.monotonic
    ticks = iter([1000.0, 1000.0, 2000.0])
    eng.time.monotonic = lambda: next(ticks)
    try:
        assert executor.execute_step(page, step) is True
    finally:
        eng.time.monotonic = real_mono
    assert len(page.scrolls) >= 1
    assert all(dy > 0 for _, dy in page.scrolls)


def test_scroll_up_goes_negative(page, executor):
    step = ScenarioStep("human_scroll", {"duration_sec": 0.5, "direction": "up"}, "x")
    # duration 0.5 < floor 0.5? clamped to >=0.5 anyway; force single pass.
    import src.warmup.engine as eng

    real_mono = eng.time.monotonic
    ticks = iter([1000.0, 1000.0, 2000.0])
    eng.time.monotonic = lambda: next(ticks)
    try:
        assert executor.execute_step(page, step) is True
    finally:
        eng.time.monotonic = real_mono
    assert len(page.scrolls) >= 1
    assert all(dy < 0 for _, dy in page.scrolls)


def test_accept_cookie_dialog_runs_consent_js(page, executor):
    step = ScenarioStep("accept_cookie_dialog", {}, "x")
    assert executor.execute_step(page, step) is True
    assert page.evals and "onetrust-accept-btn-handler" in page.evals[0][0]


def test_dwell_returns_true_and_clamps(page, executor):
    # Extreme values must be clamped to [0.5, 600], never NaN/inf-forever.
    step = ScenarioStep("dwell", {"min_sec": 1e12, "max_sec": 1e15}, "x")
    assert executor.execute_step(page, step) is True
    step2 = ScenarioStep("dwell", {"min_sec": "not-a-number", "max_sec": None}, "x")
    assert executor.execute_step(page, step2) is True


def test_watch_youtube_sleeps_and_navigates_when_url_given(page, executor):
    step = ScenarioStep("watch_youtube", {"watch_seconds": 5, "url": "http://localhost:9999/v.html"}, "x")
    assert executor.execute_step(page, step) is True
    assert page.gotos == ["http://localhost:9999/v.html"]


def test_watch_youtube_without_url_just_sleeps(page, executor):
    step = ScenarioStep("watch_youtube", {"watch_seconds": 999999}, "x")  # clamped
    assert executor.execute_step(page, step) is True
    assert page.gotos == []


def test_unknown_action_fails_loudly(page, executor):
    assert executor.execute_step(page, ScenarioStep("click_internal_link", {}, "x")) is False


def test_page_none_fails_navigation_and_scroll(executor):
    assert executor.execute_step(None, ScenarioStep("open_url", {"url": "http://x/"}, "x")) is False
    assert executor.execute_step(None, ScenarioStep("human_scroll", {}, "x")) is False
    assert executor.execute_step(None, ScenarioStep("accept_cookie_dialog", {}, "x")) is False


# ---------------------------------------------------------------------------
# JSON round-trip (upstream wire-compatible)
# ---------------------------------------------------------------------------

def test_step_serialization_round_trip():
    step = ScenarioStep("open_url", {"url": "https://www.google.com"}, "Navigate to Google")
    d = step.to_dict()
    assert d["action"] == "open_url"
    assert d["params"]["url"] == "https://www.google.com"
    recovered = ScenarioStep.from_dict(d)
    assert recovered.action == "open_url"
    assert recovered.description == "Navigate to Google"


def test_scenario_serialization_round_trip():
    scen = BUILTIN_SCENARIOS[0]
    d = scen.to_dict()
    assert "id" in d and "steps" in d
    assert d["total_steps"] == len(scen.steps)
    reconstructed = WarmupScenario.from_dict(d)
    assert reconstructed.id == scen.id
    assert len(reconstructed.steps) == len(scen.steps)
    assert reconstructed.steps[0].action == scen.steps[0].action


def test_scenario_loads_from_json_file():
    scen = BUILTIN_SCENARIOS[1]
    raw = json.dumps(scen.to_dict())
    loaded = WarmupScenario.from_dict(json.loads(raw))
    assert loaded.id == scen.id
    assert [s.action for s in loaded.steps] == [s.action for s in scen.steps]


# ---------------------------------------------------------------------------
# Scenario listing / aliases / URL re-pointing
# ---------------------------------------------------------------------------

def test_builtin_scenarios_present():
    assert len(BUILTIN_SCENARIOS) >= 4
    names = [s.name for s in BUILTIN_SCENARIOS]
    assert any("E-Commerce" in n for n in names)
    assert any("YouTube" in n for n in names)


def test_aliases_resolve():
    assert set(SCENARIO_ALIASES) == {"youtube", "ecommerce", "crypto", "finance"}
    assert get_scenario("youtube").id == "scen_youtube_viewer"
    assert get_scenario("Ecommerce").id == "scen_ecom_trust"
    assert get_scenario("scen_crypto_web3").id == "scen_crypto_web3"


def test_unknown_alias_raises_keyerror():
    with pytest.raises(KeyError):
        get_scenario("nope")


def test_list_scenarios_shape():
    rows = list_scenarios()
    assert len(rows) == 4
    for r in rows:
        assert {"id", "name", "description"} <= set(r)


def test_scenario_urls_repointable():
    # Every navigation target comes from step params -> localhost re-pointing works.
    scen = get_scenario("youtube")
    localhost = scen.to_dict()
    for s in localhost["steps"]:
        p = s["params"]
        if "url" in p:
            p["url"] = "http://localhost:9999/page.html"
        if "search_url_template" in p or s["action"] == "google_search":
            p["search_url_template"] = "http://localhost:9999/search?q={q}"
    local = WarmupScenario.from_dict(localhost)
    urls = {s.params.get("url") for s in local.steps if "url" in s.params}
    templates = {s.params.get("search_url_template") for s in local.steps if s.action == "google_search"}
    assert urls and all(u.startswith("http://localhost") for u in urls)
    assert templates and all(t.startswith("http://localhost") for t in templates)


# ---------------------------------------------------------------------------
# Full execute() on the fake page
# ---------------------------------------------------------------------------

def test_execute_runs_all_steps(page, executor):
    scen = WarmupScenario(
        id="quick",
        name="Quick",
        steps=[
            ScenarioStep("open_url", {"url": "http://localhost:9999/a.html"}),
            ScenarioStep("dwell", {"min_sec": 0.5, "max_sec": 0.5}),
            ScenarioStep("accept_cookie_dialog", {}),
        ],
    )
    outcome = executor.execute(page, scen)
    assert outcome["success"] is True
    assert outcome["total_steps"] == 3
    assert outcome["completed_steps"] == 3
    assert page.gotos == ["http://localhost:9999/a.html"]
    assert page.evals


def test_execute_records_failed_steps(page, executor):
    scen = WarmupScenario(
        id="mixed",
        name="Mixed",
        steps=[
            ScenarioStep("dwell", {"min_sec": 0.5, "max_sec": 0.5}),
            ScenarioStep("nope_action", {}),
        ],
    )
    outcome = executor.execute(page, scen)
    assert outcome["success"] is False
    assert outcome["results"][0]["success"] is True
    assert outcome["results"][1]["success"] is False


# ---------------------------------------------------------------------------
# Runner (with fakes — never launches a real browser)
# ---------------------------------------------------------------------------

class _FakeManager:
    def get(self, name):
        if name == "missing":
            raise KeyError(name)
        return {"name": name, "os": "windows"}


class _FakeLaunched:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def close(self):
        self.closed = True


def test_runner_success_collects_stats():
    launched_holder = {}

    def fake_launch(persona, headless=True):
        launched = _FakeLaunched(_FakePage())
        launched_holder["launched"] = launched
        return launched

    runner = WarmupRunner(
        profile_manager=_FakeManager(),
        launch_function=fake_launch,
        executor=ScenarioExecutor(seed=7),
    )
    res = runner.run("demo", scenario="finance", headless=True)
    assert res["profile"] == "demo"
    assert res["scenario"] == "scen_finance_banking"
    assert res["steps_executed"] == 4
    assert res["steps_ok"] == 4
    assert res["success"] is True
    assert res["errors"] == []
    assert launched_holder["launched"].closed is True
    assert isinstance(res["duration_sec"], float)


def test_runner_unknown_profile_never_raises():
    runner = WarmupRunner(profile_manager=_FakeManager(), launch_function=lambda p, headless=True: (_ for _ in ()).throw(AssertionError("must not launch")))
    res = runner.run("missing", scenario="youtube")
    assert res["success"] is False
    assert any("not found" in e for e in res["errors"])
    assert res["steps_executed"] == 0


def test_runner_unknown_scenario_never_raises():
    runner = WarmupRunner(profile_manager=_FakeManager(), launch_function=lambda p, headless=True: None)
    res = runner.run("demo", scenario="bogus")
    assert res["success"] is False
    assert res["errors"]


def test_runner_launch_failure_collected():
    def boom(persona, headless=True):
        raise RuntimeError("no binary")

    runner = WarmupRunner(profile_manager=_FakeManager(), launch_function=boom)
    res = runner.run("demo", scenario="youtube")
    assert res["success"] is False
    assert any("launch failed" in e for e in res["errors"])


# ---------------------------------------------------------------------------
# Firefox history seeder (offline, tmp dir)
# ---------------------------------------------------------------------------

def test_seed_firefox_history_writes_places_sqlite():
    with tempfile.TemporaryDirectory() as tmp:
        n = seed_firefox_history(Path(tmp), entries_count=12, seed=42)
        assert n == 12
        db = Path(tmp) / "places.sqlite"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        try:
            places = conn.execute("SELECT id, url, visit_count, last_visit_date FROM moz_places").fetchall()
            assert len(places) == 12
            visits = conn.execute(
                "SELECT place_id, visit_date, visit_type, from_visit FROM moz_historyvisits"
            ).fetchall()
            assert len(visits) >= 12
            # PRTime: microseconds since Unix epoch (not WebKit epoch).
            # Follow-up visits spread forward up to ~3 days apart (upstream's
            # design), so allow a future cap of ~13 days for max visit chains.
            now_us = int(time.time() * 1_000_000)
            for _pid, vdate, _vt, _fv in visits:
                assert 0 < vdate <= now_us + 13 * 86400 * 1_000_000
                assert vdate > now_us - 15 * 86400 * 1_000_000  # within past ~15 days
            # visit_type codes are Firefox codes (1..9).
            for _pid, _vd, vtype, _fv in visits:
                assert 1 <= vtype <= 9
            # Timestamps unique per place (audit-hardened upstream design).
            seen: dict = {}
            for pid, vdate, _vt, _fv in visits:
                seen.setdefault(pid, []).append(vdate)
            for dates in seen.values():
                assert len(set(dates)) == len(dates)
        finally:
            conn.close()


def test_seed_firefox_history_is_idempotent_and_capped():
    with tempfile.TemporaryDirectory() as tmp:
        n1 = seed_firefox_history(Path(tmp), entries_count=8, seed=1)
        n2 = seed_firefox_history(Path(tmp), entries_count=8, seed=2)
        assert n1 == n2 == 8
        conn = sqlite3.connect(str(Path(tmp) / "places.sqlite"))
        try:
            # Same URL sampled twice -> single row, accumulated visit_count.
            count = conn.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0]
            assert count <= 25  # HIGH_TRUST_SITES length
        finally:
            conn.close()
