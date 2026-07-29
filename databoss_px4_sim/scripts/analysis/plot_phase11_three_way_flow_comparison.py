#!/usr/bin/env python3
"""Generate Phase 11 LK/SIFT/stock GNSS-loss comparison plots."""

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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "experiments/comparisons/20260720_phase11_three_way_flow_comparison"


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    short_label: str
    run_dir: Path
    color: str


CASES = [
    Case(
        key="lk_phase10_run_d",
        label="LK fixed xy nmin0.3 Run D",
        short_label="LK fixed",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#1f77b4",
    ),
    Case(
        key="sift_phase11_111755",
        label="SIFT xy Phase 11",
        short_label="SIFT xy",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260720_111755_phase11_sift_xy_gnssloss_off50s_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#2ca02c",
    ),
    Case(
        key="stock_phase11_122327",
        label="PX4 stock flow Phase 11",
        short_label="Stock",
        run_dir=PROJECT_ROOT
        / "experiments/runs/20260720_122327_phase11_stock_gnssloss_off50s_flat_rural_phototex_noon_pxh_takeoff_land_truth",
        color="#d62728",
    ),
]


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
    end = float(np.hypot(x[-1] - x[0], y[-1] - y[0]))
    return {"truth_path_m": path, "truth_end_m": end, "truth_straightness": end / path if path else math.nan}


def first_true_time(t: np.ndarray, mask: np.ndarray) -> float | None:
    idx = np.flatnonzero(mask)
    return float(t[idx[0]]) if len(idx) else None


def observed_gps_loss_time(t: np.ndarray, fix_type: np.ndarray | None, sats: np.ndarray | None) -> float | None:
    if fix_type is None or sats is None:
        return None
    good = (fix_type >= 3) & (sats > 0)
    transitions = np.flatnonzero(good[:-1] & ~good[1:])
    return float(t[transitions[0] + 1]) if len(transitions) else None


def estimate_case_kind(config: dict[str, Any]) -> str:
    if nested(config, "stock_flow", "enabled"):
        return "stock"
    return str(nested(config, "flow_bridge", "estimator") or "unknown")


def load_case(case: Case, out_dir: Path) -> dict[str, Any]:
    aligned_path = case.run_dir / "ekf_vs_ground_truth_aligned.csv"
    metrics_path = case.run_dir / "ekf_vs_ground_truth_metrics.json"
    status_path = case.run_dir / "logs/pxh_takeoff_land_truth_status.json"
    config_path = case.run_dir / "config.yaml"
    ulog_path = case.run_dir / "logs/flight.ulg"

    missing = [p for p in [aligned_path, metrics_path, status_path, config_path, ulog_path] if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{case.key} missing required files: {', '.join(str(p) for p in missing)}")

    aligned = pd.read_csv(aligned_path)
    metrics = load_json(metrics_path)
    status = load_json(status_path)
    config = load_yaml(config_path)
    ulog = ULog(str(ulog_path))

    route = route_metrics(aligned)
    summary: dict[str, Any] = {
        "key": case.key,
        "label": case.label,
        "short_label": case.short_label,
        "run_dir": str(case.run_dir),
        "kind": estimate_case_kind(config),
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
        "ekf2_of_delay": nested(config, "flow_bridge", "ekf2_of_delay")
        or nested(config, "stock_flow", "ekf2_of_delay"),
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

    vehicle_status = dataset(ulog, "vehicle_status")
    if vehicle_status is not None and "accepts_offboard_setpoints" in vehicle_status.data:
        t = time_rel_s(vehicle_status.data, start_us)
        active = np.asarray(vehicle_status.data["accepts_offboard_setpoints"], dtype=np.float64) > 0
        summary["observed_offboard_accepted_rel_s"] = first_true_time(t, active)

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
                "gps_satellites_min": int(np.nanmin(sats)) if sats is not None else None,
                "gps_satellites_max": int(np.nanmax(sats)) if sats is not None else None,
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
        aid_df["test_ratio_max"] = pd.concat(
            [num(aid_df["test_ratio[0]"]), num(aid_df["test_ratio[1]"])],
            axis=1,
        ).max(axis=1)
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
                "of_rejected_fraction": rejected / len(aid_df) if len(aid_df) else math.nan,
                "of_sample_rate_mean_hz": float(aid_bin["sample_rate_hz"].mean()) if len(aid_bin) else math.nan,
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
                "flow_quality_min": int(sf_df["quality"].min()),
                "flow_quality_max": int(sf_df["quality"].max()),
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
            summary["distance_sensor_current_mean_m"] = float(num(dist_df["current_distance"]).mean())
    else:
        dist_df = pd.DataFrame()

    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(data_dir / f"{case.key}_aligned.csv", index=False)
    if not aid_df.empty:
        aid_df.to_csv(data_dir / f"{case.key}_estimator_aid_src_optical_flow.csv", index=False)
        aid_bin.to_csv(data_dir / f"{case.key}_optical_flow_fusion_1s_bins.csv", index=False)
    if not sf_df.empty:
        sf_df.to_csv(data_dir / f"{case.key}_sensor_optical_flow.csv", index=False)
    if not flag_df.empty:
        flag_df.to_csv(data_dir / f"{case.key}_estimator_status_flags.csv", index=False)

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
    plt.figure(figsize=(9.0, 7.4))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["gz_x_rel"]), num(df["gz_y_rel"]), color=case.color, linewidth=2.3, label=f"{case.short_label} Gazebo truth")
        plt.plot(num(df["px4_x_rel"]), num(df["px4_y_rel"]), color=case.color, linewidth=1.3, linestyle="--", label=f"{case.short_label} PX4 EKF")
        plt.scatter([num(df["gz_x_rel"]).iloc[-1]], [num(df["gz_y_rel"]).iloc[-1]], color=case.color, s=48, marker="x")
    plt.plot([0, 0], [0, 10], color="#111111", linewidth=1.0, alpha=0.45, label="10 m reference")
    plt.xlabel("relative X / North (m)")
    plt.ylabel("relative Y / East (m)")
    plt.title("Phase 11 route: Gazebo truth vs PX4 EKF")
    plt.axis("equal")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "route_ekf_vs_gazebo_truth_overlay.png")

    fig, axes = plt.subplots(1, len(data), figsize=(15.5, 5.3), sharex=False, sharey=False)
    for ax, item in zip(axes, data):
        case = item["case"]
        df = item["aligned"]
        ax.plot(num(df["gz_x_rel"]), num(df["gz_y_rel"]), color="#111111", linewidth=2.0, label="Gazebo truth")
        ax.plot(num(df["px4_x_rel"]), num(df["px4_y_rel"]), color=case.color, linewidth=1.3, linestyle="--", label="PX4 EKF")
        ax.plot([0, 0], [0, 10], color="#777777", linewidth=1.0, alpha=0.45, label="10 m ref")
        ax.set_title(case.short_label)
        ax.set_xlabel("relative X / North (m)")
        ax.set_ylabel("relative Y / East (m)")
        ax.axis("equal")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
    savefig(plots_dir / "route_ekf_vs_gazebo_truth_panels.png")


