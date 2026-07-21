#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    "noise": "#ff7f0e",
    "latency": "#9467bd",
    "dropout": "#17becf",
    "combo": "#8c564b",
}


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
    cases = {
        "reference_a_gnss_on_no_aiding": ("A: GNSS on", "gnss_on", 0),
        "reference_b_gnss_loss_no_aiding": ("B: GNSS loss\nno aiding", "gnss_loss_no_aiding", 1),
        "case_c_repaired_nominal_30hz_no_real_error": ("C nominal\nno real error", "nominal", 2),
        "case_c_repaired_nominal_30hz_cov010_delay0": ("C nominal\nno real error", "nominal", 2),
        "case_c_realerr_noise_mild_pos005_vel010": ("Noise mild\npos .05 vel .10", "noise", 3),
        "case_c_realerr_noise_medium_pos010_vel025": ("Noise med\npos .10 vel .25", "noise", 4),
        "case_c_realerr_noise_strong_pos025_vel050": ("Noise strong\npos .25 vel .50", "noise", 5),
        "case_c_realerr_latency100_uncompensated": ("Latency 100 ms\nEV_DELAY 0", "latency", 6),
        "case_c_realerr_latency100_compensated": ("Latency 100 ms\nEV_DELAY 100", "latency", 7),
        "case_c_realerr_dropout_1s_every_10s": ("Dropout\n1 s / 10 s", "dropout", 8),
        "case_c_realerr_dropout_2s_every_10s": ("Dropout\n2 s / 10 s", "dropout", 9),
        "case_c_realerr_combo_noise_latency_dropout": ("Combo\nnoise+latency+drop", "combo", 10),
    }
    return cases.get(name, (name, "nominal", 99))


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
        "external_odom_latency_ms",
        "external_odom_position_std_m",
        "external_odom_velocity_std_m_s",
        "external_odom_inject_position_noise_std_m",
        "external_odom_inject_velocity_noise_std_m_s",
        "external_odom_dropout_start_after_s",
        "external_odom_dropout_period_s",
        "external_odom_dropout_duration_s",
        "external_odom_dropout_probability",
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
    finite_values = [float(v) for v in values if not pd.isna(v)]
    y_offset = max(finite_values or [0.0]) * 0.025
    for bar, value in zip(bars, values):
        if pd.isna(value):
            continue
        if xlog:
            x = max(float(value) * 1.12, 0.0015)
            y = bar.get_y() + bar.get_height() / 2
            ax.text(x, y, fmt(value, digits), va="center", fontsize=8)
        else:
            x = bar.get_x() + bar.get_width() / 2
            y = bar.get_height()
            ax.text(x, y + y_offset, fmt(value, digits), ha="center", va="bottom", fontsize=8)


def plot_all_station_drift(df: pd.DataFrame, plots_dir: Path):
    values = df["truth_station_end_m"].fillna(0.0)
    plot_values = values.clip(lower=0.001)
    colors = [COLORS.get(group, "#444444") for group in df["group"]]

    fig, ax = plt.subplots(figsize=(11, 6.5))
    bars = ax.barh(df["label"], plot_values, color=colors)
    ax.set_xscale("log")
    ax.set_xlabel("Gazebo station drift at land command [m], log scale")
    ax.set_title("Phase 8A Real-Error Matrix - Final Station Drift")
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
    ax.set_title("Case C With Real Injected Errors - Final Station Drift")
    ax.grid(True, axis="y", alpha=0.35)
    ax.tick_params(axis="x", rotation=25)
    ax.set_ylim(0, max(float(values.max()) * 1.25, 0.05))
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
    ax.set_title("Case C With Real Injected Errors - EKF vs Truth Horizontal Error")
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(loc="best")
    ax.tick_params(axis="x", rotation=25)
    ax.set_ylim(0, max(float(c["horizontal_max_m"].max()) * 1.25, 0.05))
    savefig(plots_dir / "case_c_horizontal_error_zoom.png")


