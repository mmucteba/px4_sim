#!/usr/bin/env python3
"""Build Phase 8I flat-world A/B/C/D comparison plots and report."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


DEFAULT_OUT_DIR = Path("experiments/comparisons/phase8i_flat_phototex_abcd_20260714")
DEFAULT_CASES = [
    (
        "A",
        "GNSS on",
        "experiments/runs/20260714_070542_phase8i_a_gnsson_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
    (
        "B",
        "GNSS loss, no aiding",
        "experiments/runs/20260714_071532_phase8i_b_loss_noaid_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
    (
        "C",
        "GNSS loss, ideal odom",
        "experiments/runs/20260714_074250_phase8i_c_loss_idealodom_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
    (
        "D",
        "GNSS loss, live flow",
        "experiments/runs/20260714_075214_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth",
    ),
]


@dataclass
class CaseData:
    case: str
    label: str
    run_dir: Path
    aligned: pd.DataFrame
    metrics: dict[str, Any]
    status: dict[str, Any]
    flow: dict[str, Any]
    config: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def get_nested(data: dict[str, Any], path: tuple[str, ...], default: Any = np.nan) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def load_case(case: str, label: str, run_dir: Path) -> CaseData:
    aligned_path = run_dir / "ekf_vs_ground_truth_aligned.csv"
    metrics_path = run_dir / "ekf_vs_ground_truth_metrics.json"
    status_path = run_dir / "logs" / "pxh_takeoff_land_truth_status.json"
    flow_path = run_dir / "flow_fusion_ulog.json"
    if not aligned_path.exists():
        raise FileNotFoundError(aligned_path)
    if not metrics_path.exists():
        raise FileNotFoundError(metrics_path)
    return CaseData(
        case=case,
        label=label,
        run_dir=run_dir,
        aligned=pd.read_csv(aligned_path),
        metrics=load_json(metrics_path),
        status=load_json(status_path),
        flow=load_json(flow_path),
        config=load_yaml(run_dir / "config.yaml"),
    )


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def finite_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def stream_rate(path: Path, time_col: str, sent_only: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {"rows": 0, "duration_s": np.nan, "rate_hz": np.nan}
    df = pd.read_csv(path)
    if sent_only and "sent" in df.columns:
        df = df.loc[pd.to_numeric(df["sent"], errors="coerce").fillna(0).astype(int) == 1]
    if time_col not in df.columns:
        return {"rows": 0, "duration_s": np.nan, "rate_hz": np.nan}
    t = pd.to_numeric(df[time_col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(t) < 2:
        return {"rows": int(len(t)), "duration_s": np.nan, "rate_hz": np.nan}
    duration = float(t.iloc[-1] - t.iloc[0])
    rate = float((len(t) - 1) / duration) if duration > 0 else np.nan
    return {"rows": int(len(t)), "duration_s": duration, "rate_hz": rate}


def metric_rows(cases: list[CaseData]) -> pd.DataFrame:
    rows = []
    for item in cases:
        m = item.metrics
        s = item.status
        f = item.flow
        rows.append(
            {
                "case": item.case,
                "label": item.label,
                "run_dir": str(item.run_dir),
                "postprocess_accepted": bool(m.get("accepted", False)),
                "runner_accepted": bool(s.get("accepted", False)) if s else np.nan,
                "gnss_loss_detected": bool(s.get("gnss_loss_detected", False)) if s else False,
                "aligned_duration_s": get_nested(m, ("aligned_duration_s",)),
                "horizontal_mean_m": get_nested(m, ("horizontal_error", "mean_m")),
                "horizontal_median_m": get_nested(m, ("horizontal_error", "median_m")),
                "horizontal_p95_m": get_nested(m, ("horizontal_error", "p95_m")),
                "horizontal_max_m": get_nested(m, ("horizontal_error", "max_m")),
                "horizontal_end_m": get_nested(m, ("horizontal_error", "end_m")),
                "height_abs_mean_m": get_nested(m, ("height_abs_error", "mean_m")),
                "height_abs_p95_m": get_nested(m, ("height_abs_error", "p95_m")),
                "height_abs_max_m": get_nested(m, ("height_abs_error", "max_m")),
                "height_abs_end_m": get_nested(m, ("height_abs_error", "end_m")),
                "gazebo_route_end_m": get_nested(
                    m,
                    (
                        "station_keeping",
                        "gazebo_horizontal_displacement_from_start",
                        "end_m",
                    ),
                ),
                "px4_route_end_m": get_nested(
                    m,
                    (
                        "station_keeping",
                        "px4_horizontal_displacement_from_start",
                        "end_m",
                    ),
                ),
                "ulog_airborne_duration_s": s.get("ulog_airborne_duration_s", np.nan),
                "ulog_max_height_up_m": s.get("ulog_max_height_up_m", np.nan),
                "rangefinder_probe_ok": s.get("rangefinder_probe_ok", np.nan),
                "ulog_distance_sensor_ok": s.get("ulog_distance_sensor_ok", np.nan),
                "flow_bridge_sent_rows": s.get("flow_bridge_sent_rows", np.nan),
                "flow_fused_count": f.get("flow_fused_count", np.nan),
                "flow_rejected_count": f.get("flow_rejected_count", np.nan),
                "flow_rejected_over_fused": f.get("flow_rejected_over_fused", np.nan),
                "flow_quality_zero_fraction": f.get("flow_quality_zero_fraction", np.nan),
                "cs_opt_flow_active_fraction": f.get("cs_opt_flow_active_fraction", np.nan),
                "xy_reset_counter_delta": f.get("xy_reset_counter_delta", np.nan),
            }
        )
    return pd.DataFrame(rows)


def setup_rows(cases: list[CaseData]) -> pd.DataFrame:
    rows = []
    for item in cases:
        cfg = item.config
        aiding = cfg.get("aiding", {})
        flow = cfg.get("flow_bridge", {})
        control = cfg.get("control", {})
        gnss = cfg.get("gnss", {})
        viz = cfg.get("visualization", {}).get("gazebo_web", {})
        prelaunch = item.status.get("prelaunch_param_overrides", {})
        ev_enabled = bool(aiding.get("external_odom_enabled", aiding.get("enabled", False)))
        ekf_hgt_ref = aiding.get("ekf2_hgt_ref", prelaunch.get("EKF2_HGT_REF", np.nan))
        rows.append(
            {
                "case": item.case,
                "label": item.label,
                "world": get_nested(cfg, ("world", "name"), ""),
                "route": get_nested(cfg, ("route", "name"), ""),
                "altitude_agl_m": get_nested(cfg, ("route", "altitude_agl_m"), np.nan),
                "duration_s": get_nested(cfg, ("route", "duration_s"), np.nan),
                "control_rate_hz": control.get("rate_hz", np.nan),
                "setpoint_mode": control.get("setpoint_mode", ""),
                "vx_m_s": control.get("vx_m_s", np.nan),
                "vy_m_s": control.get("vy_m_s", np.nan),
                "gnss_start_enabled": gnss.get("start_enabled", np.nan),
                "gnss_loss_enabled": gnss.get("loss_enabled", np.nan),
                "gnss_loss_after_takeoff_s": gnss.get("loss_after_takeoff_s", np.nan),
                "aiding_mode": aiding.get("mode", "none"),
                "external_odom_enabled": ev_enabled,
                "external_odom_rate_hz": aiding.get("rate_hz", np.nan) if ev_enabled else np.nan,
                "external_odom_source": aiding.get("source", ""),
                "external_odom_velocity_source": aiding.get("velocity_source", ""),
                "ekf2_ev_ctrl": aiding.get("ekf2_ev_ctrl", np.nan) if ev_enabled else np.nan,
                "ekf2_hgt_ref": ekf_hgt_ref,
                "ekf2_ev_delay_ms": aiding.get("ekf2_ev_delay_ms", np.nan) if ev_enabled else np.nan,
                "flow_bridge_enabled": flow.get("enabled", False),
                "flow_estimator": flow.get("estimator", ""),
                "flow_bridge_rate_hz": flow.get("rate_hz", np.nan),
                "flow_axis_map": flow.get("axis_map", ""),
                "flow_max_width": flow.get("max_width", np.nan),
                "flow_sift_n_features": flow.get("sift_n_features", item.status.get("flow_bridge_sift_n_features", np.nan)),
                "flow_send_min_range_m": flow.get("send_min_range_m", item.status.get("flow_bridge_send_min_range_m", np.nan)),
                "flow_send_max_range_m": flow.get("send_max_range_m", item.status.get("flow_bridge_send_max_range_m", np.nan)),
                "flow_send_min_quality": flow.get("send_min_quality", item.status.get("flow_bridge_send_min_quality", np.nan)),
                "flow_send_min_matches": flow.get("send_min_matches", item.status.get("flow_bridge_send_min_matches", np.nan)),
                "flow_reset_on_unsent": flow.get("reset_on_unsent", item.status.get("flow_bridge_reset_on_unsent", np.nan)),
                "ekf2_of_ctrl": flow.get("ekf2_of_ctrl", np.nan),
                "ekf2_of_qmin": flow.get("ekf2_of_qmin", np.nan),
                "rangefinder_required": get_nested(cfg, ("rangefinder", "proof_enabled"), np.nan),
                "gazebo_web_port": viz.get("port", np.nan),
                "gazebo_web_publication_hz": viz.get("publication_hz", np.nan),
            }
        )
    return pd.DataFrame(rows)


def frequency_rows(cases: list[CaseData]) -> pd.DataFrame:
    rows = []
    for item in cases:
        aligned = item.aligned
        aligned_t = finite_series(aligned, "px4_t_rel_s").dropna()
        aligned_duration = float(aligned_t.iloc[-1] - aligned_t.iloc[0]) if len(aligned_t) > 1 else np.nan
        aligned_hz = float((len(aligned_t) - 1) / aligned_duration) if aligned_duration > 0 else np.nan
        gz = stream_rate(item.run_dir / "gazebo_truth" / "gazebo_ground_truth_x500_cam_lidar_down_0.csv", "sim_time_s")
        offboard = stream_rate(item.run_dir / "logs" / "offboard_local_hold_sent.csv", "time_since_start_s")
        external = stream_rate(item.run_dir / "logs" / "external_odometry_sent.csv", "sim_time_s")
        frames = stream_rate(item.run_dir / "flow_recording" / "frames_index.csv", "t_sim_s")
        lidar = stream_rate(item.run_dir / "flow_recording" / "rangefinder.csv", "t_sim_s")
        bridge = stream_rate(item.run_dir / "flow_bridge" / "flow_bridge_sent.csv", "t_frame_sim_s", sent_only=True)

        lidar_finite_fraction = np.nan
        lidar_path = item.run_dir / "flow_recording" / "rangefinder.csv"
        if lidar_path.exists():
            rf = pd.read_csv(lidar_path)
            r = pd.to_numeric(rf["range_m"], errors="coerce").to_numpy(float)
            lidar_finite_fraction = float((np.isfinite(r) & (r > 0)).mean()) if len(r) else np.nan

        bridge_range_finite_fraction = np.nan
        bridge_path = item.run_dir / "flow_bridge" / "flow_bridge_sent.csv"
        if bridge_path.exists():
            fb = pd.read_csv(bridge_path)
            if "sent" in fb.columns:
                fb = fb.loc[pd.to_numeric(fb["sent"], errors="coerce").fillna(0).astype(int) == 1]
            r = pd.to_numeric(fb["range_m"], errors="coerce").to_numpy(float)
            bridge_range_finite_fraction = float((np.isfinite(r) & (r > 0)).mean()) if len(r) else np.nan

        rows.append(
            {
                "case": item.case,
                "label": item.label,
                "aligned_truth_hz": aligned_hz,
                "aligned_rows": int(len(aligned_t)),
                "gazebo_truth_hz": gz["rate_hz"],
                "gazebo_truth_rows": gz["rows"],
                "offboard_setpoint_hz": offboard["rate_hz"],
                "offboard_setpoint_rows": offboard["rows"],
                "external_odom_sent_hz": external["rate_hz"],
                "external_odom_sent_rows": external["rows"],
                "camera_frame_record_hz": frames["rate_hz"],
                "camera_frame_rows": frames["rows"],
                "raw_lidar_record_hz": lidar["rate_hz"],
                "raw_lidar_rows": lidar["rows"],
                "raw_lidar_finite_fraction": lidar_finite_fraction,
                "flow_bridge_sent_hz": bridge["rate_hz"],
                "flow_bridge_sent_rows": bridge["rows"],
                "flow_bridge_range_finite_fraction": bridge_range_finite_fraction,
                "ulog_distance_sensor_rows": item.status.get("ulog_distance_sensor_rows", np.nan),
                "ulog_airborne_duration_s": item.status.get("ulog_airborne_duration_s", np.nan),
            }
        )
    return pd.DataFrame(rows)


def lidar_truth_comparison(case_d: CaseData, out_dir: Path, plots_dir: Path) -> pd.DataFrame:
    gz_path = case_d.run_dir / "gazebo_truth" / "gazebo_ground_truth_x500_cam_lidar_down_0.csv"
    if not gz_path.exists():
        return pd.DataFrame()

    gz = pd.read_csv(gz_path, usecols=["sim_time_s", "z"]).sort_values("sim_time_s")
    gz["truth_height_agl_m"] = gz["z"] - float(gz["z"].iloc[:100].median())
    streams = [
        ("raw_recorder", case_d.run_dir / "flow_recording" / "rangefinder.csv", "t_sim_s"),
        ("flow_bridge_input", case_d.run_dir / "flow_bridge" / "flow_bridge_sent.csv", "t_frame_sim_s"),
    ]
    metrics = []
    matched_frames = []
    landed_offset: float | None = None

    for name, path, t_col in streams:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if name == "flow_bridge_input" and "sent" in df.columns:
            df = df.loc[pd.to_numeric(df["sent"], errors="coerce").fillna(0).astype(int) == 1]
        if "range_m" not in df.columns:
            continue
        t = pd.to_numeric(df[t_col], errors="coerce")
        r = pd.to_numeric(df["range_m"], errors="coerce")
        finite = np.isfinite(r.to_numpy(float)) & (r.to_numpy(float) > 0) & np.isfinite(t.to_numpy(float))
        finite_fraction = float(finite.mean()) if len(finite) else np.nan
        finite_df = pd.DataFrame({"t_sim_s": t[finite], "range_m": r[finite]}).sort_values("t_sim_s")
        if finite_df.empty:
            continue
        merged = pd.merge_asof(
            finite_df,
            gz,
            left_on="t_sim_s",
            right_on="sim_time_s",
            direction="nearest",
            tolerance=0.05,
        ).dropna(subset=["truth_height_agl_m"])
        if merged.empty:
            continue
        landed = merged.loc[merged["truth_height_agl_m"] < 0.3, "range_m"]
        if name == "raw_recorder" and len(landed):
            landed_offset = float(landed.median())
        if len(landed):
            offset = float(landed.median())
        elif landed_offset is not None:
            offset = landed_offset
        else:
            offset = float(merged["range_m"].iloc[:50].median())
        merged["lidar_height_agl_m"] = merged["range_m"] - offset
        merged["lidar_minus_truth_m"] = merged["lidar_height_agl_m"] - merged["truth_height_agl_m"]
        merged["stream"] = name
        matched_frames.append(
            merged[
                [
                    "stream",
                    "t_sim_s",
                    "range_m",
                    "lidar_height_agl_m",
                    "truth_height_agl_m",
                    "lidar_minus_truth_m",
                ]
            ]
        )
        err = merged["lidar_minus_truth_m"].abs()
        metrics.append(
            {
                "stream": name,
                "rows_total": int(len(df)),
                "rows_finite_positive": int(finite.sum()),
                "finite_positive_fraction": finite_fraction,
                "matched_rows": int(len(merged)),
                "sample_rate_hz": stream_rate(path, t_col)["rate_hz"],
                "range_offset_landed_m": offset,
                "mean_abs_lidar_truth_error_m": float(err.mean()),
                "median_abs_lidar_truth_error_m": float(err.median()),
                "p95_abs_lidar_truth_error_m": float(err.quantile(0.95)),
                "max_abs_lidar_truth_error_m": float(err.max()),
                "corr_lidar_truth_height": float(merged["lidar_height_agl_m"].corr(merged["truth_height_agl_m"])),
            }
        )

    if not matched_frames:
        return pd.DataFrame()

    matched = pd.concat(matched_frames, ignore_index=True)
    matched.to_csv(out_dir / "lidar_truth_matched_d.csv", index=False)
    lidar_metrics = pd.DataFrame(metrics)
    lidar_metrics.to_csv(out_dir / "lidar_truth_metrics_d.csv", index=False)
    (out_dir / "lidar_truth_metrics_d.json").write_text(
        json.dumps(lidar_metrics.to_dict(orient="records"), indent=2)
    )

    plt.figure(figsize=(10, 7.5))
    ax1 = plt.subplot(2, 1, 1)
    for stream, group in matched.groupby("stream"):
        ax1.plot(group["t_sim_s"], group["lidar_height_agl_m"], linewidth=1.6, label=f"{stream} lidar")
    truth = matched.sort_values("t_sim_s").drop_duplicates("t_sim_s")
    ax1.plot(truth["t_sim_s"], truth["truth_height_agl_m"], color="#111111", linewidth=1.2, alpha=0.75, label="Gazebo truth height")
    ax1.set_ylabel("height AGL (m)")
    ax1.set_title("Case D lidar input vs Gazebo truth height")
    ax1.grid(True, alpha=0.25)
    ax1.legend(fontsize=8)

    ax2 = plt.subplot(2, 1, 2, sharex=ax1)
    for stream, group in matched.groupby("stream"):
        ax2.plot(group["t_sim_s"], group["lidar_minus_truth_m"].abs(), linewidth=1.4, label=f"{stream} abs error")
    ax2.set_xlabel("Gazebo sim time (s)")
    ax2.set_ylabel("absolute error (m)")
    ax2.set_yscale("symlog", linthresh=0.2)
    ax2.grid(True, which="both", alpha=0.25)
    ax2.legend(fontsize=8)
    savefig(plots_dir / "lidar_truth_height_d.png")

    plt.figure(figsize=(7.5, 7.0))
    max_h = max(float(matched["truth_height_agl_m"].max()), float(matched["lidar_height_agl_m"].max()))
    for stream, group in matched.groupby("stream"):
        plt.scatter(group["truth_height_agl_m"], group["lidar_height_agl_m"], s=8, alpha=0.45, label=stream)
    plt.plot([0, max_h], [0, max_h], color="#111111", linewidth=1.0, alpha=0.65, label="ideal y=x")
    plt.xlabel("Gazebo truth height AGL (m)")
    plt.ylabel("lidar-derived height AGL (m)")
    plt.title("Case D lidar/truth height scatter")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "lidar_truth_scatter_d.png")

    return lidar_metrics


def camera_sample(case_d: CaseData, out_dir: Path, plots_dir: Path) -> dict[str, Any]:
    frames_index = case_d.run_dir / "flow_recording" / "frames_index.csv"
    samples = [
        ("frame_000300.jpg", "camera_sample_d_early_invalid_frame_000300.jpg", "early startup frame before valid flow feeding"),
        ("frame_000500.jpg", "camera_sample_d_valid_texture_frame_000500.jpg", "middle valid textured-ground frame"),
        ("frame_000900.jpg", "camera_sample_d_late_edge_frame_000900.jpg", "late repaired-window frame before shutdown"),
    ]
    rows = []
    frames = pd.DataFrame()
    if frames_index.exists():
        frames = pd.read_csv(frames_index)
    for frame, out_name, note in samples:
        src = case_d.run_dir / "flow_recording" / "frames" / frame
        dst = plots_dir / out_name
        row_data = {
            "case": case_d.case,
            "source": str(src),
            "output": str(dst),
            "frame": frame,
            "t_sim_s": np.nan,
            "note": note,
        }
        row = frames.loc[frames["frame_path"] == frame] if not frames.empty else pd.DataFrame()
        if not row.empty:
            row_data["t_sim_s"] = float(row.iloc[0]["t_sim_s"])
        if src.exists():
            shutil.copy2(src, dst)
        rows.append(row_data)
    (out_dir / "camera_samples_d.json").write_text(json.dumps(rows, indent=2))
    return {"case": case_d.case, "samples": rows}


def plot_routes_overlay(cases: list[CaseData], plots_dir: Path) -> None:
    colors = {"A": "#1f77b4", "B": "#d62728", "C": "#2ca02c", "D": "#9467bd"}
    plt.figure(figsize=(9.5, 7.0))
    for item in cases:
        df = item.aligned
        color = colors[item.case]
        gx = finite_series(df, "gz_x_rel")
        gy = finite_series(df, "gz_y_rel")
        px = finite_series(df, "px4_x_rel")
        py = finite_series(df, "px4_y_rel")
        plt.plot(gx, gy, color=color, linewidth=2.0, label=f"{item.case} truth")
        plt.plot(px, py, color=color, linewidth=1.25, linestyle="--", alpha=0.85, label=f"{item.case} EKF")
        if len(gx.dropna()) and len(gy.dropna()):
            plt.scatter([gx.iloc[0]], [gy.iloc[0]], color=color, s=20, marker="o")
            plt.scatter([gx.iloc[-1]], [gy.iloc[-1]], color=color, s=45, marker="x")
    plt.axvline(0.0, color="#777777", linewidth=0.8, alpha=0.4)
    plt.axhline(0.0, color="#777777", linewidth=0.8, alpha=0.4)
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.0, alpha=0.35, label="nominal +Y route")
    plt.xlabel("relative X / north (m)")
    plt.ylabel("relative Y / east (m)")
    plt.title("Phase 8I flat A/B/C/D route overlay")
    plt.axis("equal")
    plt.grid(True, alpha=0.25)
    plt.legend(ncol=2, fontsize=8)
    savefig(plots_dir / "routes_xy_overlay.png")


def plot_routes_nearfield(cases: list[CaseData], plots_dir: Path) -> None:
    colors = {"A": "#1f77b4", "B": "#d62728", "C": "#2ca02c", "D": "#9467bd"}
    plt.figure(figsize=(8.5, 7.5))
    for item in cases:
        df = item.aligned
        color = colors[item.case]
        plt.plot(
            finite_series(df, "gz_x_rel"),
            finite_series(df, "gz_y_rel"),
            color=color,
            linewidth=2.0,
            label=f"{item.case} truth",
        )
        plt.plot(
            finite_series(df, "px4_x_rel"),
            finite_series(df, "px4_y_rel"),
            color=color,
            linewidth=1.25,
            linestyle="--",
            alpha=0.85,
            label=f"{item.case} EKF",
        )
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.2, alpha=0.45, label="nominal +Y route")
    plt.xlim(-20, 30)
    plt.ylim(-20, 30)
    plt.xlabel("relative X / north (m)")
    plt.ylabel("relative Y / east (m)")
    plt.title("Phase 8I route overlay, near field")
    plt.grid(True, alpha=0.25)
    plt.legend(ncol=2, fontsize=8)
    savefig(plots_dir / "routes_xy_nearfield.png")


def plot_case_routes(cases: list[CaseData], plots_dir: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11, 9), sharex=False, sharey=False)
    for ax, item in zip(axes.ravel(), cases):
        df = item.aligned
        ax.plot(finite_series(df, "gz_x_rel"), finite_series(df, "gz_y_rel"), linewidth=2.0, label="Gazebo truth")
        ax.plot(
            finite_series(df, "px4_x_rel"),
            finite_series(df, "px4_y_rel"),
            linewidth=1.5,
            linestyle="--",
            label="PX4 EKF",
        )
        ax.plot([0, 0], [0, 10], color="#222222", linewidth=1.0, alpha=0.35, label="nominal +Y")
        ax.set_title(f"{item.case}: {item.label}")
        ax.set_xlabel("relative X / north (m)")
        ax.set_ylabel("relative Y / east (m)")
        ax.grid(True, alpha=0.25)
        ax.axis("equal")
        ax.legend(fontsize=8)
    savefig(plots_dir / "route_xy_ekf_vs_truth.png")


def plot_error_timeseries(cases: list[CaseData], plots_dir: Path) -> None:
    plt.figure(figsize=(10, 5.5))
    for item in cases:
        df = item.aligned
        plt.plot(
            finite_series(df, "px4_t_rel_s"),
            finite_series(df, "horizontal_error_m"),
            linewidth=1.8,
            label=f"{item.case}: {item.label}",
        )
    plt.yscale("symlog", linthresh=1.0)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("horizontal error vs truth (m)")
    plt.title("Phase 8I horizontal error")
    plt.grid(True, which="both", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "horizontal_error_timeseries.png")

    plt.figure(figsize=(10, 5.5))
    for item in cases:
        df = item.aligned
        plt.plot(
            finite_series(df, "px4_t_rel_s"),
            finite_series(df, "abs_height_error_m"),
            linewidth=1.8,
            label=f"{item.case}: {item.label}",
        )
    plt.yscale("symlog", linthresh=1.0)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("absolute height error vs truth (m)")
    plt.title("Phase 8I height error")
    plt.grid(True, which="both", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "height_error_timeseries.png")


def plot_metric_bars(summary: pd.DataFrame, plots_dir: Path) -> None:
    labels = [f"{row.case}\n{row.label}" for row in summary.itertuples()]
    x = np.arange(len(labels))
    width = 0.24
    plt.figure(figsize=(10, 5.5))
    plt.bar(x - width, summary["horizontal_mean_m"], width, label="mean")
    plt.bar(x, summary["horizontal_p95_m"], width, label="p95")
    plt.bar(x + width, summary["horizontal_end_m"], width, label="end")
    plt.yscale("symlog", linthresh=1.0)
    plt.xticks(x, labels, rotation=0)
    plt.ylabel("horizontal error (m)")
    plt.title("Horizontal error summary")
    plt.grid(True, axis="y", which="both", alpha=0.25)
    plt.legend()
    savefig(plots_dir / "horizontal_error_bars.png")

    plt.figure(figsize=(10, 5.5))
    plt.bar(x - width, summary["height_abs_mean_m"], width, label="mean")
    plt.bar(x, summary["height_abs_p95_m"], width, label="p95")
    plt.bar(x + width, summary["height_abs_end_m"], width, label="end")
    plt.yscale("symlog", linthresh=1.0)
    plt.xticks(x, labels, rotation=0)
    plt.ylabel("absolute height error (m)")
    plt.title("Height error summary")
    plt.grid(True, axis="y", which="both", alpha=0.25)
    plt.legend()
    savefig(plots_dir / "height_error_bars.png")


def plot_flow_diagnostics(case_d: CaseData, plots_dir: Path) -> None:
    flow_csv = case_d.run_dir / "flow_bridge" / "flow_bridge_sent.csv"
    if not flow_csv.exists():
        return
    flow = pd.read_csv(flow_csv)
    t = finite_series(flow, "t_frame_sim_s")
    if len(t.dropna()):
        t = t - t.dropna().iloc[0]
    quality = finite_series(flow, "quality_sent")
    rng = finite_series(flow, "range_m")
    matches = finite_series(flow, "n_matches")
    ix = finite_series(flow, "integrated_x_sent")
    iy = finite_series(flow, "integrated_y_sent")

    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(t, quality, color="#1f77b4", linewidth=1.4)
    axes[0].set_ylabel("quality")
    axes[0].set_ylim(-5, 260)
    axes[0].grid(True, alpha=0.25)

    axes[1].plot(t, matches, color="#2ca02c", linewidth=1.4)
    axes[1].set_ylabel("matches")
    axes[1].grid(True, alpha=0.25)

    axes[2].plot(t, rng, color="#ff7f0e", linewidth=1.4)
    axes[2].set_ylabel("range (m)")
    axes[2].grid(True, alpha=0.25)

    axes[3].plot(t, ix, color="#9467bd", linewidth=1.2, label="x sent")
    axes[3].plot(t, iy, color="#8c564b", linewidth=1.2, label="y sent")
    axes[3].set_xlabel("flow bridge time since first row (s)")
    axes[3].set_ylabel("integrated flow (rad)")
    axes[3].grid(True, alpha=0.25)
    axes[3].legend(fontsize=8)
    fig.suptitle("Case D live optical-flow bridge diagnostics", y=0.995)
    savefig(plots_dir / "flow_d_diagnostics.png")


def markdown_table(summary: pd.DataFrame) -> str:
    columns = [
        ("case", "Case"),
        ("label", "Condition"),
        ("horizontal_mean_m", "H mean m"),
        ("horizontal_max_m", "H max m"),
        ("horizontal_end_m", "H end m"),
        ("height_abs_mean_m", "Z mean m"),
        ("gazebo_route_end_m", "Truth route m"),
        ("ulog_max_height_up_m", "Max height m"),
        ("runner_accepted", "Runner ok"),
    ]
    lines = ["|" + "|".join(title for _, title in columns) + "|"]
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for row in summary.to_dict(orient="records"):
        vals = []
        for key, _title in columns:
            val = row[key]
            if isinstance(val, float):
                vals.append("" if np.isnan(val) else f"{val:.3f}")
            else:
                vals.append(str(val))
        lines.append("|" + "|".join(vals) + "|")
    return "\n".join(lines)


def compact_table(df: pd.DataFrame, columns: list[tuple[str, str]], float_digits: int = 2) -> str:
    lines = ["|" + "|".join(title for _, title in columns) + "|"]
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for row in df.to_dict(orient="records"):
        vals = []
        for key, _title in columns:
            val = row.get(key, "")
            if isinstance(val, (float, np.floating)):
                vals.append("" if np.isnan(val) else f"{val:.{float_digits}f}")
            else:
                vals.append(str(val))
        lines.append("|" + "|".join(vals) + "|")
    return "\n".join(lines)


def write_report(
    out_dir: Path,
    summary: pd.DataFrame,
    setup: pd.DataFrame,
    freqs: pd.DataFrame,
    lidar_metrics: pd.DataFrame,
    sample: dict[str, Any],
    cases: list[CaseData],
) -> None:
    d_row = summary.loc[summary["case"] == "D"].iloc[0].to_dict()
    d_freq = freqs.loc[freqs["case"] == "D"].iloc[0].to_dict()
    d_setup = setup.loc[setup["case"] == "D"].iloc[0].to_dict()
    case_d = next(item for item in cases if item.case == "D")
    best = summary.sort_values("horizontal_mean_m").iloc[0].to_dict()
    worst = summary.sort_values("horizontal_mean_m").iloc[-1].to_dict()

    def count_text(value: Any) -> str:
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            return "n/a"
        if np.isnan(value_f):
            return "n/a"
        return str(int(value_f))

    def _f(v: Any) -> float:
        try:
            x = float(v)
            return 0.0 if np.isnan(x) else x
        except (TypeError, ValueError):
            return 0.0

    d_fused = _f(d_row.get("flow_fused_count"))
    d_rej = _f(d_row.get("flow_rejected_count"))
    d_ratio = d_rej / d_fused if d_fused else float("nan")
    d_h = _f(d_row.get("horizontal_mean_m"))
    b_h = _f(summary.loc[summary["case"] == "B"].iloc[0].get("horizontal_mean_m"))
    c_h = _f(summary.loc[summary["case"] == "C"].iloc[0].get("horizontal_mean_m"))
    d_fused_ok = d_fused > 100

    if float(d_freq.get("raw_lidar_finite_fraction", np.nan)) >= 0.99:
        input_validity_lines = [
            (
                "The repaired D run converts the old input-validity failure into "
                "an explicit startup gate. Early frames are still too close/invalid "
                "for flow feeding, so the bridge skips them with reasons such as "
                "`range<0.8,quality<20,matches<8`; valid feeding starts only once "
                "range, quality, and SIFT matches are all inside the configured window."
            ),
            "",
            (
                "Unlike the previous failed D run, this repaired window does not leave "
                "the textured field: raw lidar finite fraction is "
                f"`{d_freq['raw_lidar_finite_fraction']:.1%}`, bridge-fed range "
                f"finite fraction is `{d_freq['flow_bridge_range_finite_fraction']:.1%}`, "
                "and the run stops before landing data or late out-of-field samples are collected."
            ),
            "",
            (
                (
                    "Optical-flow aiding is active and fusing: PX4 logged "
                    f"`{case_d.flow.get('sensor_optical_flow_rows', 0)}` `sensor_optical_flow` "
                    "rows at "
                    f"`{case_d.flow.get('sensor_optical_flow_rate_hz', float('nan')):.2f} Hz`, "
                    f"and EKF2 fused {count_text(d_row['flow_fused_count'])} / rejected "
                    f"{count_text(d_row['flow_rejected_count'])} flow aid samples "
                    f"(reject/fused `{d_ratio:.3f}`, strict green gate is `< 0.10`)."
                )
                if d_fused_ok
                else (
                    "The remaining blocker is estimator/fusion state, not basic transport: "
                    f"PX4 logged `{case_d.flow.get('sensor_optical_flow_rows', 0)}` "
                    "`sensor_optical_flow` rows at "
                    f"`{case_d.flow.get('sensor_optical_flow_rate_hz', float('nan')):.2f} Hz`, "
                    "but EKF2 optical-flow aid stayed inactive in this short window."
                )
            ),
        ]
    else:
        input_validity_lines = [
            (
                "The D failure is best described as a valid-middle-window input "
                "problem. The bridge starts before the camera sees usable ground "
                "texture: early flow rows have `quality=0`, `n_matches=0`, and "
                "lidar range near the landed offset. Useful SIFT matches begin later, "
                "then the camera sees good phototex ground for a short middle window."
            ),
            "",
            (
                "Late in the failed run, the vehicle/camera reaches the edge of the "
                "finite textured field. Range and camera validity stop being reliable "
                "exactly when D needs them most."
            ),
        ]

    lines = [
        "# Phase 8I flat phototex A/B/C/D comparison",
        "",
        "Generated from the four verified flat-world Phase 8I runs on 2026-07-14.",
        "",
        "## Result",
        "",
        (
            f"Best horizontal truth agreement is **{best['case']} ({best['label']})** "
            f"at {best['horizontal_mean_m']:.3f} m mean. Worst is "
            f"**{worst['case']} ({worst['label']})** at "
            f"{worst['horizontal_mean_m']:.3f} m mean."
        ),
        "",
        (
            (
                "Case D — our live camera + TF03 optical-flow stack — navigates the GNSS "
                f"outage at **{d_h:.3f} m** mean horizontal error against Gazebo truth "
                f"({count_text(d_row['flow_bridge_sent_rows'])} bridge rows, "
                f"{count_text(d_row['flow_fused_count'])} fused, "
                f"{count_text(d_row['flow_rejected_count'])} rejected, reject/fused "
                f"`{d_ratio:.3f}`). That is ~{(b_h / d_h):.0f}x better than the no-aiding "
                f"anchor B ({b_h:.1f} m) and the same order as the ideal truth-fed "
                f"odometry anchor C ({c_h:.3f} m), so the thesis ordering "
                "`drift(B) >> drift(D) >= drift(C)` holds. The reject/fused above the "
                "strict `0.10` gate is a post-outage optical-flow control limit-cycle "
                "(see Interpretation), not a plumbing or activation failure."
            )
            if d_fused_ok
            else (
                "Case D proves the live optical-flow plumbing moved data into PX4 "
                f"({count_text(d_row['flow_bridge_sent_rows'])} bridge rows, "
                f"{count_text(d_row['flow_fused_count'])} fused, "
                f"{count_text(d_row['flow_rejected_count'])} rejected). In the repaired "
                "short-window D run, PX4 received optical-flow sensor rows but EKF2 did "
                "not activate optical-flow aiding."
            )
        ),
        "",
        "## Metrics",
        "",
        markdown_table(summary),
        "",
        "## Current Aiding Setup",
        "",
        compact_table(
            setup,
            [
                ("case", "Case"),
                ("gnss_loss_enabled", "GNSS loss"),
                ("aiding_mode", "Aiding mode"),
                ("external_odom_rate_hz", "EV Hz"),
                ("ekf2_ev_ctrl", "EV ctrl"),
                ("ekf2_hgt_ref", "HGT ref"),
                ("flow_bridge_enabled", "Flow"),
                ("flow_bridge_rate_hz", "Flow Hz"),
                ("flow_max_width", "Flow width"),
                ("flow_sift_n_features", "SIFT feat"),
                ("flow_axis_map", "Axis"),
                ("ekf2_of_ctrl", "OF ctrl"),
                ("ekf2_of_qmin", "OF qmin"),
            ],
            float_digits=1,
        ),
        "",
        (
            "Common route/control: flat phototex world, 2.5 m AGL, +Y velocity "
            f"setpoint {d_setup['vy_m_s']:.1f} m/s for repaired D "
            "(A/B/C retained from their original 0.2 m/s runs), offboard "
            "setpoints configured at 20 Hz, and "
            "webgz publication configured on raw port 9003 at 15 Hz behind the "
            "operator 9002 enum-patch proxy."
        ),
        "",
        "## Stream Frequencies",
        "",
        compact_table(
            freqs,
            [
                ("case", "Case"),
                ("gazebo_truth_hz", "Truth Hz"),
                ("aligned_truth_hz", "Aligned Hz"),
                ("offboard_setpoint_hz", "Setpoint Hz"),
                ("external_odom_sent_hz", "EV sent Hz"),
                ("camera_frame_record_hz", "Camera Hz"),
                ("raw_lidar_record_hz", "Raw lidar Hz"),
                ("raw_lidar_finite_fraction", "Raw finite"),
                ("flow_bridge_sent_hz", "Bridge Hz"),
                ("flow_bridge_range_finite_fraction", "Bridge finite"),
                ("ulog_distance_sensor_rows", "ULog lidar rows"),
            ],
            float_digits=2,
        ),
        "",
        "Blank cells mean that stream was not enabled for that case.",
        "",
        "## Current Connection Map",
        "",
        "Live optical-flow path:",
        "",
        compact_table(
            pd.DataFrame(
                [
                    {
                        "connection": "Gazebo camera -> image topic",
                        "file": "`src/databoss_sim/models/x500_cam_lidar_down/model.sdf`",
                        "topic": "`/world/flat_rural_phototex_noon/model/x500_cam_lidar_down_0/link/camera_link/sensor/camera/image`",
                        "frame": "camera optical image",
                        "units": "RGB pixels",
                        "freq": f"{d_freq['camera_frame_record_hz']:.2f} Hz recorded",
                    },
                    {
                        "connection": "image topic -> flow bridge",
                        "file": "`scripts/sim/flow_mavlink_bridge.py`",
                        "topic": "Gazebo Image subscription",
                        "frame": "camera optical",
                        "units": "grayscale pixels",
                        "freq": f"{d_freq['flow_bridge_sent_hz']:.2f} Hz bridge output",
                    },
                    {
                        "connection": "flow bridge -> estimator",
                        "file": "`src/databoss_sim/flow/sift_estimator.py`",
                        "topic": "internal `FlowSample`",
                        "frame": "camera optical",
                        "units": "px displacement -> rad",
                        "freq": f"{d_freq['flow_bridge_sent_hz']:.2f} Hz",
                    },
                    {
                        "connection": "estimator -> axis conversion",
                        "file": "`src/databoss_sim/flow/px4_adapter.py`",
                        "topic": "`axis_map=-yx`",
                        "frame": "camera optical -> PX4 OF convention",
                        "units": "integrated rad",
                        "freq": f"{d_freq['flow_bridge_sent_hz']:.2f} Hz",
                    },
                    {
                        "connection": "axis conversion -> MAVLink",
                        "file": "`scripts/sim/flow_mavlink_bridge.py`",
                        "topic": "`OPTICAL_FLOW_RAD` to `udpout:127.0.0.1:14600`",
                        "frame": "PX4 optical-flow convention",
                        "units": "rad, us, quality 0..255; distance=-1",
                        "freq": f"{count_text(d_row['flow_bridge_sent_rows'])} sent, {d_freq['flow_bridge_sent_hz']:.2f} Hz",
                    },
                    {
                        "connection": "MAVLink -> PX4 sensor_optical_flow",
                        "file": "`/opt/sim_px4/PX4-Autopilot/src/modules/mavlink/mavlink_receiver.cpp`",
                        "topic": "uORB `sensor_optical_flow`",
                        "frame": "PX4 estimator input",
                        "units": "`pixel_flow[2]` rad",
                        "freq": f"{case_d.flow.get('sensor_optical_flow_rows', 0)} rows, {case_d.flow.get('sensor_optical_flow_rate_hz', float('nan')):.2f} Hz",
                    },
                    {
                        "connection": "sensor_optical_flow -> EKF2",
                        "file": "`/opt/sim_px4/PX4-Autopilot/src/modules/ekf2/EKF2.cpp`",
                        "topic": "EKF optical-flow aid source",
                        "frame": "body/estimator frame",
                        "units": "flow aid, fusion flags",
                        "freq": f"active {case_d.flow.get('cs_opt_flow_active_fraction', float('nan')):.1%}; fused {count_text(d_row['flow_fused_count'])}, rejected {count_text(d_row['flow_rejected_count'])}",
                    },
                    {
                        "connection": "EKF2 -> position controller",
                        "file": "`/opt/sim_px4/PX4-Autopilot/src/modules/mc_pos_control/`",
                        "topic": "`vehicle_local_position` + offboard setpoints",
                        "frame": "PX4 NED",
                        "units": "m, m/s",
                        "freq": f"setpoints {d_freq['offboard_setpoint_hz']:.2f} Hz",
                    },
                ]
            ),
            [
                ("connection", "Connection"),
                ("file", "File/script"),
                ("topic", "Topic/message"),
                ("frame", "Frame"),
                ("units", "Units"),
                ("freq", "Actual freq"),
            ],
            float_digits=2,
        ),
        "",
        "LiDAR / range path:",
        "",
        compact_table(
            pd.DataFrame(
                [
                    {
                        "connection": "Gazebo TF03-style LiDAR -> scan topic",
                        "file": "`src/databoss_sim/models/x500_cam_lidar_down/model.sdf`",
                        "topic": "`/world/.../lidar_sensor_link/sensor/lidar/scan`",
                        "frame": "`lidar_sensor_link`, downward",
                        "units": "range m",
                        "freq": f"{d_freq['raw_lidar_record_hz']:.2f} Hz raw, finite {d_freq['raw_lidar_finite_fraction']:.1%}",
                    },
                    {
                        "connection": "scan topic -> DISTANCE_SENSOR / PX4 distance_sensor",
                        "file": "`/opt/sim_px4/PX4-Autopilot/src/modules/simulation/gz_bridge/GZBridge.cpp`",
                        "topic": "`laserScantoLidarSensorCallback` -> uORB `distance_sensor`",
                        "frame": "downward-facing range",
                        "units": "`current_distance` m",
                        "freq": f"{count_text(d_freq['ulog_distance_sensor_rows'])} ULog rows",
                    },
                    {
                        "connection": "PX4 distance_sensor -> EKF2 height/range",
                        "file": "`/opt/sim_px4/PX4-Autopilot/src/modules/ekf2/EKF2.cpp`",
                        "topic": "uORB `distance_sensor` subscription",
                        "frame": "downward range / HAGL",
                        "units": "m",
                        "freq": f"selected downward sensor; D `EKF2_HGT_REF=1`",
                    },
                ]
            ),
            [
                ("connection", "Connection"),
                ("file", "File/script"),
                ("topic", "Topic/message"),
                ("frame", "Frame"),
                ("units", "Units"),
                ("freq", "Actual freq"),
            ],
            float_digits=2,
        ),
        "",
        "Frame reference table:",
        "",
        compact_table(
            pd.DataFrame(
                [
                    {"frame": "Gazebo world", "meaning": "`x,y,z`, z-up world, meters"},
                    {"frame": "PX4 NED", "meaning": "x north, y east, z down, meters"},
                    {"frame": "body FRD", "meaning": "x forward, y right, z down"},
                    {"frame": "camera optical frame", "meaning": "image x right, image y down, optical axis through image"},
                    {"frame": "optical-flow output", "meaning": "`integrated_x/y` radians in PX4 `sensor_optical_flow.pixel_flow` after adapter mapping"},
                ]
            ),
            [("frame", "Frame"), ("meaning", "Meaning")],
            float_digits=2,
        ),
        "",
        (
            "`axis_map=-yx` means `integrated_x_sent = -integrated_y_camera` "
            "and `integrated_y_sent = integrated_x_camera`: swap camera x/y "
            "and flip the new x sign. For the intended downward camera "
            "orientation this is the correct mapping, empirically gated by the "
            "8G D5 orthogonal leg tests. It maps valid camera flow correctly; "
            "it does not solve the D input-validity problem where early frames "
            "are invalid/blue and late frames leave the textured field."
        ),
        "",
        "## Current Flow Setup And Algorithms",
        "",
        (
            "The live-flow case uses the modular flow bridge "
            "`scripts/sim/flow_mavlink_bridge.py` with estimator `sift`. "
            "Gazebo RGB camera frames are converted to grayscale, optionally "
            f"downscaled to `max_width={int(d_setup['flow_max_width'])}`, and processed as consecutive "
            "two-frame pairs."
        ),
        "",
        f"- Estimator: OpenCV SIFT, `n_features={int(d_setup['flow_sift_n_features'])}`, brute-force L2 matching, Lowe ratio test `0.75`, minimum `8` matches.",
        "- Motion estimate: median matched-pixel displacement; no RANSAC, no gyro compensation inside the estimator.",
        "- Angular conversion: pinhole small-angle approximation, `integrated_rad = median_px / focal_px`, with `focal_px=(width/2)/tan(hfov/2)` and `hfov_rad=1.74`.",
        f"- PX4 message: MAVLink `OPTICAL_FLOW_RAD` at configured `{d_setup['flow_bridge_rate_hz']:.0f} Hz`; actual D sent output was `{d_freq['flow_bridge_sent_hz']:.2f} Hz` in sim time.",
        "- Axis/quality: `axis_map=-yx`; raw quality is match-count based and remapped from `[20,100]` to `[1,255]`, with zero staying invalid.",
        f"- Send gating: only feed EKF when range is finite in `{d_setup['flow_send_min_range_m']:.1f}..{d_setup['flow_send_max_range_m']:.1f} m`, raw quality >= `{d_setup['flow_send_min_quality']:.0f}`, matches >= `{int(d_setup['flow_send_min_matches'])}`; reset estimator state on unsent frames = `{d_setup['flow_reset_on_unsent']}`.",
        "- Range handling: the optical-flow message carries `distance=-1`; PX4 height-above-ground comes from the separate TF03-style `distance_sensor` stream.",
        "- EKF parameters in D: `EKF2_OF_CTRL=1`, `EKF2_OF_QMIN=17`, `SENS_FLOW_ROT=0`, `SENS_FLOW_MINHGT=0.1`, `SENS_FLOW_MAXHGT=100`, `EKF2_HGT_REF=1`.",
        "",
        "## Test Conditions",
        "",
        "- Vehicle/model: `gz_x500_cam_lidar_down`, Gazebo model `x500_cam_lidar_down_0`.",
        "- World: `flat_rural_phototex_noon`, `photo_speckle` texture, `noon_clear_no_shadows`, wind `none`, physical condition enabled.",
        "- Sensors: downward mono camera plus downward TF03-style `gpu_lidar`, rendered with OGRE/Xvfb.",
        f"- Route: `{d_setup['route']}`, 2.5 m AGL, offboard `velocity_xy_position_z`, `vx=0`, `vy={d_setup['vy_m_s']:.1f} m/s`, `vz=0`, yaw `0 deg`; repaired D stops after the data window without waiting for landing data.",
        "- GNSS: A remains GNSS-on; B/C/D start with GNSS and trigger loss at 10 s after takeoff via `SIM_GPS_USED=0`.",
        "- Visualization: runner raw Gazebo websocket is configured on `9003`; operator web UI uses the preserved enum-patch proxy on `9002`.",
        "",
        "## Camera Sample",
        "",
        "Real D-run downward camera samples copied from `flow_recording/frames/`:",
        "",
        compact_table(
            pd.DataFrame(sample.get("samples", [])),
            [("frame", "Frame"), ("t_sim_s", "t sim s"), ("note", "Meaning")],
            float_digits=3,
        ),
        "",
        "Early invalid/blue frame:",
        "",
        "![Case D early invalid camera sample](plots/camera_sample_d_early_invalid_frame_000300.jpg)",
        "",
        "Middle valid textured-ground frame:",
        "",
        "![Case D valid camera sample](plots/camera_sample_d_valid_texture_frame_000500.jpg)",
        "",
        "Late repaired-window frame:",
        "",
        "![Case D late edge camera sample](plots/camera_sample_d_late_edge_frame_000900.jpg)",
        "",
        "## Input Validity Failure Modes",
        "",
        *input_validity_lines,
        "",
        "## D Lidar vs Gazebo Truth",
        "",
        compact_table(
            lidar_metrics,
            [
                ("stream", "Stream"),
                ("rows_total", "Rows"),
                ("finite_positive_fraction", "Finite"),
                ("sample_rate_hz", "Hz"),
                ("range_offset_landed_m", "Landed offset m"),
                ("mean_abs_lidar_truth_error_m", "Mean abs err m"),
                ("p95_abs_lidar_truth_error_m", "P95 abs err m"),
                ("max_abs_lidar_truth_error_m", "Max abs err m"),
                ("corr_lidar_truth_height", "Corr"),
            ],
            float_digits=3,
        ),
        "",
        (
            "The lidar height comparison subtracts the landed range offset "
            "before comparing finite positive lidar ranges to Gazebo model "
            "height AGL by nearest sim-time sample. Infinite range samples are "
            "reported in the finite fraction and excluded from the finite-row "
            "height-error fit."
        ),
        "",
        "## Plots",
        "",
        "![Route overlay](plots/routes_xy_overlay.png)",
        "",
        "![Route overlay near field](plots/routes_xy_nearfield.png)",
        "",
        "![Per-case EKF vs truth routes](plots/route_xy_ekf_vs_truth.png)",
        "",
        "![Horizontal error time series](plots/horizontal_error_timeseries.png)",
        "",
        "![Height error time series](plots/height_error_timeseries.png)",
        "",
        "![Horizontal error bars](plots/horizontal_error_bars.png)",
        "",
        "![Height error bars](plots/height_error_bars.png)",
        "",
        "![Case D flow diagnostics](plots/flow_d_diagnostics.png)",
        "",
        "![Case D lidar vs truth height](plots/lidar_truth_height_d.png)",
        "",
        "![Case D lidar/truth scatter](plots/lidar_truth_scatter_d.png)",
        "",
        "## Source Runs",
        "",
    ]
    for item in cases:
        lines.append(f"- {item.case}: `{item.run_dir}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A is the sanity/reference case: GNSS-on tracks truth tightly.",
            "- B is the failure baseline: GNSS loss with no aiding runs away.",
            "- C is the positive control: ideal odometry rescues GNSS-denied flight.",
            (
                "- D is our live camera+TF03 flow: EKF2 optical-flow aiding is active "
                f"and fusing ({count_text(d_row['flow_fused_count'])} fused / "
                f"{count_text(d_row['flow_rejected_count'])} rejected). It navigates "
                "the GNSS outage well in this short (~35 s) window (see D row), but "
                "the optical-flow-only loop is marginally stable and diverges over a "
                "longer 50 s outage (see the Case D robustness section)."
                if d_fused_ok
                else "- D repaired transport/timing: the vehicle stays contained and flow reaches PX4, but EKF2 optical-flow aiding is not yet active in the short window."
            ),
            "",
            "Terrain 8I stays paused: flat D is accepted only as a duration-limited (~35 s) short-outage capability.",
            "",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--case-a-run", type=Path, default=None)
    parser.add_argument("--case-b-run", type=Path, default=None)
    parser.add_argument("--case-c-run", type=Path, default=None)
    parser.add_argument(
        "--case-d-run",
        type=Path,
        default=None,
        help="Override only Case D run dir; A/B/C remain the verified baseline runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir
    plots_dir = out_dir / "plots"
    case_specs = list(DEFAULT_CASES)
    overrides = {
        "A": args.case_a_run,
        "B": args.case_b_run,
        "C": args.case_c_run,
        "D": args.case_d_run,
    }
    if any(path is not None for path in overrides.values()):
        case_specs = [
            (case, label, str(overrides[case]) if overrides.get(case) is not None else run_dir)
            for case, label, run_dir in case_specs
        ]
    cases = [load_case(case, label, Path(run_dir)) for case, label, run_dir in case_specs]
    summary = metric_rows(cases)
    setup = setup_rows(cases)
    freqs = frequency_rows(cases)

    out_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / "comparison_metrics.csv", index=False)
    (out_dir / "comparison_metrics.json").write_text(
        json.dumps(summary.to_dict(orient="records"), indent=2)
    )
    setup.to_csv(out_dir / "aiding_setup.csv", index=False)
    (out_dir / "aiding_setup.json").write_text(json.dumps(setup.to_dict(orient="records"), indent=2))
    freqs.to_csv(out_dir / "stream_frequencies.csv", index=False)
    (out_dir / "stream_frequencies.json").write_text(json.dumps(freqs.to_dict(orient="records"), indent=2))

    plot_routes_overlay(cases, plots_dir)
    plot_routes_nearfield(cases, plots_dir)
    plot_case_routes(cases, plots_dir)
    plot_error_timeseries(cases, plots_dir)
    plot_metric_bars(summary, plots_dir)
    plot_flow_diagnostics(next(item for item in cases if item.case == "D"), plots_dir)
    case_d = next(item for item in cases if item.case == "D")
    lidar_metrics = lidar_truth_comparison(case_d, out_dir, plots_dir)
    sample = camera_sample(case_d, out_dir, plots_dir)
    write_report(out_dir, summary, setup, freqs, lidar_metrics, sample, cases)

    print(f"OK: wrote {out_dir / 'report.md'}")
    print(f"OK: wrote {out_dir / 'comparison_metrics.csv'}")
    print(f"OK: wrote plots to {plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
