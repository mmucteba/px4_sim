"""Shared FastAPI dependencies: the cached live index and the write-token
check used by every mutating (POST) endpoint added from Phase 17C onward.
Built now, in 17A, even though no write endpoint exists yet - so later
phases don't have to retrofit auth onto already-shipped endpoints.
"""

from __future__ import annotations

import hmac
import sys
import threading
import time

from fastapi import Header, HTTPException

from databoss_sim.dashboard.config import INDEX_CACHE_TTL_S, PROJECT_ROOT, REQUIRE_WRITE_TOKEN, WRITE_TOKEN_PATH

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.build_experiments_index import build_index  # noqa: E402

from databoss_sim.contracts.index_entry import ExperimentsIndex  # noqa: E402

_cache_lock = threading.Lock()
_cached_index: ExperimentsIndex | None = None
_cached_at: float = 0.0


def get_index(force_refresh: bool = False) -> ExperimentsIndex:
    """Live in-process rescan with a short TTL cache - never reads the
    on-disk experiments/index.json at request time (that file stays a
    stable artifact for other consumers, independent of the dashboard).
    """
    global _cached_index, _cached_at

    with _cache_lock:
        now = time.monotonic()
        if not force_refresh and _cached_index is not None and (now - _cached_at) < INDEX_CACHE_TTL_S:
            return _cached_index

        _cached_index = build_index()
        _cached_at = now
        return _cached_index


def _read_expected_token() -> str:
    if not WRITE_TOKEN_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                f"no write token configured - run "
                f"scripts/dashboard/generate_token.py to create "
                f"{WRITE_TOKEN_PATH.name}"
            ),
        )
    return WRITE_TOKEN_PATH.read_text().strip()


def require_write_token(x_databoss_token: str | None = Header(default=None)) -> None:
    """FastAPI dependency for every mutating endpoint (POST /api/launch,
    /api/scenarios, /api/worlds/generate, /api/jobs/{id}/cancel - Phase
    17C/17D). The write-token check is opt-in via
    DATABOSS_DASHBOARD_REQUIRE_TOKEN=1; when enabled, read-only GET endpoints
    never depend on this - the dashboard is Tailscale-reachable and reads stay
    open, per the Phase 17 plan's explicit auth decision (bind address is the
    read-side boundary; this token is the write-side boundary).
    """
    if not REQUIRE_WRITE_TOKEN:
        return

    expected = _read_expected_token()
    if not x_databoss_token or not hmac.compare_digest(x_databoss_token, expected):
        raise HTTPException(status_code=401, detail="missing or invalid X-Databoss-Token")
