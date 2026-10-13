"""
RPA run engine.

Adapted from python_rpa_ui (https://github.com/anandhu1228/python_rpa_ui,
Apache-2.0; vendored commit d00f179) ``backend/workers/playwright_worker.py``.
See ``vendor/rpa-studio/ATTRIBUTION.md`` and the verbatim copy at
``vendor/rpa-studio/playwright_worker.py``.

DEVIATIONS from upstream (all documented inline):
 1. ``run_job(..., page=None, log_cb=None)`` — when a Playwright ``page`` is
    injected (our Camoufox profile page), browser launch and teardown are
    skipped and no video is recorded. When ``page`` is None, falls back to
    upstream behavior (stock headless Chromium + video recording).
 2. Upstream's ``backend/workers/job_store.py`` (in-memory + disk log files)
    is replaced by a small in-process, thread-safe registry in this module
    (same protocol: pending actions with <=5min operator handoff, stop
    requests, per-job logs/summaries/statuses). Nothing is written to disk.
 3. ``data_path`` may be None: the flow then runs once with an empty row
    (upstream required a CSV/XLSX file).
 4. Storage dirs moved from the repo-local ``storage/`` to
    ``~/.antidetect-browser/rpa/``.
 5. The ``local_disk`` file_source caveat about Docker temp mounts is
    rewritten: paths are resolved against this machine's filesystem.

Everything else — ``fill_field``, ``execute_step``, ``execute_login_steps``,
``resolve_frame``, ``check_error``, ``load_data_file``, ``human_delay``,
``FieldError`` — is behaviorally identical to upstream.
"""
import csv
import json
import os
import threading
import time
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ──────────────────────────────────────────────────────────────
#  DEVIATION from upstream: in-process job registry.
#  Replaces upstream's backend/workers/job_store.py (in-memory + disk
#  log files) with a small thread-safe registry. Same public protocol:
#  pending human actions with a <=5-minute operator handoff, stop
#  requests, per-job logs / summaries / statuses. Nothing is written to
#  disk here — the RPAManager/GUI layers handle any persistence.
# ──────────────────────────────────────────────────────────────

PENDING_ACTION_TIMEOUT_S = 300  # 5 minutes, same as upstream's polling loop

_lock = threading.Lock()


def _execute_activity_step(page, step: Dict, job_id: str):
    """Execute an RPAForge-style activity step.

    Simple format: {"type": "click", "selector": "#btn", ...}.
    See src/rpa/bridge.py for the activity → step mapping.
    Returns (success: bool, active_page).
    """
    stype = step.get("type")
    sel = step.get("selector", "")
    try:
        if stype == "goto":
            page.goto(step.get("url", ""))
            page.wait_for_load_state("networkidle", timeout=15000)
        elif stype == "click":
            page.locator(sel).first.click(timeout=8000)
        elif stype == "fill":
            page.locator(sel).first.fill(step.get("value", ""), timeout=8000)
        elif stype == "get_text":
            txt = page.locator(sel).first.inner_text(timeout=8000)
            log(job_id, f"    → Got text: {txt[:100]}")
        elif stype == "wait_for":
            page.locator(sel).first.wait_for(state="visible",
                timeout=int(step.get("timeout", 8000)))
        elif stype == "screenshot":
            page.screenshot(path=step.get("path", "/tmp/rpa_shot.png"))
        elif stype == "evaluate":
            page.evaluate(step.get("script", ""))
        elif stype == "select":
            page.locator(sel).first.select_option(step.get("value", ""))
        elif stype == "check":
            loc = page.locator(sel).first
            if step.get("checked", True): loc.check()
            else: loc.uncheck()
        elif stype == "upload":
            page.locator(sel).first.set_input_files(step.get("file", ""))
        elif stype == "dialog":
            # Dialog handling is set up via page.on("dialog") elsewhere;
            # this step just logs the intent.
            log(job_id, f"    → Dialog action: {step.get('action', 'accept')}")
        elif stype == "get_attribute":
            val = page.locator(sel).first.get_attribute(
                step.get("attribute", ""), timeout=8000)
            log(job_id, f"    → Attribute: {val}")
        elif stype == "scroll":
            page.locator(sel).first.scroll_into_view_if_needed(timeout=8000)
        elif stype == "hover":
            page.locator(sel).first.hover(timeout=8000)
        elif stype == "press":
            page.keyboard.press(step.get("key", "Enter"))
        log(job_id, f"    ✓ [{stype}] {sel or step.get('url', '')}")
        return True, page
    except Exception as e:
        log(job_id, f"    ✗ [{stype}] failed: {e}")
        return False, page
_logs: Dict[str, List[str]] = {}
_status: Dict[str, str] = {}
_summaries: Dict[str, Dict] = {}
_pending: Dict[str, Dict[str, Any]] = {}  # job_id -> {"action": dict, "answer": ...}
_stop_flags: set = set()
_log_callbacks: Dict[str, Callable[[str], None]] = {}


def _rpa_home() -> Path:
    """Our data dir, honoring ANTIDETECT_HOME (see src.paths).

    DEVIATION: upstream used the repo-local ``storage/``.
    """
    from src import paths as _paths
    home = Path(_paths.rpa_dir(create=True))
    return home


