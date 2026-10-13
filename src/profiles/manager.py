"""Profile management for the anti-detect browser.

Stores browser "personas" (fingerprints + settings) in a local SQLite database
at ``~/.antidetect-browser/profiles.db``.

Storage layout
--------------
The ``profiles`` table is:

    name TEXT PRIMARY KEY
    os TEXT
    timezone TEXT
    locale TEXT
    created_at TEXT          -- UTC ISO-8601 timestamp
    fingerprint_json TEXT    -- the persona dict serialized as JSON
    client_tag TEXT          -- agency client label (added via migration)
    template TEXT            -- template name the profile was built from
    engine TEXT              -- browser engine: 'camoufox' (default) or
                             -- 'patchright' (added via migration)

``name``, ``os``, ``timezone``, ``locale``, ``client_tag``, ``template``
and ``engine``
exist as real columns so personas are queryable by those attributes without
parsing JSON.  Everything else lives inside ``fingerprint_json`` (the three
meta keys are deliberately kept OUT of the JSON blob to avoid duplication).
``get()``/``list()`` rebuild the full persona dict from ``fingerprint_json``
(overlaid with the authoritative ``name`` column, plus ``client_tag``,
``template`` and ``engine`` restored from their columns), so the returned
dict always
contains every contract key.
"""

import src._vendor  # noqa: F401  -- makes vendored browserforge importable

import datetime
import json
import os
import secrets
import sqlite3

from browserforge.fingerprints import FingerprintGenerator

from src import paths as _paths

_VALID_OS = ("windows", "macos", "linux")

#: Browser engines a profile may use. ``'camoufox'`` (Firefox-based) is
#: the default; ``'patchright'`` selects the Chromium engine.
_VALID_ENGINES = ("camoufox", "patchright")

#: profile engine value -> persona generator engine family.
_ENGINE_GENERATOR_MAP = {"camoufox": "firefox", "patchright": "chromium"}

_DEFAULT_TIMEZONES = {
    "windows": "America/New_York",
    "macos": "America/Los_Angeles",
    "linux": "America/New_York",  # en-US default locale -> keep region consistent
}

_CHROME_HEIGHT = 80  # pixels reserved for browser chrome (viewport < screen)

# Fallback fingerprints used when browserforge generation raises. One realistic
# entry per supported OS. Values loosely mirror browserforge-style output so
# fallbacks stay consistent with generated personas.
_FALLBACK_FINGERPRINTS = {
    "windows": {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
            "Gecko/20100101 Firefox/150.0"
        ),
        "platform": "Win32",
        "screen": {"width": 1920, "height": 1080},
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 "
            "vs_5_0 ps_5_0)"
        ),
        "fonts": [
            "Arial", "Arial Black", "Calibri", "Cambria", "Consolas",
            "Georgia", "Impact", "Segoe UI", "Tahoma", "Times New Roman",
            "Trebuchet MS", "Verdana",
        ],
        "hardware_concurrency": 8,
        "device_memory": 8,
        "touch_points": 0,
    },
    "macos": {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) "
            "Gecko/20100101 Firefox/150.0"
        ),
        "platform": "MacIntel",
        "screen": {"width": 2560, "height": 1440},
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M1",
        "fonts": [
            "Arial", "Arial Black", "Courier", "Geneva", "Helvetica",
            "Helvetica Neue", "Menlo", "Monaco", "Palatino",
            "Times New Roman", "Verdana",
        ],
        "hardware_concurrency": 8,
        "device_memory": 8,
        "touch_points": 0,
    },
    "linux": {
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) "
            "Gecko/20100101 Firefox/150.0"
        ),
        "platform": "Linux x86_64",
        "screen": {"width": 1920, "height": 1080},
        "webgl_vendor": "Mesa",
        "webgl_renderer": (
            "Mesa Intel(R) UHD Graphics 620 (KBL GT2)"
        ),
        "fonts": [
            "DejaVu Sans", "DejaVu Serif", "Liberation Mono",
            "Liberation Sans", "Liberation Serif", "Noto Sans",
            "Ubuntu", "Ubuntu Mono",
        ],
        "hardware_concurrency": 4,
        "device_memory": 8,
        "touch_points": 0,
    },
}


