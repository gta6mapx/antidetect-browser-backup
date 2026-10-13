"""
Playwright example: inject fingerprint randomization.
Requires: pip install playwright && playwright install chromium
"""
import asyncio
from fingerprint_toolkit import FingerprintKit

async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Install: pip install playwright && playwright install chromium")
        return

    fpk = FingerprintKit()
    print("Generated profile:")
    print(fpk.get_profile_json())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        fpk.inject(page)
        print("Fingerprint injected!")

        await page.goto("https://browserleaks.com/canvas")
        print("Browser opened at browserleaks.com/canvas")
        print("Press Ctrl+C to exit...")

        try:
            await asyncio.sleep(300)
        except KeyboardInterrupt:
            pass
        finally:
            await browser.close()

asyncio.run(main())