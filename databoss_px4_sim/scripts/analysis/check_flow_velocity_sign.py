#!/usr/bin/env python3
"""Check PX4 EKF optical-flow velocity sign against independent velocity.

This is the Phase 8N sign sentinel. The accepted path compares PX4's own
estimator_optical_flow_vel.vel_body against GNSS velocity rotated into the PX4
FRD body frame. It catches sign errors after MAVLink receiver passthrough and
EKF2's internal optical-flow negation, which raw OPTICAL_FLOW_RAD wire checks
cannot prove by themselves.

The truth source is kept as an experimental scaffold for later GNSS-loss work;
do not use it as acceptance evidence until it has its own validation run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyulog import ULog


def dataset(ulog: ULog, name: str):
    try:
        return ulog.get_dataset(name).data
    except (KeyError, IndexError, ValueError):
        return None


def quat_to_rot_body_to_ned(q: np.ndarray) -> np.ndarray:
    """PX4 attitude quaternion rotation matrix, body FRD -> local NED."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def interp_cols(t_src: np.ndarray, cols: list[np.ndarray], t_dst: np.ndarray) -> np.ndarray:
    return np.vstack([np.interp(t_dst, t_src, c) for c in cols]).T


def velocity_body_from_ne(
    attitude: dict,
    t_s: np.ndarray,
    vel_n_m_s: np.ndarray,
    vel_e_m_s: np.ndarray,
) -> np.ndarray:
    t_att = np.asarray(attitude["timestamp_sample"], dtype=float) / 1e6
    qs = interp_cols(
        t_att,
        [np.asarray(attitude[f"q[{i}]"], dtype=float) for i in range(4)],
        t_s,
    )
    vel_body = []
    for q, vn, ve in zip(qs, vel_n_m_s, vel_e_m_s):
        norm = np.linalg.norm(q)
        if norm <= 0 or not np.isfinite(norm):
            vel_body.append([np.nan, np.nan])
            continue
        rot_body_to_ned = quat_to_rot_body_to_ned(q / norm)
        vel_body.append((rot_body_to_ned.T @ np.array([vn, ve, 0.0], dtype=float))[:2])
    return np.asarray(vel_body, dtype=float)


def reference_velocity_from_gps(ulog: ULog, t_s: np.ndarray) -> tuple[np.ndarray, str]:
    gps = dataset(ulog, "vehicle_gps_position")
    if gps is None:
        raise ValueError("vehicle_gps_position not available")
    t_gps = np.asarray(gps["timestamp_sample"], dtype=float) / 1e6
    vel_ne = interp_cols(
        t_gps,
        [
            np.asarray(gps["vel_n_m_s"], dtype=float),
            np.asarray(gps["vel_e_m_s"], dtype=float),
        ],
        t_s,
    )
    return vel_ne, "vehicle_gps_position"


def reference_velocity_from_truth(run_dir: Path, t_rel_s: np.ndarray) -> tuple[np.ndarray, str]:
    aligned_path = run_dir / "ekf_vs_ground_truth_aligned.csv"
    if not aligned_path.exists():
        raise ValueError(f"aligned truth CSV not available: {aligned_path}")
    aligned = pd.read_csv(aligned_path)
    t_ref = aligned["px4_t_rel_s"].to_numpy(dtype=float)
    if len(t_ref) < 3:
        raise ValueError("aligned truth CSV has too few rows")
    vel_n = np.gradient(aligned["gz_x_rel"].to_numpy(dtype=float), t_ref)
    vel_e = np.gradient(aligned["gz_y_rel"].to_numpy(dtype=float), t_ref)
    vel_ne = interp_cols(t_ref, [vel_n, vel_e], t_rel_s)
    return vel_ne, "ekf_vs_ground_truth_aligned"


