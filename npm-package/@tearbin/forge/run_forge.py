#!/usr/bin/env python3
"""
run_forge.py — entry point for npx / npm-installed forge.

No args / interactive → run the TUI (Textual app)
--help, --version      → show Click CLI help / version
"""

import sys
import os
import argparse

# Resolve: npm-package/@tearbin/forge/run_forge.py → forge/src/forge/
# Need to go up 4 dirs: forge/ → npm-package/ → @tearbin/ → @tearbin/ → npm-package/
root = os.path.dirname(__file__)
for _ in range(4):
    root = os.path.dirname(root)
src = os.path.join(root, "src")
if os.path.exists(src):
    sys.path.insert(0, os.path.dirname(src))

from forge.tui.app import run_tui

import json
from pathlib import Path

pkg_root = Path(__file__).parent
with open(pkg_root / "package.json") as f:
    NPM_VERSION = json.load(f)["version"]

def _get_version() -> str:
    return NPM_VERSION

if __name__ == "__main__":
    # Intercept --version / -V before entering the TUI
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"forge, version {_get_version()}")
        sys.exit(0)

    if "--help" in sys.argv or "-h" in sys.argv:
        print("forge — Production-ready multi-agent CLI. Run 'npx @tearbin/forge --help' for usage.")
        print("Or 'forge --help' after 'npm install -g @tearbin/forge'.")
        sys.exit(0)

    run_tui()