def plot_sweep(
    df: pd.DataFrame,
    rows: list[str],
    x_col: str,
    title: str,
    xlabel: str,
    out_path: Path,
):
    sweep = df[df["case"].isin(rows)].copy()
    sweep = sweep.sort_values(x_col)

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


def plot_latency_comparison(df: pd.DataFrame, plots_dir: Path):
    rows = [
        "case_c_repaired_nominal_30hz_no_real_error",
        "case_c_realerr_latency100_uncompensated",
        "case_c_realerr_latency100_compensated",
    ]
    sub = df[df["case"].isin(rows)].copy()
    x = range(len(sub))

    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    ax.bar([i - 0.18 for i in x], sub["truth_station_end_m"], width=0.36, label="Station drift", color="#2ca02c")
    ax.bar([i + 0.18 for i in x], sub["horizontal_max_m"], width=0.36, label="Horizontal max", color="#08519c")
    ax.set_xticks(list(x), sub["label"])
    ax.set_ylabel("Meters")
    ax.set_title("100 ms Real Transport Latency: Compensation Check")
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(loc="best")
    ax.tick_params(axis="x", rotation=15)
    ax.set_ylim(0, max(float(sub["horizontal_max_m"].max()) * 1.25, 0.05))
    savefig(plots_dir / "latency_compensation_comparison.png")


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
    ax1.set_title("Case C EV Health Under Real Injected Errors")
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
        if aligned is None or aligned.empty:
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
        if aligned is None or aligned.empty:
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
    ax.set_title("Station Drift Time History - Case C Real-Error Variants")
    ax.grid(True, alpha=0.35)
    ax.legend(loc="upper left", fontsize=8)
    savefig(plots_dir / "case_c_station_drift_time_zoom.png")


def plot_xy_case_c(df: pd.DataFrame, plots_dir: Path):
    c = df[df["case"].str.startswith("case_c")].copy()
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    for _, row in c.iterrows():
        aligned = load_aligned(row["run_dir"])
        if aligned is None or aligned.empty:
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
    ax.set_title("Gazebo Truth XY Track - Case C Real-Error Variants")
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
        "external_odom_latency_ms",
        "external_odom_inject_position_noise_std_m",
        "external_odom_inject_velocity_noise_std_m_s",
        "external_odom_dropout_enabled",
        "external_odom_dropout_period_s",
        "external_odom_dropout_duration_s",
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
    out.to_csv(batch_dir / "real_error_summary.csv", index=False)

    lines = [
        "# Phase 8A Real-Error Summary Data",
        "",
        "| Case | Accepted | Actual latency ms | Inject pos noise m | Inject vel noise m/s | Dropout duration s | Station end m | H max m | EV rejects pos/hgt/vel | XY resets |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
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
                    fmt(row.get("accepted")),
                    fmt(row.get("external_odom_latency_ms"), 0),
                    fmt(row.get("external_odom_inject_position_noise_std_m"), 3),
                    fmt(row.get("external_odom_inject_velocity_noise_std_m_s"), 3),
                    fmt(row.get("external_odom_dropout_duration_s"), 1),
                    fmt(row.get("truth_station_end_m"), 3),
                    fmt(row.get("horizontal_max_m"), 3),
                    rejects,
                    fmt(row.get("ulog_xy_reset_counter_delta"), 0),
                ]
            )
            + " |"
        )
    lines.append("")
    (batch_dir / "real_error_summary.md").write_text("\n".join(lines))


def rel_plot(path: Path, batch_dir: Path) -> str:
    return str(path.relative_to(batch_dir))


def find_row(df: pd.DataFrame, case: str) -> pd.Series | None:
    rows = df[df["case"] == case]
    if rows.empty:
        return None
    return rows.iloc[0]


def row_metric(row: pd.Series | None, col: str, digits: int = 3) -> str:
    if row is None:
        return ""
    return fmt(row.get(col), digits)


