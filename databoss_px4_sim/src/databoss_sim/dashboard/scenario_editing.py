"""Scenario generation logic for Phase 17C: derive vehicle fields, classify
scenario blocks as editable/read-only, apply whitelisted edits, and produce
the manual run command - no process launching anywhere in this module.

vehicle.model -> px4_airframe -> gazebo_model_name derivation confirmed
2026-07-24 against every real scenario in experiments/configs/mvp/scenarios/
(70+ occurrences, zero exceptions): px4_airframe is vehicle.model with its
`gz_` prefix stripped, and gazebo_model_name is px4_airframe + "_0". No
hardcoded lookup table - this generalizes to any future model automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from databoss_sim.dashboard.config import PROJECT_ROOT

DATABOSS_MODELS_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "models"
SCENARIOS_DIR = PROJECT_ROOT / "experiments" / "configs" / "mvp" / "scenarios"

# Field classification, grounded in the Phase 17 audit of what
# auto_takeoff_land_pxh_truth.py / build_gazebo_world.py actually read
# (see docs/architecture/mvp_backend_contract.md's correction notes and
# the Phase 17B findings). Keys are "block.field" dotted paths; a field
# not listed here that appears in a real scenario is classified "unknown"
# rather than silently assumed safe to edit.
#
# status: "editable" | "readonly" | "derived" | "dead"
#   editable: real, safe for a dashboard-generated scenario to set
#   readonly: real and consumed, but safety/tuning-sensitive - display only
#   derived:  computed from another field, never independently editable
#   dead:     present in some scenarios but never read by the runner
FIELD_CLASSIFICATION: dict[str, dict[str, str]] = {
    "vehicle.model": {"status": "editable", "note": "dynamically enumerated from src/databoss_sim/models/"},
    "vehicle.px4_airframe": {"status": "derived", "note": "derived from vehicle.model (strip gz_ prefix)"},
    "vehicle.gazebo_model_name": {"status": "derived", "note": "derived from px4_airframe + '_0'"},
    "vehicle.start_pose": {"status": "editable", "note": "real spawn pose"},
    "world.size_m": {"status": "editable", "note": "read by build_gazebo_world.py"},
    "world.texture": {"status": "editable", "note": "read by build_gazebo_world.py"},
    "world.lighting": {"status": "editable", "note": "read by build_gazebo_world.py"},
    "world.wind": {"status": "editable", "note": "read by build_gazebo_world.py"},
    "world.objects": {"status": "editable", "note": "box/cylinder only, read by build_gazebo_world.py"},
    "world.terrain": {"status": "dead", "note": "never read by build_gazebo_world.py - label only"},
    "world.frame": {"status": "dead", "note": "never read - project-wide ENU/NED invariant, not per-world"},
    "world.sdf_path": {"status": "derived", "note": "set to the Tier B generated SDF path"},
    "route.altitude_agl_m": {"status": "editable", "note": "the only route.* field the runner reads"},
    "route.type": {"status": "dead", "note": "never read by the runner"},
    "route.waypoints": {"status": "dead", "note": "never read by the runner"},
    "route.duration_s": {"status": "dead", "note": "never read by the runner - duration is emergent"},
    "gnss.loss_enabled": {"status": "editable", "note": "real, resolves gnss_loss_after_takeoff_s"},
    "gnss.loss_after_takeoff_s": {"status": "editable", "note": "real"},
    "gnss.start_enabled": {"status": "dead", "note": "real control is --gnss-start-used CLI flag"},
    "gnss.restore_after_run": {"status": "dead", "note": "never read by the runner"},
    "failsafe.profile": {"status": "editable", "note": "real - default | delayed_observation"},
    "aiding.mode": {"status": "editable", "note": "only synthetic_external_odometry is actually branched on"},
    "aiding.enabled": {"status": "editable", "note": "real"},
    "flow_bridge.enabled": {"status": "editable", "note": "real"},
    "flow_bridge.estimator": {"status": "editable", "note": "dynamically enumerated from flow.ESTIMATORS"},
    "flow_bridge.hfov_rad": {"status": "derived", "note": "pinned to the selected vehicle's real camera SDF FOV"},
    "flow_bridge.rate_hz": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.axis_map": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_ctrl": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_qmin": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_n_min": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_delay": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "control.gnss_loss_after_offboard_s": {"status": "editable", "note": "real minimum-floor trigger"},
    "control.vx_m_s": {"status": "editable", "note": "real"},
    "control.vy_m_s": {"status": "editable", "note": "real"},
    "control.vz_m_s": {"status": "editable", "note": "real"},
    "control.sim_time_wall_multiplier": {"status": "readonly", "note": "wall-clock timeout compensation"},
    "control.warmup_s": {"status": "readonly", "note": "display only"},
    "control.rate_hz": {"status": "readonly", "note": "display only"},
    "extra_px4_params": {"status": "readonly", "note": "discovered-failure-mode fixes, not design knobs"},
}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and path not in (
            "vehicle.start_pose", "world.objects",
        ):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def classify_scenario_fields(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk a parsed scenario dict and classify every field actually
    present, using FIELD_CLASSIFICATION. A present field with no matching
    rule is classified "unknown" rather than silently assumed editable -
    the whitelist in apply_scenario_edits() is the real safety boundary,
    this is just the display-facing explanation of it."""
    flat = _flatten(scenario)
    result = []
    for path, value in flat.items():
        rule = FIELD_CLASSIFICATION.get(path)
        result.append({
            "path": path,
            "value": value,
            "status": rule["status"] if rule else "unknown",
            "note": rule["note"] if rule else "no classification rule - treat as read-only",
        })
    return result


