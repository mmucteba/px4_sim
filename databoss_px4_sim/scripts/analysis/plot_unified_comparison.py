#!/usr/bin/env python3
"""Generate unified, manifest-driven LK/SIFT/stock x GNSS-on/off comparison plots.

Adapted from plot_phase11_three_way_flow_comparison.py: cases come from a
manifest YAML (via comparison_manifest.load_manifest) instead of a
hardcoded CASES list, and color/linestyle are derived from each case's
algorithm + gnss_state tags so an arbitrary-length, arbitrary-mix case set
renders consistently without code changes.
"""

from __future__ import annotations

import argparse
import json
import math
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
import yaml
from pyulog import ULog

from comparison_manifest import Case as ManifestCase
from comparison_manifest import load_manifest, open_ulog

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# One base color per algorithm; GNSS-on renders solid, GNSS-loss renders
# with a lighter alpha and dashed overlay lines where applicable, so a
# mixed on/off matrix stays readable without new colors per combination.
ALGORITHM_COLOR = {"lk": "#1f77b4", "sift": "#2ca02c", "stock": "#d62728"}


@dataclass(frozen=True)
class PlotCase:
    key: str
    label: str
    short_label: str
    run_dir: Path
    color: str
    linestyle: str
    kind: str
    gnss_state: str


def to_plot_cases(cases: list[ManifestCase]) -> list[PlotCase]:
    out = []
    for c in cases:
        color = ALGORITHM_COLOR.get(c.kind, "#7f7f7f")
        linestyle = "-" if c.gnss_state == "loss" else ":"
        short = c.short if c.gnss_state == "loss" else f"{c.short}"
        out.append(
            PlotCase(
                key=c.key,
                label=c.label,
                short_label=short,
                run_dir=c.run_dir,
                color=color,
                linestyle=linestyle,
                kind=c.kind,
                gnss_state=c.gnss_state,
            )
        )
    return out


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def arr(data: dict[str, Any], key: str) -> np.ndarray | None:
    if key not in data:
        return None
    return np.asarray(data[key], dtype=np.float64)


def dataset(ulog: ULog, name: str):
    matches = [d for d in ulog.data_list if d.name == name]
    return matches[0] if matches else None


def time_rel_s(data: dict[str, Any], start_us: int) -> np.ndarray:
    return (np.asarray(data["timestamp"], dtype=np.float64) - float(start_us)) / 1e6


