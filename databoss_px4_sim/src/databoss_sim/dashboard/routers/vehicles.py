"""Vehicle composition and install endpoints."""

from __future__ import annotations

import filecmp
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from databoss_sim.dashboard.config import PROJECT_ROOT
from databoss_sim.dashboard.deps import require_write_token
from databoss_sim.dashboard.job_registry import BusyError
from databoss_sim.dashboard.scenario_editing import find_available_vehicle_models
from databoss_sim.dashboard.vehicle_generation import compose_vehicle, extract_sensor_block, sdf_value, write_vehicle
from databoss_sim.dashboard.vehicle_install import (
    DEFAULT_AIRFRAMES_DIR,
    DEFAULT_MODELS_DIR,
    DEFAULT_PINS_PATH,
    DEFAULT_PX4_ROOT,
    PX4_MODELS_REL,
    preflight,
    start_vehicle_install_job,
)

router = APIRouter()


class GenerateVehicleRequest(BaseModel):
    name: str
    base: str
    base_airframe: str
    description: str
    sensors: list[dict[str, Any]] = Field(default_factory=list)
    boot_params: dict[str, Any] = Field(default_factory=dict)
    write: bool = False


def _pins_airframes(path: Path | None = None) -> dict[str, str]:
    path = path or DEFAULT_PINS_PATH
    if not path.is_file():
        return {}
    with path.open() as f:
        pins = yaml.safe_load(f) or {}
    out: dict[str, str] = {}
    for airframe in pins.get("airframes") or []:
        if not isinstance(airframe, str):
            continue
        prefix, sep, model = airframe.partition("_gz_")
        if sep and prefix.isdigit() and model:
            out[model] = airframe
    return out


def _pins_models(path: Path | None = None) -> set[str]:
    path = path or DEFAULT_PINS_PATH
    if not path.is_file():
        return set()
    with path.open() as f:
        pins = yaml.safe_load(f) or {}
    return {model for model in pins.get("models") or [] if isinstance(model, str)}


def _camera_hfov_from_sdf(path: Path) -> float | None:
    if not path.is_file():
        return None
    block = extract_sensor_block(path.read_text(), "camera")
    if block is None:
        return None
    raw = sdf_value(block, "horizontal_fov")
    return float(raw) if raw is not None else None


def _camera_hfov(model_dir: Path, px4_models_dir: Path) -> float | None:
    sdf_path = model_dir / "model.sdf"
    direct = _camera_hfov_from_sdf(sdf_path)
    if direct is not None:
        return direct
    if not sdf_path.is_file():
        return None
    text = sdf_path.read_text()
    for submodel in re.findall(r"model://([A-Za-z0-9_]+)", text):
        for root in (DEFAULT_MODELS_DIR, px4_models_dir):
            hfov = _camera_hfov_from_sdf(root / submodel / "model.sdf")
            if hfov is not None:
                return hfov
    return None


def _sensors(model_dir: Path) -> list[str]:
    sdf_path = model_dir / "model.sdf"
    if not sdf_path.is_file():
        return []
    text = sdf_path.read_text()
    sensors = []
    for match in re.finditer(r"<sensor\b[^>]*\btype=['\"]([^'\"]+)['\"][^>]*\bname=['\"]([^'\"]+)['\"]", text):
        sensors.append(f"{match.group(1)}:{match.group(2)}")
    for match in re.finditer(r"<sensor\b[^>]*\bname=['\"]([^'\"]+)['\"][^>]*\btype=['\"]([^'\"]+)['\"]", text):
        sensors.append(f"{match.group(2)}:{match.group(1)}")
    for include in re.findall(r"model://([A-Za-z0-9_]+)", text):
        sensors.append(f"include:{include}")
    return sorted(dict.fromkeys(sensors))


def _sync_status(model: str, px4_models_dir: Path, declared_models: set[str]) -> str:
    databoss_dir = DEFAULT_MODELS_DIR / model
    px4_dir = px4_models_dir / model
    if not px4_dir.is_dir():
        return "MISSING_PX4" if model in declared_models else "NOT_INSTALLED"
    for filename in ("model.sdf", "model.config"):
        left = databoss_dir / filename
        right = px4_dir / filename
        if not left.is_file() or not right.is_file() or not filecmp.cmp(left, right, shallow=False):
            return "DRIFTED"
    return "IN_SYNC"


