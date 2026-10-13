"""Human-like interaction behaviors for the anti-detect browser.

This package simulates how a real person drives a browser: hands on the
keyboard and mouse, eyes reading the screen. Bot detectors (and heuristics
like ``page.mouse`` event analysis, input-event cadence checks, and
scroll-velocity profiling) flag *instant* actions, perfectly linear mouse
paths, and metronomic typing cadence. Everything here introduces the
irregularities real users have — nothing here moves the fingerprint itself,
it just makes the *activity* look human.

Current primitives live in :mod:`src.behavior.human`:

* ``human_type``  — click a field, then type char-by-char with per-keystroke
  delays sampled from a wpm-derived distribution, including occasional
  typo/correction cycles.
* ``human_click`` — move the mouse along a curved bezier path with a small
  overshoot-and-correct at the end, clicking a randomized point inside the
  target element's bounding box (never the exact center twice).
* ``human_scroll`` — scroll in variable-speed chunks with random pauses
  instead of one smooth programmatic jump.
* ``micro_pause`` — a short 80–300 ms breather between actions.
* ``idle_break`` — a longer 2–8 s pause simulating reading or thinking.

All timings are randomized through :func:`new_rng`, so every function is
seedable and reproducible. No function uses a fixed ``time.sleep`` value.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from .human import (  # noqa: E402
    human_click,
    human_scroll,
    human_type,
    idle_break,
    micro_pause,
    new_rng,
)

__all__ = [
    "human_type",
    "human_click",
    "human_scroll",
    "micro_pause",
    "idle_break",
    "new_rng",
]
