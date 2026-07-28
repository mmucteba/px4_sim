"""Read-only run listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from databoss_sim.contracts.index_entry import IndexEntry
from databoss_sim.dashboard.config import RUNS_DIR
from databoss_sim.dashboard.deps import get_index
from databoss_sim.dashboard.file_browser import build_file_tree

router = APIRouter()


@router.get("/api/runs", response_model=list[IndexEntry])
def list_runs() -> list[IndexEntry]:
    return get_index().runs


@router.get("/api/runs/{run_id}", response_model=IndexEntry)
def get_run(run_id: str) -> IndexEntry:
    for entry in get_index().runs:
        if entry.run_id == run_id:
            return entry
    raise HTTPException(status_code=404, detail=f"no such run: {run_id}")


@router.get("/api/runs/{run_id}/files")
def get_run_files(run_id: str) -> list[dict]:
    # run_id must match a real indexed run before it ever touches the
    # filesystem - the index is built from actual directory names, so this
    # also rules out path-traversal segments (e.g. "../../etc") reaching
    # RUNS_DIR / run_id below.
    if not any(entry.run_id == run_id for entry in get_index().runs):
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return build_file_tree(RUNS_DIR / run_id, f"runs/{run_id}")
