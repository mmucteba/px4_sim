"""Dashboard configuration - one place for the paths/constants every
dashboard module needs, so nothing hardcodes PROJECT_ROOT separately.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_ROOT = PROJECT_ROOT / "experiments"
RUNS_DIR = EXPERIMENTS_ROOT / "runs"
COMPARISONS_DIR = EXPERIMENTS_ROOT / "comparisons"
JOBS_DIR = EXPERIMENTS_ROOT / "jobs"
JOB_LOCK_PATH = JOBS_DIR / ".active.lock"
RUN_AS_USER = "px4"
MIN_AVAILABLE_MEM_MB = 1200
CANCEL_GRACE_S = 180.0
JOB_LOG_CHUNK_BYTES = 65536
JOB_REAPER_INTERVAL_S = 5.0

# Live in-process rescan cache TTL (Phase 17A plan: "not reading the static
# index.json file at request time" - short enough to reflect an in-progress
# run, long enough that a page with several API calls doesn't rescan per call.
INDEX_CACHE_TTL_S = 8.0

# Corrected 2026-07-24: this host's actual Tailscale interface address (was
# previously confused with the separate QGC/Mac client's address). Never
# bind to 0.0.0.0 - this host has a public IP with no firewall.
DASHBOARD_HOST = "100.78.93.35"
DASHBOARD_PORT = 8600

WRITE_TOKEN_PATH = PROJECT_ROOT / ".dashboard_token"

# The dashboard binds to a Tailscale-only address, which is the real access
# boundary; the write token was a second boundary behind it. Default off so a
# local single-operator setup needs no token. Set
# DATABOSS_DASHBOARD_REQUIRE_TOKEN=1 to re-enable it unchanged (e.g. if the
# bind address is ever widened).
REQUIRE_WRITE_TOKEN = os.environ.get("DATABOSS_DASHBOARD_REQUIRE_TOKEN", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
