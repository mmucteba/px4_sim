#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/databoss-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyulog import ULog


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    run_dir: Path
    color: str


CASES = [
    Case(
        key="lk_bounded_50s",
        label="DATABOSS LK/LD",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260717_132616_phase8k_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#1f77b4",
    ),
    Case(
        key="stock_px4_flow_50s",
        label="PX4 stock flow",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260717_133641_phase8j_stock_flow_50s_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#d62728",
    ),
]


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def finite(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def nested(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def load_case(case: Case) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    aligned_path = case.run_dir / "ekf_vs_ground_truth_aligned.csv"
    metrics_path = case.run_dir / "ekf_vs_ground_truth_metrics.json"
    status_path = case.run_dir / "logs/pxh_takeoff_land_truth_status.json"
    if not aligned_path.exists():
        raise FileNotFoundError(aligned_path)
    if not metrics_path.exists():
        raise FileNotFoundError(metrics_path)
    if not status_path.exists():
        raise FileNotFoundError(status_path)
    return (
        pd.read_csv(aligned_path),
        json.loads(metrics_path.read_text()),
        json.loads(status_path.read_text()),
    )


def load_optical_flow_aid(case: Case, out_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    ulog_path = case.run_dir / "logs/flight.ulg"
    if not ulog_path.exists():
        raise FileNotFoundError(ulog_path)
    ulog = ULog(str(ulog_path))
    datasets = [dataset for dataset in ulog.data_list if dataset.name == "estimator_aid_src_optical_flow"]
    if not datasets:
        raise RuntimeError(f"missing estimator_aid_src_optical_flow in {ulog_path}")
    data = datasets[0].data
    df = pd.DataFrame({key: data[key] for key in data.keys()})
    df["t_rel_s"] = (finite(df["timestamp"]) - float(finite(df["timestamp"]).iloc[0])) / 1_000_000.0
    df["fused"] = finite(df["fused"]).fillna(0).astype(int)
    df["innovation_rejected"] = finite(df["innovation_rejected"]).fillna(0).astype(int)
    df["not_fused"] = 1 - df["fused"]
    df["test_ratio_max"] = pd.concat(
        [finite(df["test_ratio[0]"]), finite(df["test_ratio[1]"])],
        axis=1,
    ).max(axis=1)
    df["innovation_abs_max"] = pd.concat(
        [finite(df["innovation[0]"]).abs(), finite(df["innovation[1]"]).abs()],
        axis=1,
    ).max(axis=1)

    flow_dir = out_dir / "optical_flow_aid"
    flow_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(flow_dir / f"{case.key}_estimator_aid_src_optical_flow.csv", index=False)

    total = int(len(df))
    fused = int(df["fused"].sum())
    rejected = int(df["innovation_rejected"].sum())
    not_fused = int(total - fused)
    metrics = {
        "of_aid_samples": total,
        "of_fused_count": fused,
        "of_not_fused_count": not_fused,
        "of_rejected_count": rejected,
        "of_fused_fraction": float(fused / total) if total else np.nan,
        "of_rejected_fraction": float(rejected / total) if total else np.nan,
        "of_test_ratio_max": float(df["test_ratio_max"].max()) if total else np.nan,
        "of_test_ratio_p95": float(df["test_ratio_max"].quantile(0.95)) if total else np.nan,
        "of_test_ratio_over_1_count": int((df["test_ratio_max"] > 1.0).sum()),
        "of_innovation_abs_max": float(df["innovation_abs_max"].max()) if total else np.nan,
    }
    return df, metrics


def build_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def plot_truth_overlay(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(8.2, 7.2))
    for case, df in cases_data:
        gx = finite(df["gz_x_rel"])
        gy = finite(df["gz_y_rel"])
        plt.plot(gx, gy, color=case.color, linewidth=2.3, label=f"{case.label} truth")
        if len(gx.dropna()) and len(gy.dropna()):
            plt.scatter([gx.iloc[0]], [gy.iloc[0]], color=case.color, s=24, marker="o")
            plt.scatter([gx.iloc[-1]], [gy.iloc[-1]], color=case.color, s=60, marker="x")
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.2, alpha=0.45, label="10 m +Y/East reference")
    plt.axvline(0.0, color="#777777", linewidth=0.8, alpha=0.35)
    plt.axhline(0.0, color="#777777", linewidth=0.8, alpha=0.35)
    plt.xlabel("relative X / North (m)")
    plt.ylabel("relative Y / East (m)")
    plt.title("Phase 8M route truth overlay")
    plt.axis("equal")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "route_truth_overlay.png")


def plot_ekf_vs_truth(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.8), sharex=False, sharey=False)
    for ax, (case, df) in zip(axes, cases_data):
        ax.plot(finite(df["gz_x_rel"]), finite(df["gz_y_rel"]), color="#111111", linewidth=2.0, label="Gazebo truth")
        ax.plot(
            finite(df["px4_x_rel"]),
            finite(df["px4_y_rel"]),
            color=case.color,
            linewidth=1.6,
            linestyle="--",
            label="PX4 EKF",
        )
        ax.plot([0, 0], [0, 10], color="#777777", linewidth=1.0, alpha=0.45, label="10 m +Y/East ref")
        ax.set_title(case.label)
        ax.set_xlabel("relative X / North (m)")
        ax.set_ylabel("relative Y / East (m)")
        ax.grid(True, alpha=0.25)
        ax.axis("equal")
        ax.legend(fontsize=8)
    savefig(plots_dir / "route_ekf_vs_truth_panels.png")


