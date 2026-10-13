#!/usr/bin/env python3
"""Real-browser fingerprint test: launch Camoufox with a generated persona,
read actual browser signals via JS, and compare against the persona +
ConsistencyValidator. Run: .venv/bin/python tests/test_real_browser.py"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))
os.environ.setdefault("ANTIDETECT_HOME", "/tmp/antidetect-qa")

from src.profiles.manager import ProfileManager
from src.fingerprints.validator import ConsistencyValidator
from src.browser.launcher import launch_profile

JS = """() => {
  const out = {};
  out.userAgent = navigator.userAgent;
  out.platform = navigator.platform;
  out.language = navigator.language;
  out.languages = navigator.languages;
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory || null;
  out.maxTouchPoints = navigator.maxTouchPoints;
  out.webdriver = navigator.webdriver;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.screen = {w: screen.width, h: screen.height, d: screen.colorDepth};
  out.window = {iw: window.innerWidth, ow: window.outerWidth};
  try {
    const c = document.createElement('canvas');
    const g = c.getContext('webgl') || c.getContext('experimental-webgl');
    const dbg = g.getExtension('WEBGL_debug_renderer_info');
    out.webgl = {
      vendor: dbg ? g.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : g.getParameter(g.VENDOR),
      renderer: dbg ? g.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER),
    };
  } catch(e) { out.webgl = {error: String(e)}; }
  try {
    const c2 = document.createElement('canvas');
    const x = c2.getContext('2d');
    x.textBaseline = 'top'; x.font = '14px Arial';
    x.fillText('fingerprint-test-123', 2, 2);
    out.canvas_sample = c2.toDataURL().slice(0, 80);
  } catch(e) { out.canvas_sample = 'error'; }
  return out;
}"""

def main():
    pm = ProfileManager()
    try: pm.delete("qa-real")
    except Exception: pass
    persona = pm.create("qa-real", os="windows")
    print("persona UA:", persona["user_agent"][:80])
    print("persona webgl:", persona["webgl_vendor"], "/", persona["webgl_renderer"][:60])
    print("persona tz:", persona["timezone"], "| viewport:", persona["viewport"])

    v = ConsistencyValidator().validate(persona)
    passed = sum(1 for r in v if r["passed"])
    print(f"validator: {passed}/{len(v)}")

    print("launching real browser (headless)...")
    with launch_profile(persona, headless=True) as b:
        b.page.goto("about:blank")
        sig = b.page.evaluate(JS)

    print("\n--- real browser signals ---")
    print("UA:      ", sig["userAgent"][:100])
    print("platform:", sig["platform"], "| lang:", sig["language"], "| tz:", sig["timezone"])
    print("webgl:   ", sig["webgl"].get("vendor"), "/", str(sig["webgl"].get("renderer"))[:70])
    print("screen:  ", sig["screen"], "| window:", sig["window"])
    print("webdriver:", sig["webdriver"], "| touch:", sig["maxTouchPoints"], "| cores:", sig["hardwareConcurrency"])

    print("\n--- persona vs reality ---")
    checks = [
        ("UA contains Firefox", "Firefox/" in sig["userAgent"] and "Firefox/" in persona["user_agent"]),
        ("timezone match", sig["timezone"] == persona["timezone"]),
        ("webdriver hidden", sig["webdriver"] in (False, None)),
        ("webgl vendor sane", bool(sig["webgl"].get("vendor"))),
        ("canvas rendered", sig["canvas_sample"] != "error"),
    ]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    pm.delete("qa-real")
    print("\ndone.")

if __name__ == "__main__":
    main()
