"""Backup and restore of browser profile session directories.

A profile's session state (cookies, storage, logins) lives in its persistent
user-data dir ``~/.antidetect-browser/profiles/<name>/`` (see
:mod:`src.browser.launcher`). Gmail logins persist there — backups are
safety copies of that dir. This module NEVER deletes or wipes profile
directories: :func:`restore_profile` refuses to touch a non-empty live
session dir (raises :class:`FileExistsError` instead of overwriting), and
:func:`session_health` is strictly read-only.

Profile names are restricted to ``[A-Za-z0-9_-]`` so they map safely to
directory names; anything else raises :class:`ValueError`.
"""

import src._vendor  # noqa: F401  (first import: keep vendored path setup)

import os
import re
import shutil
import zipfile
from datetime import datetime, timezone

from src import paths as _paths

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Session fingerprints used by session_health(): presence of any one of
# these means the profile dir holds real browser state.
_SESSION_MARKERS = ("Cookies", "Default", ".sqlite", ".ldb")


def _sanitize(name: str) -> str:
    """Validate a profile name for safe use as a directory name.

    Raises:
        ValueError: If ``name`` is not a non-empty string matching
            ``[A-Za-z0-9_-]+``.
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"profile name must match [A-Za-z0-9_-]+, got {name!r}"
        )
    return name


def _profile_dir(name: str) -> str:
    """Return the live session dir for a profile (does not create it)."""
    return _paths.profile_dir(_sanitize(name))


def _backup_root(name: str) -> str:
    """Return the backups dir for a profile (created on demand)."""
    _sanitize(name)
    path = os.path.join(_paths.backups_dir(create=True), name)
    os.makedirs(path, exist_ok=True)
    return path


def backup_profile(name: str) -> str:
    """Zip a profile's session dir into its backups tree.

    Args:
        name: Profile name (``[A-Za-z0-9_-]+``).

    Returns:
        Absolute path of the created ``<UTC timestamp>.zip`` under
        ``~/.antidetect-browser/backups/<name>/``. The zip contains the
        profile dir's contents; an empty profile dir backs up gracefully
        to a valid (empty) zip.

    Raises:
        ValueError: On an unsafe profile name.
        FileNotFoundError: If the profile dir does not exist.
    """
    src = _profile_dir(name)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"no profile dir for {name!r}: {src}")
    root = _backup_root(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = os.path.join(root, f"{stamp}.zip")
    # Suffix the timestamp if two backups land in the same second.
    counter = 1
    base = zip_path
    while os.path.exists(zip_path):
        counter += 1
        zip_path = base[:-4] + f"-{counter}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(src):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                arc = os.path.relpath(full, src)
                zf.write(full, arc)
            if not filenames and not _dirnames:
                # Keep empty subdirs so an empty tree round-trips.
                arc = os.path.relpath(dirpath, src)
                if arc != ".":
                    zf.writestr(arc + "/", "")
    return os.path.abspath(zip_path)


def _is_inside_backups(path: str) -> bool:
    """True iff ``path`` resolves inside the backups tree."""
    root = os.path.realpath(_paths.backups_dir())
    return os.path.realpath(path).startswith(root + os.sep)


def restore_profile(name: str, backup_path: str) -> str:
    """Restore a profile's session dir from a backup zip.

    The live session dir is NEVER silently overwritten: if the target dir
    already exists AND is non-empty, :class:`FileExistsError` is raised.

    Args:
        name: Profile name (``[A-Za-z0-9_-]+``).
        backup_path: Path to a ``.zip`` inside the backups tree
            (``~/.antidetect-browser/backups/``). Path traversal is
            rejected.

    Returns:
        Absolute path of the restored profile dir.

    Raises:
        ValueError: On an unsafe profile name, a ``backup_path`` outside
            the backups tree, or a missing / non-zip file.
        FileExistsError: If the target profile dir exists and is
            non-empty.
    """
    _sanitize(name)
    if not isinstance(backup_path, str) or not os.path.isfile(backup_path):
        raise ValueError(f"backup not found: {backup_path!r}")
    if not backup_path.lower().endswith(".zip"):
        raise ValueError(f"backup must be a .zip file: {backup_path!r}")
    if not _is_inside_backups(backup_path):
        raise ValueError(
            f"backup must live inside {_paths.backups_dir()!r}: "
            f"{backup_path!r}"
        )
    if not zipfile.is_zipfile(backup_path):
        raise ValueError(f"not a valid zip file: {backup_path!r}")

    target = _profile_dir(name)
    if os.path.isdir(target) and os.listdir(target):
        raise FileExistsError(
            f"profile dir {target!r} is a non-empty live session; "
            "refusing to overwrite. Delete or move it first if you really "
            "mean to replace it."
        )
    os.makedirs(target, exist_ok=True)

    with zipfile.ZipFile(backup_path, "r") as zf:
        for member in zf.namelist():
            # Zip-slip guard: every member must stay inside the target dir.
            dest = os.path.realpath(os.path.join(target, member))
            if dest != os.path.realpath(target) and not dest.startswith(
                os.path.realpath(target) + os.sep
            ):
                raise ValueError(
                    f"zip member escapes target dir: {member!r}"
                )
            if member.endswith("/"):
                os.makedirs(dest, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member, "r") as src_fh, open(dest, "wb") as dst_fh:
                    shutil.copyfileobj(src_fh, dst_fh)
    return os.path.abspath(target)


def list_backups(name: str) -> "list[str]":
    """List backup zips for a profile, sorted oldest-first.

    Returns an empty list when the profile has no backups (or no backup
    dir at all yet). Never raises for missing dirs.

    Args:
        name: Profile name (``[A-Za-z0-9_-]+``).
    """
    _sanitize(name)
    root = os.path.join(_paths.backups_dir(), name)
    if not os.path.isdir(root):
        return []
    zips = [
        os.path.abspath(os.path.join(root, fn))
        for fn in os.listdir(root)
        if fn.lower().endswith(".zip")
        and os.path.isfile(os.path.join(root, fn))
    ]
    return sorted(zips)


def session_health(name: str) -> dict:
    """Check a profile's session dir for signs of a live session.

    Strictly read-only: never deletes, modifies, or repairs anything.

    Args:
        name: Profile name (``[A-Za-z0-9_-]+``).

    Returns:
        ``{'ok': bool, 'size_mb': float, 'detail': str}`` where ``ok`` is
        True only if the profile dir exists AND contains at least one
        session marker: a ``Cookies`` file, a ``Default`` subdir, or any
        ``*.sqlite`` / ``*.ldb`` file. ``size_mb`` is the recursive
        directory size in MiB (0.0 when missing). ``detail`` is a
        human-readable summary.
    """
    target = _profile_dir(name)
    if not os.path.isdir(target):
        return {
            "ok": False,
            "size_mb": 0.0,
            "detail": f"profile dir missing: {target}",
        }

    total = 0
    markers = set()
    for dirpath, dirnames, filenames in os.walk(target):
        rel = os.path.relpath(dirpath, target)
        if "Cookies" in filenames:
            markers.add("Cookies")
        if "Default" in dirnames or rel.split(os.sep)[0] == "Default":
            markers.add("Default")
        for fn in filenames:
            if fn.endswith(".sqlite") or fn.endswith(".ldb"):
                markers.add(fn.rsplit(".", 1)[1])
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass

    size_mb = round(total / (1024 * 1024), 2)
    if markers:
        return {
            "ok": True,
            "size_mb": size_mb,
            "detail": (
                f"session state present ({', '.join(sorted(markers))}); "
                f"dir size {size_mb} MiB"
            ),
        }
    return {
        "ok": False,
        "size_mb": size_mb,
        "detail": (
            f"profile dir exists ({size_mb} MiB) but no session markers "
            "(Cookies / Default / *.sqlite / *.ldb) found"
        ),
    }
