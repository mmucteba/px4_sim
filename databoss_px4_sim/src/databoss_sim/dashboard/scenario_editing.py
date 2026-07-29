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
PX4_PINS_PATH = PROJECT_ROOT / "deploy" / "px4" / "px4_pins.yaml"
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
    "run.name": {"status": "derived", "note": "always set to the new scenario's own name"},
    "run.description": {"status": "editable", "note": "free text, purely descriptive, never read for logic"},
    "vehicle.model": {"status": "editable", "note": "dynamically enumerated from src/databoss_sim/models/"},
    "vehicle.px4_airframe": {"status": "derived", "note": "derived from vehicle.model (strip gz_ prefix)"},
    "vehicle.gazebo_model_name": {"status": "derived", "note": "derived from px4_airframe + '_0'"},
    "vehicle.start_pose": {"status": "editable", "note": "real spawn pose"},
    "world.name": {"status": "derived", "note": "set as a bundle by picking a world (GET /api/worlds)"},
    "world.sdf_path": {"status": "derived", "note": "set as a bundle by picking a world (GET /api/worlds)"},
    "world.lighting": {"status": "derived", "note": "label bundled with the picked world - see world.sdf_path"},
    "world.wind": {"status": "derived", "note": "label bundled with the picked world - see world.sdf_path"},
    "world.texture": {"status": "derived", "note": "label bundled with the picked world - see world.sdf_path"},
    "world.condition_is_physical": {"status": "derived", "note": "bundled with the picked world"},
    "world.size_m": {"status": "editable", "note": "only meaningful when generating a NEW world, not selecting one"},
    "world.objects": {"status": "editable", "note": "box/cylinder only, only for new-world generation"},
    "world.terrain": {"status": "dead", "note": "never read by build_gazebo_world.py - label only"},
    "world.frame": {"status": "dead", "note": "never read - project-wide ENU/NED invariant, not per-world"},
    "route.name": {"status": "derived", "note": "always set to the new scenario's own name"},
    "route.altitude_agl_m": {"status": "editable", "note": "the only route.* field the runner reads"},
    "route.type": {"status": "dead", "note": "never read by the runner"},
    "route.waypoints": {"status": "dead", "note": "never read by the runner"},
    "route.duration_s": {"status": "dead", "note": "never read by the runner - duration is emergent"},
    "gnss.loss_enabled": {"status": "editable", "note": "real, resolves gnss_loss_after_takeoff_s"},
    "gnss.loss_after_takeoff_s": {"status": "editable", "note": "real"},
    "gnss.start_enabled": {"status": "dead", "note": "real control is --gnss-start-used CLI flag"},
    "gnss.restore_after_run": {"status": "dead", "note": "never read by the runner"},
    "failsafe.profile": {"status": "editable", "note": "real - default | delayed_observation"},
    "camera.proof_enabled": {"status": "readonly", "note": "keeps run evidence-backed - see project's hard rule"},
    "camera.description": {"status": "readonly", "note": "display only"},
    "camera.render_engine": {"status": "readonly", "note": "pinned - ogre is the proven-working choice"},
    "camera.xvfb_enabled": {"status": "readonly", "note": "technical/operational, display only"},
    "camera.xvfb_server_args": {"status": "readonly", "note": "technical/operational, display only"},
    "camera.headless_rendering": {"status": "readonly", "note": "technical/operational, display only"},
    "camera.probe_timeout_s": {"status": "readonly", "note": "technical/operational, display only"},
    "rangefinder.proof_enabled": {"status": "readonly", "note": "keeps run evidence-backed"},
    "rangefinder.description": {"status": "readonly", "note": "display only"},
    "rangefinder.render_engine": {"status": "readonly", "note": "pinned - matches camera.render_engine"},
    "rangefinder.xvfb_enabled": {"status": "readonly", "note": "technical/operational, display only"},
    "rangefinder.probe_timeout_s": {"status": "readonly", "note": "technical/operational, display only"},
    "rangefinder.min_ulog_rows": {"status": "readonly", "note": "technical/operational, display only"},
    "rangefinder.height_agreement_tolerance_m": {"status": "readonly", "note": "technical/operational, display only"},
    "visualization.gazebo_web.enabled": {
        "status": "editable",
        "note": "defaults true per standing policy [[keep-qgc-and-gzweb-every-run]] - turning off is a deliberate choice",
    },
    "visualization.gazebo_web.required": {"status": "editable", "note": "whether missing the bridge fails the run"},
    "visualization.gazebo_web.config": {"status": "readonly", "note": "technical/operational, display only"},
    "visualization.gazebo_web.host": {"status": "readonly", "note": "technical/operational, display only"},
    "visualization.gazebo_web.port": {"status": "readonly", "note": "hard port convention - 9003, do not change"},
    "visualization.gazebo_web.publication_hz": {"status": "readonly", "note": "technical/operational, display only"},
    "visualization.gazebo_web.startup_timeout_s": {"status": "readonly", "note": "technical/operational, display only"},
    "aiding.mode": {"status": "editable", "note": "only synthetic_external_odometry is actually branched on"},
    "aiding.enabled": {"status": "editable", "note": "real"},
    "aiding.external_odom_enabled": {"status": "derived", "note": "kept in sync with aiding.mode"},
    "logging.record_ulog": {"status": "readonly", "note": "keeps run evidence-backed - always on in real scenarios"},
    "logging.record_gazebo_truth": {"status": "readonly", "note": "keeps run evidence-backed - the project's hard rule"},
    "logging.record_sensor_logs": {"status": "readonly", "note": "keeps run evidence-backed"},
    "logging.record_camera_topics": {"status": "readonly", "note": "keeps run evidence-backed"},
    "logging.record_external_odometry_sent": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.align_with_gazebo_truth": {"status": "readonly", "note": "the project's hard rule - always on"},
    "analysis.generate_plots": {"status": "readonly", "note": "keeps run evidence-backed"},
    "analysis.generate_summary": {"status": "readonly", "note": "keeps run evidence-backed"},
    "analysis.check_external_odometry_received": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.check_optical_flow_fusion": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.check_flow_velocity_sign": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.flow_velocity_sign_source": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.flow_velocity_sign_required": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.sensor_contract_report": {"status": "readonly", "note": "keeps run evidence-backed"},
    "analysis.sensor_contract_gate": {"status": "readonly", "note": "technical/operational, display only"},
    "analysis.sensor_contract_gate_required": {"status": "readonly", "note": "technical/operational, display only"},
    "flow_recording.enabled": {
        "status": "readonly",
        "note": "fixed to the known-safe default - rate_hz=0 (uncapped) caused a real ~150MB/run disk-cost bug",
    },
    "flow_recording.rate_hz": {"status": "readonly", "note": "pinned at 2 Hz - see known disk-cost bug note above"},
    "flow_recording.max_width": {"status": "readonly", "note": "technical/operational, display only"},
    "flow_recording.min_frames": {"status": "readonly", "note": "technical/operational, display only"},
    "flow_bridge.enabled": {"status": "editable", "note": "real"},
    "flow_bridge.estimator": {"status": "editable", "note": "dynamically enumerated from flow.ESTIMATORS"},
    "flow_bridge.hfov_rad": {"status": "derived", "note": "pinned to the selected vehicle's real camera SDF FOV"},
    "flow_bridge.rate_hz": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.max_width": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.axis_map": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.quality_in_min": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.quality_in_max": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_max_corners": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_quality_level": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_min_distance": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_min_tracks": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_mad_multiplier": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.lk_max_flow_rate_rad_s": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.send_min_quality": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.send_min_matches": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.reset_on_unsent": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.prime_on_unsent": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.startup_prime_hz": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.startup_prime_duration_s": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.prearm_min_mavlink_samples": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.prearm_timeout_s": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.min_sent_samples": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_ctrl": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_qmin": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_n_min": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.ekf2_of_delay": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "control.mode": {"status": "readonly", "note": "structural control-strategy choice, not a simple parameter"},
    "control.setpoint_mode": {"status": "readonly", "note": "structural, paired with control.mode"},
    "control.start_after_takeoff_s": {"status": "readonly", "note": "technical/operational, display only"},
    "control.gnss_loss_after_offboard_s": {"status": "editable", "note": "real minimum-floor trigger"},
    "control.sim_time_wall_multiplier": {"status": "readonly", "note": "wall-clock timeout compensation"},
    "control.warmup_s": {"status": "readonly", "note": "display only"},
    "control.rate_hz": {"status": "readonly", "note": "display only"},
    "control.x_m": {"status": "editable", "note": "real hold-position x setpoint"},
    "control.y_m": {"status": "editable", "note": "real hold-position y setpoint"},
    "control.z_m": {
        "status": "derived",
        "note": "confirmed = -route.altitude_agl_m in all 119 real scenarios with both fields - "
        "kept in sync automatically so raising altitude can't silently leave the hold-position target behind",
    },
    "control.vx_m_s": {"status": "editable", "note": "real"},
    "control.vy_m_s": {"status": "editable", "note": "real"},
    "control.vz_m_s": {"status": "editable", "note": "real"},
    "control.yaw_deg": {"status": "readonly", "note": "technical/operational, display only"},
    "control.use_yaw": {"status": "readonly", "note": "technical/operational, display only"},
    "control.skip_landing_command": {"status": "readonly", "note": "structural, affects run completion behavior"},
    "camera.image_topic": {"status": "readonly", "note": "technical override, display only"},
    "extra_px4_params": {"status": "readonly", "note": "discovered-failure-mode fixes, not design knobs"},

    # External-odometry aiding sub-fields (aiding.mode ==
    # synthetic_external_odometry) - all confirmed real (auto_takeoff_land_pxh_truth.py
    # lines ~1733-1876) but kept readonly like flow_bridge's tuning
    # fields: real safety history here (2026-07-08 incident - nonphysical
    # finite-difference velocity caused gyro clipping and a physics
    # abort), not simple parameters a generated scenario should vary.
    "aiding.rate_hz": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.ekf2_ev_ctrl": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.ekf2_hgt_ref": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.ekf2_ev_delay_ms": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.mav_frame": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.velocity_source": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.velocity_alpha": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.max_finite_diff_speed_m_s": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.velocity_reject_action": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.quality": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.latency_ms": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.injected_error": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.dropout": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.ekf2_extra_params": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.allow_experimental_velocity_fusion": {
        "status": "readonly",
        "note": "gated behind a real incident (2026-07-08 gyro-clipping/physics-abort) - not exposed for editing",
    },
    "aiding.noise": {"status": "readonly", "note": "external-odometry tuning, display only"},
    "aiding.frame": {"status": "dead", "note": "never read by the runner"},
    "aiding.source": {"status": "dead", "note": "never read by the runner"},
    "aiding.input_path": {"status": "dead", "note": "never read by the runner"},

    # Legacy PX4-stock optical-flow pipeline (separate from DATABOSS's own
    # flow_bridge - confirmed real, auto_takeoff_land_pxh_truth.py
    # lines ~1620-1636/2462-2463). Same readonly reasoning as flow_bridge.
    "stock_flow.enabled": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_ctrl": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_qmin": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_n_min": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_n_max": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_gate": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.ekf2_of_delay": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sens_flow_rot": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sens_flow_minhgt": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sens_flow_maxhgt": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sens_flow_rate": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sens_flow_scale": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sim_gz_en_flow": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},
    "stock_flow.sim_gz_en_lidar": {"status": "readonly", "note": "legacy PX4-stock flow pipeline, display only"},

    # Deprecated Phase 7D condition-reference system (Phase 17B: never
    # physically wired to build_gazebo_world.py - see
    # experiments/configs/mvp/conditions/README.md).
    "matrix": {"status": "dead", "note": "deprecated Phase 7D system, never physically wired - see Phase 17B"},
    "conditions": {"status": "dead", "note": "deprecated Phase 7D system, never physically wired - see Phase 17B"},

    "flow_bridge.gyro_mode": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.sift_n_features": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.sift_ratio": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.sift_min_matches": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.send_min_range_m": {"status": "readonly", "note": "tuning-sensitive, display only"},
    "flow_bridge.send_max_range_m": {"status": "readonly", "note": "tuning-sensitive, display only"},

    # PX4 SITL home GPS origin - real (sets PX4_HOME_LAT/LON/ALT env vars),
    # rarely needs to vary per-scenario (every real scenario checked uses
    # the same fixed default), kept readonly rather than exposed as a
    # per-scenario knob.
    "world.home.lat_deg": {"status": "readonly", "note": "PX4 SITL home origin, display only"},
    "world.home.lon_deg": {"status": "readonly", "note": "PX4 SITL home origin, display only"},
    "world.home.alt_m": {"status": "readonly", "note": "PX4 SITL home origin, display only"},

    # Confirmed zero matches anywhere in the runner.
    "gnss.loss_time_s": {"status": "dead", "note": "never read by the runner - real field is loss_after_takeoff_s"},
    "gnss.off_value": {"status": "dead", "note": "never read by the runner"},
    "gnss.restore_value": {"status": "dead", "note": "never read by the runner"},
    "world.fallback_sim_world": {"status": "dead", "note": "never read by the runner"},
    "world.future_name": {"status": "dead", "note": "never read by the runner"},
    "analysis.compare_against_no_aiding": {"status": "dead", "note": "never read by the runner"},
}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    # Dicts that are one cohesive unit (edited/displayed as a whole via
    # FIELD_CLASSIFICATION, or too variable internally to usefully classify
    # field-by-field) - never recursed into, so they don't explode into a
    # wall of misleading "unknown" sub-keys.
    OPAQUE_BLOCKS = (
        "vehicle.start_pose", "world.objects", "world.size_m", "world.wind",
        "extra_px4_params", "aiding.injected_error", "aiding.dropout",
        "aiding.ekf2_extra_params", "aiding.noise", "matrix", "conditions",
    )
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and path not in OPAQUE_BLOCKS:
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


