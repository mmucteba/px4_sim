#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics as stats
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - reported in output
    cv2 = None
    np = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def finite_float(value) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def rate_stats(times: list[float]) -> dict:
    times = [t for t in times if math.isfinite(t)]
    if len(times) < 2:
        return {"rows": len(times), "duration_s": None, "rate_hz": None, "median_dt_ms": None}
    times = sorted(times)
    dts = [b - a for a, b in zip(times, times[1:]) if b > a]
    duration = times[-1] - times[0]
    return {
        "rows": len(times),
        "duration_s": duration,
        "rate_hz": len(times) / duration if duration > 0 else None,
        "median_dt_ms": stats.median(dts) * 1000.0 if dts else None,
    }


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def summarize_frames(run_dir: Path, max_frames: int = 24, window_sim_s: tuple[float, float] | None = None) -> dict:
    frames_dir = run_dir / "flow_recording"
    index_path = frames_dir / "frames_index.csv"
    all_rows = read_csv_rows(index_path)
    rows = all_rows
    if window_sim_s is not None:
        t_min, t_max = window_sim_s
        rows = [
            r for r in all_rows
            if (t := finite_float(r.get("t_sim_s"))) is not None and t_min <= t <= t_max
        ]
    times = [finite_float(r.get("t_sim_s")) for r in rows]
    out = {
        "available": bool(rows),
        "all_rows": len(all_rows),
        "window_sim_s": list(window_sim_s) if window_sim_s is not None else None,
        **rate_stats([t for t in times if t is not None]),
    }
    if not rows:
        return out
    if cv2 is None or np is None:
        out["image_analysis_available"] = False
        out["image_analysis_error"] = "cv2/numpy unavailable"
        return out

    if len(rows) <= max_frames:
        sample_rows = rows
    else:
        idxs = np.linspace(0, len(rows) - 1, max_frames).round().astype(int)
        sample_rows = [rows[int(i)] for i in idxs]

    means = []
    stds = []
    lap_vars = []
    feature_counts = []
    textured_cell_fracs = []
    sky_like_fracs = []

    for r in sample_rows:
        frame_path = frames_dir / "frames" / str(r.get("frame_path", ""))
        img = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        means.append(float(img.mean()))
        stds.append(float(img.std()))
        lap_vars.append(float(cv2.Laplacian(img, cv2.CV_64F).var()))
        corners = cv2.goodFeaturesToTrack(
            img,
            maxCorners=600,
            qualityLevel=0.01,
            minDistance=6,
        )
        feature_counts.append(0 if corners is None else int(len(corners)))

        h, w = img.shape[:2]
        grid = []
        for y0 in np.linspace(0, h, 9, dtype=int)[:-1]:
            y1 = min(h, y0 + max(1, h // 8))
            for x0 in np.linspace(0, w, 9, dtype=int)[:-1]:
                x1 = min(w, x0 + max(1, w // 8))
                grid.append(float(img[y0:y1, x0:x1].std()))
        textured_cell_fracs.append(sum(v > 5.0 for v in grid) / max(len(grid), 1))
        sky_like_fracs.append(float(((img > 145) & (img < 235)).mean()))

    if not means:
        out["image_analysis_available"] = False
        out["image_analysis_error"] = "no readable sampled frames"
        return out

    out.update({
        "image_analysis_available": True,
        "sampled_frames": len(means),
        "brightness_mean": stats.mean(means),
        "contrast_std_mean": stats.mean(stds),
        "laplacian_var_mean": stats.mean(lap_vars),
        "feature_count_median": stats.median(feature_counts),
        "feature_count_min": min(feature_counts),
        "textured_cell_fraction_mean": stats.mean(textured_cell_fracs),
        "sky_like_fraction_mean": stats.mean(sky_like_fracs),
    })
    return out


def infer_hover_window_from_range(run_dir: Path) -> dict:
    rows = read_csv_rows(run_dir / "flow_recording" / "rangefinder.csv")
    timed_values = []
    for r in rows:
        t = finite_float(r.get("t_sim_s"))
        v = finite_float(r.get("range_m"))
        if t is not None and v is not None and v > 0:
            timed_values.append((t, v))
    if len(timed_values) < 20:
        return {"available": False, "reason": "insufficient finite range rows"}

    airborne = [v for _, v in timed_values if v >= 1.0]
    if len(airborne) < 20:
        return {"available": False, "reason": "insufficient airborne range rows"}
    hover_median = stats.median(airborne)
    tol = max(0.35, hover_median * 0.15)
    selected = [
        (t, v) for t, v in timed_values
        if v >= max(1.0, hover_median * 0.75) and abs(v - hover_median) <= tol
    ]
    if len(selected) < 20:
        return {"available": False, "reason": "no stable hover range window", "hover_median_m": hover_median}

    t_min = selected[0][0]
    t_max = selected[-1][0]
    if t_max - t_min > 4.0:
        t_min += 1.0
        t_max -= 1.0
    return {
        "available": True,
        "window_sim_s": [t_min, t_max],
        "hover_median_m": hover_median,
        "tolerance_m": tol,
        "selected_rows": len(selected),
        "total_finite_positive_rows": len(timed_values),
    }


def summarize_ranges(run_dir: Path, window_sim_s: tuple[float, float] | None = None) -> dict:
    rows = read_csv_rows(run_dir / "flow_recording" / "rangefinder.csv")
    considered_rows = 0
    values = []
    for r in rows:
        if window_sim_s is not None:
            t = finite_float(r.get("t_sim_s"))
            if t is None or not (window_sim_s[0] <= t <= window_sim_s[1]):
                continue
        considered_rows += 1
        v = finite_float(r.get("range_m"))
        if v is not None and v > 0:
            values.append(v)
    out = {
        "available": bool(rows),
        "rows": len(rows),
        "considered_rows": considered_rows,
        "finite_positive_rows": len(values),
        "window_sim_s": list(window_sim_s) if window_sim_s is not None else None,
        "finite_positive_fraction": len(values) / considered_rows if considered_rows else None,
    }
    if values:
        out.update({
            "median_m": stats.median(values),
            "mean_m": stats.mean(values),
            "min_m": min(values),
            "max_m": max(values),
        })
    return out


def summarize_bridge(run_dir: Path) -> dict:
    rows = read_csv_rows(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
    if not rows:
        return {"available": False}
    sent = [r for r in rows if str(r.get("sent")) == "1"]
    mav = [r for r in rows if str(r.get("mavlink_sent")) == "1"]
    real = [r for r in rows if r.get("send_reason") != "startup_prime"]
    times = [finite_float(r.get("t_frame_sim_s")) for r in real]
    qualities = [finite_float(r.get("quality_raw")) for r in real]
    qualities = [q for q in qualities if q is not None]
    matches = [finite_float(r.get("n_matches")) for r in real]
    matches = [m for m in matches if m is not None]
    out = {
        "available": True,
        "rows": len(rows),
        "real_rows": len(real),
        "logical_sent_rows": len(sent),
        "mavlink_sent_rows": len(mav),
        **{f"real_{k}": v for k, v in rate_stats([t for t in times if t is not None]).items()},
    }
    if qualities:
        out.update({
            "quality_raw_mean": stats.mean(qualities),
            "quality_raw_median": stats.median(qualities),
            "quality_raw_zero_fraction": sum(q == 0 for q in qualities) / len(qualities),
        })
    if matches:
        out.update({
            "matches_mean": stats.mean(matches),
            "matches_median": stats.median(matches),
        })
    return out


def summarize_axis_contract(run_dir: Path, config: dict) -> dict:
    control = config.get("control", {}) if isinstance(config, dict) else {}
    vx_cmd = float(control.get("vx_m_s", 0.0) or 0.0)
    vy_cmd = float(control.get("vy_m_s", 0.0) or 0.0)
    axis = None
    sign = 0
    if abs(vx_cmd) >= abs(vy_cmd) and abs(vx_cmd) > 1e-6:
        axis = "vx"
        sign = 1 if vx_cmd > 0 else -1
    elif abs(vy_cmd) > 1e-6:
        axis = "vy"
        sign = 1 if vy_cmd > 0 else -1
    else:
        return {"available": False, "reason": "no commanded horizontal velocity"}

    truth_files = sorted((run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
    sent_rows = [
        r for r in read_csv_rows(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
        if str(r.get("sent")) == "1" and (finite_float(r.get("quality_sent")) or 0.0) > 0
    ]
    if not truth_files or not sent_rows:
        return {"available": False, "reason": "missing truth CSV or informative sent bridge rows"}

    truth = read_csv_rows(truth_files[0])
    tt = [finite_float(r.get("sim_time_s")) for r in truth]
    gx = [finite_float(r.get("x")) for r in truth]
    gy = [finite_float(r.get("y")) for r in truth]
    good = [(t, x, y) for t, x, y in zip(tt, gx, gy) if t is not None and x is not None and y is not None]
    if len(good) < 20:
        return {"available": False, "reason": "insufficient truth rows"}
    tt = [g[0] for g in good]
    gx = [g[1] for g in good]
    gy = [g[2] for g in good]

    # ENU -> PX4 NED horizontal: vx_ned = d(ENU_y)/dt, vy_ned = d(ENU_x)/dt.
    velocities = []
    for i in range(1, len(tt) - 1):
        dt = tt[i + 1] - tt[i - 1]
        if dt <= 0:
            continue
        vx = (gy[i + 1] - gy[i - 1]) / dt
        vy = (gx[i + 1] - gx[i - 1]) / dt
        velocities.append((tt[i], vx, vy))
    cmd_speed = abs(vx_cmd if axis == "vx" else vy_cmd)
    threshold = max(0.05, min(0.2, cmd_speed * 0.5))
    moving = [(t, vx, vy) for t, vx, vy in velocities if abs(vx if axis == "vx" else vy) >= threshold]
    if len(moving) < 20:
        return {"available": False, "reason": "no steady truth motion window"}
    t_lo = moving[0][0] + 2.5
    t_hi = moving[-1][0] - 2.5
    if t_hi <= t_lo:
        return {"available": False, "reason": "steady truth window too short"}

    leg_rows = []
    for r in sent_rows:
        t = finite_float(r.get("t_frame_sim_s"))
        if t is not None and t_lo <= t <= t_hi:
            leg_rows.append(r)
    if len(leg_rows) < 10:
        return {"available": False, "reason": "no bridge rows in steady truth window"}

    ix_rates = []
    iy_rates = []
    ranges = []
    for r in leg_rows:
        dt = finite_float(r.get("integration_dt_s"))
        ix = finite_float(r.get("integrated_x_sent"))
        iy = finite_float(r.get("integrated_y_sent"))
        rng = finite_float(r.get("range_m"))
        if dt and dt > 0 and ix is not None and iy is not None:
            ix_rates.append(ix / dt)
            iy_rates.append(iy / dt)
        if rng is not None and rng > 0:
            ranges.append(rng)
    if not ix_rates or not iy_rates:
        return {"available": False, "reason": "no finite bridge rates"}

    finite_fraction = len(ranges) / len(leg_rows)
    agl = stats.median(ranges) if ranges else None
    truth_axis_values = [vx if axis == "vx" else vy for _, vx, vy in moving if t_lo <= _ <= t_hi]
    truth_axis_speed = abs(stats.mean(truth_axis_values)) if truth_axis_values else cmd_speed

    ix_mean = stats.mean(ix_rates)
    iy_mean = stats.mean(iy_rates)
    if axis == "vx":
        dominant = iy_mean
        cross = ix_mean
        # Wire convention: +body-X motion -> positive integrated_y.
        sign_ok = dominant * sign > 0
    else:
        dominant = ix_mean
        cross = iy_mean
        # Wire convention: +body-Y motion -> negative integrated_x.
        sign_ok = dominant * sign < 0
    expected_rate = truth_axis_speed / agl if agl and agl > 0 else None
    magnitude_ratio = abs(dominant) / expected_rate if expected_rate else None

    return {
        "available": True,
        "contract": "mavlink_wire_pixel_flow_pre_ekf2_negation",
        "final_sign_authority": "scripts/analysis/check_flow_velocity_sign.py",
        "command_axis": axis,
        "command_sign": sign,
        "window_sim_s": [t_lo, t_hi],
        "samples": len(leg_rows),
        "integrated_x_rate_mean": ix_mean,
        "integrated_y_rate_mean": iy_mean,
        "dominant_axis_rate": dominant,
        "cross_axis_rate": cross,
        "axis_dominance_ok": abs(dominant) > 2.0 * abs(cross),
        "sign_ok": bool(sign_ok),
        "range_finite_fraction": finite_fraction,
        "agl_median_m": agl,
        "truth_axis_speed_m_s": truth_axis_speed,
        "expected_flow_rate_rad_s": expected_rate,
        "magnitude_ratio": magnitude_ratio,
        "magnitude_ok": magnitude_ratio is not None and 0.7 <= magnitude_ratio <= 1.3,
    }


def summarize_rotation_contract(run_dir: Path, config: dict) -> dict:
    control = config.get("control", {}) if isinstance(config, dict) else {}
    flow_cfg = config.get("flow_bridge", {}) if isinstance(config, dict) else {}
    rows = [
        r for r in read_csv_rows(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
        if r.get("send_reason") != "startup_prime"
        and str(r.get("mavlink_sent")) == "1"
        and (finite_float(r.get("quality_sent")) or 0.0) > 0
    ]
    if not rows:
        return {"available": False, "reason": "missing informative flow bridge rows"}

    ix_rates = []
    iy_rates = []
    gx_rates = []
    gy_rates = []
    gz_rates = []
    ranges = []
    gyro_available = 0
    for r in rows:
        dt = finite_float(r.get("integration_dt_s"))
        if not dt or dt <= 0:
            continue
        ix = finite_float(r.get("integrated_x_sent"))
        iy = finite_float(r.get("integrated_y_sent"))
        gx = finite_float(r.get("integrated_xgyro_sent"))
        gy = finite_float(r.get("integrated_ygyro_sent"))
        gz = finite_float(r.get("integrated_zgyro_sent"))
        rng = finite_float(r.get("range_m"))
        if ix is not None:
            ix_rates.append(ix / dt)
        if iy is not None:
            iy_rates.append(iy / dt)
        if gx is not None and gy is not None and gz is not None:
            gyro_available += 1
            gx_rates.append(gx / dt)
            gy_rates.append(gy / dt)
            gz_rates.append(gz / dt)
        if rng is not None and rng > 0:
            ranges.append(rng)

    def series_stats(values: list[float]) -> dict:
        if not values:
            return {"mean": None, "median": None, "abs_mean": None, "abs_max": None}
        return {
            "mean": stats.mean(values),
            "median": stats.median(values),
            "abs_mean": stats.mean(abs(v) for v in values),
            "abs_max": max(abs(v) for v in values),
        }

    gyro_mode = str(flow_cfg.get("gyro_mode", "nan"))
    return {
        "available": True,
        "gyro_mode": gyro_mode,
        "use_yaw": bool(control.get("use_yaw", False)),
        "yaw_deg": finite_float(control.get("yaw_deg")),
        "vx_m_s": finite_float(control.get("vx_m_s")),
        "vy_m_s": finite_float(control.get("vy_m_s")),
        "rows": len(rows),
        "gyro_available_rows": gyro_available,
        "gyro_available_fraction": gyro_available / len(rows) if rows else 0.0,
        "range_finite_fraction": len(ranges) / len(rows) if rows else 0.0,
        "range_median_m": stats.median(ranges) if ranges else None,
        "flow_x_rate_rad_s": series_stats(ix_rates),
        "flow_y_rate_rad_s": series_stats(iy_rates),
        "gyro_x_rate_rad_s": series_stats(gx_rates),
        "gyro_y_rate_rad_s": series_stats(gy_rates),
        "gyro_z_rate_rad_s": series_stats(gz_rates),
    }


def static_audit() -> dict:
    databoss_model = PROJECT_ROOT / "src/databoss_sim/models/x500_cam_lidar_down/model.sdf"
    deployed_model = PX4_ROOT / "Tools/simulation/gz/models/x500_cam_lidar_down/model.sdf"
    stock_model = PX4_ROOT / "Tools/simulation/gz/models/x500_flow/model.sdf"
    bridge_adapter = PROJECT_ROOT / "src/databoss_sim/flow/px4_adapter.py"
    world_yaml = PROJECT_ROOT / "experiments/configs/mvp/worlds/flat_rural_phototex_noon.yaml"

    def poses(path: Path) -> list[dict]:
        if not path.exists():
            return []
        root = ET.fromstring(path.read_text())
        out = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            name = elem.attrib.get("name")
            if tag in {"include", "joint", "link", "sensor"} and name:
                pose_elem = elem.find("pose")
                out.append({
                    "tag": tag,
                    "name": name,
                    "pose": pose_elem.text.strip() if pose_elem is not None and pose_elem.text else None,
                    "pose_relative_to": pose_elem.attrib.get("relative_to") if pose_elem is not None else None,
                })
        return out

    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "databoss_model": str(databoss_model),
        "deployed_model": str(deployed_model),
        "stock_model": str(stock_model),
        "databoss_model_poses": poses(databoss_model),
        "stock_model_poses": poses(stock_model),
        "flow_adapter_contract": {
            "path": str(bridge_adapter),
            "distance_field": -1.0,
            "gyro_fields": "NaN by default; Phase 8L Gate 5 can enable same-window Gazebo IMU integration",
            "default_axis_map_in_phase8k": "-yx",
            "corrected_axis_map_phase8n": "xy",
            "sign_acceptance_authority": "scripts/analysis/check_flow_velocity_sign.py",
            "source_to_px4": "Gazebo camera -> LK/SIFT bridge -> OPTICAL_FLOW_RAD -> sensor_optical_flow -> EKF2 + separate distance_sensor",
        },
        "world": load_yaml(world_yaml).get("world", {}),
    }


def evaluate_gate(gate: str, report: dict) -> dict:
    frames = report.get("camera_frames", {})
    ranges = report.get("rangefinder", {})
    bridge = report.get("flow_bridge", {})
    flow = report.get("flow_fusion", {})
    truth = report.get("truth_metrics", {})
    status = report.get("status", {})

    checks = {}

    if gate in {"scene", "attitude", "auto"}:
        checks["camera_frames_present"] = frames.get("rows", 0) >= 100
        checks["camera_textured"] = (
            frames.get("image_analysis_available") is True
            and frames.get("textured_cell_fraction_mean", 0.0) >= 0.90
            and frames.get("feature_count_median", 0) >= 80
        )
        checks["range_finite"] = ranges.get("finite_positive_fraction", 0.0) >= 0.95
        med = ranges.get("median_m")
        checks["range_near_2p5m"] = med is not None and abs(float(med) - 2.5) <= 0.5

    if gate in {"axis", "rotation", "timing", "fusion", "loss", "auto"}:
        checks["bridge_present"] = bridge.get("available") is True
        checks["bridge_rate_ok"] = bridge.get("real_median_dt_ms") is not None and bridge.get("real_median_dt_ms") <= 40.0
        checks["bridge_quality_not_zeroed"] = bridge.get("quality_raw_zero_fraction", 1.0) < 0.5

    if gate == "axis":
        axis = report.get("axis_contract", {})
        checks["axis_contract_available"] = axis.get("available") is True
        checks["axis_sign_ok"] = axis.get("sign_ok") is True
        checks["axis_dominance_ok"] = axis.get("axis_dominance_ok") is True
        checks["axis_magnitude_ok"] = axis.get("magnitude_ok") is True
        checks["axis_range_finite"] = axis.get("range_finite_fraction", 0.0) >= 0.95

    if gate == "rotation":
        rot = report.get("rotation_contract", {})
        gyro_mode = rot.get("gyro_mode", "nan")
        checks["rotation_contract_available"] = rot.get("available") is True
        checks["rotation_rows"] = rot.get("rows", 0) >= 50
        checks["rotation_range_finite"] = rot.get("range_finite_fraction", 0.0) >= 0.95
        checks["rotation_flow_quantified"] = (
            rot.get("flow_x_rate_rad_s", {}).get("abs_mean") is not None
            and rot.get("flow_y_rate_rad_s", {}).get("abs_mean") is not None
        )
        if gyro_mode == "nan":
            checks["rotation_nan_gyro_baseline"] = rot.get("gyro_available_fraction", 1.0) == 0.0
        else:
            checks["rotation_gyro_available"] = rot.get("gyro_available_fraction", 0.0) >= 0.90

    if gate in {"fusion", "loss", "auto"}:
        checks["flow_fusion_rows"] = flow.get("aid_src_optical_flow_rows", 0) > 100
        checks["flow_active_fraction"] = flow.get("cs_opt_flow_active_fraction", 0.0) >= 0.6
        checks["flow_reject_ratio"] = flow.get("flow_rejected_over_fused", 999.0) < (0.25 if gate == "loss" else 0.10)
        checks["distance_sensor_ok"] = status.get("distance_sensor_ok") is True

    if gate == "loss":
        hmax = truth.get("horizontal_error", {}).get("max_m")
        zmax = truth.get("height_abs_error", {}).get("max_m")
        checks["truth_horizontal_bounded"] = hmax is not None and hmax <= 25.0
        checks["truth_horizontal_accepted"] = hmax is not None and hmax <= 10.0
        checks["truth_height_bounded"] = zmax is not None and zmax <= 10.0
        checks["gnss_loss_detected"] = status.get("gnss_loss_detected") is True

    hard_fail = [name for name, ok in checks.items() if not ok]
    return {
        "gate": gate,
        "checks": checks,
        "accepted": not hard_fail,
        "failed_checks": hard_fail,
    }


def build_run_report(run_dir: Path, gate: str) -> dict:
    status = load_json(run_dir / "logs" / "pxh_takeoff_land_truth_status.json")
    if not status:
        status = load_json(run_dir / "end_to_end_status.json")
    config = load_yaml(run_dir / "config.yaml")
    hover_window = infer_hover_window_from_range(run_dir)
    window_tuple = None
    if hover_window.get("available") is True:
        w = hover_window.get("window_sim_s") or []
        if len(w) == 2:
            window_tuple = (float(w[0]), float(w[1]))
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "gate": gate,
        "config": config,
        "status": status,
        "scene_window": hover_window,
        "camera_frames_all": summarize_frames(run_dir),
        "camera_frames": summarize_frames(run_dir, window_sim_s=window_tuple),
        "rangefinder_all": summarize_ranges(run_dir),
        "rangefinder": summarize_ranges(run_dir, window_sim_s=window_tuple),
        "flow_bridge": summarize_bridge(run_dir),
        "flow_fusion": load_json(run_dir / "flow_fusion_ulog.json"),
        "truth_metrics": load_json(run_dir / "ekf_vs_ground_truth_metrics.json"),
    }
    if gate in {"axis", "auto"}:
        report["axis_contract"] = summarize_axis_contract(run_dir, config)
    if gate in {"rotation", "auto"}:
        report["rotation_contract"] = summarize_rotation_contract(run_dir, config)
    report["gate_result"] = evaluate_gate(gate, report)
    return report


def write_markdown(report: dict, path: Path) -> None:
    gate = report.get("gate")
    result = report.get("gate_result", {})
    lines = [
        "# Sensor Contract Report",
        "",
        f"- Run: `{report.get('run_dir')}`",
        f"- Gate: `{gate}`",
        f"- Accepted: `{result.get('accepted')}`",
        "",
        "## Checks",
        "",
    ]
    for name, ok in result.get("checks", {}).items():
        lines.append(f"- `{name}`: `{ok}`")
    lines.extend(["", "## Key Metrics", ""])
    frames = report.get("camera_frames", {})
    frames_all = report.get("camera_frames_all", {})
    ranges = report.get("rangefinder", {})
    ranges_all = report.get("rangefinder_all", {})
    bridge = report.get("flow_bridge", {})
    flow = report.get("flow_fusion", {})
    truth = report.get("truth_metrics", {})
    axis = report.get("axis_contract", {})
    rot = report.get("rotation_contract", {})
    lines.extend([
        f"- Scene window sim s: `{report.get('scene_window', {}).get('window_sim_s')}`",
        f"- Camera frames in window: `{frames.get('rows')}`, rate Hz: `{frames.get('rate_hz')}`",
        f"- Camera frames total: `{frames_all.get('rows')}`, full-capture texture cell fraction mean: `{frames_all.get('textured_cell_fraction_mean')}`",
        f"- Texture cell fraction mean: `{frames.get('textured_cell_fraction_mean')}`",
        f"- Feature count median: `{frames.get('feature_count_median')}`",
        f"- Range finite fraction: `{ranges.get('finite_positive_fraction')}`, median m: `{ranges.get('median_m')}`",
        f"- Full-capture range finite fraction: `{ranges_all.get('finite_positive_fraction')}`, median m: `{ranges_all.get('median_m')}`",
        f"- Bridge real rows: `{bridge.get('real_rows')}`, median dt ms: `{bridge.get('real_median_dt_ms')}`",
        f"- Flow fused/rejected: `{flow.get('flow_fused_count')}` / `{flow.get('flow_rejected_count')}`",
        f"- Truth horizontal max m: `{truth.get('horizontal_error', {}).get('max_m')}`",
        f"- Truth height abs max m: `{truth.get('height_abs_error', {}).get('max_m')}`",
        f"- Axis sign/dominance/magnitude: `{axis.get('sign_ok')}` / `{axis.get('axis_dominance_ok')}` / `{axis.get('magnitude_ok')}`",
        f"- Axis contract type: `{axis.get('contract')}`",
        f"- Final sign authority: `{axis.get('final_sign_authority')}`",
        f"- Rotation gyro mode: `{rot.get('gyro_mode')}`, gyro available fraction: `{rot.get('gyro_available_fraction')}`",
        f"- Rotation flow abs mean rad/s x/y: `{rot.get('flow_x_rate_rad_s', {}).get('abs_mean')}` / `{rot.get('flow_y_rate_rad_s', {}).get('abs_mean')}`",
        f"- Rotation gyro abs mean rad/s x/y/z: `{rot.get('gyro_x_rate_rad_s', {}).get('abs_mean')}` / `{rot.get('gyro_y_rate_rad_s', {}).get('abs_mean')}` / `{rot.get('gyro_z_rate_rad_s', {}).get('abs_mean')}`",
        "",
    ])
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DATABOSS Phase 8L sensor-contract sanity report.")
    parser.add_argument("--run-dir", help="Run folder to summarize.")
    parser.add_argument("--gate", choices=["scene", "attitude", "axis", "rotation", "timing", "fusion", "loss", "auto"], default="auto")
    parser.add_argument("--static-audit", action="store_true", help="Emit static model/world/bridge contract audit instead of run report.")
    parser.add_argument("--out", help="Output JSON path. Defaults to <run-dir>/sensor_contract_report.json.")
    parser.add_argument("--markdown", help="Optional Markdown output path. Defaults to <run-dir>/sensor_contract_report.md for run reports.")
    args = parser.parse_args()

    if args.static_audit:
        report = static_audit()
        out = Path(args.out) if args.out else PROJECT_ROOT / "experiments/inspections/phase8l_static_contract_audit.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {out}")
        return 0

    if not args.run_dir:
        print("ERROR: --run-dir is required unless --static-audit is used", file=sys.stderr)
        return 2
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"ERROR: run dir not found: {run_dir}", file=sys.stderr)
        return 2

    report = build_run_report(run_dir, args.gate)
    out = Path(args.out) if args.out else run_dir / "sensor_contract_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    md = Path(args.markdown) if args.markdown else run_dir / "sensor_contract_report.md"
    write_markdown(report, md)
    accepted = bool(report["gate_result"]["accepted"])
    print(f"accepted={accepted}")
    print(f"wrote {out}")
    print(f"wrote {md}")
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