def plot_route_progress(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(10.5, 5.6))
    for case, df in cases_data:
        t = finite(df["px4_t_rel_s"])
        y = finite(df["gz_y_rel"])
        disp = finite(df["gazebo_horizontal_displacement_from_start_m"])
        plt.plot(t, y, color=case.color, linewidth=2.0, label=f"{case.label}: truth East/Y")
        plt.plot(t, disp, color=case.color, linewidth=1.3, linestyle=":", label=f"{case.label}: truth displacement")
    plt.axhline(10.0, color="#111111", linewidth=1.1, alpha=0.35, label="10 m reference")
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("route progress (m)")
    plt.title("Phase 8M truth route progress")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "route_progress_timeseries.png")


def plot_error_timeseries(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(10.5, 5.4))
    for case, df in cases_data:
        plt.plot(
            finite(df["px4_t_rel_s"]),
            finite(df["horizontal_error_m"]),
            color=case.color,
            linewidth=1.8,
            label=case.label,
        )
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("horizontal EKF-vs-truth error (m)")
    plt.title("Phase 8M horizontal error over time")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "horizontal_error_timeseries.png")

    plt.figure(figsize=(10.5, 5.4))
    for case, df in cases_data:
        plt.plot(
            finite(df["px4_t_rel_s"]),
            finite(df["abs_height_error_m"]),
            color=case.color,
            linewidth=1.8,
            label=case.label,
        )
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("absolute height error (m)")
    plt.title("Phase 8M height error over time")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "height_error_timeseries.png")


def plot_metric_bars(summary: pd.DataFrame, plots_dir: Path) -> None:
    labels = summary["label"].to_list()
    x = np.arange(len(labels))
    width = 0.24

    plt.figure(figsize=(9.4, 5.4))
    plt.bar(x - width, summary["truth_end_m"], width, label="truth end")
    plt.bar(x, summary["px4_end_m"], width, label="EKF end")
    plt.bar(x + width, summary["horizontal_end_error_m"], width, label="horizontal end error")
    plt.axhline(10.0, color="#111111", linewidth=1.0, alpha=0.35, label="10 m reference")
    plt.xticks(x, labels)
    plt.ylabel("meters")
    plt.title("Phase 8M route end comparison")
    plt.grid(True, axis="y", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "route_end_bars.png")

    plt.figure(figsize=(9.4, 5.4))
    plt.bar(x - width, summary["horizontal_mean_m"], width, label="mean")
    plt.bar(x, summary["horizontal_p95_m"], width, label="p95")
    plt.bar(x + width, summary["horizontal_max_m"], width, label="max")
    plt.xticks(x, labels)
    plt.ylabel("horizontal error (m)")
    plt.title("Phase 8M horizontal error summary")
    plt.grid(True, axis="y", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "horizontal_error_bars.png")


