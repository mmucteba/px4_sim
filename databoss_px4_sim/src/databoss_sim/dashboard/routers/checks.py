"""Phase 17B model-sync/FOV-consistency check, cached and refreshed at
startup - a GET endpoint that shells out on every request is a smell even
when the underlying check is cheap.
"""

from __future__ import annotations

import sys
import threading

from fastapi import APIRouter

from databoss_sim.dashboard.config import PROJECT_ROOT

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