def load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIOS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(name)
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def apply_scenario_edits(source: dict[str, Any], edits: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply only whitelisted (status == "editable") dotted-path edits to a
    deep copy of `source`. Returns (new_scenario, rejected_paths) - a
    rejected edit is silently NOT applied, never partially applied, and the
    caller decides whether to surface the rejection as an error."""
    import copy

    new_scenario = copy.deepcopy(source)
    rejected: list[str] = []

    for path, value in edits.items():
        rule = FIELD_CLASSIFICATION.get(path)
        if rule is None or rule["status"] != "editable":
            rejected.append(path)
            continue
        _set_path(new_scenario, path, value)

    # Derived fields always recomputed from the (possibly just-edited)
    # vehicle.model, never taken from user input directly.
    vehicle_model = _get_path(new_scenario, "vehicle.model")
    if vehicle_model:
        airframe = derive_px4_airframe(vehicle_model)
        _set_path(new_scenario, "vehicle.px4_airframe", airframe)
        _set_path(new_scenario, "vehicle.gazebo_model_name", derive_gazebo_model_name(airframe))

    return new_scenario, rejected


def compute_confound_diff(source: dict[str, Any], new_scenario: dict[str, Any]) -> dict[str, list[str]]:
    """Group changed field paths by top-level block, for the
    one-variable-at-a-time confound banner. Returns {block: [changed paths]}
    - more than one block present means the caller should show a
    non-blocking confound warning."""
    source_flat = _flatten(source)
    new_flat = _flatten(new_scenario)
    changed: dict[str, list[str]] = {}

    all_paths = set(source_flat) | set(new_flat)
    for path in sorted(all_paths):
        if source_flat.get(path) != new_flat.get(path):
            block = path.split(".")[0]
            changed.setdefault(block, []).append(path)

    return changed


def build_run_command(scenario_relpath: str, gnss_start_used: int | None = None) -> str:
    """The exact manual invocation to copy-paste and run - the dashboard's
    actual deliverable for Phase 17C. Matches the real wrapper script's CLI
    (scripts/runner/run_scenario_pxh_end_to_end.py) exactly: a positional
    scenario path, plus --gnss-start-used only when explicitly set (the
    real control for GNSS-start state, since gnss.start_enabled in the
    scenario YAML itself is dead - see FIELD_CLASSIFICATION).
    """
    parts = [
        "venv/bin/python", "scripts/runner/run_scenario_pxh_end_to_end.py",
        scenario_relpath,
    ]
    if gnss_start_used is not None:
        parts.append(f"--gnss-start-used {gnss_start_used}")
    return " ".join(parts)


def write_new_scenario(new_name: str, scenario: dict[str, Any]) -> Path:
    """Writes scenario to a NEW file only - 409-equivalent (FileExistsError)
    if new_name already exists, never overwrites."""
    path = SCENARIOS_DIR / f"{new_name}.yaml"
    if path.exists():
        raise FileExistsError(new_name)
    with path.open("w") as f:
        yaml.safe_dump(scenario, f, sort_keys=False)
    return path


def derive_px4_airframe(vehicle_model: str) -> str:
    return vehicle_model.removeprefix("gz_")


def derive_gazebo_model_name(px4_airframe: str) -> str:
    return f"{px4_airframe}_0"


def find_available_vehicle_models() -> list[str]:
    """Returns vehicle.model-style names (gz_-prefixed) for every model
    directory that actually exists on disk under src/databoss_sim/models/ -
    dynamically enumerated, not hardcoded, matching the same principle
    check_model_sync_and_fov.py already established in Phase 17B."""
    if not DATABOSS_MODELS_DIR.is_dir():
        return []
    return sorted(f"gz_{p.name}" for p in DATABOSS_MODELS_DIR.iterdir() if p.is_dir())
