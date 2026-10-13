"""
Browser Fingerprint Toolkit
============================
Multi-dimensional browser fingerprint randomization for web automation.
Supports Selenium (CDP), Playwright, and standalone JavaScript injection.

Usage:
    from fingerprint_toolkit import FingerprintKit

    fpk = FingerprintKit()
    fpk.inject(driver)          # Selenium/undetected-chromedriver
    fpk.inject(page)            # Playwright
    js = fpk.to_script()        # Standalone JS string
"""

from .fingerprinter import FingerprintKit

__version__ = "1.0.0"
__all__ = ["FingerprintKit"]