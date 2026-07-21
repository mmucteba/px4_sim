#!/usr/bin/env python3
"""Phase 8L Gate 6 loop diagnosis.

Question: with GNSS on and EKF position tracking truth, why does the vehicle
fly loops instead of the commanded straight +Y leg (vx=0, vy=0.2 m/s)?

Discriminating evidence per run:
  A) EKF velocity vs Gazebo truth velocity (PX4 NED frame).
     - EKF vel ~= setpoint while truth vel loops  -> estimator velocity is
       corrupted (flow fusion drags velocity while GNSS pins position).
     - EKF vel ~= truth vel (both loop)           -> estimator is honest and
       the controller/attitude path is producing the loop (e.g. yaw error).
  B) EKF heading vs Gazebo truth yaw (toilet-bowl check).
  C) estimator_status test ratios and estimator_innovations flow/gps channels.

Frame rule (docs/architecture/frames_and_alignment.md, 2026-07-13):
  px4_x(N) = gz_y - gz_y0 ; px4_y(E) = gz_x - gz_x0 ; px4_z(D) = -(gz_z - gz_z0)
  => truth NED velocity: vN = d(gz_y)/dt, vE = d(gz_x)/dt.
Truth yaw (ENU, about +Z up) maps to NED heading as: yaw_ned = pi/2 - yaw_enu.

Time alignment: takeoff edge (height crossing +1.0 m upward) in each stream.
"""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def moving_average(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def takeoff_time(t: np.ndarray, height_up: np.ndarray, thresh: float = 1.0) -> float:
    above = height_up > thresh
    idx = np.argmax(above)
    if not above.any():
        raise RuntimeError("height never crossed threshold")
    return float(t[idx])


def quat_yaw_enu(qw, qx, qy, qz):
    return np.arctan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


def wrap_pi(a):
    return (a + np.pi) % (2.0 * np.pi) - np.pi


def analyze_run(run_dir: Path, label: str, out_dir: Path) -> dict:
    lp = pd.read_csv(run_dir / "extracted_csv" / "vehicle_local_position.csv")
    st = pd.read_csv(run_dir / "extracted_csv" / "estimator_status.csv")
    inn = pd.read_csv(run_dir / "extracted_csv" / "estimator_innovations.csv")
    att = pd.read_csv(run_dir / "extracted_csv" / "vehicle_attitude.csv")
    truth_csv = next((run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
    gt = pd.read_csv(truth_csv)

    # --- PX4 side ---
    t_px4 = lp["timestamp"].to_numpy() / 1e6
    h_px4 = -(lp["z"].to_numpy() - lp["z"].to_numpy()[0])
    t0_px4 = takeoff_time(t_px4, h_px4)

    # --- truth side ---
    t_gz = gt["sim_time_s"].to_numpy()
    h_gz = gt["z"].to_numpy() - gt["z"].to_numpy()[0]
    t0_gz = takeoff_time(t_gz, h_gz)

    # relative time after each stream's takeoff edge
    tr_px4 = t_px4 - t0_px4
    tr_gz = t_gz - t0_gz

    # truth NED velocity by finite difference, lightly smoothed (~0.5 s)
    dt_gz = np.median(np.diff(t_gz))
    win = max(1, int(round(0.5 / dt_gz)))
    vN_gz = moving_average(np.gradient(gt["y"].to_numpy(), t_gz), win)
    vE_gz = moving_average(np.gradient(gt["x"].to_numpy(), t_gz), win)

    # interpolate truth velocity onto PX4 timeline
    vN_gz_i = np.interp(tr_px4, tr_gz, vN_gz)
    vE_gz_i = np.interp(tr_px4, tr_gz, vE_gz)

    vx_ekf = lp["vx"].to_numpy()
    vy_ekf = lp["vy"].to_numpy()
    heading_ekf = lp["heading"].to_numpy()

    # truth yaw on PX4 timeline
    yaw_enu = quat_yaw_enu(gt["qw"].to_numpy(), gt["qx"].to_numpy(),
                           gt["qy"].to_numpy(), gt["qz"].to_numpy())
    yaw_ned = wrap_pi(np.pi / 2.0 - yaw_enu)
    yaw_unwrapped = np.unwrap(yaw_ned)
    yaw_gz_i = np.interp(tr_px4, tr_gz, yaw_unwrapped)
    yaw_err = wrap_pi(heading_ekf - wrap_pi(yaw_gz_i))

    # cruise window: leg starts ~ after takeoff+climb; use 15..60 s after takeoff
    cw = (tr_px4 > 15.0) & (tr_px4 < 60.0)

    speed_truth = np.hypot(vN_gz_i, vE_gz_i)
    speed_ekf = np.hypot(vx_ekf, vy_ekf)
    vel_gap = np.hypot(vx_ekf - vN_gz_i, vy_ekf - vE_gz_i)

    def corr(a, b):
        a = a[cw]; b = b[cw]
        if a.std() < 1e-6 or b.std() < 1e-6:
            return float("nan")
        return float(np.corrcoef(a, b)[0, 1])

    # estimator status ratios on same relative clock
    tr_st = st["timestamp"].to_numpy() / 1e6 - t0_px4
    tr_inn = inn["timestamp"].to_numpy() / 1e6 - t0_px4
    cw_st = (tr_st > 15.0) & (tr_st < 60.0)

    metrics = {
        "run": run_dir.name,
        "label": label,
        "cruise_window_s": [15.0, 60.0],
        "mean_truth_speed_mps": float(np.nanmean(speed_truth[cw])),
        "mean_ekf_speed_mps": float(np.nanmean(speed_ekf[cw])),
        "mean_ekf_minus_truth_vel_gap_mps": float(np.nanmean(vel_gap[cw])),
        "corr_vN_ekf_vs_truth": corr(vx_ekf, vN_gz_i),
        "corr_vE_ekf_vs_truth": corr(vy_ekf, vE_gz_i),
        "mean_abs_yaw_err_deg": float(np.degrees(np.nanmean(np.abs(yaw_err[cw])))),
        "max_abs_yaw_err_deg": float(np.degrees(np.nanmax(np.abs(yaw_err[cw])))),
        "max_vel_test_ratio": float(np.nanmax(st["vel_test_ratio"].to_numpy()[cw_st])),
        "max_pos_test_ratio": float(np.nanmax(st["pos_test_ratio"].to_numpy()[cw_st])),
        "max_hdg_test_ratio": float(np.nanmax(st["hdg_test_ratio"].to_numpy()[cw_st])),
        "setpoint_mps": [0.0, 0.2],
    }

    fig, axes = plt.subplots(4, 1, figsize=(11, 13), sharex=True)
    ax = axes[0]
    ax.plot(tr_px4, vN_gz_i, "k", lw=1.8, label="truth vN")
    ax.plot(tr_px4, vx_ekf, "C0", lw=1.0, label="EKF vx (N)")
    ax.axhline(0.0, color="gray", ls="--", lw=0.8, label="setpoint vN=0")
    ax.set_ylabel("vN (m/s)")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"{label}: EKF vs truth velocity, yaw, test ratios")

    ax = axes[1]
    ax.plot(tr_px4, vE_gz_i, "k", lw=1.8, label="truth vE")
    ax.plot(tr_px4, vy_ekf, "C1", lw=1.0, label="EKF vy (E)")
    ax.axhline(0.2, color="gray", ls="--", lw=0.8, label="setpoint vE=0.2")
    ax.set_ylabel("vE (m/s)")
    ax.legend(loc="upper right", fontsize=8)

    ax = axes[2]
    ax.plot(tr_px4, np.degrees(wrap_pi(yaw_gz_i)), "k", lw=1.8, label="truth yaw (NED)")
    ax.plot(tr_px4, np.degrees(heading_ekf), "C2", lw=1.0, label="EKF heading")
    ax.set_ylabel("yaw (deg)")
    ax.legend(loc="upper right", fontsize=8)

    ax = axes[3]
    ax.plot(tr_st, st["vel_test_ratio"], label="vel_test_ratio")
    ax.plot(tr_st, st["pos_test_ratio"], label="pos_test_ratio")
    ax.plot(tr_st, st["hdg_test_ratio"], label="hdg_test_ratio")
    ax.axhline(1.0, color="r", ls="--", lw=0.8, label="reject threshold")
    ax.set_ylabel("test ratio")
    ax.set_xlabel("time since takeoff edge (s)")
    ax.set_xlim(-5, 90)
    ax.legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    png = out_dir / f"vel_yaw_diag_{label}.png"
    fig.savefig(png, dpi=110)
    plt.close(fig)
    metrics["plot"] = str(png)

    # innovation detail plot: flow vs gps velocity innovations
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    ax = axes[0]
    ax.plot(tr_inn, inn["flow[0]"], label="flow innov [0]")
    ax.plot(tr_inn, inn["flow[1]"], label="flow innov [1]")
    ax.set_ylabel("flow innov (rad/s)")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"{label}: EKF innovations")
    ax = axes[1]
    ax.plot(tr_inn, inn["gps_hvel[0]"], label="gps_hvel[0] (N)")
    ax.plot(tr_inn, inn["gps_hvel[1]"], label="gps_hvel[1] (E)")
    ax.set_ylabel("gps vel innov (m/s)")
    ax.set_xlabel("time since takeoff edge (s)")
    ax.set_xlim(-5, 90)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    png2 = out_dir / f"innovations_{label}.png"
    fig.savefig(png2, dpi=110)
    plt.close(fig)
    metrics["innovations_plot"] = str(png2)

    return metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    batch = Path(args.batch)
    out_dir = Path(args.out) if args.out else batch / "velocity_loop_diagnosis"
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_summary = json.loads((batch / "batch_summary.json").read_text())
    runs = []
    for case in batch_summary.get("results", []):
        run_dir = case.get("run_dir")
        label = case.get("name")
        if run_dir:
            runs.append((label, Path(run_dir)))
    if not runs:
        raise SystemExit("no runs found in batch_summary.json")

    all_metrics = []
    for label, run_dir in runs:
        print(f"== {label}: {run_dir.name}")
        m = analyze_run(run_dir, label, out_dir)
        all_metrics.append(m)
        print(json.dumps({k: v for k, v in m.items() if k not in ("run",)},
                         indent=2))

    (out_dir / "velocity_loop_diagnosis.json").write_text(
        json.dumps(all_metrics, indent=2))

    lines = [
        "# Gate 6 Velocity Loop Diagnosis", "",
        "Setpoint during leg: vN=0.0, vE=0.2 m/s (LOCAL_NED, velocity_xy_position_z).", "",
        "| Case | truth speed m/s | EKF speed m/s | vel gap m/s | corr vN | corr vE | yaw err mean/max deg | max vel/pos/hdg ratio |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for m in all_metrics:
        lines.append(
            f"| {m['label']} | {m['mean_truth_speed_mps']:.3f} | "
            f"{m['mean_ekf_speed_mps']:.3f} | "
            f"{m['mean_ekf_minus_truth_vel_gap_mps']:.3f} | "
            f"{m['corr_vN_ekf_vs_truth']:.3f} | {m['corr_vE_ekf_vs_truth']:.3f} | "
            f"{m['mean_abs_yaw_err_deg']:.1f} / {m['max_abs_yaw_err_deg']:.1f} | "
            f"{m['max_vel_test_ratio']:.2f} / {m['max_pos_test_ratio']:.2f} / "
            f"{m['max_hdg_test_ratio']:.2f} |")
    lines += [
        "",
        "Reading:",
        "- EKF speed ~= 0.2 with truth speed >> 0.2 -> estimator velocity corrupted.",
        "- EKF vel correlates ~1.0 with truth vel -> estimator honest; controller path bends the route.",
        "- Large yaw err -> toilet-bowl candidate.",
        "",
    ]
    (out_dir / "velocity_loop_diagnosis.md").write_text("\n".join(lines))
    print(f"\nwrote {out_dir}/velocity_loop_diagnosis.md")


if __name__ == "__main__":
    main()