def nested(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def route_metrics(df: pd.DataFrame) -> dict[str, float]:
    x = num(df["gz_x_rel"]).to_numpy()
    y = num(df["gz_y_rel"]).to_numpy()
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if len(x) < 2:
        return {"truth_path_m": math.nan, "truth_end_m": math.nan, "truth_straightness": math.nan}
    steps = np.hypot(np.diff(x), np.diff(y))
    path = float(np.sum(steps))
    end = float(math.hypot(x[-1] - x[0], y[-1] - y[0]))
    straightness = end / path if path > 0 else math.nan
    return {"truth_path_m": path, "truth_end_m": end, "truth_straightness": straightness}


def first_true_time(t: np.ndarray, mask: np.ndarray) -> float | None:
    idx = np.flatnonzero(mask)
    return float(t[idx[0]]) if len(idx) else None


def observed_gps_loss_time(t: np.ndarray, fix_type: np.ndarray | None, sats: np.ndarray | None) -> float | None:
    if fix_type is None or sats is None:
        return None
    good = (fix_type >= 3) & (sats > 0)
    transitions = np.flatnonzero(good[:-1] & ~good[1:])
    return float(t[transitions[0] + 1]) if len(transitions) else None


def load_case(case: PlotCase, out_dir: Path) -> dict[str, Any]:
    aligned_path = case.run_dir / "ekf_vs_ground_truth_aligned.csv"
    metrics_path = case.run_dir / "ekf_vs_ground_truth_metrics.json"
    status_path = case.run_dir / "logs/pxh_takeoff_land_truth_status.json"
    config_path = case.run_dir / "config.yaml"
    ulog_path = case.run_dir / "logs/flight.ulg"
    ulog_gz_path = case.run_dir / "logs/flight.ulg.gz"

    missing = [p for p in [aligned_path, metrics_path, status_path, config_path] if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{case.key} missing required files: {', '.join(str(p) for p in missing)}")
    if not ulog_path.exists() and not ulog_gz_path.exists():
        raise FileNotFoundError(f"{case.key} missing required files: {ulog_path} (or .gz)")

    aligned = pd.read_csv(aligned_path)
    metrics = load_json(metrics_path)
    status = load_json(status_path)
    config = load_yaml(config_path)
    ulog = open_ulog(case.run_dir)

    route = route_metrics(aligned)
    summary: dict[str, Any] = {
        "key": case.key,
        "label": case.label,
        "short_label": case.short_label,
        "run_dir": str(case.run_dir),
        "kind": case.kind,
        "gnss_state": case.gnss_state,
        "vehicle_model": nested(config, "vehicle", "model"),
        "world": nested(config, "world", "name"),
        "route": nested(config, "route", "name"),
        "validation_accepted": status.get("accepted"),
        "metrics_accepted": metrics.get("accepted"),
        "comparison_window_ok": metrics.get("comparison_window_ok"),
        "comparison_end_reason": metrics.get("comparison_end_reason"),
        "flow_bridge_enabled": nested(config, "flow_bridge", "enabled"),
        "flow_bridge_estimator": nested(config, "flow_bridge", "estimator"),
        "flow_bridge_rate_hz": nested(config, "flow_bridge", "rate_hz"),
        "stock_flow_enabled": nested(config, "stock_flow", "enabled"),
        "axis_map": nested(config, "flow_bridge", "axis_map"),
        "ekf2_of_n_min": nested(config, "flow_bridge", "ekf2_of_n_min"),
        "ekf2_of_delay": nested(config, "flow_bridge", "ekf2_of_delay") or nested(config, "stock_flow", "ekf2_of_delay"),
        "status_flow_bridge_sent_rows": status.get("flow_bridge_sent_rows"),
        "status_flow_bridge_ok": status.get("flow_bridge_ok"),
        "status_ulog_max_height_up_m": status.get("ulog_max_height_up_m"),
        "status_distance_sensor_height_diff_m": status.get("ulog_distance_sensor_height_diff_m"),
        "gnss_loss_requested": status.get("gnss_loss_requested"),
        "effective_gnss_loss_after_takeoff_s": status.get("effective_gnss_loss_after_takeoff_s"),
        "post_loss_hover_s": status.get("post_loss_hover_s"),
        "horizontal_error_max_m": nested(metrics, "horizontal_error", "max_m"),
        "horizontal_error_mean_m": nested(metrics, "horizontal_error", "mean_m"),
        "horizontal_error_p95_m": nested(metrics, "horizontal_error", "p95_m"),
        "height_error_max_m": nested(metrics, "height_abs_error", "max_m"),
        "height_error_mean_m": nested(metrics, "height_abs_error", "mean_m"),
        "height_error_p95_m": nested(metrics, "height_abs_error", "p95_m"),
        "gazebo_station_end_m": nested(metrics, "station_keeping", "gazebo_horizontal_displacement_from_start", "end_m"),
        **route,
    }

    start_us = int(ulog.start_timestamp)
    summary["ulog_start_timestamp_us"] = start_us

    local_pos = dataset(ulog, "vehicle_local_position")
    takeoff_t = None
    if local_pos is not None and "z" in local_pos.data:
        t = time_rel_s(local_pos.data, start_us)
        takeoff_t = first_true_time(t, -np.asarray(local_pos.data["z"], dtype=np.float64) > 0.5)
    summary["observed_takeoff_threshold_rel_s"] = takeoff_t

    scheduled = None
    if takeoff_t is not None and status.get("effective_gnss_loss_after_takeoff_s") is not None:
        scheduled = takeoff_t + float(status["effective_gnss_loss_after_takeoff_s"])
    summary["scheduled_gnss_loss_rel_s"] = scheduled

    gps = dataset(ulog, "vehicle_gps_position") or dataset(ulog, "sensor_gps")
    if gps is not None:
        t = time_rel_s(gps.data, start_us)
        fix = arr(gps.data, "fix_type")
        sats = arr(gps.data, "satellites_used")
        eph = arr(gps.data, "eph")
        epv = arr(gps.data, "epv")
        loss_t = observed_gps_loss_time(t, fix, sats)
        summary.update(
            {
                "gps_rows": int(len(t)),
                "observed_gps_loss_rel_s": loss_t,
                "observed_gps_loss_after_takeoff_s": loss_t - takeoff_t if loss_t is not None and takeoff_t is not None else None,
                "gps_fix_type_unique": sorted({int(x) for x in fix}) if fix is not None else [],
                "gps_eph_max_m": float(np.nanmax(eph)) if eph is not None else None,
                "gps_epv_max_m": float(np.nanmax(epv)) if epv is not None else None,
            }
        )

    aid = dataset(ulog, "estimator_aid_src_optical_flow")
    if aid is not None:
        aid_df = pd.DataFrame({k: aid.data[k] for k in aid.data.keys()})
        aid_df["t_rel_s"] = time_rel_s(aid.data, start_us)
        aid_df["fused"] = num(aid_df["fused"]).fillna(0).astype(int)
        aid_df["innovation_rejected"] = num(aid_df["innovation_rejected"]).fillna(0).astype(int)
        aid_df["test_ratio_max"] = pd.concat([num(aid_df["test_ratio[0]"]), num(aid_df["test_ratio[1]"])], axis=1).max(axis=1)
        aid_bin = (
            aid_df.assign(bin_s=np.floor(aid_df["t_rel_s"]).astype(int))
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
        aid_bin["fusion_fraction"] = aid_bin["fused"] / aid_bin["samples"]
        aid_bin["rejected_fraction"] = aid_bin["rejected"] / aid_bin["samples"]
        aid_bin["sample_rate_hz"] = aid_bin["samples"]
        fused = int(aid_df["fused"].sum())
        rejected = int(aid_df["innovation_rejected"].sum())
        summary.update(
            {
                "of_aid_rows": int(len(aid_df)),
                "of_fused_count": fused,
                "of_rejected_count": rejected,
                "of_fused_fraction": fused / len(aid_df) if len(aid_df) else math.nan,
                "of_test_ratio_p95": float(aid_df["test_ratio_max"].quantile(0.95)) if len(aid_df) else math.nan,
                "of_test_ratio_max": float(aid_df["test_ratio_max"].max()) if len(aid_df) else math.nan,
            }
        )
    else:
        aid_df = pd.DataFrame()
        aid_bin = pd.DataFrame()

    sensor_flow = dataset(ulog, "sensor_optical_flow")
    if sensor_flow is not None:
        sf_df = pd.DataFrame({k: sensor_flow.data[k] for k in sensor_flow.data.keys()})
        sf_df["t_rel_s"] = time_rel_s(sensor_flow.data, start_us)
        sf_df["quality"] = num(sf_df["quality"])
        duration = float(sf_df["t_rel_s"].iloc[-1] - sf_df["t_rel_s"].iloc[0]) if len(sf_df) > 1 else math.nan
        summary.update(
            {
                "sensor_optical_flow_rows": int(len(sf_df)),
                "sensor_optical_flow_rate_hz": len(sf_df) / duration if duration and duration > 0 else math.nan,
                "flow_quality_mean": float(sf_df["quality"].mean()),
                "flow_quality_zero_fraction": float((sf_df["quality"] == 0).mean()),
            }
        )
    else:
        sf_df = pd.DataFrame()

    flags = dataset(ulog, "estimator_status_flags")
    if flags is not None:
        flag_df = pd.DataFrame({k: flags.data[k] for k in flags.data.keys()})
        flag_df["t_rel_s"] = time_rel_s(flags.data, start_us)
        for key in ["cs_opt_flow", "cs_gnss_pos", "cs_gnss_vel", "cs_rng_hgt", "cs_gps_hgt", "cs_inertial_dead_reckoning"]:
            if key in flag_df:
                summary[f"{key}_fraction"] = float(num(flag_df[key]).mean())
    else:
        flag_df = pd.DataFrame()

    distance = dataset(ulog, "distance_sensor")
    if distance is not None:
        dist_df = pd.DataFrame({k: distance.data[k] for k in distance.data.keys()})
        dist_df["t_rel_s"] = time_rel_s(distance.data, start_us)
        if "current_distance" in dist_df:
            summary["distance_sensor_current_max_m"] = float(num(dist_df["current_distance"]).max())
    else:
        dist_df = pd.DataFrame()

    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(data_dir / f"{case.key}_aligned.csv", index=False)
    if not aid_df.empty:
        aid_bin.to_csv(data_dir / f"{case.key}_optical_flow_fusion_1s_bins.csv", index=False)

    return {
        "case": case,
        "aligned": aligned,
        "metrics": metrics,
        "status": status,
        "config": config,
        "ulog": ulog,
        "summary": summary,
        "aid_bin": aid_bin,
        "sensor_flow": sf_df,
        "flags": flag_df,
        "distance": dist_df,
    }


def add_event_lines(ax: plt.Axes, summary: dict[str, Any]) -> None:
    events = [
        ("takeoff", summary.get("observed_takeoff_threshold_rel_s"), "#555555", "-"),
        ("GPS loss observed", summary.get("observed_gps_loss_rel_s"), "#d62728", "-"),
        ("GPS loss scheduled", summary.get("scheduled_gnss_loss_rel_s"), "#b95f02", "--"),
    ]
    for _label, value, color, linestyle in events:
        if isinstance(value, (int, float)) and math.isfinite(value):
            ax.axvline(value, color=color, linestyle=linestyle, linewidth=0.9, alpha=0.65)


def plot_routes(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(9.5, 7.6))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["gz_x_rel"]), num(df["gz_y_rel"]), color=case.color, linestyle=case.linestyle, linewidth=2.0, label=f"{case.short_label} Gazebo truth")
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.0, alpha=0.45, label="10 m reference")
    plt.xlabel("relative X / North (m)")
    plt.ylabel("relative Y / East (m)")
    plt.title("Unified comparison route: Gazebo truth (solid=GNSS-loss, dotted=GNSS-on)")
    plt.axis("equal")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "route_ekf_vs_gazebo_truth_overlay.png")