def plot_truth_errors(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["px4_t_rel_s"]), num(df["horizontal_error_m"]), color=case.color, linewidth=1.8, label=case.short_label)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("horizontal EKF-vs-Gazebo error (m)")
    plt.title("Horizontal error against Gazebo truth")
    plt.grid(True, alpha=0.25)
    plt.legend()
    savefig(plots_dir / "horizontal_error_vs_gazebo_truth.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["px4_t_rel_s"]), num(df["abs_height_error_m"]), color=case.color, linewidth=1.8, label=case.short_label)
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("absolute height error (m)")
    plt.title("Height error against Gazebo truth")
    plt.grid(True, alpha=0.25)
    plt.legend()
    savefig(plots_dir / "height_error_vs_gazebo_truth.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["px4_t_rel_s"]), num(df["px4_height_up"]), color=case.color, linewidth=1.5, linestyle="--", label=f"{case.short_label} PX4 height")
        plt.plot(num(df["px4_t_rel_s"]), num(df["gz_height_up"]), color=case.color, linewidth=1.8, label=f"{case.short_label} Gazebo height")
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("height up (m)")
    plt.title("PX4 height estimate vs Gazebo truth")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "height_px4_vs_gazebo_truth.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aligned"]
        plt.plot(num(df["px4_t_rel_s"]), num(df["gazebo_horizontal_displacement_from_start_m"]), color=case.color, linewidth=1.8, label=f"{case.short_label} truth displacement")
        plt.plot(num(df["px4_t_rel_s"]), num(df["gz_y_rel"]), color=case.color, linewidth=1.2, linestyle=":", label=f"{case.short_label} truth Y/East")
    plt.axhline(10.0, color="#111111", linewidth=1.0, alpha=0.35, label="10 m reference")
    plt.xlabel("PX4 relative time (s)")
    plt.ylabel("route progress (m)")
    plt.title("Gazebo truth route progress")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "gazebo_truth_route_progress.png")


def plot_gnss(data: list[dict[str, Any]], plots_dir: Path) -> None:
    fig, axes = plt.subplots(len(data), 1, figsize=(13.0, 9.5), sharex=False)
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

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        gps = dataset(item["ulog"], "vehicle_gps_position") or dataset(item["ulog"], "sensor_gps")
        if gps is None:
            continue
        t = time_rel_s(gps.data, int(item["ulog"].start_timestamp))
        plt.plot(t, arr(gps.data, "eph"), color=case.color, linewidth=1.8, label=f"{case.short_label} eph")
        plt.plot(t, arr(gps.data, "epv"), color=case.color, linestyle="--", linewidth=1.4, label=f"{case.short_label} epv")
    plt.yscale("symlog", linthresh=1.0)
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("GPS accuracy (m)")
    plt.title("GNSS eph/epv degradation after loss")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "gnss_eph_epv_over_time.png")


