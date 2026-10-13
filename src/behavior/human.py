"""Human-like interaction primitives.

Behavioral model
----------------
Bot detectors score *how* a page is driven, not just *what* the fingerprint
says. The classic tells are:

* **Instant actions** — ``fill()``/``click()`` with zero delay between
  focus, typing, and submit. Real users focus a field, hesitate, then type
  unevenly.
* **Linear mouse paths** — programmatic ``mouse.move`` traces a perfect
  straight line at constant velocity; human hands move in gentle curves
  with acceleration/deceleration, and routinely *overshoot* a target and
  correct back onto it.
* **Uniform typing cadence** — real keystroke intervals follow a skewed,
  wpm-dependent distribution with occasional long pauses (word boundaries,
  thinking) and rare typo/backspace corrections.
* **Mechanical scrolling** — one big ``window.scrollBy`` jump versus
  several uneven wheel notches with pauses while the user reads.

Each primitive below reproduces one of those irregularities using
:mod:`random` only, is seedable via :func:`new_rng`, and never raises on
best-effort operations (a detector must never be the thing that crashes a
session — failures are swallowed where safe to do so).

The functions operate on a Playwright ``Page`` (as exposed on
``LaunchedProfile.page``); they are synchronous to match the rest of the
codebase's Camoufox ``sync_api`` usage.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

import math
import random
import time

__all__ = [
    "new_rng",
    "micro_pause",
    "idle_break",
    "human_type",
    "human_click",
    "human_scroll",
    "ghost_click",
    "ghost_move_to",
]


def _ghost_cursor(page, start=None):
    """Build a python_ghost_cursor cursor (MIT) for the page.

    Returns None if the library is unavailable — callers fall back to
    the built-in bezier implementation.
    """
    try:
        from python_ghost_cursor.playwright_sync import create_cursor
    except ImportError:
        return None
    try:
        if start is not None:
            return create_cursor(page, {"x": start[0], "y": start[1]})
        return create_cursor(page)
    except Exception:
        return None


def ghost_click(page, selector, seed=None):
    """Click via python_ghost_cursor (MIT, Xetera/ghost-cursor port).

    Bezier path + overshoot + settle, driven by the real OSS library
    instead of our hand-rolled curve. Falls back to :func:`human_click`
    if the library is missing. Never raises.
    """
    _ghost_click_impl(page, selector, seed=seed)


def _ghost_click_impl(page, selector, seed=None):
    rng = new_rng(seed)
    tx = ty = None
    cursor = _ghost_cursor(page, start=_last_mouse.get(id(page)))
    if cursor is None:
        human_click(page, selector, seed=seed)
        return
    try:
        locator = page.locator(selector)
        box = locator.bounding_box()
        if not box:
            human_click(page, selector, seed=seed)
            return
        margin_x = min(box["width"] * 0.15, 8.0)
        margin_y = min(box["height"] * 0.15, 8.0)
        tx = rng.uniform(box["x"] + margin_x, box["x"] + box["width"] - margin_x)
        ty = rng.uniform(box["y"] + margin_y, box["y"] + box["height"] - margin_y)
        cursor.click({"x": tx, "y": ty})
    except Exception:
        try:
            human_click(page, selector, seed=seed)
        except Exception:
            pass
    if tx is not None:
        _last_mouse[id(page)] = (tx, ty)


def ghost_move_to(page, x, y):
    """Humanized move to (x, y) via python_ghost_cursor. Never raises."""
    cursor = _ghost_cursor(page, start=_last_mouse.get(id(page)))
    if cursor is None:
        return
    try:
        cursor.move_to({"x": x, "y": y})
    except Exception:
        pass
    _last_mouse[id(page)] = (x, y)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

#: Adjacent keys on a QWERTY layout, used to pick realistic typo characters.
_ADJACENT_KEYS = {
    "q": "was", "w": "qase", "e": "wsdr", "r": "edft", "t": "rfgy",
    "y": "tghu", "u": "yhji", "i": "ujko", "o": "iklp", "p": "ol",
    "a": "qwsz", "s": "aqwedxz", "d": "swerfcx", "f": "dertgv",
    "g": "frtyhb", "h": "gtyujn", "j": "hyuikm", "k": "juiolm",
    "l": "kiop", "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb",
    "b": "vghn", "n": "bhjm", "m": "njk",
    "1": "2q", "2": "13qw", "3": "24we", "4": "35er", "5": "46rt",
    "6": "57ty", "7": "68yu", "8": "79ui", "9": "80io", "0": "9p",
}

#: Fraction of keystrokes that become a typo/correction cycle.
_TYPO_PROBABILITY = 0.02

#: Last known mouse position per page (Playwright does not expose it).
_last_mouse = {}


def new_rng(seed=None):
    """Return a ``random.Random`` seeded with ``seed`` (or unseeded).

    Every public function in this module accepts ``seed`` and builds its
    RNG from this helper, so behavior is reproducible when needed and
    truly random otherwise.
    """
    return random.Random(seed) if seed is not None else random.Random()


def _sleep(rng, lo, hi):
    """Sleep a random number of seconds in ``[lo, hi]``."""
    time.sleep(rng.uniform(lo, hi))


def _viewport_size(page):
    """Best-effort viewport size; falls back to a common 1366x768."""
    try:
        vp = page.viewport_size
        if vp and vp.get("width") and vp.get("height"):
            return int(vp["width"]), int(vp["height"])
    except Exception:
        pass
    return 1366, 768


def _typo_char(rng, ch):
    """Return a plausible mistyped neighbor of ``ch`` (same case)."""
    lower = ch.lower()
    pool = _ADJACENT_KEYS.get(lower)
    if not pool:
        return None
    wrong = rng.choice(pool)
    return wrong.upper() if ch.isupper() else wrong


def _bezier_point(p0, p1, p2, p3, t):
    """Evaluate a cubic bezier curve at parameter ``t`` in ``[0, 1]``."""
    mt = 1.0 - t
    x = (mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0]
         + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0])
    y = (mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1]
         + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1])
    return x, y


# --------------------------------------------------------------------------
# pauses
# --------------------------------------------------------------------------

def micro_pause(seed=None):
    """Sleep a short, random 80–300 ms (breather between actions)."""
    rng = new_rng(seed)
    _sleep(rng, 0.08, 0.30)


def idle_break(seconds=(2, 8), seed=None):
    """Sleep a random duration in ``seconds`` range (reading/thinking)."""
    rng = new_rng(seed)
    _sleep(rng, float(seconds[0]), float(seconds[1]))


# --------------------------------------------------------------------------
# typing
# --------------------------------------------------------------------------

def human_type(page, selector, text, wpm=(40, 80), seed=None):
    """Click a field and type ``text`` like a person.

    Per-keystroke delay: a target words-per-minute is sampled from
    ``wpm`` for the whole string, converted to ms/char
    (``12000 / wpm``), then multiplied by a jitter factor drawn from a
    lognormal-ish (gauss, clamped positive) distribution — so cadence is
    uneven, not metronomic. Word boundaries get an extra thinking pause.
    About 2% of characters are mistyped as an adjacent key, followed by
    a pause, a Backspace, and the correct character.

    Args:
        page: Playwright Page to act on.
        selector: CSS selector of the input/field.
        text: The string to type.
        wpm: ``(low, high)`` range of typing speed.
        seed: Optional seed for reproducibility.

    Never raises on best-effort ops.
    """
    rng = new_rng(seed)
    text = "" if text is None else str(text)

    try:
        page.locator(selector).click()
    except Exception:
        return
    _sleep(rng, 0.08, 0.35)

    if not text:
        return

    # One typing speed for the whole string (people don't change speed per char).
    target_wpm = rng.uniform(float(wpm[0]), float(wpm[1]))
    base_ms = 12000.0 / max(target_wpm, 1.0)

    for ch in text:
        # Uneven cadence: gauss around 1.0, clamped so delays stay positive.
        jitter = max(0.15, rng.gauss(1.0, 0.35))
        _sleep(rng, 0.0, (base_ms * jitter) / 1000.0)

        # Occasional long pause at word boundaries (thinking / reading).
        if ch == " " and rng.random() < 0.25:
            _sleep(rng, 0.25, 1.1)

        # Occasional typo: wrong adjacent key, pause, backspace, correct.
        wrong = _typo_char(rng, ch)
        if wrong is not None and rng.random() < _TYPO_PROBABILITY:
            try:
                page.keyboard.type(wrong)
            except Exception:
                return
            _sleep(rng, 0.12, 0.40)
            try:
                page.keyboard.press("Backspace")
            except Exception:
                return
            _sleep(rng, 0.05, 0.20)

        try:
            page.keyboard.type(ch)
        except Exception:
            return


# --------------------------------------------------------------------------
# clicking
# --------------------------------------------------------------------------

def human_click(page, selector, seed=None):
    """Move the mouse to a random point in the element and click it.

    The cursor travels along a cubic bezier curve (random control points)
    in 12–24 small steps — a curved path with natural acceleration —
    overshoots the target by a few pixels, corrects back, then presses
    with a short random hold. If the element has no bounding box, falls
    back to a plain ``locator.click()`` after a random delay.

    Never raises on best-effort ops.
    """
    rng = new_rng(seed)
    locator = page.locator(selector)

    try:
        box = locator.bounding_box()
    except Exception:
        box = None

    if not box:
        _sleep(rng, 0.05, 0.30)
        try:
            locator.click()
        except Exception:
            pass
        return

    # Random landing point inside the element (inset margin, never dead center).
    margin_x = min(box["width"] * 0.15, 8.0)
    margin_y = min(box["height"] * 0.15, 8.0)
    tx = rng.uniform(box["x"] + margin_x, box["x"] + box["width"] - margin_x)
    ty = rng.uniform(box["y"] + margin_y, box["y"] + box["height"] - margin_y)

    # Starting point: last known mouse position, else a random viewport point.
    width, height = _viewport_size(page)
    start = _last_mouse.get(id(page))
    if start is None:
        start = (rng.uniform(0, width), rng.uniform(0, height))

    # Cubic bezier with two randomized control points, biased toward the path.
    mx, my = (start[0] + tx) / 2.0, (start[1] + ty) / 2.0
    bend = rng.uniform(-120, 120)
    p1 = (start[0] + (tx - start[0]) * 0.3 + bend, start[1] + (ty - start[1]) * 0.3 - bend)
    p2 = (start[0] + (tx - start[0]) * 0.7 - bend, start[1] + (ty - start[1]) * 0.7 + bend)

    steps = rng.randint(12, 24)
    try:
        for i in range(1, steps + 1):
            x, y = _bezier_point(start, p1, p2, (tx, ty), i / steps)
            # Decelerate toward the target: later steps move slower.
            page.mouse.move(x, y)
            _sleep(rng, 0.004, 0.016)

        # Overshoot: drift a few px past the target, then correct back.
        angle = rng.uniform(0, 2 * math.pi)
        over = rng.uniform(4, 14)
        ox, oy = tx + math.cos(angle) * over, ty + math.sin(angle) * over
        for i in range(1, 4):
            x = ox + (tx - ox) * i / 3
            y = oy + (ty - oy) * i / 3
            page.mouse.move(x, y)
            _sleep(rng, 0.008, 0.025)

        page.mouse.down()
        _sleep(rng, 0.04, 0.12)
        page.mouse.up()
    except Exception:
        pass

    _last_mouse[id(page)] = (tx, ty)


# --------------------------------------------------------------------------
# scrolling
# --------------------------------------------------------------------------

def human_scroll(page, distance=None, seed=None):
    """Scroll vertically in uneven chunks with random pauses between them.

    ``distance`` in pixels (negative scrolls up); when ``None`` a random
    300–900 px with random sign is used. Scrolls 2–5 chunks via
    ``mouse.wheel`` with variable per-chunk speed and short pauses while
    the "user" reads, ending with a micro pause.

    Never raises on best-effort ops.
    """
    rng = new_rng(seed)
    if distance is None:
        distance = rng.choice((-1, 1)) * rng.uniform(300, 900)

    chunks = rng.randint(2, 5)
    remaining = float(distance)
    try:
        for i in range(chunks):
            if i == chunks - 1:
                delta = int(round(remaining))
            else:
                # Variable speed: each chunk takes an uneven share.
                delta = int(round(remaining * rng.uniform(0.15, 0.55)))
            remaining -= delta
            page.mouse.wheel(0, delta)
            if i < chunks - 1:
                _sleep(rng, 0.06, 0.30)
    except Exception:
        pass

    _sleep(rng, 0.08, 0.30)
