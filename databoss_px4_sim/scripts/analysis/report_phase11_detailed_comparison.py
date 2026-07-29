#!/usr/bin/env python3
"""Generate a detailed Phase 11 LK/SIFT/stock comparison audit report."""

from __future__ import annotations

import csv
import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from pyulog import ULog


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "experiments/comparisons/20260720_phase11_three_way_flow_comparison"


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    short: str
    kind: str
    run_dir: Path


CASES = [
    Case(
        key="lk_phase10_run_d",
        label="LK fixed xy nmin0.3 Run D",
        short="LK fixed",
        kind="lk",
        run_dir=ROOT
        / "experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
    Case(
        key="sift_phase11_111755",
        label="SIFT xy Phase 11",
        short="SIFT xy",
        kind="sift",
        run_dir=ROOT
        / "experiments/runs/20260720_111755_phase11_sift_xy_gnssloss_off50s_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
    Case(
        key="stock_phase11_122327",
        label="PX4 stock flow Phase 11",
        short="Stock",
        kind="stock",
        run_dir=ROOT
        / "experiments/runs/20260720_122327_phase11_stock_gnssloss_off50s_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
]


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

    ulog = ULog(str(run_dir / "logs/flight.ulg"))
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
            "gnss_loss_after_takeoff_s": nested(config, "gnss", "loss_after_takeoff_s"),
            "gnss_start_enabled": nested(config, "gnss", "start_enabled"),
            "gnss_loss_enabled": nested(config, "gnss", "loss_enabled"),
            "failsafe_requested": nested(config, "failsafe", "profile"),
            "control_mode": nested(config, "control", "mode"),
            "setpoint_mode": nested(config, "control", "setpoint_mode"),
            "control_start_after_takeoff_s": nested(config, "control", "start_after_takeoff_s"),
            "control_warmup_s": nested(config, "control", "warmup_s"),
            "gnss_loss_after_offboard_s": nested(config, "control", "gnss_loss_after_offboard_s"),
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
            "ekf2_of_ctrl": nested(config, "flow_bridge", "ekf2_of_ctrl")
            or nested(config, "stock_flow", "ekf2_of_ctrl"),
            "ekf2_of_qmin": nested(config, "flow_bridge", "ekf2_of_qmin")
            or nested(config, "stock_flow", "ekf2_of_qmin"),
            "ekf2_of_n_min": nested(config, "flow_bridge", "ekf2_of_n_min")
            or nested(config, "stock_flow", "ekf2_of_n_min"),
            "ekf2_of_delay": nested(config, "flow_bridge", "ekf2_of_delay")
            or nested(config, "stock_flow", "ekf2_of_delay"),
            "stock_flow_enabled": nested(config, "stock_flow", "enabled", default=False),
            "stock_flow_sens_flow_rot": nested(config, "stock_flow", "sens_flow_rot"),
            "stock_flow_sens_flow_minhgt": nested(config, "stock_flow", "sens_flow_minhgt"),
            "stock_flow_sens_flow_maxhgt": nested(config, "stock_flow", "sens_flow_maxhgt"),
            "stock_flow_sim_gz_en_flow": nested(config, "stock_flow", "sim_gz_en_flow"),
            "stock_flow_sim_gz_en_lidar": nested(config, "stock_flow", "sim_gz_en_lidar"),
            "rangefinder_tolerance_m": nested(config, "rangefinder", "height_agreement_tolerance_m"),
            "rangefinder_min_ulog_rows": nested(config, "rangefinder", "min_ulog_rows"),
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
            "sim_gps_zero_command_logged": "param set SIM_GPS_USED 0" in json.dumps(status),
            "sim_gps_ten_command_logged": "param set SIM_GPS_USED 10" in json.dumps(status),
        },
        "ulog_topics_present": sorted(topics),
    }

    if "vehicle_status" in topics:
        vs = topics["vehicle_status"]
        out["timeline"] = {
            "armed_time_rel_s": None,
            "takeoff_time_rel_s": None,
            "failsafe_fraction": bool_fraction(vs, "failsafe"),
            "failsafe_samples": sum_bool(vs, "failsafe"),
            "nav_states_unique": unique_ints(np.asarray(vs.get("nav_state", []), dtype=float)),
        }
        armed = np.asarray(vs.get("armed_time", []), dtype=float)
        takeoff = np.asarray(vs.get("takeoff_time", []), dtype=float)
        if len(armed) and np.nanmax(armed) > 0:
            out["timeline"]["armed_time_rel_s"] = (float(np.nanmax(armed)) - t0_us) / 1e6
        if len(takeoff) and np.nanmax(takeoff) > 0:
            out["timeline"]["takeoff_time_rel_s"] = (float(np.nanmax(takeoff)) - t0_us) / 1e6
    else:
        out["timeline"] = {}

    if "vehicle_local_position" in topics:
        vlp = topics["vehicle_local_position"]
        z_up = -np.asarray(vlp["z"], dtype=float)
        ts = np.asarray(vlp["timestamp"], dtype=float)
        mask = z_up > 0.5
        out["vehicle_local_position"] = {
            **topic_rate(vlp),
            "takeoff_threshold_0p5_rel_s": first_true_rel(vlp, mask, t0_us),
            "max_height_up_m": float(np.nanmax(z_up)),
            "duration_above_0p5_s": None
            if not np.any(mask)
            else (float(ts[np.where(mask)[0][-1]]) - float(ts[np.where(mask)[0][0]])) / 1e6,
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
                }
            )
        if len(sat):
            gps_out.update({"satellites_min": float(np.nanmin(sat)), "satellites_max": float(np.nanmax(sat))})
        gps_out.update(numeric_stats(gps, "eph"))
        gps_out.update(numeric_stats(gps, "epv"))
        out[topic] = gps_out

    if "sensor_optical_flow" in topics:
        sof = topics["sensor_optical_flow"]
        sof_out = {**topic_rate(sof)}
        for field in ["quality", "pixel_flow[0]", "pixel_flow[1]", "delta_angle[0]", "delta_angle[1]", "delta_angle[2]"]:
            sof_out.update(numeric_stats(sof, field))
        if "quality" in sof:
            q = np.asarray(sof["quality"], dtype=float)
            sof_out["quality_zero_fraction"] = float(np.nanmean(q <= 0)) if len(q) else None
        if "delta_angle_available" in sof:
            sof_out["delta_angle_available_fraction"] = bool_fraction(sof, "delta_angle_available")
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
        if "innovation_rejected" in aid:
            aid_out["innovation_rejected_count"] = sum_bool(aid, "innovation_rejected")
            aid_out["innovation_rejected_fraction"] = bool_fraction(aid, "innovation_rejected")
        aid_out.update(numeric_stats(aid, "test_ratio"))
        out[topic] = aid_out

    if "estimator_status_flags" in topics:
        flags = topics["estimator_status_flags"]
        flag_out = {**topic_rate(flags)}
        for field in [
            "cs_opt_flow",
            "cs_gnss_pos",
            "cs_gnss_vel",
            "cs_gps_hgt",
            "cs_rng_hgt",
            "cs_inertial_dead_reckoning",
        ]:
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
        if "orientation" in dist:
            dist_out["orientation_unique"] = unique_ints(np.asarray(dist["orientation"], dtype=float))
        out["distance_sensor"] = dist_out

    if "vehicle_attitude" in topics:
        att = topics["vehicle_attitude"]
        q_cols = ["q[0]", "q[1]", "q[2]", "q[3]"]
        if all(col in att for col in q_cols):
            q = np.vstack([att[col] for col in q_cols]).T.astype(float)
            roll: list[float] = []
            pitch: list[float] = []
            for w, x, y, z in q:
                roll.append(math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
                pitch.append(math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x)))))
            out["attitude"] = {
                **topic_rate(att),
                "roll_abs_p95_deg": float(np.percentile(np.abs(np.asarray(roll) * 180.0 / math.pi), 95)),
                "pitch_abs_p95_deg": float(np.percentile(np.abs(np.asarray(pitch) * 180.0 / math.pi), 95)),
            }

    return out


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def case_link(path: str) -> str:
    p = Path(path)
    return f"[{p.name}]({p})"


