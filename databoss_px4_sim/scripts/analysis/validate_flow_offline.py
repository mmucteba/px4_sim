#!/usr/bin/env python3
"""Phase 8F offline flow validation: run a FlowEstimator over a recorded
flight, derive ground velocity, compare against Gazebo truth velocity.

Usage: validate_flow_offline.py <run_dir> [--estimator sift] [--hfov-rad 1.74]
       [--native-width 1280]

Reads:  <run_dir>/flow_recording/{frames/, frames_index.csv, rangefinder.csv}
        <run_dir>/ekf_vs_ground_truth_aligned.csv   (truth positions on PX4 time)
Writes: <run_dir>/flow_validation/{flow_samples.csv, summary.json, summary.md}
        <run_dir>/plots/flow_speed_vs_truth.png
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from databoss_sim.flow import make_estimator
from databoss_sim.flow.velocity import flow_to_ground_velocity

C_FLOW = "#2a78d6"
C_TRUTH = "#1baf7a"
C_QUALITY = "#eda100"
INK = "#333333"
INK_MUTED = "#767676"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir")
    parser.add_argument("--estimator", default="sift")
    parser.add_argument("--hfov-rad", type=float, default=1.74)
    parser.add_argument("--native-width", type=int, default=1280)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    rec_dir = run_dir / "flow_recording"
    out_dir = run_dir / "flow_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = pd.read_csv(rec_dir / "frames_index.csv")
    ranges = pd.read_csv(rec_dir / "rangefinder.csv")
    if len(frames) < 2:
        print("ERROR: fewer than 2 recorded frames", file=sys.stderr)
        return 1

    first = cv2.imread(str(rec_dir / "frames" / frames.iloc[0]["frame_path"]), cv2.IMREAD_GRAYSCALE)
    rec_width = first.shape[1]
    focal_px = (rec_width / 2.0) / math.tan(args.hfov_rad / 2.0)
    estimator = make_estimator(args.estimator, focal_px=focal_px)
    print(f"estimator={args.estimator}, recorded width={rec_width}, focal={focal_px:.1f} px")

    range_t = ranges["t_sim_s"].to_numpy()
    range_m = ranges["range_m"].to_numpy()

    rows = []
    for _, row in frames.iterrows():
        gray = cv2.imread(str(rec_dir / "frames" / row["frame_path"]), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        sample = estimator.update(gray, float(row["t_sim_s"]))
        if sample is None:
            continue
        idx = np.searchsorted(range_t, sample.t_s)
        idx = min(max(idx, 0), len(range_m) - 1)
        dist = float(range_m[idx])
        vel = flow_to_ground_velocity(sample, dist)
        rows.append({
            "t_sim_s": sample.t_s,
            "integrated_x_rad": sample.integrated_x_rad,
            "integrated_y_rad": sample.integrated_y_rad,
            "integration_dt_s": sample.integration_dt_s,
            "quality": sample.quality,
            "n_matches": sample.n_matches,
            "range_m": dist,
            "flow_vx_m_s": vel[0] if vel else np.nan,
            "flow_vy_m_s": vel[1] if vel else np.nan,
        })

    flow_df = pd.DataFrame(rows)
    flow_df.to_csv(out_dir / "flow_samples.csv", index=False)

    # Truth velocity from aligned truth positions (gz_x_rel/gz_y_rel on gz time,
    # mapped to sim time via gz_t_rel_s offset from the recording clock).
    aligned = pd.read_csv(run_dir / "ekf_vs_ground_truth_aligned.csv")
    gz_t = aligned["gz_t_rel_s"].to_numpy()
    vx_truth = np.gradient(aligned["gz_x_rel"].to_numpy(), gz_t)
    vy_truth = np.gradient(aligned["gz_y_rel"].to_numpy(), gz_t)
    speed_truth = np.hypot(vx_truth, vy_truth)

    # The recorder's sim clock and gz_t_rel share the same rate; align zero by
    # matching flight windows: use the first frame time as recording start and
    # gz_t_rel already starts near sim-start. Compare on overlapping window.
    flow_t = flow_df["t_sim_s"].to_numpy()
    flow_speed = np.hypot(flow_df["flow_vx_m_s"], flow_df["flow_vy_m_s"])
    valid = flow_df["quality"].to_numpy() > 0

    # gz truth t=0 corresponds to first truth sample; the recorder timestamps are
    # absolute sim seconds. Recover the offset by aligning the truth window start
    # to the earliest gz sample's absolute sim time, which equals the truth
    # recorder start ~ first frame time minus px4 boot offset. Empirically the
    # dominant term: both series cover the flight; use overlap after shifting
    # gz_t so its window midpoint matches the flow window midpoint.
    if len(flow_t):
        shift = (flow_t.min() + flow_t.max()) / 2 - (gz_t.min() + gz_t.max()) / 2
        gz_t_shifted = gz_t + shift
    else:
        gz_t_shifted = gz_t

    truth_on_flow = np.interp(flow_t, gz_t_shifted, speed_truth)
    err = np.abs(flow_speed - truth_on_flow)[valid]

    summary = {
        "estimator": args.estimator,
        "frames": int(len(frames)),
        "flow_samples": int(len(flow_df)),
        "valid_samples": int(valid.sum()),
        "valid_fraction": float(valid.mean()) if len(flow_df) else 0.0,
        "quality_mean": float(flow_df["quality"].mean()) if len(flow_df) else 0.0,
        "n_matches_mean": float(flow_df["n_matches"].mean()) if len(flow_df) else 0.0,
        "speed_err_mean_m_s": float(np.mean(err)) if len(err) else None,
        "speed_err_median_m_s": float(np.median(err)) if len(err) else None,
        "speed_err_p95_m_s": float(np.percentile(err, 95)) if len(err) else None,
        "time_shift_applied_s": float(shift) if len(flow_t) else None,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "summary.md").write_text(
        "# Offline Flow Validation\n\n"
        + "\n".join(f"- {k}: `{v}`" for k, v in summary.items())
        + "\n"
    )

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), dpi=150, sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(gz_t_shifted, speed_truth, color=C_TRUTH, linewidth=2, label="Gazebo truth speed")
    ax1.plot(flow_t[valid], flow_speed[valid], color=C_FLOW, linewidth=2,
             label=f"Flow-derived speed ({args.estimator})")
    ax1.set_ylabel("Ground speed (m/s)", color=INK)
    ax1.set_title(f"Optical-flow speed vs Gazebo truth — {run_dir.name}",
                  color=INK, fontsize=12, loc="left")
    ax1.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK)
    ax2.plot(flow_t, flow_df["quality"], color=C_QUALITY, linewidth=2, label="Flow quality (0-255)")
    ax2.set_ylabel("Quality", color=INK)
    ax2.set_xlabel("Sim time (s)", color=INK)
    ax2.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK)
    for ax in (ax1, ax2):
        ax.grid(True, color="#dddddd", linewidth=0.6)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.tick_params(colors=INK_MUTED)
    fig.tight_layout()
    plots_dir = run_dir / "plots"
    plots_dir.mkdir(exist_ok=True)
    fig.savefig(plots_dir / "flow_speed_vs_truth.png")

    print(json.dumps(summary, indent=2))
    print(f"plot: {plots_dir / 'flow_speed_vs_truth.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