def analyze(
    run_dir: Path,
    *,
    source: str,
    min_ref_speed_m_s: float,
    min_flow_speed_m_s: float,
    active_axis_mean_m_s: float,
    corr_min: float,
    gain_min: float,
    gain_max: float,
) -> dict:
    ulg = run_dir / "logs" / "flight.ulg" if run_dir.is_dir() else run_dir
    if not ulg.exists():
        raise FileNotFoundError(f"ULog not found: {ulg}")

    ulog = ULog(str(ulg))
    flow_vel = dataset(ulog, "estimator_optical_flow_vel")
    attitude = dataset(ulog, "vehicle_attitude")
    if flow_vel is None:
        raise ValueError("estimator_optical_flow_vel not available")
    if attitude is None:
        raise ValueError("vehicle_attitude not available")

    t_s = np.asarray(flow_vel["timestamp_sample"], dtype=float) / 1e6
    t_rel_s = t_s - (float(ulog.start_timestamp) / 1e6)
    flow_body = np.vstack([
        np.asarray(flow_vel["vel_body[0]"], dtype=float),
        np.asarray(flow_vel["vel_body[1]"], dtype=float),
    ]).T

    source_used = source
    if source == "auto":
        try:
            vel_ne, reference_name = reference_velocity_from_gps(ulog, t_s)
            source_used = "gps"
        except Exception:
            vel_ne, reference_name = reference_velocity_from_truth(
                run_dir if run_dir.is_dir() else run_dir.parent.parent,
                t_rel_s,
            )
            source_used = "truth"
    elif source == "gps":
        vel_ne, reference_name = reference_velocity_from_gps(ulog, t_s)
    else:
        vel_ne, reference_name = reference_velocity_from_truth(
            run_dir if run_dir.is_dir() else run_dir.parent.parent,
            t_rel_s,
        )

    ref_body = velocity_body_from_ne(attitude, t_s, vel_ne[:, 0], vel_ne[:, 1])

    finite = np.isfinite(flow_body).all(axis=1) & np.isfinite(ref_body).all(axis=1)
    moving = (
        finite
        & (np.linalg.norm(ref_body, axis=1) >= min_ref_speed_m_s)
        & (np.linalg.norm(flow_body, axis=1) >= min_flow_speed_m_s)
    )

    axes = {}
    active_axis_names = []
    for idx, name in enumerate(["body_x", "body_y"]):
        x = ref_body[moving, idx]
        y = flow_body[moving, idx]
        ref_abs_mean = float(np.mean(np.abs(x))) if len(x) else None
        flow_abs_mean = float(np.mean(np.abs(y))) if len(y) else None
        active = bool(ref_abs_mean is not None and ref_abs_mean >= active_axis_mean_m_s)
        if active:
            active_axis_names.append(name)
        corr = float(np.corrcoef(x, y)[0, 1]) if len(x) >= 3 else None
        gain = float(np.dot(x, y) / np.dot(x, x)) if len(x) >= 3 and np.dot(x, x) > 0 else None
        ok = (
            active
            and corr is not None
            and gain is not None
            and corr >= corr_min
            and gain_min <= gain <= gain_max
        )
        axes[name] = {
            "active": active,
            "samples": int(len(x)),
            "corr": round(corr, 4) if corr is not None else None,
            "gain": round(gain, 4) if gain is not None else None,
            "reference_abs_mean_m_s": round(ref_abs_mean, 4) if ref_abs_mean is not None else None,
            "flow_abs_mean_m_s": round(flow_abs_mean, 4) if flow_abs_mean is not None else None,
            "ok": bool(ok),
        }

    accepted = bool(active_axis_names and all(axes[name]["ok"] for name in active_axis_names))
    return {
        "run": str(run_dir),
        "ulog": str(ulg),
        "source_requested": source,
        "source_used": source_used,
        "reference": reference_name,
        "samples_total": int(len(t_s)),
        "samples_used": int(moving.sum()),
        "min_ref_speed_m_s": min_ref_speed_m_s,
        "min_flow_speed_m_s": min_flow_speed_m_s,
        "active_axis_mean_m_s": active_axis_mean_m_s,
        "corr_min": corr_min,
        "gain_min": gain_min,
        "gain_max": gain_max,
        "active_axes": active_axis_names,
        "axes": axes,
        "accepted": accepted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="Run directory or flight.ulg path.")
    parser.add_argument("--source", choices=["auto", "gps", "truth"], default="auto")
    parser.add_argument("--min-ref-speed-m-s", type=float, default=0.05)
    parser.add_argument("--min-flow-speed-m-s", type=float, default=0.01)
    parser.add_argument("--active-axis-mean-m-s", type=float, default=0.05)
    parser.add_argument("--corr-min", type=float, default=0.8)
    parser.add_argument("--gain-min", type=float, default=0.7)
    parser.add_argument("--gain-max", type=float, default=1.3)
    parser.add_argument("--out", help="Optional JSON output path.")
    args = parser.parse_args()

    results = []
    ok = True
    for run in args.runs:
        result = analyze(
            Path(run).resolve(),
            source=args.source,
            min_ref_speed_m_s=args.min_ref_speed_m_s,
            min_flow_speed_m_s=args.min_flow_speed_m_s,
            active_axis_mean_m_s=args.active_axis_mean_m_s,
            corr_min=args.corr_min,
            gain_min=args.gain_min,
            gain_max=args.gain_max,
        )
        results.append(result)
        ok = ok and bool(result["accepted"])

    text = json.dumps(results if len(results) > 1 else results[0], indent=2) + "\n"
    print(text, end="")
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
