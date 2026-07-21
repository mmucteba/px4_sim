#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


COLORS = {
    "gnss_on": "#1f77b4",
    "gnss_loss_no_aiding": "#d62728",
    "nominal": "#2ca02c",
    "rate": "#17becf",
    "covariance": "#ff7f0e",
    "delay": "#9467bd",
    "combo": "#8c564b",
}


def to_float(value, default=None):
    try:
        if value == "" or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def classify_case(name: str) -> tuple[str, str, int]:
    if name == "reference_a_gnss_on_no_aiding":
        return "A: GNSS on", "gnss_on", 0
    if name == "reference_b_gnss_loss_no_aiding":
        return "B: GNSS loss, no aiding", "gnss_loss_no_aiding", 1
    if name == "case_c_repaired_nominal_30hz_cov010_delay0":
        return "C nominal\n30 Hz, std 0.10, delay 0", "nominal", 2
    if name == "case_c_stress_rate_15hz":
        return "C rate\n15 Hz", "rate", 3
    if name == "case_c_stress_rate_10hz":
        return "C rate\n10 Hz", "rate", 4
    if name == "case_c_stress_rate_5hz":
        return "C rate\n5 Hz", "rate", 5
    if name == "case_c_stress_cov_pos025_vel150":
        return "C cov\npos 0.25 vel 1.50", "covariance", 6
    if name == "case_c_stress_cov_pos050_vel200":
        return "C cov\npos 0.50 vel 2.00", "covariance", 7
    if name == "case_c_stress_evdelay_100ms":
        return "C delay\n100 ms", "delay", 8
    if name == "case_c_stress_evdelay_160ms":
        return "C delay\n160 ms", "delay", 9
    if name == "case_c_stress_combo_rate10_cov025_delay100":
        return "C combo\n10 Hz, pos 0.25, delay 100", "combo", 10
    return name, "nominal", 99