# Persona keys that live in dedicated DB columns instead of fingerprint_json,
# so they are never duplicated inside the JSON blob.
_META_KEYS = ("client_tag", "template", "engine")

# Sentinel distinguishing "field not passed" from "field passed as None"
# in update().
_UNSET = object()


def _default_db_path():
    """Return the default SQLite path, creating the parent directory.

    Honors ``ANTIDETECT_HOME`` via :mod:`src.paths` (falls back to the
    legacy ``~/.antidetect-browser`` location when it already exists).
    """
    db_path = _paths.profiles_db()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return db_path


def _viewport_for(screen_width, screen_height):
    """Return a viewport slightly smaller than the screen (browser chrome)."""
    height = max(400, screen_height - _CHROME_HEIGHT)
    return {"width": screen_width, "height": min(height, screen_height)}


def _generate_persona(name, os_name, proxy, overrides, generator="high_entropy",
                      engine="camoufox"):
    """Build a persona dict, preferring the requested generator.

    :param generator: ``"high_entropy"`` uses
        :func:`src.fingerprints.generator.generate_persona`;
        ``"browserforge"`` uses the vendored browserforge generator (the
        pre-Phase-3 path).  Either way, a hardcoded per-OS fallback applies
        when generation raises.
    :param engine: ``"camoufox"`` (default) or ``"patchright"``; selects
        the persona generator's UA family (``"firefox"`` vs
        ``"chromium"``). The legacy browserforge/hardcoded fallbacks only
        produce Firefox UAs, so for ``"patchright"`` their UA is replaced
        with a generated Chrome UA.
    Returns a dict containing exactly the persona contract keys, overlaid with
    ``overrides`` (which win on any conflict).
    """
    gen_engine = _ENGINE_GENERATOR_MAP.get(engine, "firefox")
    if generator == "high_entropy":
        try:
            from src.fingerprints.generator import generate_persona
            persona = generate_persona(name, os=os_name, proxy=proxy,
                                       engine=gen_engine)
            persona.update(overrides)
            return persona
        except Exception:
            pass  # fall through to the browserforge path below
    try:
        generator = FingerprintGenerator(
            browser=("firefox",), os=(os_name,), device="desktop"
        )
        fp = generator.generate()
        nav = fp.navigator
        persona = {
            "name": name,
            "os": os_name,
            "user_agent": nav.userAgent,
            "platform": nav.platform,
            "viewport": _viewport_for(fp.screen.width, fp.screen.height),
            "screen": {"width": fp.screen.width, "height": fp.screen.height},
            "timezone": _DEFAULT_TIMEZONES[os_name],
            "locale": "en-US",
            "geolocation": None,
            "webgl_vendor": fp.videoCard.vendor,
            "webgl_renderer": fp.videoCard.renderer,
            "fonts": list(fp.fonts) or list(
                _FALLBACK_FINGERPRINTS[os_name]["fonts"]
            ),
            "hardware_concurrency": int(nav.hardwareConcurrency),
            "device_memory": int(nav.deviceMemory)
            if nav.deviceMemory is not None else 8,
            "touch_points": int(nav.maxTouchPoints),
            "color_depth": 24,
            "proxy": proxy,
            "canvas_seed": secrets.token_hex(8),
        }
    except Exception:
        fb = _FALLBACK_FINGERPRINTS[os_name]
        persona = {
            "name": name,
            "os": os_name,
            "user_agent": fb["user_agent"],
            "platform": fb["platform"],
            "viewport": _viewport_for(fb["screen"]["width"],
                                      fb["screen"]["height"]),
            "screen": {"width": fb["screen"]["width"],
                       "height": fb["screen"]["height"]},
            "timezone": _DEFAULT_TIMEZONES[os_name],
            "locale": "en-US",
            "geolocation": None,
            "webgl_vendor": fb["webgl_vendor"],
            "webgl_renderer": fb["webgl_renderer"],
            "fonts": list(fb["fonts"]),
            "hardware_concurrency": fb["hardware_concurrency"],
            "device_memory": fb["device_memory"],
            "touch_points": fb["touch_points"],
            "color_depth": 24,
            "proxy": proxy,
            "canvas_seed": secrets.token_hex(8),
        }
    if engine == "patchright":
        # The browserforge/hardcoded fallbacks only know Firefox UAs; a
        # patchright profile must never ship one.
        from src.fingerprints.generator import chrome_user_agent
        persona["user_agent"] = chrome_user_agent(os_name)
    persona.update(overrides)
    return persona


