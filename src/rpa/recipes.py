"""RPA recipe storage.

Recipe schema (``Recipe`` / ``FlowStep`` / ``DelayConfig``) ported from
python_rpa_ui (https://github.com/anandhu1228/python_rpa_ui, Apache-2.0)
``backend/routers/recipe_router.py`` — model fields are verbatim; only the
storage layer changed.

DEVIATION from upstream: recipes were stored as flat JSON files
(``storage/recipes/<8-char-uuid>.json``). Here they live in our existing
SQLite database (same file ``ProfileManager`` uses) in the ``rpa_recipes``
table: ``id TEXT PK, name TEXT, description TEXT, recipe_json TEXT,
created_at TEXT, updated_at TEXT``.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ──────────────────────────────────────────────────────────────
#  Recipe schema — field-for-field port of upstream's Pydantic models
#  (backend/routers/recipe_router.py). Kept verbatim so recipes stay
#  interchangeable with the upstream format.
# ──────────────────────────────────────────────────────────────

class FlowStep(BaseModel):
    step_id: str
    label: str
    url: str                          # page URL (can be relative)

    # Field mappings: { field_selector -> csv_column | literal_value }
    field_mappings: List[Dict[str, Any]] = []

    # Steps to execute just for the inspector to reach this page
    inspection_steps: Optional[List[Dict[str, Any]]] = None

    # CAPTCHA / Human Handoff
    captcha_image_selector: Optional[str] = None
    captcha_input_selector: Optional[str] = None

    submit_selector: Optional[str] = None
    wait_for_url: Optional[str] = None      # URL substring to wait for after submit
    wait_for_selector: Optional[str] = None
    skip_if_no_data: bool = False           # skip step if all mapped columns are empty
    opens_new_tab: bool = False             # click opens a new browser tab — switch to it

    # Error handling
    error_selector: Optional[str] = None           # CSS selector for an element that signals failure
    error_text_contains: Optional[str] = None       # only match error_selector when its text contains this string
    on_error: Optional[str] = "fail"               # "fail" | "retry" | "ask"
    max_retries: Optional[int] = 2                 # max retry attempts when on_error is retry or ask
    dismiss_dialogs: Optional[bool] = False        # auto-dismiss native alert/confirm/prompt dialogs
    dialog_action: Optional[str] = "accept"        # "accept" | "dismiss" — used when dismiss_dialogs is true


class DelayConfig(BaseModel):
    between_records_ms: int = 800       # ms between each CSV row
    between_fields_ms: int = 100        # ms between filling each field
    between_steps_ms: int = 300         # ms between flow steps
    char_delay_ms: int = 0              # ms between each character typed (0 = instant fill)
    page_load_timeout_ms: int = 15000
    action_timeout_ms: int = 8000


class Recipe(BaseModel):
    name: str
    description: Optional[str] = ""
    base_url: str
    flow: List[FlowStep] = []
    delay: DelayConfig = DelayConfig()
    # Login steps (executed once before the flow loop)
    login_steps: Optional[List[Dict[str, Any]]] = None


# ──────────────────────────────────────────────────────────────
#  SQLite storage — same database file our ProfileManager uses.
# ──────────────────────────────────────────────────────────────

def _default_db_path() -> str:
    """Reuse the profile DB path convention (see src/profiles/manager.py)."""
    from src.profiles.manager import _default_db_path as _profiles_db_path
    return _profiles_db_path()


def _connect(db_path: Optional[str]):
    path = db_path or _default_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(db_path: Optional[str] = None):
    with _connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS rpa_recipes (
                   id TEXT PRIMARY KEY,
                   name TEXT NOT NULL,
                   description TEXT DEFAULT '',
                   recipe_json TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   updated_at TEXT
               )"""
        )
        conn.commit()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a recipe dict against the schema; return the normalized dict."""
    return Recipe(**data).model_dump()


# ──────────────────────────────────────────────────────────────
#  CRUD
# ──────────────────────────────────────────────────────────────

def create_recipe(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    """Validate and store a recipe; return its new id (8-char uuid, upstream format)."""
    _ensure_table(db_path)
    recipe = _validate(data)
    recipe_id = str(uuid.uuid4())[:8]
    recipe["recipe_id"] = recipe_id
    recipe["created_at"] = _now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO rpa_recipes (id, name, description, recipe_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (recipe_id, recipe["name"], recipe.get("description", ""),
             json.dumps(recipe), recipe["created_at"]),
        )
        conn.commit()
    return recipe_id


def get_recipe(recipe_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Return the full recipe dict. Raises KeyError if not found."""
    _ensure_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT recipe_json FROM rpa_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
    if row is None:
        raise KeyError(recipe_id)
    return json.loads(row["recipe_json"])


def list_recipes(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return recipe metadata (id, name, description, base_url, created_at, step_count)."""
    _ensure_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, description, recipe_json, created_at, updated_at"
            " FROM rpa_recipes ORDER BY created_at"
        ).fetchall()
    out = []
    for row in rows:
        try:
            data = json.loads(row["recipe_json"])
        except Exception:
            data = {}
        out.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"] or "",
            "base_url": data.get("base_url", ""),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "step_count": len(data.get("flow", [])),
        })
    return out


def update_recipe(recipe_id: str, data: Dict[str, Any],
                  db_path: Optional[str] = None) -> Dict[str, Any]:
    """Replace a recipe's content (validates first). Preserves created_at."""
    _ensure_table(db_path)
    existing = get_recipe(recipe_id, db_path)  # raises KeyError if missing
    recipe = _validate(data)
    recipe["recipe_id"] = recipe_id
    recipe["created_at"] = existing.get("created_at", _now_iso())
    recipe["updated_at"] = _now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE rpa_recipes SET name = ?, description = ?, recipe_json = ?,"
            " updated_at = ? WHERE id = ?",
            (recipe["name"], recipe.get("description", ""),
             json.dumps(recipe), recipe["updated_at"], recipe_id),
        )
        conn.commit()
    return recipe


def delete_recipe(recipe_id: str, db_path: Optional[str] = None) -> bool:
    """Delete a recipe. Returns True if one was deleted."""
    _ensure_table(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM rpa_recipes WHERE id = ?", (recipe_id,))
        conn.commit()
    return cur.rowcount > 0