def plot_flow(data: list[dict[str, Any]], plots_dir: Path) -> None:
    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aid_bin"]
        if df.empty:
            continue
        plt.plot(df["bin_s"], df["fusion_fraction"], color=case.color, linewidth=1.8, label=f"{case.short_label} fused")
        plt.plot(df["bin_s"], df["rejected_fraction"], color=case.color, linewidth=1.1, linestyle="--", label=f"{case.short_label} rejected")
    plt.ylim(-0.02, 1.05)
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("1 s fraction")
    plt.title("Optical-flow EKF aid fusion and rejection fraction")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_fusion_fraction_1s.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aid_bin"]
        if not df.empty:
            plt.plot(df["bin_s"], df["sample_rate_hz"], color=case.color, linewidth=1.8, label=case.short_label)
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("aid-source samples per second")
    plt.title("Optical-flow aid-source sample rate")
    plt.grid(True, alpha=0.25)
    plt.legend()
    savefig(plots_dir / "optical_flow_aid_sample_rate_1s.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["aid_bin"]
        if not df.empty:
            plt.plot(df["bin_s"], df["test_ratio_p95"], color=case.color, linewidth=1.8, label=f"{case.short_label} p95")
            plt.plot(df["bin_s"], df["test_ratio_max"], color=case.color, linewidth=1.0, linestyle=":", label=f"{case.short_label} max")
    plt.axhline(1.0, color="#111111", alpha=0.4, linewidth=1.0, label="reject threshold approx")
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("max axis optical-flow innovation test ratio")
    plt.title("Optical-flow innovation test ratio")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    savefig(plots_dir / "optical_flow_test_ratio_1s.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["sensor_flow"]
        if df.empty:
            continue
        plt.plot(df["t_rel_s"], df["quality"], color=case.color, linewidth=1.0, alpha=0.85, label=f"{case.short_label} quality")
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("sensor_optical_flow quality")
    plt.title("Optical-flow quality reaching PX4")
    plt.grid(True, alpha=0.25)
    plt.legend()
    savefig(plots_dir / "optical_flow_quality_over_time.png")


def plot_flags_and_range(data: list[dict[str, Any]], plots_dir: Path) -> None:
    fig, axes = plt.subplots(len(data), 1, figsize=(13.0, 9.5), sharex=False)
    for ax, item in zip(axes, data):
        case = item["case"]
        df = item["flags"]
        if df.empty:
            continue
        for key, color in [
            ("cs_gnss_pos", "#1f77b4"),
            ("cs_gnss_vel", "#17becf"),
            ("cs_opt_flow", "#ff7f0e"),
            ("cs_rng_hgt", "#2ca02c"),
            ("cs_gps_hgt", "#9467bd"),
            ("cs_inertial_dead_reckoning", "#d62728"),
        ]:
            if key in df:
                ax.step(df["t_rel_s"], num(df[key]), where="post", linewidth=1.3, label=key, color=color)
        add_event_lines(ax, item["summary"])
        ax.set_ylim(-0.15, 1.25)
        ax.set_ylabel(case.short_label)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8, ncol=3, loc="upper right")
    axes[-1].set_xlabel("PX4 log time since ULog start (s)")
    fig.suptitle("EKF control-status flags")
    savefig(plots_dir / "ekf_control_status_flags.png")

    plt.figure(figsize=(11.2, 5.4))
    for item in data:
        case = item["case"]
        df = item["distance"]
        if df.empty or "current_distance" not in df:
            continue
        plt.plot(df["t_rel_s"], num(df["current_distance"]), color=case.color, linewidth=1.2, alpha=0.9, label=f"{case.short_label} distance_sensor")
    plt.xlabel("PX4 log time since ULog start (s)")
    plt.ylabel("distance_sensor current_distance (m)")
    plt.title("Downward rangefinder stream")
    plt.grid(True, alpha=0.25)
    plt.legend()
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
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.4))
    for ax, (key, title) in zip(axes.flat, metrics):
        values = [s.get(key, math.nan) for s in summaries]
        ax.bar(labels, values, color=colors, alpha=0.86)
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.25)
        for i, value in enumerate(values):
            if isinstance(value, (int, float)) and math.isfinite(value):
                ax.text(i, value, f"{value:.3g}", ha="center", va="bottom", fontsize=8)
    fig.suptitle("Phase 11 summary metrics")
    savefig(plots_dir / "summary_metric_bars.png")


