# Reference: DOM-scraping portion of inspect_router.py
# from https://github.com/anandhu1228/python_rpa_ui (Apache-2.0).
# Copied verbatim on 2026-10-08 for reference. The live adaptation
# lives in src/rpa/inspector.py (no subprocess, no browser launch);
# it is NOT imported by our code.

def inspect_page(page):
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

    return {"inputs": inputs, "selects": selects, "textareas": textareas, "buttons": buttons}


