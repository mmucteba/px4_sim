"""Phase 17B model-sync/FOV-consistency check, cached and refreshed at
startup - a GET endpoint that shells out on every request is a smell even
when the underlying check is cheap.
"""

from __future__ import annotations

import shutil
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException

from databoss_sim.dashboard.config import (
    JOB_LOCK_PATH,
    MIN_FREE_DISK_GB,
    MIN_AVAILABLE_MEM_MB,
    PROJECT_ROOT,
    QGC_IP,
    REQUIRE_WRITE_TOKEN,
    WARN_FREE_DISK_GB,
    WRITE_TOKEN_PATH,
)
from databoss_sim.dashboard.deps import require_write_token
from databoss_sim.dashboard.launch import _mem_available_mb

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.check_model_sync_and_fov import run_all_checks as run_model_sync_checks  # noqa: E402
from scripts.deploy.check_deployment import STATUS_ORDER, run_all_checks as run_deployment_checks  # noqa: E402

router = APIRouter()

_model_sync_lock = threading.Lock()
_cached_model_sync_result: dict | None = None
_deployment_lock = threading.Lock()
_cached_deployment_result: dict | None = None


def _to_jsonable(results: dict[str, list]) -> dict:
    return {key: [r.__dict__ for r in items] for key, items in results.items()}


def refresh_model_sync_cache() -> dict:
    global _cached_model_sync_result
    with _model_sync_lock:
        _cached_model_sync_result = _to_jsonable(run_model_sync_checks())
        return _cached_model_sync_result


@router.get("/api/checks/model_sync")
def get_model_sync_check() -> dict:
    with _model_sync_lock:
        if _cached_model_sync_result is not None:
            return _cached_model_sync_result
    return refresh_model_sync_cache()


@router.get("/api/host")
def get_host() -> dict:
    try:
        mem_available_mb = _mem_available_mb()
    except Exception:
        mem_available_mb = None
    mem_ok = None if mem_available_mb is None else mem_available_mb >= MIN_AVAILABLE_MEM_MB

    try:
        disk = shutil.disk_usage(PROJECT_ROOT)
        disk_free_gb = round(disk.free / (1024 ** 3), 2)
        disk_total_gb = round(disk.total / (1024 ** 3), 2)
    except Exception:
        disk_free_gb = None
        disk_total_gb = None
    disk_ok = disk_free_gb is not None and disk_free_gb >= MIN_FREE_DISK_GB
    disk_warn = disk_free_gb is not None and disk_free_gb < WARN_FREE_DISK_GB

    return {
        "mem_available_mb": mem_available_mb,
        "mem_guard_mb": MIN_AVAILABLE_MEM_MB,
        "mem_ok": mem_ok,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "disk_ok": disk_ok,
        "disk_warn": disk_warn,
        "disk_block_gb": MIN_FREE_DISK_GB,
        "disk_warn_gb": WARN_FREE_DISK_GB,
        "job_lock_held": JOB_LOCK_PATH.exists(),
        "qgc_ip_default": QGC_IP,
    }


def _deployment_summary(groups: dict[str, list[dict]]) -> dict:
    counts = {status.lower(): 0 for status in STATUS_ORDER}
    for items in groups.values():
        for item in items:
            status = str(item.get("status", "")).lower()
            counts[status] = counts.get(status, 0) + 1
    return counts


def _deployment_blocking(groups: dict[str, list[dict]]) -> list[dict]:
    return [
        {"group": group, "name": item.get("name"), "detail": item.get("detail")}
        for group, items in groups.items()
        for item in items
        if item.get("status") == "FAIL"
    ]


def refresh_deployment_cache() -> dict:
    global _cached_deployment_result
    with _deployment_lock:
        groups = _to_jsonable(run_deployment_checks())
        _cached_deployment_result = {
            "groups": groups,
            "summary": _deployment_summary(groups),
            "blocking": _deployment_blocking(groups),
        }
        return _cached_deployment_result


@router.get("/api/checks/deployment")
def get_deployment_check() -> dict:
    with _deployment_lock:
        if _cached_deployment_result is not None:
            return _cached_deployment_result
    return refresh_deployment_cache()


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


@router.post("/api/checks/deployment/refresh", dependencies=[Depends(require_write_token)])
def refresh_deployment_check() -> dict:
    if JOB_LOCK_PATH.exists():
        raise HTTPException(status_code=409, detail="refusing deployment refresh while a dashboard job lock is held")
    return refresh_deployment_cache()
