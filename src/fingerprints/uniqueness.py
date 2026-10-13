"""Uniqueness checking for anti-detect browser personas.

An agency may run 500-1000 profiles; if two profiles ever share fingerprint
signals (same user agent, same canvas seed, same WebGL renderer, ...),
platforms can link them and ban the whole cluster. This module provides the
pure-logic half of that protection:

* :func:`_signature` -- normalizes a persona down to the small set of
  comparable signals that must be unique across profiles.
* :func:`find_collisions` -- reports every signal a persona shares with any
  already-existing persona.
* :func:`ensure_unique` -- drives a persona factory until it produces a
  persona with zero collisions (or gives up with a detailed error).

This module is pure logic: no I/O, no network, stdlib only.
"""

import src._vendor  # noqa: F401  (must be first: enables vendored imports)

from typing import Any, Callable, Dict, List, Optional

# Signal fields compared between personas, in a fixed order so output is
# stable and deterministic.
_SIGNAL_FIELDS = (
    "user_agent",
    "canvas_seed",
    "webgl_renderer",
    "viewport",
    "fonts_sig",
    "timezone",
)

# Upper bound on fonts kept in the comparable fonts signature; bounds memory
# when personas carry very long font lists.
_MAX_FONTS = 40


def _as_str(value: Any) -> Optional[str]:
    """Normalize a value to a stripped string, or None when missing/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_viewport(viewport: Any) -> Optional[tuple]:
    """Normalize a persona viewport to a ``(width, height)`` int tuple.

    Accepts the persona-contract dict ``{'width': w, 'height': h}`` as well
    as a plain 2-tuple/list. Returns None when the viewport is missing or
    not numeric.
    """
    if viewport is None:
        return None
    if isinstance(viewport, dict):
        width = viewport.get("width")
        height = viewport.get("height")
    elif isinstance(viewport, (tuple, list)) and len(viewport) == 2:
        width, height = viewport
    else:
        return None
    try:
        return (int(width), int(height))
    except (TypeError, ValueError):
        return None


def _normalize_fonts(fonts: Any) -> Optional[tuple]:
    """Normalize a persona font list to a sorted tuple, capped at 40.

    Sorting makes the signature order-independent; the cap bounds the
    signature size. Returns None when the font list is missing or empty.
    """
    if not isinstance(fonts, (list, tuple)) or not fonts:
        return None
    return tuple(sorted(str(font) for font in fonts)[:_MAX_FONTS])


def _signature(persona: dict) -> dict:
    """Build the normalized, comparable uniqueness signature of a persona.

    Args:
        persona: Persona dict following the profiles-manager contract.

    Returns:
        Dict with keys ``user_agent`` (str), ``canvas_seed`` (str),
        ``webgl_renderer`` (str), ``viewport`` ((width, height) tuple),
        ``fonts_sig`` (sorted tuple of up to 40 font names) and ``timezone``
        (str). Any signal that is missing normalizes to None.
    """
    return {
        "user_agent": _as_str(persona.get("user_agent")),
        "canvas_seed": _as_str(persona.get("canvas_seed")),
        "webgl_renderer": _as_str(persona.get("webgl_renderer")),
        "viewport": _normalize_viewport(persona.get("viewport")),
        "fonts_sig": _normalize_fonts(persona.get("fonts")),
        "timezone": _as_str(persona.get("timezone")),
    }


def find_collisions(persona: dict, existing: List[dict]) -> List[dict]:
    """Find every fingerprint signal ``persona`` shares with ``existing``.

    Each signal field of the persona is compared against the same field of
    every persona in ``existing``. Viewports compare by (width, height)
    tuple; font lists compare by their sorted, capped signature tuple.

    Args:
        persona: The candidate persona dict to check.
        existing: List of already-accepted persona dicts.

    Returns:
        A list of ``{'field': str, 'value': str, 'with': str}`` dicts, one
        per (field, colliding profile name) pair. Empty list when the
        persona is clean. The ``'with'`` value is the colliding persona's
        ``name`` (or ``'<unnamed-N>'`` when it has none).

    This is a pure function and never raises on missing keys: a missing
    signal is treated as None and simply skipped (it cannot collide).
    """
    if not isinstance(persona, dict):
        return []
    mine = _signature(persona)
    collisions: List[dict] = []
    for index, other in enumerate(existing or []):
        if not isinstance(other, dict):
            continue
        name = other.get("name")
        other_name = str(name) if name is not None else "<unnamed-%d>" % index
        theirs = _signature(other)
        for field in _SIGNAL_FIELDS:
            my_value = mine.get(field)
            if my_value is None:
                continue  # missing signal: cannot collide, skip
            if my_value == theirs.get(field):
                collisions.append(
                    {
                        "field": field,
                        "value": str(my_value),
                        "with": other_name,
                    }
                )
    return collisions


def ensure_unique(
    make_persona_fn: Callable[[], dict],
    existing: List[dict],
    max_attempts: int = 10,
) -> dict:
    """Generate a persona with zero fingerprint collisions.

    Calls ``make_persona_fn()`` (a zero-argument callable returning a persona
    dict) and returns the first persona that has no collisions against
    ``existing`` *plus* every previously generated attempt in this call, so
    a batch of generated personas is pairwise unique as well.

    Args:
        make_persona_fn: Zero-arg callable producing a persona dict.
        existing: Already-accepted personas the result must not collide
            with.
        max_attempts: Maximum number of factory calls before giving up.

    Returns:
        The first collision-free persona dict.

    Raises:
        ValueError: If ``max_attempts`` is less than 1.
        RuntimeError: If no unique persona was produced within
            ``max_attempts``; the message details the colliding fields of
            the last attempt.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1, got %r" % (max_attempts,))
    accepted = list(existing or [])
    attempts: List[dict] = []
    last_collisions: List[dict] = []
    for _ in range(max_attempts):
        persona = make_persona_fn()
        collisions = find_collisions(persona, accepted + attempts)
        if not collisions:
            return persona
        attempts.append(persona)
        last_collisions = collisions
    details = "; ".join(
        "%s=%r (collides with %s)"
        % (entry["field"], entry["value"], entry["with"])
        for entry in last_collisions
    )
    raise RuntimeError(
        "could not generate a unique persona in %d attempt(s); "
        "last attempt collided on: %s" % (max_attempts, details)
    )
