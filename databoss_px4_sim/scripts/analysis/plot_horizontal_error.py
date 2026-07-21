#!/usr/bin/env python3
"""Plot EKF-vs-Gazebo-truth horizontal error over time for one or more runs.

Usage: plot_horizontal_error.py <run_dir> [<run_dir> ...] [--out output.png]

One run dir: single-series plot saved to <run_dir>/plots/horizontal_error.png.
Several run dirs: overlay comparison on one axis (same PX4-relative timeline);
--out is then required.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Reference categorical palette, fixed slot order (dataviz skill).
SERIES_COLORS = ["#2a78d6", "#1baf7a", "#eda100"]
INK = "#333333"
INK_MUTED = "#767676"


def short_label(run_dir: Path) -> str:
    """20260710_180752_phase8f_flow_rec_flat_rural_high_texture_noon_pxh_... ->
    flat_rural_high_texture_noon"""
    name = run_dir.name
    m = re.search(r"phase\w+?_(?:flow_rec_|cam_lidar_|camera_)?(.+?)_pxh", name)
    return m.group(1) if m else name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--out", help="output PNG (required for several runs)")
    args = parser.parse_args()

    run_dirs = [Path(r).resolve() for r in args.run_dirs]
    if len(run_dirs) > len(SERIES_COLORS):
        parser.error(f"at most {len(SERIES_COLORS)} runs per plot")
    if len(run_dirs) > 1 and not args.out:
        parser.error("--out is required when comparing several runs")
    out_path = (
        Path(args.out).resolve()
        if args.out
        else run_dirs[0] / "plots" / "horizontal_error.png"
    )

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    for run_dir, color in zip(run_dirs, SERIES_COLORS):
        aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
        t = aligned["px4_t_rel_s"]
        err = aligned["horizontal_error_m"]
        label = f"{short_label(run_dir)} (mean {err.mean():.3f} m, max {err.max():.3f} m)"
        ax.plot(t, err, color=color, linewidth=2, label=label)
        print(f"{run_dir.name}: mean {err.mean():.4f} m, max {err.max():.4f} m, "
              f"rows {len(err)}")

    if len(run_dirs) == 1:
        title = f"EKF horizontal error vs Gazebo truth — {short_label(run_dirs[0])}"
        err = pd.read_csv(run_dirs[0] / "ekf_vs_ground_truth_aligned.csv")["horizontal_error_m"]
        ax.annotate(
            f"mean {err.mean():.3f} m   max {err.max():.3f} m",
            xy=(0.99, 0.97), xycoords="axes fraction",
            ha="right", va="top", color=INK_MUTED, fontsize=10,
        )
    else:
        title = "EKF horizontal error vs Gazebo truth — run comparison"
        ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK)

    ax.set_title(title, color=INK, fontsize=12, loc="left")
    ax.set_xlabel("PX4 time since log start (s)", color=INK)
    ax.set_ylabel("Horizontal error (m)", color=INK)
    ax.set_ylim(bottom=0)
    ax.grid(True, color="#dddddd", linewidth=0.6)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(INK_MUTED)
    ax.tick_params(colors=INK_MUTED)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