class ProfileManager:
    """Create, read, update and delete browser personas backed by SQLite."""

    def __init__(self, db_path=None):
        """Open (creating if needed) the profiles database.

        :param db_path: path to the SQLite file; defaults to
            ``~/.antidetect-browser/profiles.db``.
        """
        self.db_path = db_path or _default_db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _connect(self):
        """Return a fresh SQLite connection to the database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create the ``profiles`` table if it does not exist.

        Also migrates databases created before the ``client_tag``/
        ``template``/``engine`` columns existed: any missing column is
        added via ``ALTER TABLE``, and pre-existing rows get
        ``engine='camoufox'`` (the historical default).
        """
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    name TEXT PRIMARY KEY,
                    os TEXT,
                    timezone TEXT,
                    locale TEXT,
                    created_at TEXT,
                    fingerprint_json TEXT
                )
                """
            )
            existing = {r["name"]
                        for r in conn.execute("PRAGMA table_info(profiles)")}
            if "client_tag" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN client_tag TEXT")
            if "template" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN template TEXT")
            if "engine" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN engine TEXT")
            if "group_name" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN group_name TEXT")
            if "deleted_at" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN deleted_at REAL")
            if "platform_acct" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN platform_acct TEXT")
            if "startup_urls" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN startup_urls TEXT")
            if "remark" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN remark TEXT")
            if "cookie_json" not in existing:
                conn.execute("ALTER TABLE profiles ADD COLUMN cookie_json TEXT")
            # Legacy rows predate the engine column: they were all
            # camoufox profiles.
            conn.execute(
                "UPDATE profiles SET engine = 'camoufox' "
                "WHERE engine IS NULL OR engine = ''"
            )
            # Groups table (AdsPower-style profile organization).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    name TEXT PRIMARY KEY,
                    remark TEXT,
                    created_at TEXT,
                    created_by TEXT
                )
                """
            )
            # Global preferences / default values for new profiles.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

    # -- global settings -------------------------------------------------
    def get_setting(self, key, default=None):
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?",
                               (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (key, value))

    def all_settings(self):
        with self._connect() as conn:
            return {r["key"]: r["value"] for r in
                    conn.execute("SELECT key, value FROM settings")}

    def _insert(self, persona):
        """Persist a persona; raise ValueError if the name already exists.

        If a soft-deleted (trashed) profile has the same name, it is
        permanently removed first so the name can be reused.

        ``client_tag``/``template``/``engine`` are stored in their own
        columns and are stripped from ``fingerprint_json`` so the data is
        not duplicated.
        """
        # Free up the name if a trashed profile holds it.
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM profiles WHERE name = ? AND deleted_at IS NOT NULL",
                (persona["name"],),
            )
        clean = {k: v for k, v in persona.items() if k not in _META_KEYS}
        row = {
            "name": persona["name"],
            "os": persona["os"],
            "timezone": persona.get("timezone"),
            "locale": persona.get("locale"),
            "created_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(),
            "fingerprint_json": json.dumps(clean),
            "client_tag": persona.get("client_tag"),
            "template": persona.get("template"),
            "engine": persona.get("engine") or "camoufox",
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO profiles
                        (name, os, timezone, locale, created_at,
                         fingerprint_json, client_tag, template, engine)
                    VALUES
                        (:name, :os, :timezone, :locale, :created_at,
                         :fingerprint_json, :client_tag, :template, :engine)
                    """,
                    row,
                )
        except sqlite3.IntegrityError:
            raise ValueError(
                "profile '%s' already exists" % persona["name"]
            )

    def _row_to_persona(self, row):
        """Rebuild the full persona dict from a database row."""
        persona = json.loads(row["fingerprint_json"])
        persona["name"] = row["name"]  # PK is authoritative
        # Columns are authoritative for meta keys; fall back to defaults
        # when the row predates the migration (defensive: _init_db always
        # migrates, and backfills engine='camoufox').
        keys = row.keys()
        persona["client_tag"] = (row["client_tag"]
                                 if "client_tag" in keys else None)
        persona["template"] = (row["template"]
                               if "template" in keys else None)
        persona["engine"] = (row["engine"]
                             if "engine" in keys and row["engine"]
                             else "camoufox")
        persona["group_name"] = (row["group_name"]
                                 if "group_name" in keys else None)
        persona["platform_acct"] = (row["platform_acct"]
                                    if "platform_acct" in keys else None)
        persona["remark"] = (row["remark"]
                             if "remark" in keys else None)
        persona["cookie_json"] = (row["cookie_json"]
                                  if "cookie_json" in keys else None)
        if "startup_urls" in keys and row["startup_urls"]:
            try:
                persona["startup_urls"] = json.loads(row["startup_urls"])
            except (ValueError, TypeError):
                persona["startup_urls"] = []
        else:
            persona["startup_urls"] = []
        persona["deleted_at"] = (row["deleted_at"]
                                 if "deleted_at" in keys else None)
        return persona

    def create(self, name, os="windows", proxy=None, client_tag=None,
               template=None, generator="high_entropy", engine="camoufox",
               **overrides):
        """Create and persist a new persona.

        :param name: unique profile name; must be a non-empty string.
        :param os: one of ``'windows'``, ``'macos'``, ``'linux'``.
        :param proxy: proxy dict (host/port/username/password/type) or None.
        :param client_tag: agency client label, stored in its own column.
        :param template: template name the profile was built from, stored in
            its own column.  Neither is duplicated inside ``fingerprint_json``.
        :param generator: ``"high_entropy"`` (default) uses
            :func:`src.fingerprints.generator.generate_persona`;
            ``"browserforge"`` keeps the pre-Phase-3 browserforge path.
        :param engine: ``"camoufox"`` (default) or ``"patchright"``; stored
            in its own column and selects the persona UA family
            (Firefox vs Chromium).
        :param overrides: extra fields merged into the persona (win).
        :return: the persona dict (including ``client_tag``/``template``/
            ``engine``).
        :raises ValueError: on duplicate name, empty name, unknown os or
            unknown engine.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if os not in _VALID_OS:
            raise ValueError("os must be one of %s" % (_VALID_OS,))
        if engine not in _VALID_ENGINES:
            raise ValueError("engine must be one of %s" % (_VALID_ENGINES,))
        # Belt and braces: the named params above already bind these, but a
        # caller could not reach here with them inside overrides otherwise.
        overrides.pop("client_tag", None)
        overrides.pop("template", None)
        overrides.pop("generator", None)
        overrides.pop("engine", None)
        persona = _generate_persona(name, os, proxy, overrides,
                                    generator=generator, engine=engine)
        persona["client_tag"] = client_tag
        persona["template"] = template
        persona["engine"] = engine
        self._insert(persona)
        return persona

    def get(self, name):
        """Return the persona dict for ``name``.

        :raises KeyError: if no profile with ``name`` exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise KeyError(name)
        return self._row_to_persona(row)

    def list(self, include_deleted=False):
        """Return all stored personas as a list of dicts.

        Excludes soft-deleted (trashed) profiles unless
        ``include_deleted`` is True.
        """
        with self._connect() as conn:
            if include_deleted:
                rows = conn.execute(
                    "SELECT * FROM profiles ORDER BY name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM profiles WHERE deleted_at IS NULL"
                    " ORDER BY name"
                ).fetchall()
        return [self._row_to_persona(row) for row in rows]

    def delete(self, name, permanent=False):
        """Delete the profile ``name``.

        By default this is a soft delete (moves to trash, ``deleted_at``
        timestamp). Pass ``permanent=True`` to remove immediately.

        :return: True if a profile was deleted, False if not found.
        """
        import time
        with self._connect() as conn:
            if permanent:
                cursor = conn.execute(
                    "DELETE FROM profiles WHERE name = ?", (name,)
                )
            else:
                cursor = conn.execute(
                    "UPDATE profiles SET deleted_at = ?"
                    " WHERE name = ? AND deleted_at IS NULL",
                    (time.time(), name),
                )
        return cursor.rowcount > 0

    def restore(self, name):
        """Restore a trashed profile.

        :return: True if restored, False if not found in trash.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE profiles SET deleted_at = NULL"
                " WHERE name = ? AND deleted_at IS NOT NULL",
                (name,),
            )
        return cursor.rowcount > 0

    def list_trash(self):
        """Return trashed profiles (soft-deleted), newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profiles WHERE deleted_at IS NOT NULL"
                " ORDER BY deleted_at DESC"
            ).fetchall()
        return [self._row_to_persona(row) for row in rows]

    def purge_trash(self, older_than_days=30):
        """Permanently delete trash older than N days. Returns count."""
        import time
        cutoff = time.time() - older_than_days * 86400
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM profiles WHERE deleted_at IS NOT NULL"
                " AND deleted_at < ?", (cutoff,),
            )
        return cursor.rowcount

    def update(self, name, **fields):
        """Merge ``fields`` into the stored persona and persist it.

        The ``name`` primary key cannot be changed: attempting to pass
        ``name=...`` raises ``TypeError`` (duplicate argument binding), which
        guarantees the stored name always matches the row key.  Fields may
        also introduce new keys.  ``client_tag``/``template``/``engine`` are
        stored in their dedicated columns (kept out of
        ``fingerprint_json``); every
        other field lands in the JSON blob.

        :return: the updated persona dict.
        :raises KeyError: if no profile with ``name`` exists.
        :raises ValueError: on an unknown ``engine`` value.
        """
        client_tag = fields.pop("client_tag", _UNSET)
        template = fields.pop("template", _UNSET)
        engine = fields.pop("engine", _UNSET)
        if engine is not _UNSET and engine not in _VALID_ENGINES:
            raise ValueError("engine must be one of %s" % (_VALID_ENGINES,))
        persona = self.get(name)  # raises KeyError when missing
        persona.update(fields)
        if client_tag is not _UNSET:
            persona["client_tag"] = client_tag
        if template is not _UNSET:
            persona["template"] = template
        if engine is not _UNSET:
            persona["engine"] = engine
        clean = {k: v for k, v in persona.items() if k not in _META_KEYS}
        set_clause = ("os = :os, timezone = :timezone, locale = :locale, "
                      "fingerprint_json = :fingerprint_json")
        params = {
            "name": persona["name"],
            "os": persona["os"],
            "timezone": persona.get("timezone"),
            "locale": persona.get("locale"),
            "fingerprint_json": json.dumps(clean),
        }
        if client_tag is not _UNSET:
            set_clause += ", client_tag = :client_tag"
            params["client_tag"] = client_tag
        if template is not _UNSET:
            set_clause += ", template = :template"
            params["template"] = template
        if engine is not _UNSET:
            set_clause += ", engine = :engine"
            params["engine"] = engine
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET %s WHERE name = :name" % set_clause,
                params,
            )
        return persona

    def bulk_create(self, items):
        """Create many profiles, never raising on per-item failures.

        :param items: list of dicts, each of the form
            ``{'name': ..., 'os': ..., 'proxy': ..., 'client_tag': ...,
            'template': ..., 'engine': ..., **overrides}``.  Only ``name`` is
            required; ``os`` defaults to ``'windows'``, ``engine`` to
            ``'camoufox'`` and the rest default to None.
        :return: ``{'created': int, 'skipped': list, 'errors': list}`` where
            ``skipped`` holds names that already existed (duplicates do not
            raise) and ``errors`` holds ``{'name': ..., 'error': str}`` dicts
            for every other failure (empty name, unknown os, ...).  The
            caller's ``items`` dicts are not mutated.
        """
        result = {"created": 0, "skipped": [], "errors": []}
        for item in items:
            item = dict(item)
            name = item.pop("name", None)
            try:
                self.create(
                    name,
                    os=item.pop("os", "windows"),
                    proxy=item.pop("proxy", None),
                    client_tag=item.pop("client_tag", None),
                    template=item.pop("template", None),
                    engine=item.pop("engine", "camoufox"),
                    **item,
                )
                result["created"] += 1
            except ValueError as exc:
                # Duplicate name -> skip; any other ValueError (empty name,
                # unknown os) -> error.  Distinguish by re-reading: if the
                # profile now exists, the insert failed on the PK.
                try:
                    self.get(name)
                except (KeyError, TypeError):
                    result["errors"].append(
                        {"name": name, "error": str(exc)})
                else:
                    result["skipped"].append(name)
            except Exception as exc:  # never let one bad row stop the batch
                result["errors"].append({"name": name, "error": str(exc)})
        return result

    def list_by_tag(self, tag):
        """Return all personas whose ``client_tag`` equals ``tag``.

        :param tag: client label to filter by (exact match).
        :return: list of persona dicts, ordered by name.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profiles WHERE client_tag = ? ORDER BY name",
                (tag,),
            ).fetchall()
        return [self._row_to_persona(row) for row in rows]

    # ------------------------------------------------------------------
    # Groups (AdsPower-style profile organization)
    # ------------------------------------------------------------------

    def create_group(self, name, remark=None, created_by=None):
        """Create a new profile group."""
        if not name or not name.strip():
            raise ValueError("group name must be a non-empty string")
        name = name.strip()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO groups (name, remark, created_at, created_by)"
                    " VALUES (?, ?, ?, ?)",
                    (name, remark,
                     datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     created_by),
                )
        except sqlite3.IntegrityError:
            raise ValueError("group '%s' already exists" % name)
        return {"name": name, "remark": remark}

    def list_groups(self):
        """Return all groups with profile counts, ordered by name."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT g.name, g.remark, g.created_at, g.created_by,
                       COUNT(p.name) AS profile_count
                FROM groups g
                LEFT JOIN profiles p ON p.group_name = g.name
                GROUP BY g.name
                ORDER BY g.name
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def update_group(self, name, remark=None):
        """Update a group's remark."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE groups SET remark = ? WHERE name = ?",
                (remark, name),
            )
            if cur.rowcount == 0:
                raise ValueError("group '%s' not found" % name)

    def delete_group(self, name):
        """Delete a group; profiles in it become ungrouped."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET group_name = NULL WHERE group_name = ?",
                (name,),
            )
            cur = conn.execute("DELETE FROM groups WHERE name = ?", (name,))
            if cur.rowcount == 0:
                raise ValueError("group '%s' not found" % name)

    def assign_group(self, profile_name, group_name):
        """Assign a profile to a group (None to ungroup)."""
        if group_name is not None:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM groups WHERE name = ?", (group_name,)
                ).fetchone()
            if not exists:
                raise ValueError("group '%s' not found" % group_name)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE profiles SET group_name = ? WHERE name = ?",
                (group_name, profile_name),
            )
            if cur.rowcount == 0:
                raise ValueError("profile '%s' not found" % profile_name)

    def list_by_group(self, group_name):
        """Return all personas in a group, ordered by name."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profiles WHERE group_name = ? ORDER BY name",
                (group_name,),
            ).fetchall()
        return [self._row_to_persona(row) for row in rows]
