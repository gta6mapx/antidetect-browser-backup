"""Live-page DOM inspector for RPA recipe building.

Adapted from python_rpa_ui (https://github.com/anandhu1228/python_rpa_ui,
Apache-2.0) ``backend/routers/inspect_router.py``'s ``inspect_page(page)``
function — the DOM-scraping logic is verbatim; see the reference copy at
``vendor/rpa-studio/inspect_router_reference.py``.

DEVIATION from upstream: the subprocess-templated ``INSPECTOR_SCRIPT``
(which launched its own stock Chromium via ``subprocess.run`` with
``repr()``-interpolated arguments) is dropped entirely. ``inspect_page``
runs directly against a live Playwright page — typically our Camoufox
profile page — so there is no extra browser launch and no string-templated
code execution.
"""


def inspect_page(page):
    """Scrape a live Playwright page for automatable elements.

    :param page: an open Playwright ``Page`` (already navigated).
    :return: ``{"inputs": [...], "selects": [...], "textareas": [...],
        "buttons": [...], "final_url": str}``.
    """
    inputs = []
    for el in page.locator("input").all():
        try:
            inputs.append({
                "type":        el.get_attribute("type") or "text",
                "name":        el.get_attribute("name"),
                "id":          el.get_attribute("id"),
                "placeholder": el.get_attribute("placeholder"),
                "value":       el.get_attribute("value"),
                "class":       el.get_attribute("class"),
            })
        except Exception:
            pass

    selects = []
    for el in page.locator("select").all():
        try:
            opts = []
            for opt in el.locator("option").all():
                try:
                    opts.append({"value": opt.get_attribute("value"), "text": opt.inner_text().strip()})
                except Exception:
                    pass
            selects.append({
                "name":    el.get_attribute("name"),
                "id":      el.get_attribute("id"),
                "options": opts,
            })
        except Exception:
            pass

    textareas = []
    for el in page.locator("textarea").all():
        try:
            textareas.append({
                "name":        el.get_attribute("name"),
                "id":          el.get_attribute("id"),
                "placeholder": el.get_attribute("placeholder"),
            })
        except Exception:
            pass

    buttons = []
    for el in page.locator("button").all():
        try:
            buttons.append({
                "text":  el.inner_text().strip(),
                "type":  el.get_attribute("type"),
                "id":    el.get_attribute("id"),
                "class": el.get_attribute("class"),
            })
        except Exception:
            pass

    result = {"inputs": inputs, "selects": selects,
              "textareas": textareas, "buttons": buttons}
    try:
        result["final_url"] = page.url
    except Exception:
        result["final_url"] = ""
    return result


def inspect_url(profile_name, url, login_steps=None, headless=True,
                db_path=None):
    """Launch a profile, navigate to ``url`` (optionally running login
    steps first), and return :func:`inspect_page` output. The browser is
    always closed afterwards.

    :param profile_name: our profile name to launch.
    :param url: page URL to inspect.
    :param login_steps: optional upstream-format login steps
        (``[{"url":..., "fields":[{"selector":..., "literal_value":...}],
        "submit_selector":..., "wait_for_url":...}]``).
    :param headless: launch headless (default True).
    :param db_path: profile DB path override (for tests).
    """
    from src.profiles.manager import ProfileManager
    from src.browser.launcher import launch_profile

    try:
        persona = ProfileManager(db_path).get(profile_name)
    except KeyError:
        raise RuntimeError(f"profile not found: {profile_name!r}")

    launched = launch_profile(persona, headless=headless)
    try:
        page = launched.page
        for step in (login_steps or []):
            step_url = step.get("url")
            if step_url:
                page.goto(step_url)
                page.wait_for_load_state("networkidle", timeout=15000)
            for f in step.get("fields", []):
                try:
                    page.fill(f["selector"], f.get("literal_value", ""))
                except Exception:
                    pass
            if step.get("submit_selector"):
                try:
                    page.click(step["submit_selector"])
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
            if step.get("wait_for_url"):
                try:
                    page.wait_for_url(f"**{step['wait_for_url']}**", timeout=15000)
                except Exception:
                    pass
        page.goto(url)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        return inspect_page(page)
    finally:
        try:
            launched.close()
        except Exception:
            pass
