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
DEFAULT_OUT = PROJECT_ROOT / "experiments/comparisons/20260720_phase10_gnssloss_lk_xy_nmin03_pair"


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    run_dir: Path
    color: str


CASES = [
    Case(
        key="phase10_085443",
        label="Phase 10 08:54:43",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260720_085443_phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#1f77b4",
    ),
    Case(
        key="phase10_090850",
        label="Phase 10 09:08:50",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260720_090850_phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#d62728",
    ),
]


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def num(series: pd.Series) -> pd.Series:
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


def route_metrics(df: pd.DataFrame) -> dict[str, float]:
    x = num(df["gz_x_rel"]).to_numpy()
    y = num(df["gz_y_rel"]).to_numpy()
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if len(x) < 2:
        return {
            "truth_path_m": float("nan"),
            "truth_end_m": float("nan"),
            "truth_straightness": float("nan"),
        }
    steps = np.hypot(np.diff(x), np.diff(y))
    path = float(np.sum(steps))
    end = float(np.hypot(x[-1] - x[0], y[-1] - y[0]))
    return {
        "truth_path_m": path,
        "truth_end_m": end,
        "truth_straightness": end / path if path else float("nan"),
    }


def load_flow_aid(case: Case, out_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    ulg = case.run_dir / "logs/flight.ulg"
    if not ulg.exists():
        raise FileNotFoundError(ulg)
    ulog = ULog(str(ulg))
    aid = ulog.get_dataset("estimator_aid_src_optical_flow").data
    df = pd.DataFrame({key: aid[key] for key in aid.keys()})
    ts = num(df["timestamp"]).to_numpy(dtype=float)
    df["t_rel_s"] = (ts - ts[0]) / 1_000_000.0
    df["fused"] = num(df["fused"]).fillna(0).astype(int)
    df["innovation_rejected"] = num(df["innovation_rejected"]).fillna(0).astype(int)
    df["test_ratio_max"] = pd.concat(
        [num(df["test_ratio[0]"]), num(df["test_ratio[1]"])],
        axis=1,
    ).max(axis=1)

    binned = (
        df.assign(bin_s=np.floor(df["t_rel_s"]).astype(int))
        .groupby("bin_s")
        .agg(
            samples=("fused", "size"),
            fused=("fused", "sum"),
            rejected=("innovation_rejected", "sum"),
            test_ratio_p95=("test_ratio_max", lambda s: float(s.quantile(0.95))),
            test_ratio_max=("test_ratio_max", "max"),
        )
        .reset_index()
    )
    binned["fusion_fraction"] = binned["fused"] / binned["samples"]
    binned["rejected_fraction"] = binned["rejected"] / binned["samples"]
    binned["sample_rate_hz"] = binned["samples"]

    flow_dir = out_dir / "data"
    flow_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(flow_dir / f"{case.key}_estimator_aid_src_optical_flow.csv", index=False)
    binned.to_csv(flow_dir / f"{case.key}_optical_flow_fusion_1s_bins.csv", index=False)

    total = int(len(df))
    fused = int(df["fused"].sum())
    rejected = int(df["innovation_rejected"].sum())
    metrics = {
        "of_aid_rows": total,
        "of_fused_count": fused,
        "of_rejected_count": rejected,
        "of_fused_fraction": fused / total if total else float("nan"),
        "of_rejected_fraction": rejected / total if total else float("nan"),
        "of_sample_rate_mean_hz": float(binned["sample_rate_hz"].mean()) if len(binned) else float("nan"),
        "of_test_ratio_max": float(df["test_ratio_max"].max()) if total else float("nan"),
        "of_test_ratio_p95": float(df["test_ratio_max"].quantile(0.95)) if total else float("nan"),
    }
    return binned, metrics


def plot_route_overlay(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(8.4, 7.2))
    for case, df in cases_data:
        gx = num(df["gz_x_rel"])
        gy = num(df["gz_y_rel"])
        px = num(df["px4_x_rel"])
        py = num(df["px4_y_rel"])
        plt.plot(gx, gy, color=case.color, linewidth=2.4, label=f"{case.label} Gazebo truth")
        plt.plot(px, py, color=case.color, linewidth=1.5, linestyle="--", label=f"{case.label} PX4 EKF")
        plt.scatter([gx.iloc[0]], [gy.iloc[0]], color=case.color, s=28, marker="o")
        plt.scatter([gx.iloc[-1]], [gy.iloc[-1]], color=case.color, s=64, marker="x")
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.0, alpha=0.45, label="10 m route reference")
    plt.xlabel("relative X / North (m)")
    plt.ylabel("relative Y / East (m)")
    plt.title("Phase 10 GNSS-loss route: Gazebo truth vs PX4 EKF")
    plt.axis("equal")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "route_ekf_vs_gazebo_truth_overlay.png")


