"""Phase 17C Tier B world generation endpoint. No process/port interaction -
pure file generation, see world_generation.py's module docstring."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from databoss_sim.dashboard.deps import require_write_token
from databoss_sim.dashboard.world_generation import apply_wind, generate_world, list_worlds

router = APIRouter()


@router.get("/api/worlds")
def get_worlds() -> list[dict]:
    return list_worlds()


class ApplyWindRequest(BaseModel):
    source_world: str
    new_name: str
    mean_mps: float
    direction_vector_enu: list[float]


@router.post("/api/worlds/apply_wind", dependencies=[Depends(require_write_token)])
def create_wind_variant(req: ApplyWindRequest) -> dict:
    try:
        result = apply_wind(req.source_world, req.new_name, req.mean_mps, req.direction_vector_enu)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no such world: {req.source_world}")
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


class GenerateWorldRequest(BaseModel):
    new_name: str
    size_m: dict[str, Any]
    texture: dict[str, Any]
    lighting: dict[str, Any]
    wind: dict[str, Any] = {"enabled": False}
    objects: list[dict[str, Any]] = []
    pad: dict[str, Any] | None = None


@router.post("/api/worlds/generate", dependencies=[Depends(require_write_token)])
def create_world(req: GenerateWorldRequest) -> dict:
    world_fields: dict[str, Any] = {
        "size_m": req.size_m,
        "texture": req.texture,
        "lighting": req.lighting,
        "wind": req.wind,
        "objects": req.objects,
    }
    if req.pad is not None:
        world_fields["pad"] = req.pad

    try:
        result = generate_world(req.new_name, world_fields)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"name": req.new_name, **result}
