#!/usr/bin/env python3
"""Generate Phase 8I optical-flow run plots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyulog import ULog


C_EKF = "#2563eb"
C_TRUTH = "#16a34a"
C_FLOW = "#7c3aed"
C_BAD = "#dc2626"
C_RANGE = "#ea580c"
C_DARK = "#111827"
C_GRAY = "#6b7280"


def rel_time(ulog: ULog, data: dict, field: str = "timestamp") -> np.ndarray:
    return (np.asarray(data[field], dtype=float) - float(ulog.start_timestamp)) / 1e6


def get_dataset(ulog: ULog, name: str) -> dict | None:
    try:
        return ulog.get_dataset(name).data
    except (KeyError, IndexError, ValueError):
        return None


def finish(fig: plt.Figure, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_route(run_dir: Path, out_dir: Path) -> Path:
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.plot(aligned["gz_x_rel"], aligned["gz_y_rel"], color=C_TRUTH, linewidth=2.4, label="Gazebo truth")
    ax.plot(aligned["px4_x_rel"], aligned["px4_y_rel"], color=C_EKF, linewidth=2.0, label="PX4 EKF")
    ax.scatter(aligned["gz_x_rel"].iloc[0], aligned["gz_y_rel"].iloc[0], color=C_TRUTH, s=45, marker="o", label="Truth start")
    ax.scatter(aligned["gz_x_rel"].iloc[-1], aligned["gz_y_rel"].iloc[-1], color=C_TRUTH, s=55, marker="s", label="Truth end")
    ax.scatter(aligned["px4_x_rel"].iloc[0], aligned["px4_y_rel"].iloc[0], color=C_EKF, s=45, marker="o", label="EKF start")
    ax.scatter(aligned["px4_x_rel"].iloc[-1], aligned["px4_y_rel"].iloc[-1], color=C_EKF, s=55, marker="s", label="EKF end")
    ax.set_title("Route: PX4 EKF vs Gazebo Truth")
    ax.set_xlabel("X relative (m)")
    ax.set_ylabel("Y relative (m)")
    ax.axis("equal")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best", fontsize=8)
    out = out_dir / "phase8i_route_ekf_vs_gazebo.png"
    finish(fig, out)
    return out


def plot_error(run_dir: Path, out_dir: Path) -> Path:
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    t = aligned["px4_t_rel_s"]
    fig, ax = plt.subplots(figsize=(11, 5.8))
    ax.plot(t, aligned["horizontal_error_m"], color=C_BAD, linewidth=2.0, label="Horizontal error")
    ax.plot(t, aligned["abs_height_error_m"], color=C_RANGE, linewidth=1.8, label="Height abs error")
    ax.plot(t, aligned["error_3d_m"], color=C_DARK, linewidth=1.5, alpha=0.75, label="3D error")
    ax.set_title("EKF vs Gazebo Truth Error")
    ax.set_xlabel("PX4 relative time (s)")
    ax.set_ylabel("Error (m)")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best")
    out = out_dir / "phase8i_ekf_truth_error_timeseries.png"
    finish(fig, out)
    return out


def plot_height(run_dir: Path, out_dir: Path, ulog: ULog) -> Path:
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    dist = get_dataset(ulog, "distance_sensor")
    fig, ax = plt.subplots(figsize=(11, 5.8))
    ax.plot(aligned["px4_t_rel_s"], aligned["gz_height_up"], color=C_TRUTH, linewidth=2.3, label="Gazebo truth height")
    ax.plot(aligned["px4_t_rel_s"], aligned["px4_height_up"], color=C_EKF, linewidth=1.9, label="PX4 EKF height")
    if dist is not None and "current_distance" in dist:
        t = rel_time(ulog, dist)
        ax.plot(t, dist["current_distance"], color=C_RANGE, linewidth=1.3, alpha=0.85, label="PX4 distance_sensor")
    ax.set_title("Height and Downward LiDAR")
    ax.set_xlabel("PX4 relative time (s)")
    ax.set_ylabel("Height / range (m)")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best")
    out = out_dir / "phase8i_height_lidar_truth_ekf.png"
    finish(fig, out)
    return out


def plot_flow_delivery(run_dir: Path, out_dir: Path) -> Path:
    flow = pd.read_csv(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
    t = flow["t_frame_sim_s"]
    real = flow["sent"].astype(bool)
    prime = flow["mavlink_sent"].astype(bool) & ~real if "mavlink_sent" in flow else ~real

    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True)
    axes[0].plot(t, flow["range_m"], color=C_RANGE, linewidth=1.8)
    axes[0].axhline(0.8, color=C_GRAY, linestyle="--", linewidth=1.0, label="send_min_range=0.8m")
    axes[0].set_ylabel("Range (m)")
    axes[0].legend(loc="best", fontsize=8)

    axes[1].plot(t, flow["quality_sent"], color=C_FLOW, linewidth=1.5, label="quality_sent")
    axes[1].plot(t, flow["quality_raw"], color=C_GRAY, linewidth=1.0, alpha=0.55, label="quality_raw")
    axes[1].set_ylabel("Quality")
    axes[1].legend(loc="best", fontsize=8)

    axes[2].plot(t, flow["n_matches"], color=C_DARK, linewidth=1.5)
    axes[2].axhline(8, color=C_GRAY, linestyle="--", linewidth=1.0, label="min_matches=8")
    axes[2].set_ylabel("Matches")
    axes[2].legend(loc="best", fontsize=8)

    axes[3].scatter(t[prime], np.zeros(int(prime.sum())), color=C_GRAY, s=12, alpha=0.55, label="prime zero-quality MAVLink")
    axes[3].scatter(t[real], np.ones(int(real.sum())), color=C_FLOW, s=12, alpha=0.85, label="real sent flow")
    axes[3].set_yticks([0, 1], ["prime", "sent"])
    axes[3].set_xlabel("Gazebo sim time (s)")
    axes[3].set_ylabel("Bridge send")
    axes[3].legend(loc="best", fontsize=8)

    fig.suptitle("Optical-Flow Bridge Delivery, Range Gate, Quality, and Matches")
    for ax in axes:
        ax.grid(True, alpha=0.24)
    out = out_dir / "phase8i_flow_delivery_quality_range.png"
    finish(fig, out)
    return out


def plot_flow_fusion(run_dir: Path, out_dir: Path, ulog: ULog) -> Path:
    aid = get_dataset(ulog, "estimator_aid_src_optical_flow")
    flags = get_dataset(ulog, "estimator_status_flags")
    if aid is None:
        raise SystemExit("missing estimator_aid_src_optical_flow")
    t = rel_time(ulog, aid)
    fused = np.asarray(aid["fused"], dtype=bool)
    rejected = np.asarray(aid["innovation_rejected"], dtype=bool)

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].scatter(t[fused], np.ones(int(fused.sum())), s=12, color=C_FLOW, label="fused")
    axes[0].scatter(t[rejected], np.zeros(int(rejected.sum())), s=14, color=C_BAD, label="innovation rejected")
    axes[0].set_yticks([0, 1], ["rejected", "fused"])
    axes[0].set_ylabel("Aid status")
    axes[0].legend(loc="best", fontsize=8)

    axes[1].plot(t, aid["test_ratio[0]"], color=C_EKF, linewidth=1.3, label="test_ratio[0]")
    axes[1].plot(t, aid["test_ratio[1]"], color=C_RANGE, linewidth=1.3, label="test_ratio[1]")
    axes[1].axhline(1.0, color=C_BAD, linestyle="--", linewidth=1.0, label="reject threshold")
    axes[1].set_ylabel("Test ratio")
    axes[1].legend(loc="best", fontsize=8)

    axes[2].plot(t, aid["innovation[0]"], color=C_EKF, linewidth=1.2, label="innovation[0]")
    axes[2].plot(t, aid["innovation[1]"], color=C_RANGE, linewidth=1.2, label="innovation[1]")
    if flags is not None and "cs_opt_flow" in flags:
        tf = rel_time(ulog, flags)
        cs = np.asarray(flags["cs_opt_flow"], dtype=float)
        ax2 = axes[2].twinx()
        ax2.step(tf, cs, where="post", color=C_FLOW, alpha=0.35, linewidth=2.0, label="cs_opt_flow")
        ax2.set_ylabel("OF active")
        ax2.set_ylim(-0.05, 1.05)
    axes[2].set_xlabel("PX4 relative time (s)")
    axes[2].set_ylabel("Innovation")
    axes[2].legend(loc="best", fontsize=8)

    fig.suptitle("EKF Optical-Flow Aid Source: Fusion, Rejection, and Innovations")
    for ax in axes:
        ax.grid(True, alpha=0.24)
    out = out_dir / "phase8i_ekf_flow_fusion_innovations.png"
    finish(fig, out)
    return out


def plot_gnss_flow_timeline(run_dir: Path, out_dir: Path, ulog: ULog) -> Path:
    gps = get_dataset(ulog, "vehicle_gps_position")
    flow = get_dataset(ulog, "sensor_optical_flow")
    flags = get_dataset(ulog, "estimator_status_flags")
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True)
    if gps is not None:
        t = rel_time(ulog, gps)
        if "satellites_used" in gps:
            axes[0].plot(t, gps["satellites_used"], color=C_TRUTH, linewidth=1.8, label="satellites_used")
        if "fix_type" in gps:
            axes[0].step(t, gps["fix_type"], color=C_EKF, linewidth=1.1, alpha=0.7, label="fix_type")
    axes[0].set_ylabel("GNSS")
    axes[0].legend(loc="best", fontsize=8)

    if flow is not None:
        t = rel_time(ulog, flow)
        axes[1].plot(t, flow["quality"], color=C_FLOW, linewidth=1.4, label="sensor_optical_flow quality")
    axes[1].set_ylabel("Flow quality")
    axes[1].legend(loc="best", fontsize=8)

    if flags is not None:
        t = rel_time(ulog, flags)
        for name, color in [("cs_gnss_pos", C_TRUTH), ("cs_opt_flow", C_FLOW), ("cs_inertial_dead_reckoning", C_BAD)]:
            if name in flags:
                axes[2].step(t, flags[name], where="post", linewidth=1.6, label=name, color=color)
    axes[2].set_xlabel("PX4 relative time (s)")
    axes[2].set_ylabel("EKF status")
    axes[2].set_ylim(-0.05, 1.05)
    axes[2].legend(loc="best", fontsize=8)

    fig.suptitle("GNSS Loss and Optical-Flow Fusion Timeline")
    for ax in axes:
        ax.grid(True, alpha=0.24)
    out = out_dir / "phase8i_gnss_flow_timeline.png"
    finish(fig, out)
    return out


def write_manifest(run_dir: Path, out_dir: Path, plots: list[Path], ulog: ULog) -> Path:
    status_path = run_dir / "logs" / "pxh_takeoff_land_truth_status.json"
    metrics_path = run_dir / "ekf_vs_ground_truth_metrics.json"
    flow_csv = pd.read_csv(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
    aid = get_dataset(ulog, "estimator_aid_src_optical_flow")
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    real_sent = int(flow_csv["sent"].sum()) if "sent" in flow_csv else 0
    mav_sent = int(flow_csv["mavlink_sent"].sum()) if "mavlink_sent" in flow_csv else real_sent
    fused = rejected = None
    if aid is not None:
        fused = int(np.asarray(aid.get("fused", []), dtype=int).sum())
        rejected = int(np.asarray(aid.get("innovation_rejected", []), dtype=int).sum())

    h_err = metrics.get("horizontal_error", {})
    lines = [
        "# Phase 8I Last Run Plots",
        "",
        f"- Run: `{run_dir}`",
        f"- Accepted: `{status.get('accepted')}`",
        f"- Effective GNSS loss after takeoff: `{status.get('effective_gnss_loss_after_takeoff_s')}` s",
        f"- Flow real sent: `{real_sent}`",
        f"- Flow prime packets: `{mav_sent - real_sent}`",
        f"- EKF flow fused / rejected: `{fused}` / `{rejected}`",
        f"- Horizontal error mean/max: `{h_err.get('mean_m')}` / `{h_err.get('max_m')}` m",
        "",
        "## Plots",
        "",
    ]
    for p in plots:
        lines.append(f"- [{p.name}]({p.name})")
    manifest = out_dir / "phase8i_last_run_plots.md"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--out-subdir", default="phase8i_last_run")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    out_dir = run_dir / "plots" / args.out_subdir
    ulog = ULog(str(run_dir / "logs" / "flight.ulg"))

    plots = [
        plot_route(run_dir, out_dir),
        plot_error(run_dir, out_dir),
        plot_height(run_dir, out_dir, ulog),
        plot_flow_delivery(run_dir, out_dir),
        plot_flow_fusion(run_dir, out_dir, ulog),
        plot_gnss_flow_timeline(run_dir, out_dir, ulog),
    ]
    manifest = write_manifest(run_dir, out_dir, plots, ulog)
    print(f"plot_dir={out_dir}")
    for plot in plots:
        print(f"plot={plot}")
    print(f"manifest={manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
