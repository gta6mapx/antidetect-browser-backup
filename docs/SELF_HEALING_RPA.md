# Self-healing RPA recipes

`src/rpa/self_healing.py` adds selector-level self-healing on top of our
existing RPA engine (`src/rpa/engine.py`). When a site changes its markup,
a recipe no longer dies on the first missed selector: it falls back through
backup selectors, captures a failure snapshot, attempts a deterministic
repair, and can roll back to a previous working recipe version.

Architecture pattern learned from
[the-ks/self-healing-code-rpa](https://github.com/the-ks/self-healing-code-rpa)
(MIT); the implementation here is original, no code copied, and — unlike
that experimental MVP — there is **no LLM anywhere in the path**. Repair is
purely deterministic: re-query by visible text, ARIA role, or attribute
variations.

## Recipe format (YAML)

```yaml
meta:
  name: demo-login            # recipe identity (used for version storage)
  version: 3                  # bumped on every promoted repair
  description: Logs in and waits for the dashboard.

steps:
  - id: open_login            # unique within the recipe
    action: goto              # any engine activity: goto/click/fill/get_text/
                              # wait_for/screenshot/evaluate/select/check/
                              # upload/dialog/get_attribute/scroll/hover/press
    url: https://example.com/login

  - id: fill_username
    action: fill
    value: demo-user           # literal value (fills play the same role as
                              # the legacy engine's field_mappings)
    primary: "#username"      # tried first
    fallbacks:                # tried in order when primary misses
      - "[name='username']"
      - "//input[@id='username']"          # XPath (starts with / or ()
      - {role: "textbox", name: "Username"}  # ARIA-role selector
    repair:                   # optional hints for the repair step
      text_hint: "Username"   # re-query by visible text content
      role: textbox           # re-query by ARIA role (+ optional name:)
      name: Username

  - id: click_signin
    action: click
    primary: "button[type=submit]"
    fallbacks:
      - "input[type=submit]"
      - "//button[contains(., 'Sign in')]"
      - {role: "button", name: "Sign in"}
    repair:
      text_hint: "Sign in"

  - id: wait_dashboard
    action: wait_for
    primary: "#dashboard"
    fallbacks:
      - "[data-testid='dashboard']"
    timeout_ms: 10000         # engine also accepts timeout:
```

Selector spec forms (in `primary` / `fallbacks`):

| Form | Example | Meaning |
|---|---|---|
| CSS string | `"#login"`, `".btn.primary"` | `page.locator(...)` |
| XPath string | `"//button[@id='x']"` | `page.locator("xpath=...")` |
| `{role:, name:}` | `{role: "button", name: "OK"}` | `page.get_by_role(...)` |
| `{text:}` | `{text: "Sign in"}` | `page.get_by_text(...)` |
| `{testid:}` | `{testid: "submit"}` | `page.get_by_test_id(...)` |

Actions `goto`, `press`, `screenshot`, `evaluate`, `dialog` need no selector.

## How it runs

`SelfHealingRunner` wraps the existing engine — it does **not** fork it.
Each step is translated to the engine's activity-step format
(`{"type": "click", "selector": ...}`) and executed via
`engine.execute_step`, so all action semantics stay in `engine.py`.

Per step:

1. **Resolve** — probe `primary` with a read-only query, then each fallback
   in order. First hit wins; the probe order is recorded.
2. **Execute** — run the action through the engine with the resolved selector.
3. **Snapshot** — if every selector misses, capture a failure snapshot:
   page URL, HTML excerpt (first 4000 chars), screenshot, and the full list
   of attempted selectors with their errors. Written to
   `~/.antidetect-browser/rpa/self_healing/snapshots/<run>_<step>.json`.
4. **Sandbox repair** — deterministic candidates are generated from the
   step's `repair:` hints and from the primary selector's shape:
   - re-query by visible text (`text_hint`)
   - re-query by ARIA role (+ name)
   - attribute variations (`#id` → `[id="id"]`, `.cls` → `[class*="cls"]`,
     `[name="x"]` → `[name*="x"]`)
   - XPath translation (`#id` → `//*[@id='id']`)

   Every candidate is validated **in the sandbox**: read-only DOM probes
   only (`count()` / `wait_for(state="attached")`). Repair never clicks,
   fills, presses keys, or navigates — it has no side effects. A validated
   candidate becomes a `RepairPatch` (selector-only; never touches actions
   or step logic). By default patches are *reported*, not applied —
   `promote_repairs=True` (or the `promote_patch()` helper) applies one and
   bumps the recipe version.
5. **Versioned rollback** — the loaded recipe version is snapshotted before
   the run; every promoted repair creates `vN+1` while keeping all older
   versions under
   `~/.antidetect-browser/rpa/self_healing/versions/<name>/`. If a recipe
   fails all selectors on 3 consecutive runs (configurable), the runner
   automatically reverts to the previous working version. `rollback_recipe()`
   does it manually.

## Usage

```python
from src.rpa.self_healing import SelfHealingRunner

runner = SelfHealingRunner.from_yaml(
    "src/rpa/recipes/self_healing_demo_login.yaml",
    page,                       # injected Camoufox profile page
    job_id="login-job-1",
    promote_repairs=False,      # report repairs; operator promotes
)
summary = runner.run()
print(summary)
# {"run_id": ..., "recipe": "self_healing_demo_login", "version": 1,
#  "success": True, "failed_step": None, "selector_attempts": 7,
#  "snapshots": 0, "repairs_proposed": 0, "rolled_back_to": None}
```

Operator review lane after a run with `repairs_proposed > 0`:

```python
from src.rpa.self_healing import promote_patch, rollback_recipe

for patch in runner.patches:
    print(patch.step_id, patch.strategy, patch.proposed, patch.reason)
    promote_patch(patch)          # sandbox-validated only; bumps version

rollback_recipe("demo-login")     # revert to previous working version
```

## Integration with the existing runner

The coordinator wires it into `RPAManager.run()` / `engine.run_job()`
(wherever self-healing recipes should be accepted) with:

```python
from src.rpa.self_healing import SelfHealingRunner
runner = SelfHealingRunner.from_yaml(recipe_path, page, job_id=job_id)
summary = runner.run()
```

The default `step_executor` already delegates every action to
`engine.execute_step` in activity format, so no engine changes are needed —
a self-healing recipe is just a YAML front-end with its own selector
resolution layered in front of the existing executor.

## Demo recipes

- `src/rpa/recipes/self_healing_demo_login.yaml` — login flow (fill, click,
  wait_for) with text/ARIA repair hints.
- `src/rpa/recipes/self_healing_demo_search.yaml` — search flow showing
  fallback ordering and snapshot capture.

## Caveats

- Repair is selector-level only by design. If the *action* fails after a
  selector resolved (e.g. click intercepted), a snapshot is captured but no
  repair is attempted — that's a page-state problem, not a selector problem.
- The sandbox validates candidates with DOM presence probes; it does not
  re-run the step (re-running could double-submit forms).
- Version storage is local (`~/.antidetect-browser/rpa/self_healing`).
  Point `work_dir` elsewhere for tests or per-tenant isolation.
