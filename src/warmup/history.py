"""Offline Firefox history seeder (Phase 6).

Design adapted from nazak-browser-studio ``nazak/core/history_seeder.py``
(MIT; see ``vendor/nazak-warmup/ATTRIBUTION.md``): randomized visits over the
past 1–14 days, visit chaining, realistic dwell, high-trust site list.

Schema rewritten for **Firefox**: upstream writes Chromium's ``History``
SQLite (WebKit-epoch timestamps, Chromium ``PageTransition`` constants);
Firefox uses ``places.sqlite`` with ``moz_places`` / ``moz_historyvisits``,
PRTime (microseconds since the **Unix** epoch), and Firefox ``visit_type``
codes. Stdlib only — no browser, no network.

The database is written to ``<profile_dir>/places.sqlite`` where
``profile_dir`` is ``~/.antidetect-browser/profiles/<name>/`` — the same
``user_data_dir`` Camoufox/Playwright's ``launch_persistent_context`` uses.
For Firefox, ``launch_persistent_context``'s user-data dir *is* the profile
dir, so ``places.sqlite`` sits at its root (verified against Firefox's
profile layout: ``<profile>/places.sqlite``).
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from src import paths as _paths

import logging
import os
import random
import sqlite3
import string
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Firefox nsINavHistoryService visit_type codes
# (toolkit/components/places/nsINavHistoryService.idl)
VISIT_LINK = 1
VISIT_TYPED = 2
VISIT_BOOKMARK = 3
VISIT_EMBED = 4
VISIT_REDIRECT_PERMANENT = 5
VISIT_REDIRECT_TEMPORARY = 6
VISIT_DOWNLOAD = 7
VISIT_FRAMED_LINK = 8
VISIT_RELOAD = 9

# Pools mirroring upstream's intent: first visits look "typed", follow-ups
# look like link clicks; RELOAD stays rare.
_FIRST_VISIT_TYPES = (VISIT_TYPED, VISIT_TYPED, VISIT_LINK)
_FOLLOWUP_VISIT_TYPES = (VISIT_LINK, VISIT_LINK, VISIT_FRAMED_LINK, VISIT_RELOAD)

# High-trust sites taken from upstream's HIGH_TRUST_SITES (same design;
# offline DB writes only, no traffic is generated).
HIGH_TRUST_SITES = [
    ("https://www.google.com/", "Google", "search"),
    ("https://www.google.com/search?q=weather+forecast+this+week", "weather forecast this week - Google Search", "search"),
    ("https://www.google.com/search?q=best+laptops+2026", "best laptops 2026 - Google Search", "search"),
    ("https://www.google.com/search?q=wikipedia+world+history", "wikipedia world history - Google Search", "search"),
    ("https://en.wikipedia.org/wiki/Main_Page", "Wikipedia, the free encyclopedia", "organic"),
    ("https://en.wikipedia.org/wiki/Computer_science", "Computer science - Wikipedia", "organic"),
    ("https://en.wikipedia.org/wiki/Artificial_intelligence", "Artificial intelligence - Wikipedia", "organic"),
    ("https://github.com/", "GitHub: Let's build from here", "tech"),
    ("https://github.com/trending", "Trending repositories on GitHub today", "tech"),
    ("https://stackoverflow.com/", "Stack Overflow - Where Developers Learn, Share, & Build Careers", "tech"),
    ("https://developer.mozilla.org/en-US/", "MDN Web Docs", "tech"),
    ("https://www.reddit.com/", "Reddit - Dive into anything", "social"),
    ("https://www.reddit.com/r/technology/", "Technology - Reddit", "social"),
    ("https://www.reddit.com/r/news/", "Real-Time News - Reddit", "social"),
    ("https://www.youtube.com/", "YouTube", "video"),
    ("https://www.youtube.com/feed/trending", "Trending - YouTube", "video"),
    ("https://news.ycombinator.com/", "Hacker News", "tech"),
    ("https://www.bbc.com/news", "BBC News - World", "news"),
    ("https://www.cnn.com/", "CNN - Breaking News, Latest News and Videos", "news"),
    ("https://www.amazon.com/", "Amazon.com. Spend less. Smile more.", "ecommerce"),
    ("https://www.cloudflare.com/", "Cloudflare - The Web Performance & Security Company", "tech"),
    ("https://medium.com/", "Medium - Where good ideas find you.", "reading"),
    ("https://www.quora.com/", "Quora - A place to share knowledge", "social"),
    ("https://twitter.com/", "X", "social"),
    ("https://www.linkedin.com/", "LinkedIn: Log In or Sign Up", "business"),
]

def _profile_dir(profile_name: str) -> Path:
    """Profile user-data dir, honoring ANTIDETECT_HOME (see src.paths)."""
    return Path(_paths.profile_dir(profile_name))


def _prtime_microsec(epoch_sec: float) -> int:
    """PRTime: microseconds since the Unix epoch (Firefox timestamp unit)."""
    return int(epoch_sec * 1_000_000)


def _rev_host(url: str) -> str:
    """Reversed hostname with trailing dot, e.g. ``moc.elgoog.www.``."""
    host = (urlparse(url).hostname or "").lower()
    return host[::-1] + "." if host else ""


def _guid(rng: random.Random) -> str:
    """12-char places GUID (same alphabet Firefox uses)."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(rng.choice(alphabet) for _ in range(12))


