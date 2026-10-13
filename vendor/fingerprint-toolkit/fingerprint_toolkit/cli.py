"""
CLI interface for fingerprint-toolkit.
Usage: python -m fingerprint_toolkit [generate|inspect|test]
"""

import argparse
import sys
from .fingerprinter import FingerprintKit, FingerprintProfile


def cmd_generate(args):
    """Generate a random fingerprint profile."""
    fpk = FingerprintKit(seed=args.seed)
    profile = fpk.get_profile()

    if args.json:
        print(profile.to_json())
    else:
        print("=== Fingerprint Profile ===")
        for k, v in profile.to_dict().items():
            print(f"  {k}: {v}")

    if args.output:
        fpk.save_profile(args.output)
        print(f"\nSaved to: {args.output}")

    if args.script:
        print("\n=== JavaScript Injection ===")
        print(fpk.to_script())


def cmd_inspect(args):
    """Inspect or reuse a saved profile."""
    fpk = FingerprintKit.from_profile_file(args.file)
    profile = fpk.get_profile()
    print(profile.to_json())


def cmd_test(args):
    """Test fingerprint injection with a browser."""
    print("[INFO] Testing fingerprint with undetected-chromedriver...")
    try:
        import undetected_chromedriver as uc
        driver = uc.Chrome()
        fpk = FingerprintKit()
        ok = fpk.inject(driver)
        print(f"Injection: {'OK' if ok else 'FAILED'}")
        print("Profile:", fpk.get_profile_json())
        driver.get("https://browserleaks.com/canvas")
        input("Press Enter to close browser...")
        driver.quit()
    except ImportError:
        print("Error: undetected-chromedriver not installed. Run: pip install undetected-chromedriver")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        prog="fingerprint-toolkit",
        description="Browser fingerprint randomization toolkit"
    )
    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="Generate a random fingerprint profile")
    p_gen.add_argument("--seed", type=int, help="Random seed")
    p_gen.add_argument("--json", action="store_true", help="Output as JSON")
    p_gen.add_argument("--script", action="store_true", help="Output JS injection script")
    p_gen.add_argument("-o", "--output", help="Save profile to file")
    p_gen.set_defaults(func=cmd_generate)

    p_inspect = sub.add_parser("inspect", help="View a saved profile")
    p_inspect.add_argument("file", help="Profile JSON file")
    p_inspect.set_defaults(func=cmd_inspect)

    p_test = sub.add_parser("test", help="Run a live browser test")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()