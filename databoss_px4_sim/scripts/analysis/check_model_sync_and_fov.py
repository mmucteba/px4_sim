#!/usr/bin/env python3
"""Check that DATABOSS vehicle model SDFs match their deployed PX4-tree
copies, and that each flow-enabled scenario's declared camera FOV matches
the real camera SDF FOV of the vehicle it targets.

    venv/bin/python scripts/analysis/check_model_sync_and_fov.py
    venv/bin/python scripts/analysis/check_model_sync_and_fov.py --json

Two independent checks (Phase 17B):

- Sync: src/databoss_sim/models/<name>/ vs PX4-Autopilot's
  Tools/simulation/gz/models/<name>/ - these are hand-mirrored with no
  symlink/build step, so drift is only ever caught by actually reading
  both copies. Models are discovered dynamically from whatever exists on
  disk under --databoss-models-dir, never a hardcoded list - a model can
  exist in one checkout's working tree and not another's (e.g. as
  uncommitted work), and a hardcoded list would silently skip it.

  Phase 20 adds one important distinction: a model declared in
  deploy/px4/px4_pins.yaml but absent from PX4 is MISSING_PX4 and fails,
  because the deploy manifest says it should be installed. A model present
  on DATABOSS disk but not declared in pins and absent from PX4 is
  NOT_INSTALLED and does not fail; that is the normal generated-but-not-yet
  deployed state from scripts/sim/add_vehicle.py or the dashboard composer.
  If both copies exist and differ, the result is still DRIFTED and fails,
  regardless of whether the model is declared in pins.

- FOV: for every scenario with flow_bridge.enabled, resolve vehicle.model
  to the real camera SDF's <horizontal_fov> (via sdf_inspect, shared with
  build_unified_comparison_report.py) and compare against the scenario's
  declared flow_bridge.hfov_rad, since nothing else in the codebase keeps
  these in sync.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from sdf_inspect import extract_sensor_block, sdf_value  # noqa: E402

DEFAULT_DATABOSS_MODELS_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "models"
PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))
DEFAULT_PX4_MODELS_DIR = PX4_ROOT / "Tools/simulation/gz/models"
DEFAULT_SCENARIOS_DIR = PROJECT_ROOT / "experiments" / "configs" / "mvp" / "scenarios"
DEFAULT_PINS_PATH = Path(os.environ.get("DATABOSS_PX4_PINS_PATH", PROJECT_ROOT / "deploy" / "px4" / "px4_pins.yaml"))

SYNC_FILES = ["model.sdf", "model.config"]
FOV_TOLERANCE_RAD = 0.01


@dataclass
class ModelSyncResult:
    model: str
    status: str  # IN_SYNC | DRIFTED | MISSING_DATABOSS | MISSING_PX4 | NOT_INSTALLED
    diffs: dict[str, str] = field(default_factory=dict)
    declared_in_pins: bool = False
    detail: str = ""


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unified_diff(a: Path, b: Path) -> str:
    import difflib

    a_lines = a.read_text().splitlines(keepends=True) if a.is_file() else []
    b_lines = b.read_text().splitlines(keepends=True) if b.is_file() else []
    diff = difflib.unified_diff(a_lines, b_lines, fromfile=str(a), tofile=str(b))
    return "".join(diff)


def find_databoss_models(models_dir: Path) -> list[str]:
    if not models_dir.is_dir():
        return []
    return sorted(p.name for p in models_dir.iterdir() if p.is_dir())


def find_pinned_models(pins_path: Path = DEFAULT_PINS_PATH) -> list[str]:
    if not pins_path.is_file():
        return []
    with pins_path.open() as f:
        data = yaml.safe_load(f) or {}
    models = data.get("models") or []
    return sorted(str(model) for model in models if isinstance(model, str))


def check_model_sync(
    databoss_models_dir: Path = DEFAULT_DATABOSS_MODELS_DIR,
    px4_models_dir: Path = DEFAULT_PX4_MODELS_DIR,
    pins_path: Path = DEFAULT_PINS_PATH,
) -> list[ModelSyncResult]:
    results: list[ModelSyncResult] = []
    databoss_models = set(find_databoss_models(databoss_models_dir))
    pinned_models = set(find_pinned_models(pins_path))
    for model in sorted(databoss_models | pinned_models):
        databoss_dir = databoss_models_dir / model
        px4_dir = px4_models_dir / model
        declared = model in pinned_models

        if not px4_dir.is_dir():
            if declared:
                results.append(
                    ModelSyncResult(
                        model=model,
                        status="MISSING_PX4",
                        declared_in_pins=True,
                        detail=f"{model} is declared in {pins_path} but missing from {px4_dir}",
                    )
                )
            else:
                results.append(
                    ModelSyncResult(
                        model=model,
                        status="NOT_INSTALLED",
                        declared_in_pins=False,
                        detail=(
                            f"{model} exists in {databoss_models_dir} but is not declared in pins or installed in PX4; "
                            "deploy it with scripts/sim/add_vehicle.py or the dashboard install action"
                        ),
                    )
                )
            continue

        if not databoss_dir.is_dir():
            results.append(
                ModelSyncResult(
                    model=model,
                    status="MISSING_DATABOSS",
                    declared_in_pins=declared,
                    detail=f"{px4_dir} exists but {databoss_dir} is missing",
                )
            )
            continue

        diffs: dict[str, str] = {}
        for filename in SYNC_FILES:
            a = databoss_dir / filename
            b = px4_dir / filename
            if _sha256(a) != _sha256(b):
                diffs[filename] = _unified_diff(a, b)

        status = "DRIFTED" if diffs else "IN_SYNC"
        detail = (
            f"{databoss_dir} and {px4_dir} differ"
            if diffs else
            f"{databoss_dir} and {px4_dir} match"
        )
        results.append(ModelSyncResult(model=model, status=status, diffs=diffs, declared_in_pins=declared, detail=detail))

    return results


@dataclass
class FovCheckResult:
    scenario: str
    vehicle_model: str
    camera_submodel: str | None
    declared_hfov_rad: float | None
    actual_hfov_rad: float | None
    status: str  # MATCH | MISMATCH | UNRESOLVED


def resolve_model_dir_name(vehicle_model: str) -> str:
    """Scenario YAML `vehicle.model` values are prefixed `gz_`; the actual
    model directory (both in src/databoss_sim/models/ and the PX4 tree)
    drops that prefix - e.g. gz_x500_cam_lidar_down -> x500_cam_lidar_down."""
    return vehicle_model.removeprefix("gz_")


def find_camera_submodel(databoss_model_sdf: Path, px4_models_dir: Path) -> str | None:
    """A vehicle model.sdf <include>s one or more model://X submodels; return
    the first X whose own model.sdf (in the PX4 tree, since camera submodels
    like mono_cam/optical_flow are PX4-stock, not DATABOSS-authored) actually
    contains a camera sensor block."""
    if not databoss_model_sdf.is_file():
        return None
    text = databoss_model_sdf.read_text()
    for submodel in re.findall(r"model://([A-Za-z0-9_]+)", text):
        submodel_sdf = px4_models_dir / submodel / "model.sdf"
        if not submodel_sdf.is_file():
            continue
        if extract_sensor_block(submodel_sdf.read_text(), "camera"):
            return submodel
    return None


def get_camera_hfov(px4_models_dir: Path, camera_submodel: str) -> float | None:
    submodel_sdf = px4_models_dir / camera_submodel / "model.sdf"
    if not submodel_sdf.is_file():
        return None
    block = extract_sensor_block(submodel_sdf.read_text(), "camera")
    if not block:
        return None
    raw = sdf_value(block, "horizontal_fov")
    return float(raw) if raw is not None else None


def check_fov_consistency(
    scenarios_dir: Path = DEFAULT_SCENARIOS_DIR,
    databoss_models_dir: Path = DEFAULT_DATABOSS_MODELS_DIR,
    px4_models_dir: Path = DEFAULT_PX4_MODELS_DIR,
) -> list[FovCheckResult]:
    results: list[FovCheckResult] = []
    if not scenarios_dir.is_dir():
        return results

    for path in sorted(scenarios_dir.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue
        flow_bridge = data.get("flow_bridge") or {}
        if not flow_bridge.get("enabled"):
            continue

        vehicle_model = (data.get("vehicle") or {}).get("model")
        declared = flow_bridge.get("hfov_rad")
        if not vehicle_model:
            results.append(
                FovCheckResult(
                    scenario=path.name, vehicle_model="", camera_submodel=None,
                    declared_hfov_rad=declared, actual_hfov_rad=None, status="UNRESOLVED",
                )
            )
            continue

        model_dir_name = resolve_model_dir_name(vehicle_model)
        databoss_model_sdf = databoss_models_dir / model_dir_name / "model.sdf"
        camera_submodel = find_camera_submodel(databoss_model_sdf, px4_models_dir)

        if camera_submodel is None:
            results.append(
                FovCheckResult(
                    scenario=path.name, vehicle_model=vehicle_model, camera_submodel=None,
                    declared_hfov_rad=declared, actual_hfov_rad=None, status="UNRESOLVED",
                )
            )
            continue

        actual = get_camera_hfov(px4_models_dir, camera_submodel)
        if actual is None or declared is None:
            status = "UNRESOLVED"
        elif abs(float(declared) - actual) <= FOV_TOLERANCE_RAD:
            status = "MATCH"
        else:
            status = "MISMATCH"

        results.append(
            FovCheckResult(
                scenario=path.name, vehicle_model=vehicle_model, camera_submodel=camera_submodel,
                declared_hfov_rad=declared, actual_hfov_rad=actual, status=status,
            )
        )

    return results


def run_all_checks(
    databoss_models_dir: Path = DEFAULT_DATABOSS_MODELS_DIR,
    px4_models_dir: Path = DEFAULT_PX4_MODELS_DIR,
    scenarios_dir: Path = DEFAULT_SCENARIOS_DIR,
    pins_path: Path = DEFAULT_PINS_PATH,
) -> dict[str, list]:
    return {
        "model_sync": check_model_sync(databoss_models_dir, px4_models_dir, pins_path),
        "fov_consistency": check_fov_consistency(scenarios_dir, databoss_models_dir, px4_models_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--databoss-models-dir", type=Path, default=DEFAULT_DATABOSS_MODELS_DIR)
    parser.add_argument("--px4-models-dir", type=Path, default=DEFAULT_PX4_MODELS_DIR)
    parser.add_argument("--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS_DIR)
    parser.add_argument("--pins-path", type=Path, default=DEFAULT_PINS_PATH)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args()

    sync_results = check_model_sync(args.databoss_models_dir, args.px4_models_dir, args.pins_path)
    fov_results = check_fov_consistency(args.scenarios_dir, args.databoss_models_dir, args.px4_models_dir)

    if args.json:
        print(json.dumps({
            "model_sync": [r.__dict__ for r in sync_results],
            "fov_consistency": [r.__dict__ for r in fov_results],
        }, indent=2))
    else:
        print("# Model sync check\n")
        print("| model | status | detail |")
        print("| --- | --- | --- |")
        for r in sync_results:
            print(f"| {r.model} | {r.status} | {r.detail} |")
        for r in sync_results:
            if r.diffs:
                print(f"\n## {r.model} diff\n")
                for filename, diff in r.diffs.items():
                    print(f"### {filename}\n```diff\n{diff}\n```")

        print("\n# FOV consistency check\n")
        print("| scenario | vehicle_model | camera_submodel | declared_hfov_rad | actual_hfov_rad | status |")
        print("| --- | --- | --- | --- | --- | --- |")
        for r in fov_results:
            print(
                f"| {r.scenario} | {r.vehicle_model} | {r.camera_submodel} | "
                f"{r.declared_hfov_rad} | {r.actual_hfov_rad} | {r.status} |"
            )

    any_drift = any(r.status in {"DRIFTED", "MISSING_DATABOSS", "MISSING_PX4"} for r in sync_results)
    any_mismatch = any(r.status == "MISMATCH" for r in fov_results)
    return 1 if (any_drift or any_mismatch) else 0


if __name__ == "__main__":
    raise SystemExit(main())
