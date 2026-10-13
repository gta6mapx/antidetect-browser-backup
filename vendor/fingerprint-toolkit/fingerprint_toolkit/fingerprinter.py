"""
Core fingerprint randomization engine.
Generates realistic, coherent browser fingerprints and injects them.
"""

import random
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .scripts import ScriptGenerator


@dataclass
class FingerprintProfile:
    """A complete browser fingerprint profile."""
    canvas_noise: tuple = (0, 0, 0)
    webgl_vendor: str = ""
    webgl_renderer: str = ""
    hardware_concurrency: int = 8
    device_memory: int = 8
    max_touch_points: int = 0
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    pixel_depth: int = 24
    audio_noise: float = 0.0
    platform: str = "Win32"
    languages: list = field(default_factory=lambda: ["en-US", "en"])
    timezone_offset: int = -480
    fonts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class FingerprintKit:
    """
    Browser fingerprint randomization toolkit.

    Generates realistic, consistent fingerprint profiles and injects
    anti-fingerprinting JavaScript into browser automation frameworks.

    Usage:
        fpk = FingerprintKit()
        profile = fpk.generate()
        fpk.inject(driver)  # Selenium
    """

    # GPU database: vendor → [renderers]
    GPU_DB = {
        "Intel Inc.": [
            "Intel(R) UHD Graphics 620",
            "Intel(R) UHD Graphics 630",
            "Intel(R) Iris(R) Xe Graphics",
            "Intel(R) Iris(R) Plus Graphics 640",
            "Intel(R) HD Graphics 620",
            "Intel(R) HD Graphics 630",
            "Intel(R) Arc(TM) Graphics",
        ],
        "NVIDIA Corporation": [
            "NVIDIA GeForce GTX 1650",
            "NVIDIA GeForce GTX 1660",
            "NVIDIA GeForce GTX 1660 Ti",
            "NVIDIA GeForce RTX 2060",
            "NVIDIA GeForce RTX 3060",
            "NVIDIA GeForce RTX 3070",
            "NVIDIA GeForce RTX 4060",
            "NVIDIA GeForce RTX 4070",
        ],
        "AMD": [
            "AMD Radeon RX 580",
            "AMD Radeon RX 6600",
            "AMD Radeon RX 6700 XT",
            "AMD Radeon RX 7600",
            "AMD Radeon(TM) Graphics",
        ],
        "Apple Inc.": [
            "Apple M1",
            "Apple M1 Pro",
            "Apple M2",
            "Apple M2 Pro",
            "Apple M3",
        ],
    }

    # Screen resolutions (matching common real-world devices)
    RESOLUTIONS = [
        {"width": 1920, "height": 1080},   # Full HD
        {"width": 1366, "height": 768},    # Laptop
        {"width": 1440, "height": 900},    # MacBook
        {"width": 1536, "height": 864},    # Laptop
        {"width": 2560, "height": 1440},   # QHD
        {"width": 2560, "height": 1600},   # MacBook Retina
        {"width": 1680, "height": 1050},   # Desktop
    ]

    # Platform-consistent settings
    PLATFORM_CONFIGS = {
        "Win32": {
            "vendor": "Google Inc.",
            "languages": ["en-US", "en"],
            "timezone_range": (-480, -300),  # US timezones
        },
        "MacIntel": {
            "vendor": "Google Inc.",
            "languages": ["en-US", "en"],
            "timezone_range": (-480, -300),
        },
        "Linux x86_64": {
            "vendor": "Google Inc.",
            "languages": ["en-US", "en"],
            "timezone_range": (-480, -300),
        },
    }

    COMMON_FONTS = [
        "Arial", "Helvetica", "Times New Roman", "Courier New",
        "Verdana", "Georgia", "Comic Sans MS", "Trebuchet MS",
        "Lucida Console", "Tahoma", "Impact", "Segoe UI",
    ]

    def __init__(self, seed: Optional[int] = None, profile: Optional[FingerprintProfile] = None):
        """
        Initialize the fingerprint kit.

        Args:
            seed: Random seed for reproducible profiles.
            profile: Use a pre-defined profile instead of generating one.
        """
        self._seed = seed if seed is not None else int(time.time() * 1000) % 100000
        self._rng = random.Random(self._seed)

        if profile:
            self.profile = profile
        else:
            self.profile = self.generate()

        self._scripts = ScriptGenerator(self.profile)

    def generate(self) -> FingerprintProfile:
        """Generate a random but coherent fingerprint profile."""
        r = self._rng

        # Pick GPU vendor first, then a matching renderer
        vendor = r.choice(list(self.GPU_DB.keys()))
        renderer = r.choice(self.GPU_DB[vendor])

        # Pick platform based on vendor
        if vendor == "Apple Inc.":
            platform = "MacIntel"
            resolution = r.choice([
                {"width": 1440, "height": 900},
                {"width": 2560, "height": 1600},
                {"width": 1680, "height": 1050},
            ])
            concurrency = r.choice([8, 10, 12])
            memory = r.choice([8, 16, 32, 64])
        else:
            platform = r.choice(["Win32", "Linux x86_64"])
            resolution = r.choice(self.RESOLUTIONS)
            concurrency = r.choice([2, 4, 6, 8, 12, 16])
            memory = r.choice([4, 8, 16, 32])

        platform_config = self.PLATFORM_CONFIGS.get(platform, self.PLATFORM_CONFIGS["Win32"])

        # Timezone
        tz_min, tz_max = platform_config["timezone_range"]
        tz_offset = r.randint(tz_min, tz_max)

        # Random fonts subset
        num_fonts = r.randint(6, len(self.COMMON_FONTS))
        fonts = r.sample(self.COMMON_FONTS, min(num_fonts, len(self.COMMON_FONTS)))

        return FingerprintProfile(
            canvas_noise=(r.randint(1, 10), r.randint(1, 10), r.randint(1, 10)),
            webgl_vendor=vendor,
            webgl_renderer=renderer,
            hardware_concurrency=concurrency,
            device_memory=memory,
            max_touch_points=r.choice([0, 0, 0, 1, 5, 10]),
            screen_width=resolution["width"],
            screen_height=resolution["height"],
            color_depth=r.choice([24, 30, 32]),
            pixel_depth=r.choice([24, 30]),
            audio_noise=round(r.uniform(0.00001, 0.0001), 8),
            platform=platform,
            languages=platform_config["languages"][:],
            timezone_offset=tz_offset,
            fonts=fonts,
        )

    def to_script(self) -> str:
        """Get the complete JavaScript injection script as a string."""
        return self._scripts.build()

    def inject(self, target) -> bool:
        """
        Inject fingerprint randomization into a browser automation target.

        Supports:
            - Selenium WebDriver (via CDP Page.addScriptToEvaluateOnNewDocument)
            - Playwright Page (via page.add_init_script)
            - Any object with .execute_cdp_cmd() method

        Args:
            target: Selenium WebDriver, Playwright Page, or CDP-compatible object.

        Returns:
            True if injection succeeded, False otherwise.
        """
        script = self.to_script()

        try:
            # Playwright
            if hasattr(target, 'add_init_script'):
                target.add_init_script(script)
                return True

            # Selenium / CDP
            if hasattr(target, 'execute_cdp_cmd'):
                target.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                    'source': script
                })
                return True

            # Fallback: try CDP via execute_script
            if hasattr(target, 'execute_script'):
                target.execute_script(script)
                return True

        except Exception:
            pass

        return False

    def get_profile(self) -> FingerprintProfile:
        """Return the current fingerprint profile."""
        return self.profile

    def get_profile_json(self) -> str:
        """Return the current profile as JSON string."""
        return self.profile.to_json()

    def save_profile(self, path: str):
        """Save fingerprint profile to a JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.profile.to_json())

    @classmethod
    def from_profile_file(cls, path: str) -> 'FingerprintKit':
        """Create a FingerprintKit from a saved profile JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        profile = FingerprintProfile(**data)
        return cls(profile=profile)
