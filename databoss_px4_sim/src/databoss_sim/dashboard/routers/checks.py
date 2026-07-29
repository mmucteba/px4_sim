"""Phase 17B model-sync/FOV-consistency check, cached and refreshed at
startup - a GET endpoint that shells out on every request is a smell even
when the underlying check is cheap.
"""

from __future__ import annotations

import sys
import threading

from fastapi import APIRouter, Depends, HTTPException

from databoss_sim.dashboard.config import JOB_LOCK_PATH, PROJECT_ROOT, REQUIRE_WRITE_TOKEN, WRITE_TOKEN_PATH
from databoss_sim.dashboard.deps import require_write_token

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.check_model_sync_and_fov import run_all_checks  # noqa: E402

router = APIRouter()

_lock = threading.Lock()
_cached_result: dict | None = None


def _to_jsonable(results: dict[str, list]) -> dict:
    return {key: [r.__dict__ for r in items] for key, items in results.items()}


def refresh_model_sync_cache() -> dict:
    global _cached_result
    with _lock:
        _cached_result = _to_jsonable(run_all_checks())
        return _cached_result


@router.get("/api/checks/model_sync")
def get_model_sync_check() -> dict:
    with _lock:
        if _cached_result is not None:
            return _cached_result
    return refresh_model_sync_cache()


@router.get("/api/auth")
def get_auth_mode() -> dict:
    return {
        "write_token_required": REQUIRE_WRITE_TOKEN,
        "write_token_present": WRITE_TOKEN_PATH.is_file(),
    }


@router.post("/api/checks/model_sync/refresh", dependencies=[Depends(require_write_token)])
def refresh_model_sync_check() -> dict:
    if JOB_LOCK_PATH.exists():
        raise HTTPException(status_code=409, detail="refusing model-sync refresh while a dashboard job lock is held")
    # The check reads model SDFs and PX4 airframe files, which change on an
    # operator event, not a clock; shortening the cache TTL would just re-shell
    # on a timer on a memory-tight host.
    return refresh_model_sync_cache()
