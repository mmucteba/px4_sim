"""Dashboard job launch/monitor/cancel endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from databoss_sim.dashboard.config import JOB_LOCK_PATH, JOB_LOG_CHUNK_BYTES
from databoss_sim.dashboard.deps import require_write_token
from databoss_sim.dashboard.job_registry import (
    BusyError,
    active_job_id_from_lock,
    find_run_dir_for_job,
    job_dir,
    list_jobs,
    probe_viz_ports,
    read_job,
    reconcile,
    tail_file,
)
from databoss_sim.dashboard.launch import LaunchError, LaunchRequest, cancel_job, cleanup_stale_hosts, start_job

router = APIRouter()


def _record_dict(record) -> dict:
    return record.model_dump(mode="json")


def _read_reconciled(job_id: str):
    try:
        return reconcile(read_job(job_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"job registry corrupt for {job_id}: {exc}")


@router.post("/api/launch", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_write_token)])
def launch(req: LaunchRequest) -> dict:
    try:
        record = start_job(req)
    except BusyError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "another job is already active", "active_job_id": exc.active_job_id},
        )
    except LaunchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="job id collision; retry launch")

    return {
        "job_id": record.job_id,
        "kind": record.kind,
        "status": record.status,
        "scenario": record.scenario,
        "command": record.command,
        "log_url": f"/api/jobs/{record.job_id}/log",
        "job_url": f"/api/jobs/{record.job_id}",
    }


@router.get("/api/jobs")
def jobs_index() -> dict:
    records = []
    for record in list_jobs():
        records.append(reconcile(record))

    active = None
    active_id = active_job_id_from_lock()
    if active_id is not None:
        try:
            active_record = reconcile(read_job(active_id))
            if active_record.status not in {"succeeded", "failed", "cancelled", "crashed"}:
                active = _record_dict(active_record)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            active = {"job_id": active_id, "status": "unknown"}

    return {"active": active, "jobs": [_record_dict(record) for record in records]}


@router.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    record = _read_reconciled(job_id)
    found = find_run_dir_for_job(record)
    if found is not None and record.run_dir != str(found):
        record.run_dir = str(found)
    data = _record_dict(record)
    data["run_dir"] = str(found) if found is not None else record.run_dir
    data["viz"] = probe_viz_ports()
    return data


@router.get("/api/jobs/{job_id}/log")
def job_log(job_id: str, offset: int = Query(default=0, ge=0)) -> dict:
    _read_reconciled(job_id)
    return tail_file(job_dir(job_id) / "console.log", offset, JOB_LOG_CHUNK_BYTES)


@router.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_write_token)])
def cancel(job_id: str) -> dict:
    try:
        return _record_dict(cancel_job(job_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no such job: {job_id}")


@router.post("/api/jobs/cleanup", dependencies=[Depends(require_write_token)])
def cleanup() -> dict:
    if JOB_LOCK_PATH.exists():
        raise HTTPException(status_code=409, detail="refusing cleanup while a dashboard job lock is held")
    return cleanup_stale_hosts()

