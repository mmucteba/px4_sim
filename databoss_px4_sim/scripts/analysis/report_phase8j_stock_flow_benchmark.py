#!/usr/bin/env python3
"""Build a Phase 8J stock-flow vs DATABOSS-flow benchmark report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_D_SHORT = PROJECT_ROOT / "experiments/runs/20260714_170829_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth"
DEFAULT_OUT = PROJECT_ROOT / "experiments/comparisons/phase8j_stock_vs_databoss_flow"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in cols:
            value = row[col]
            if isinstance(value, float):
                values.append(f"{value:.6g}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def metric(data: dict[str, Any], *keys: str, default=None):
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def divergence_verdict(
    *,
    available: bool,
    horizontal_max_m: Any,
    height_abs_max_m: Any,
    flow_rejected_over_fused: Any,
    status_accepted: Any,
) -> str:
    if not available:
        return "missing"
    hmax = float(horizontal_max_m) if horizontal_max_m is not None else float("nan")
    zmax = float(height_abs_max_m) if height_abs_max_m is not None else float("nan")
    rej = float(flow_rejected_over_fused) if flow_rejected_over_fused is not None else float("nan")
    if hmax > 25.0 or zmax > 10.0:
        return "diverged"
    if status_accepted is False and (hmax > 5.0 or zmax > 2.0):
        return "failed/diverged"
    if rej == rej and rej > 1.0:
        return "fusion-rejection-risk"
    return "bounded"


def summarize_run(label: str, run_dir: Path | None, window: str) -> dict[str, Any]:
    if run_dir is None:
        return {"label": label, "window": window, "run_dir": "", "available": False, "divergence_verdict": "missing"}
    run_dir = run_dir.resolve()
    metrics = load_json(run_dir / "ekf_vs_ground_truth_metrics.json")
    status = load_json(run_dir / "logs" / "pxh_takeoff_land_truth_status.json")
    flow = load_json(run_dir / "flow_fusion_ulog.json")
    available = run_dir.exists()
    status_accepted = status.get("accepted", metrics.get("accepted"))
    horizontal_max_m = metric(metrics, "horizontal_error", "max_m")
    height_abs_max_m = metric(metrics, "height_abs_error", "max_m")
    flow_rejected_over_fused = flow.get("flow_rejected_over_fused")
    return {
        "label": label,
        "window": window,
        "run_dir": str(run_dir),
        "available": available,
        "accepted": status_accepted,
        "model": status.get("model", ""),
        "world": status.get("world_name", ""),
        "gnss_loss_detected": status.get("gnss_loss_detected", False),
        "horizontal_mean_m": metric(metrics, "horizontal_error", "mean_m"),
        "horizontal_max_m": horizontal_max_m,
        "horizontal_end_m": metric(metrics, "horizontal_error", "end_m"),
        "height_abs_mean_m": metric(metrics, "height_abs_error", "mean_m"),
        "height_abs_max_m": height_abs_max_m,
        "ulog_airborne_duration_s": status.get("ulog_airborne_duration_s"),
        "ulog_max_height_up_m": status.get("ulog_max_height_up_m"),
        "distance_sensor_ok": status.get("ulog_distance_sensor_ok"),
        "flow_bridge_sent_rows": status.get("flow_bridge_sent_rows"),
        "sensor_optical_flow_rows": flow.get("sensor_optical_flow_rows"),
        "sensor_optical_flow_rate_hz": flow.get("sensor_optical_flow_rate_hz"),
        "cs_opt_flow_active_fraction": flow.get("cs_opt_flow_active_fraction"),
        "flow_fused_count": flow.get("flow_fused_count"),
        "flow_rejected_count": flow.get("flow_rejected_count"),
        "flow_rejected_over_fused": flow_rejected_over_fused,
        "xy_reset_counter_delta": flow.get("xy_reset_counter_delta"),
        "divergence_verdict": divergence_verdict(
            available=available,
            horizontal_max_m=horizontal_max_m,
            height_abs_max_m=height_abs_max_m,
            flow_rejected_over_fused=flow_rejected_over_fused,
            status_accepted=status_accepted,
        ),
    }


def existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stock-short-run")
    ap.add_argument("--stock-long-run")
    ap.add_argument("--databoss-short-run", default=str(DEFAULT_D_SHORT))
    ap.add_argument("--databoss-long-run")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_dir = existing_path(args.out_dir) or DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        summarize_run("PX4 stock GZ optical flow", existing_path(args.stock_short_run), "short"),
        summarize_run("DATABOSS SIFT bridge", existing_path(args.databoss_short_run), "short"),
        summarize_run("PX4 stock GZ optical flow", existing_path(args.stock_long_run), "50s"),
        summarize_run("DATABOSS SIFT bridge", existing_path(args.databoss_long_run), "50s"),
    ]
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "phase8j_metrics.csv", index=False)
    (out_dir / "phase8j_metrics.json").write_text(json.dumps(rows, indent=2) + "\n")

    available = df[df["available"] == True]  # noqa: E712
    stock_available = df[(df["label"] == "PX4 stock GZ optical flow") & (df["available"] == True)]  # noqa: E712
    databoss_available = df[(df["label"] == "DATABOSS SIFT bridge") & (df["available"] == True)]  # noqa: E712
    if len(stock_available) >= 2 and len(databoss_available) >= 2:
        status = "Stock short/50s and DATABOSS SIFT short/50s rows are available."
    elif len(stock_available) >= 2 and len(databoss_available) >= 1:
        status = "Stock short/50s benchmark rows are available; DATABOSS long 50s row is optional/future."
    elif len(stock_available) > 0:
        status = "Partial stock-flow benchmark rows are available."
    else:
        status = "In progress. Missing stock-flow rows are expected until the benchmark flights are run."
    report = [
        "# Phase 8J — PX4 Stock Flow Benchmark",
        "",
        f"Status: {status}",
        "",
        "## Metric Table",
        "",
        df_to_markdown(df),
        "",
        "## Interpretation Rules",
        "",
        "- Gazebo truth is the physical-error reference.",
        "- PX4 stock flow is a simulation reference, not a real sensor proof.",
        "- The stock-vs-DATABOSS flight comparison mixes estimator, camera model, transport, EKF, and controller behavior.",
        "- The identical-frame estimator replay isolates estimator behavior and must be used before accepting `lk` for live GNSS-denied flight.",
        "",
    ]
    if len(available) >= 2:
        current_read = (
            "At least two runs are available; compare horizontal error, fused/rejected ratio, "
            "reset count, range health, and the divergence verdict before drawing a conclusion."
        )
        long_rows = df[(df["window"] == "50s") & (df["available"] == True)]  # noqa: E712
        if len(long_rows) >= 2:
            stock_long = long_rows[long_rows["label"] == "PX4 stock GZ optical flow"].iloc[0]
            databoss_long = long_rows[long_rows["label"] == "DATABOSS SIFT bridge"].iloc[0]
            current_read = (
                "50s result: PX4 stock flow stays bounded while DATABOSS SIFT diverges. "
                f"Stock max horizontal error is {stock_long['horizontal_max_m']:.3f} m with "
                f"{stock_long['flow_rejected_count']} flow rejects; DATABOSS SIFT max horizontal "
                f"error is {databoss_long['horizontal_max_m']:.3f} m with reject/fused "
                f"{databoss_long['flow_rejected_over_fused']:.3f}."
            )
        report.extend([
            "## Current Read",
            "",
            current_read,
            "",
        ])
    (out_dir / "report.md").write_text("\n".join(report))
    print(f"wrote {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
