"""Per-profile browser extension management.

Extensions are stored as files under each profile's directory
(``<profiles_dir>/<name>/extensions/``) and tracked in a small JSON
manifest. Supported formats: ``.crx`` (Chromium) and ``.zip`` (unpacked).

Chromium/Patchright loads enabled extensions via ``--load-extension``.
Firefox/Camoufox: files are stored for manual install (auto-install is
best-effort and documented in the UI).
"""

import json
import os
import shutil

from src import paths as _paths


def _ext_dir(profile_name):
    """Return (creating) the extensions directory for a profile."""
    d = os.path.join(_paths.profiles_dir(), profile_name, "extensions")
    os.makedirs(d, exist_ok=True)
    return d


def _manifest_path(profile_name):
    return os.path.join(_ext_dir(profile_name), "manifest.json")


def _load_manifest(profile_name):
    path = _manifest_path(profile_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_manifest(profile_name, items):
    with open(_manifest_path(profile_name), "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def list_extensions(profile_name):
    """Return the extension manifest for a profile.

    Each item: ``{"name", "filename", "enabled"}``.
    """
    return _load_manifest(profile_name)


def add_extension(profile_name, src_path, name=None):
    """Copy an extension file into the profile's extension dir.

    :param src_path: path to a ``.crx`` or ``.zip`` file.
    :param name: display name; defaults to the file's base name.
    :raises ValueError: on unsupported format or duplicate name.
    """
    if not os.path.isfile(src_path):
        raise ValueError("file not found: %s" % src_path)
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in (".crx", ".zip"):
        raise ValueError(
            "unsupported extension format '%s' (use .crx or .zip)" % ext)
    items = _load_manifest(profile_name)
    disp = (name or os.path.splitext(os.path.basename(src_path))[0]).strip()
    if not disp:
        raise ValueError("extension name must be non-empty")
    if any(i["name"] == disp for i in items):
        raise ValueError("extension '%s' already exists" % disp)
    dest = os.path.join(_ext_dir(profile_name), disp + ext)
    # Avoid overwriting an unrelated file with the same derived name.
    n = 1
    base = dest
    while os.path.exists(dest):
        dest = "%s-%d%s" % (os.path.splitext(base)[0], n, ext)
        n += 1
    shutil.copy2(src_path, dest)
    items.append({"name": disp,
                  "filename": os.path.basename(dest),
                  "enabled": True})
    _save_manifest(profile_name, items)
    return {"name": disp, "filename": os.path.basename(dest),
            "enabled": True}


def remove_extension(profile_name, name):
    """Remove an extension (file + manifest entry)."""
    items = _load_manifest(profile_name)
    kept = [i for i in items if i["name"] != name]
    if len(kept) == len(items):
        raise ValueError("extension '%s' not found" % name)
    removed = [i for i in items if i["name"] == name][0]
    fpath = os.path.join(_ext_dir(profile_name), removed["filename"])
    try:
        os.remove(fpath)
    except OSError:
        pass
    _save_manifest(profile_name, kept)
    return True


def set_enabled(profile_name, name, enabled):
    """Enable or disable an extension."""
    items = _load_manifest(profile_name)
    found = False
    for i in items:
        if i["name"] == name:
            i["enabled"] = bool(enabled)
            found = True
    if not found:
        raise ValueError("extension '%s' not found" % name)
    _save_manifest(profile_name, items)
    return True


def enabled_extension_paths(profile_name):
    """Return absolute paths of enabled ``.crx`` extensions.

    Used by the Chromium engine to build ``--load-extension`` args.
    ``.zip`` (unpacked) extensions are returned as their extracted
    directory if present, else skipped.
    """
    out = []
    for item in _load_manifest(profile_name):
        if not item.get("enabled"):
            continue
        fpath = os.path.join(_ext_dir(profile_name), item["filename"])
        if os.path.isfile(fpath):
            out.append(fpath)
    return out