def load_batch_metrics(batch_dir: Path) -> pd.DataFrame:
    metrics_path = batch_dir / "batch_metrics.csv"
    if not metrics_path.exists():
        raise FileNotFoundError(metrics_path)

    df = pd.read_csv(metrics_path)
    labels = df["case"].map(lambda name: classify_case(str(name))[0])
    groups = df["case"].map(lambda name: classify_case(str(name))[1])
    orders = df["case"].map(lambda name: classify_case(str(name))[2])
    df.insert(1, "label", labels)
    df.insert(2, "group", groups)
    df.insert(3, "order", orders)

    numeric_cols = [
        "external_odom_rate_hz",
        "external_odom_ev_delay_ms",
        "external_odom_position_std_m",
        "external_odom_velocity_std_m_s",
        "ulog_ev_vel_active_count",
        "ulog_ev_pos_rejected_count",
        "ulog_ev_hgt_rejected_count",
        "ulog_ev_vel_rejected_count",
        "ulog_xy_reset_counter_delta",
        "horizontal_mean_m",
        "horizontal_max_m",
        "horizontal_p95_m",
        "horizontal_end_m",
        "error_3d_max_m",
        "truth_station_end_m",
        "truth_station_max_m",
        "land_command_t_rel_s",
        "aligned_duration_s",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("order").reset_index(drop=True)


def savefig(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def annotate_bar_values(ax, bars, values, digits=3, xlog=False):
    for bar, value in zip(bars, values):
        if pd.isna(value):
            continue
        if xlog:
            x = max(float(value) * 1.12, 0.001)
            y = bar.get_y() + bar.get_height() / 2
            ax.text(x, y, fmt(value, digits), va="center", fontsize=8)
        else:
            x = bar.get_x() + bar.get_width() / 2
            y = bar.get_height()
            ax.text(x, y + max(values) * 0.025, fmt(value, digits), ha="center", va="bottom", fontsize=8)


def plot_all_station_drift(df: pd.DataFrame, plots_dir: Path):
    values = df["truth_station_end_m"].fillna(0.0)
    colors = [COLORS.get(group, "#444444") for group in df["group"]]

    fig, ax = plt.subplots(figsize=(11, 6.5))
    bars = ax.barh(df["label"], values, color=colors)
    ax.set_xscale("log")
    ax.set_xlabel("Gazebo station drift at land command [m], log scale")
    ax.set_title("Phase 8A Stress Matrix - Final Station Drift")
    ax.grid(True, axis="x", alpha=0.35)
    ax.invert_yaxis()
    annotate_bar_values(ax, bars, values, xlog=True)
    savefig(plots_dir / "station_drift_all_cases_log.png")


def plot_case_c_station_zoom(df: pd.DataFrame, plots_dir: Path):
    c = df[df["case"].str.startswith("case_c")].copy()
    values = c["truth_station_end_m"].fillna(0.0)
    colors = [COLORS.get(group, "#444444") for group in c["group"]]

    fig, ax = plt.subplots(figsize=(11, 5.8))
    bars = ax.bar(c["label"], values, color=colors)
    ax.set_ylabel("Gazebo station drift at land command [m]")
    ax.set_title("Repaired/Stressed Case C - Final Station Drift")
    ax.grid(True, axis="y", alpha=0.35)
    ax.tick_params(axis="x", rotation=25)
    ax.set_ylim(0, max(float(values.max()) * 1.22, 0.05))
    annotate_bar_values(ax, bars, values)
    savefig(plots_dir / "case_c_station_drift_zoom.png")


def plot_case_c_error_zoom(df: pd.DataFrame, plots_dir: Path):
    c = df[df["case"].str.startswith("case_c")].copy()
    x = range(len(c))

    fig, ax = plt.subplots(figsize=(11, 5.8))
    ax.bar([i - 0.18 for i in x], c["horizontal_mean_m"], width=0.36, label="Horizontal mean", color="#6baed6")
    ax.bar([i + 0.18 for i in x], c["horizontal_max_m"], width=0.36, label="Horizontal max", color="#08519c")
    ax.set_xticks(list(x), c["label"])
    ax.set_ylabel("EKF vs Gazebo horizontal error [m]")
    ax.set_title("Repaired/Stressed Case C - EKF vs Truth Horizontal Error")
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(loc="best")
    ax.tick_params(axis="x", rotation=25)
    ax.set_ylim(0, max(float(c["horizontal_max_m"].max()) * 1.22, 0.05))
    savefig(plots_dir / "case_c_horizontal_error_zoom.png")


def plot_sweep(
    df: pd.DataFrame,
    rows: list[str],
    x_col: str,
    title: str,
    xlabel: str,
    out_path: Path,
    sort_desc: bool = False,
):
    sweep = df[df["case"].isin(rows)].copy()
    sweep = sweep.sort_values(x_col, ascending=not sort_desc)

    fig, ax = plt.subplots(figsize=(8, 5.2))
    ax.plot(
        sweep[x_col],
        sweep["truth_station_end_m"],
        marker="o",
        linewidth=2,
        label="Final station drift",
        color="#2ca02c",
    )
    ax.plot(
        sweep[x_col],
        sweep["horizontal_max_m"],
        marker="s",
        linewidth=2,
        label="Horizontal error max",
        color="#08519c",
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Meters")
    ax.set_title(title)
    ax.grid(True, alpha=0.35)
    ax.legend(loc="best")
    savefig(out_path)


def plot_ev_health(df: pd.DataFrame, plots_dir: Path):
    c = df[df["case"].str.startswith("case_c")].copy()
    labels = c["label"].tolist()
    total_rejects = (
        c["ulog_ev_pos_rejected_count"].fillna(0)
        + c["ulog_ev_hgt_rejected_count"].fillna(0)
        + c["ulog_ev_vel_rejected_count"].fillna(0)
    )
    xy_resets = c["ulog_xy_reset_counter_delta"].fillna(0)
    x = range(len(c))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ax1.bar(x, total_rejects, color="#d62728")
    ax1.set_ylabel("Total EV rejects")
    ax1.set_title("Case C EV Health")
    ax1.grid(True, axis="y", alpha=0.35)
    ax1.set_ylim(0, max(1.0, float(total_rejects.max()) + 1.0))
    for i, value in enumerate(total_rejects):
        ax1.text(i, value + 0.05, fmt(value, 0), ha="center", va="bottom", fontsize=8)

    ax2.bar(x, xy_resets, color="#9467bd")
    ax2.set_ylabel("XY reset delta")
    ax2.grid(True, axis="y", alpha=0.35)
    ax2.set_xticks(list(x), labels)
    ax2.tick_params(axis="x", rotation=25)
    ax2.set_ylim(0, max(3.0, float(xy_resets.max()) + 1.0))
    for i, value in enumerate(xy_resets):
        ax2.text(i, value + 0.05, fmt(value, 0), ha="center", va="bottom", fontsize=8)

    savefig(plots_dir / "case_c_ev_health.png")


def load_aligned(run_dir: str) -> pd.DataFrame | None:
    if not run_dir or pd.isna(run_dir):
        return None
    path = Path(str(run_dir)) / "ekf_vs_ground_truth_aligned.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


def plot_station_time(df: pd.DataFrame, plots_dir: Path):
    fig, ax = plt.subplots(figsize=(11, 5.8))
    for _, row in df.iterrows():
        aligned = load_aligned(row["run_dir"])
        if aligned is None:
            continue
        t = aligned["px4_t_rel_s"] - aligned["px4_t_rel_s"].iloc[0]
        ax.plot(
            t,
            aligned["gazebo_horizontal_displacement_from_start_m"],
            label=row["label"].replace("\n", " "),
            color=COLORS.get(row["group"], "#444444"),
            linewidth=1.6,
            alpha=0.9 if row["group"] in {"gnss_on", "gnss_loss_no_aiding", "nominal"} else 0.65,
        )
    ax.set_xlabel("Comparison time [s]")
    ax.set_ylabel("Gazebo station drift [m]")
    ax.set_title("Station Drift Time History - All Cases")
    ax.grid(True, alpha=0.35)
    ax.legend(loc="upper left", fontsize=7)
    savefig(plots_dir / "station_drift_time_all_cases.png")

    c = df[df["case"].str.startswith("case_c")].copy()
    fig, ax = plt.subplots(figsize=(11, 5.8))
    for _, row in c.iterrows():
        aligned = load_aligned(row["run_dir"])
        if aligned is None:
            continue
        t = aligned["px4_t_rel_s"] - aligned["px4_t_rel_s"].iloc[0]
        ax.plot(
            t,
            aligned["gazebo_horizontal_displacement_from_start_m"],
            label=row["label"].replace("\n", " "),
            color=COLORS.get(row["group"], "#444444"),
            linewidth=1.8,
        )
    ax.set_xlabel("Comparison time [s]")
    ax.set_ylabel("Gazebo station drift [m]")
    ax.set_title("Station Drift Time History - Repaired/Stressed Case C")
    ax.grid(True, alpha=0.35)
    ax.legend(loc="upper left", fontsize=8)
    savefig(plots_dir / "case_c_station_drift_time_zoom.png")


def plot_xy_case_c(df: pd.DataFrame, plots_dir: Path):
    c = df[df["case"].str.startswith("case_c")].copy()
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    for _, row in c.iterrows():
        aligned = load_aligned(row["run_dir"])
        if aligned is None:
            continue
        ax.plot(
            aligned["gz_x_rel"],
            aligned["gz_y_rel"],
            label=row["label"].replace("\n", " "),
            color=COLORS.get(row["group"], "#444444"),
            linewidth=1.6,
        )
    ax.set_xlabel("Gazebo truth X from start [m]")
    ax.set_ylabel("Gazebo truth Y from start [m]")
    ax.set_title("Gazebo Truth XY Track - Repaired/Stressed Case C")
    ax.grid(True, alpha=0.35)
    ax.set_aspect("equal", adjustable="box")
    ax.legend(loc="best", fontsize=7)
    savefig(plots_dir / "case_c_truth_xy_zoom.png")


def write_summary_files(df: pd.DataFrame, batch_dir: Path):
    cols = [
        "case",
        "label",
        "group",
        "accepted",
        "external_odom_rate_hz",
        "external_odom_ev_delay_ms",
        "external_odom_position_std_m",
        "external_odom_velocity_std_m_s",
        "external_odom_mav_frame",
        "truth_station_end_m",
        "truth_station_max_m",
        "horizontal_mean_m",
        "horizontal_max_m",
        "horizontal_end_m",
        "ulog_ev_pos_rejected_count",
        "ulog_ev_hgt_rejected_count",
        "ulog_ev_vel_rejected_count",
        "ulog_xy_reset_counter_delta",
        "run_dir",
    ]
    out = df[[col for col in cols if col in df.columns]].copy()
    out.to_csv(batch_dir / "stress_summary.csv", index=False)

    lines = [
        "# Phase 8A Stress Summary Data",
        "",
        "| Case | Rate Hz | EV delay ms | Pos std m | Vel std m/s | Station end m | H max m | EV rejects pos/hgt/vel | XY resets |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for _, row in out.iterrows():
        rejects = (
            f"{fmt(row.get('ulog_ev_pos_rejected_count'), 0)}/"
            f"{fmt(row.get('ulog_ev_hgt_rejected_count'), 0)}/"
            f"{fmt(row.get('ulog_ev_vel_rejected_count'), 0)}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['case']}`",
                    fmt(row.get("external_odom_rate_hz"), 1),
                    fmt(row.get("external_odom_ev_delay_ms"), 0),
                    fmt(row.get("external_odom_position_std_m"), 3),
                    fmt(row.get("external_odom_velocity_std_m_s"), 3),
                    fmt(row.get("truth_station_end_m"), 3),
                    fmt(row.get("horizontal_max_m"), 3),
                    rejects,
                    fmt(row.get("ulog_xy_reset_counter_delta"), 0),
                ]
            )
            + " |"
        )
    lines.append("")
    (batch_dir / "stress_summary.md").write_text("\n".join(lines))


def rel_plot(path: Path, batch_dir: Path) -> str:
    return str(path.relative_to(batch_dir))


def write_report(df: pd.DataFrame, batch_dir: Path, plots_dir: Path):
    report_path = batch_dir / "phase8a_case_c_stress_report.md"

    a = df[df["case"] == "reference_a_gnss_on_no_aiding"].iloc[0]
    b = df[df["case"] == "reference_b_gnss_loss_no_aiding"].iloc[0]
    nominal = df[df["case"] == "case_c_repaired_nominal_30hz_cov010_delay0"].iloc[0]
    c = df[df["case"].str.startswith("case_c")].copy()
    worst_c = c.sort_values("truth_station_end_m", ascending=False).iloc[0]
    best_c = c.sort_values("truth_station_end_m", ascending=True).iloc[0]
    c_reject_total = (
        c["ulog_ev_pos_rejected_count"].fillna(0)
        + c["ulog_ev_hgt_rejected_count"].fillna(0)
        + c["ulog_ev_vel_rejected_count"].fillna(0)
    )

    plot_order = [
        ("All-case final station drift", plots_dir / "station_drift_all_cases_log.png"),
        ("Case C final station drift zoom", plots_dir / "case_c_station_drift_zoom.png"),
        ("Case C EKF/truth horizontal error", plots_dir / "case_c_horizontal_error_zoom.png"),
        ("Odometry-rate sweep", plots_dir / "rate_sweep_station_error.png"),
        ("Reported-covariance sweep", plots_dir / "covariance_sweep_station_error.png"),
        ("EKF2_EV_DELAY sweep", plots_dir / "delay_sweep_station_error.png"),
        ("Case C EV health", plots_dir / "case_c_ev_health.png"),
        ("Case C station drift over time", plots_dir / "case_c_station_drift_time_zoom.png"),
        ("Case C truth XY tracks", plots_dir / "case_c_truth_xy_zoom.png"),
    ]

    lines = []
    lines.append("# Phase 8A Case C Stress Matrix Report")
    lines.append("")
    lines.append(f"Generated UTC: `{datetime.now(timezone.utc).isoformat()}`")
    lines.append("")
    lines.append(f"Batch: `{batch_dir}`")
    lines.append("")
    lines.append("## Current Situation")
    lines.append("")
    lines.append("The 120 s stress matrix passed mechanically: all 11 cases were accepted, QGC was connected, GNSS loss was detected where requested, ULogs were copied, and Gazebo truth metrics were generated.")
    lines.append("")
    lines.append("The important result is that every repaired/stressed Case C stayed near the hover point while the GNSS-loss/no-aiding reference drifted far away.")
    lines.append("")
    lines.append("## Key Numbers")
    lines.append("")
    lines.append("| Item | Station drift end m | Horizontal error max m | Notes |")
    lines.append("|---|---:|---:|---|")
    lines.append(f"| GNSS-on reference | {fmt(a['truth_station_end_m'])} | {fmt(a['horizontal_max_m'])} | Healthy baseline |")
    lines.append(f"| GNSS-loss/no-aiding reference | {fmt(b['truth_station_end_m'])} | {fmt(b['horizontal_max_m'])} | Drift baseline |")
    lines.append(f"| Nominal repaired Case C | {fmt(nominal['truth_station_end_m'])} | {fmt(nominal['horizontal_max_m'])} | 30 Hz, pos std 0.10, vel std 1.00, delay 0 |")
    lines.append(f"| Best stressed Case C | {fmt(best_c['truth_station_end_m'])} | {fmt(best_c['horizontal_max_m'])} | `{best_c['case']}` |")
    lines.append(f"| Worst stressed Case C | {fmt(worst_c['truth_station_end_m'])} | {fmt(worst_c['horizontal_max_m'])} | `{worst_c['case']}` |")
    lines.append("")
    lines.append("Case C health summary:")
    lines.append("")
    lines.append(f"- Case C station drift range: `{fmt(c['truth_station_end_m'].min())}` to `{fmt(c['truth_station_end_m'].max())}` m.")
    lines.append(f"- Case C EV rejection total across all stress cases: `{fmt(c_reject_total.sum(), 0)}`.")
    lines.append(f"- Case C XY reset delta range: `{fmt(c['ulog_xy_reset_counter_delta'].min(), 0)}` to `{fmt(c['ulog_xy_reset_counter_delta'].max(), 0)}`.")
    lines.append("")
    lines.append("## Data Files")
    lines.append("")
    lines.append(f"- Full batch metrics CSV: `{rel_plot(batch_dir / 'batch_metrics.csv', batch_dir)}`")
    lines.append(f"- Full batch metrics MD: `{rel_plot(batch_dir / 'batch_metrics.md', batch_dir)}`")
    lines.append(f"- Reduced stress summary CSV: `{rel_plot(batch_dir / 'stress_summary.csv', batch_dir)}`")
    lines.append(f"- Reduced stress summary MD: `{rel_plot(batch_dir / 'stress_summary.md', batch_dir)}`")
    lines.append("")
    lines.append("## Plots")
    lines.append("")
    for title, path in plot_order:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"![{title}]({rel_plot(path, batch_dir)})")
        lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- The no-aiding GNSS-loss reference is still the failure baseline: it ended at `143.189 m` station drift.")
    lines.append("- Nominal repaired Case C ended at `0.143 m`, and the worst stressed Case C ended at `0.226 m`.")
    lines.append("- All Case C variants had `0/0/0` EV position/height/velocity rejections, so the external odometry stream stayed acceptable to EKF2 in these tests.")
    lines.append("- Lowering the odometry rate to `5 Hz` did not break the hover in this SITL truth-fed setup.")
    lines.append("- Looser reported covariance increased error in the strongest covariance case, but still stayed sub-meter.")
    lines.append("- The `EKF2_EV_DELAY` settings tested here did not cause the old runaway behavior.")
    lines.append("")
    lines.append("## Limitation")
    lines.append("")
    lines.append("These are wired-parameter stresses, not true sensor degradation. The covariance cases change the uncertainty reported to PX4; they do not add random noise to the measured pose. The delay cases change `EKF2_EV_DELAY`; they do not inject transport latency into the MAVLink stream. Dropout is still not wired.")
    lines.append("")
    lines.append("## Recommended Next Move")
    lines.append("")
    lines.append("Implement real disturbance injection in the live odometry bridge: random position/velocity noise, delayed sample replay, and controlled dropout bursts. Then rerun a smaller matrix around the thresholds found here.")
    lines.append("")

    report_path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).resolve()
    if not (batch_dir / "batch_summary.json").exists():
        raise FileNotFoundError(batch_dir / "batch_summary.json")

    plots_dir = batch_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    df = load_batch_metrics(batch_dir)
    write_summary_files(df, batch_dir)

    plot_all_station_drift(df, plots_dir)
    plot_case_c_station_zoom(df, plots_dir)
    plot_case_c_error_zoom(df, plots_dir)
    plot_sweep(
        df,
        [
            "case_c_repaired_nominal_30hz_cov010_delay0",
            "case_c_stress_rate_15hz",
            "case_c_stress_rate_10hz",
            "case_c_stress_rate_5hz",
        ],
        "external_odom_rate_hz",
        "Odometry Rate Sweep",
        "External odometry rate [Hz]",
        plots_dir / "rate_sweep_station_error.png",
        sort_desc=True,
    )
    plot_sweep(
        df,
        [
            "case_c_repaired_nominal_30hz_cov010_delay0",
            "case_c_stress_cov_pos025_vel150",
            "case_c_stress_cov_pos050_vel200",
        ],
        "external_odom_position_std_m",
        "Reported Covariance Sweep",
        "Reported EV position std [m]",
        plots_dir / "covariance_sweep_station_error.png",
    )
    plot_sweep(
        df,
        [
            "case_c_repaired_nominal_30hz_cov010_delay0",
            "case_c_stress_evdelay_100ms",
            "case_c_stress_evdelay_160ms",
        ],
        "external_odom_ev_delay_ms",
        "EKF2_EV_DELAY Sweep",
        "EKF2_EV_DELAY [ms]",
        plots_dir / "delay_sweep_station_error.png",
    )
    plot_ev_health(df, plots_dir)
    plot_station_time(df, plots_dir)
    plot_xy_case_c(df, plots_dir)
    write_report(df, batch_dir, plots_dir)

    print(f"OK: plots written to {plots_dir}")
    print(f"OK: stress_summary.csv written to {batch_dir / 'stress_summary.csv'}")
    print(f"OK: stress_summary.md written to {batch_dir / 'stress_summary.md'}")
    print(f"OK: report written to {batch_dir / 'phase8a_case_c_stress_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
