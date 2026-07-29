#!/usr/bin/env python3
"""Build a Phase 8J stock-flow vs DATABOSS bridge replicate report."""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "experiments/comparisons/phase8j_stock_vs_sift_50s_replicates"
RUN_DIR_RE = re.compile(r"(?:run_dir=|Run dir: |Detected run_dir: )(/opt/databoss_px4_sim/experiments/runs/[^\s]+)")


def resolve_path(value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def metric(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def system_for_case(case_name: str, run_dir: Path | None, item: dict[str, Any]) -> str:
    if case_name.startswith("stock"):
        return "PX4 stock gz_x500_flow"
    if case_name.startswith("lk"):
        return "DATABOSS LK bridge"
    if case_name.startswith("sift"):
        return "DATABOSS SIFT bridge"
    scenario = str(item.get("case", {}).get("scenario", ""))
    if "lk" in scenario:
        return "DATABOSS LK bridge"
    if run_dir and run_dir.exists():
        cfg = load_yaml(run_dir / "config.yaml")
        flow_cfg = cfg.get("flow_bridge", {}) if isinstance(cfg, dict) else {}
        estimator = str(flow_cfg.get("estimator", "")).lower()
        if estimator == "lk":
            return "DATABOSS LK bridge"
        if estimator == "sift":
            return "DATABOSS SIFT bridge"
    return "DATABOSS SIFT bridge"


def read_batch_rows(batch_dir: Path) -> list[dict[str, Any]]:
    summary = load_json(batch_dir / "batch_summary.json")
    rows: list[dict[str, Any]] = []
    for item in summary.get("results", []):
        case_name = str(item.get("name", ""))
        log_path = resolve_path(item.get("log_path"))
        run_dir = resolve_path(item.get("run_dir"))
        if run_dir is None and log_path and log_path.exists():
            matches = RUN_DIR_RE.findall(log_path.read_text(errors="replace"))
            if matches:
                run_dir = Path(matches[-1]).resolve()
        rows.append(
            {
                "case": case_name,
                "system": system_for_case(case_name, run_dir, item),
                "returncode": item.get("returncode"),
                "batch_accepted": item.get("accepted"),
                "run_dir": run_dir,
                "log_path": log_path,
                "batch_dir": batch_dir,
            }
        )
    return rows


def run_tool(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout


def repair_run(run_dir: Path, force: bool = False) -> dict[str, Any]:
    notes: dict[str, Any] = {"run_dir": str(run_dir), "commands": []}
    if not run_dir.exists():
        notes["missing"] = True
        return notes
    if not (run_dir / "logs/flight.ulg").exists():
        notes["missing_ulog"] = True
        return notes

    if force or not (run_dir / "postprocess_summary.json").exists():
        cmd = [sys.executable, "scripts/runner/postprocess_latest_truth_run.py", "--run-dir", str(run_dir)]
        rc, out = run_tool(cmd, PROJECT_ROOT)
        notes["commands"].append({"cmd": cmd, "returncode": rc, "tail": out[-2000:]})

    if force or not (run_dir / "ekf_vs_ground_truth_metrics.json").exists():
        cmd = [
            sys.executable,
            "scripts/runner/align_latest_truth_run.py",
            "--run-dir",
            str(run_dir),
            "--comparison-window",
            "full",
        ]
        rc, out = run_tool(cmd, PROJECT_ROOT)
        notes["commands"].append({"cmd": cmd, "returncode": rc, "tail": out[-2000:]})

    flow_json = run_dir / "flow_fusion_ulog.json"
    if force or not flow_json.exists():
        cmd = [sys.executable, "scripts/analysis/analyze_flow_fusion_ulog.py", str(run_dir), "--json", str(flow_json)]
        rc, out = run_tool(cmd, PROJECT_ROOT)
        # This tool returns 2 for poor fusion; keep the output because that is
        # exactly the evidence we need for divergent SIFT runs.
        notes["commands"].append({"cmd": cmd, "returncode": rc, "tail": out[-2000:]})
    return notes


def divergence_verdict(row: dict[str, Any]) -> str:
    hmax = as_float(row.get("horizontal_max_m"))
    zmax = as_float(row.get("height_abs_max_m"))
    rej = as_float(row.get("flow_rejected_over_fused"))
    if hmax is None and zmax is None:
        return "missing"
    if (hmax is not None and hmax > 25.0) or (zmax is not None and zmax > 10.0):
        return "diverged"
    if rej is not None and rej > 1.0:
        return "fusion-rejection-risk"
    if row.get("status_accepted") is False and ((hmax or 0.0) > 5.0 or (zmax or 0.0) > 2.0):
        return "failed/diverged"
    return "bounded"


def summarize_run(case: dict[str, Any]) -> dict[str, Any]:
    run_dir: Path | None = case.get("run_dir")
    out = {
        "case": case["case"],
        "system": case["system"],
        "batch_returncode": case.get("returncode"),
        "batch_accepted": case.get("batch_accepted"),
        "run_dir": str(run_dir) if run_dir else "",
        "available": bool(run_dir and run_dir.exists()),
    }
    if not run_dir or not run_dir.exists():
        out["divergence_verdict"] = "missing"
        return out

    status = load_json(run_dir / "logs/pxh_takeoff_land_truth_status.json")
    metrics = load_json(run_dir / "ekf_vs_ground_truth_metrics.json")
    flow = load_json(run_dir / "flow_fusion_ulog.json")
    out.update(
        {
            "status_accepted": status.get("accepted", metrics.get("accepted")),
            "model": status.get("model", ""),
            "world": status.get("world_name", ""),
            "gnss_loss_detected": status.get("gnss_loss_detected"),
            "gps_fusion_after_loss_fraction": status.get("gps_fusion_after_loss_fraction"),
            "distance_sensor_ok": status.get("ulog_distance_sensor_ok"),
            "ulog_airborne_duration_s": status.get("ulog_airborne_duration_s"),
            "ulog_max_height_up_m": status.get("ulog_max_height_up_m"),
            "horizontal_mean_m": metric(metrics, "horizontal_error", "mean_m"),
            "horizontal_max_m": metric(metrics, "horizontal_error", "max_m"),
            "horizontal_end_m": metric(metrics, "horizontal_error", "end_m"),
            "height_abs_mean_m": metric(metrics, "height_abs_error", "mean_m"),
            "height_abs_max_m": metric(metrics, "height_abs_error", "max_m"),
            "sensor_optical_flow_rows": flow.get("sensor_optical_flow_rows"),
            "sensor_optical_flow_rate_hz": flow.get("sensor_optical_flow_rate_hz"),
            "flow_quality_mean": flow.get("flow_quality_mean"),
            "flow_quality_zero_fraction": flow.get("flow_quality_zero_fraction"),
            "cs_opt_flow_active_fraction": flow.get("cs_opt_flow_active_fraction"),
            "flow_fused_count": flow.get("flow_fused_count"),
            "flow_rejected_count": flow.get("flow_rejected_count"),
            "flow_rejected_over_fused": flow.get("flow_rejected_over_fused"),
            "xy_reset_counter_delta": flow.get("xy_reset_counter_delta"),
        }
    )
    out["divergence_verdict"] = divergence_verdict(out)
    return out


def rate_from_series(series: pd.Series) -> tuple[int, float | None, float | None, float | None]:
    values = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    n = int(len(values))
    if n < 2:
        return n, None, None, None
    duration = float(values.iloc[-1] - values.iloc[0])
    if duration <= 0:
        return n, duration, None, None
    dt = values.diff().dropna()
    return n, duration, float((n - 1) / duration), float(dt.median())


def summarize_bridge_rate(run_dir: Path, system: str = "") -> dict[str, Any]:
    cfg = load_yaml(run_dir / "config.yaml")
    flow_cfg = cfg.get("flow_bridge", {}) if isinstance(cfg, dict) else {}
    out: dict[str, Any] = {
        "run_dir": str(run_dir),
        "system": system,
        "estimator": flow_cfg.get("estimator"),
        "configured_rate_hz": flow_cfg.get("rate_hz"),
        "configured_min_period_ms": 1000.0 / float(flow_cfg.get("rate_hz")) if flow_cfg.get("rate_hz") else None,
    }

    frames_path = run_dir / "flow_recording/frames_index.csv"
    sent_path = run_dir / "flow_bridge/flow_bridge_sent.csv"
    range_path = run_dir / "flow_recording/rangefinder.csv"

    if frames_path.exists():
        frames = pd.read_csv(frames_path)
        n, duration, rate, median_dt = rate_from_series(frames["t_sim_s"])
        out.update(
            {
                "camera_frame_rows": n,
                "camera_duration_s": duration,
                "camera_rate_hz": rate,
                "camera_median_dt_ms": median_dt * 1000.0 if median_dt is not None else None,
            }
        )

    if sent_path.exists():
        sent = pd.read_csv(sent_path)
        n, duration, rate, median_dt = rate_from_series(sent["t_frame_sim_s"])
        sent_bool = pd.to_numeric(sent.get("sent", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int) > 0
        mav_bool = pd.to_numeric(sent.get("mavlink_sent", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int) > 0
        logical = sent[sent_bool]
        mav = sent[mav_bool]
        ln, ldur, lrate, lmedian_dt = rate_from_series(logical["t_frame_sim_s"]) if len(logical) else (0, None, None, None)
        mn, mdur, mrate, mmedian_dt = rate_from_series(mav["t_frame_sim_s"]) if len(mav) else (0, None, None, None)
        compute = pd.to_numeric(sent.get("compute_s", pd.Series(dtype=float)), errors="coerce").dropna()
        wall = pd.to_numeric(sent.get("t_wall_s", pd.Series(dtype=float)), errors="coerce").dropna()
        sim = pd.to_numeric(sent.get("t_frame_sim_s", pd.Series(dtype=float)), errors="coerce").dropna()
        rtf = None
        if len(wall) > 1 and len(sim) > 1 and float(wall.iloc[-1] - wall.iloc[0]) > 0:
            rtf = float((sim.iloc[-1] - sim.iloc[0]) / (wall.iloc[-1] - wall.iloc[0]))
        wall_budget_s = None
        compute_fraction = None
        if rtf and out.get("camera_median_dt_ms"):
            wall_budget_s = (float(out["camera_median_dt_ms"]) / 1000.0) / rtf
            if wall_budget_s and len(compute):
                compute_fraction = float(compute.median() / wall_budget_s)

        out.update(
            {
                "bridge_processed_rows": n,
                "bridge_processed_duration_s": duration,
                "bridge_processed_rate_hz": rate,
                "bridge_processed_median_dt_ms": median_dt * 1000.0 if median_dt is not None else None,
                "bridge_logical_sent_rows": ln,
                "bridge_logical_sent_rate_hz": lrate,
                "bridge_logical_sent_median_dt_ms": lmedian_dt * 1000.0 if lmedian_dt is not None else None,
                "bridge_mavlink_sent_rows": mn,
                "bridge_mavlink_sent_rate_hz": mrate,
                "bridge_mavlink_sent_median_dt_ms": mmedian_dt * 1000.0 if mmedian_dt is not None else None,
                "bridge_compute_median_ms": float(compute.median() * 1000.0) if len(compute) else None,
                "bridge_compute_p90_ms": float(compute.quantile(0.90) * 1000.0) if len(compute) else None,
                "real_time_factor_est": rtf,
                "wall_budget_per_camera_frame_ms": wall_budget_s * 1000.0 if wall_budget_s else None,
                "compute_fraction_of_camera_wall_period": compute_fraction,
                "quality_mean": float(pd.to_numeric(sent.get("quality_sent", pd.Series(dtype=float)), errors="coerce").mean()) if "quality_sent" in sent else None,
                "quality_zero_fraction": float((pd.to_numeric(sent.get("quality_sent", pd.Series(dtype=float)), errors="coerce").fillna(0) == 0).mean()) if "quality_sent" in sent else None,
                "matches_median": float(pd.to_numeric(sent.get("n_matches", pd.Series(dtype=float)), errors="coerce").median()) if "n_matches" in sent else None,
            }
        )

    cam_dt_ms = out.get("camera_median_dt_ms")
    min_period_ms = out.get("configured_min_period_ms")
    if cam_dt_ms and min_period_ms:
        multiplier = max(1, math.ceil(min_period_ms / cam_dt_ms - 1e-9))
        alias_dt_ms = multiplier * cam_dt_ms
        out["rate_cap_alias_multiplier"] = multiplier
        out["rate_cap_alias_expected_dt_ms"] = alias_dt_ms
        out["rate_cap_alias_expected_hz"] = 1000.0 / alias_dt_ms

    if range_path.exists():
        rng = pd.read_csv(range_path)
        r = pd.to_numeric(rng["range_m"], errors="coerce")
        finite = r.notna() & (r != float("inf")) & (r != float("-inf"))
        ok = finite & (r >= 0.8) & (r <= 60.0)
        out.update(
            {
                "range_rows": int(len(rng)),
                "range_finite_fraction": float(finite.mean()) if len(rng) else None,
                "range_gate_ok_fraction": float(ok.mean()) if len(rng) else None,
                "first_range_inf_t_sim_s": float(rng.loc[~finite, "t_sim_s"].iloc[0]) if (~finite).any() else None,
                "first_range_gate_ok_t_sim_s": float(rng.loc[ok, "t_sim_s"].iloc[0]) if ok.any() else None,
                "last_range_gate_ok_t_sim_s": float(rng.loc[ok, "t_sim_s"].iloc[-1]) if ok.any() else None,
            }
        )

    return out


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col, "")
            if value is None:
                values.append("")
            elif isinstance(value, float):
                values.append(f"{value:.4g}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    df = pd.DataFrame(rows)
    out = []
    for system, group in df.groupby("system", dropna=False):
        def mean_col(col: str) -> float | None:
            values = pd.to_numeric(group.get(col), errors="coerce").dropna()
            return float(values.mean()) if len(values) else None

        def max_col(col: str) -> float | None:
            values = pd.to_numeric(group.get(col), errors="coerce").dropna()
            return float(values.max()) if len(values) else None

        out.append(
            {
                "system": system,
                "runs": int(len(group)),
                "bounded_runs": int((group.get("divergence_verdict") == "bounded").sum()),
                "diverged_runs": int((group.get("divergence_verdict") == "diverged").sum()),
                "mean_horizontal_max_m": mean_col("horizontal_max_m"),
                "worst_horizontal_max_m": max_col("horizontal_max_m"),
                "mean_height_abs_max_m": mean_col("height_abs_max_m"),
                "mean_sensor_flow_rate_hz": mean_col("sensor_optical_flow_rate_hz"),
                "mean_flow_reject_over_fused": mean_col("flow_rejected_over_fused"),
                "mean_xy_reset_delta": mean_col("xy_reset_counter_delta"),
            }
        )
    return out


def sanitize_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)


def make_plots(run_rows: list[dict[str, Any]], bridge_rows: list[dict[str, Any]], out_dir: Path) -> list[str]:
    if not run_rows:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    df = pd.DataFrame(run_rows)
    colors = {
        "PX4 stock gz_x500_flow": "#2f6f9f",
        "DATABOSS SIFT bridge": "#b35c1e",
        "DATABOSS LK bridge": "#2f8f5b",
    }

    def add_bar_plot(metric: str, ylabel: str, filename: str, logy: bool = False) -> None:
        values = pd.to_numeric(df.get(metric), errors="coerce")
        fig, ax = plt.subplots(figsize=(max(8, len(df) * 0.85), 4.8))
        ax.bar(
            df["case"],
            values,
            color=[colors.get(str(s), "#666666") for s in df["system"]],
        )
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Run")
        ax.tick_params(axis="x", labelrotation=45)
        if logy:
            ax.set_yscale("log")
            ax.set_ylim(bottom=max(0.01, float(values[values > 0].min()) * 0.5) if (values > 0).any() else 0.01)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        path = plots_dir / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        made.append(f"plots/{filename}")

    add_bar_plot("horizontal_max_m", "Max horizontal truth error (m)", "horizontal_max_by_run.png", logy=True)
    add_bar_plot("height_abs_max_m", "Max height truth error (m)", "height_max_by_run.png", logy=True)
    add_bar_plot("sensor_optical_flow_rate_hz", "ULog sensor_optical_flow rate (Hz)", "flow_rate_by_run.png")
    add_bar_plot("flow_rejected_over_fused", "Optical-flow rejected / fused", "reject_fused_by_run.png")

    if bridge_rows:
        bdf = pd.DataFrame(bridge_rows)
        bdf["label"] = bdf["system"].astype(str).str.replace("DATABOSS ", "", regex=False).str.replace(" bridge", "", regex=False)
        bdf["label"] = bdf["label"] + " " + bdf.groupby("system").cumcount().add(1).astype(str)
        fig, ax1 = plt.subplots(figsize=(max(8, len(bdf) * 0.9), 4.8))
        x = range(len(bdf))
        ax1.bar(
            [v - 0.18 for v in x],
            pd.to_numeric(bdf.get("bridge_logical_sent_rate_hz"), errors="coerce"),
            width=0.36,
            color="#3f76a6",
            label="Logical send Hz",
        )
        ax1.set_ylabel("Logical send rate (Hz)")
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(bdf["label"], rotation=45, ha="right")
        ax1.grid(axis="y", alpha=0.25)
        ax2 = ax1.twinx()
        ax2.bar(
            [v + 0.18 for v in x],
            pd.to_numeric(bdf.get("bridge_compute_median_ms"), errors="coerce"),
            width=0.36,
            color="#9a6b33",
            label="Median compute ms",
        )
        ax2.set_ylabel("Median compute (ms wall)")
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
        fig.tight_layout()
        path = plots_dir / "bridge_rate_compute.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        made.append("plots/bridge_rate_compute.png")

    for row in run_rows:
        run_dir = Path(str(row.get("run_dir", "")))
        aligned = run_dir / "ekf_vs_ground_truth_aligned.csv"
        if not aligned.exists():
            continue
        try:
            adf = pd.read_csv(aligned, usecols=["px4_x_rel", "px4_y_rel", "gz_x_rel", "gz_y_rel"])
        except Exception:
            continue
        if adf.empty:
            continue
        fig, ax = plt.subplots(figsize=(5.8, 5.2))
        step = max(1, len(adf) // 2000)
        thin = adf.iloc[::step]
        ax.plot(thin["gz_y_rel"], thin["gz_x_rel"], color="#111111", linewidth=2.0, label="Gazebo truth")
        ax.plot(thin["px4_y_rel"], thin["px4_x_rel"], color=colors.get(str(row.get("system")), "#666666"), linewidth=1.6, label="PX4 EKF")
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("East / PX4 y (m)")
        ax.set_ylabel("North / PX4 x (m)")
        ax.set_title(str(row.get("case")))
        ax.grid(alpha=0.25)
        ax.legend(loc="best")
        fig.tight_layout()
        filename = f"trajectory_{sanitize_name(str(row.get('case')))}.png"
        path = plots_dir / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        made.append(f"plots/{filename}")

    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch-dir", action="append", required=True)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--repair-missing", action="store_true")
    ap.add_argument("--force-repair", action="store_true")
    args = ap.parse_args()

    batch_dirs = [resolve_path(v) for v in args.batch_dir]
    out_dir = resolve_path(args.out_dir) or DEFAULT_OUT
    for batch_dir in batch_dirs:
        if batch_dir is None or not batch_dir.exists():
            print(f"ERROR: batch dir not found: {batch_dir}", file=sys.stderr)
            return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = []
    for batch_dir in batch_dirs:
        if batch_dir is not None:
            cases.extend(read_batch_rows(batch_dir))
    repair_notes = []
    if args.repair_missing or args.force_repair:
        for case in cases:
            run_dir = case.get("run_dir")
            if run_dir:
                repair_notes.append(repair_run(run_dir, force=args.force_repair))

    run_rows = [summarize_run(case) for case in cases]
    bridge_rate_rows = [
        summarize_bridge_rate(case["run_dir"], system=case["system"])
        for case in cases
        if case["system"].startswith("DATABOSS") and case.get("run_dir")
    ]
    agg_rows = aggregate(run_rows)
    plot_paths = make_plots(run_rows, bridge_rate_rows, out_dir)

    pd.DataFrame(run_rows).to_csv(out_dir / "replicate_metrics.csv", index=False)
    pd.DataFrame(bridge_rate_rows).to_csv(out_dir / "bridge_rate_diagnostics.csv", index=False)
    # Backward-compatible filename for earlier Phase 8J references.
    pd.DataFrame(bridge_rate_rows).to_csv(out_dir / "sift_rate_diagnostics.csv", index=False)
    (out_dir / "replicate_metrics.json").write_text(json.dumps(run_rows, indent=2) + "\n")
    (out_dir / "bridge_rate_diagnostics.json").write_text(json.dumps(bridge_rate_rows, indent=2) + "\n")
    (out_dir / "sift_rate_diagnostics.json").write_text(json.dumps(bridge_rate_rows, indent=2) + "\n")
    (out_dir / "repair_notes.json").write_text(json.dumps(repair_notes, indent=2) + "\n")

    report = [
        "# Phase 8J - PX4 Stock Flow vs DATABOSS SIFT/LK, 50 s Comparison",
        "",
        "Batches:",
        "",
        *[f"- `{batch_dir}`" for batch_dir in batch_dirs],
        "",
        "World/profile: batch-configured flat phototextured world, 2.5 m AGL, slow +Y command, GNSS starts enabled, then `SIM_GPS_USED=0`, 50 s post-loss observation, no landing wait (`skip_landing_command: true`).",
        "",
        "## Plots",
        "",
        *[f"- ![]({path})" for path in plot_paths if not path.startswith("plots/trajectory_")],
        "",
        "Trajectory plots are saved under `plots/trajectory_*.png` next to this report.",
        "",
        "## Replicate Verdicts",
        "",
        md_table(
            run_rows,
            [
                "case",
                "system",
                "divergence_verdict",
                "gnss_loss_detected",
                "distance_sensor_ok",
                "horizontal_max_m",
                "horizontal_end_m",
                "height_abs_max_m",
                "sensor_optical_flow_rate_hz",
                "cs_opt_flow_active_fraction",
                "flow_fused_count",
                "flow_rejected_count",
                "flow_rejected_over_fused",
                "xy_reset_counter_delta",
            ],
        ),
        "",
        "## System Summary",
        "",
        md_table(
            agg_rows,
            [
                "system",
                "runs",
                "bounded_runs",
                "diverged_runs",
                "mean_horizontal_max_m",
                "worst_horizontal_max_m",
                "mean_height_abs_max_m",
                "mean_sensor_flow_rate_hz",
                "mean_flow_reject_over_fused",
                "mean_xy_reset_delta",
            ],
        ),
        "",
        "## Current Read",
        "",
        "Use the verdict table as the authority for this batch. A run is only treated as bounded when Gazebo-truth horizontal/height error, range health, optical-flow fusion, and reset counters stay under control through the 50 s outage.",
        "",
        "PX4 stock flow is the simulation reference path. It is useful for judging whether the world/profile is flyable with PX4's own Gazebo optical-flow stack, but it is not proof of a physical sensor.",
        "",
        "DATABOSS SIFT and LK are bridge paths. Their bridge-rate diagnostics show camera cadence, sent cadence, compute cost, range gating, quality, and match/track behavior, so slow-Hz and estimator-quality failures are kept separate.",
        "",
        "## Bridge Rate Diagnostics",
        "",
        md_table(
            bridge_rate_rows,
            [
                "system",
                "estimator",
                "camera_frame_rows",
                "camera_rate_hz",
                "camera_median_dt_ms",
                "configured_rate_hz",
                "configured_min_period_ms",
                "rate_cap_alias_expected_hz",
                "bridge_processed_rate_hz",
                "bridge_processed_median_dt_ms",
                "bridge_logical_sent_rate_hz",
                "bridge_logical_sent_median_dt_ms",
                "bridge_compute_median_ms",
                "bridge_compute_p90_ms",
                "real_time_factor_est",
                "compute_fraction_of_camera_wall_period",
                "range_gate_ok_fraction",
                "first_range_inf_t_sim_s",
            ],
        ),
        "",
        "## What The LK Rows Mean",
        "",
        "LK is the intended first bridge upgrade: active sparse tracks, pyramidal Lucas-Kanade flow, and consistency rejection. In this report it is flown through the same MAVLink bridge family, range gates, EKF params, route, and world as SIFT; the exact bridge cap is recorded per run in `bridge_rate_diagnostics.csv`.",
        "",
        "Read the LK row directly before accepting it as a live fix. Lower compute alone is not enough; LK must also maintain quality/tracks, send valid range-coupled samples, and show EKF optical-flow fusion during the outage. If it fails, the next debug pass is message fields, quality scaling, integrated-flow signs/magnitudes, integration time, distance coupling, and EKF innovation gates against SIFT and PX4 stock reference rows.",
        "",
        "## Why The Current SIFT Bridge Performs Poorly",
        "",
        "1. PX4 stock flow is a tightly integrated sim reference: 100x100 downward camera, KLT/OpenCV flow around 50 Hz, a synchronized downward rangefinder, and the stock PX4 optical-flow path. In this benchmark it should be treated as the reference system, not as a real sensor proof.",
        "2. The current DATABOSS bridge uses SIFT descriptor matching plus a median displacement. That is robust for offline image matching, but it is not a great live optical-flow frontend under closed-loop drift: when the vehicle tilts hard or translates faster than expected, match consistency drops, quality drops, and the EKF starts rejecting flow. The controller then drifts farther, which makes the next frames harder, so rejection and drift reinforce each other.",
        "3. Range is not simply 'missing from the start'. In the known divergent run, the first `inf` appeared while the vehicle center was still inside the 240x240 m map, because the vehicle was already tilted around 87 deg and the downward ray was effectively looking sideways. After the vehicle left the +/-120 m world extent, range became sustained `inf`, the bridge gates stopped logical sends, and optical-flow aiding collapsed.",
        "4. The EKF evidence is the key failure signature: stock flow should show high-rate flow with little or no rejection, while current SIFT shows much lower effective rate, many rejected aid-source samples, optical-flow control-status dropouts, XY resets, and large truth error.",
        "",
        "## Why The Bridge Hz Is Slow",
        "",
        "There are two separate low-Hz effects.",
        "",
        "First, the EKF-relevant bridge sample rate is limited by rate-cap aliasing, not by estimator compute. The camera source arrives at about 30.3 Hz, or 33 ms between frames. The current bridge cap is 20 Hz, so `min_period = 50 ms`. A 33 ms source cannot produce exactly 20 Hz through that gate: every other frame passes, so the accepted spacing becomes about 66 ms, or 15.15 Hz. Setting the cap to 20 or 30 still aliases; the cap must be higher than the source rate, for example `rate_hz: 40`, or disabled, to deliver the full camera rate.",
        "",
        "Second, wall-clock runtime is slow because the scenario deliberately slows simulation (`sim_time_wall_multiplier: 30`) and render/logging overhead is heavy. SIFT adds expensive descriptor work; LK is cheaper, but it does not remove the global Gazebo/render/recording slowdown by itself.",
        "",
        "The camera SDF can request 50 Hz, but this render path is only delivering about 30 Hz frames in practice. The bridge cannot exceed the frame source rate.",
        "",
        "## Practical Next Fix",
        "",
        "Use this report as the current stock/SIFT/LK baseline. The next one-variable run should first debug LK fusion evidence, because the live LK rows did not activate EKF optical-flow fusion. After LK fusion is active, change only `flow_bridge.rate_hz` from 20 to 40 or 0 and verify that `flow_bridge_sent.csv` moves from the 66 ms alias to the full 33 ms camera cadence.",
        "",
        "## Artifacts",
        "",
        f"- Metrics CSV: `{out_dir / 'replicate_metrics.csv'}`",
        f"- Bridge rate CSV: `{out_dir / 'bridge_rate_diagnostics.csv'}`",
        f"- Plots: `{out_dir / 'plots'}`",
        f"- Repair notes: `{out_dir / 'repair_notes.json'}`",
    ]
    (out_dir / "report.md").write_text("\n".join(report) + "\n")
    print(f"wrote {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