def plot_truth_panels(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.6), sharex=False, sharey=False)
    for ax, (case, df) in zip(axes, cases_data):
        ax.plot(num(df["gz_x_rel"]), num(df["gz_y_rel"]), color="#111111", linewidth=2.0, label="Gazebo truth")
        ax.plot(num(df["px4_x_rel"]), num(df["px4_y_rel"]), color=case.color, linewidth=1.5, linestyle="--", label="PX4 EKF")
        ax.plot([0, 0], [0, 10], color="#777777", linewidth=1.0, alpha=0.45, label="10 m ref")
        ax.set_title(case.label)
        ax.set_xlabel("relative X / North (m)")
        ax.set_ylabel("relative Y / East (m)")
        ax.axis("equal")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
    savefig(plots_dir / "route_ekf_vs_gazebo_truth_panels.png")


def plot_errors(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(10.8, 5.4))
    for case, df in cases_data:
        plt.plot(num(df["px4_t_rel_s"]), num(df["horizontal_error_m"]), color=case.color, linewidth=1.8, label=case.label)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("horizontal EKF-vs-Gazebo error (m)")
    plt.title("Phase 10 horizontal error against Gazebo truth")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "horizontal_error_vs_gazebo_truth.png")

    plt.figure(figsize=(10.8, 5.4))
    for case, df in cases_data:
        plt.plot(num(df["px4_t_rel_s"]), num(df["abs_height_error_m"]), color=case.color, linewidth=1.8, label=case.label)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("absolute height error (m)")
    plt.title("Phase 10 height error against Gazebo truth")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "height_error_vs_gazebo_truth.png")


def plot_route_progress(cases_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(10.8, 5.4))
    for case, df in cases_data:
        plt.plot(num(df["px4_t_rel_s"]), num(df["gz_y_rel"]), color=case.color, linewidth=1.9, label=f"{case.label} truth Y/East")
        plt.plot(
            num(df["px4_t_rel_s"]),
            num(df["gazebo_horizontal_displacement_from_start_m"]),
            color=case.color,
            linewidth=1.2,
            linestyle=":",
            label=f"{case.label} truth displacement",
        )
    plt.axhline(10.0, color="#111111", linewidth=1.0, alpha=0.35, label="10 m reference")
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("route progress (m)")
    plt.title("Phase 10 Gazebo truth route progress")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "gazebo_truth_route_progress.png")


def plot_flow_fusion(flow_data: list[tuple[Case, pd.DataFrame]], plots_dir: Path) -> None:
    plt.figure(figsize=(10.8, 5.4))
    for case, df in flow_data:
        plt.plot(df["bin_s"], df["fusion_fraction"], color=case.color, linewidth=1.8, label=f"{case.label} fused fraction")
        plt.plot(df["bin_s"], df["rejected_fraction"], color=case.color, linewidth=1.2, linestyle="--", label=f"{case.label} rejected fraction")
    plt.ylim(-0.02, 1.05)
    plt.xlabel("optical-flow aid relative time (s)")
    plt.ylabel("1 s fraction")
    plt.title("Phase 10 optical-flow fusion/rejection fraction")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_fusion_fraction_1s.png")

    plt.figure(figsize=(10.8, 5.4))
    for case, df in flow_data:
        plt.plot(df["bin_s"], df["sample_rate_hz"], color=case.color, linewidth=1.8, label=case.label)
    plt.xlabel("optical-flow aid relative time (s)")
    plt.ylabel("aid-source samples per second")
    plt.title("Phase 10 optical-flow aid-source sample rate")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=9)
    savefig(plots_dir / "optical_flow_aid_sample_rate_1s.png")

    plt.figure(figsize=(10.8, 5.4))
    for case, df in flow_data:
        plt.plot(df["bin_s"], df["test_ratio_p95"], color=case.color, linewidth=1.8, label=f"{case.label} p95")
        plt.plot(df["bin_s"], df["test_ratio_max"], color=case.color, linewidth=1.0, linestyle=":", label=f"{case.label} max")
    plt.axhline(1.0, color="#111111", linewidth=1.0, alpha=0.35, label="reject threshold")
    plt.xlabel("optical-flow aid relative time (s)")
    plt.ylabel("innovation test ratio")
    plt.title("Phase 10 optical-flow innovation test ratio")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_test_ratio_1s.png")


