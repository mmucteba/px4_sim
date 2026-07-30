"""Vehicle composition and install endpoints."""

from __future__ import annotations

import filecmp
import os
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
    generation_preflight,
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


def _relative_paths(paths: list[Path]) -> list[str]:
    out: list[str] = []
    for path in paths:
        try:
            out.append(str(path.relative_to(PROJECT_ROOT)))
        except ValueError:
            out.append(str(path))
    return out


def _existing_vehicle_output_paths(name: str) -> list[Path]:
    model_dir = DEFAULT_MODELS_DIR / name
    return [path for path in (model_dir / "model.sdf", model_dir / "model.config") if path.exists()]


def _conflict_detail(paths: list[Path]) -> dict[str, Any]:
    display_paths = _relative_paths(paths)
    return {
        "message": "vehicle output already exists; refusing to overwrite: " + ", ".join(display_paths),
        "paths": display_paths,
        "fix": "choose a new vehicle name or remove the existing generated files after confirming they are no longer needed",
    }


def _paths_from_file_exists(exc: FileExistsError) -> list[Path]:
    message = str(exc)
    _, sep, tail = message.partition(": ")
    if not sep:
        return []
    return [Path(part.strip()) for part in tail.split(",") if part.strip()]


def _write_root_for_error_path(path: Path | None) -> Path:
    if path is None:
        return DEFAULT_MODELS_DIR
    path = path.resolve(strict=False)
    for root in (DEFAULT_MODELS_DIR, DEFAULT_AIRFRAMES_DIR):
        root_resolved = root.resolve(strict=False)
        if path == root_resolved or root_resolved in path.parents:
            return root
    return path


def _write_error_detail(exc: OSError) -> dict[str, str]:
    filename = exc.filename or getattr(exc, "filename2", None)
    failed_path = Path(filename) if filename else None
    write_root = _write_root_for_error_path(failed_path)
    attempted = str(failed_path) if failed_path else str(write_root)
    return {
        "message": (
            f"cannot write generated vehicle at {attempted}; {write_root} is not writable by uid {os.geteuid()}. "
            f"Fix ownership with `sudo chown px4:px4 {write_root}`."
        ),
        "path": attempted,
        "write_root": str(write_root),
        "fix": f"sudo chown px4:px4 {write_root}",
    }


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
    if req.write and re.fullmatch(r"^[a-z0-9_]+$", req.name):
        existing = _existing_vehicle_output_paths(req.name)
        if existing:
            raise HTTPException(status_code=409, detail=_conflict_detail(existing))
    try:
        composed = compose_vehicle(spec, databoss_models_dir=DEFAULT_MODELS_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    written_paths: list[str] = []
    if req.write:
        try:
            written = write_vehicle(composed, DEFAULT_MODELS_DIR, DEFAULT_AIRFRAMES_DIR, overwrite=False)
        except FileExistsError as exc:
            paths = _paths_from_file_exists(exc)
            raise HTTPException(status_code=409, detail=_conflict_detail(paths) if paths else str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=500, detail=_write_error_detail(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=_write_error_detail(exc))
        written_paths = _relative_paths(written)

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


@router.get("/api/vehicles/generate/preflight")
def vehicle_generation_preflight() -> list[dict]:
    return [
        result.asdict()
        for result in generation_preflight(
            models_dir=DEFAULT_MODELS_DIR,
            airframes_dir=DEFAULT_AIRFRAMES_DIR,
            project_root=PROJECT_ROOT,
        )
    ]


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