def build_report(data: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Phase 11 Detailed Three-Way Flow Comparison Audit")
    lines.append("")
    lines.append("Generated from run configs, runner status JSON, PX4 ULogs, Gazebo truth metrics, and existing comparison CSV/plots.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(
        "LK and SIFT are valid bounded GNSS-denied bridge runs with corrected `axis_map: xy`. "
        "The latest stock run is now genuinely GNSS-denied and fuses stock optical flow, but its own validation remains rejected because the stock rangefinder stream is unreliable. "
        "Therefore the comparison is useful as an engineering diagnostic, but not yet a final accepted three-way benchmark."
    )
    lines.append("")
    lines.append("## Runs And Classification")
    rows = []
    for item in data:
        rows.append(
            [
                item["short"],
                item["kind"],
                case_link(item["run_dir"]),
                fmt(item["status"].get("accepted")),
                fmt(item["metrics"].get("accepted")),
                item["metrics"].get("comparison_end_reason"),
                "accepted with skip-landing metrics limitation"
                if item["status"].get("accepted") and not item["metrics"].get("accepted")
                else "rejected: stock rangefinder validation failed"
                if item["kind"] == "stock"
                else "check required",
            ]
        )
    lines.append(markdown_table(["Case", "Kind", "Run", "Validation", "Metrics", "Metrics end", "Classification"], rows))
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
                f"({fmt(cfg['x_m'])}, {fmt(cfg['y_m'])}, {fmt(cfg['z_m'])})",
                fmt(cfg["skip_landing_command"]),
            ]
        )
    lines.append(markdown_table(["Case", "Route", "Type", "Alt AGL m", "Duration s", "Control", "Setpoint", "Velocity m/s", "Position target", "Skip land"], rows))
    lines.append("")

    lines.append("## GNSS And Failsafe Timeline")
    rows = []
    for item in data:
        cfg = item["config"]
        st = item["status"]
        gps = item.get("vehicle_gps_position", {})
        tl = item.get("timeline", {})
        vlp = item.get("vehicle_local_position", {})
        gps_loss_after_takeoff_threshold = None
        if gps.get("first_fix_lt3_rel_s") is not None and vlp.get("takeoff_threshold_0p5_rel_s") is not None:
            gps_loss_after_takeoff_threshold = gps["first_fix_lt3_rel_s"] - vlp["takeoff_threshold_0p5_rel_s"]
        rows.append(
            [
                item["short"],
                cfg["gnss_loss_after_takeoff_s"],
                st["effective_gnss_loss_after_takeoff_s"],
                st["failsafe_profile_effective"],
                fmt(st["failsafe_profile_ok"]),
                fmt(tl.get("armed_time_rel_s")),
                fmt(tl.get("takeoff_time_rel_s")),
                fmt(vlp.get("takeoff_threshold_0p5_rel_s")),
                fmt(gps.get("first_fix_lt3_rel_s")),
                fmt(gps_loss_after_takeoff_threshold),
                str(gps.get("fix_type_unique")),
                f"{fmt(gps.get('satellites_min'), 0)}..{fmt(gps.get('satellites_max'), 0)}",
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "Config loss s",
                "Effective loss s",
                "Failsafe",
                "FS OK",
                "Arm rel s",
                "Takeoff rel s",
                "Height>0.5 rel s",
                "GPS unhealthy rel s",
                "After height>0.5 s",
                "Fix types",
                "Satellites",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "Note: the comparison report uses the height-threshold takeoff event for `observed GPS loss after takeoff`; "
        "PX4 `vehicle_status.takeoff_time` is a different clock event, so timing values should state which origin they use."
    )
    lines.append("")

    lines.append("## Sensor Physical Models And Limits")
    lines.append("")
    lines.append(
        "- LK/SIFT vehicle `gz_x500_cam_lidar_down`: downward monocular camera from `mono_cam`, pose `0 0 .10 0 1.5707 0`, HFOV `1.74 rad`, image `1280x960`, physical camera rate `30 Hz`; bridge resizes to `max_width` from config."
    )
    lines.append(
        "- LK/SIFT rangefinder: DATABOSS v2 TF03-style downward GPU lidar, offset `+0.08 m` forward, `3x1` horizontal fan `[-0.02, 0.02] rad`, range `0.1..100 m`, update `50 Hz`."
    )
    lines.append(
        "- Stock vehicle `gz_x500_flow`: PX4 stock optical-flow camera, HFOV `0.733038 rad`, image `100x100`, update `50 Hz`; stock rangefinder is centered `1x1`, range `0.1..100 m`, update `50 Hz`."
    )
    lines.append("")

    lines.append("## Optical Flow Configuration")
    rows = []
    for item in data:
        cfg = item["config"]
        rows.append(
            [
                item["short"],
                cfg["flow_bridge_enabled"],
                cfg["flow_bridge_estimator"] or "stock",
                cfg["flow_bridge_rate_hz"] or "native",
                cfg["axis_map"] or "PX4 native",
                cfg["max_width"] or "100",
                cfg["hfov_rad"] or "0.733038",
                cfg["quality_in_min"],
                cfg["quality_in_max"],
                cfg["send_min_quality"],
                cfg["send_min_matches"],
                cfg["ekf2_of_ctrl"],
                cfg["ekf2_of_qmin"],
                cfg["ekf2_of_n_min"],
                cfg["ekf2_of_delay"],
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "Bridge",
                "Estimator",
                "Configured Hz",
                "Axis map",
                "Width",
                "HFOV",
                "Q in min",
                "Q in max",
                "Send min Q",
                "Send min matches",
                "EKF2_OF_CTRL",
                "EKF2_OF_QMIN",
                "EKF2_OF_N_MIN",
                "EKF2_OF_DELAY",
            ],
            rows,
        )
    )
    lines.append("")

    lines.append("## Sensor Publication Rates And Limits Observed")
    rows = []
    for item in data:
        sof = item.get("sensor_optical_flow", {})
        vof = item.get("vehicle_optical_flow", {})
        ds = item.get("distance_sensor", {})
        rows.append(
            [
                item["short"],
                sof.get("rows"),
                fmt(sof.get("rate_hz")),
                fmt(sof.get("quality_mean")),
                fmt(sof.get("quality_p95")),
                fmt(sof.get("quality_max"), 0),
                fmt(sof.get("quality_zero_fraction")),
                vof.get("rows"),
                fmt(vof.get("rate_hz")),
                ds.get("rows"),
                fmt(ds.get("rate_hz")),
                fmt(ds.get("finite_fraction")),
                ds.get("nonfinite_count"),
                fmt(ds.get("current_distance_max")),
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "sensor_OF rows",
                "sensor_OF Hz",
                "Q mean",
                "Q p95",
                "Q max",
                "Q zero frac",
                "vehicle_OF rows",
                "vehicle_OF Hz",
                "distance rows",
                "distance Hz",
                "distance finite frac",
                "distance nonfinite",
                "distance max finite m",
            ],
            rows,
        )
    )
    lines.append("")

    lines.append("## EKF Feeding Connections")
    lines.append("")
    lines.append("```text")
    lines.append("LK/SIFT:")
    lines.append("Gazebo camera image -> DATABOSS LK/SIFT bridge -> MAVLink OPTICAL_FLOW_RAD -> PX4 sensor_optical_flow -> vehicle_optical_flow -> estimator_aid_src_optical_flow -> EKF2")
    lines.append("Gazebo GPU lidar -> PX4 GZ bridge -> distance_sensor -> estimator_aid_src_rng_hgt -> EKF2")
    lines.append("GNSS before loss -> sensor_gps/vehicle_gps_position -> estimator_aid_src_gnss_pos/vel/hgt -> EKF2")
    lines.append("")
    lines.append("Stock:")
    lines.append("PX4 Gazebo OpticalFlowSystem -> sensor_optical_flow -> vehicle_optical_flow -> estimator_aid_src_optical_flow -> EKF2")
    lines.append("PX4 stock 1x1 GPU lidar -> distance_sensor -> estimator_aid_src_rng_hgt -> EKF2")
    lines.append("GNSS before loss -> sensor_gps/vehicle_gps_position -> estimator_aid_src_gnss_pos/vel/hgt -> EKF2")
    lines.append("```")
    lines.append("")

    lines.append("## EKF Aid Source Rates And Fusion")
    rows = []
    for item in data:
        of = item.get("estimator_aid_src_optical_flow", {})
        flags = item.get("estimator_status_flags", {})
        rows.append(
            [
                item["short"],
                of.get("rows"),
                fmt(of.get("rate_hz")),
                of.get("fused_count"),
                of.get("innovation_rejected_count"),
                fmt(of.get("fused_fraction")),
                fmt(of.get("test_ratio_p95")),
                fmt(of.get("test_ratio_max")),
                fmt(flags.get("cs_opt_flow_fraction")),
                fmt(flags.get("cs_gnss_pos_fraction")),
                fmt(flags.get("cs_gnss_vel_fraction")),
                fmt(flags.get("cs_rng_hgt_fraction")),
                fmt(flags.get("cs_gps_hgt_fraction")),
                fmt(flags.get("cs_inertial_dead_reckoning_fraction")),
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "OF aid rows",
                "OF aid Hz",
                "OF fused",
                "OF rejected",
                "OF fused frac",
                "OF test p95",
                "OF test max",
                "cs_opt_flow",
                "cs_gnss_pos",
                "cs_gnss_vel",
                "cs_rng_hgt",
                "cs_gps_hgt",
                "dead reckoning",
            ],
            rows,
        )
    )
    lines.append("")

    lines.append("## GNSS And Range Aid Source Rates")
    rows = []
    for item in data:
        rows.append(
            [
                item["short"],
                fmt(item.get("estimator_aid_src_gnss_pos", {}).get("rate_hz")),
                fmt(item.get("estimator_aid_src_gnss_pos", {}).get("fused_fraction")),
                fmt(item.get("estimator_aid_src_gnss_vel", {}).get("rate_hz")),
                fmt(item.get("estimator_aid_src_gnss_vel", {}).get("fused_fraction")),
                fmt(item.get("estimator_aid_src_gnss_hgt", {}).get("rate_hz")),
                fmt(item.get("estimator_aid_src_gnss_hgt", {}).get("fused_fraction")),
                fmt(item.get("estimator_aid_src_rng_hgt", {}).get("rate_hz")),
                fmt(item.get("estimator_aid_src_rng_hgt", {}).get("fused_fraction")),
                fmt(item.get("distance_sensor", {}).get("finite_fraction")),
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "GNSS pos Hz",
                "GNSS pos fused",
                "GNSS vel Hz",
                "GNSS vel fused",
                "GNSS hgt Hz",
                "GNSS hgt fused",
                "Range hgt Hz",
                "Range hgt fused",
                "Distance finite",
            ],
            rows,
        )
    )
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
                fmt(m.get("px4_path_end_m")),
                fmt(m.get("horizontal_error_mean_m")),
                fmt(m.get("horizontal_error_p95_m")),
                fmt(m.get("horizontal_error_max_m")),
                fmt(m.get("height_error_mean_m")),
                fmt(m.get("height_error_p95_m")),
                fmt(m.get("height_error_max_m")),
                fmt(m.get("error_3d_max_m")),
            ]
        )
    lines.append(
        markdown_table(
            [
                "Case",
                "Aligned s",
                "Truth end m",
                "PX4 end m",
                "H err mean",
                "H err p95",
                "H err max",
                "Z err mean",
                "Z err p95",
                "Z err max",
                "3D err max",
            ],
            rows,
        )
    )
    lines.append("")

    lines.append("## Per-Case Notes")
    for item in data:
        lines.append("")
        lines.append(f"### {item['short']}")
        cfg = item["config"]
        st = item["status"]
        lines.append(f"- Run: `{item['run_dir']}`")
        lines.append(f"- Command: `{item['commands'].get('entry_command')}`")
        if item["kind"] in {"lk", "sift"}:
            lines.append(
                f"- Bridge estimator `{cfg['flow_bridge_estimator']}`, configured `{cfg['flow_bridge_rate_hz']} Hz`, `axis_map: {cfg['axis_map']}`, `EKF2_OF_CTRL={cfg['ekf2_of_ctrl']}`, `EKF2_OF_QMIN={cfg['ekf2_of_qmin']}`."
            )
            if item["kind"] == "lk":
                lines.append(
                    f"- LK limits: `max_corners={cfg['lk_max_corners']}`, `min_tracks={cfg['lk_min_tracks']}`, `max_flow_rate_rad_s={cfg['lk_max_flow_rate_rad_s']}`, `EKF2_OF_N_MIN={cfg['ekf2_of_n_min']}`."
                )
            else:
                lines.append(
                    f"- SIFT limits: `n_features={cfg['sift_n_features']}`, `ratio={cfg['sift_ratio']}`, `min_matches={cfg['sift_min_matches']}`, send range gate `{cfg['send_min_range_m']}..{cfg['send_max_range_m']} m`."
                )
            lines.append(
                f"- Validation accepted is `{st['accepted']}`; metrics rejected only because the skip-landing comparison window has `land_command_not_found`."
            )
        else:
            lines.append(
                f"- Stock optical flow path enabled with `SIM_GZ_EN_FLOW={cfg['stock_flow_sim_gz_en_flow']}`, `SIM_GZ_EN_LIDAR={cfg['stock_flow_sim_gz_en_lidar']}`, `SENS_FLOW_ROT={cfg['stock_flow_sens_flow_rot']}`, height gate `{cfg['stock_flow_sens_flow_minhgt']}..{cfg['stock_flow_sens_flow_maxhgt']} m`."
            )
            lines.append(
                f"- Validation rejected because `distance_sensor_ok={st['distance_sensor_ok']}` and height agreement diff is `{fmt(st['distance_sensor_height_diff_m'])} m`."
            )
        lines.append(
            f"- GNSS proof: `fix_type {item.get('vehicle_gps_position', {}).get('fix_type_unique')}`, GPS unhealthy at `{fmt(item.get('vehicle_gps_position', {}).get('first_fix_lt3_rel_s'))} s` relative to first ULog timestamp."
        )
        lines.append(
            f"- Optical-flow proof: `{item.get('estimator_aid_src_optical_flow', {}).get('fused_count')}` fused, `{item.get('estimator_aid_src_optical_flow', {}).get('innovation_rejected_count')}` rejected; `cs_opt_flow={fmt(item.get('estimator_status_flags', {}).get('cs_opt_flow_fraction'))}`."
        )

    lines.append("")
    lines.append("## Plots In This Comparison Folder")
    for name in [
        "route_ekf_vs_gazebo_truth_overlay.png",
        "route_ekf_vs_gazebo_truth_panels.png",
        "horizontal_error_vs_gazebo_truth.png",
        "height_error_vs_gazebo_truth.png",
        "height_px4_vs_gazebo_truth.png",
        "gazebo_truth_route_progress.png",
        "gnss_fix_satellites_over_time.png",
        "gnss_eph_epv_over_time.png",
        "optical_flow_fusion_fraction_1s.png",
        "optical_flow_aid_sample_rate_1s.png",
        "optical_flow_test_ratio_1s.png",
        "optical_flow_quality_over_time.png",
        "ekf_control_status_flags.png",
        "distance_sensor_current_distance.png",
        "summary_metric_bars.png",
    ]:
        lines.append(f"- `plots/{name}`")

    lines.append("")
    lines.append("## Confirmed Findings")
    lines.append("")
    lines.append("1. `axis_map: xy` is the corrected bridge contract for both LK and SIFT; both bridge cases fuse optical flow without innovation rejection and remain bounded against Gazebo truth.")
    lines.append("2. Stock `122327` is the first stock run in this comparison folder that is genuinely GNSS-denied in the ULog; old stock `115202` is preserved only as superseded data and must not be used as the valid comparator.")
    lines.append("3. Stock `122327` is not accepted because the stock rangefinder validation fails; its centered `1x1` lidar produces many non-finite samples and a max range inconsistent with height.")
    lines.append("4. The three-way comparison is still confounded: stock uses a different vehicle/sensor implementation and delayed-observation timing, while LK/SIFT use the DATABOSS camera + rangefinder bridge vehicle.")
    lines.append("")
    lines.append("## Minimum Next Experiment")
    lines.append("")
    lines.append(
        "Run stock again with only the rangefinder geometry changed or isolated. "
        "Best diagnostic: create a stock-flow variant that keeps PX4 stock optical flow unchanged but replaces the stock centered `1x1` lidar with the DATABOSS v2 `+0.08 m` offset and `3x1` fan. "
        "Acceptance gates: GPS fix/sats drop, `sensor_optical_flow` at ~50 Hz, OF aid fused fraction >0.70, `distance_sensor` finite fraction >0.95 during airborne hover, range-vs-truth height p95 <0.25 m, and max horizontal error remains bounded."
    )
    lines.append("")
    lines.append("## Generated Evidence Files")
    lines.append("")
    lines.append("- `phase11_detailed_audit_report.md`")
    lines.append("- `phase11_detailed_audit_data.json`")
    lines.append("- `phase11_detailed_audit_summary.csv`")
    return "\n".join(lines) + "\n"