def seed_firefox_history(
    profile_dir: str | Path,
    entries_count: int = 30,
    niche: str | None = None,
    seed: int | None = None,
) -> int:
    """Create or populate a Firefox ``places.sqlite`` inside ``profile_dir``.

    Spreads realistic visits across the previous 14 days. Firefox's real
    profile layout keeps ``places.sqlite`` at the root of the profile dir,
    which is exactly our Camoufox ``user_data_dir``
    (``~/.antidetect-browser/profiles/<name>/``).

    Args:
        profile_dir: Profile directory to write ``places.sqlite`` into.
        entries_count: How many distinct URLs to seed (5–``len(HIGH_TRUST_SITES)``).
        niche: Reserved for future niche-weighted site selection (unused).
        seed: Optional seed for reproducible seeding in tests.

    Returns:
        Number of distinct URLs seeded (0 on failure).
    """
    rng = random.Random(seed)
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path = profile_dir / "places.sqlite"

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS moz_places(
                id INTEGER PRIMARY KEY,
                url LONGVARCHAR,
                title LONGVARCHAR,
                rev_host LONGVARCHAR,
                visit_count INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0 NOT NULL,
                typed INTEGER DEFAULT 0 NOT NULL,
                favicon_id INTEGER,
                frecency INTEGER DEFAULT -1 NOT NULL,
                last_visit_date INTEGER,
                guid TEXT,
                foreign_count INTEGER DEFAULT 0 NOT NULL,
                url_hash INTEGER DEFAULT 0 NOT NULL,
                description TEXT,
                preview_image_url TEXT,
                origin_id INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS moz_historyvisits(
                id INTEGER PRIMARY KEY,
                from_visit INTEGER,
                place_id INTEGER,
                visit_date INTEGER,
                visit_type INTEGER,
                session INTEGER
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_places_url_hashindex ON moz_places (url_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_places_reversedhostindex ON moz_places (rev_host)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_historyvisits_placedateindex ON moz_historyvisits (place_id, visit_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_historyvisits_fromindex ON moz_historyvisits (from_visit)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_historyvisits_dateindex ON moz_historyvisits (visit_date)")

        sample_size = min(max(5, entries_count), len(HIGH_TRUST_SITES))
        chosen_sites = rng.sample(HIGH_TRUST_SITES, sample_size)

        now_sec = time.time()
        seeded = 0

        for site_url, site_title, _category in chosen_sites:
            # Randomize the anchor visit within the past 1 to 14 days.
            days_ago = rng.uniform(0.5, 14.0)
            anchor_sec = now_sec - days_ago * 86400

            visit_count = rng.randint(1, 4)
            typed = rng.choice([0, 1])

            cursor.execute("SELECT id, visit_count FROM moz_places WHERE url = ?", (site_url,))
            row = cursor.fetchone()
            if row:
                place_id = row[0]
                new_vc = row[1] + visit_count
            else:
                cursor.execute(
                    """
                    INSERT INTO moz_places
                        (url, title, rev_host, visit_count, hidden, typed,
                         frecency, last_visit_date, guid, url_hash)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        site_url,
                        site_title,
                        _rev_host(site_url),
                        visit_count,
                        typed,
                        rng.randint(500, 9000),  # plausible frecency
                        _prtime_microsec(anchor_sec),
                        _guid(rng),
                        zlib.crc32(site_url.encode("utf-8")) & 0xFFFFFFFF,
                    ),
                )
                place_id = cursor.lastrowid
                new_vc = visit_count

            # Insert visit events with unique timestamps and from_visit chaining
            # (same design as upstream, audit-hardened).
            last_visit_id = 0
            current_sec = anchor_sec
            session = rng.randint(1, 1 << 30)
            for visit_idx in range(visit_count):
                if visit_idx > 0:
                    # Spread follow-up visits 2 hours to 3 days apart.
                    current_sec += rng.uniform(7200, 259200)
                    from_visit_id = last_visit_id if rng.random() < 0.6 else 0
                    visit_type = rng.choice(_FOLLOWUP_VISIT_TYPES)
                else:
                    from_visit_id = 0
                    visit_type = VISIT_TYPED if typed else rng.choice(_FIRST_VISIT_TYPES)
                cursor.execute(
                    """
                    INSERT INTO moz_historyvisits
                        (from_visit, place_id, visit_date, visit_type, session)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (from_visit_id, place_id, _prtime_microsec(current_sec), visit_type, session),
                )
                last_visit_id = cursor.lastrowid

            cursor.execute(
                "UPDATE moz_places SET visit_count = ?, last_visit_date = ? WHERE id = ?",
                (new_vc, _prtime_microsec(anchor_sec), place_id),
            )
            seeded += 1

        conn.commit()
        logger.info("seeded %d history URLs into %s", seeded, db_path)
        return seeded
    except Exception as exc:
        logger.exception("failed to seed Firefox history for %s: %s", profile_dir, exc)
        conn.rollback()
        return 0
    finally:
        conn.close()


def seed_profile_history(
    profile_name: str,
    entries_count: int = 30,
    seed: int | None = None,
) -> int:
    """Seed ``places.sqlite`` for a named profile's persistent directory.

    Args:
        profile_name: Profile name (resolves to
            ``~/.antidetect-browser/profiles/<name>/``).
        entries_count: How many distinct URLs to seed.
        seed: Optional seed for reproducible seeding.

    Returns:
        Number of distinct URLs seeded (0 on failure).
    """
    return seed_firefox_history(
        _profile_dir(profile_name),
        entries_count=entries_count,
        seed=seed,
    )
