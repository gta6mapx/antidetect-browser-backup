# RPA — recipe-driven browser automation (Phase 7)

Recipes describe multi-step browser flows (open page → fill fields → submit →
check result), optionally repeated once per row of a CSV/XLSX data file.
The run engine drives **our Camoufox profile pages** — no separate stock
browser is launched.

Adapted from [python_rpa_ui](https://github.com/anandhu1228/python_rpa_ui)
(Apache-2.0); attribution and verbatim reference copies live in
`vendor/rpa-studio/`. Adapted files carry "DEVIATION from upstream" comments
where behavior differs.

## Modules

| Module | Role |
|---|---|
| `src/rpa/engine.py` | Run engine: `fill_field`, `execute_step`, `execute_login_steps`, `run_job`. Takes an injected Playwright `page`. |
| `src/rpa/recipes.py` | Pydantic `Recipe` / `FlowStep` / `DelayConfig` + SQLite CRUD (`rpa_recipes` table in the profiles DB). |
| `src/rpa/manager.py` | `RPAManager`: recipe CRUD, `run()` on a profile (daemon thread), `job_status`, `stop_job`, `answer_action`. |
| `src/rpa/inspector.py` | `inspect_page(page)` — scrape a live page for inputs/selects/textareas/buttons (no-code selector capture). |

## Recipe format

```jsonc
{
  "name": "Site Registration",
  "description": "",
  "base_url": "https://example.com",   // reset target after a failed row
  "delay": {
    "between_records_ms": 800,   // between CSV rows
    "between_fields_ms": 100,    // between fields
    "between_steps_ms": 300,     // between flow steps
    "char_delay_ms": 0,          // >0 → human typing, per-char delay
    "page_load_timeout_ms": 15000,
    "action_timeout_ms": 8000
  },
  "login_steps": [                  // run ONCE before the row loop
    {
      "url": "https://example.com/login",
      "fields": [
        { "selector": "[name=\"username\"]", "field_type": "text",
          "source": "literal", "literal_value": "user" }
      ],
      "submit_selector": "button[type=\"submit\"]",
      "wait_for_url": "/dashboard/"
    }
  ],
  "flow": [
    {
      "step_id": "step_1",
      "label": "Registration Form",
      "url": "https://example.com/signup/",
      "field_mappings": [
        { "selector": "input[name=\"full_name\"]", "field_type": "text",
          "source": "csv_column", "csv_column": "Name",
          "value_map": [{"from_val": "Male", "to_val": "male"}] },
        { "selector": "input[name=\"photo\"]", "field_type": "file_upload",
          "source": "csv_column", "csv_column": "PhotoPath",
          "file_accept": "image", "file_max_mb": 2 },
        { "selector": "input[name=\"newsletter\"]", "field_type": "checkbox",
          "source": "literal", "literal_value": "yes" }
      ],
      "submit_selector": "button[type=\"submit\"]",
      "wait_for_url": "/dashboard/",     // or "wait_for_selector"
      "skip_if_no_data": false,
      "opens_new_tab": false,
      "captcha_image_selector": "",      // human handoff: screenshot → operator solves
      "captcha_input_selector": "",
      "error_selector": "#send_err:not(:empty)",
      "error_text_contains": "",
      "on_error": "fail",                // "fail" | "retry" | "ask" (operator)
      "max_retries": 2,
      "dismiss_dialogs": false,
      "dialog_action": "accept"
    }
  ]
}
```

### Field types (13)

`text` `password` `email` `tel` `number` `textarea` `select` `radio`
`checkbox` `click` `human_input` `split_fill` `file_upload`

### Value sources

- `csv_column` — value from the data-file column `csv_column` (row loop).
- `literal` — fixed value from `literal_value`.
- `human_input` — pauses the job (≤5 min) and asks the operator; answered via
  `POST /api/rpa/jobs/{id}/answer`, `rpa status`, or the dashboard.

### File uploads

`file_source`: `server_path` (default, absolute path on this machine) |
`local_disk` (path on this machine, staged through our temp dir) |
`external_url` (downloaded first). Constraints: `file_accept` =
`image` | `pdf` | `any`; `file_max_mb` size cap.

### Human-in-the-loop

`human_input` fields, captcha handoffs, and `on_error: "ask"` pause the worker
for up to 5 minutes waiting for an operator answer (`answer_action`). If no
answer arrives, the row fails with a timeout error.

## Usage

CLI:
```
rpa list
rpa run <recipe_id_or_name> --profile <name> [--data file.csv] [--headless]
rpa status <job_id>
```

Python:
```python
from src.rpa import RPAManager
m = RPAManager()
rid = m.create("signup", {...recipe dict...})
job = m.run(rid, "client-acme-01", data_path="leads.csv")
print(m.job_status(job))
m.answer_action(job, "solution-text")   # answer a captcha/human_input
m.stop_job(job)
```

GUI: the dashboard has an **RPA** section (recipe list → pick profile → run →
watch job status, answer pending prompts).

## Key deviations from upstream

1. **Browser**: recipes run on our Camoufox profile pages (injected `page`);
   upstream launched its own stock headless Chromium per run. The engine keeps
   a documented stock-Chromium fallback when no page is injected.
2. **Storage**: recipes in SQLite (`rpa_recipes`), not flat JSON files.
3. **Job store**: in-process registry; no disk log files, no `job_store.py`.
4. **Inspector**: runs against a live profile page; the subprocess +
   string-templated script is gone.
5. **Auth**: upstream's unsalted-SHA-256 `auth.py` is dropped; our dashboard
   runs local-only behind the existing control server.
6. **Data file optional**: `data_path=None` runs the flow once with an empty
   row (upstream required a CSV/XLSX).
