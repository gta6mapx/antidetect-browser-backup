import src._vendor  # noqa: F401  -- vendored deps shim; must stay first

from src import paths as _paths

"""Bulk CSV import/export of profiles, plus reusable profile templates.

CSV format for :func:`bulk_import`
----------------------------------
Columns:

    name (required), os, proxy_name, client_tag, template

plus any extra columns, which are treated as persona overrides.  Override
values stay strings, except ``hardware_concurrency``, ``device_memory``,
``touch_points`` and ``color_depth``, which are converted to ``int`` when
possible.  Blank cells are treated as "not set".

Precedence for ``client_tag``/``template`` on each row: the row's own column
(if non-blank) beats the ``bulk_import()`` argument; the argument beats the
template's field.  For ``os``: row beats template beats ``'windows'``.
Explicit CSV override columns always beat template fields.

Templates are plain JSON files under ``~/.antidetect-browser/templates/``.
"""

import csv
import json
import os
import re

def _template_dir() -> str:
    """Templates dir, honoring ANTIDETECT_HOME (see src.paths)."""
    return _paths.templates_dir(create=True)

# Template names become file names: keep them filesystem-safe.
_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# CSV columns with reserved meaning; everything else is a persona override.
_RESERVED_COLUMNS = ("name", "os", "proxy_name", "client_tag", "template")

# Override fields that should be integers when the CSV gives a number.
_INT_FIELDS = ("hardware_concurrency", "device_memory", "touch_points",
               "color_depth")

_EXPORT_COLUMNS = ("name", "os", "client_tag", "template", "timezone",
                   "locale")


def _template_path(name):
    """Return the JSON file path for ``name``, validating the name.

    :raises ValueError: if ``name`` contains anything outside
        ``[A-Za-z0-9_-]``.
    """
    if not isinstance(name, str) or not _TEMPLATE_NAME_RE.match(name):
        raise ValueError(
            "template name must match [A-Za-z0-9_-]+, got %r" % (name,))
    return os.path.join(_template_dir(), name + ".json")


def save_template(name, fields):
    """Save ``fields`` (a dict of persona overrides) as template ``name``.

    Overwrites an existing template with the same name.

    :return: a copy of the saved fields dict.
    :raises ValueError: on an invalid template name.
    """
    path = _template_path(name)
    os.makedirs(_template_dir(), exist_ok=True)
    data = dict(fields)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    return data


def list_templates():
    """Return the sorted names of all saved templates."""
    tmpl = _template_dir()
    return sorted(
        fname[:-len(".json")]
        for fname in os.listdir(tmpl)
        if fname.endswith(".json")
    )


def get_template(name):
    """Return the fields dict stored for template ``name``.

    :raises ValueError: on an invalid template name.
    :raises KeyError: if no template named ``name`` exists.
    """
    path = _template_path(name)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise KeyError(name)


def _cell(row, column):
    """Return a CSV cell stripped of whitespace; missing/None -> ''."""
    return (row.get(column) or "").strip()


def _coerce_override(key, value):
    """Convert numeric override fields to int when possible.

    :return: ``int(value)`` for the known numeric fields when the value
        parses as an integer, otherwise the original string unchanged.
    """
    if key in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def _row_to_item(row, template_fields, arg_client_tag, arg_template,
                 proxy_manager):
    """Convert one CSV row into a :meth:`ProfileManager.bulk_create` item.

    :raises ValueError: on an empty name or an unknown ``proxy_name``.
    """
    name = _cell(row, "name")
    if not name:
        raise ValueError("missing required 'name' column value")

    # Reserved columns: row value (non-blank) beats template field beats
    # function argument / default.
    os_name = (_cell(row, "os")
               or str(template_fields.get("os") or "").strip()
               or "windows")
    client_tag = (_cell(row, "client_tag")
                  or str(template_fields.get("client_tag") or "").strip()
                  or arg_client_tag)
    template_name = (_cell(row, "template") or arg_template)

    proxy = None
    proxy_name = (_cell(row, "proxy_name")
                  or str(template_fields.get("proxy_name") or "").strip())
    if proxy_name:
        try:
            proxy = proxy_manager.get(proxy_name)
        except KeyError:
            raise ValueError("unknown proxy_name '%s'" % proxy_name)

    # Template fields are the base overrides; explicit non-blank CSV columns
    # win over them.  Reserved columns never become overrides.
    overrides = {k: v for k, v in template_fields.items()
                 if k not in _RESERVED_COLUMNS}
    for column, value in row.items():
        if column in _RESERVED_COLUMNS or column is None:
            continue
        value = (value or "").strip()
        if value:
            overrides[column] = _coerce_override(column, value)

    return {
        "name": name,
        "os": os_name,
        "proxy": proxy,
        "client_tag": client_tag,
        "template": template_name,
        **overrides,
    }


def bulk_import(csv_path, client_tag=None, template=None, db_path=None):
    """Import profiles from a CSV file; never raises on row errors.

    :param csv_path: path to the CSV file (see module docstring for format).
    :param client_tag: default ``client_tag`` for rows that leave the column
        blank.
    :param template: name of a saved template whose fields are applied as
        base overrides (explicit CSV columns win); also recorded on each
        profile unless the row names its own template.
    :param db_path: profiles database path; defaults to
        ``~/.antidetect-browser/profiles.db``.
    :return: ``{'created': int, 'skipped': list, 'errors': list}`` where
        ``skipped`` holds duplicate profile names and ``errors`` holds
        ``{'name': ..., 'error': str}`` dicts, one per bad row.  Only file
        level problems (missing/unreadable CSV) raise.
    """
    from src.profiles.manager import ProfileManager
    from src.proxy.manager import ProxyManager  # lazy: avoids import cycles

    profile_manager = ProfileManager(db_path=db_path)
    proxy_manager = ProxyManager()
    template_fields = dict(get_template(template)) if template else {}

    result = {"created": 0, "skipped": [], "errors": []}
    items = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                items.append(_row_to_item(row, template_fields, client_tag,
                                          template, proxy_manager))
            except ValueError as exc:
                result["errors"].append(
                    {"name": _cell(row, "name"), "error": str(exc)})

    outcome = profile_manager.bulk_create(items)
    result["created"] += outcome["created"]
    result["skipped"].extend(outcome["skipped"])
    result["errors"].extend(outcome["errors"])
    return result


def bulk_export(csv_path, client_tag=None, db_path=None):
    """Export profiles to a CSV file.

    :param csv_path: destination path (parent directories are created).
    :param client_tag: when given, only profiles with this tag are
        exported; otherwise all profiles are exported.
    :param db_path: profiles database path; defaults to
        ``~/.antidetect-browser/profiles.db``.
    :return: number of rows written (excluding the header).
    """
    from src.profiles.manager import ProfileManager

    profile_manager = ProfileManager(db_path=db_path)
    if client_tag is not None:
        profiles = profile_manager.list_by_tag(client_tag)
    else:
        profiles = profile_manager.list()

    parent = os.path.dirname(os.path.abspath(csv_path))
    os.makedirs(parent, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_EXPORT_COLUMNS)
        writer.writeheader()
        for persona in profiles:
            writer.writerow({
                column: (persona.get(column)
                         if persona.get(column) is not None else "")
                for column in _EXPORT_COLUMNS
            })
    return len(profiles)
