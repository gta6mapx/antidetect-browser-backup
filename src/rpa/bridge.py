"""RPAForge ↔ Legacy engine bridge.

Connects RPAForge's WebUI activity model
(https://github.com/chelslava/rpaforge, Apache-2.0)
with our proven python_rpa_ui-based run engine (src/rpa/engine.py).

This lets users build workflows from RPAForge's 17 WebUI activities
in the Activities tab, and execute them through our Camoufox/Patchright
profile pages via the existing battle-tested engine.

Activity → Step mapping:
  RPAForge activity      →  engine step type
  ─────────────────────────────────────────
  Navigate To            →  goto
  Click                  →  click
  Input Text             →  fill
  Get Text               →  get_text
  Wait For Element       →  wait_for
  Screenshot             →  screenshot
  Execute JavaScript     →  evaluate
  Select Option          →  select
  Check Checkbox         →  check
  Upload File            →  upload
  Handle Dialog          →  dialog
  Get Attribute          →  get_attribute
  Scroll To              →  scroll
  Hover                  →  hover
  Press Key              →  press
  Open Browser           →  (handled by profile launch)
  Close Browser          →  (handled by profile stop)
"""

# RPAForge activity name → our engine step type.
ACTIVITY_TO_STEP = {
    "Navigate To": "goto",
    "Click": "click",
    "Input Text": "fill",
    "Get Text": "get_text",
    "Wait For Element": "wait_for",
    "Screenshot": "screenshot",
    "Execute JavaScript": "evaluate",
    "Select Option": "select",
    "Check Checkbox": "check",
    "Upload File": "upload",
    "Handle Dialog": "dialog",
    "Get Attribute": "get_attribute",
    "Scroll To": "scroll",
    "Hover": "hover",
    "Press Key": "press",
}

# RPAForge activity → our step field mapping.
# Each entry: {activity_param: step_field}
ACTIVITY_PARAMS = {
    "Navigate To": {"url": "url"},
    "Click": {"selector": "selector"},
    "Input Text": {"selector": "selector", "text": "value"},
    "Get Text": {"selector": "selector"},
    "Wait For Element": {"selector": "selector", "timeout": "timeout"},
    "Screenshot": {"path": "path"},
    "Execute JavaScript": {"script": "script"},
    "Select Option": {"selector": "selector", "value": "value"},
    "Check Checkbox": {"selector": "selector", "checked": "checked"},
    "Upload File": {"selector": "selector", "file": "file"},
    "Handle Dialog": {"action": "action"},
    "Get Attribute": {"selector": "selector", "attribute": "attribute"},
    "Scroll To": {"selector": "selector"},
    "Hover": {"selector": "selector"},
    "Press Key": {"key": "key"},
}


def activity_to_step(activity_name, params=None):
    """Convert an RPAForge activity + params to an engine step dict.

    Returns None for browser lifecycle activities (handled by profile
    launch/stop, not by the step engine).
    """
    params = params or {}
    if activity_name in ("Open Browser", "Close Browser"):
        return None
    step_type = ACTIVITY_TO_STEP.get(activity_name)
    if not step_type:
        raise ValueError(f"Unknown activity: {activity_name}")
    field_map = ACTIVITY_PARAMS.get(activity_name, {})
    step = {"type": step_type}
    for act_param, step_field in field_map.items():
        if act_param in params:
            step[step_field] = params[act_param]
    return step


def activities_to_recipe(name, activities, description=""):
    """Build a legacy recipe dict from a list of RPAForge activities.

    Each activity: {"name": "Click", "params": {"selector": "#btn"}}.
    Returns a recipe dict compatible with src/rpa/recipes.py.
    """
    steps = []
    for act in activities:
        step = activity_to_step(act["name"], act.get("params", {}))
        if step:
            steps.append(step)
    return {
        "name": name,
        "description": description or f"Built from RPAForge activities ({len(steps)} steps)",
        "engine": "rpaforge",
        "steps": steps,
    }


def recipe_uses_rpaforge(recipe):
    """Check if a recipe was built from RPAForge activities."""
    return recipe.get("engine") == "rpaforge"