def plot_truth_errors(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(11.5, 5.6))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["px4_t_rel_s"]), num(df["horizontal_error_m"]), color=case.color, linestyle=case.linestyle, linewidth=1.8, label=case.short_label)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("horizontal EKF-vs-Gazebo error (m)")
    plt.title("Horizontal error against Gazebo truth (solid=GNSS-loss, dotted=GNSS-on)")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "horizontal_error_vs_gazebo_truth.png")


def plot_gnss(data: list[dict[str, Any]], plots_dir: Path) -> None:
    fig, axes = plt.subplots(len(data), 1, figsize=(13.0, 2.4 * len(data)), sharex=False)
    if len(data) == 1:
        axes = [axes]
    for ax, item in zip(axes, data):
        case = item["case"]
        gps = dataset(item["ulog"], "vehicle_gps_position") or dataset(item["ulog"], "sensor_gps")
        if gps is None:
            continue
        t = time_rel_s(gps.data, int(item["ulog"].start_timestamp))
        fix = arr(gps.data, "fix_type")
        sats = arr(gps.data, "satellites_used")
        ax.step(t, fix, where="post", color=case.color, linewidth=1.4, label="fix_type")
        ax.set_ylim(-0.2, 4.2)
        ax.set_ylabel(f"{case.short_label}\nfix_type")
        ax2 = ax.twinx()
        ax2.step(t, sats, where="post", color="#444444", linewidth=1.0, alpha=0.75, label="satellites")
        ax2.set_ylabel("sats")
        add_event_lines(ax, item["summary"])
        ax.grid(True, alpha=0.25)
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, loc="upper right", fontsize=8)
    axes[-1].set_xlabel("PX4 log time since ULog start (s)")
    fig.suptitle("GNSS validity: fix_type and satellites_used")
    savefig(plots_dir / "gnss_fix_satellites_over_time.png")