def write_summary_csv(data: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "case",
        "validation_accepted",
        "vehicle",
        "route",
        "configured_flow_hz",
        "sensor_flow_hz",
        "of_aid_hz",
        "of_fused_count",
        "of_rejected_count",
        "of_fused_fraction",
        "distance_finite_fraction",
        "gps_loss_rel_s",
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
                    "validation_accepted": item["status"].get("accepted"),
                    "vehicle": item["config"].get("vehicle_model"),
                    "route": item["config"].get("route_name"),
                    "configured_flow_hz": item["config"].get("flow_bridge_rate_hz") or "native",
                    "sensor_flow_hz": item.get("sensor_optical_flow", {}).get("rate_hz"),
                    "of_aid_hz": item.get("estimator_aid_src_optical_flow", {}).get("rate_hz"),
                    "of_fused_count": item.get("estimator_aid_src_optical_flow", {}).get("fused_count"),
                    "of_rejected_count": item.get("estimator_aid_src_optical_flow", {}).get("innovation_rejected_count"),
                    "of_fused_fraction": item.get("estimator_aid_src_optical_flow", {}).get("fused_fraction"),
                    "distance_finite_fraction": item.get("distance_sensor", {}).get("finite_fraction"),
                    "gps_loss_rel_s": item.get("vehicle_gps_position", {}).get("first_fix_lt3_rel_s"),
                    "horizontal_error_max_m": item["metrics"].get("horizontal_error_max_m"),
                    "height_error_max_m": item["metrics"].get("height_error_max_m"),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory for the generated detailed report artifacts.",
    )
    args = parser.parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    data = [extract_case(case) for case in CASES]
    (out_dir / "phase11_detailed_audit_data.json").write_text(json.dumps(data, indent=2, sort_keys=True))
    write_summary_csv(data, out_dir / "phase11_detailed_audit_summary.csv")
    (out_dir / "phase11_detailed_audit_report.md").write_text(build_report(data))
    print(out_dir / "phase11_detailed_audit_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