def plot_optical_flow_aid(
    flow_data: list[tuple[Case, pd.DataFrame]],
    summary: pd.DataFrame,
    plots_dir: Path,
) -> None:
    labels = summary["label"].to_list()
    x = np.arange(len(labels))
    width = 0.25

    plt.figure(figsize=(9.8, 5.4))
    plt.bar(x - width, summary["of_fused_count"], width, color="#2ca02c", label="fused/accepted")
    plt.bar(x, summary["of_not_fused_count"], width, color="#ff7f0e", label="not fused incl. rejected")
    plt.bar(x + width, summary["of_rejected_count"], width, color="#d62728", label="innovation rejected")
    plt.xticks(x, labels)
    plt.ylabel("EKF optical-flow aid samples")
    plt.title("Phase 8M EKF optical-flow fused/rejected counts")
    plt.grid(True, axis="y", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "optical_flow_fused_rejected_counts.png")

    fig, axes = plt.subplots(len(flow_data), 1, figsize=(11.2, 6.8), sharex=False)
    if len(flow_data) == 1:
        axes = [axes]
    for ax, (case, df) in zip(axes, flow_data):
        t = finite(df["t_rel_s"])
        ax.step(t, finite(df["fused"]), where="post", color="#2ca02c", linewidth=1.4, label="fused/accepted")
        ax.step(
            t,
            finite(df["innovation_rejected"]),
            where="post",
            color="#d62728",
            linewidth=1.2,
            label="innovation rejected",
        )
        ax.set_ylim(-0.1, 1.25)
        ax.set_yticks([0, 1])
        ax.set_ylabel(case.label)
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("relative optical-flow aid time (s)")
    fig.suptitle("Phase 8M EKF optical-flow accepted/rejected flags over time")
    savefig(plots_dir / "optical_flow_fused_rejected_timeseries.png")

    plt.figure(figsize=(11.0, 5.6))
    for case, df in flow_data:
        t = finite(df["t_rel_s"])
        fused_cum = finite(df["fused"]).fillna(0).cumsum()
        rejected_cum = finite(df["innovation_rejected"]).fillna(0).cumsum()
        plt.plot(t, fused_cum, color=case.color, linewidth=1.9, label=f"{case.label} fused/accepted")
        plt.plot(t, rejected_cum, color=case.color, linewidth=1.5, linestyle="--", label=f"{case.label} rejected")
    plt.xlabel("relative optical-flow aid time (s)")
    plt.ylabel("cumulative EKF optical-flow aid samples")
    plt.title("Phase 8M cumulative EKF optical-flow accepted/rejected samples")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_cumulative_fused_rejected.png")

    plt.figure(figsize=(11.0, 5.6))
    for case, df in flow_data:
        plt.plot(
            finite(df["t_rel_s"]),
            finite(df["test_ratio_max"]),
            color=case.color,
            linewidth=1.5,
            label=f"{case.label} max test ratio",
        )
    plt.axhline(1.0, color="#d62728", linewidth=1.0, alpha=0.6, label="EKF reject threshold 1.0")
    plt.xlabel("relative optical-flow aid time (s)")
    plt.ylabel("max optical-flow innovation test ratio")
    plt.title("Phase 8M optical-flow EKF test ratio over time")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "optical_flow_test_ratio_timeseries.png")

    fig, axes = plt.subplots(2, 1, figsize=(11.0, 7.4), sharex=False)
    for case, df in flow_data:
        t = finite(df["t_rel_s"])
        axes[0].plot(t, finite(df["innovation[0]"]), color=case.color, linewidth=1.2, label=f"{case.label} X")
        axes[0].plot(t, finite(df["innovation[1]"]), color=case.color, linewidth=1.0, linestyle="--", label=f"{case.label} Y")
        axes[1].plot(t, finite(df["observation[0]"]), color=case.color, linewidth=1.2, label=f"{case.label} obs X")
        axes[1].plot(t, finite(df["observation[1]"]), color=case.color, linewidth=1.0, linestyle="--", label=f"{case.label} obs Y")
    axes[0].set_ylabel("innovation")
    axes[0].set_title("Optical-flow EKF innovation")
    axes[1].set_ylabel("observation")
    axes[1].set_title("Optical-flow EKF observation")
    axes[1].set_xlabel("relative optical-flow aid time (s)")
    for ax in axes:
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, ncol=2)
    savefig(plots_dir / "optical_flow_innovation_observation_timeseries.png")