def apply_scenario_edits(
    source: dict[str, Any], edits: dict[str, Any], new_name: str
) -> tuple[dict[str, Any], list[str]]:
    """Apply only whitelisted (status == "editable") dotted-path edits to a
    deep copy of `source`, then recompute every "derived" field. Returns
    (new_scenario, rejected_paths) - a rejected edit is silently NOT
    applied, never partially applied, and the caller decides whether to
    surface the rejection as an error."""
    import copy

    new_scenario = copy.deepcopy(source)
    rejected: list[str] = []

    for path, value in edits.items():
        rule = FIELD_CLASSIFICATION.get(path)
        if rule is None or rule["status"] != "editable":
            rejected.append(path)
            continue
        _set_path(new_scenario, path, value)

    # Derived fields always recomputed after edits, never taken from user
    # input directly - each one closes a real class of bug found in the
    # Phase 17 audit (a scenario whose name doesn't match its filename, a
    # vehicle/airframe pair that can drift apart, a hold-position altitude
    # that silently disagrees with the route's requested altitude).
    _set_path(new_scenario, "run.name", new_name)
    _set_path(new_scenario, "route.name", new_name)

    vehicle_model = _get_path(new_scenario, "vehicle.model")
    if vehicle_model:
        airframe = derive_px4_airframe(vehicle_model)
        _set_path(new_scenario, "vehicle.px4_airframe", airframe)
        _set_path(new_scenario, "vehicle.gazebo_model_name", derive_gazebo_model_name(airframe))

    altitude = _get_path(new_scenario, "route.altitude_agl_m")
    if altitude is not None and _get_path(new_scenario, "control.z_m") is not None:
        _set_path(new_scenario, "control.z_m", -float(altitude))

    aiding_mode = _get_path(new_scenario, "aiding.mode")
    if aiding_mode is not None:
        _set_path(new_scenario, "aiding.external_odom_enabled", aiding_mode == "synthetic_external_odometry")

    return new_scenario, rejected


