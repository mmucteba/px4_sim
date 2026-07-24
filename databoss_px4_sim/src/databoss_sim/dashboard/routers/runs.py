"""Read-only run listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from databoss_sim.contracts.index_entry import IndexEntry
from databoss_sim.dashboard.deps import get_index

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