def write_report(data: list[dict[str, Any]], out_dir: Path) -> None:
    rows = [item["summary"] for item in data]
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "summary.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def fmt(value: Any, digits: int = 3) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            if math.isnan(value):
                return "n/a"
            return f"{value:.{digits}f}"
        return str(value)

    lines = [
        "# Phase 11 three-way flow comparison plots",
        "",
        "Runs compared:",
        "",
        "| Case | Run | Validation | Kind | Vehicle | Max horiz err | Max height err | Flow fused/rej | cs_opt_flow | Observed GPS loss after takeoff |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["short_label"],
                    Path(row["run_dir"]).name,
                    str(row.get("validation_accepted")),
                    row.get("kind") or "n/a",
                    row.get("vehicle_model") or "n/a",
                    fmt(row.get("horizontal_error_max_m")),
                    fmt(row.get("height_error_max_m")),
                    f"{row.get('of_fused_count', 'n/a')} / {row.get('of_rejected_count', 'n/a')}",
                    fmt(row.get("cs_opt_flow_fraction")),
                    fmt(row.get("observed_gps_loss_after_takeoff_s")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Plot files",
            "",
            "- `plots/route_ekf_vs_gazebo_truth_overlay.png`",
            "- `plots/route_ekf_vs_gazebo_truth_panels.png`",
            "- `plots/horizontal_error_vs_gazebo_truth.png`",
            "- `plots/height_error_vs_gazebo_truth.png`",
            "- `plots/height_px4_vs_gazebo_truth.png`",
            "- `plots/gazebo_truth_route_progress.png`",
            "- `plots/gnss_fix_satellites_over_time.png`",
            "- `plots/gnss_eph_epv_over_time.png`",
            "- `plots/optical_flow_fusion_fraction_1s.png`",
            "- `plots/optical_flow_aid_sample_rate_1s.png`",
            "- `plots/optical_flow_test_ratio_1s.png`",
            "- `plots/optical_flow_quality_over_time.png`",
            "- `plots/ekf_control_status_flags.png`",
            "- `plots/distance_sensor_current_distance.png`",
            "- `plots/summary_metric_bars.png`",
            "",
            "## Interpretation",
            "",
            "SIFT did not magically beat a healthy LK system. The newer SIFT run used the corrected `axis_map: xy` bridge direction and reached EKF optical-flow fusion cleanly: `804 / 0` fused/rejected aid samples, `cs_opt_flow` active for about `0.763` of status-flag samples, max horizontal error `1.214 m`, and max height error `0.389 m`.",
            "",
            "The earlier LK failures were mostly a bad contract problem: old LK configs were sign-inverted or failed to activate optical-flow fusion under GNSS loss. Once LK was rerun on the corrected `xy` contract with `EKF2_OF_N_MIN=0.3`, it also became bounded: `1595 / 0` fused/rejected, max horizontal error `1.093 m`, and max height error `0.377 m`.",
            "",
            "The stock run used here (`122327`) is the first genuinely GNSS-denied stock comparator: `vehicle_gps_position` was verified directly from the ULog to actually drop (`fix_type`/`satellites_used` to `0` at t=22.4 s, staying down for the rest of the flight), unlike an earlier accepted run (`115202`) whose GPS silently never dropped despite the loss command being sent and acknowledged -- see Known limitations. On this genuinely GNSS-denied flight, PX4 stock Gazebo optical flow fused `2839 / 0` aid samples, `cs_opt_flow` active for about `0.705` of status-flag samples, max horizontal error `1.347 m`, and max height error `0.628 m` -- bounded, but not tighter than LK or SIFT as previously reported. Two earlier stock attempts (`112920`, and an incomplete run `113659`) hit `mc_pos_control` position-controller failsafe (`stop and wait` / `blind descent`, 8 occurrences) with optical-flow quality collapsing mid-flight; `112920`'s GPS was also confirmed genuinely cut (t=19.9 s), so that failure is real evidence of a stock GNSS-loss failure mode, not a resource-pressure artifact. Whether stock is reliably bounded under GNSS loss (this run) or prone to failsafe (`112920`) is still open -- n=1 valid GNSS-denied stock run so far, replicates needed.",
            "",
            "The per-run metrics JSON `accepted=false` flag remains a bookkeeping artifact for the skip-landing comparison window in the accepted LK/SIFT cases; use `validation_accepted` plus the ULog/truth evidence above for run classification.",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    data = [load_case(case, out_dir) for case in CASES]
    plot_routes(data, plots_dir)
    plot_truth_errors(data, plots_dir)
    plot_gnss(data, plots_dir)
    plot_flow(data, plots_dir)
    plot_flags_and_range(data, plots_dir)
    plot_summary_bars(data, plots_dir)
    write_report(data, out_dir)

    print(json.dumps([item["summary"] for item in data], indent=2, sort_keys=True))
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
