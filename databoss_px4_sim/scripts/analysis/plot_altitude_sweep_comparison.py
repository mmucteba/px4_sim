#!/usr/bin/env python3
"""Cross-cut the Phase 16a/16b/16c wind batches by altitude instead of by algorithm.

Where build_unified_comparison_report.py / plot_unified_comparison.py hold
altitude fixed and compare algorithms, this holds algorithm+wind-speed
("case_type") fixed and compares the same case across the three tested
altitudes (15/35/60 m) on one graph. Manifest schema: `case_types` (key +
label) and `runs` (case_type + altitude_m + run_dir) -- see
20260724_phase16_altitude_sweep/manifest.yaml.

Altitude is an ordered quantity, not a nominal category, so it gets a
single-hue sequential ramp (light->dark = low->high altitude) rather than
arbitrary categorical colors.
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
import pandas as pd
import yaml

PROJECT_ROOT = Path("/opt/databoss_px4_sim")

# ColorBrewer Blues (3-class), light->dark = 15m->60m: ordered/sequential,
# not arbitrary categorical hues, since altitude is an ordered quantity.
ALTITUDE_COLOR = {15: "#9ecae1", 35: "#4292c6", 60: "#08519c"}


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open() as f:
        raw = yaml.safe_load(f)
    case_types = {c["key"]: c.get("label", c["key"]) for c in raw.get("case_types", [])}
    runs_by_case: dict[str, dict[int, Path]] = {}
    for r in raw.get("runs", []):
        run_dir = Path(r["run_dir"])
        if not run_dir.is_absolute():
            run_dir = PROJECT_ROOT / run_dir
        runs_by_case.setdefault(r["case_type"], {})[int(r["altitude_m"])] = run_dir
    return {
        "name": raw.get("report", {}).get("name", path.parent.name),
        "title": raw.get("report", {}).get("title", "Altitude sweep comparison"),
        "case_types": case_types,
        "runs_by_case": runs_by_case,
    }


def load_run(run_dir: Path) -> dict[str, Any]:
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    metrics = json.loads((run_dir / "ekf_vs_ground_truth_metrics.json").read_text())
    return {"aligned": aligned, "metrics": metrics}


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()


def plot_horizontal_error_single(case_key: str, label: str, runs: dict[int, dict], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    for alt in sorted(runs):
        df = runs[alt]["aligned"]
        ax.plot(df["px4_t_rel_s"], df["horizontal_error_m"], color=ALTITUDE_COLOR[alt], lw=2, label=f"{alt} m")
    ax.set_xlabel("PX4 relative time (s)")
    ax.set_ylabel("horizontal EKF-vs-truth error (m)")
    ax.set_title(f"{label}\nhorizontal error across altitude")
    ax.legend(title="altitude")
    ax.grid(alpha=0.3)
    savefig(out_path)


def plot_route_single(case_key: str, label: str, runs: dict[int, dict], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 7))
    for alt in sorted(runs):
        df = runs[alt]["aligned"]
        ax.plot(df["gz_x_rel"], df["gz_y_rel"], color=ALTITUDE_COLOR[alt], lw=2, label=f"{alt} m truth")
    ax.set_xlabel("relative X / North (m)")
    ax.set_ylabel("relative Y / East (m)")
    ax.set_title(f"{label}\nGazebo-truth route across altitude")
    ax.legend(title="altitude")
    ax.grid(alpha=0.3)
    ax.set_aspect("equal", adjustable="datalim")
    savefig(out_path)


def plot_grid(kind: str, manifest: dict[str, Any], data: dict[str, dict[int, dict]], plots_dir: Path) -> None:
    case_keys = list(manifest["case_types"].keys())
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    for ax, case_key in zip(axes.flat, case_keys):
        runs = data[case_key]
        for alt in sorted(runs):
            df = runs[alt]["aligned"]
            if kind == "horizontal_error":
                ax.plot(df["px4_t_rel_s"], df["horizontal_error_m"], color=ALTITUDE_COLOR[alt], lw=1.6, label=f"{alt} m")
            else:
                ax.plot(df["gz_x_rel"], df["gz_y_rel"], color=ALTITUDE_COLOR[alt], lw=1.6, label=f"{alt} m")
        ax.set_title(manifest["case_types"][case_key], fontsize=10)
        ax.grid(alpha=0.3)
        if kind == "horizontal_error":
            ax.set_xlabel("t (s)")
            ax.set_ylabel("H error (m)")
        else:
            ax.set_xlabel("North (m)")
            ax.set_ylabel("East (m)")
            ax.set_aspect("equal", adjustable="datalim")
        ax.legend(fontsize=8, title="altitude")
    title = "Horizontal error vs time, by altitude" if kind == "horizontal_error" else "Gazebo-truth route, by altitude"
    fig.suptitle(f"{manifest['title']}\n{title}", fontsize=13)
    fig.tight_layout()
    savefig(plots_dir / f"altitude_sweep_grid_{kind}.png")


def plot_summary_bars(manifest: dict[str, Any], data: dict[str, dict[int, dict]], plots_dir: Path) -> None:
    case_keys = list(manifest["case_types"].keys())
    altitudes = [15, 35, 60]
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    width = 0.25
    x = range(len(case_keys))
    for ax, metric_key, title in [
        (axes[0], "mean_m", "Horizontal error -- mean (m)"),
        (axes[1], "max_m", "Horizontal error -- max (m)"),
    ]:
        for i, alt in enumerate(altitudes):
            vals = [data[ck][alt]["metrics"]["horizontal_error"][metric_key] for ck in case_keys]
            offsets = [xi + (i - 1) * width for xi in x]
            bars = ax.bar(offsets, vals, width=width, color=ALTITUDE_COLOR[alt], label=f"{alt} m")
            ax.bar_label(bars, fmt="%.1f", fontsize=7, padding=2)
        ax.set_xticks(list(x))
        ax.set_xticklabels([manifest["case_types"][ck] for ck in case_keys], rotation=25, ha="right", fontsize=8)
        ax.set_title(title)
        ax.legend(title="altitude")
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle(f"{manifest['title']}\nsummary metrics by altitude", fontsize=13)
    fig.tight_layout()
    savefig(plots_dir / "altitude_sweep_summary_bars.png")


def build_report(manifest: dict[str, Any], data: dict[str, dict[int, dict]], out_dir: Path) -> None:
    case_keys = list(manifest["case_types"].keys())
    lines = [f"# {manifest['title']}", "", "Cross-cuts Phase 16a (15m) / 16b (35m) / 16c (60m): holds algorithm and "
             "wind speed fixed, compares the same case across altitude. Altitude is rendered as a single-hue "
             "sequential ramp (light=15m, dark=60m) since it is an ordered quantity, not a nominal category.", ""]

    lines.append("## Horizontal error summary (mean / max, meters)")
    lines.append("")
    lines.append("| Case | 15 m | 35 m | 60 m |")
    lines.append("| --- | --- | --- | --- |")
    for ck in case_keys:
        row = [manifest["case_types"][ck]]
        for alt in (15, 35, 60):
            he = data[ck][alt]["metrics"]["horizontal_error"]
            row.append(f"{he['mean_m']:.2f} / {he['max_m']:.2f}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Summary bars")
    lines.append("")
    lines.append("![summary bars](plots/altitude_sweep_summary_bars.png)")
    lines.append("")

    lines.append("## Horizontal error vs time, all cases")
    lines.append("")
    lines.append("![grid horizontal error](plots/altitude_sweep_grid_horizontal_error.png)")
    lines.append("")

    lines.append("## Gazebo-truth route, all cases")
    lines.append("")
    lines.append("![grid route](plots/altitude_sweep_grid_route.png)")
    lines.append("")

    lines.append("## Per-case detail")
    lines.append("")
    for ck in case_keys:
        lines.append(f"### {manifest['case_types'][ck]}")
        lines.append("")
        lines.append(f"![{ck} horizontal error](plots/per_case/{ck}_horizontal_error.png)")
        lines.append("")
        lines.append(f"![{ck} route](plots/per_case/{ck}_route.png)")
        lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    out_dir = Path(args.out_dir) if args.out_dir else manifest_path.parent
    plots_dir = out_dir / "plots"
    per_case_dir = plots_dir / "per_case"

    data: dict[str, dict[int, dict]] = {}
    for case_key, alt_map in manifest["runs_by_case"].items():
        data[case_key] = {alt: load_run(run_dir) for alt, run_dir in alt_map.items()}

    for case_key, label in manifest["case_types"].items():
        plot_horizontal_error_single(case_key, label, data[case_key], per_case_dir / f"{case_key}_horizontal_error.png")
        plot_route_single(case_key, label, data[case_key], per_case_dir / f"{case_key}_route.png")

    plot_grid("horizontal_error", manifest, data, plots_dir)
    plot_grid("route", manifest, data, plots_dir)
    plot_summary_bars(manifest, data, plots_dir)
    build_report(manifest, data, out_dir)

    print(f"report={out_dir / 'report.md'}")
    print(f"plots={plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
