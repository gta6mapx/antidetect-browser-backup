"""
Selenium example: inject fingerprint randomization.
Requires: pip install undetected-chromedriver selenium
"""
from fingerprint_toolkit import FingerprintKit

try:
    import undetected_chromedriver as uc
except ImportError:
    print("Install: pip install undetected-chromedriver")
    exit(1)

# Generate a random fingerprint
fpk = FingerprintKit()
print("Generated profile:")
print(fpk.get_profile_json())
print()

# Start browser
print("Launching browser...")
options = uc.ChromeOptions()
options.add_argument("--window-size=1920,1080")
driver = uc.Chrome(options=options)

# Inject fingerprint protection BEFORE navigating
fpk.inject(driver)
print("Fingerprint injected!")

# Test at a fingerprint detection site
driver.get("https://browserleaks.com/canvas")
print("Browser opened at browserleaks.com/canvas")
print("Press Ctrl+C to exit...")

try:
    import time
    time.sleep(300)
except KeyboardInterrupt:
    pass
finally:
    driver.quit()