#!/usr/bin/env python3
"""Decrypt saved logins from a Firefox profile directory.

GPL ISOLATION: this wrapper NEVER imports firefox_decrypt. It runs the
vendored upstream script (tools/firefox_decrypt/firefox_decrypt.py, GPL-3.0)
as a separate process via subprocess only.

Usage:
    python pc\\decrypt_logins.py <profile_dir>

Prints the extracted logins as JSON to stdout. Exits non-zero with a clear
error message if the profile dir is missing key4.db (or anything else fails).
"""
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# <repo>/pc -> <repo>
_REPO_ROOT = os.path.dirname(_HERE)
FIREFOX_DECRYPT = os.path.join(
    _REPO_ROOT, "tools", "firefox_decrypt", "firefox_decrypt.py"
)


def fail(message):
    sys.stderr.write("decrypt_logins: ERROR: %s\n" % message)
    sys.exit(1)


def main(argv):
    if len(argv) != 2:
        fail("usage: python pc\\decrypt_logins.py <profile_dir>")
    profile_dir = os.path.abspath(argv[1])

    if not os.path.isdir(profile_dir):
        fail("profile dir not found: %s" % profile_dir)
    if not os.path.isfile(FIREFOX_DECRYPT):
        fail("vendored firefox_decrypt.py not found at %s" % FIREFOX_DECRYPT)
    if not os.path.isfile(os.path.join(profile_dir, "key4.db")):
        fail(
            "no key4.db in %s -- nothing to decrypt "
            "(profile has no saved logins)" % profile_dir
        )
    if not os.path.isfile(os.path.join(profile_dir, "logins.json")):
        fail("no logins.json in %s -- nothing to decrypt" % profile_dir)

    # Subprocess only. Never import firefox_decrypt (GPL isolation).
    try:
        proc = subprocess.run(
            [sys.executable, FIREFOX_DECRYPT, "-n", "-f", "json", profile_dir],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        fail("firefox_decrypt timed out after 120s")
    except OSError as exc:
        fail("could not launch firefox_decrypt: %s" % exc)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        fail("firefox_decrypt failed (exit %d)%s" % (
            proc.returncode, (": " + detail) if detail else ""))

    out = proc.stdout.strip()
    try:
        logins = json.loads(out)
    except json.JSONDecodeError:
        fail("firefox_decrypt returned non-JSON output: %r" % out[:200])

    sys.stdout.write(json.dumps(logins, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
