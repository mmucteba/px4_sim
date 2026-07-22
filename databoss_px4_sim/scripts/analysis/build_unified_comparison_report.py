#!/usr/bin/env python3
"""Generate a unified, manifest-driven LK/SIFT/stock x GNSS-on/off comparison report.

Extends the Phase 11 detailed-audit report generator: instead of a hardcoded
CASES list, cases come from a manifest YAML (algorithm x gnss_state x
world_variant tags), and GPS loss/available status is independently
verified per case from the ULog rather than trusted from the manifest tag
or the run's own status-file flags -- this is the guard that would have
caught the original Phase 11 mistake (run 115202 tagged GNSS-loss despite
GPS never actually dropping) automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from comparison_manifest import Case, Manifest, load_manifest, open_ulog

ROOT = Path("/opt/databoss_px4_sim")

# Sensor SDF sources per algorithm -- read once per report, not per case,
# since LK/SIFT always share DATABOSS's own vehicle and stock always uses
# PX4's own x500_flow. Lidar and camera live in different SDF files: the
# lidar is defined directly on the vehicle model, the camera comes from a
# separately merged-in PX4/DATABOSS sub-model.
LIDAR_SDF_PATHS = {
    "lk": ROOT / "src/databoss_sim/models/x500_cam_lidar_down/model.sdf",
    "sift": ROOT / "src/databoss_sim/models/x500_cam_lidar_down/model.sdf",
    "stock": Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz/models/x500_flow/model.sdf"),
}
CAMERA_SDF_PATHS = {
    "lk": Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz/models/mono_cam/model.sdf"),
    "sift": Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz/models/mono_cam/model.sdf"),
    "stock": Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz/models/optical_flow/model.sdf"),
}

STOCK_LIDAR_KNOWN_CHARACTERISTIC = (
    "Stock's rangefinder shows a high non-finite (`inf`) dropout rate (~44% measured across "
    "two genuinely GNSS-denied replicates, `122327` and `133743`) that LK/SIFT do not exhibit. "
    "Root cause (confirmed by direct SDF inspection this phase): PX4's own `x500_flow/model.sdf` "
    "GPU lidar uses a degenerate `1x1` sample geometry (`horizontal samples=1, min_angle=max_angle=0`), "
    "which gz-sim's raycasting renders unreliably even though PX4's `GZBridge.cpp::laserScantoLidarSensorCallback` "
    "only ever reads `ranges()[0]` regardless of sample count. DATABOSS's own `x500_cam_lidar_down` model "
    "avoids this with a `3x1` horizontal fan (`+/-0.02 rad`), documented in that model's SDF since 2026-07-13, "
    "but that fix was never applied to stock's own model file. **This is accepted as-is for the stock reference "
    "case per explicit project decision this phase -- PX4-Autopilot's engine tree stays off-limits to edit "
    "without separate authorization, and this dropout rate is treated as a known characteristic of the stock "
    "baseline, not a defect being tracked for repair.** Note the dropout *rate* is not the same as the height-drift "
    "failure mode: `122327`'s surviving finite readings drifted from ~2.3m to ~3.3m+ (failed `distance_sensor_ok`), "
    "while `133743` had an almost identical dropout rate but no drift (passed) -- these are two distinct, only "
    "sometimes co-occurring phenomena, not one bug."
)


def nested(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out):
        return None
    return out


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    val = safe_float(value)
    if val is not None:
        return f"{val:.{digits}f}"
    return str(value)


def rate_hz(timestamps_us: np.ndarray) -> float | None:
    if len(timestamps_us) < 2:
        return None
    duration = (float(timestamps_us[-1]) - float(timestamps_us[0])) / 1e6
    if duration <= 0:
        return None
    return (len(timestamps_us) - 1) / duration


def topic_rate(data: dict[str, np.ndarray]) -> dict[str, Any]:
    ts = np.asarray(data.get("timestamp", []), dtype=float)
    return {
        "rows": int(len(ts)),
        "duration_s": None if len(ts) < 2 else (float(ts[-1]) - float(ts[0])) / 1e6,
        "rate_hz": rate_hz(ts),
    }


def unique_ints(values: np.ndarray) -> list[int]:
    if len(values) == 0:
        return []
    return sorted(int(v) for v in np.unique(values.astype(float)) if np.isfinite(v))


def bool_fraction(data: dict[str, np.ndarray], field: str) -> float | None:
    if field not in data:
        return None
    arr = np.asarray(data[field], dtype=float)
    if len(arr) == 0:
        return None
    return float(np.nanmean(arr))


def sum_bool(data: dict[str, np.ndarray], field: str) -> int | None:
    if field not in data:
        return None
    return int(np.nansum(np.asarray(data[field], dtype=float)))


def numeric_stats(data: dict[str, np.ndarray], field: str) -> dict[str, Any]:
    if field not in data:
        return {}
    arr = np.asarray(data[field], dtype=float)
    finite = arr[np.isfinite(arr)]
    out: dict[str, Any] = {
        f"{field}_finite_fraction": None if len(arr) == 0 else float(len(finite) / len(arr)),
        f"{field}_nonfinite_count": int(len(arr) - len(finite)),
    }
    if len(finite):
        out.update(
            {
                f"{field}_min": float(np.min(finite)),
                f"{field}_mean": float(np.mean(finite)),
                f"{field}_median": float(np.median(finite)),
                f"{field}_p95": float(np.percentile(finite, 95)),
                f"{field}_max": float(np.max(finite)),
            }
        )
    return out


def first_true_rel(topic: dict[str, np.ndarray], mask: np.ndarray, t0_us: float) -> float | None:
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return None
    ts = np.asarray(topic["timestamp"], dtype=float)
    return (float(ts[idx[0]]) - t0_us) / 1e6


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def read_commands(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(errors="ignore")


def extract_case(case: Case) -> dict[str, Any]:
    run_dir = case.run_dir
    config = read_config(run_dir / "config.yaml")
    status = load_json(run_dir / "logs/pxh_takeoff_land_truth_status.json")
    metrics = load_json(run_dir / "ekf_vs_ground_truth_metrics.json")
    commands = read_commands(run_dir / "commands.log")

    ulog = open_ulog(run_dir)
    topics = {d.name: d.data for d in ulog.data_list}

    all_timestamps: list[float] = []
    for data in topics.values():
        if "timestamp" in data and len(data["timestamp"]):
            all_timestamps.append(float(np.asarray(data["timestamp"], dtype=float)[0]))
    t0_us = min(all_timestamps) if all_timestamps else 0.0

    out: dict[str, Any] = {
        "key": case.key,
        "label": case.label,
        "short": case.short,
        "kind": case.kind,
        "gnss_state": case.gnss_state,
        "world_variant": case.world_variant,
        "replicate_of": case.replicate_of,
        "run_dir": str(run_dir),
        "config": {
            "vehicle_model": nested(config, "vehicle", "model"),
            "px4_airframe": nested(config, "vehicle", "px4_airframe"),
            "gazebo_model_name": nested(config, "vehicle", "gazebo_model_name"),
            "world": nested(config, "world", "name"),
            "world_sdf": nested(config, "world", "sdf_path"),
            "world_texture": nested(config, "world", "texture"),
            "wind": nested(config, "world", "wind"),
            "lighting": nested(config, "world", "lighting"),
            "condition_is_physical": nested(config, "world", "condition_is_physical"),
            "route_name": nested(config, "route", "name"),
            "route_type": nested(config, "route", "type"),
            "altitude_agl_m": nested(config, "route", "altitude_agl_m"),
            "duration_s": nested(config, "route", "duration_s"),
            "gnss_loss_enabled_cfg": nested(config, "gnss", "loss_enabled"),
            "gnss_loss_after_takeoff_s": nested(config, "gnss", "loss_after_takeoff_s"),
            "gnss_start_enabled": nested(config, "gnss", "start_enabled"),
            "failsafe_requested": nested(config, "failsafe", "profile"),
            "control_mode": nested(config, "control", "mode"),
            "setpoint_mode": nested(config, "control", "setpoint_mode"),
            "control_start_after_takeoff_s": nested(config, "control", "start_after_takeoff_s"),
            "control_warmup_s": nested(config, "control", "warmup_s"),
            "vx_m_s": nested(config, "control", "vx_m_s"),
            "vy_m_s": nested(config, "control", "vy_m_s"),
            "vz_m_s": nested(config, "control", "vz_m_s"),
            "x_m": nested(config, "control", "x_m"),
            "y_m": nested(config, "control", "y_m"),
            "z_m": nested(config, "control", "z_m"),
            "skip_landing_command": nested(config, "control", "skip_landing_command"),
            "flow_bridge_enabled": nested(config, "flow_bridge", "enabled", default=False),
            "flow_bridge_estimator": nested(config, "flow_bridge", "estimator"),
            "flow_bridge_rate_hz": nested(config, "flow_bridge", "rate_hz"),
            "axis_map": nested(config, "flow_bridge", "axis_map"),
            "max_width": nested(config, "flow_bridge", "max_width"),
            "hfov_rad": nested(config, "flow_bridge", "hfov_rad"),
            "quality_in_min": nested(config, "flow_bridge", "quality_in_min"),
            "quality_in_max": nested(config, "flow_bridge", "quality_in_max"),
            "send_min_quality": nested(config, "flow_bridge", "send_min_quality"),
            "send_min_matches": nested(config, "flow_bridge", "send_min_matches"),
            "send_min_range_m": nested(config, "flow_bridge", "send_min_range_m"),
            "send_max_range_m": nested(config, "flow_bridge", "send_max_range_m"),
            "lk_max_corners": nested(config, "flow_bridge", "lk_max_corners"),
            "lk_min_tracks": nested(config, "flow_bridge", "lk_min_tracks"),
            "lk_max_flow_rate_rad_s": nested(config, "flow_bridge", "lk_max_flow_rate_rad_s"),
            "sift_n_features": nested(config, "flow_bridge", "sift_n_features"),
            "sift_ratio": nested(config, "flow_bridge", "sift_ratio"),
            "sift_min_matches": nested(config, "flow_bridge", "sift_min_matches"),
            "ekf2_of_ctrl": nested(config, "flow_bridge", "ekf2_of_ctrl") or nested(config, "stock_flow", "ekf2_of_ctrl"),
            "ekf2_of_qmin": nested(config, "flow_bridge", "ekf2_of_qmin") or nested(config, "stock_flow", "ekf2_of_qmin"),
            "ekf2_of_n_min": nested(config, "flow_bridge", "ekf2_of_n_min") or nested(config, "stock_flow", "ekf2_of_n_min"),
            "ekf2_of_delay": nested(config, "flow_bridge", "ekf2_of_delay") or nested(config, "stock_flow", "ekf2_of_delay"),
            "stock_flow_enabled": nested(config, "stock_flow", "enabled", default=False),
            "stock_flow_sens_flow_rot": nested(config, "stock_flow", "sens_flow_rot"),
            "stock_flow_sens_flow_minhgt": nested(config, "stock_flow", "sens_flow_minhgt"),
            "stock_flow_sens_flow_maxhgt": nested(config, "stock_flow", "sens_flow_maxhgt"),
            "stock_flow_sim_gz_en_flow": nested(config, "stock_flow", "sim_gz_en_flow"),
            "stock_flow_sim_gz_en_lidar": nested(config, "stock_flow", "sim_gz_en_lidar"),
            "rangefinder_tolerance_m": nested(config, "rangefinder", "height_agreement_tolerance_m"),
            "rangefinder_min_ulog_rows": nested(config, "rangefinder", "min_ulog_rows"),
            "camera_image_topic_override": nested(config, "camera", "image_topic"),
            "flow_recording_enabled": nested(config, "flow_recording", "enabled", default=False),
            "flow_recording_rate_hz": nested(config, "flow_recording", "rate_hz"),
            "flow_recording_max_width": nested(config, "flow_recording", "max_width"),
            "flow_recording_min_frames": nested(config, "flow_recording", "min_frames"),
        },
        "status": {
            "accepted": status.get("accepted"),
            "returncode": status.get("returncode"),
            "failsafe_profile_effective": status.get("failsafe_profile"),
            "failsafe_profile_ok": status.get("failsafe_profile_ok"),
            "gnss_loss_requested": status.get("gnss_loss_requested"),
            "gnss_loss_after_takeoff_s": status.get("gnss_loss_after_takeoff_s"),
            "effective_gnss_loss_after_takeoff_s": status.get("effective_gnss_loss_after_takeoff_s"),
            "post_loss_hover_s": status.get("post_loss_hover_s"),
            "flow_bridge_started": status.get("flow_bridge_started"),
            "flow_bridge_sent_rows": status.get("flow_bridge_sent_rows"),
            "flow_bridge_ok": status.get("flow_bridge_ok"),
            "stock_flow_enabled": status.get("stock_flow_enabled"),
            "distance_sensor_ok": status.get("ulog_distance_sensor_ok"),
            "distance_sensor_height_diff_m": status.get("ulog_distance_sensor_height_diff_m"),
            "distance_sensor_rows": status.get("ulog_distance_sensor_rows"),
            "ulog_flight_ok": status.get("ulog_flight_ok"),
            "ulog_airborne_duration_s": status.get("ulog_airborne_duration_s"),
            "ulog_max_height_up_m": status.get("ulog_max_height_up_m"),
        },
        "metrics": {
            "accepted": metrics.get("accepted"),
            "comparison_window_ok": metrics.get("comparison_window_ok"),
            "comparison_end_reason": metrics.get("comparison_end_reason"),
            "aligned_duration_s": metrics.get("aligned_duration_s"),
            "horizontal_error_max_m": nested(metrics, "horizontal_error", "max_m"),
            "horizontal_error_mean_m": nested(metrics, "horizontal_error", "mean_m"),
            "horizontal_error_p95_m": nested(metrics, "horizontal_error", "p95_m"),
            "height_error_max_m": nested(metrics, "height_abs_error", "max_m"),
            "height_error_mean_m": nested(metrics, "height_abs_error", "mean_m"),
            "height_error_p95_m": nested(metrics, "height_abs_error", "p95_m"),
            "error_3d_max_m": nested(metrics, "error_3d", "max_m"),
            "truth_path_end_m": nested(metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", "end_m"),
            "truth_path_max_m": nested(metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", "max_m"),
            "px4_path_end_m": nested(metrics, "station_keeping", "px4_horizontal_displacement_from_start", "end_m"),
            "px4_path_max_m": nested(metrics, "station_keeping", "px4_horizontal_displacement_from_start", "max_m"),
        },
        "commands": {
            "entry_command": next((line for line in commands.splitlines() if line.startswith("python3 ")), None),
        },
        "ulog_topics_present": sorted(topics),
    }

    if "vehicle_local_position" in topics:
        vlp = topics["vehicle_local_position"]
        z_up = -np.asarray(vlp["z"], dtype=float)
        ts = np.asarray(vlp["timestamp"], dtype=float)
        mask = z_up > 0.5
        out["vehicle_local_position"] = {
            **topic_rate(vlp),
            "takeoff_threshold_0p5_rel_s": first_true_rel(vlp, mask, t0_us),
            "max_height_up_m": float(np.nanmax(z_up)),
        }
    else:
        out["vehicle_local_position"] = {}

    for topic in ["vehicle_gps_position", "sensor_gps"]:
        if topic not in topics:
            continue
        gps = topics[topic]
        ts = np.asarray(gps["timestamp"], dtype=float)
        fix = np.asarray(gps.get("fix_type", []), dtype=float)
        sat = np.asarray(gps.get("satellites_used", []), dtype=float)
        gps_out: dict[str, Any] = {**topic_rate(gps)}
        if len(fix):
            gps_out.update(
                {
                    "fix_type_unique": unique_ints(fix),
                    "first_fix_lt3_rel_s": first_true_rel(gps, fix < 3, t0_us),
                    "last_fix_ge3_rel_s": None
                    if not np.any(fix >= 3)
                    else (float(ts[np.where(fix >= 3)[0][-1]]) - t0_us) / 1e6,
                    "all_fix_ge3": bool(np.all(fix >= 3)),
                }
            )
        if len(sat):
            gps_out.update({"satellites_min": float(np.nanmin(sat)), "satellites_max": float(np.nanmax(sat))})
        out[topic] = gps_out

    if "sensor_optical_flow" in topics:
        sof = topics["sensor_optical_flow"]
        sof_out = {**topic_rate(sof)}
        for field in ["quality", "pixel_flow[0]", "pixel_flow[1]"]:
            sof_out.update(numeric_stats(sof, field))
        if "quality" in sof:
            q = np.asarray(sof["quality"], dtype=float)
            sof_out["quality_zero_fraction"] = float(np.nanmean(q <= 0)) if len(q) else None
        out["sensor_optical_flow"] = sof_out

    if "vehicle_optical_flow" in topics:
        out["vehicle_optical_flow"] = topic_rate(topics["vehicle_optical_flow"])

    if "estimator_aid_src_optical_flow" in topics:
        aid = topics["estimator_aid_src_optical_flow"]
        aid_out = {**topic_rate(aid)}
        aid_out.update(
            {
                "fused_count": sum_bool(aid, "fused"),
                "innovation_rejected_count": sum_bool(aid, "innovation_rejected"),
                "fused_fraction": bool_fraction(aid, "fused"),
                "innovation_rejected_fraction": bool_fraction(aid, "innovation_rejected"),
            }
        )
        aid_out.update(numeric_stats(aid, "test_ratio"))
        out["estimator_aid_src_optical_flow"] = aid_out

    for topic in ["estimator_aid_src_gnss_pos", "estimator_aid_src_gnss_vel", "estimator_aid_src_gnss_hgt", "estimator_aid_src_rng_hgt"]:
        if topic not in topics:
            continue
        aid = topics[topic]
        aid_out = {**topic_rate(aid)}
        if "fused" in aid:
            aid_out["fused_count"] = sum_bool(aid, "fused")
            aid_out["fused_fraction"] = bool_fraction(aid, "fused")
        out[topic] = aid_out

    if "estimator_status_flags" in topics:
        flags = topics["estimator_status_flags"]
        flag_out = {**topic_rate(flags)}
        for field in ["cs_opt_flow", "cs_gnss_pos", "cs_gnss_vel", "cs_gps_hgt", "cs_rng_hgt", "cs_inertial_dead_reckoning"]:
            flag_out[f"{field}_fraction"] = bool_fraction(flags, field)
        out["estimator_status_flags"] = flag_out

    if "distance_sensor" in topics:
        dist = topics["distance_sensor"]
        dist_out = {**topic_rate(dist)}
        dist_out.update(numeric_stats(dist, "current_distance"))
        if "current_distance" in dist:
            values = np.asarray(dist["current_distance"], dtype=float)
            finite = np.isfinite(values)
            dist_out["finite_fraction"] = float(np.mean(finite)) if len(values) else None
            dist_out["nonfinite_count"] = int(np.sum(~finite))
        out["distance_sensor"] = dist_out

    # GPS loss/available independent verification -- computed directly from
    # the ULog above (first_fix_lt3_rel_s / all_fix_ge3), never trusted from
    # the manifest tag or the status-file gnss_loss_detected/gnss_loss_ok
    # flags. This is the guard for the 115202 class of mistake.
    gps = out.get("vehicle_gps_position") or out.get("sensor_gps") or {}
    if gps.get("first_fix_lt3_rel_s") is not None:
        observed_state = "loss"
    elif gps.get("all_fix_ge3"):
        observed_state = "on"
    else:
        observed_state = "unknown"
    out["gps_guard"] = {
        "manifest_gnss_state": case.gnss_state,
        "observed_gnss_state": observed_state,
        "first_fix_lt3_rel_s": gps.get("first_fix_lt3_rel_s"),
        "last_fix_ge3_rel_s": gps.get("last_fix_ge3_rel_s"),
        "mismatch": observed_state != "unknown" and observed_state != case.gnss_state,
    }

    # Camera inputs (flow_recording), when present. Frames land in a
    # frames/ subdirectory (frame_NNNNNN.jpg), indexed by frames_index.csv.
    flow_recording_dir = run_dir / "flow_recording"
    frames_subdir = flow_recording_dir / "frames"
    if frames_subdir.is_dir():
        frames = sorted(frames_subdir.glob("*.jpg")) + sorted(frames_subdir.glob("*.png"))
    elif flow_recording_dir.is_dir():
        frames = sorted(flow_recording_dir.glob("*.jpg")) + sorted(flow_recording_dir.glob("*.png"))
    else:
        frames = []
    if flow_recording_dir.is_dir():
        out["camera_inputs"] = {
            "flow_recording_dir": str(flow_recording_dir),
            "frame_count": len(frames),
            "sample_frames": [str(p) for p in ([frames[0], frames[len(frames) // 2], frames[-1]] if len(frames) >= 3 else frames)],
        }
    else:
        out["camera_inputs"] = {"flow_recording_dir": None, "frame_count": 0, "sample_frames": []}

    return out


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def case_link(path: str) -> str:
    p = Path(path)
    return f"[{p.name}]({p})"


def extract_sensor_block(text: str, sensor_type: str) -> str | None:
    """First <sensor ... type='sensor_type'> ... </sensor> block, whole text."""
    pattern = re.compile(r"<sensor\s[^>]*type=['\"]" + re.escape(sensor_type) + r"['\"][^>]*>.*?</sensor>", re.DOTALL)
    m = pattern.search(text)
    return m.group(0) if m else None


def sdf_tag_block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[ >].*?</{tag}>", text, re.DOTALL)
    return m.group(0) if m else ""


def sdf_value(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1).strip() if m else None


def read_raw_sensor_block(path: Path, sensor_type: str) -> str:
    if not path.exists():
        return f"(SDF not found at `{path}`)"
    block = extract_sensor_block(path.read_text(), sensor_type)
    if not block:
        return f"(no `type='{sensor_type}'` sensor block found in `{path}`)"
    return "\n".join(line.strip() for line in block.splitlines() if line.strip())


def lidar_human_summary(path: Path) -> str:
    if not path.exists():
        return f"Lidar SDF not found at `{path}`."
    block = extract_sensor_block(path.read_text(), "gpu_lidar")
    if not block:
        return f"No `gpu_lidar` sensor block found in `{path}`."
    horiz = sdf_tag_block(block, "horizontal")
    vert = sdf_tag_block(block, "vertical")
    range_block = sdf_tag_block(block, "range")
    h_samples = sdf_value(horiz, "samples")
    h_min = sdf_value(horiz, "min_angle")
    h_max = sdf_value(horiz, "max_angle")
    v_samples = sdf_value(vert, "samples")
    r_min = sdf_value(range_block, "min")
    r_max = sdf_value(range_block, "max")
    update_rate = sdf_value(block, "update_rate")
    if h_samples == "1":
        geometry = "a single centered ray (degenerate 1x1 -- no fan, see known-characteristic note below)"
        consumer_note = (
            "PX4 reads only this one ray (`ranges[0]`, nadir) -- with no neighboring samples, a degenerate "
            "1-pixel GPU depth render is unreliable and randomly returns `inf` (see known-characteristic note below)."
        )
    else:
        geometry = f"a {h_samples}-ray horizontal fan spanning {h_min} to {h_max} rad"
        consumer_note = (
            "PX4 still only ever reads the first ray (`ranges[0]`, nadir) regardless of sample count -- the extra "
            "horizontal samples exist purely to make the GPU depth render non-degenerate, not to widen the "
            "effective field of view."
        )
    return (
        f"Downward GPU lidar: {geometry}, {v_samples} vertical sample(s) (nadir-only, no vertical spread), "
        f"range {r_min}-{r_max} m, update rate {update_rate} Hz. {consumer_note}"
    )


def camera_human_summary(path: Path) -> str:
    if not path.exists():
        return f"Camera SDF not found at `{path}`."
    block = extract_sensor_block(path.read_text(), "camera")
    if not block:
        return f"No `camera` sensor block found in `{path}`."
    fov = sdf_value(block, "horizontal_fov")
    width = sdf_value(block, "width")
    height = sdf_value(block, "height")
    near = sdf_value(block, "near")
    far = sdf_value(block, "far")
    update_rate = sdf_value(block, "update_rate")
    fov_deg = f"{math.degrees(float(fov)):.0f} deg" if fov else "n/a"
    return (
        f"Downward monocular camera: {width}x{height} px, horizontal FOV {fov} rad (~{fov_deg}), "
        f"clip range {near}-{far} m, native update rate {update_rate} Hz."
    )


def copy_sample_frames(data: list[dict[str, Any]], out_dir: Path) -> None:
    samples_dir = out_dir / "camera_samples"
    for item in data:
        cam = item["camera_inputs"]
        if not cam["sample_frames"]:
            continue
        case_dir = samples_dir / item["key"]
        case_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in cam["sample_frames"]:
            src_path = Path(src)
            dst_path = case_dir / src_path.name
            shutil.copy2(src_path, dst_path)
            copied.append(str(dst_path.relative_to(out_dir)))
        cam["sample_frame_report_paths"] = copied


def build_report(manifest: Manifest, data: list[dict[str, Any]], out_dir: Path) -> str:
    lines: list[str] = []
    lines.append(f"# {manifest.title}")
    lines.append("")
    lines.append(
        "Generated from run configs, runner status JSON, PX4 ULogs, Gazebo truth metrics, "
        f"and captured camera frames, driven by `manifest.yaml` (`{manifest.name}`). "
        "Adding a case is a manifest edit; no script changes required."
    )
    lines.append("")

    mismatches = [item for item in data if item["gps_guard"]["mismatch"]]
    lines.append("## Verdict")
    lines.append("")
    if mismatches:
        lines.append(
            f"**WARNING: {len(mismatches)} case(s) have a GPS-state mismatch between the manifest tag and "
            "the independently-verified ULog evidence.** See GPS Loss Verification below before trusting any "
            "other number in this report for the flagged case(s)."
        )
    else:
        lines.append(
            "All cases' manifest `gnss_state` tags agree with independently-verified `vehicle_gps_position`/"
            "`sensor_gps` evidence from the ULog (see GPS Loss Verification). No silent GNSS-loss/GNSS-on "
            "mislabeling detected."
        )
    lines.append("")

    lines.append("## Test Conditions At A Glance")
    lines.append("")
    lines.append(
        "One row per case, everything needed to know what was actually flown without cross-referencing "
        "other tables: algorithm, GNSS state, altitude, commanded velocity, world/lighting/wind, and vehicle. "
        "Full detail (axis maps, EKF gains, sensor rates, ...) is in the sections below."
    )
    lines.append("")
    rows = []
    for item in data:
        cfg = item["config"]
        gnss_label = "GNSS on (throughout)" if item["gnss_state"] == "on" else f"GNSS loss (t~{fmt(item['gps_guard'].get('first_fix_lt3_rel_s'), 1)}s)"
        algo_label = {"lk": "LK optical flow", "sift": "SIFT optical flow", "stock": "PX4 stock flow", "none": "No aiding (unaided)"}.get(item["kind"], item["kind"])
        wind = cfg["wind"] or "none"
        lighting = cfg["lighting"] or "n/a"
        rows.append(
            [
                item["short"],
                algo_label,
                gnss_label,
                f"{fmt(cfg['altitude_agl_m'], 1)} m AGL",
                f"vy={fmt(cfg['vy_m_s'], 2)} m/s",
                f"{cfg['world']}, {lighting}, wind={wind}",
                cfg["vehicle_model"],
            ]
        )
    lines.append(markdown_table(["Case", "Algorithm", "GNSS", "Altitude", "Commanded velocity", "World / lighting / wind", "Vehicle model"], rows))
    lines.append("")

    lines.append("## Runs And Classification")
    rows = []
    for item in data:
        rep = f" (replicate of `{item['replicate_of']}`)" if item.get("replicate_of") else ""
        rows.append(
            [
                item["short"] + rep,
                item["kind"],
                item["gnss_state"],
                item["world_variant"],
                case_link(item["run_dir"]),
                fmt(item["status"].get("accepted")),
                fmt(item["metrics"].get("accepted")),
            ]
        )
    lines.append(markdown_table(["Case", "Algorithm", "GNSS state (manifest)", "World variant", "Run", "Validation", "Metrics"], rows))
    lines.append("")

    lines.append("## GPS Loss Verification (independent of manifest tag and status flags)")
    lines.append("")
    lines.append(
        "Computed directly from `vehicle_gps_position`/`sensor_gps` in each case's ULog -- `fix_type < 3` at "
        "any point means GPS was lost; `fix_type >= 3` for every sample means GPS was available throughout. "
        "This does **not** trust the runner's own `gnss_loss_detected`/`gnss_loss_ok` status-file flags, which "
        "only confirm the `SIM_GPS_USED 0` command was sent -- not that the simulated GPS driver actually "
        "dropped the fix (this is exactly how run `115202` was wrongly accepted as a GNSS-loss comparator "
        "in the original Phase 11 report)."
    )
    lines.append("")
    rows = []
    for item in data:
        g = item["gps_guard"]
        rows.append(
            [
                item["short"],
                g["manifest_gnss_state"],
                g["observed_gnss_state"],
                "**MISMATCH**" if g["mismatch"] else "match",
                fmt(g["first_fix_lt3_rel_s"]),
                fmt(g["last_fix_ge3_rel_s"]),
            ]
        )
    lines.append(markdown_table(["Case", "Manifest tag", "Observed (ULog)", "Status", "First fix<3 rel s", "Last fix>=3 rel s"], rows))
    lines.append("")

    lines.append("## System Configuration")
    rows = []
    for item in data:
        cfg = item["config"]
        rows.append(
            [
                item["short"],
                cfg["vehicle_model"],
                cfg["px4_airframe"],
                cfg["gazebo_model_name"],
                cfg["world"],
                cfg["world_texture"],
                cfg["lighting"],
                cfg["wind"],
                fmt(cfg["condition_is_physical"]),
            ]
        )
    lines.append(markdown_table(["Case", "Vehicle", "Airframe", "GZ model", "World", "Texture", "Lighting", "Wind", "Physical"], rows))
    lines.append("")

    lines.append("## Route And Control Settings")
    rows = []
    for item in data:
        cfg = item["config"]
        rows.append(
            [
                item["short"],
                cfg["route_name"],
                cfg["route_type"],
                fmt(cfg["altitude_agl_m"]),
                fmt(cfg["duration_s"]),
                cfg["control_mode"],
                cfg["setpoint_mode"],
                f"({fmt(cfg['vx_m_s'])}, {fmt(cfg['vy_m_s'])}, {fmt(cfg['vz_m_s'])})",
                fmt(cfg["skip_landing_command"]),
            ]
        )
    lines.append(markdown_table(["Case", "Route", "Type", "Alt AGL m", "Duration s", "Control", "Setpoint", "Velocity m/s", "Skip land"], rows))
    lines.append("")

    lines.append("## World And Environment Settings")
    lines.append("")
    lines.append(
        "This table reads world, lighting, texture, wind, and SDF path directly from each run's copied "
        "`config.yaml`. Future world-lighting/shadow/reflectivity or sensor-range variants become new rows "
        "automatically once a manifest entry points at a run whose `world:` block actually differs, so the "
        "report does not silently claim a variant was tested when it was only named in the manifest."
    )
    lines.append("")
    rows = []
    for item in data:
        cfg = item["config"]
        rows.append([item["short"], item["world_variant"], cfg["world"], cfg["lighting"], cfg["world_texture"], cfg["wind"], cfg["world_sdf"]])
    lines.append(markdown_table(["Case", "World variant tag", "World name", "Lighting", "Texture", "Wind", "SDF path"], rows))
    lines.append("")

    lines.append("## Sensor Settings And Known Characteristics")
    lines.append("")
    for kind in ["lk", "sift", "stock"]:
        if not any(item["kind"] == kind for item in data):
            continue
        lidar_path = LIDAR_SDF_PATHS[kind]
        camera_path = CAMERA_SDF_PATHS[kind]
        lines.append(f"### {kind.upper()} vehicle sensors")
        lines.append("")
        lines.append(f"- **{camera_human_summary(camera_path)}**")
        lines.append(f"- **{lidar_human_summary(lidar_path)}**")
        lines.append("")
        lines.append(f"<details><summary>Raw camera SDF (`{camera_path}`)</summary>")
        lines.append("")
        lines.append("```xml")
        lines.append(read_raw_sensor_block(camera_path, "camera"))
        lines.append("```")
        lines.append("</details>")
        lines.append("")
        lines.append(f"<details><summary>Raw lidar SDF (`{lidar_path}`)</summary>")
        lines.append("")
        lines.append("```xml")
        lines.append(read_raw_sensor_block(lidar_path, "gpu_lidar"))
        lines.append("```")
        lines.append("</details>")
        lines.append("")
    lines.append("### Stock rangefinder non-finite dropout -- accepted known characteristic, not a defect")
    lines.append("")
    lines.append(STOCK_LIDAR_KNOWN_CHARACTERISTIC)
    lines.append("")

    lines.append("## Camera Inputs")
    lines.append("")
    rows = []
    for item in data:
        cfg = item["config"]
        cam = item["camera_inputs"]
        rows.append(
            [
                item["short"],
                fmt(cfg["flow_recording_enabled"]),
                fmt(cfg["flow_recording_rate_hz"], 1),
                cfg["flow_recording_max_width"],
                cam["frame_count"],
            ]
        )
    lines.append(markdown_table(["Case", "Recording enabled", "Rate Hz", "Max width px", "Frames captured"], rows))
    lines.append("")
    for item in data:
        cam = item["camera_inputs"]
        if not cam.get("sample_frame_report_paths"):
            continue
        lines.append(f"**{item['short']} sample frames** (first / mid / last of {cam['frame_count']}):")
        lines.append("")
        for p in cam["sample_frame_report_paths"]:
            lines.append(f"![{item['short']} sample]({p})")
        lines.append("")

    lines.append("## Optical Flow Configuration")
    rows = []
    for item in data:
        cfg = item["config"]
        rows.append(
            [
                item["short"],
                cfg["flow_bridge_enabled"],
                cfg["flow_bridge_estimator"] or ("stock" if item["kind"] == "stock" else "none (unaided)"),
                cfg["flow_bridge_rate_hz"] or "native",
                cfg["axis_map"] or "PX4 native",
                cfg["ekf2_of_ctrl"],
                cfg["ekf2_of_qmin"],
                cfg["ekf2_of_n_min"],
                cfg["ekf2_of_delay"],
            ]
        )
    lines.append(markdown_table(["Case", "Bridge", "Estimator", "Configured Hz", "Axis map", "EKF2_OF_CTRL", "EKF2_OF_QMIN", "EKF2_OF_N_MIN", "EKF2_OF_DELAY"], rows))
    lines.append("")

    lines.append("## EKF Aid Source Rates And Fusion")
    rows = []
    for item in data:
        of = item.get("estimator_aid_src_optical_flow", {})
        flags = item.get("estimator_status_flags", {})
        rows.append(
            [
                item["short"],
                of.get("fused_count"),
                of.get("innovation_rejected_count"),
                fmt(of.get("fused_fraction")),
                fmt(flags.get("cs_opt_flow_fraction")),
                fmt(flags.get("cs_gnss_pos_fraction")),
                fmt(flags.get("cs_rng_hgt_fraction")),
                fmt(flags.get("cs_inertial_dead_reckoning_fraction")),
            ]
        )
    lines.append(markdown_table(["Case", "OF fused", "OF rejected", "OF fused frac", "cs_opt_flow", "cs_gnss_pos", "cs_rng_hgt", "dead reckoning"], rows))
    lines.append("")

    lines.append("## Sensor Publication Rates And Rangefinder Health")
    rows = []
    for item in data:
        sof = item.get("sensor_optical_flow", {})
        ds = item.get("distance_sensor", {})
        rows.append(
            [
                item["short"],
                fmt(sof.get("rate_hz")),
                fmt(sof.get("quality_mean")),
                fmt(sof.get("quality_zero_fraction")),
                fmt(ds.get("rate_hz")),
                fmt(ds.get("finite_fraction")),
                ds.get("nonfinite_count"),
                fmt(ds.get("current_distance_max")),
            ]
        )
    lines.append(markdown_table(["Case", "sensor_OF Hz", "Q mean", "Q zero frac", "distance Hz", "distance finite frac", "distance nonfinite", "distance max finite m"], rows))
    lines.append("")

    lines.append("## Route / Truth Performance")
    rows = []
    for item in data:
        m = item["metrics"]
        rows.append(
            [
                item["short"],
                fmt(m.get("aligned_duration_s")),
                fmt(m.get("truth_path_end_m")),
                fmt(m.get("horizontal_error_mean_m")),
                fmt(m.get("horizontal_error_max_m")),
                fmt(m.get("height_error_mean_m")),
                fmt(m.get("height_error_max_m")),
                fmt(m.get("error_3d_max_m")),
            ]
        )
    lines.append(markdown_table(["Case", "Aligned s", "Truth end m", "H err mean", "H err max", "Z err mean", "Z err max", "3D err max"], rows))
    lines.append("")

    lines.append("## Per-Case Notes")
    for item in data:
        lines.append("")
        lines.append(f"### {item['short']}")
        cfg = item["config"]
        lines.append(f"- Run: `{item['run_dir']}`")
        lines.append(f"- Manifest tags: algorithm=`{item['kind']}`, gnss_state=`{item['gnss_state']}`, world_variant=`{item['world_variant']}`" + (f", replicate_of=`{item['replicate_of']}`" if item.get("replicate_of") else ""))
        lines.append(f"- Command: `{item['commands'].get('entry_command')}`")
        g = item["gps_guard"]
        lines.append(f"- GPS guard: manifest=`{g['manifest_gnss_state']}`, observed=`{g['observed_gnss_state']}`" + (" **MISMATCH**" if g["mismatch"] else " (match)"))
        dashboard_path = out_dir / "plots" / "per_run" / f"{item['key']}_dashboard.png"
        if dashboard_path.exists():
            lines.append("")
            lines.append(f"![{item['short']} single-run dashboard](plots/per_run/{item['key']}_dashboard.png)")
        else:
            lines.append(
                f"- Single-run dashboard not generated yet (`plots/per_run/{item['key']}_dashboard.png` missing; "
                "run `plot_unified_comparison.py` after this report)."
            )

    lines.append("")
    lines.append("## Plots In This Comparison Folder")
    lines.append("")
    plot_titles = {
        "route_ekf_vs_gazebo_truth_overlay.png": "Route: Gazebo truth vs PX4 EKF (solid=GNSS-loss, dotted=GNSS-on)",
        "horizontal_error_vs_gazebo_truth.png": "Horizontal error vs Gazebo truth over time",
        "gnss_fix_satellites_over_time.png": "GNSS fix_type and satellites_used over time",
        "optical_flow_fusion_fraction_1s.png": "Optical-flow EKF aid fusion fraction (1 s bins)",
        "distance_sensor_current_distance.png": "Downward rangefinder current_distance stream",
        "summary_metric_bars.png": "Summary metric bars across all cases",
    }
    for name, title in plot_titles.items():
        plot_path = out_dir / "plots" / name
        if not plot_path.exists():
            lines.append(f"**{title}** -- not generated (`plots/{name}` missing; run `plot_unified_comparison.py` after this report).")
        else:
            lines.append(f"**{title}**")
            lines.append("")
            lines.append(f"![{title}](plots/{name})")
        lines.append("")

    lines.append("## Generated Evidence Files")
    lines.append("")
    lines.append("- `report.md`")
    lines.append("- `summary.json`")
    lines.append("- `summary.csv`")
    lines.append("- `camera_samples/<case_key>/*.jpg`")
    lines.append("- `plots/*.png` (cross-case comparison plots)")
    lines.append("- `plots/per_run/<case_key>_dashboard.png` (single-run detail, one per case)")
    return "\n".join(lines) + "\n"


def write_summary_csv(data: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "case",
        "algorithm",
        "gnss_state",
        "world_variant",
        "gps_guard_mismatch",
        "validation_accepted",
        "of_fused_fraction",
        "distance_finite_fraction",
        "horizontal_error_max_m",
        "height_error_max_m",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in data:
            writer.writerow(
                {
                    "case": item["short"],
                    "algorithm": item["kind"],
                    "gnss_state": item["gnss_state"],
                    "world_variant": item["world_variant"],
                    "gps_guard_mismatch": item["gps_guard"]["mismatch"],
                    "validation_accepted": item["status"].get("accepted"),
                    "of_fused_fraction": item.get("estimator_aid_src_optical_flow", {}).get("fused_fraction"),
                    "distance_finite_fraction": item.get("distance_sensor", {}).get("finite_fraction"),
                    "horizontal_error_max_m": item["metrics"].get("horizontal_error_max_m"),
                    "height_error_max_m": item["metrics"].get("height_error_max_m"),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Path to manifest.yaml")
    parser.add_argument("--out-dir", type=Path, default=None, help="Defaults to the manifest's own directory")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    out_dir = args.out_dir or args.manifest.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    data = []
    for case in manifest.cases:
        ulog_path = case.run_dir / "logs/flight.ulg"
        ulog_gz_path = case.run_dir / "logs/flight.ulg.gz"
        if not ulog_path.exists() and not ulog_gz_path.exists():
            raise FileNotFoundError(f"Case {case.key!r} missing required artifact: {ulog_path} (or .gz)")
        data.append(extract_case(case))

    copy_sample_frames(data, out_dir)

    (out_dir / "summary.json").write_text(json.dumps(data, indent=2, sort_keys=True))
    write_summary_csv(data, out_dir / "summary.csv")
    (out_dir / "report.md").write_text(build_report(manifest, data, out_dir))
    print(out_dir / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
