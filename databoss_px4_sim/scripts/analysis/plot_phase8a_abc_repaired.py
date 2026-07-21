#!/usr/bin/env python3

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


CASE_STYLES = {
    "case_a": {
        "label": "A: GNSS on",
        "color": "#1f77b4",
    },
    "case_b": {
        "label": "B: GNSS loss, no aiding",
        "color": "#d62728",
    },
    "case_c": {
        "label": "C: GNSS loss, repaired aiding",
        "color": "#2ca02c",
    },
}


@dataclass
class CaseData:
    name: str
    label: str
    color: str
    run_dir: Path
    accepted: bool
    aligned: pd.DataFrame
    metrics: dict
    status: dict


def read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def case_style(case_name: str) -> dict:
    for prefix, style in CASE_STYLES.items():
        if case_name.startswith(prefix):
            return style
    return {"label": case_name, "color": "#444444"}


def nested(dct: dict, *keys, default=None):
    cur = dct
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def plot_time_series(cases, y_column: str, ylabel: str, title: str, out_path: Path, ylim=None):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for case in cases:
        df = case.aligned
        t = df["px4_t_rel_s"] - df["px4_t_rel_s"].iloc[0]
        ax.plot(t, df[y_column], label=case.label, color=case.color, linewidth=1.8)
    ax.set_xlabel("Comparison time [s]")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.35)
    ax.legend(loc="best")
    if ylim is not None:
        ax.set_ylim(*ylim)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def plot_xy(cases, out_path: Path, title: str, zoom_hover: bool = False):
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    zoom_values = []
    for case in cases:
        df = case.aligned
        ax.plot(df["gz_x_rel"], df["gz_y_rel"], label=case.label, color=case.color, linewidth=1.8)
        if zoom_hover and case.name.startswith(("case_a", "case_c")):
            zoom_values.extend(df["gz_x_rel"].tolist())
            zoom_values.extend(df["gz_y_rel"].tolist())
    ax.set_xlabel("Gazebo truth X from start [m]")
    ax.set_ylabel("Gazebo truth Y from start [m]")
    ax.set_title(title)
    ax.grid(True, alpha=0.35)
    ax.set_aspect("equal", adjustable="box")
    ax.legend(loc="best")
    if zoom_hover and zoom_values:
        low = min(zoom_values)
        high = max(zoom_values)
        pad = max(0.15, (high - low) * 0.25)
        ax.set_xlim(low - pad, high + pad)
        ax.set_ylim(low - pad, high + pad)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def plot_summary_bars(cases, out_path: Path):
    labels = [case.label for case in cases]
    colors = [case.color for case in cases]
    station_end = [
        nested(
            case.metrics,
            "station_keeping",
            "gazebo_horizontal_displacement_from_start",
            "end_m",
            default=0.0,
        )
        for case in cases
    ]
    h_err_end = [nested(case.metrics, "horizontal_error", "end_m", default=0.0) for case in cases]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.5))
    ax1.bar(labels, station_end, color=colors)
    ax1.set_yscale("log")
    ax1.set_ylabel("End drift [m], log scale")
    ax1.set_title("Gazebo Station Drift at Land Command")
    ax1.grid(True, axis="y", alpha=0.35)
    ax1.tick_params(axis="x", rotation=18)
    for idx, value in enumerate(station_end):
        ax1.text(idx, value * 1.2, fmt(value), ha="center", va="bottom", fontsize=9)

    ax2.bar(labels, h_err_end, color=colors)
    ax2.set_yscale("log")
    ax2.set_ylabel("End horizontal error [m], log scale")
    ax2.set_title("EKF vs Gazebo Truth Error at Land Command")
    ax2.grid(True, axis="y", alpha=0.35)
    ax2.tick_params(axis="x", rotation=18)
    for idx, value in enumerate(h_err_end):
        ax2.text(idx, value * 1.2, fmt(value), ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def plot_case_c_ev_counts(cases, out_path: Path):
    case_c = next((case for case in cases if case.name.startswith("case_c")), None)
    if case_c is None:
        return

    labels = ["EV pos", "EV hgt", "EV vel"]
    active = [
        case_c.status.get("ulog_ev_pos_active_count", 0),
        case_c.status.get("ulog_ev_hgt_active_count", 0),
        case_c.status.get("ulog_ev_vel_active_count", 0),
    ]
    rejected = [
        case_c.status.get("ulog_ev_pos_rejected_count", 0),
        case_c.status.get("ulog_ev_hgt_rejected_count", 0),
        case_c.status.get("ulog_ev_vel_rejected_count", 0),
    ]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(labels))
    ax.bar([v - 0.18 for v in x], active, width=0.36, label="Active/fused samples", color="#2ca02c")
    ax.bar([v + 0.18 for v in x], rejected, width=0.36, label="Rejected samples", color="#d62728")
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("ULog sample count")
    ax.set_title("Case C External Vision Fusion Counts")
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(loc="best")
    for idx, value in enumerate(active):
        ax.text(idx - 0.18, value + 2, str(value), ha="center", va="bottom", fontsize=9)
    for idx, value in enumerate(rejected):
        ax.text(idx + 0.18, value + 2, str(value), ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def load_cases(batch_dir: Path) -> list[CaseData]:
    summary = read_json(batch_dir / "batch_summary.json")
    cases = []
    for result in sorted(summary["results"], key=lambda item: item["index"]):
        name = result["name"]
        run_dir = Path(result["run_dir"]).resolve()
        aligned_path = run_dir / "ekf_vs_ground_truth_aligned.csv"
        metrics_path = run_dir / "ekf_vs_ground_truth_metrics.json"
        status_path = run_dir / "logs" / "pxh_takeoff_land_truth_status.json"
        if not aligned_path.exists():
            raise FileNotFoundError(aligned_path)

        style = case_style(name)
        cases.append(
            CaseData(
                name=name,
                label=style["label"],
                color=style["color"],
                run_dir=run_dir,
                accepted=bool(result.get("accepted")),
                aligned=pd.read_csv(aligned_path),
                metrics=read_json(metrics_path),
                status=read_json(status_path),
            )
        )
    return cases


def write_report(batch_dir: Path, cases: list[CaseData], plots_dir: Path, report_path: Path):
    case_b = next((case for case in cases if case.name.startswith("case_b")), None)
    case_c = next((case for case in cases if case.name.startswith("case_c")), None)

    b_end = None
    c_end = None
    if case_b and case_c:
        b_end = nested(
            case_b.metrics,
            "station_keeping",
            "gazebo_horizontal_displacement_from_start",
            "end_m",
        )
        c_end = nested(
            case_c.metrics,
            "station_keeping",
            "gazebo_horizontal_displacement_from_start",
            "end_m",
        )

    lines = []
    lines.append("# Phase 8A Repaired ABC 120 s Plot Report")
    lines.append("")
    lines.append(f"Generated UTC: `{datetime.now(timezone.utc).isoformat()}`")
    lines.append("")
    lines.append(f"Batch: `{batch_dir}`")
    lines.append("")
    lines.append("## Current situation")
    lines.append("")
    lines.append("The repaired Case C is doing what we wanted in this SITL proof: after GNSS loss, the vehicle stays near the hover point while the no-aiding Case B physically drifts away.")
    if b_end is not None and c_end not in (None, 0):
        lines.append("")
        lines.append(f"End station drift improves from `{fmt(b_end)} m` in Case B to `{fmt(c_end)} m` in Case C, about `{fmt(b_end / c_end, 1)}x` lower drift.")
    lines.append("")
    lines.append("## Key metrics")
    lines.append("")
    lines.append("| Case | Accepted | Aligned duration s | Airborne s | Gazebo station end m | Gazebo station max m | EKF/truth H end m | EKF/truth H max m | EV active pos/hgt/vel | EV rejected pos/hgt/vel | XY resets |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|")

    for case in cases:
        station = nested(case.metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", default={})
        h_err = nested(case.metrics, "horizontal_error", default={})
        active = (
            case.status.get("ulog_ev_pos_active_count", 0),
            case.status.get("ulog_ev_hgt_active_count", 0),
            case.status.get("ulog_ev_vel_active_count", 0),
        )
        rejected = (
            case.status.get("ulog_ev_pos_rejected_count", 0),
            case.status.get("ulog_ev_hgt_rejected_count", 0),
            case.status.get("ulog_ev_vel_rejected_count", 0),
        )
        xy_resets = case.status.get("ulog_xy_reset_counter_delta")
        lines.append(
            "| "
            + " | ".join(
                [
                    case.label,
                    str(case.accepted),
                    fmt(case.metrics.get("aligned_duration_s")),
                    fmt(case.status.get("ulog_airborne_duration_s")),
                    fmt(station.get("end_m")),
                    fmt(station.get("max_m")),
                    fmt(h_err.get("end_m")),
                    fmt(h_err.get("max_m")),
                    f"{active[0]}/{active[1]}/{active[2]}",
                    f"{rejected[0]}/{rejected[1]}/{rejected[2]}",
                    fmt(xy_resets, 0),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## Plots")
    lines.append("")
    plot_names = [
        ("Station displacement, full scale", "station_displacement_time.png"),
        ("Station displacement, 0 to 1 m zoom", "station_displacement_zoom_1m.png"),
        ("Gazebo truth XY track, full scale", "truth_xy_track.png"),
        ("Gazebo truth XY track, hover zoom", "truth_xy_track_hover_zoom.png"),
        ("Horizontal EKF/truth error, full scale", "horizontal_error_time.png"),
        ("Horizontal EKF/truth error, 0 to 1 m zoom", "horizontal_error_zoom_1m.png"),
        ("Gazebo truth height", "height_up_time.png"),
        ("Summary bars", "summary_bars.png"),
        ("Case C EV fusion counts", "case_c_ev_fusion_counts.png"),
    ]
    for title, filename in plot_names:
        rel = plots_dir.relative_to(batch_dir) / filename
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"![{title}]({rel})")
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append("- Case A is the healthy GNSS baseline and stays essentially fixed.")
    lines.append("- Case B proves the GNSS-denied/no-aiding failure mode: the physical Gazebo vehicle drifts hundreds of meters before the land command.")
    lines.append("- Case C proves the repaired external-aiding path: GNSS is lost, EV position/height/velocity are active, EV rejections are zero, and station drift stays below 0.1 m at the land command.")
    lines.append("- This is still truth-fed synthetic aiding. It proves the PX4 external odometry fusion path and the ABC experiment design, not real VIO, LiDAR SLAM, or optical flow.")
    lines.append("")
    lines.append("## Next moves")
    lines.append("")
    lines.append("- Treat this repaired 120 s ABC run as the main Phase 8A proof artifact.")
    lines.append("- Add robustness sweeps for synthetic-aiding noise, latency, dropout, and rate reduction.")
    lines.append("- Keep EV yaw disabled until yaw/frame behavior is separately validated.")
    lines.append("- Keep reporting station drift, EKF/truth error, EV rejection counts, and XY reset delta together.")
    lines.append("")

    report_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch-dir",
        default="experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s",
        help="Batch directory containing batch_summary.json",
    )
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).resolve()
    plots_dir = batch_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(batch_dir)

    plot_time_series(
        cases,
        "gazebo_horizontal_displacement_from_start_m",
        "Gazebo horizontal displacement from start [m]",
        "Station Drift During ABC Comparison",
        plots_dir / "station_displacement_time.png",
    )
    plot_time_series(
        cases,
        "gazebo_horizontal_displacement_from_start_m",
        "Gazebo horizontal displacement from start [m]",
        "Station Drift During ABC Comparison - Hover Zoom",
        plots_dir / "station_displacement_zoom_1m.png",
        ylim=(0.0, 1.0),
    )
    plot_xy(cases, plots_dir / "truth_xy_track.png", "Gazebo Truth XY Track - Full Scale")
    plot_xy(cases, plots_dir / "truth_xy_track_hover_zoom.png", "Gazebo Truth XY Track - Hover Zoom", zoom_hover=True)
    plot_time_series(
        cases,
        "horizontal_error_m",
        "Horizontal EKF/truth error [m]",
        "Horizontal EKF vs Gazebo Truth Error",
        plots_dir / "horizontal_error_time.png",
    )
    plot_time_series(
        cases,
        "horizontal_error_m",
        "Horizontal EKF/truth error [m]",
        "Horizontal EKF vs Gazebo Truth Error - Hover Zoom",
        plots_dir / "horizontal_error_zoom_1m.png",
        ylim=(0.0, 1.0),
    )
    plot_time_series(
        cases,
        "gz_height_up",
        "Gazebo truth height up [m]",
        "Gazebo Truth Height",
        plots_dir / "height_up_time.png",
    )
    plot_summary_bars(cases, plots_dir / "summary_bars.png")
    plot_case_c_ev_counts(cases, plots_dir / "case_c_ev_fusion_counts.png")

    report_path = batch_dir / "phase8a_abc_repaired_plot_report.md"
    write_report(batch_dir, cases, plots_dir, report_path)

    print(f"OK: plots written to {plots_dir}")
    print(f"OK: report written to {report_path}")


if __name__ == "__main__":
    main()
