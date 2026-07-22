#!/usr/bin/env python3
"""Plot error metrics against altitude for a manifest-driven comparison.

Complements plot_unified_comparison.py, which plots one case per line over
flight time. This script instead puts altitude_agl_m on the x-axis and one
line per (algorithm, gnss_state) series on the y-axis, so an altitude sweep
(one manifest built from several phases' accepted runs, e.g. Phase 14a/b/c)
reads as a single degradation curve per algorithm instead of N separate
per-phase reports. Replicates at the same altitude are plotted as individual
points plus a mean line, so replicate spread is visible rather than averaged
away silently.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/databoss-matplotlib")

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Same algorithm->color mapping as plot_unified_comparison.py, so this chart
# reads consistently with every other Phase 14 comparison plot.
ALGORITHM_COLOR = {"lk": "#1f77b4", "sift": "#2ca02c", "stock": "#d62728", "none": "#7f7f7f"}
ALGORITHM_LABEL = {"lk": "LK", "sift": "SIFT", "stock": "Stock", "none": "No-aid"}

# Sequential, one hue, light->dark: here color encodes altitude (a
# magnitude), not algorithm identity, so it deliberately does not reuse
# ALGORITHM_COLOR -- each per-algorithm panel below already names its
# algorithm in the title.
ALTITUDE_COLOR = {15.0: "#9ecae1", 35.0: "#4292c6", 60.0: "#08306b"}


def load_summary(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def series_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item["kind"], item["gnss_state"])


def plot_metric_panel(ax: plt.Axes, data: list[dict[str, Any]], metric: str, title: str, log_scale: bool) -> None:
    series: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for item in data:
        alt = item["config"]["altitude_agl_m"]
        val = item["metrics"][metric]
        if val is None:
            continue
        series.setdefault(series_key(item), []).append((alt, val))

    # Fixed draw order so color/linestyle assignment never depends on dict
    # iteration order: on-reference first (drawn under), then loss series.
    order = [("lk", "on"), ("lk", "loss"), ("sift", "loss"), ("stock", "loss"), ("none", "loss")]

    for kind, gnss_state in order:
        points = series.get((kind, gnss_state))
        if not points:
            continue
        color = ALGORITHM_COLOR[kind]
        linestyle = "-" if gnss_state == "loss" else ":"
        label = f"{ALGORITHM_LABEL[kind]} ({'loss' if gnss_state == 'loss' else 'on'})"

        # individual replicates, lightly, so spread is visible
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        ax.scatter(xs, ys, color=color, alpha=0.45, s=28, zorder=3)

        # mean per altitude, connected
        by_alt: dict[float, list[float]] = {}
        for x, y in points:
            by_alt.setdefault(x, []).append(y)
        mean_alts = sorted(by_alt)
        mean_vals = [float(np.mean(by_alt[a])) for a in mean_alts]
        ax.plot(mean_alts, mean_vals, color=color, linestyle=linestyle, linewidth=2.2, marker="o", markersize=5, label=label, zorder=4)

    ax.set_title(title, fontsize=11)
    ax.set_xlabel("Altitude AGL (m)")
    ax.set_xticks([15, 35, 60])
    if log_scale:
        ax.set_yscale("log")
    ax.grid(True, which="both", axis="both", alpha=0.25, linewidth=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def load_aligned(comparison_dir: Path, key: str) -> pd.DataFrame | None:
    path = comparison_dir / "data" / f"{key}_aligned.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return df if not df.empty else None


def plot_algorithm_altitude_timeseries(comparison_dir: Path, data: list[dict[str, Any]], plots_dir: Path) -> Path:
    """One subplot per algorithm; within a subplot, one line per altitude.

    This is the complement to plot_metric_panel above: that chart collapses
    each run to a single max-error point, this one keeps the full time
    series so *when* a run starts diverging is visible, not just how far it
    got by the end. GNSS-on references are excluded here (they already have
    a near-zero row in the summary table) so each panel is a clean
    same-algorithm, different-altitude comparison.
    """
    algorithms = ["lk", "sift", "stock", "none"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharey=True)
    axes_map = dict(zip(algorithms, axes.flat))

    seen_altitudes: set[float] = set()
    for item in data:
        if item["gnss_state"] != "loss" or item["kind"] not in axes_map:
            continue
        df = load_aligned(comparison_dir, item["key"])
        if df is None:
            continue
        alt = item["config"]["altitude_agl_m"]
        color = ALTITUDE_COLOR.get(alt, "#333333")
        seen_altitudes.add(alt)
        ax = axes_map[item["kind"]]
        ax.plot(df["px4_t_rel_s"], df["horizontal_error_m"].clip(lower=1e-3), color=color, linewidth=1.6, alpha=0.9)

    for kind, ax in axes_map.items():
        ax.set_title(f"{ALGORITHM_LABEL[kind]} (GNSS-loss)", fontsize=11)
        ax.set_yscale("log")
        ax.set_xlabel("Time since flight start (s)")
        ax.grid(True, which="both", alpha=0.25, linewidth=0.6)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    axes_map["lk"].set_ylabel("Horizontal error vs Gazebo truth (m, log)")
    axes_map["stock"].set_ylabel("Horizontal error vs Gazebo truth (m, log)")

    handles = [
        plt.Line2D([0], [0], color=ALTITUDE_COLOR[alt], linewidth=2.2, label=f"{int(alt)} m")
        for alt in sorted(seen_altitudes)
    ]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0), bbox_transform=fig.transFigure)
    fig.suptitle("Same algorithm, different altitude -- horizontal error over time (GNSS-loss cases, noon lighting)", fontsize=10)

    out_path = plots_dir / "algorithm_altitude_error_vs_time.png"
    fig.tight_layout(rect=(0, 0.07, 1, 0.94))
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison_dir", type=Path, help="Comparison folder containing summary.json")
    args = parser.parse_args()

    data = load_summary(args.comparison_dir / "summary.json")
    plots_dir = args.comparison_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.6))
    plot_metric_panel(ax1, data, "horizontal_error_max_m", "Max horizontal error vs Gazebo truth", log_scale=True)
    plot_metric_panel(ax2, data, "height_error_max_m", "Max height error vs Gazebo truth", log_scale=True)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0), bbox_transform=fig.transFigure)
    fig.suptitle("Phase 14 altitude sweep -- flat_rural_phototex, noon lighting (dots = individual replicates, line = mean)", fontsize=10)

    out_path = plots_dir / "altitude_sweep_error_vs_altitude.png"
    fig.tight_layout(rect=(0, 0.08, 1, 0.94))
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(out_path)

    per_algo_path = plot_algorithm_altitude_timeseries(args.comparison_dir, data, plots_dir)
    print(per_algo_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
