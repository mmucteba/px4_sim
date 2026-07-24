"""Read-only comparison listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from databoss_sim.contracts.index_entry import ComparisonIndexEntry
from databoss_sim.dashboard.config import COMPARISONS_DIR
from databoss_sim.dashboard.deps import get_index
from databoss_sim.dashboard.file_browser import build_file_tree

router = APIRouter()


@router.get("/api/comparisons", response_model=list[ComparisonIndexEntry])
def list_comparisons() -> list[ComparisonIndexEntry]:
    return get_index().comparisons


@router.get("/api/comparisons/{comparison_id}", response_model=ComparisonIndexEntry)
def get_comparison(comparison_id: str) -> ComparisonIndexEntry:
    for entry in get_index().comparisons:
        if entry.comparison_id == comparison_id:
            return entry
    raise HTTPException(status_code=404, detail=f"no such comparison: {comparison_id}")


@router.get("/api/comparisons/{comparison_id}/files")
def get_comparison_files(comparison_id: str) -> list[dict]:
    if not any(entry.comparison_id == comparison_id for entry in get_index().comparisons):
        raise HTTPException(status_code=404, detail=f"no such comparison: {comparison_id}")
    return build_file_tree(COMPARISONS_DIR / comparison_id, f"comparisons/{comparison_id}")