def plot_summary_bars(summary: pd.DataFrame, plots_dir: Path) -> None:
    labels = summary["label"].to_list()
    x = np.arange(len(labels))
    width = 0.2
    plt.figure(figsize=(9.6, 5.2))
    plt.bar(x - 1.5 * width, summary["truth_end_m"], width, label="truth end")
    plt.bar(x - 0.5 * width, summary["truth_path_m"], width, label="truth path")
    plt.bar(x + 0.5 * width, summary["horizontal_error_max_m"], width, label="max horiz error")
    plt.bar(x + 1.5 * width, summary["height_error_max_m"], width, label="max height error")
    plt.xticks(x, labels, rotation=8, ha="right")
    plt.ylabel("meters")
    plt.title("Phase 10 GNSS-loss summary metrics")
    plt.grid(True, axis="y", alpha=0.25)
    plt.legend(fontsize=8)
    savefig(plots_dir / "summary_metric_bars.png")


def write_report(out_dir: Path, summary: pd.DataFrame) -> None:
    rows = []
    for _, row in summary.iterrows():
        rows.append(
            "| {label} | {validation_accepted} | {metrics_accepted} | {effective_gnss_loss_after_takeoff_s:.1f} | "
            "{truth_path_m:.2f} | {truth_end_m:.2f} | {truth_straightness:.3f} | "
            "{horizontal_error_max_m:.2f} | {of_fused_count}/{of_aid_rows} | {of_rejected_count} |".format(**row)
        )

    report = [
        "# Phase 10 GNSS-loss LK xy nmin0.3 comparison",
        "",
        "Generated from the two latest Phase 10 GNSS-loss run folders. Gazebo truth is the physical reference.",
        "",
        "## Why the metric files are not accepted",
        "",
        "Both low-level flight validations are accepted, but both `ekf_vs_ground_truth_metrics.json` files have "
        "`accepted=false` because the alignment step used `comparison_window=until-land-command` while these "
        "runs intentionally use `control.skip_landing_command=true`. The comparison window is therefore marked "
        "`comparison_window_ok=false` with `comparison_end_reason=land_command_not_found`.",
        "",
        "A second limitation remains: the requested GNSS-loss time is 20 s after takeoff, but the effective "
        "recorded GNSS-loss time is 10 s after takeoff in both runs.",
        "",
        "## Summary",
        "",
        "| Run | Validation accepted | Metrics accepted | Effective GNSS loss s | Truth path m | Truth end m | Truth straightness | Max horiz error m | OF fused | OF rejected |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *rows,
        "",
        "## Plots",
        "",
        "- `plots/route_ekf_vs_gazebo_truth_overlay.png`",
        "- `plots/route_ekf_vs_gazebo_truth_panels.png`",
        "- `plots/gazebo_truth_route_progress.png`",
        "- `plots/horizontal_error_vs_gazebo_truth.png`",
        "- `plots/height_error_vs_gazebo_truth.png`",
        "- `plots/optical_flow_fusion_fraction_1s.png`",
        "- `plots/optical_flow_aid_sample_rate_1s.png`",
        "- `plots/optical_flow_test_ratio_1s.png`",
        "- `plots/summary_metric_bars.png`",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(report))


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot the latest two Phase 10 GNSS-loss LK xy runs.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    plots_dir = out_dir / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    cases_data = []
    flow_data = []
    summary_rows = []
    for case in CASES:
        df, metrics, status = load_case(case)
        flow_bins, flow_metrics = load_flow_aid(case, out_dir)
        rmetrics = route_metrics(df)
        row = {
            "key": case.key,
            "label": case.label,
            "run_dir": str(case.run_dir),
            "validation_accepted": bool(status.get("accepted")),
            "metrics_accepted": bool(metrics.get("accepted")),
            "comparison_window_ok": bool(metrics.get("comparison_window_ok")),
            "comparison_end_reason": str(metrics.get("comparison_end_reason")),
            "gnss_loss_after_takeoff_s": float(status.get("gnss_loss_after_takeoff_s") or float("nan")),
            "effective_gnss_loss_after_takeoff_s": float(status.get("effective_gnss_loss_after_takeoff_s") or float("nan")),
            "horizontal_error_max_m": float(nested(metrics, "horizontal_error", "max_m")),
            "horizontal_error_mean_m": float(nested(metrics, "horizontal_error", "mean_m")),
            "height_error_max_m": float(nested(metrics, "height_abs_error", "max_m")),
            **rmetrics,
            **flow_metrics,
        }
        summary_rows.append(row)
        cases_data.append((case, df))
        flow_data.append((case, flow_bins))

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out_dir / "summary.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(summary_rows, indent=2) + "\n")

    plot_route_overlay(cases_data, plots_dir)
    plot_truth_panels(cases_data, plots_dir)
    plot_errors(cases_data, plots_dir)
    plot_route_progress(cases_data, plots_dir)
    plot_flow_fusion(flow_data, plots_dir)
    plot_summary_bars(summary, plots_dir)
    write_report(out_dir, summary)

    print(f"wrote {out_dir}")
    print(f"summary={out_dir / 'summary.csv'}")
    print(f"report={out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
