"""Fingerprint noise injection for Chromium via fingerprint-toolkit (MIT).

Vendored at ``vendor/fingerprint-toolkit``.
Upstream: https://github.com/xuweizhengo/fingerprint-toolkit (MIT License)

This module generates a per-profile deterministic JS injection script
(canvas/WebGL/audio/fonts/screen/timezone/WebRTC/navigator spoofing)
and exposes it for the Patchright/Chromium engine.
"""

import os
import sys

_VENDOR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "vendor", "fingerprint-toolkit")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from fingerprint_toolkit import FingerprintKit  # noqa: E402


def _seed_from_name(name: str) -> int:
    """Deterministic integer seed from a profile name."""
    import hashlib
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def injection_script(profile_name: str, seed: int = None) -> str:
    """Build the JS fingerprint-noise injection script for a profile.

    Deterministic per profile name unless ``seed`` is given.
    """
    kit = FingerprintKit(seed=seed if seed is not None
                         else _seed_from_name(profile_name))
    kit.generate()
    return kit.to_script()


def profile_json(profile_name: str, seed: int = None) -> dict:
    """Return the generated fingerprint profile as a dict (for Overview UI)."""
    import json
    kit = FingerprintKit(seed=seed if seed is not None
                         else _seed_from_name(profile_name))
    profile = kit.generate()
    return json.loads(profile.to_json())