def plot_flow(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(11.5, 5.6))
    for item in data:
        case = item["case"]
        df = item["aid_bin"]
        if df.empty:
            continue
        plt.plot(df["bin_s"], df["fusion_fraction"], color=case.color, linestyle=case.linestyle, linewidth=1.8, label=case.short_label)
    plt.ylim(-0.02, 1.05)
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("1 s fused fraction")
    plt.title("Optical-flow EKF aid fusion fraction (solid=GNSS-loss, dotted=GNSS-on)")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_fusion_fraction_1s.png")


def plot_flags_and_range(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(11.5, 5.6))
    for item in data:
        case = item["case"]
        df = item["distance"]
        if df.empty or "current_distance" not in df:
            continue
        plt.plot(df["t_rel_s"], num(df["current_distance"]), color=case.color, linestyle=case.linestyle, linewidth=1.2, alpha=0.9, label=f"{case.short_label} distance_sensor")
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("distance_sensor current_distance (m)")
    plt.title("Downward rangefinder stream (solid=GNSS-loss, dotted=GNSS-on)")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "distance_sensor_current_distance.png")


def plot_summary_bars(data: list[dict[str, Any]], plots_dir: Path) -> None:
    labels = [item["case"].short_label for item in data]
    colors = [item["case"].color for item in data]
    summaries = [item["summary"] for item in data]
    metrics = [
        ("horizontal_error_max_m", "Max horizontal error (m)"),
        ("height_error_max_m", "Max height error (m)"),
        ("truth_straightness", "Truth straightness"),
        ("of_fused_fraction", "OF fused fraction"),
        ("cs_opt_flow_fraction", "cs_opt_flow fraction"),
        ("flow_quality_zero_fraction", "OF quality zero fraction"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 7.6))
    for ax, (key, title) in zip(axes.flat, metrics):
        values = [s.get(key, math.nan) for s in summaries]
        ax.bar(labels, values, color=colors, alpha=0.86)
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=30, labelsize=7)
        ax.grid(True, axis="y", alpha=0.25)
        for i, value in enumerate(values):
            if isinstance(value, (int, float)) and math.isfinite(value):
                ax.text(i, value, f"{value:.3g}", ha="center", va="bottom", fontsize=7)
    fig.suptitle("Unified comparison summary metrics")
    savefig(plots_dir / "summary_metric_bars.png")