def append_log(job_id: str, msg: str):
    """Store a log line for a job and fan it out to any live callback."""
    with _lock:
        _logs.setdefault(job_id, []).append(msg)
        cb = _log_callbacks.get(job_id)
    if cb is not None:
        try:
            cb(msg)
        except Exception:
            pass


def get_logs(job_id: str) -> List[str]:
    """Return all stored log lines for a job (oldest first)."""
    with _lock:
        return list(_logs.get(job_id, []))


def set_status(job_id: str, status: str):
    with _lock:
        _status[job_id] = status


def get_status(job_id: str) -> str:
    with _lock:
        return _status.get(job_id, "unknown")


def set_summary(job_id: str, summary: Dict):
    with _lock:
        _summaries[job_id] = summary


def get_summary(job_id: str) -> Dict:
    with _lock:
        return dict(_summaries.get(job_id, {}))


def request_stop(job_id: str):
    """Ask a running job to stop after the current row (checked per row)."""
    with _lock:
        _stop_flags.add(job_id)


def is_stop_requested(job_id: str) -> bool:
    with _lock:
        return job_id in _stop_flags


def set_pending_action(job_id: str, action: Dict):
    """Register a human-handoff question; the worker polls for an answer."""
    with _lock:
        _pending[job_id] = {"action": dict(action), "answer": None}


def get_pending_action(job_id: str) -> Optional[Dict]:
    """Return the current pending action dict (or None). For GUI/CLI polling."""
    with _lock:
        entry = _pending.get(job_id)
        return dict(entry["action"]) if entry else None


def get_action_response(job_id: str):
    with _lock:
        entry = _pending.get(job_id)
        return entry.get("answer") if entry else None


def answer_action(job_id: str, answer: str) -> bool:
    """Operator answers a pending human-handoff question.

    Returns False when no action is pending for the job.
    """
    with _lock:
        entry = _pending.get(job_id)
        if entry is None:
            return False
        entry["answer"] = answer
        return True


def clear_action(job_id: str):
    with _lock:
        _pending.pop(job_id, None)


def reset_job_state(job_id: str):
    """Clear any stale registry state for a job id before a fresh run."""
    with _lock:
        _logs.pop(job_id, None)
        _status.pop(job_id, None)
        _summaries.pop(job_id, None)
        _pending.pop(job_id, None)
        _stop_flags.discard(job_id)

# DEVIATION from upstream: temp uploads live under our rpa home dir,
# not the repo-local storage/ (no Docker bind-mount here).
TEMP_ATTACHMENTS_DIR = _rpa_home() / "temp_attachments"
TEMP_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# ──────────────────────────────────────────────────────────────
#  Structured log helpers
#  Every line is either a plain string (developer log) OR a
#  JSON-encoded envelope:  {"_t": "<type>", ...fields}
#  The frontend detects lines starting with {"_t": and routes
#  them into the "user" view; everything else goes to "dev" view.
# ──────────────────────────────────────────────────────────────

def log(job_id: str, msg: str):
    """Raw developer log line."""
    append_log(job_id, msg)
    print(msg, flush=True)


def ulog(job_id: str, event_type: str, **kwargs):
    """Structured user-friendly log event (JSON envelope)."""
    payload = json.dumps({"_t": event_type, **kwargs}, ensure_ascii=False)
    append_log(job_id, payload)
    print(payload, flush=True)


def load_data_file(path: str) -> List[Dict[str, str]]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        if not HAS_OPENPYXL:
            raise RuntimeError("openpyxl not installed — cannot read Excel files")
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h) if h is not None else "" for h in rows[0]]
        result = []
        for row in rows[1:]:
            result.append({
                headers[i]: (str(row[i]) if row[i] is not None else "")
                for i in range(len(headers))
            })
        wb.close()
        return result
    else:
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def resolve_value(mapping: Dict, row: Dict[str, str]) -> str:
    source = mapping.get("source", "csv_column")
    if source == "literal":
        val = mapping.get("literal_value", "")
    elif source == "human_input":
        return ""
    else:
        col = mapping.get("csv_column", "")
        val = row.get(col, "").strip()

    value_map = mapping.get("value_map", [])
    if value_map:
        for vmap in value_map:
            if val == vmap.get("from_val"):
                return vmap.get("to_val")

    return val


def human_delay(ms: int, jitter_pct: float = 0.2):
    if ms <= 0:
        return
    jitter = ms * jitter_pct * (random.random() * 2 - 1)
    actual = max(10, ms + jitter)
    time.sleep(actual / 1000.0)


# ──────────────────────────────────────────────────────────────
#  iframe resolution helper
#  Returns the page/frame object that owns the given selector.
#  Checks the main page first, then all frames (iframes).
# ──────────────────────────────────────────────────────────────

def resolve_frame(page, selector: str, timeout_ms: int = 4000):
    """Return (frame_or_page, locator) for the first frame that has the selector."""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="attached", timeout=timeout_ms)
        return page, loc
    except Exception:
        pass
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            loc = frame.locator(selector).first
            loc.wait_for(state="attached", timeout=timeout_ms)
            return frame, loc
        except Exception:
            continue
    return page, page.locator(selector).first