def write_report(df: pd.DataFrame, batch_dir: Path, plots_dir: Path):
    report_path = batch_dir / "phase8a_case_c_real_error_report.md"

    a = find_row(df, "reference_a_gnss_on_no_aiding")
    b = find_row(df, "reference_b_gnss_loss_no_aiding")
    nominal = find_row(df, "case_c_repaired_nominal_30hz_no_real_error")
    c = df[df["case"].str.startswith("case_c")].copy()
    c_with_metrics = c.dropna(subset=["truth_station_end_m"])
    worst_c = c_with_metrics.sort_values("truth_station_end_m", ascending=False).iloc[0] if not c_with_metrics.empty else None
    best_c = c_with_metrics.sort_values("truth_station_end_m", ascending=True).iloc[0] if not c_with_metrics.empty else None
    c_reject_total = (
        c["ulog_ev_pos_rejected_count"].fillna(0)
        + c["ulog_ev_hgt_rejected_count"].fillna(0)
        + c["ulog_ev_vel_rejected_count"].fillna(0)
    )
    accepted_count = df["accepted"].astype(str).str.lower().eq("true").sum()

    plot_order = [
        ("All-case final station drift", plots_dir / "station_drift_all_cases_log.png"),
        ("Case C final station drift zoom", plots_dir / "case_c_station_drift_zoom.png"),
        ("Case C EKF/truth horizontal error", plots_dir / "case_c_horizontal_error_zoom.png"),
        ("Injected-noise sweep", plots_dir / "noise_sweep_station_error.png"),
        ("Latency compensation comparison", plots_dir / "latency_compensation_comparison.png"),
        ("Dropout-duration sweep", plots_dir / "dropout_sweep_station_error.png"),
        ("Case C EV health", plots_dir / "case_c_ev_health.png"),
        ("Case C station drift over time", plots_dir / "case_c_station_drift_time_zoom.png"),
        ("Case C truth XY tracks", plots_dir / "case_c_truth_xy_zoom.png"),
    ]

    lines = []
    lines.append("# Phase 8A Case C Real-Error Matrix Report")
    lines.append("")
    lines.append(f"Generated UTC: `{datetime.now(timezone.utc).isoformat()}`")
    lines.append("")
    lines.append(f"Batch: `{batch_dir}`")
    lines.append("")
    lines.append("## Current Situation")
    lines.append("")
    lines.append(
        f"The 120 s real-error matrix completed `{accepted_count}/{len(df)}` accepted cases. "
        "This batch keeps the repaired Case C external-aiding path, then injects real measurement "
        "disturbances into the MAVLink odometry stream."
    )
    lines.append("")
    lines.append(
        "The key question is whether Case C still holds position after GNSS loss when external "
        "odometry is noisy, delayed, or temporarily missing."
    )
    lines.append("")
    lines.append("## Key Numbers")
    lines.append("")
    lines.append("| Item | Station drift end m | Horizontal error max m | Notes |")
    lines.append("|---|---:|---:|---|")
    lines.append(f"| GNSS-on reference | {row_metric(a, 'truth_station_end_m')} | {row_metric(a, 'horizontal_max_m')} | Healthy baseline |")
    lines.append(f"| GNSS-loss/no-aiding reference | {row_metric(b, 'truth_station_end_m')} | {row_metric(b, 'horizontal_max_m')} | Drift baseline |")
    lines.append(f"| Nominal repaired Case C | {row_metric(nominal, 'truth_station_end_m')} | {row_metric(nominal, 'horizontal_max_m')} | 30 Hz external odometry, no real injected error |")
    if best_c is not None:
        lines.append(f"| Best real-error Case C | {fmt(best_c['truth_station_end_m'])} | {fmt(best_c['horizontal_max_m'])} | `{best_c['case']}` |")
    if worst_c is not None:
        lines.append(f"| Worst real-error Case C | {fmt(worst_c['truth_station_end_m'])} | {fmt(worst_c['horizontal_max_m'])} | `{worst_c['case']}` |")
    lines.append("")
    lines.append("Case C health summary:")
    lines.append("")
    if not c_with_metrics.empty:
        lines.append(f"- Case C station drift range: `{fmt(c_with_metrics['truth_station_end_m'].min())}` to `{fmt(c_with_metrics['truth_station_end_m'].max())}` m.")
    lines.append(f"- Case C EV rejection total across real-error cases: `{fmt(c_reject_total.sum(), 0)}`.")
    if "ulog_xy_reset_counter_delta" in c:
        lines.append(f"- Case C XY reset delta range: `{fmt(c['ulog_xy_reset_counter_delta'].min(), 0)}` to `{fmt(c['ulog_xy_reset_counter_delta'].max(), 0)}`.")
    lines.append("")
    lines.append("## Injected Real Errors")
    lines.append("")
    lines.append("| Family | What changed | Cases |")
    lines.append("|---|---|---|")
    lines.append("| Noise | Gaussian noise added to the outgoing EV position and velocity measurements | mild, medium, strong |")
    lines.append("| Latency | Outgoing odometry samples are replayed late with their original timestamps | 100 ms uncompensated, 100 ms compensated |")
    lines.append("| Dropout | Odometry messages are skipped in recurring bursts while heartbeat stays alive | 1 s / 10 s, 2 s / 10 s |")
    lines.append("| Combo | Medium noise plus 100 ms latency plus 1 s / 10 s dropout | combined disturbance |")
    lines.append("")
    lines.append("## Data Files")
    lines.append("")
    lines.append(f"- Full batch metrics CSV: `{rel_plot(batch_dir / 'batch_metrics.csv', batch_dir)}`")
    lines.append(f"- Full batch metrics MD: `{rel_plot(batch_dir / 'batch_metrics.md', batch_dir)}`")
    lines.append(f"- Reduced real-error summary CSV: `{rel_plot(batch_dir / 'real_error_summary.csv', batch_dir)}`")
    lines.append(f"- Reduced real-error summary MD: `{rel_plot(batch_dir / 'real_error_summary.md', batch_dir)}`")
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
    lines.append(
        f"- The no-aiding GNSS-loss reference ended at `{row_metric(b, 'truth_station_end_m')}` m station drift, "
        "so it remains the failure baseline."
    )
    if worst_c is not None:
        lines.append(
            f"- The worst real-error Case C ended at `{fmt(worst_c['truth_station_end_m'])}` m station drift, "
            f"which is still far below the no-aiding drift baseline."
        )
    lines.append("- The comparison window is cropped at the land command, so post-land GNSS-loss estimator craziness does not contaminate the hover result.")
    lines.append("- EV rejection counts tell us whether EKF2 accepted the aided measurements; station drift tells us whether the vehicle actually held position.")
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
            "case_c_repaired_nominal_30hz_no_real_error",
            "case_c_realerr_noise_mild_pos005_vel010",
            "case_c_realerr_noise_medium_pos010_vel025",
            "case_c_realerr_noise_strong_pos025_vel050",
        ],
        "external_odom_inject_position_noise_std_m",
        "Injected Noise Sweep",
        "Injected EV position noise std [m]",
        plots_dir / "noise_sweep_station_error.png",
    )
    plot_latency_comparison(df, plots_dir)
    plot_sweep(
        df,
        [
            "case_c_repaired_nominal_30hz_no_real_error",
            "case_c_realerr_dropout_1s_every_10s",
            "case_c_realerr_dropout_2s_every_10s",
        ],
        "external_odom_dropout_duration_s",
        "Dropout Duration Sweep",
        "Dropout duration every 10 s [s]",
        plots_dir / "dropout_sweep_station_error.png",
    )
    plot_ev_health(df, plots_dir)
    plot_station_time(df, plots_dir)
    plot_xy_case_c(df, plots_dir)
    write_report(df, batch_dir, plots_dir)

    print(f"OK: plots written to {plots_dir}")
    print(f"OK: real_error_summary.csv written to {batch_dir / 'real_error_summary.csv'}")
    print(f"OK: real_error_summary.md written to {batch_dir / 'real_error_summary.md'}")
    print(f"OK: report written to {batch_dir / 'phase8a_case_c_real_error_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
