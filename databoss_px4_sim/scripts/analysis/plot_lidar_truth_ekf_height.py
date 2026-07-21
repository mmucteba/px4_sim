#!/usr/bin/env python3
"""Plot lidar distance vs Gazebo truth height vs EKF height for one run.

Usage: plot_lidar_truth_ekf_height.py <run_dir> [output.png]
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyulog import ULog

# Reference categorical palette, fixed slot order (dataviz skill).
C_LIDAR = "#2a78d6"   # slot 1 blue
C_TRUTH = "#1baf7a"   # slot 2 aqua
C_EKF = "#eda100"     # slot 3 yellow
INK = "#333333"
INK_MUTED = "#767676"


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()
    out_path = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) > 2
        else run_dir / "plots" / "lidar_vs_truth_vs_ekf_height.png"
    )

    ulog = ULog(str(run_dir / "logs" / "flight.ulg"))
    dist = ulog.get_dataset("distance_sensor").data
    vlp = ulog.get_dataset("vehicle_local_position").data

    # Zero the PX4 clock at the first local-position sample — the same origin
    # the truth-alignment postprocess uses for px4_t_rel_s.
    t0 = float(vlp["timestamp"][0])
    lidar_t = (np.asarray(dist["timestamp"], dtype=float) - t0) / 1e6
    lidar_m = np.asarray(dist["current_distance"], dtype=float)

    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    ax.plot(lidar_t, lidar_m, color=C_LIDAR, linewidth=2, label="Lidar distance (distance_sensor)")
    ax.plot(
        aligned["px4_t_rel_s"], aligned["gz_height_up"],
        color=C_TRUTH, linewidth=2, label="Gazebo truth height",
    )
    ax.plot(
        aligned["px4_t_rel_s"], aligned["px4_height_up"],
        color=C_EKF, linewidth=2, linestyle="--", label="EKF height (vehicle_local_position)",
    )

    ax.set_title(
        "Downward lidar vs Gazebo truth vs EKF height — each in its own reference frame",
        color=INK, fontsize=12, loc="left",
    )
    ax.set_xlabel("PX4 time since log start (s)", color=INK)
    ax.set_ylabel("Height / range above launch pad (m)", color=INK)
    ax.grid(True, color="#dddddd", linewidth=0.6)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(INK_MUTED)
    ax.tick_params(colors=INK_MUTED)
    ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    print(f"wrote {out_path}")

    # Console summary for the log
    hover = lidar_m[np.isfinite(lidar_m) & (lidar_m > 1.0)]
    if len(hover):
        print(f"lidar hover samples: {len(hover)}, median {np.median(hover):.4f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
