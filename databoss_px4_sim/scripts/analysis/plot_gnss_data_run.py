#!/usr/bin/env python3
"""Plot GNSS-related PX4 ULog data for one DATABOSS run."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from pyulog import ULog


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _latest_phase10_run() -> Path:
    runs = sorted((REPO_ROOT / "experiments" / "runs").glob("*phase10*"))
    if not runs:
        raise SystemExit("No Phase 10 runs found")
    return runs[-1]


def _dataset(ulog: ULog, name: str):
    matches = [d for d in ulog.data_list if d.name == name]
    return matches[0] if matches else None


def _time_rel_s(data: dict[str, np.ndarray], start_timestamp_us: int) -> np.ndarray:
    ts = np.asarray(data["timestamp"], dtype=np.float64)
    return (ts - float(start_timestamp_us)) / 1e6


def _array(data: dict[str, np.ndarray], key: str) -> np.ndarray | None:
    if key not in data:
        return None
    return np.asarray(data[key], dtype=np.float64)


def _step(ax, t: np.ndarray, y: np.ndarray | None, label: str, **kwargs: Any) -> None:
    if y is None:
        return
    ax.step(t, y, where="post", label=label, **kwargs)


def _line(ax, t: np.ndarray, y: np.ndarray | None, label: str, **kwargs: Any) -> None:
    if y is None:
        return
    ax.plot(t, y, label=label, **kwargs)


def _add_event_lines(axes: list[plt.Axes], events: dict[str, float | None]) -> None:
    styles = {
        "takeoff threshold": ("#555555", "-"),
        "scheduled GNSS loss": ("#b95f02", "--"),
        "observed GPS loss": ("#d62728", "-"),
        "offboard accepted": ("#1f77b4", ":"),
    }
    for ax in axes:
        for label, value in events.items():
            if value is None or not math.isfinite(value):
                continue
            color, linestyle = styles.get(label, ("#777777", "--"))
            ax.axvline(value, color=color, linestyle=linestyle, linewidth=1.1, alpha=0.8)


def _annotate_events(ax: plt.Axes, events: dict[str, float | None]) -> None:
    ylim = ax.get_ylim()
    y = ylim[1]
    for label, value in events.items():
        if value is None or not math.isfinite(value):
            continue
        ax.text(
            value,
            y,
            f" {label}\n {value:.2f}s",
            color="#333333",
            fontsize=8,
            rotation=90,
            va="top",
            ha="left",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.65, "pad": 1.5},
        )


def _first_true_time(t: np.ndarray, mask: np.ndarray) -> float | None:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return None
    return float(t[idx[0]])


def _observed_gps_loss_time(t: np.ndarray, fix_type: np.ndarray, sats: np.ndarray) -> float | None:
    good = (fix_type >= 3) & (sats > 0)
    bad = ~good
    transitions = np.flatnonzero(good[:-1] & bad[1:])
    if len(transitions) == 0:
        return None
    return float(t[transitions[0] + 1])


def _plot_position_trace(gps_data: dict[str, np.ndarray], start_us: int, out_path: Path) -> None:
    lat = _array(gps_data, "latitude_deg")
    lon = _array(gps_data, "longitude_deg")
    fix = _array(gps_data, "fix_type")
    sats = _array(gps_data, "satellites_used")
    if lat is None or lon is None or fix is None or sats is None:
        return

    good = (fix >= 3) & (sats > 0)
    if not np.any(good):
        return

    lat0 = float(lat[good][0])
    lon0 = float(lon[good][0])
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(lat0))
    east = (lon - lon0) * meters_per_deg_lon
    north = (lat - lat0) * meters_per_deg_lat
    t = _time_rel_s(gps_data, start_us)

    fig, ax = plt.subplots(figsize=(8.5, 6.0), constrained_layout=True)
    sc = ax.scatter(east[good], north[good], c=t[good], s=14, cmap="viridis", label="valid GPS fix")
    if np.any(~good):
        ax.scatter(east[~good], north[~good], c="#d62728", s=9, alpha=0.45, label="invalid/no fix")
    ax.set_title("GNSS Position Trace From vehicle_gps_position")
    ax.set_xlabel("East from first valid fix (m)")
    ax.set_ylabel("North from first valid fix (m)")
    ax.axis("equal")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("PX4 log time (s)")
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def plot_run(run_dir: Path, out_dir: Path) -> dict[str, Any]:
    ulog_path = run_dir / "logs" / "flight.ulg"
    if not ulog_path.exists():
        raise SystemExit(f"Missing ULog: {ulog_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    ulog = ULog(str(ulog_path))
    start_us = int(ulog.start_timestamp)

    gps = _dataset(ulog, "vehicle_gps_position") or _dataset(ulog, "sensor_gps")
    estimator_gps = _dataset(ulog, "estimator_gps_status")
    global_position = _dataset(ulog, "vehicle_global_position")
    status_flags = _dataset(ulog, "estimator_status_flags")
    local_position = _dataset(ulog, "vehicle_local_position")
    vehicle_status = _dataset(ulog, "vehicle_status")

    if gps is None:
        raise SystemExit("No vehicle_gps_position or sensor_gps topic found")

    gps_data = gps.data
    gps_t = _time_rel_s(gps_data, start_us)
    fix = _array(gps_data, "fix_type")
    sats = _array(gps_data, "satellites_used")
    eph = _array(gps_data, "eph")
    epv = _array(gps_data, "epv")
    hdop = _array(gps_data, "hdop")
    vdop = _array(gps_data, "vdop")
    vel = _array(gps_data, "vel_m_s")
    vel_n = _array(gps_data, "vel_n_m_s")
    vel_e = _array(gps_data, "vel_e_m_s")
    vel_d = _array(gps_data, "vel_d_m_s")
    s_var = _array(gps_data, "s_variance_m_s")
    h_speed = None
    if vel_n is not None and vel_e is not None:
        h_speed = np.sqrt(vel_n * vel_n + vel_e * vel_e)

    observed_loss = None
    if fix is not None and sats is not None:
        observed_loss = _observed_gps_loss_time(gps_t, fix, sats)

    takeoff_t = None
    if local_position is not None and "z" in local_position.data:
        lp_t = _time_rel_s(local_position.data, start_us)
        z = np.asarray(local_position.data["z"], dtype=np.float64)
        takeoff_t = _first_true_time(lp_t, -z > 0.5)

    offboard_t = None
    if vehicle_status is not None and "accepts_offboard_setpoints" in vehicle_status.data:
        vs_t = _time_rel_s(vehicle_status.data, start_us)
        offboard = np.asarray(vehicle_status.data["accepts_offboard_setpoints"], dtype=np.float64) > 0
        offboard_t = _first_true_time(vs_t, offboard)

    status = _load_json(run_dir / "logs" / "pxh_takeoff_land_truth_status.json")
    metrics = _load_json(run_dir / "ekf_vs_ground_truth_metrics.json")
    configured_after_takeoff = status.get("effective_gnss_loss_after_takeoff_s")
    if configured_after_takeoff is None:
        configured_after_takeoff = status.get("gnss_loss_after_takeoff_s")
    if configured_after_takeoff is None:
        configured_after_takeoff = metrics.get("gnss_loss_after_takeoff_s")
    scheduled_loss = None
    if takeoff_t is not None and configured_after_takeoff is not None:
        scheduled_loss = takeoff_t + float(configured_after_takeoff)

    events = {
        "takeoff threshold": takeoff_t,
        "offboard accepted": offboard_t,
        "observed GPS loss": observed_loss,
        "scheduled GNSS loss": scheduled_loss,
    }

    fig, axes = plt.subplots(4, 1, figsize=(13.5, 10.5), sharex=True, constrained_layout=True)
    fig.suptitle(f"GNSS Data Overview\n{run_dir.name}", fontsize=13)

    ax = axes[0]
    _step(ax, gps_t, fix, "fix_type", color="#1f77b4", linewidth=1.6)
    ax.set_ylabel("fix_type")
    ax.set_ylim(-0.2, 4.2)
    ax.grid(True, alpha=0.3)
    ax2 = ax.twinx()
    _step(ax2, gps_t, sats, "satellites_used", color="#2ca02c", linewidth=1.4)
    ax2.set_ylabel("satellites")
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc="upper right")

    ax = axes[1]
    _line(ax, gps_t, eph, "GPS eph (m)", color="#9467bd")
    _line(ax, gps_t, epv, "GPS epv (m)", color="#8c564b")
    _line(ax, gps_t, hdop, "hdop", color="#17becf", alpha=0.75)
    _line(ax, gps_t, vdop, "vdop", color="#bcbd22", alpha=0.75)
    ax.set_ylabel("accuracy / DOP")
    ax.set_yscale("symlog", linthresh=1.0)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    ax = axes[2]
    _line(ax, gps_t, vel, "GPS vel_m_s", color="#ff7f0e")
    _line(ax, gps_t, h_speed, "GPS horizontal speed", color="#1f77b4")
    _line(ax, gps_t, vel_d, "GPS vel_d_m_s", color="#d62728", alpha=0.85)
    _line(ax, gps_t, s_var, "s_variance_m_s", color="#7f7f7f", alpha=0.75)
    ax.set_ylabel("velocity / variance")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    ax = axes[3]
    if status_flags is not None:
        sf_t = _time_rel_s(status_flags.data, start_us)
        _step(ax, sf_t, _array(status_flags.data, "cs_gnss_pos"), "EKF cs_gnss_pos", color="#1f77b4")
        _step(ax, sf_t, _array(status_flags.data, "cs_gnss_vel"), "EKF cs_gnss_vel", color="#2ca02c")
        _step(ax, sf_t, _array(status_flags.data, "cs_opt_flow"), "EKF cs_opt_flow", color="#ff7f0e")
        _step(
            ax,
            sf_t,
            _array(status_flags.data, "cs_inertial_dead_reckoning"),
            "EKF inertial_dead_reckoning",
            color="#d62728",
        )
    if global_position is not None:
        gp_t = _time_rel_s(global_position.data, start_us)
        _step(ax, gp_t, _array(global_position.data, "dead_reckoning"), "global_position.dead_reckoning", color="#9467bd")
    ax.set_ylabel("estimator flags")
    ax.set_xlabel("PX4 log time since ULog start (s)")
    ax.set_ylim(-0.15, 1.25)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", ncol=2)

    _add_event_lines(list(axes), events)
    _annotate_events(axes[0], events)

    overview_path = out_dir / "gnss_data_over_time.png"
    fig.savefig(overview_path, dpi=180)
    plt.close(fig)

    trace_path = out_dir / "gnss_position_trace.png"
    _plot_position_trace(gps_data, start_us, trace_path)

    summary = {
        "run_dir": str(run_dir),
        "ulog": str(ulog_path),
        "plot": str(overview_path),
        "position_trace_plot": str(trace_path) if trace_path.exists() else None,
        "ulog_start_timestamp_us": start_us,
        "gps_topic": gps.name,
        "gps_rows": int(len(gps_t)),
        "takeoff_threshold_rel_s": takeoff_t,
        "offboard_accepted_rel_s": offboard_t,
        "scheduled_gnss_loss_rel_s": scheduled_loss,
        "scheduled_gnss_loss_after_takeoff_s": configured_after_takeoff,
        "observed_gps_loss_rel_s": observed_loss,
        "observed_gps_loss_after_takeoff_s": (
            observed_loss - takeoff_t if observed_loss is not None and takeoff_t is not None else None
        ),
        "fix_type_unique": sorted({int(x) for x in fix}) if fix is not None else [],
        "satellites_used_min": int(np.nanmin(sats)) if sats is not None else None,
        "satellites_used_max": int(np.nanmax(sats)) if sats is not None else None,
        "eph_min_m": float(np.nanmin(eph)) if eph is not None else None,
        "eph_max_m": float(np.nanmax(eph)) if eph is not None else None,
        "epv_min_m": float(np.nanmin(epv)) if epv is not None else None,
        "epv_max_m": float(np.nanmax(epv)) if epv is not None else None,
        "validation_accepted": status.get("accepted"),
        "metrics_accepted": metrics.get("accepted"),
        "comparison_window_ok": metrics.get("comparison_window_ok"),
        "comparison_end_reason": metrics.get("comparison_end_reason"),
    }

    with (out_dir / "gnss_data_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir or _latest_phase10_run()
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir
    out_dir = args.out_dir or (run_dir / "plots")
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir

    summary = plot_run(run_dir, out_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
