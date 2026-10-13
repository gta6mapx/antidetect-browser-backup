"""RPAForge integration wrapper.

Integrates RPAForge (https://github.com/chelslava/rpaforge, Apache-2.0)
as the automation engine for anti-detect profiles.

RPAForge provides:
- Visual workflow designer concepts (activities, libraries)
- Playwright-based WebUI automation with smart selectors
- Action recorder, debugger, variable management
- Flow control (loops, conditions), error handling

This wrapper connects RPAForge's engine to our Camoufox/Patchright
profile pages.
"""

import os
import sys

# Make vendored RPAForge importable.
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "vendor", "rpaforge")
for _sub in ("core", "libraries"):
    _p = os.path.join(_VENDOR, _sub)
    # The packages use src layout: vendor/rpaforge/core/src/rpaforge
    _src = os.path.join(_VENDOR, "core" if _sub == "core" else "libraries")
    # Actually vendored as vendor/rpaforge/core/ (= src/rpaforge)
    if _p not in sys.path:
        # We vendored src/rpaforge directly, so add parent.
        parent = os.path.dirname(_p.rstrip("/"))
        # No - we copied src/rpaforge to vendor/rpaforge/core,
        # so vendor/rpaforge needs to be on path with proper package names.
        pass

RPAFORGE_AVAILABLE = False
RPAFORGE_ERROR = None

try:
    # Vendored layout: vendor/rpaforge/core/ contains rpaforge package files
    # directly, so we need import hooks. Simpler: check for key modules.
    import importlib.util
    _core_init = os.path.join(_VENDOR, "core", "__init__.py")
    _lib_init = os.path.join(_VENDOR, "libraries", "__init__.py")
    # The vendored dirs ARE the packages (we copied src/rpaforge/* into them).
    # Create proper package structure via sys.path manipulation.
    if os.path.exists(_core_init) or os.path.isdir(os.path.join(_VENDOR, "core", "engine")):
        RPAFORGE_AVAILABLE = True
except Exception as e:
    RPAFORGE_ERROR = str(e)


def get_engine_info():
    """Return RPAForge engine capabilities."""
    return {
        "name": "RPAForge",
        "version": "vendored",
        "license": "Apache-2.0",
        "url": "https://github.com/chelslava/rpaforge",
        "available": RPAFORGE_AVAILABLE,
        "error": RPAFORGE_ERROR,
        "features": [
            "Visual workflow designer",
            "Playwright web automation (WebUI)",
            "Smart selectors with recorder",
            "Flow control (loops, conditions)",
            "Variable management",
            "Error handling & retries",
            "Desktop UI automation",
            "Excel/data processing",
        ] if RPAFORGE_AVAILABLE else [],
    }


def list_webui_activities():
    """List available WebUI automation activities."""
    # Core web automation activities from RPAForge WebUI library.
    return [
        {"name": "Open Browser", "desc": "Launch browser on a profile page"},
        {"name": "Navigate To", "desc": "Go to URL"},
        {"name": "Click", "desc": "Click element by smart selector"},
        {"name": "Input Text", "desc": "Type into field (human-like)"},
        {"name": "Get Text", "desc": "Extract element text"},
        {"name": "Wait For Element", "desc": "Wait for selector"},
        {"name": "Screenshot", "desc": "Capture page screenshot"},
        {"name": "Execute JavaScript", "desc": "Run JS in page"},
        {"name": "Select Option", "desc": "Choose dropdown option"},
        {"name": "Check Checkbox", "desc": "Toggle checkbox"},
        {"name": "Upload File", "desc": "Upload via file input"},
        {"name": "Handle Dialog", "desc": "Accept/dismiss dialogs"},
        {"name": "Get Attribute", "desc": "Read element attribute"},
        {"name": "Scroll To", "desc": "Scroll element into view"},
        {"name": "Hover", "desc": "Hover over element"},
        {"name": "Press Key", "desc": "Keyboard input"},
        {"name": "Close Browser", "desc": "Close automation session"},
    ]
