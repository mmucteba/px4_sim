#!/usr/bin/env python3
"""Plot the truth-vs-EKF ground route for a single run and flag anything unusual.

Generic per-run counterpart to plot_phase8m_route_compare.py (which is
hardcoded to one historical 2-case comparison). Reads the run's own
ekf_vs_ground_truth_aligned.csv / _metrics.json / status.json -- all
produced automatically by run_scenario_pxh_end_to_end.py's postprocess +
align steps, so no extra postprocessing is required before running this.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/databoss-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
import gzip
import shutil
import tempfile
from pyulog import ULog

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# PX4 vehicle_status nav_state values relevant here (see VehicleStatus.msg).
NAV_STATE_OFFBOARD = 14
NAV_STATE_NAMES = {
    0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER",
    5: "AUTO_RTL", 12: "DESCEND", 13: "TERMINATION", 14: "OFFBOARD", 15: "STAB",
    17: "AUTO_TAKEOFF", 18: "AUTO_LAND", 20: "AUTO_PRECLAND",
}


def open_ulog(run_dir: Path) -> ULog:
    ulog_path = run_dir / "logs/flight.ulg"
    if ulog_path.exists():
        return ULog(str(ulog_path))
    gz_path = run_dir / "logs/flight.ulg.gz"
    with tempfile.NamedTemporaryFile(suffix=".ulg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with gzip.open(gz_path, "rb") as src:
            shutil.copyfileobj(src, tmp)
    try:
        return ULog(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)


def check_nav_state_departure(run_dir: Path, scenario: dict[str, Any]) -> list[str]:
    """Flag an unrequested mode change away from OFFBOARD (e.g. an offboard-loss RTL)."""
    control = scenario.get("control", {})
    if control.get("mode") != "offboard_local_position_hold":
        return []
    if not (run_dir / "logs/flight.ulg").exists() and not (run_dir / "logs/flight.ulg.gz").exists():
        return []

    ulog = open_ulog(run_dir)
    datasets = [d for d in ulog.data_list if d.name == "vehicle_status"]
    if not datasets:
        return []
    data = datasets[0].data
    t = (data["timestamp"] - data["timestamp"][0]) / 1e6
    nav = data["nav_state"]

    entered_offboard = False
    findings = []
    for i in range(len(t)):
        if nav[i] == NAV_STATE_OFFBOARD:
            entered_offboard = True
            continue
        if entered_offboard and nav[i] != NAV_STATE_OFFBOARD:
            name = NAV_STATE_NAMES.get(int(nav[i]), str(int(nav[i])))
            findings.append(
                f"nav_state left OFFBOARD at t={t[i]:.1f}s (-> {name}) without a requested landing -- "
                f"likely an offboard-signal-loss failsafe (COM_OF_LOSS_T), not a real navigation event"
            )
            break
    return findings

TRUTH_JUMP_THRESHOLD_M = 3.0
GROUND_COLLISION_MARGIN_M = 0.3
CLIMB_HEIGHT_TRANSIENT_THRESHOLD_M = 5.0
CLIMB_HEIGHT_TRANSIENT_SETTLED_TAIL_S = 15.0


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def load_run(run_dir: Path) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    metrics = json.loads((run_dir / "ekf_vs_ground_truth_metrics.json").read_text())
    status = json.loads((run_dir / "logs/pxh_takeoff_land_truth_status.json").read_text())
    return aligned, metrics, status


def load_scenario(status: dict[str, Any]) -> dict[str, Any]:
    scenario_path = Path(status["scenario"])
    return yaml.safe_load(scenario_path.read_text()) or {}


def load_wind_manifest(scenario: dict[str, Any]) -> dict[str, Any] | None:
    sdf_path = scenario.get("world", {}).get("sdf_path")
    if not sdf_path:
        return None
    manifest_path = (PROJECT_ROOT / sdf_path).with_suffix(".manifest.json")
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text())


def gnss_loss_t_rel_s(metrics: dict[str, Any], status: dict[str, Any]) -> float | None:
    if not status.get("gnss_loss_requested"):
        return None
    takeoff_t = metrics.get("px4_takeoff_crossing_t_rel_s")
    loss_after = status.get("effective_gnss_loss_after_takeoff_s")
    if takeoff_t is None or loss_after is None:
        return None
    return float(takeoff_t) + float(loss_after)


def bearing_deg(east: float, north: float) -> float:
    return math.degrees(math.atan2(east, north)) % 360.0


def plot_route(
    aligned: pd.DataFrame,
    scenario: dict[str, Any],
    wind: dict[str, Any] | None,
    loss_t: float | None,
    title: str,
    out_path: Path,
) -> None:
    gx, gy = aligned["gz_x_rel"], aligned["gz_y_rel"]
    px, py = aligned["px4_x_rel"], aligned["px4_y_rel"]

    plt.figure(figsize=(7.6, 7.2))
    plt.plot(gy, gx, color="#111111", linewidth=2.0, label="Gazebo truth")
    plt.plot(py, px, color="#1f77b4", linewidth=1.4, linestyle="--", label="PX4 EKF")
    plt.scatter([gy.iloc[0]], [gx.iloc[0]], color="#2ca02c", s=55, marker="o", zorder=5, label="start")
    plt.scatter([gy.iloc[-1]], [gx.iloc[-1]], color="#d62728", s=70, marker="x", zorder=5, label="truth end")

    control = scenario.get("control", {})
    vx, vy = float(control.get("vx_m_s", 0.0)), float(control.get("vy_m_s", 0.0))
    if vx or vy:
        norm = math.hypot(vx, vy)
        extent = max(gy.abs().max(), gx.abs().max(), py.abs().max(), px.abs().max(), 1.0)
        ux, uy = vx / norm, vy / norm
        plt.plot([-uy * extent, uy * extent], [-ux * extent, ux * extent],
                 color="#999999", linewidth=1.0, linestyle=":", alpha=0.6, label="commanded course")

    if loss_t is not None:
        idx = (aligned["px4_t_rel_s"] - loss_t).abs().idxmin()
        plt.scatter([gy.iloc[idx]], [gx.iloc[idx]], color="#ff7f0e", s=90, marker="*", zorder=6,
                    label=f"GNSS loss (t={loss_t:.1f}s)")

    subtitle = ""
    if wind and wind.get("wind_enabled"):
        east, north = wind["wind_direction_vector_enu"]
        subtitle = f"\nwind: {wind['wind_mean_mps']:.1f} m/s toward bearing {bearing_deg(east, north):.0f} deg (0=N,90=E)"

    plt.xlabel("East relative (m)")
    plt.ylabel("North relative (m)")
    plt.title(title + subtitle, fontsize=10)
    plt.axis("equal")
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=8)
    savefig(out_path)


def plot_error_timeseries(aligned: pd.DataFrame, loss_t: float | None, title: str, out_path: Path) -> None:
    t = aligned["px4_t_rel_s"]
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.8), sharex=True)
    axes[0].plot(t, aligned["horizontal_error_m"], color="#1f77b4", linewidth=1.4)
    axes[0].set_ylabel("horizontal error (m)")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(t, aligned["abs_height_error_m"], color="#d62728", linewidth=1.4)
    axes[1].set_ylabel("height error (m)")
    axes[1].set_xlabel("PX4 relative time (s)")
    axes[1].grid(True, alpha=0.3)
    if loss_t is not None:
        for ax in axes:
            ax.axvline(loss_t, color="#ff7f0e", linewidth=1.2, linestyle="--", alpha=0.8)
        axes[0].text(loss_t, axes[0].get_ylim()[1] * 0.9, " GNSS loss", color="#ff7f0e", fontsize=8)
    fig.suptitle(title, fontsize=10)
    savefig(out_path)


def check_anomalies(
    aligned: pd.DataFrame,
    metrics: dict[str, Any],
    status: dict[str, Any],
    scenario: dict[str, Any],
    wind: dict[str, Any] | None,
) -> dict[str, Any]:
    findings: list[str] = []

    gx, gy = aligned["gz_x_rel"], aligned["gz_y_rel"]
    px, py = aligned["px4_x_rel"], aligned["px4_y_rel"]
    gz_height = aligned["gz_height_up"]

    truth_nan = int(gx.isna().sum() + gy.isna().sum())
    ekf_nan = int(px.isna().sum() + py.isna().sum())
    if truth_nan:
        findings.append(f"{truth_nan} NaN samples in Gazebo truth xy -- truth logger gap")
    if ekf_nan:
        findings.append(f"{ekf_nan} NaN samples in PX4 EKF xy")

    step = np.hypot(gx.diff(), gy.diff())
    max_step = float(step.max(skipna=True)) if len(step) else 0.0
    if max_step > TRUTH_JUMP_THRESHOLD_M:
        findings.append(f"truth position jumped {max_step:.2f} m between consecutive samples (possible teleport/glitch)")

    # Ground-collision check only applies to the airborne window -- rows
    # before takeoff (and after an intentional landing) are on the ground
    # by design, so a height near 0 there is normal, not a strike.
    takeoff_t = metrics.get("px4_takeoff_crossing_t_rel_s")
    land_t = metrics.get("land_command_t_rel_s")
    airborne = aligned
    if takeoff_t is not None:
        airborne = airborne[airborne["px4_t_rel_s"] > float(takeoff_t) + 1.0]
    if land_t is not None:
        airborne = airborne[airborne["px4_t_rel_s"] < float(land_t) - 1.0]
    airborne_height = airborne["gz_height_up"]
    min_height = float(airborne_height.min(skipna=True)) if len(airborne_height) else None
    if min_height is not None and min_height < GROUND_COLLISION_MARGIN_M:
        findings.append(f"Gazebo truth height dropped to {min_height:.2f} m during the airborne window (possible ground strike)")

    height_err = airborne["abs_height_error_m"] if "abs_height_error_m" in airborne else pd.Series(dtype=float)
    transient_max = float(height_err.max(skipna=True)) if len(height_err) else 0.0
    if transient_max > CLIMB_HEIGHT_TRANSIENT_THRESHOLD_M:
        peak_t = float(airborne.loc[height_err.idxmax(), "px4_t_rel_s"])
        tail_mask = airborne["px4_t_rel_s"] > (airborne["px4_t_rel_s"].max() - CLIMB_HEIGHT_TRANSIENT_SETTLED_TAIL_S)
        settled = airborne.loc[tail_mask, "abs_height_error_m"]
        settled_residual = float(settled.mean(skipna=True)) if len(settled) else transient_max
        if settled_residual < max(2.0, 0.25 * transient_max):
            findings.append(
                f"info: EKF-vs-truth height error peaked at {transient_max:.1f} m (t={peak_t:.1f}s) then settled to "
                f"{settled_residual:.2f} m by end of run -- climb-phase estimator transient, self-corrects, not a persistent fault"
            )
        else:
            findings.append(
                f"EKF-vs-truth height error peaked at {transient_max:.1f} m (t={peak_t:.1f}s) and did not settle "
                f"(end-of-run residual {settled_residual:.2f} m) -- possible persistent height estimation fault"
            )

    net_east = float(gy.iloc[-1] - gy.iloc[0])
    net_north = float(gx.iloc[-1] - gx.iloc[0])
    net_mag = math.hypot(net_east, net_north)
    net_bearing = bearing_deg(net_east, net_north) if net_mag > 0.05 else None

    control = scenario.get("control", {})
    vx, vy = float(control.get("vx_m_s", 0.0)), float(control.get("vy_m_s", 0.0))
    commanded_bearing = bearing_deg(vy, vx) if (vx or vy) else None

    gnss_state = "on" if not status.get("gnss_loss_requested") else "loss"
    if gnss_state == "on" and commanded_bearing is not None and net_bearing is not None:
        angle_diff = min(abs(net_bearing - commanded_bearing), 360 - abs(net_bearing - commanded_bearing))
        if angle_diff > 60 and net_mag > 1.0:
            findings.append(
                f"GNSS-on case: truth net course bearing {net_bearing:.0f} deg deviates {angle_diff:.0f} deg "
                f"from commanded {commanded_bearing:.0f} deg (mag {net_mag:.2f} m)"
            )

    if wind and wind.get("wind_enabled") and gnss_state == "loss":
        east, north = wind["wind_direction_vector_enu"]
        wind_bearing = bearing_deg(east, north)
        if net_bearing is not None:
            angle_diff = min(abs(net_bearing - wind_bearing), 360 - abs(net_bearing - wind_bearing))
            findings.append(
                f"info: GNSS-loss net truth drift bearing {net_bearing:.0f} deg vs wind bearing "
                f"{wind_bearing:.0f} deg (diff {angle_diff:.0f} deg, mag {net_mag:.2f} m) -- drift is expected evidence, not a failure"
            )

    horiz_max = metrics.get("horizontal_error", {}).get("max_m")
    if horiz_max is not None and (math.isnan(horiz_max) if isinstance(horiz_max, float) else False):
        findings.append("horizontal_error.max_m is NaN -- alignment/metrics pipeline problem")

    try:
        nav_findings = check_nav_state_departure(Path(status["run_dir"]), scenario)
    except Exception as exc:  # ULog missing/corrupt shouldn't block the rest of the report
        nav_findings = [f"could not check nav_state transitions ({exc})"]
    for nav_finding in nav_findings:
        if gnss_state == "loss":
            # Under GNSS loss, EKF/position degradation is the experimental
            # subject -- a failsafe mode change can be a downstream
            # consequence of already-documented drift, not an independent
            # infra problem. Report it, but don't let it drive "unusual".
            findings.append(
                f"info: {nav_finding} (GNSS-loss case: may be a consequence of estimator degradation, not infra)"
            )
        else:
            findings.append(nav_finding)

    return {
        "run_dir": str(status.get("run_dir")),
        "gnss_state": gnss_state,
        "truth_nan_samples": truth_nan,
        "ekf_nan_samples": ekf_nan,
        "max_truth_step_m": max_step,
        "min_truth_height_m": min_height,
        "net_truth_east_m": net_east,
        "net_truth_north_m": net_north,
        "net_truth_bearing_deg": net_bearing,
        "commanded_bearing_deg": commanded_bearing,
        "wind_enabled": bool(wind and wind.get("wind_enabled")),
        "wind_mean_mps": wind.get("wind_mean_mps") if wind else None,
        "findings": findings,
        "unusual": any(not f.startswith("info:") for f in findings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot single-run truth-vs-EKF route and flag anomalies.")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    aligned, metrics, status = load_run(run_dir)
    scenario = load_scenario(status)
    wind = load_wind_manifest(scenario)
    loss_t = gnss_loss_t_rel_s(metrics, status)

    title = run_dir.name
    plots_dir = run_dir / "plots"
    plot_route(aligned, scenario, wind, loss_t, title, plots_dir / "route_truth_vs_ekf.png")
    plot_error_timeseries(aligned, loss_t, title, plots_dir / "route_error_timeseries.png")

    report = check_anomalies(aligned, metrics, status, scenario, wind)
    (run_dir / "route_anomaly_check.json").write_text(json.dumps(report, indent=2))

    print(f"route_plot={plots_dir / 'route_truth_vs_ekf.png'}")
    print(f"error_plot={plots_dir / 'route_error_timeseries.png'}")
    print(f"anomaly_report={run_dir / 'route_anomaly_check.json'}")
    print(f"unusual={report['unusual']}")
    for finding in report["findings"]:
        print(f"  - {finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