def apply_world_selection(scenario: dict[str, Any], world_block: dict[str, Any]) -> None:
    """Set the world.* bundle in place - world.name/sdf_path/lighting/wind/
    texture/condition_is_physical are all "derived" (FIELD_CLASSIFICATION),
    set together by picking a world (world_generation.resolve_world_selection),
    never edited individually.

    launch_pad_vehicle_start_z_m is not a world.* field - it's
    resolve_world_selection()'s way of saying "this terrain world has the
    standard launch pad, spawn the vehicle above it" (real gap found
    2026-07-27: leaving vehicle.start_pose.z_m at the template default of
    0 spawns the vehicle at bare heightfield level, not on the pad, and it
    never reaches a stable airborne state). Popped out here and applied to
    vehicle.start_pose.z_m instead of leaking into the world block.
    """
    world_block = dict(world_block)
    launch_pad_z_m = world_block.pop("launch_pad_vehicle_start_z_m", None)
    scenario["world"] = world_block
    if launch_pad_z_m is not None:
        _set_path(scenario, "vehicle.start_pose.z_m", launch_pad_z_m)


def compute_confound_diff(source: dict[str, Any], new_scenario: dict[str, Any]) -> dict[str, list[str]]:
    """Group changed *editable* field paths by top-level block, for the
    one-variable-at-a-time confound banner. Returns {block: [changed paths]}
    - more than one block present means the caller should show a
    non-blocking confound warning.

    Only status=="editable" paths count - "derived" fields (run.name,
    route.name, vehicle.px4_airframe, control.z_m, ...) always change as a
    mechanical side effect of naming/deriving, not an independent user
    choice, so counting them would make nearly every generated scenario
    look "confounded" even when only one real thing was actually changed.
    """
    source_flat = _flatten(source)
    new_flat = _flatten(new_scenario)
    changed: dict[str, list[str]] = {}

    all_paths = set(source_flat) | set(new_flat)
    for path in sorted(all_paths):
        rule = FIELD_CLASSIFICATION.get(path)
        if rule is None or rule["status"] != "editable":
            continue
        if source_flat.get(path) != new_flat.get(path):
            block = path.split(".")[0]
            changed.setdefault(block, []).append(path)

    return changed