def write_report(out_dir: Path, summary: pd.DataFrame) -> None:
    csv_path = out_dir / "route_compare_summary.csv"
    summary.to_csv(csv_path, index=False)

    lines = [
        "# Phase 8M Route Comparison",
        "",
        "Two accepted 50 s GNSS-loss route runs compared from `ekf_vs_ground_truth_aligned.csv`.",
        "Gazebo truth is the physical route judge; PX4 EKF is plotted separately.",
        "",
        "## Summary",
        "",
        "| Case | Accepted | Truth end m | EKF end m | Mean horiz err m | Max horiz err m | End horiz err m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.label} | {row.accepted} | {row.truth_end_m:.3f} | {row.px4_end_m:.3f} | "
            f"{row.horizontal_mean_m:.3f} | {row.horizontal_max_m:.3f} | {row.horizontal_end_error_m:.3f} |"
        )

    lines.extend(
        [
            "",
            "## EKF Optical-Flow Fusion",
            "",
            "| Case | Aid samples | Fused/accepted | Not fused incl. rejected | Innovation rejected | Fused % | Max test ratio | Test ratio > 1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.label} | {row.of_aid_samples} | {row.of_fused_count} | {row.of_not_fused_count} | "
            f"{row.of_rejected_count} | {100.0 * row.of_fused_fraction:.1f}% | "
            f"{row.of_test_ratio_max:.3f} | {row.of_test_ratio_over_1_count} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- PX4 stock optical flow tracks the intended +Y/East route much closer in this run.",
            "- DATABOSS LK/LD remains accepted and keeps the bridge alive, but its physical route under-runs the reference and EKF-vs-truth disagreement is larger.",
            "- The LK/LD run has substantial EKF optical-flow innovation rejection: `713` rejected aid samples and only `55.0%` fused/accepted.",
            "- The stock PX4 flow run has `0` optical-flow innovation rejections and `96.5%` fused/accepted aid samples.",
            "- The plots use the project frame convention: X is PX4 North, Y is PX4 East; Gazebo truth has already been converted into that frame by the alignment step.",
            "",
            "## Plots",
            "",
            "![Truth route overlay](plots/route_truth_overlay.png)",
            "",
            "![EKF vs truth route panels](plots/route_ekf_vs_truth_panels.png)",
            "",
            "![Truth route progress](plots/route_progress_timeseries.png)",
            "",
            "![Horizontal error time series](plots/horizontal_error_timeseries.png)",
            "",
            "![Route end bars](plots/route_end_bars.png)",
            "",
            "![Optical-flow fused/rejected counts](plots/optical_flow_fused_rejected_counts.png)",
            "",
            "![Optical-flow fused/rejected time series](plots/optical_flow_fused_rejected_timeseries.png)",
            "",
            "![Cumulative optical-flow fused/rejected samples](plots/optical_flow_cumulative_fused_rejected.png)",
            "",
            "![Optical-flow test ratio time series](plots/optical_flow_test_ratio_timeseries.png)",
            "",
            "![Optical-flow innovation and observation](plots/optical_flow_innovation_observation_timeseries.png)",
            "",
            f"CSV summary: `{csv_path}`",
            "",
            f"Per-sample optical-flow aid CSVs: `{out_dir / 'optical_flow_aid'}`",
            "",
        ]
    )
    (out_dir / "route_compare_report.md").write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot Phase 8M LK vs stock PX4 flow route comparison.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "experiments/batches/20260717_132613_phase8m_lk_vs_stock_flow_50s_compare/route_compare",
    )
    args = parser.parse_args()

    out_dir = args.out_dir
    plots_dir = out_dir / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    cases_data: list[tuple[Case, pd.DataFrame]] = []
    flow_data: list[tuple[Case, pd.DataFrame]] = []
    summary_rows: list[dict[str, Any]] = []
    for case in CASES:
        aligned, metrics, status = load_case(case)
        flow_df, flow_metrics = load_optical_flow_aid(case, out_dir)
        cases_data.append((case, aligned))
        flow_data.append((case, flow_df))
        summary_rows.append(
            {
                "case": case.key,
                "label": case.label,
                "run_dir": str(case.run_dir),
                "accepted": bool(metrics.get("accepted")) and bool(status.get("accepted")),
                "aligned_duration_s": nested(metrics, "aligned_duration_s"),
                "truth_end_m": nested(metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", "end_m"),
                "truth_max_m": nested(metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", "max_m"),
                "px4_end_m": nested(metrics, "station_keeping", "px4_horizontal_displacement_from_start", "end_m"),
                "px4_max_m": nested(metrics, "station_keeping", "px4_horizontal_displacement_from_start", "max_m"),
                "horizontal_mean_m": nested(metrics, "horizontal_error", "mean_m"),
                "horizontal_p95_m": nested(metrics, "horizontal_error", "p95_m"),
                "horizontal_max_m": nested(metrics, "horizontal_error", "max_m"),
                "horizontal_end_error_m": nested(metrics, "horizontal_error", "end_m"),
                "height_mean_m": nested(metrics, "height_abs_error", "mean_m"),
                "height_max_m": nested(metrics, "height_abs_error", "max_m"),
                "qgc_connected": bool(status.get("qgc_connected")),
                "gazebo_web_ok": bool(status.get("gazebo_web_ok")),
                "gnss_loss_ok": bool(status.get("gnss_loss_ok")),
                "ulog_copied": bool(status.get("ulog_copied")),
                "flow_bridge_sent_rows": status.get("flow_bridge_sent_rows"),
                "stock_flow_enabled": bool(status.get("stock_flow_enabled")),
                **flow_metrics,
            }
        )

    summary = build_summary(summary_rows)
    plot_truth_overlay(cases_data, plots_dir)
    plot_ekf_vs_truth(cases_data, plots_dir)
    plot_route_progress(cases_data, plots_dir)
    plot_error_timeseries(cases_data, plots_dir)
    plot_metric_bars(summary, plots_dir)
    plot_optical_flow_aid(flow_data, summary, plots_dir)
    write_report(out_dir, summary)

    print(f"report={out_dir / 'route_compare_report.md'}")
    print(f"summary={out_dir / 'route_compare_summary.csv'}")
    print(f"plots={plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
