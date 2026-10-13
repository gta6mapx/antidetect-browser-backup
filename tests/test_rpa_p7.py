#!/usr/bin/env python3
"""Phase 7 tests: RPA engine / recipes / inspector — all offline.

No real browser is launched anywhere in this file. Engine action functions
run against a fake Playwright page double that records calls; the
page-injection path of ``run_job`` is verified by monkeypatching
``playwright.sync_api.sync_playwright`` to raise if a browser launch is
attempted.

Runnable without pytest: ``.venv/bin/python tests/test_rpa_p7.py``.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

from src.rpa import engine
from src.rpa import recipes
from src.rpa.inspector import inspect_page
from src.rpa.manager import RPAManager


# ---------------------------------------------------------------------------
# Fake Playwright page double (records interactions, never launches anything)
# ---------------------------------------------------------------------------

class FakeElement:
    """Stands in for a Playwright Locator/ElementHandle."""

    def __init__(self, page, selector, attrs=None):
        self._page = page
        self._selector = selector
        self._attrs = dict(attrs or {})

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        return None

    def click(self, **kwargs):
        self._page.clicked.append(self._selector)

    def fill(self, value, **kwargs):
        self._page.filled[self._selector] = value

    def type(self, ch, **kwargs):
        self._page.typed.setdefault(self._selector, []).append(ch)

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self, **kwargs):
        # Option elements in the test fixtures carry their label under "text".
        return self._attrs.get("inner_text", self._attrs.get("text", ""))

    def select_option(self, **kwargs):
        self._page.selected[self._selector] = kwargs

    def is_checked(self):
        return bool(self._attrs.get("checked", False))

    def check(self, **kwargs):
        self._attrs["checked"] = True
        self._page.checked[self._selector] = True

    def uncheck(self, **kwargs):
        self._attrs["checked"] = False
        self._page.checked[self._selector] = False

    def set_input_files(self, path, **kwargs):
        self._page.uploaded[self._selector] = path

    def screenshot(self, **kwargs):
        return b"fake-png-bytes"

    def locator(self, sub):
        # Only used for select > option in the inspector.
        if sub == "option":
            return FakeLocator(self._page, self._selector,
                               items=self._attrs.get("options", []))
        return FakeLocator(self._page, self._selector + " " + sub)


class FakeLocator:
    def __init__(self, page, selector, items=None):
        self._page = page
        self._selector = selector
        self._items = items  # preset attr dicts for .all()

    @property
    def first(self):
        return FakeElement(self._page, self._selector)

    def all(self):
        if self._items is not None:
            return [FakeElement(self._page, self._selector, a)
                    for a in self._items]
        return [FakeElement(self._page, self._selector, a)
                for a in self._page.tag_elements.get(self._selector, [])]

    def wait_for(self, **kwargs):
        return None

    def click(self, **kwargs):
        self.first.click(**kwargs)

    def fill(self, value, **kwargs):
        self.first.fill(value, **kwargs)

    def all_text_contents(self):
        return []


class FakeContext:
    def expect_page(self):
        raise AssertionError("expect_page must not be called in offline tests")


class FakePage:
    """Stands in for a Playwright Page."""

    def __init__(self, tag_elements=None):
        self.tag_elements = tag_elements or {}
        self.clicked = []
        self.filled = {}
        self.typed = {}
        self.selected = {}
        self.checked = {}
        self.uploaded = {}
        self.navigated = []
        self.frames = []
        self.main_frame = self
        self.context = FakeContext()
        self.url = "about:blank"
        self._dialog_handlers = []

    def locator(self, selector):
        return FakeLocator(self, selector)

    def goto(self, url, **kwargs):
        self.navigated.append(url)
        self.url = url

    def wait_for_load_state(self, state="load", **kwargs):
        return None

    def wait_for_url(self, pattern, **kwargs):
        return None

    def wait_for_selector(self, selector, **kwargs):
        return None

    def on(self, event, handler):
        self._dialog_handlers.append((event, handler))

    def remove_listener(self, event, handler):
        pass

    def is_closed(self):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
    else:
        FAILED.append((name, detail))
        print("FAIL: %s %s" % (name, detail))


SAMPLE_RECIPE = {
    "name": "Test Recipe",
    "description": "offline test",
    "base_url": "https://example.com",
    "delay": {
        "between_records_ms": 0,
        "between_fields_ms": 0,
        "between_steps_ms": 0,
        "char_delay_ms": 0,
        "page_load_timeout_ms": 15000,
        "action_timeout_ms": 8000,
    },
    "login_steps": [],
    "flow": [
        {
            "step_id": "step_1",
            "label": "Form",
            "url": "https://example.com/form",
            "field_mappings": [
                {"selector": "#name", "field_type": "text",
                 "source": "csv_column", "csv_column": "Name",
                 "value_map": [{"from_val": "Male", "to_val": "male"}]},
                {"selector": "#agree", "field_type": "checkbox",
                 "source": "literal", "literal_value": "yes"},
            ],
            "submit_selector": "#go",
            "wait_for_url": "/done",
            "on_error": "fail",
            "max_retries": 2,
        }
    ],
}


# ---------------------------------------------------------------------------
# 1. Recipe CRUD roundtrip in a temp DB
# ---------------------------------------------------------------------------

def test_recipe_crud():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rpa-test-")
    os.close(fd)
    try:
        rid = recipes.create_recipe(dict(SAMPLE_RECIPE), db_path=db_path)
        check("crud: create returns 8-char id", isinstance(rid, str) and len(rid) == 8, rid)

        got = recipes.get_recipe(rid, db_path=db_path)
        check("crud: get returns name", got["name"] == "Test Recipe", got.get("name"))
        check("crud: get has recipe_id/created_at", "recipe_id" in got and "created_at" in got)

        items = recipes.list_recipes(db_path=db_path)
        check("crud: list finds 1", len(items) == 1, items)
        check("crud: list metadata", items[0]["step_count"] == 1
              and items[0]["base_url"] == "https://example.com", items[0])

        updated = dict(SAMPLE_RECIPE)
        updated["name"] = "Renamed"
        upd = recipes.update_recipe(rid, updated, db_path=db_path)
        check("crud: update renames", upd["name"] == "Renamed")
        check("crud: update preserves created_at",
              upd["created_at"] == got["created_at"])
        check("crud: update stamps updated_at", bool(upd.get("updated_at")))

        check("crud: delete", recipes.delete_recipe(rid, db_path=db_path) is True)
        check("crud: delete missing -> False",
              recipes.delete_recipe("nope", db_path=db_path) is False)
        try:
            recipes.get_recipe(rid, db_path=db_path)
            check("crud: get after delete raises KeyError", False)
        except KeyError:
            check("crud: get after delete raises KeyError", True)
    finally:
        os.unlink(db_path)


def test_recipe_validation():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rpa-test-")
    os.close(fd)
    try:
        try:
            recipes.create_recipe({"name": "x"}, db_path=db_path)  # missing base_url
            check("validation: missing base_url rejected", False)
        except Exception:
            check("validation: missing base_url rejected", True)
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 2. Engine action functions against the fake page
# ---------------------------------------------------------------------------

def test_fill_field_text_with_value_map():
    page = FakePage()
    engine.fill_field(
        page,
        {"selector": "#name", "field_type": "text", "source": "csv_column",
         "csv_column": "Name",
         "value_map": [{"from_val": "Male", "to_val": "male"}]},
        {"Name": "Male"},
        {"between_fields_ms": 0, "char_delay_ms": 0, "action_timeout_ms": 8000},
        job_id=None,
    )
    check("fill_field: value_map applied", page.filled.get("#name") == "male",
          page.filled)


def test_fill_field_checkbox_literal():
    page = FakePage()
    engine.fill_field(
        page,
        {"selector": "#agree", "field_type": "checkbox",
         "source": "literal", "literal_value": "yes"},
        {},
        {"between_fields_ms": 0, "char_delay_ms": 0, "action_timeout_ms": 8000},
        job_id=None,
    )
    check("fill_field: checkbox checked", page.checked.get("#agree") is True,
          page.checked)


def test_fill_field_char_delay_types():
    page = FakePage()
    engine.fill_field(
        page,
        {"selector": "#q", "field_type": "text", "source": "literal",
         "literal_value": "hi"},
        {},
        {"between_fields_ms": 0, "char_delay_ms": 1, "action_timeout_ms": 8000},
        job_id=None,
    )
    check("fill_field: char_delay types per char", page.typed.get("#q") == ["h", "i"],
          page.typed)


def test_execute_step():
    page = FakePage()
    ok, active = engine.execute_step(
        page, SAMPLE_RECIPE["flow"][0], {"Name": "Male"},
        SAMPLE_RECIPE["delay"], "job-test-step")
    check("execute_step: ok", ok is True)
    check("execute_step: navigated", page.navigated == ["https://example.com/form"],
          page.navigated)
    check("execute_step: filled via value_map", page.filled.get("#name") == "male",
          page.filled)
    check("execute_step: submit clicked", "#go" in page.clicked, page.clicked)
    check("execute_step: returns active page", active is page)


def test_execute_login_steps():
    page = FakePage()
    engine.execute_login_steps(
        page,
        [{"url": "https://example.com/login",
          "fields": [{"selector": "#u", "field_type": "text",
                      "source": "literal", "literal_value": "bob"}],
          "submit_selector": "#login",
          "wait_for_url": "/dash"}],
        SAMPLE_RECIPE["delay"], "job-test-login")
    check("login: navigated", "https://example.com/login" in page.navigated,
          page.navigated)
    check("login: field filled", page.filled.get("#u") == "bob", page.filled)
    check("login: submitted", "#login" in page.clicked, page.clicked)


# ---------------------------------------------------------------------------
# 3. run_job page-injection path — must NOT launch a browser
# ---------------------------------------------------------------------------

def test_run_job_injected_page_no_browser_launch():
    import playwright.sync_api as pw_sync
    orig = pw_sync.sync_playwright

    def _boom(*a, **k):
        raise AssertionError("browser launch attempted on injected-page path")

    pw_sync.sync_playwright = _boom
    page = FakePage()
    try:
        engine.run_job("job-inject-1", dict(SAMPLE_RECIPE), data_path=None,
                       page=page)
    finally:
        pw_sync.sync_playwright = orig

    check("run_job: status done", engine.get_status("job-inject-1") == "done",
          engine.get_status("job-inject-1"))
    summary = engine.get_summary("job-inject-1")
    check("run_job: summary 1 success", summary.get("success") == 1
          and summary.get("failed") == 0, summary)
    # NOTE: the empty row means the csv_column text field is correctly
    # skipped (upstream skips empty values); the literal checkbox still fills.
    check("run_job: injected page was driven",
          page.checked.get("#agree") is True and "#go" in page.clicked
          and page.navigated == ["https://example.com/form"],
          (page.checked, page.clicked, page.navigated))
    logs = engine.get_logs("job-inject-1")
    check("run_job: logs recorded", len(logs) > 5, len(logs))
    check("run_job: injection logged",
          any("injected page" in l for l in logs))


def test_run_job_with_csv_data(tmp_csv=None):
    import playwright.sync_api as pw_sync
    orig = pw_sync.sync_playwright
    pw_sync.sync_playwright = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("browser launch attempted"))
    fd, csv_path = tempfile.mkstemp(suffix=".csv", prefix="rpa-test-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("Name\nAlice\nBob\n")
        page = FakePage()
        try:
            engine.run_job("job-inject-csv", dict(SAMPLE_RECIPE),
                           data_path=csv_path, page=page)
        finally:
            pw_sync.sync_playwright = orig
        summary = engine.get_summary("job-inject-csv")
        check("run_job csv: 2 rows processed",
              summary.get("success") == 2 and summary.get("failed") == 0,
              summary)
        check("run_job csv: csv_column values filled",
              page.filled.get("#name") == "Bob", page.filled)
        check("run_job csv: row range respected",
              any("rows 1–2" in l or "rows 1-2" in l for l in engine.get_logs("job-inject-csv")))
    finally:
        os.unlink(csv_path)


# ---------------------------------------------------------------------------
# 4. Pending-action registry (human handoff protocol)
# ---------------------------------------------------------------------------

def test_pending_action_registry():
    jid = "job-pending-1"
    check("registry: no action initially",
          engine.get_pending_action(jid) is None)
    check("registry: answer with none pending -> False",
          engine.answer_action(jid, "x") is False)
    engine.set_pending_action(jid, {"type": "captcha", "question": "solve me"})
    pa = engine.get_pending_action(jid)
    check("registry: pending action visible", pa == {"type": "captcha", "question": "solve me"}, pa)
    check("registry: answer ok", engine.answer_action(jid, "abc123") is True)
    check("registry: response returned", engine.get_action_response(jid) == "abc123")
    engine.clear_action(jid)
    check("registry: cleared", engine.get_pending_action(jid) is None)
    check("registry: timeout constant is 300s", engine.PENDING_ACTION_TIMEOUT_S == 300)


def test_stop_request():
    jid = "job-stop-1"
    check("stop: not requested initially", engine.is_stop_requested(jid) is False)
    engine.request_stop(jid)
    check("stop: requested", engine.is_stop_requested(jid) is True)
    engine.reset_job_state(jid)
    check("stop: reset clears", engine.is_stop_requested(jid) is False)


# ---------------------------------------------------------------------------
# 5. Inspector against the fake page
# ---------------------------------------------------------------------------

def test_inspector():
    page = FakePage(tag_elements={
        "input": [
            {"type": "text", "name": "q", "id": "q",
             "placeholder": "Search", "value": "", "class": "x"},
            {"type": "checkbox", "name": "agree", "id": "agree",
             "placeholder": None, "value": "1", "class": None},
        ],
        "select": [
            {"name": "country", "id": "c",
             "options": [{"value": "us", "text": "USA"},
                         {"value": "uk", "text": "UK"}]},
        ],
        "textarea": [
            {"name": "msg", "id": "t", "placeholder": "Say hi"},
        ],
        "button": [
            {"inner_text": "  Go  ", "type": "submit", "id": "b", "class": "btn"},
        ],
    })
    page.url = "https://example.com/form"
    result = inspect_page(page)
    check("inspector: inputs", len(result["inputs"]) == 2
          and result["inputs"][0]["name"] == "q"
          and result["inputs"][0]["placeholder"] == "Search", result["inputs"])
    check("inspector: selects with options",
          len(result["selects"]) == 1
          and result["selects"][0]["options"][1] == {"value": "uk", "text": "UK"},
          result["selects"])
    check("inspector: textareas", result["textareas"][0]["name"] == "msg",
          result["textareas"])
    check("inspector: buttons stripped", result["buttons"][0]["text"] == "Go",
          result["buttons"])
    check("inspector: final_url", result["final_url"] == "https://example.com/form",
          result.get("final_url"))


# ---------------------------------------------------------------------------
# 6. RPAManager — offline surface (no browser)
# ---------------------------------------------------------------------------

def test_manager_offline():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rpa-test-")
    os.close(fd)
    try:
        mgr = RPAManager(db_path=db_path)
        rid = mgr.create("M1", dict(SAMPLE_RECIPE))
        check("manager: create", isinstance(rid, str) and len(rid) == 8, rid)
        check("manager: list", len(mgr.list()) == 1)
        check("manager: resolve by name", mgr.resolve_id("M1") == rid)
        check("manager: resolve by id", mgr.resolve_id(rid) == rid)
        try:
            mgr.resolve_id("nope")
            check("manager: resolve unknown raises", False)
        except KeyError:
            check("manager: resolve unknown raises", True)

        # run() with a missing profile must fail cleanly (no browser touched)
        job_id = mgr.run(rid, "no-such-profile")
        import time as _t
        for _ in range(50):
            st = mgr.job_status(job_id)
            if st["status"] in ("done", "error", "stopped"):
                break
            _t.sleep(0.2)
        check("manager: bad profile -> error status",
              st["status"] == "error", st["status"])
        check("manager: error summary mentions profile",
              "no-such-profile" in str(st["summary"].get("error", "")), st["summary"])

        try:
            mgr.job_status("nope")
            check("manager: unknown job raises", False)
        except KeyError:
            check("manager: unknown job raises", True)
        try:
            mgr.answer_action("nope", "x")
            check("manager: answer unknown job raises", False)
        except KeyError:
            check("manager: answer unknown job raises", True)
        try:
            mgr.stop_job("nope")
            check("manager: stop unknown job raises", False)
        except KeyError:
            check("manager: stop unknown job raises", True)

        check("manager: delete", mgr.delete(rid) is True)
        check("manager: list empty after delete", mgr.list() == [])
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------

def main():
    test_recipe_crud()
    test_recipe_validation()
    test_fill_field_text_with_value_map()
    test_fill_field_checkbox_literal()
    test_fill_field_char_delay_types()
    test_execute_step()
    test_execute_login_steps()
    test_run_job_injected_page_no_browser_launch()
    test_run_job_with_csv_data()
    test_pending_action_registry()
    test_stop_request()
    test_inspector()
    test_manager_offline()

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for name, detail in FAILED:
        print("  FAILED: %s — %s" % (name, detail))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
