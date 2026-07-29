#!/usr/bin/env python3
"""One-time generator for the dashboard's optional write-access token.

    venv/bin/python scripts/dashboard/generate_token.py

Writes .dashboard_token at the project root (chmod 600). This script is only
needed when DATABOSS_DASHBOARD_REQUIRE_TOKEN=1. In that opt-in mode, every
mutating dashboard endpoint (Phase 17C/17D - launch a run, edit a scenario,
etc.) requires this value in an `X-Databoss-Token` header; GET/read endpoints
never need it. Refuses to overwrite an existing token silently - pass --force
to rotate.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from databoss_sim.dashboard.config import WRITE_TOKEN_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing token (rotates it)")
    args = parser.parse_args()

    if WRITE_TOKEN_PATH.exists() and not args.force:
        print(f"{WRITE_TOKEN_PATH} already exists - pass --force to rotate it", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(32)
    WRITE_TOKEN_PATH.write_text(token + "\n")
    WRITE_TOKEN_PATH.chmod(0o600)
    print(f"Wrote {WRITE_TOKEN_PATH} (chmod 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