def build_run_command(
    scenario_relpath: str,
    gnss_start_used: int | None = None,
    post_loss_hover_s: float | None = None,
) -> str:
    """The exact manual invocation to copy-paste and run - the dashboard's
    actual deliverable for Phase 17C. Matches the real wrapper script's CLI
    (scripts/runner/run_scenario_pxh_end_to_end.py) exactly: a positional
    scenario path, plus --gnss-start-used only when explicitly set (the
    real control for GNSS-start state, since gnss.start_enabled in the
    scenario YAML itself is dead - see FIELD_CLASSIFICATION).

    post_loss_hover_s is request-level for the same reason gnss_start_used
    is: it isn't a scenario YAML field at all. Real gap found 2026-07-27:
    for the only control mode this runner actually exercises
    (offboard_local_position_hold), gnss.loss_after_takeoff_s only acts as
    a presence flag - the real GNSS-denied hold duration is this CLI-only
    flag, defaulting (if omitted here) to whatever's left of --hover-s
    after the pre-loss stabilization window, which is rarely what anyone
    actually wants for a deliberate GNSS-denied duration test. Omitted
    from the command when not given, matching gnss_start_used's pattern.
    """
    parts = [
        "venv/bin/python", "scripts/runner/run_scenario_pxh_end_to_end.py",
        scenario_relpath,
    ]
    if gnss_start_used is not None:
        parts.append(f"--gnss-start-used {gnss_start_used}")
    if post_loss_hover_s is not None:
        parts.append(f"--post-loss-hover-s {post_loss_hover_s}")
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
    """Returns vehicle.model-style names (gz_-prefixed) for disk-backed
    models that also have a registered PX4 airframe.

    Sensor-only submodels can live under src/databoss_sim/models/ too, but
    only NNNN_gz_<model> entries in deploy/px4/px4_pins.yaml under
    airframes are bootable vehicles. The directory scan stays dynamic and
    unhardcoded, matching the principle check_model_sync_and_fov.py
    established in Phase 17B, but the picker excludes models PX4 cannot
    launch.
    """
    if not DATABOSS_MODELS_DIR.is_dir() or not PX4_PINS_PATH.is_file():
        return []
    with PX4_PINS_PATH.open() as f:
        pins = yaml.safe_load(f) or {}
    registered_models: set[str] = set()
    for airframe in pins.get("airframes") or []:
        if not isinstance(airframe, str):
            continue
        prefix, sep, model = airframe.partition("_gz_")
        if sep and prefix.isdigit() and model:
            registered_models.add(model)
    return sorted(f"gz_{p.name}" for p in DATABOSS_MODELS_DIR.iterdir() if p.is_dir() and p.name in registered_models)