@router.get("/api/vehicles")
def list_vehicles() -> list[dict]:
    if not DEFAULT_MODELS_DIR.is_dir():
        return []
    registered_vehicle_models = {value.removeprefix("gz_") for value in find_available_vehicle_models()}
    airframes = _pins_airframes()
    declared_models = _pins_models()
    px4_models_dir = DEFAULT_PX4_ROOT / PX4_MODELS_REL
    out: list[dict] = []
    for model_dir in sorted(path for path in DEFAULT_MODELS_DIR.iterdir() if path.is_dir()):
        name = model_dir.name
        airframe = airframes.get(name)
        repo_airframes = sorted((PROJECT_ROOT / "src" / "databoss_sim" / "airframes").glob(f"*_gz_{name}"))
        is_vehicle = name in registered_vehicle_models
        has_repo_airframe = len(repo_airframes) == 1
        display_airframe = airframe or (repo_airframes[0].name if has_repo_airframe else None)
        autostart_id = int(display_airframe.split("_", 1)[0]) if display_airframe else None
        sync = _sync_status(name, px4_models_dir, declared_models)
        needs_install = sync == "NOT_INSTALLED" and has_repo_airframe
        out.append({
            "name": name,
            "is_vehicle": is_vehicle,
            "airframe_filename": airframe,
            "repo_airframe_filename": repo_airframes[0].name if has_repo_airframe else None,
            "autostart_id": autostart_id,
            "camera_hfov_rad": _camera_hfov(model_dir, px4_models_dir),
            "sensors": _sensors(model_dir),
            "install_state": "installed" if sync == "IN_SYNC" else "needs_install" if sync == "NOT_INSTALLED" else "missing_px4" if sync == "MISSING_PX4" else "drifted",
            "install_action": "Install" if needs_install else None,
            "verify_state": "verified" if sync == "IN_SYNC" else "needs_install" if sync == "NOT_INSTALLED" else "not_flyable" if not is_vehicle else sync.lower(),
            "model_sync_status": sync,
            "declared_in_pins": name in declared_models,
            "has_repo_airframe": has_repo_airframe,
            "needs_install": needs_install,
        })
    return out


@router.post("/api/vehicles/generate", dependencies=[Depends(require_write_token)])
def generate_vehicle(req: GenerateVehicleRequest) -> dict:
    spec = req.model_dump(exclude={"write"})
    try:
        composed = compose_vehicle(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    written_paths: list[str] = []
    if req.write:
        try:
            written = write_vehicle(composed, DEFAULT_MODELS_DIR, DEFAULT_AIRFRAMES_DIR, overwrite=False)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        written_paths = [str(path.relative_to(PROJECT_ROOT)) for path in written]

    return {
        "name": req.name,
        "model_sdf": composed.model_sdf,
        "model_config": composed.model_config,
        "airframe": composed.airframe,
        "airframe_filename": composed.airframe_filename,
        "autostart_id": composed.autostart_id,
        "camera_hfov_rad": composed.camera_hfov_rad,
        "warnings": composed.warnings,
        "written": req.write,
        "written_paths": written_paths,
    }


@router.post(
    "/api/vehicles/{name}/install",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_write_token)],
)
def install_vehicle(name: str) -> dict:
    try:
        record = start_vehicle_install_job(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no generated vehicle to install: {name}")
    except BusyError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "another job is already active", "active_job_id": exc.active_job_id},
        )
    except FileExistsError:
        raise HTTPException(status_code=409, detail="job id collision; retry install")
    return {
        "job_id": record.job_id,
        "kind": record.kind,
        "status": record.status,
        "vehicle": name,
        "command": record.command,
        "log_url": f"/api/jobs/{record.job_id}/log",
        "job_url": f"/api/jobs/{record.job_id}",
    }


@router.get("/api/vehicles/{name}/preflight")
def vehicle_preflight(name: str) -> list[dict]:
    return [
        result.asdict()
        for result in preflight(
            name,
            px4_root=DEFAULT_PX4_ROOT,
            pins_path=DEFAULT_PINS_PATH,
            project_root=PROJECT_ROOT,
        )
    ]
