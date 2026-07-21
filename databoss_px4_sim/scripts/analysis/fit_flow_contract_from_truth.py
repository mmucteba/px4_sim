#!/usr/bin/env python3
"""Empirical optical-flow wire-contract fit against Gazebo truth.

For each sent flow sample, build truth-side regressors at the same sim time:
  vbx/h, vby/h  : truth body-frame horizontal velocity over height AGL
  wx, wy        : truth body rates (from quaternion differentiation)
and fit least squares:
  flow_rate_x_sent ~ c0*vbx/h + c1*vby/h + c2*wx + c3*wy + c4
  flow_rate_y_sent ~ (same regressors)

Expected MAVLink/SensorOpticalFlow wire convention before EKF2 ingestion:
  pixel_flow[0] = -vby/h + wx   (coefficients [0, -1, +1, 0])
  pixel_flow[1] = +vbx/h + wy   (coefficients [+1, 0, 0, +1])

EKF2 then negates pixel_flow and delta_angle at ingestion before fusion. This
script only fits the wire-side OPTICAL_FLOW_RAD contract. Use
check_flow_velocity_sign.py to verify that PX4's estimator_optical_flow_vel
has the correct body-velocity sign after EKF2 ingestion.

The fitted coefficients expose scale errors, axis swaps, sign flips, and
rotation-term sign errors that pure-translation open-loop gates cannot see.

Body frame note: PX4 body = FRD; Gazebo model body = FLU.
  v_body_frd = [ v_flu_x, -v_flu_y, -v_flu_z ], same for rates.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def quat_to_rot(qw, qx, qy, qz):
    """Rotation matrices world<-body for arrays of quaternions."""
    n = len(qw)
    R = np.empty((n, 3, 3))
    R[:, 0, 0] = 1 - 2 * (qy * qy + qz * qz)
    R[:, 0, 1] = 2 * (qx * qy - qw * qz)
    R[:, 0, 2] = 2 * (qx * qz + qw * qy)
    R[:, 1, 0] = 2 * (qx * qy + qw * qz)
    R[:, 1, 1] = 1 - 2 * (qx * qx + qz * qz)
    R[:, 1, 2] = 2 * (qy * qz - qw * qx)
    R[:, 2, 0] = 2 * (qx * qz - qw * qy)
    R[:, 2, 1] = 2 * (qy * qz + qw * qx)
    R[:, 2, 2] = 1 - 2 * (qx * qx + qy * qy)
    return R


def body_rates_from_quat(t, qw, qx, qy, qz):
    """Body angular rates (FLU) from quaternion time series (world<-body)."""
    q = np.stack([qw, qx, qy, qz], axis=1)
    # enforce continuity
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    dq = np.gradient(q, t, axis=0)
    # omega_body = 2 * q^-1 * dq  (vector part), q = [w, x, y, z]
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    dw, dx, dy, dz = dq[:, 0], dq[:, 1], dq[:, 2], dq[:, 3]
    ox = 2 * (-x * dw + w * dx + z * dy - y * dz)
    oy = 2 * (-y * dw - z * dx + w * dy + x * dz)
    oz = 2 * (-z * dw + y * dx - x * dy + w * dz)
    return np.stack([ox, oy, oz], axis=1)


def smooth(x, n):
    if n <= 1:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


def analyze(run_dir: Path) -> dict:
    fb = pd.read_csv(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
    truth_csv = next((run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
    gt = pd.read_csv(truth_csv)

    # real sent samples with actual flow content
    m = (fb["sent"] == 1) & (fb["n_matches"] > 0) & fb["range_m"].notna()
    fb = fb[m].reset_index(drop=True)
    t_f = fb["t_frame_sim_s"].to_numpy()
    dt = fb["integration_dt_s"].to_numpy()
    fx = fb["integrated_x_sent"].to_numpy() / dt
    fy = fb["integrated_y_sent"].to_numpy() / dt
    rng = fb["range_m"].to_numpy()

    # truth signals
    t_g = gt["sim_time_s"].to_numpy()
    vx_w = smooth(np.gradient(gt["x"].to_numpy(), t_g), 5)
    vy_w = smooth(np.gradient(gt["y"].to_numpy(), t_g), 5)
    vz_w = smooth(np.gradient(gt["z"].to_numpy(), t_g), 5)
    qw, qx, qy, qz = (gt[k].to_numpy() for k in ("qw", "qx", "qy", "qz"))
    R = quat_to_rot(qw, qx, qy, qz)  # world <- body(FLU)
    v_w = np.stack([vx_w, vy_w, vz_w], axis=1)
    v_b_flu = np.einsum("nij,nj->ni", R.transpose(0, 2, 1), v_w)
    om_flu = body_rates_from_quat(t_g, qw, qx, qy, qz)
    om_flu = np.stack([smooth(om_flu[:, i], 5) for i in range(3)], axis=1)

    # FLU -> FRD (PX4 body)
    vbx = v_b_flu[:, 0]
    vby = -v_b_flu[:, 1]
    wx = om_flu[:, 0]
    wy = -om_flu[:, 1]

    # interpolate truth onto flow frame midpoints (integration window center)
    t_mid = t_f - dt / 2.0
    def gi(sig):
        return np.interp(t_mid, t_g, sig)
    vbx_i, vby_i, wx_i, wy_i = gi(vbx), gi(vby), gi(wx), gi(wy)

    # restrict to airborne, moving samples
    air = rng > 1.0
    X = np.stack([vbx_i / rng, vby_i / rng, wx_i, wy_i,
                  np.ones_like(rng)], axis=1)[air]
    yx = fx[air]
    yy = fy[air]

    cx, res_x, *_ = np.linalg.lstsq(X, yx, rcond=None)
    cy, res_y, *_ = np.linalg.lstsq(X, yy, rcond=None)

    def r2(c, y):
        pred = X @ c
        ss_res = np.sum((y - pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")

    out = {
        "run": run_dir.name,
        "n_samples_used": int(air.sum()),
        "regressors": ["vbx/h", "vby/h", "wx", "wy", "bias"],
        "flow_x_coeffs": [round(float(v), 4) for v in cx],
        "flow_x_r2": round(r2(cx, yx), 4),
        "flow_y_coeffs": [round(float(v), 4) for v in cy],
        "flow_y_r2": round(r2(cy, yy), 4),
        "contract": "mavlink_wire_pixel_flow_pre_ekf2_negation",
        "expected_wire_pixel_flow_x": [0.0, -1.0, 1.0, 0.0, 0.0],
        "expected_wire_pixel_flow_y": [1.0, 0.0, 0.0, 1.0, 0.0],
        "expected_ekf_compensated_flow_x": [0.0, 1.0, 0.0, 0.0, 0.0],
        "expected_ekf_compensated_flow_y": [-1.0, 0.0, 0.0, 0.0, 0.0],
        # Backward-compatible aliases. These are wire-side, not final EKF
        # velocity-sign acceptance.
        "expected_px4_flow_x": [0.0, -1.0, 1.0, 0.0, 0.0],
        "expected_px4_flow_y": [1.0, 0.0, 0.0, 1.0, 0.0],
    }
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="+")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    results = [analyze(Path(r)) for r in args.runs]
    text = json.dumps(results, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text)


if __name__ == "__main__":
    main()
