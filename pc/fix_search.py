#!/usr/bin/env python3
"""Patch Camoufox's policies.json to enable Google search.

Camoufox intentionally disables search engines via
distribution/policies.json (Remove: [Google, ...], PreventInstalls: true).
This script patches it to allow Google search.

Run once on Windows:
    python pc\\fix_search.py
"""
import json
import os
import sys

def find_camoufox_policies():
    """Find Camoufox policies.json in standard locations."""
    candidates = []
    # Windows: %LOCALAPPDATA%\\camoufox\\...
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        base = os.path.join(local_app, "camoufox")
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if "policies.json" in files and "distribution" in root:
                    candidates.append(os.path.join(root, "policies.json"))
    # Also check .cache (Linux/Mac)
    home = os.path.expanduser("~")
    for cache_dir in [os.path.join(home, ".cache", "camoufox")]:
        if os.path.isdir(cache_dir):
            for root, dirs, files in os.walk(cache_dir):
                if "policies.json" in files and "distribution" in root:
                    candidates.append(os.path.join(root, "policies.json"))
    return candidates

def patch_policies(path):
    """Patch policies.json to enable Google search."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    policies = data.get("policies", {})
    search = policies.get("SearchEngines", {})
    
    # Remove Google from the Remove list.
    remove_list = search.get("Remove", [])
    if "Google" in remove_list:
        remove_list.remove("Google")
        print(f"  Removed 'Google' from Remove list")
    
    # Allow installs.
    if search.get("PreventInstalls"):
        search["PreventInstalls"] = False
        print(f"  Set PreventInstalls=False")
    
    # Add Google as a custom engine.
    search["Add"] = search.get("Add", [])
    # Remove existing Google entry if present.
    search["Add"] = [e for e in search["Add"] if e.get("Name") != "Google"]
    search["Add"].append({
        "Name": "Google",
        "URLTemplate": "https://www.google.com/search?q={searchTerms}",
        "Method": "GET",
        "Alias": "google",
        "Description": "Google Search"
    })
    print(f"  Added Google search engine")
    
    # Set as default.
    search["Default"] = "Google"
    print(f"  Set Default=Google")
    
    policies["SearchEngines"] = search
    data["policies"] = policies
    
    # Backup original.
    backup = path + ".backup"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(json.load(open(path, encoding="utf-8")), f, indent=2)
        print(f"  Backup saved to {backup}")
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  Patched {path}")

def main():
    print("Camoufox Search Fix")
    print("=" * 40)
    candidates = find_camoufox_policies()
    if not candidates:
        print("ERROR: Could not find Camoufox policies.json")
        print("Make sure Camoufox is installed.")
        return 1
    for path in candidates:
        print(f"\nPatching: {path}")
        try:
            patch_policies(path)
        except Exception as e:
            print(f"  ERROR: {e}")
    print("\nDone! Restart the browser for changes to take effect.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
