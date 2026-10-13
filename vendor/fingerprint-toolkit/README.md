# Fingerprint Toolkit

[![PyPI](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Cross--platform-lightgrey.svg)]()

Multi-dimensional browser fingerprint randomization for web automation. Works with **Selenium**, **Playwright**, **undetected-chromedriver**, and any CDP-compatible framework.

> 📖 中文文档: [README_CN.md](README_CN.md)

## Features

| Module | Description |
|--------|-------------|
| 🎨 Canvas | Adds invisible RGB noise to Canvas rendering, changes hash completely |
| 🎮 WebGL | Spoofs GPU vendor and renderer (Intel / NVIDIA / AMD / Apple) |
| 🔊 Audio | Adds microscopic frequency offset to AudioContext oscillators |
| 🖥️ Navigator | Overrides hardwareConcurrency, deviceMemory, platform, languages, webdriver |
| 📺 Screen | Randomizes resolution, color depth, pixel depth |
| 🌐 WebRTC | Prevents real IP leaks via WebRTC |
| 🕐 Timezone | Spoofs timezone string and Intl.DateTimeFormat |
| 🔤 Fonts | Injects custom font list for font fingerprint evasion |
| 🔋 Battery | Spoofs battery level and charging status |
| 🔒 Permissions | Masks notification/clipboard permission states |

## Quick Start

### Install

```bash
pip install git+https://github.com/xuweizhengo/fingerprint-toolkit.git
```

### CLI

```bash
# Generate a random fingerprint
fingerprint-toolkit generate

# Generate and save
fingerprint-toolkit generate -o profile.json

# Output as JSON + injection script
fingerprint-toolkit generate --json --script

# Reuse a saved profile
fingerprint-toolkit inspect profile.json

# Live browser test (requires undetected-chromedriver)
fingerprint-toolkit test
```

### Python API

```python
from fingerprint_toolkit import FingerprintKit

# Generate a random profile
fpk = FingerprintKit()
print(fpk.get_profile_json())

# Inject into Selenium
fpk.inject(driver)   # undetected-chromedriver / selenium-wire

# Inject into Playwright
fpk.inject(page)     # playwright async page

# Get standalone JS script
js_script = fpk.to_script()

# Save / load profiles for consistent sessions
fpk.save_profile("profile.json")
fpk2 = FingerprintKit.from_profile_file("profile.json")
```

### Selenium Example

```python
import undetected_chromedriver as uc
from fingerprint_toolkit import FingerprintKit

driver = uc.Chrome()
FingerprintKit().inject(driver)  # Inject BEFORE navigating
driver.get("https://browserleaks.com/canvas")
```

### Playwright Example

```python
from playwright.sync_api import sync_playwright
from fingerprint_toolkit import FingerprintKit

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    FingerprintKit().inject(page)
    page.goto("https://browserleaks.com/canvas")
```

## Profiles

Each generated fingerprint profile is **coherent** — related settings stay consistent:

```
GPU: Intel → Platform: Windows → Resolutions: desktop-class
GPU: Apple M2 → Platform: MacIntel → Resolutions: MacBook-class
```

Total unique combinations: **2.4M+** (1000 Canvas × 20 GPU × 6 CPU × 4 RAM × 5 Screen)

## Test Sites

Verify fingerprint randomization at:

- https://browserleaks.com/canvas
- https://browserleaks.com/webgl
- https://abrahamjuliot.github.io/creepjs/
- https://amiunique.org/
- https://coveryourtracks.eff.org/

## Architecture

```
JavaScript injection (CDP Page.addScriptToEvaluateOnNewDocument)
       │
       ▼
┌──────────────────────────────────────────┐
│  Canvas  │ WebGL │ Audio │ Navigator     │
│  Screen  │ WebRTC│ TZ    │ Fonts/Battery │
└──────────────────────────────────────────┘
       │
       ▼
  Runs BEFORE any page script — invisible to detection
```

## Responsible Use

Use this toolkit for controlled testing, QA automation, and privacy research on systems you own or are authorized to test. Keep generated profiles consistent with your test scenario, and avoid using fingerprint randomization to impersonate real users, bypass access controls, or violate service terms.

## Disclaimer

This project is for educational purposes and legitimate automation testing. Users are responsible for complying with target website terms of service.



## Related Projects

- [aws-auto-register](https://github.com/xuweizhengo/aws-auto-register) — Automated AWS Builder ID registration. Uses this toolkit for anti-detection.
- [cursor-free-api](https://github.com/xuweizhengo/cursor-free-api) — Convert Cursor free API to OpenAI/Anthropic compatible format
- [llm-api-purity](https://github.com/xuweizhengo/llm-api-purity) — OpenAI and Claude API purity checker
- [skills-hub](https://github.com/xuweizhengo/skills-hub) — AI Skills & MCP marketplace

## License

[MIT](LICENSE)
