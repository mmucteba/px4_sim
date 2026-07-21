#!/usr/bin/env python3
"""Phase 8G.2 open-loop bridge analysis: sent-CSV vs ULog sensor_optical_flow.

Usage: analyze_flow_bridge_openloop.py <run_dir> [--expect none|vx|vy]
                                       [--json out.json]

Checks (EKF2_OF_CTRL=0 runs):
- delivery: bridge-sent samples that appear in the ULog (needs the
  HIGH_RATE_SENSORS logger profile; with the default 1 Hz cap only a
  delivery *spot check* is possible and delivery_ok is not gated).
- latency: ULog timestamp_sample (arrival, lockstep hrt ~= sim time) minus
  frame sim time, per value-matched sample -> EKF2_OF_DELAY candidate.
- sign gate (--expect vx|vy): MAVLink wire convention for a zero-yaw local
  leg. Motion along +X body induces POSITIVE integrated_y; motion along +Y
  body induces NEGATIVE integrated_x. This proves bridge-to-wire sign only;
  final acceptance must also run check_flow_velocity_sign.py, which checks
  PX4's estimator_optical_flow_vel after EKF2's internal negation.
- magnitude: |flow rate| vs v/AGL from truth + sent range, evaluated only on
  the steady part of the leg: the longest contiguous truth window above the
  speed threshold, trimmed by --leg-trim-s at both ends. The raw gazebo truth
  CSV shares the gz sim-time base with the sent CSV, so samples are matched
  by time (the earlier |flow|>median selection let takeoff/acceleration
  transients poison the mean: legx 2026-07-13 read 1.35x on a 1.00x plateau).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyulog import ULog


def match_sent_to_ulog(sent: pd.DataFrame, fx: np.ndarray, fy: np.ndarray,
                       ft_us: np.ndarray) -> pd.DataFrame:
    """Two-pointer value match of sent samples to ULog rows (order-preserving)."""
    rows = []
    j = 0
    for _, s in sent.iterrows():
        sx, sy = s.integrated_x_sent, s.integrated_y_sent
        for k in range(j, min(j + 50, len(fx))):
            if abs(fx[k] - sx) < 1e-6 and abs(fy[k] - sy) < 1e-6:
                rows.append({
                    "t_frame_sim_s": s.t_frame_sim_s,
                    "t_ulog_s": ft_us[k] / 1e6,
                    "latency_s": ft_us[k] / 1e6 - s.t_frame_sim_s,
                    "integration_dt_s": s.integration_dt_s,
                    "quality_sent": s.quality_sent,
                    "range_m": s.range_m,
                    "ix": sx, "iy": sy,
                })
                j = k + 1
                break
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir")
    parser.add_argument("--expect", choices=["none", "vx", "vy"], default="none")
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--speed-threshold-m-s", type=float, default=0.2)
    parser.add_argument("--leg-trim-s", type=float, default=2.5)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    sent = pd.read_csv(run_dir / "flow_bridge" / "flow_bridge_sent.csv")
    # quality-0 samples carry integrated (0,0), which value-matches ANY other
    # zero row -> bogus multi-second latencies. Match only informative samples.
    sent = sent[(sent.sent == 1) & (sent.quality_sent > 0)].reset_index(drop=True)
    ulog = ULog(str(run_dir / "logs" / "flight.ulg"))
    flow = ulog.get_dataset("sensor_optical_flow").data
    ft_us = np.asarray(flow["timestamp_sample"], dtype=float)
    fx = np.asarray(flow["pixel_flow[0]"], dtype=float)
    fy = np.asarray(flow["pixel_flow[1]"], dtype=float)

    out: dict = {
        "run_dir": str(run_dir),
        "contract": "mavlink_wire_pixel_flow_pre_ekf2_negation",
        "final_sign_authority": "scripts/analysis/check_flow_velocity_sign.py",
        "sent_rows": int(len(sent)),
        "ulog_flow_rows": int(len(ft_us)),
        "full_rate_logged": bool(len(ft_us) > 0.5 * len(sent)),
    }

    m = match_sent_to_ulog(sent, fx, fy, ft_us)
    out["matched_rows"] = int(len(m))
    if out["full_rate_logged"]:
        out["delivery_fraction"] = round(len(m) / max(len(sent), 1), 4)
        out["delivery_ok"] = out["delivery_fraction"] > 0.90
    else:
        # 1 Hz-capped log: every logged row should still value-match a sent one.
        out["delivery_fraction"] = None
        out["delivery_ok"] = len(m) > 0

    if len(m):
        lat = m.latency_s.to_numpy()
        out["latency_median_s"] = round(float(np.median(lat)), 4)
        out["latency_p90_s"] = round(float(np.quantile(lat, 0.9)), 4)
        dt_med = float(m.integration_dt_s.median())
        out["ekf2_of_delay_candidate_ms"] = round(
            (float(np.median(lat)) + dt_med / 2) * 1000.0, 1)

    # Sign/magnitude gate against truth
    if args.expect != "none":
        truth_files = sorted((run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
        if not truth_files:
            raise FileNotFoundError(f"no gazebo_ground_truth_*.csv under {run_dir}/gazebo_truth")
        truth = pd.read_csv(truth_files[0])
        tt = truth["sim_time_s"].to_numpy()
        # ENU->NED: NED x (body +X at zero yaw) = ENU y, NED y = ENU x
        # (docs/architecture/frames_and_alignment.md).
        vx = np.gradient(truth["y"].to_numpy(), tt)
        vy = np.gradient(truth["x"].to_numpy(), tt)
        # Light smoothing so per-sample gradient noise cannot split the leg.
        win = max(1, int(0.5 / max(np.median(np.diff(tt)), 1e-3)))
        kernel = np.ones(win) / win
        speed = np.convolve(np.hypot(vx, vy), kernel, mode="same")
        fast = speed > args.speed_threshold_m_s

        # Longest contiguous above-threshold window, trimmed at both ends to
        # exclude the acceleration/deceleration transients.
        best_len, best, start = 0, None, None
        for i, f in enumerate(np.append(fast, False)):
            if f and start is None:
                start = i
            elif not f and start is not None:
                if i - start > best_len:
                    best_len, best = i - start, (start, i)
                start = None
        if best is None:
            raise ValueError("no truth window above the speed threshold; is this a leg run?")
        t_lo = tt[best[0]] + args.leg_trim_s
        t_hi = tt[best[1] - 1] - args.leg_trim_s
        if t_hi - t_lo < 5.0:
            raise ValueError(f"steady leg window too short: [{t_lo:.1f}, {t_hi:.1f}] s")
        out["leg_window_sim_s"] = [round(float(t_lo), 2), round(float(t_hi), 2)]

        leg = (tt >= t_lo) & (tt <= t_hi)
        out["truth_leg_samples"] = int(leg.sum())
        v_mean = {"vx": float(vx[leg].mean()), "vy": float(vy[leg].mean())}
        out["truth_leg_velocity_mean"] = {k: round(v, 3) for k, v in v_mean.items()}

        moving = (m.t_frame_sim_s >= t_lo) & (m.t_frame_sim_s <= t_hi)
        out["flow_leg_samples"] = int(moving.sum())
        if not moving.any():
            raise ValueError("no matched flow samples inside the steady leg window")
        rate_x = m.ix / m.integration_dt_s
        rate_y = m.iy / m.integration_dt_s
        out["flow_rate_x_mean"] = round(float(rate_x[moving].mean()), 4)
        out["flow_rate_y_mean"] = round(float(rate_y[moving].mean()), 4)

        # Rangefinder drops out to inf/nan on some frames; median over the raw
        # column can land on inf and silently zero the expected rate. Use only
        # finite positive readings inside the leg window.
        rng = m.range_m[moving].to_numpy(dtype=float)
        finite = np.isfinite(rng) & (rng > 0)
        out["agl_finite_fraction"] = round(float(finite.mean()), 3) if len(rng) else 0.0
        if finite.sum() < 3:
            raise ValueError(
                f"insufficient finite rangefinder readings in leg window "
                f"({int(finite.sum())} of {len(rng)})")
        agl = float(np.median(rng[finite]))
        out["agl_median_m"] = round(agl, 3)
        v_leg = abs(v_mean[args.expect])
        expected_rate = v_leg / agl if agl > 0 else float("nan")
        out["expected_flow_rate_mag"] = round(expected_rate, 4)

        if args.expect == "vx":
            # Wire convention: +body-X motion -> positive integrated_y.
            dominant, cross = out["flow_rate_y_mean"], out["flow_rate_x_mean"]
            sign_ok = dominant > 0
        else:
            # Wire convention: +body-Y motion -> negative integrated_x.
            dominant, cross = out["flow_rate_x_mean"], out["flow_rate_y_mean"]
            sign_ok = dominant < 0
        out["dominant_axis_rate"] = dominant
        out["cross_axis_rate"] = cross
        out["axis_dominance_ok"] = abs(dominant) > 2 * abs(cross)
        out["magnitude_ratio"] = round(abs(dominant) / expected_rate, 3) if expected_rate else None
        out["magnitude_ok"] = bool(out["magnitude_ratio"] and 0.7 <= out["magnitude_ratio"] <= 1.3)
        out["sign_ok"] = bool(sign_ok)

    for k, v in out.items():
        print(f"{k}={v}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {args.json_out}")

    ok = out["delivery_ok"] and (args.expect == "none"
                                 or (out["sign_ok"] and out["axis_dominance_ok"] and out["magnitude_ok"]))
    print(f"openloop_ok={ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
