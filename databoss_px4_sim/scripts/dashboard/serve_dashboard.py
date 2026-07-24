#!/usr/bin/env python3
"""Launch the DATABOSS dashboard.

    venv/bin/python scripts/dashboard/serve_dashboard.py [--reload]

Binds to this host's Tailscale interface address only (never 0.0.0.0 - this
host has a public IP with no firewall, confirmed 2026-07-24). See
databoss_sim.dashboard.config for the host/port constants.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import uvicorn  # noqa: E402

from databoss_sim.dashboard.config import DASHBOARD_HOST, DASHBOARD_PORT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DASHBOARD_HOST, help=f"default: {DASHBOARD_HOST}")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT, help=f"default: {DASHBOARD_PORT}")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.host == "0.0.0.0":  # noqa: S104
        print("refusing to bind 0.0.0.0 - this host has a public IP with no firewall", file=sys.stderr)
        return 1

    uvicorn.run(
        "databoss_sim.dashboard.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
