"""Browser engine interface.

An *engine* knows how to launch a browser for a stored persona and how to
report the version of the browser binary it would use. Engines never
generate personas and never validate them — that is the job of
:mod:`src.fingerprints.generator` and :mod:`src.fingerprints.validator`,
which are engine-aware via the profile's ``engine`` field.

Available engines:

- ``"camoufox"`` — Firefox-based anti-detect browser (default). Spoofs
  fingerprints per persona via fpgen.
- ``"patchright"`` — patched Playwright driver on stock Chromium. Hides
  automation tells at the protocol/driver layer; does NOT spoof
  fingerprints (personas carry honest, internally consistent Chromium
  signals instead).
"""

from src.engines.base import Engine
from src.engines.camoufox import CamoufoxEngine
from src.engines.patchright_engine import PatchrightEngine

__all__ = [
    "Engine",
    "CamoufoxEngine",
    "PatchrightEngine",
    "get_engine",
    "normalize_engine_name",
    "ENGINE_NAMES",
]

#: Canonical engine names stored in the profile ``engine`` field.
ENGINE_NAMES = ("camoufox", "patchright")

#: User-facing aliases accepted wherever an engine name is given
#: (CLI ``--engine``, GUI selector, :func:`get_engine`).
_ENGINE_ALIASES = {
    "camoufox": "camoufox",
    "firefox": "camoufox",
    "patchright": "patchright",
    "chromium": "patchright",
    "chrome": "patchright",
}

_ENGINES = {
    "camoufox": CamoufoxEngine,
    "patchright": PatchrightEngine,
}


def normalize_engine_name(name) -> str:
    """Map a user-supplied engine name/alias to a canonical engine name.

    :raises ValueError: on an unknown engine name.
    """
    if not isinstance(name, str):
        raise ValueError("engine name must be a string, got %r" % (name,))
    canonical = _ENGINE_ALIASES.get(name.strip().lower())
    if canonical is None:
        raise ValueError(
            "unknown engine %r; expected one of %s"
            % (name, sorted(_ENGINE_ALIASES))
        )
    return canonical


def get_engine(name="camoufox") -> Engine:
    """Return the :class:`Engine` implementation for ``name``.

    Accepts canonical names (``"camoufox"``, ``"patchright"``) and the
    aliases in :func:`normalize_engine_name` (``"firefox"``,
    ``"chromium"``, ``"chrome"``).

    :raises ValueError: on an unknown engine name.
    """
    return _ENGINES[normalize_engine_name(name)]()