def default_scenario_template() -> dict[str, Any]:
    """A complete, from-scratch base scenario - every block a real,
    accepted run actually has, with safe/conservative defaults. Lets
    scenario creation work with NO source scenario to clone (Phase 17C,
    user-requested 2026-07-24: "cover all the yaml options so we don't
    need a source yaml"). Shape and every default value grounded in real
    accepted scenarios (grep across experiments/configs/mvp/scenarios/,
    2026-07-24) - not invented:

    - world.name defaults to flat_rural_phototex_noon, used by 41 of the
      168 real scenario files - the single most common world, a safe
      starting point pending the user picking a different one via
      GET /api/worlds.
    - GNSS starts on, no loss, no optical flow, `default` failsafe -
      the simplest scenario shape that still runs and produces a real,
      gazebo-truth-compared, conformant result. GNSS-loss and flow are
      opt-in via edits, not defaulted on, since enabling them changes
      what the run is testing.
    - QGC/gz-web both on by default (visualization.gazebo_web.enabled:
      true), matching the standing operator policy
      ([[keep-qgc-and-gzweb-every-run]]) - never silently defaulted off.
    - camera/rangefinder proof, all logging, and all analysis flags on by
      default - every accepted real scenario has these on; a run that
      can't be evidence-checked isn't a useful one to generate by default.
    """
    return {
        "run": {
            "name": None,  # overwritten with new_name at write time
            "description": "",
        },
        "vehicle": {
            "model": "gz_x500_cam_lidar_down",
            "px4_airframe": "x500_cam_lidar_down",
            "gazebo_model_name": "x500_cam_lidar_down_0",
            "start_pose": {"x_m": 0, "y_m": 0, "z_m": 0, "yaw_deg": 0},
        },
        "world": {
            "name": "flat_rural_phototex_noon",
            "sdf_path": "generated_worlds/flat_rural_phototex_noon.sdf",
            "lighting": "noon_clear_no_shadows",
            "wind": "none",
            "texture": "photo_speckle",
            "condition_is_physical": True,
        },
        "route": {
            "name": None,  # overwritten with new_name at write time
            "type": "hover",
            "altitude_agl_m": 2.5,
            "duration_s": 30,
            "waypoints": [],
        },
        "gnss": {
            "start_enabled": True,
            "loss_enabled": False,
            "loss_after_takeoff_s": None,
            "restore_after_run": True,
        },
        "failsafe": {"profile": "default"},
        "camera": {
            "proof_enabled": True,
            "description": "Downward camera proof.",
            "render_engine": "ogre",
            "xvfb_enabled": True,
            "xvfb_server_args": "-screen 0 1280x1024x24",
            "headless_rendering": False,
            "probe_timeout_s": 25,
        },
        "rangefinder": {
            "proof_enabled": True,
            "description": "Downward rangefinder proof.",
            "render_engine": "ogre",
            "xvfb_enabled": True,
            "probe_timeout_s": 20,
            "min_ulog_rows": 50,
            "height_agreement_tolerance_m": 0.75,
        },
        "visualization": {
            "gazebo_web": {
                "enabled": True,
                "required": True,
                "config": "/usr/share/gz/gz-launch7/configs/websocket.gzlaunch",
                "host": "127.0.0.1",
                "port": 9003,
                "publication_hz": 15,
                "startup_timeout_s": 15,
            }
        },
        "aiding": {"mode": "none", "enabled": False, "external_odom_enabled": False},
        "logging": {
            "record_ulog": True,
            "record_gazebo_truth": True,
            "record_sensor_logs": True,
            "record_camera_topics": True,
            "record_external_odometry_sent": False,
        },
        "analysis": {
            "align_with_gazebo_truth": True,
            "generate_plots": True,
            "generate_summary": True,
            "check_external_odometry_received": False,
            "sensor_contract_report": True,
            "sensor_contract_gate": "timing",
            "sensor_contract_gate_required": False,
        },
        "flow_recording": {"enabled": True, "rate_hz": 2, "max_width": 640, "min_frames": 100},
        "flow_bridge": {
            "enabled": False,
            "estimator": "lk",
            "rate_hz": 40,
            "max_width": 320,
            "hfov_rad": 1.74,
            "axis_map": "xy",
            "quality_in_min": 20,
            "quality_in_max": 100,
            "lk_max_corners": 160,
            "lk_quality_level": 0.01,
            "lk_min_distance": 6.0,
            "lk_min_tracks": 8,
            "lk_mad_multiplier": 3.0,
            "lk_max_flow_rate_rad_s": 7.4,
            "send_min_quality": 0,
            "send_min_matches": 0,
            "reset_on_unsent": False,
            "prime_on_unsent": True,
            "startup_prime_hz": 2,
            "startup_prime_duration_s": 120,
            "prearm_min_mavlink_samples": 3,
            "prearm_timeout_s": 30,
            "min_sent_samples": 100,
            "ekf2_of_ctrl": 1,
            "ekf2_of_qmin": 17,
            "ekf2_of_n_min": 0.3,
            "ekf2_of_delay": 140,
        },
        "control": {
            "mode": "offboard_local_position_hold",
            "setpoint_mode": "velocity_xy_position_z",
            "start_after_takeoff_s": 5,
            "warmup_s": 2,
            "gnss_loss_after_offboard_s": 3,
            "sim_time_wall_multiplier": 30,
            "rate_hz": 20,
            "x_m": 0,
            "y_m": 0,
            "z_m": -2.5,
            "vx_m_s": 0.0,
            "vy_m_s": 0.0,
            "vz_m_s": 0.0,
            "yaw_deg": 0,
            "skip_landing_command": False,
        },
        "extra_px4_params": {},
    }