def plot_run_dashboard(item: dict[str, Any], plots_dir: Path) -> Path:
    """One combined multi-panel figure for a single case's own data --
    the per-run detail behind the cross-case overlay plots above."""
    case = item["case"]
    df = item["aligned"]
    summary = item["summary"]
    fig, axes = plt.subplots(2, 3, figsize=(15.0, 8.6))
    fig.suptitle(f"{case.label} -- single-run dashboard ({Path(case.run_dir).name})", fontsize=11)

    ax = axes[0, 0]
    ax.plot(num(df["gz_x_rel"]), num(df["gz_y_rel"]), color="#111111", linewidth=2.0, label="Gazebo truth")
    ax.plot(num(df["px4_x_rel"]), num(df["px4_y_rel"]), color=case.color, linewidth=1.4, linestyle="--", label="PX4 EKF")
    ax.set_title("Route: truth vs EKF")
    ax.set_xlabel("relative X (m)")
    ax.set_ylabel("relative Y (m)")
    ax.axis("equal")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)

    ax = axes[0, 1]
    ax.plot(num(df["px4_t_rel_s"]), num(df["horizontal_error_m"]), color=case.color, linewidth=1.6, label="horizontal")
    ax.plot(num(df["px4_t_rel_s"]), num(df["abs_height_error_m"]), color=case.color, linewidth=1.2, linestyle=":", label="height")
    add_event_lines(ax, summary)
    ax.set_title("Error vs Gazebo truth")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("error (m)")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)

    ax = axes[0, 2]
    gps = dataset(item["ulog"], "vehicle_gps_position") or dataset(item["ulog"], "sensor_gps")
    if gps is not None:
        t = time_rel_s(gps.data, int(item["ulog"].start_timestamp))
        ax.step(t, arr(gps.data, "fix_type"), where="post", color=case.color, linewidth=1.4, label="fix_type")
        ax.set_ylim(-0.2, 4.2)
        ax2 = ax.twinx()
        ax2.step(t, arr(gps.data, "satellites_used"), where="post", color="#444444", linewidth=1.0, alpha=0.7, label="sats")
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, fontsize=7, loc="upper right")
    add_event_lines(ax, summary)
    ax.set_title("GNSS fix_type / satellites")
    ax.set_xlabel("time (s)")
    ax.grid(True, alpha=0.25)

    ax = axes[1, 0]
    aid_bin = item["aid_bin"]
    if not aid_bin.empty:
        ax.plot(aid_bin["bin_s"], aid_bin["fusion_fraction"], color=case.color, linewidth=1.6, label="fused")
        ax.plot(aid_bin["bin_s"], aid_bin["rejected_fraction"], color=case.color, linewidth=1.0, linestyle="--", label="rejected")
        ax.legend(fontsize=7)
    else:
        ax.text(0.5, 0.5, "no optical-flow aid source\n(unaided case)", ha="center", va="center", transform=ax.transAxes, fontsize=9, color="#777777")
    ax.set_ylim(-0.02, 1.05)
    add_event_lines(ax, summary)
    ax.set_title("Optical-flow aid fusion (1 s)")
    ax.set_xlabel("time (s)")
    ax.grid(True, alpha=0.25)

    ax = axes[1, 1]
    dist = item["distance"]
    if not dist.empty and "current_distance" in dist:
        ax.plot(dist["t_rel_s"], num(dist["current_distance"]), color=case.color, linewidth=1.2, alpha=0.9)
    add_event_lines(ax, summary)
    ax.set_title("Rangefinder current_distance")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("m")
    ax.grid(True, alpha=0.25)

    ax = axes[1, 2]
    ax.plot(num(df["px4_t_rel_s"]), num(df["px4_height_up"]), color=case.color, linewidth=1.4, linestyle="--", label="PX4 height")
    ax.plot(num(df["px4_t_rel_s"]), num(df["gz_height_up"]), color=case.color, linewidth=1.8, label="Gazebo height")
    add_event_lines(ax, summary)
    ax.set_title("Height: EKF vs truth")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("m")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)

    per_run_dir = plots_dir / "per_run"
    out_path = per_run_dir / f"{case.key}_dashboard.png"
    savefig(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Path to manifest.yaml")
    parser.add_argument("--out-dir", type=Path, default=None, help="Defaults to the manifest's own directory")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    out_dir = args.out_dir or args.manifest.parent
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    plot_cases = to_plot_cases(manifest.cases)
    data = [load_case(case, out_dir) for case in plot_cases]
    plot_routes(data, plots_dir)
    plot_truth_errors(data, plots_dir)
    plot_gnss(data, plots_dir)
    plot_flow(data, plots_dir)
    plot_flags_and_range(data, plots_dir)
    plot_summary_bars(data, plots_dir)
    for item in data:
        plot_run_dashboard(item, plots_dir)

    print(f"wrote plots to {plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
