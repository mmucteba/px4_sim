"""Phase 17C: scenario listing, detail+classification, and generation
endpoints. No process launching anywhere in this router - see
scenario_editing.py's module docstring.
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from databoss_sim.dashboard.config import PROJECT_ROOT
from databoss_sim.dashboard.deps import require_write_token
from databoss_sim.dashboard.scenario_editing import (
    SCENARIOS_DIR,
    apply_scenario_edits,
    apply_world_selection,
    build_run_command,
    classify_scenario_fields,
    compute_confound_diff,
    default_scenario_template,
    find_available_vehicle_models,
    load_scenario,
    write_new_scenario,
)
from databoss_sim.dashboard.world_generation import resolve_world_selection

router = APIRouter()


class CreateScenarioRequest(BaseModel):
    # Optional (2026-07-24): omit to build from default_scenario_template()
    # instead of cloning an existing file - "cover all the yaml options so
    # we don't need a source yaml".
    source_scenario: str | None = None
    new_name: str
    edits: dict[str, Any] = {}
    # Not a scenario YAML field (gnss.start_enabled is dead - see
    # FIELD_CLASSIFICATION) - the real control is the runner's
    # --gnss-start-used CLI flag, so it's request-level, not an "edit".
    gnss_start_used: int | None = None
    # world.* fields are a derived bundle (see apply_world_selection) - not
    # part of `edits`. Omit to keep the source/template's existing world.
    world_name: str | None = None


@router.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    if not SCENARIOS_DIR.is_dir():
        return []
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            with path.open() as f:
                data = yaml.safe_load(f)
        except Exception as e:
            out.append({"name": path.stem, "error": str(e)})
            continue
        run = (data or {}).get("run", {})
        vehicle = (data or {}).get("vehicle", {})
        out.append({
            "name": path.stem,
            "run_name": run.get("name"),
            "description": run.get("description"),
            "vehicle_model": vehicle.get("model"),
        })
    return out


@router.get("/api/vehicle_models")
def list_vehicle_models() -> list[str]:
    return find_available_vehicle_models()


@router.get("/api/scenarios/{name}")
def get_scenario(name: str) -> dict:
    try:
        data = load_scenario(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no such scenario: {name}")
    return {
        "name": name,
        "content": data,
        "fields": classify_scenario_fields(data),
    }


@router.post("/api/scenarios", dependencies=[Depends(require_write_token)])
def create_scenario(req: CreateScenarioRequest) -> dict:
    if req.source_scenario is None:
        source = default_scenario_template()
    else:
        try:
            source = load_scenario(req.source_scenario)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"no such source scenario: {req.source_scenario}")

    new_scenario, rejected = apply_scenario_edits(source, req.edits, req.new_name)

    if req.world_name is not None:
        try:
            world_block = resolve_world_selection(req.world_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"no such generated world: {req.world_name}")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        apply_world_selection(new_scenario, world_block)

    confound = compute_confound_diff(source, new_scenario)

    try:
        path = write_new_scenario(req.new_name, new_scenario)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"scenario already exists: {req.new_name}")

    scenario_relpath = str(path.relative_to(PROJECT_ROOT))
    return {
        "name": req.new_name,
        "path": scenario_relpath,
        "content": new_scenario,
        "rejected_edits": rejected,
        "confound": {
            "blocks_changed": sorted(confound.keys()),
            "is_confounded": len(confound) > 1,
            "detail": confound,
        },
        "run_command": build_run_command(scenario_relpath, req.gnss_start_used),
    }