def check_error(page, step: Dict, action_to: int):
    """Check for a page error after submit using error_selector.
    Returns (found: bool, error_text: str).
    Checks main page then iframes. Only matches visible elements.
    If error_text_contains is set, also checks inner text."""
    error_sel = step.get("error_selector", "")
    if not error_sel:
        return False, ""
    text_contains = step.get("error_text_contains", "")
    short_to = min(action_to, 3000)
    for ctx in [page] + [fr for fr in page.frames if fr != page.main_frame]:
        try:
            loc = ctx.locator(error_sel).first
            loc.wait_for(state="visible", timeout=short_to)
            inner = ""
            try:
                inner = loc.inner_text(timeout=short_to).strip()
            except Exception:
                pass
            if text_contains and text_contains not in inner:
                continue
            return True, inner
        except Exception:
            continue
    return False, ""


# ──────────────────────────────────────────────────────────────
#  Field-filling logic
# ──────────────────────────────────────────────────────────────

def fill_field(page, field_cfg: Dict, row: Dict[str, str], delay: Dict, job_id: str = None, temp_files: list = None):
    selector   = field_cfg.get("selector", "")
    field_type = field_cfg.get("field_type", "text")
    value      = resolve_value(field_cfg, row)
    char_delay = delay.get("char_delay_ms", 0)
    action_to  = delay.get("action_timeout_ms", 8000)

    # ── human_input ──
    if field_type == "human_input":
        question = field_cfg.get("human_input_question", "Please provide the required input:")
        if job_id:
            log(job_id, f"    → [Human Input] Waiting for operator: {question}")
            ulog(job_id, "ask", question=question)
            set_pending_action(job_id, {"type": "human_input", "question": question})
            waited = 0
            resp = None
            while waited < 300:
                resp = get_action_response(job_id)
                if resp:
                    break
                time.sleep(1)
                waited += 1
            clear_action(job_id)
            if not resp:
                raise FieldError(f"Human input timed out for: {question}")
            log(job_id, f"    ✓ [Human Input] Received. Filling '{selector}'...")
            ulog(job_id, "answer", question=question, answer=resp)
            if selector:
                frame, el = resolve_frame(page, selector, action_to)
                el.wait_for(state="visible", timeout=action_to)
                el.click(timeout=action_to)
                el.fill("", timeout=action_to)
                el.fill(resp, timeout=action_to)
        return

    # ── split_fill ──
    if field_type == "split_fill":
        source = field_cfg.get("source", "csv_column")
        if source == "human_input":
            question = field_cfg.get("human_input_question", "Please provide the required input:")
            if job_id:
                log(job_id, f"    → [Human Input] Waiting for operator: {question}")
                ulog(job_id, "ask", question=question)
                set_pending_action(job_id, {"type": "human_input", "question": question})
                waited = 0
                resp = None
                while waited < 300:
                    resp = get_action_response(job_id)
                    if resp:
                        break
                    time.sleep(1)
                    waited += 1
                clear_action(job_id)
                if not resp:
                    raise FieldError(f"Human input timed out for: {question}")
                log(job_id, f"    ✓ [Human Input] Received. Splitting across boxes...")
                ulog(job_id, "answer", question=question, answer=resp)
                source_value = resp
            else:
                source_value = ""
        else:
            source_value = resolve_value(field_cfg, row)
        boxes = field_cfg.get("split_boxes", [])
        pos = 0
        for box in boxes:
            box_sel = box.get("selector", "")
            box_len = int(box.get("length", 1))
            chunk = source_value[pos:pos + box_len]
            pos += box_len
            if not box_sel:
                continue
            try:
                frame, el = resolve_frame(page, box_sel, action_to)
                el.wait_for(state="visible", timeout=action_to)
                el.click(timeout=action_to)
                el.fill("", timeout=action_to)
                el.fill(chunk, timeout=action_to)
                human_delay(delay.get("between_fields_ms", 100))
            except Exception as e:
                if job_id:
                    log(job_id, f"    ⚠ split_fill box '{box_sel}': {e}")
        return

    # ── file_upload ──
    # Uploads a file to an <input type="file"> element.
    # The CSV column (or literal value) must contain the absolute path of the
    # file on the server filesystem.
    # Three optional constraints are read from the mapping config:
    #   file_accept  : "image"  → only image/* MIME types
    #                  "pdf"    → only application/pdf and common doc types
    #                  "any"    → no MIME restriction (default)
    #   file_max_mb  : maximum allowed file size in MB (0 = no limit)
    # If a constraint is violated the field raises FieldError (row fails).
    if field_type == "file_upload":
        import mimetypes
        file_path = value.strip()
        if not file_path:
            if job_id:
                log(job_id, f"    ⚠ file_upload: no file path in CSV/literal for selector '{selector}' — skipping")
            return

        file_source = field_cfg.get("file_source", "server_path")
        _temp_file_to_cleanup = None  # track any temp file we create, so we can delete it after upload

        if file_source == "local_disk":
            # DEVIATION from upstream: no Docker here — the engine runs on the
            # same machine as the operator, so "local disk" just means this
            # machine's filesystem. We copy it into our temp_attachments dir
            # under a job-scoped name so concurrent runs don't collide,
            # then delete the copy after set_input_files() completes.
            import shutil
            src = Path(file_path)
            if not src.exists():
                raise FieldError(
                    f"file_upload (local_disk): file not found: {file_path}. "
                    f"The path must be readable on this machine."
                )
            dest = TEMP_ATTACHMENTS_DIR / f"{job_id}_{src.name}"
            shutil.copy2(str(src), str(dest))
            _temp_file_to_cleanup = dest
            p_file = dest
            if job_id:
                log(job_id, f"    → [file_upload] local_disk: copied '{src.name}' to temp_attachments for upload")

        elif file_source == "external_url":
            # file_path is an HTTP/HTTPS URL (e.g. an S3 presigned URL or any public link).
            # Download it into temp_attachments/ under a job-scoped name, upload, then delete.
            import urllib.request
            import urllib.parse
            import re as _re
            url_str = file_path
            _gdrive_file = _re.search(r'drive\.google\.com/file/d/([^/?#]+)', url_str)
            _gdocs_file  = _re.search(r'docs\.google\.com/\w+/d/([^/?#]+)', url_str)
            if _gdrive_file:
                url_str = f"https://drive.google.com/uc?export=download&id={_gdrive_file.group(1)}"
            elif _gdocs_file:
                url_str = f"https://drive.google.com/uc?export=download&id={_gdocs_file.group(1)}"
            if job_id:
                log(job_id, f"    → [file_upload] external_url: downloading from URL to temp_attachments...")
                ulog(job_id, "file_downloading", url=url_str)
            try:
                req = urllib.request.Request(url_str, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp:
                    content_disposition = resp.headers.get("Content-Disposition", "")
                    url_filename = None
                    if content_disposition:
                        import re
                        m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', content_disposition, re.IGNORECASE)
                        if m:
                            url_filename = urllib.parse.unquote(m.group(1).strip().strip('"\''))
                    if not url_filename:
                        parsed = urllib.parse.urlparse(url_str)
                        url_filename = Path(parsed.path).name or "attachment"
                    dest = TEMP_ATTACHMENTS_DIR / f"{job_id}_{url_filename}"
                    dest.write_bytes(resp.read())
            except Exception as dl_err:
                raise FieldError(f"file_upload (external_url): download failed for '{url_str}': {dl_err}")
            _temp_file_to_cleanup = dest
            p_file = dest
            if job_id:
                log(job_id, f"    ✓ [file_upload] external_url: downloaded '{url_filename}'")
                ulog(job_id, "file_downloaded", filename=url_filename)

        else:
            # "server_path" (default) — value is already an absolute path on the server filesystem
            p_file = Path(file_path)

        if not p_file.exists():
            raise FieldError(f"file_upload: file not found: {file_path}")

        # ── size check ──
        file_max_mb = float(field_cfg.get("file_max_mb", 0) or 0)
        if file_max_mb > 0:
            size_mb = p_file.stat().st_size / (1024 * 1024)
            if size_mb > file_max_mb:
                raise FieldError(
                    f"file_upload: file '{p_file.name}' is {size_mb:.2f} MB, "
                    f"exceeds limit of {file_max_mb} MB"
                )

        # ── MIME / accept check ──
        file_accept = field_cfg.get("file_accept", "any")
        mime_type, _ = mimetypes.guess_type(str(p_file))
        mime_type = mime_type or "application/octet-stream"

        if file_accept == "image":
            if not mime_type.startswith("image/"):
                raise FieldError(
                    f"file_upload: file '{p_file.name}' (MIME: {mime_type}) is not an image — "
                    f"only image/* files are accepted for this field"
                )
        elif file_accept == "pdf":
            # Accept PDF and common document types
            _PDF_DOC_MIMES = {
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "text/plain",
                "application/rtf",
            }
            if mime_type not in _PDF_DOC_MIMES:
                raise FieldError(
                    f"file_upload: file '{p_file.name}' (MIME: {mime_type}) is not an accepted "
                    f"PDF/document type for this field"
                )
        # "any" → no MIME restriction

        if job_id:
            log(job_id, f"    → [file_upload] Uploading '{p_file.name}' ({mime_type}) to '{selector}'...")
            ulog(job_id, "file_upload", selector=selector, filename=p_file.name, mime=mime_type)

        try:
            frame, el = resolve_frame(page, selector, action_to)
            el.wait_for(state="attached", timeout=action_to)
            el.set_input_files(str(p_file), timeout=action_to)
            if job_id:
                log(job_id, f"    ✓ [file_upload] '{p_file.name}' set successfully.")
                ulog(job_id, "file_attached", filename=p_file.name)
        except Exception as e:
            raise FieldError(f"file_upload: could not set file on '{selector}': {e}")
        finally:
            # Clean up any temp file we created (local_disk copy or external_url download)
            if _temp_file_to_cleanup:
                if temp_files is not None:
                    temp_files.append(_temp_file_to_cleanup)
                else:
                    if _temp_file_to_cleanup.exists():
                        try:
                            _temp_file_to_cleanup.unlink()
                            if job_id:
                                log(job_id, f"    → [file_upload] temp file '{_temp_file_to_cleanup.name}' removed.")
                        except Exception:
                            pass
        return

    if not value and field_type not in ("checkbox", "click"):
        return

    try:
        if field_type in ("text", "password", "email", "tel", "number", "textarea"):
            frame, el = resolve_frame(page, selector, action_to)
            el.wait_for(state="visible", timeout=action_to)
            el.click(timeout=action_to)
            el.fill("", timeout=action_to)
            if char_delay > 0:
                for ch in value:
                    el.type(ch, delay=char_delay)
            else:
                el.fill(value, timeout=action_to)

        elif field_type == "select":
            frame, el = resolve_frame(page, selector, action_to)
            el.wait_for(state="visible", timeout=action_to)
            try:
                el.select_option(value=value, timeout=action_to)
            except Exception:
                try:
                    el.select_option(label=value, timeout=action_to)
                except Exception:
                    pass

        elif field_type == "radio":
            radio_sel = selector
            if value:
                name_match = field_cfg.get("radio_name", "")
                if name_match:
                    radio_sel = f'input[type="radio"][name="{name_match}"][value="{value}"]'
            frame, el = resolve_frame(page, radio_sel, action_to)
            el.first.click(timeout=action_to)

        elif field_type == "checkbox":
            frame, el = resolve_frame(page, selector, action_to)
            should_check = value.lower() in ("yes", "true", "1", "on", "checked")
            if should_check:
                if not el.is_checked():
                    el.check(timeout=action_to, force=True)
            else:
                if el.is_checked():
                    el.uncheck(timeout=action_to, force=True)

        elif field_type == "click":
            frame, el = resolve_frame(page, selector, action_to)
            el.click(timeout=action_to)

    except Exception as e:
        raise FieldError(f"Field '{selector}' ({field_type}): {e}")


class FieldError(Exception):
    pass


# ──────────────────────────────────────────────────────────────
#  Login steps (run once before the loop)
# ──────────────────────────────────────────────────────────────

def execute_login_steps(page, login_steps: List[Dict], delay: Dict, job_id: str):
    if not login_steps:
        return
    log(job_id, "  → Executing login steps...")
    page_to   = delay.get("page_load_timeout_ms", 15000)
    action_to = delay.get("action_timeout_ms", 8000)

    for step in login_steps:
        url = step.get("url", "")
        if url:
            log(job_id, f"    → Navigating to {url}")
            page.goto(url)
            page.wait_for_load_state("networkidle", timeout=page_to)

        for field_cfg in step.get("fields", []):
            try:
                fill_field(page, field_cfg, {}, delay, job_id)
                human_delay(delay.get("between_fields_ms", 100))
            except FieldError as e:
                log(job_id, f"    ⚠ {e}")

        submit_sel = step.get("submit_selector", "")
        if submit_sel:
            page.locator(submit_sel).first.click(timeout=action_to)
            page.wait_for_load_state("networkidle", timeout=page_to)

        wait_url = step.get("wait_for_url", "")
        if wait_url:
            try:
                page.wait_for_url(f"**{wait_url}**", timeout=page_to)
                log(job_id, f"    ✓ Reached {page.url}")
            except Exception:
                log(job_id, f"    ✗ Expected URL containing '{wait_url}', got {page.url}")


# ──────────────────────────────────────────────────────────────
#  Single flow step
# ──────────────────────────────────────────────────────────────

def execute_step(page, step: Dict, row: Dict[str, str], delay: Dict, job_id: str):
    """Execute one flow step for one CSV row. Returns (success: bool, active_page)."""
    # RPAForge bridge: handle simple activity steps (from src/rpa/bridge.py).
    # These use {"type": "click", "selector": "..."} format instead of
    # the legacy field_mappings format.
    if step.get("type") in ("goto", "click", "fill", "get_text", "wait_for",
                            "screenshot", "evaluate", "select", "check",
                            "upload", "dialog", "get_attribute", "scroll",
                            "hover", "press"):
        return _execute_activity_step(page, step, job_id)

    label     = step.get("label", step.get("step_id", "?"))
    url       = step.get("url", "")
    page_to   = delay.get("page_load_timeout_ms", 15000)
    action_to = delay.get("action_timeout_ms", 8000)

    on_error    = step.get("on_error", "fail")
    max_retries = int(step.get("max_retries", 2) or 2)
    dismiss_dialogs = step.get("dismiss_dialogs", False)
    dialog_action   = step.get("dialog_action", "accept")

    if step.get("skip_if_no_data", False):
        has_data = any(
            resolve_value(fm, row)
            for fm in step.get("field_mappings", [])
            if fm.get("source") == "csv_column"
        )
        if not has_data:
            log(job_id, f"    → Skipping step '{label}' (no data)")
            return True, page

    if url:
        log(job_id, f"    → [{label}] Navigating to {url}")
        ulog(job_id, "navigate", label=label, url=url)
        page.goto(url)
        page.wait_for_load_state("networkidle", timeout=page_to)

    human_input_mappings = [
        fm for fm in step.get("field_mappings", [])
        if fm.get("field_type") == "human_input"
        or (fm.get("field_type") == "split_fill" and fm.get("source") == "human_input")
    ]

    for attempt in range(max_retries + 1):
        if attempt > 0:
            log(job_id, f"    ↩ [{label}] Retrying (attempt {attempt} of {max_retries})...")
            ulog(job_id, "retrying", label=label, attempt=attempt, max_retries=max_retries)
            for fm in human_input_mappings:
                try:
                    fill_field(page, fm, row, delay, job_id)
                    human_delay(delay.get("between_fields_ms", 100))
                except FieldError as e:
                    log(job_id, f"    ⚠ {e}")
        else:
            _step_temp_files = []
            for fm in step.get("field_mappings", []):
                try:
                    fill_field(page, fm, row, delay, job_id, temp_files=_step_temp_files)
                    human_delay(delay.get("between_fields_ms", 100))
                except FieldError as e:
                    log(job_id, f"    ⚠ {e}")

        # CAPTCHA / Human Handoff — runs on every attempt so user always sees the latest image
        cap_img_sel = step.get("captcha_image_selector", "")
        cap_inp_sel = step.get("captcha_input_selector", "")
        if cap_img_sel and cap_inp_sel:
            log(job_id, f"    → [Human Handoff] Waiting for image/canvas '{cap_img_sel}'...")
            try:
                import base64
                # Try main page then iframes
                frame_obj = page
                try:
                    el_check = page.locator(cap_img_sel).first
                    el_check.wait_for(state="visible", timeout=action_to)
                except Exception:
                    for fr in page.frames:
                        if fr == page.main_frame:
                            continue
                        try:
                            el_check = fr.locator(cap_img_sel).first
                            el_check.wait_for(state="visible", timeout=2000)
                            frame_obj = fr
                            break
                        except Exception:
                            continue

                el = frame_obj.locator(cap_img_sel).first
                el.wait_for(state="visible", timeout=action_to)
                img_bytes = el.screenshot(timeout=action_to)
                b64 = base64.b64encode(img_bytes).decode('utf-8')

                log(job_id, "    → [Human Handoff] Action required: Waiting for user to solve CAPTCHA via Chat...")
                ulog(job_id, "captcha", image_b64=b64)
                set_pending_action(job_id, {"type": "captcha", "image_b64": b64})

                waited = 0
                resp = None
                while waited < 300:
                    resp = get_action_response(job_id)
                    if resp:
                        break
                    time.sleep(1)
                    waited += 1

                clear_action(job_id)

                if not resp:
                    log(job_id, "    ✗ [Human Handoff] Timed out waiting for response (5 mins).")
                    ulog(job_id, "captcha_timeout")
                    return False, page

                log(job_id, f"    ✓ [Human Handoff] Received response. Filling field...")
                ulog(job_id, "captcha_answer", answer=resp)

                inp_frame, inp_el = resolve_frame(page, cap_inp_sel, action_to)
                inp_el.wait_for(state="visible", timeout=action_to)
                inp_el.fill(resp, timeout=action_to)
                human_delay(delay.get("between_fields_ms", 100))

            except Exception as e:
                log(job_id, f"    ✗ [Human Handoff] Failed: {e}")
                clear_action(job_id)

        # Dialog handling — register before submit click
        _dialog_log = []
        def _handle_dialog(dialog, _da=dialog_action, _dl=_dialog_log):
            msg = dialog.message
            if _da == "dismiss":
                dialog.dismiss()
                _dl.append(("dismiss", msg))
            else:
                dialog.accept()
                _dl.append(("accept", msg))

        if dismiss_dialogs:
            page.on("dialog", _handle_dialog)

        # Submit
        submit_sel  = step.get("submit_selector", "")
        opens_new_tab = step.get("opens_new_tab", False)

        if submit_sel:
            try:
                if opens_new_tab:
                    log(job_id, f"    → [{label}] Clicking (expects new tab)...")
                    ulog(job_id, "click", label=label, selector=submit_sel, context="new tab")
                    with page.context.expect_page() as new_page_info:
                        page.locator(submit_sel).first.click(timeout=action_to)
                    new_page = new_page_info.value
                    new_page.wait_for_load_state("networkidle", timeout=page_to)
                    log(job_id, f"    ✓ New tab opened: {new_page.url}")
                    ulog(job_id, "new_tab", url=new_page.url)
                    return True, new_page
                else:
                    # Try main page first, then iframes
                    clicked = False
                    try:
                        page.locator(submit_sel).first.click(timeout=action_to)
                        clicked = True
                    except Exception:
                        pass
                    if not clicked:
                        for fr in page.frames:
                            if fr == page.main_frame:
                                continue
                            try:
                                fr.locator(submit_sel).first.click(timeout=2000)
                                clicked = True
                                log(job_id, f"    → [{label}] Clicked inside iframe")
                                break
                            except Exception:
                                continue
                    if not clicked:
                        raise Exception(f"Could not find submit selector '{submit_sel}' on page or any iframe")
                    log(job_id, f"    → [{label}] Clicked '{submit_sel}'")
                    ulog(job_id, "click", label=label, selector=submit_sel, context="same tab")
            except Exception as e:
                if dismiss_dialogs:
                    try:
                        page.remove_listener("dialog", _handle_dialog)
                    except Exception:
                        pass
                log(job_id, f"    ✗ Submit failed on step '{label}': {e}")
                return False, page

        if dismiss_dialogs:
            for act, msg in _dialog_log:
                log(job_id, f"    → [Dialog] {act}ed native dialog: {msg!r}")
                ulog(job_id, "dialog_handled", action=act, message=msg)
            try:
                page.remove_listener("dialog", _handle_dialog)
            except Exception:
                pass

        # Error check — runs immediately after submit, before URL/selector waits
        err_found, err_text = check_error(page, step, action_to)
        if err_found:
            log(job_id, f"    ✗ [{label}] Page error detected: {err_text!r}")
            ulog(job_id, "step_error", label=label, error_text=err_text, attempt=attempt)
            if on_error == "fail" or attempt >= max_retries:
                if attempt >= max_retries and on_error != "fail":
                    log(job_id, f"    ✗ [{label}] Max retries ({max_retries}) reached — failing row.")
                    ulog(job_id, "retry_exhausted", label=label, max_retries=max_retries)
                return False, page
            has_captcha = bool(step.get("captcha_image_selector", "") and step.get("captcha_input_selector", ""))
            if on_error == "retry":
                if not human_input_mappings and not has_captcha:
                    log(job_id, f"    ✗ [{label}] on_error=retry but no human_input fields or captcha to re-ask — failing row.")
                    return False, page
                continue
            if on_error == "ask":
                log(job_id, f"    → [{label}] Asking operator: retry or fail?")
                ulog(job_id, "step_error_ask", label=label, error_text=err_text, attempt=attempt, options=["retry", "fail"])
                set_pending_action(job_id, {"type": "step_error", "label": label, "error_text": err_text, "options": ["retry", "fail"]})
                waited = 0
                op_resp = None
                while waited < 300:
                    op_resp = get_action_response(job_id)
                    if op_resp:
                        break
                    time.sleep(1)
                    waited += 1
                clear_action(job_id)
                if not op_resp or op_resp.strip().lower() == "fail":
                    log(job_id, f"    ✗ [{label}] Operator chose to fail row.")
                    return False, page
                if human_input_mappings or has_captcha:
                    continue
                log(job_id, f"    ✗ [{label}] Operator chose retry but no human_input fields or captcha to re-ask — failing row.")
                return False, page

        # Wait for URL
        wait_url = step.get("wait_for_url", "")
        if wait_url:
            try:
                page.wait_for_url(f"**{wait_url}**", timeout=page_to)
                log(job_id, f"    ✓ Step '{label}' — reached {page.url}")
                ulog(job_id, "reached", label=label, url=page.url)
            except Exception:
                errors = []
                try:
                    errors = page.locator(
                        ".error, .errorlist, [class*='error'], .alert-danger, .alert"
                    ).all_text_contents()
                except Exception:
                    pass
                log(job_id, f"    ✗ Step '{label}' — expected URL '{wait_url}', got {page.url}")
                if errors:
                    clean = [e.strip() for e in errors if e.strip()]
                    log(job_id, f"    ✗ Page errors: {clean}")
                return False, page

        # Wait for selector (try page + iframes)
        wait_sel = step.get("wait_for_selector", "")
        if wait_sel:
            found = False
            try:
                page.wait_for_selector(wait_sel, timeout=page_to)
                found = True
            except Exception:
                pass
            if not found:
                for fr in page.frames:
                    if fr == page.main_frame:
                        continue
                    try:
                        fr.wait_for_selector(wait_sel, timeout=2000)
                        found = True
                        break
                    except Exception:
                        continue
            if not found:
                log(job_id, f"    ✗ Step '{label}' — wait_for_selector '{wait_sel}' not found (step failed)")
                return False, page

        if attempt > 0:
            log(job_id, f"    ✓ [{label}] Retry succeeded on attempt {attempt}.")
            ulog(job_id, "retry_success", label=label, attempt=attempt)

        for _tf in _step_temp_files:
            if _tf.exists():
                try:
                    _tf.unlink()
                    log(job_id, f"    → [file_upload] temp file '{_tf.name}' removed.")
                except Exception:
                    pass

        return True, page

    return False, page


# ──────────────────────────────────────────────────────────────
#  Main entry point
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
#  Main entry point
# ──────────────────────────────────────────────────────────────

def run_job(job_id: str, recipe: Dict, data_path: Optional[str] = None,
            start_row: int = 1, end_row: Optional[int] = None,
            page=None, log_cb=None):
    """Run a recipe against data rows.

    :param job_id: unique id for this run (logs/status/summary keyed on it).
    :param recipe: recipe dict (see ``src/rpa/recipes.py`` Recipe schema).
    :param data_path: CSV/XLSX data file. DEVIATION from upstream (which
        required a file): may be None, in which case the flow runs once
        with an empty row — for recipes that only use literal selectors.
    :param start_row: 1-based first data row (default 1).
    :param end_row: 1-based last data row inclusive (default: all rows).
    :param page: an already-open Playwright ``Page`` — our Camoufox profile
        page. DEVIATION from upstream: when provided, the engine skips the
        browser launch and the teardown (no ``context.close()`` /
        ``browser.close()``) and does not record video. When None, falls
        back to upstream behavior: launches stock headless Chromium and
        records video into ``~/.antidetect-browser/rpa/recordings/``.
        (A Camoufox-provided page's context can carry its own video
        recording only if our launcher enabled ``record_video_dir``.)
    :param log_cb: optional ``callable(str)`` invoked with every log line
        (live log streaming for GUI/CLI).
    """
    from playwright.sync_api import sync_playwright

    reset_job_state(job_id)
    if log_cb is not None:
        with _lock:
            _log_callbacks[job_id] = log_cb

    RECORDINGS_DIR = _rpa_home() / "recordings"
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    set_status(job_id, "running")
    delay = recipe.get("delay", {})

    owns_browser = page is None
    _pw = _browser = _context = None
    recorded_pages = []

    def _teardown_owned():
        # Only called on the fallback path (we launched it, we close it).
        # The injected-page path never touches the caller's browser.
        if _context is not None:
            try:
                _context.close()
            except Exception:
                pass
        if _browser is not None:
            try:
                _browser.close()
            except Exception:
                pass
        if _pw is not None:
            try:
                _pw.stop()
            except Exception:
                pass

    try:
        try:
            if data_path:
                log(job_id, "📂 Loading data file...")
                rows = load_data_file(data_path)
            else:
                log(job_id, "📂 No data file — running the flow once with an empty row.")
                rows = [{}]
            total = len(rows)
            log(job_id, f"   Found {total} rows.")

            start_idx = max(0, start_row - 1)
            end_idx = min(total, end_row) if end_row else total
            batch = rows[start_idx:end_idx]

            if not batch:
                log(job_id, "❌ No rows to process.")
                set_status(job_id, "done")
                set_summary(job_id, {"success": 0, "failed": 0, "failed_ids": []})
                return

            log(job_id, f"   Processing rows {start_idx + 1}–{end_idx} ({len(batch)} records).")
            ulog(job_id, "start", total=len(batch), start=start_idx + 1, end=end_idx)

            stats = {"success": 0, "failed": 0, "failed_ids": []}

            if owns_browser:
                # Fallback path (upstream behavior): stock headless Chromium.
                # Only used when no page was injected — e.g. offline runs
                # without a Camoufox profile.
                log(job_id, "🚀 Launching stock Chromium (fallback — no page injected)...")
                _pw = sync_playwright()
                _pw.start()
                _browser = _pw.chromium.launch(headless=True)
                _context = _browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    record_video_dir=str(RECORDINGS_DIR)
                )

                def _track_page(new_p):
                    if new_p not in recorded_pages:
                        recorded_pages.append(new_p)
                _context.on("page", _track_page)
                page = _context.new_page()
                _track_page(page)
            else:
                log(job_id, "🚀 Using injected page (browser launch/teardown skipped).")

            login_steps = recipe.get("login_steps") or []
            if login_steps:
                try:
                    execute_login_steps(page, login_steps, delay, job_id)
                except Exception as e:
                    log(job_id, f"  💥 Login failed: {e}")
                    set_status(job_id, "error")
                    return

            flow = recipe.get("flow", [])

            for i, row in enumerate(batch, start=start_idx + 1):
                row_id = next(iter(row.values()), str(i)) if row else str(i)
                log(job_id, "")
                log(job_id, f"{'='*56}")
                log(job_id, f"[{i}/{end_idx}] Row: {row_id}")
                log(job_id, f"{'='*56}")
                ulog(job_id, "row_start", row_num=i, row_total=end_idx, row_id=row_id)

                if is_stop_requested(job_id):
                    log(job_id, "  🛑 Stop requested by user — finishing after current row.")
                    ulog(job_id, "info", msg="Run stopped by user.")
                    break

                row_ok = True
                active_page = page
                for step in flow:
                    try:
                        ok, active_page = execute_step(active_page, step, row, delay, job_id)
                        if not ok:
                            row_ok = False
                            break
                        human_delay(delay.get("between_steps_ms", 300))
                    except Exception as e:
                        log(job_id, f"  💥 Unexpected error in step: {e}")
                        row_ok = False
                        break

                if row_ok:
                    stats["success"] += 1
                    log(job_id, f"  ✅ Row {row_id} — done")
                    ulog(job_id, "row_done", row_id=row_id, success=True)
                else:
                    stats["failed"] += 1
                    stats["failed_ids"].append(row_id)
                    log(job_id, f"  ❌ Row {row_id} — failed")
                    ulog(job_id, "row_done", row_id=row_id, success=False)
                    try:
                        base_url = recipe.get("base_url", "")
                        if base_url:
                            page.goto(base_url)
                            page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                human_delay(delay.get("between_records_ms", 800))

            if owns_browser:
                # Video saving is a new_context() option; only the browser we
                # launched has recording enabled.
                all_pages = recorded_pages
                for tab_idx, p in enumerate(all_pages):
                    try:
                        suffix = f"{job_id}.webm" if tab_idx == 0 else f"{job_id}_tab{tab_idx + 1}.webm"
                        if not p.is_closed():
                            p.close()
                        if p.video:
                            p.video.save_as(str(RECORDINGS_DIR / suffix))
                            p.video.delete()
                    except Exception as e:
                        log(job_id, f"  ⚠ Could not process video for tab {tab_idx + 1}: {e}")

            log(job_id, "")
            log(job_id, "=" * 56)
            log(job_id, "📊 RUN SUMMARY")
            log(job_id, "=" * 56)
            log(job_id, f"  ✅ Success : {stats['success']}")
            log(job_id, f"  ❌ Failed  : {stats['failed']}")
            if stats["failed_ids"]:
                log(job_id, f"  Failed IDs: {', '.join(str(x) for x in stats['failed_ids'])}")
            log(job_id, "=" * 56)
            ulog(job_id, "summary", success=stats["success"], failed=stats["failed"],
                 failed_ids=stats.get("failed_ids", []))

            set_summary(job_id, stats)
            set_status(job_id, "done")

        except Exception as e:
            log(job_id, f"\n💥 Fatal error: {e}")
            import traceback
            log(job_id, traceback.format_exc())
            set_status(job_id, "error")
            set_summary(job_id, {"error": str(e)})
    finally:
        if owns_browser:
            _teardown_owned()
        with _lock:
            _stop_flags.discard(job_id)
            _log_callbacks.pop(job_id, None)
