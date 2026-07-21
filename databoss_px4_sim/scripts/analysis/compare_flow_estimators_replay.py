#!/usr/bin/env python3
"""Compare DATABOSS flow estimators on the same recorded frames.

Phase 8J Stage B: isolate estimator behavior from PX4/EKF/controller effects.
Reads a run folder containing flow_recording/ and writes a compact comparison
under flow_estimator_comparison/ or a caller-provided output directory.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from databoss_sim.flow import make_estimator
from databoss_sim.flow.velocity import flow_to_ground_velocity


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


def load_truth(run_dir: Path) -> pd.DataFrame:
    truth_dir = run_dir / "gazebo_truth"
    candidates = sorted(truth_dir.glob("gazebo_ground_truth_*.csv"))
    if not candidates:
        return pd.DataFrame()
    truth = pd.read_csv(candidates[0])
    required = {"sim_time_s", "x", "y"}
    if not required.issubset(truth.columns):
        return pd.DataFrame()
    truth = truth.dropna(subset=["sim_time_s", "x", "y"]).drop_duplicates("sim_time_s").sort_values("sim_time_s")
    if len(truth) < 2:
        return pd.DataFrame()
    t = pd.to_numeric(truth["sim_time_s"], errors="coerce").to_numpy()
    x = pd.to_numeric(truth["x"], errors="coerce").to_numpy()
    y = pd.to_numeric(truth["y"], errors="coerce").to_numpy()
    valid = np.isfinite(t) & np.isfinite(x) & np.isfinite(y)
    truth = truth.loc[valid].copy()
    t = t[valid]
    x = x[valid]
    y = y[valid]
    if len(truth) < 2:
        return pd.DataFrame()
    truth["truth_vx_m_s"] = np.gradient(x, t)
    truth["truth_vy_m_s"] = np.gradient(y, t)
    truth["truth_speed_m_s"] = np.hypot(truth["truth_vx_m_s"], truth["truth_vy_m_s"])
    return truth


def load_recording(run_dir: Path) -> tuple[Path, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rec_dir = run_dir / "flow_recording"
    frames = pd.read_csv(rec_dir / "frames_index.csv")
    ranges = pd.read_csv(rec_dir / "rangefinder.csv") if (rec_dir / "rangefinder.csv").exists() else pd.DataFrame()
    truth = load_truth(run_dir)
    return rec_dir, frames, ranges, truth


def nearest_range(ranges: pd.DataFrame, t_s: float) -> float:
    if ranges.empty or "t_sim_s" not in ranges or "range_m" not in ranges:
        return float("nan")
    ts = pd.to_numeric(ranges["t_sim_s"], errors="coerce").to_numpy()
    rs = pd.to_numeric(ranges["range_m"], errors="coerce").to_numpy()
    idx = int(np.searchsorted(ts, t_s))
    idx = min(max(idx, 0), len(rs) - 1)
    return float(rs[idx])


def interp_truth(truth: pd.DataFrame, t_s: float) -> tuple[float, float, float]:
    if truth.empty:
        return float("nan"), float("nan"), float("nan")
    ts = pd.to_numeric(truth["sim_time_s"], errors="coerce").to_numpy()
    vx = pd.to_numeric(truth["truth_vx_m_s"], errors="coerce").to_numpy()
    vy = pd.to_numeric(truth["truth_vy_m_s"], errors="coerce").to_numpy()
    speed = pd.to_numeric(truth["truth_speed_m_s"], errors="coerce").to_numpy()
    if len(ts) < 2 or not (ts[0] <= t_s <= ts[-1]):
        return float("nan"), float("nan"), float("nan")
    return (
        float(np.interp(t_s, ts, vx)),
        float(np.interp(t_s, ts, vy)),
        float(np.interp(t_s, ts, speed)),
    )


def run_estimator(
    name: str,
    rec_dir: Path,
    frames: pd.DataFrame,
    ranges: pd.DataFrame,
    truth: pd.DataFrame,
    *,
    hfov_rad: float,
    max_width: int | None,
    estimator_kwargs: dict,
) -> tuple[pd.DataFrame, dict]:
    first = cv2.imread(str(rec_dir / "frames" / frames.iloc[0]["frame_path"]), cv2.IMREAD_GRAYSCALE)
    if first is None:
        raise RuntimeError("first recorded frame could not be read")
    width = first.shape[1]
    if max_width and width > max_width:
        width = int(max_width)
    focal_px = (width / 2.0) / math.tan(hfov_rad / 2.0)
    estimator = make_estimator(name, focal_px=focal_px, **estimator_kwargs)

    rows = []
    compute = []
    for _, row in frames.iterrows():
        gray = cv2.imread(str(rec_dir / "frames" / row["frame_path"]), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        if max_width and gray.shape[1] > max_width:
            scale = max_width / gray.shape[1]
            gray = cv2.resize(gray, (max_width, int(gray.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        t_sim = float(row["t_sim_s"])
        t0 = time.monotonic()
        sample = estimator.update(gray, t_sim)
        compute_s = time.monotonic() - t0
        if sample is None:
            continue
        compute.append(compute_s)
        dist = nearest_range(ranges, sample.t_s)
        vel = flow_to_ground_velocity(sample, dist) if math.isfinite(dist) else None
        truth_vx, truth_vy, truth_speed = interp_truth(truth, sample.t_s)
        flow_speed = float(np.hypot(vel[0], vel[1])) if vel else np.nan
        speed_error = flow_speed - truth_speed if math.isfinite(flow_speed) and math.isfinite(truth_speed) else np.nan
        rows.append({
            "estimator": name,
            "t_sim_s": sample.t_s,
            "integration_dt_s": sample.integration_dt_s,
            "integrated_x_rad": sample.integrated_x_rad,
            "integrated_y_rad": sample.integrated_y_rad,
            "quality": sample.quality,
            "n_matches": sample.n_matches,
            "range_m": dist,
            "flow_vx_m_s": vel[0] if vel else np.nan,
            "flow_vy_m_s": vel[1] if vel else np.nan,
            "flow_speed_m_s": flow_speed,
            "truth_vx_m_s": truth_vx,
            "truth_vy_m_s": truth_vy,
            "truth_speed_m_s": truth_speed,
            "speed_error_m_s": speed_error,
            "speed_abs_error_m_s": abs(speed_error) if math.isfinite(speed_error) else np.nan,
            "compute_s": compute_s,
        })

    df = pd.DataFrame(rows)
    quality = pd.to_numeric(df.get("quality", pd.Series(dtype=float)), errors="coerce") if len(df) else pd.Series(dtype=float)
    valid = quality > 0
    speed_abs_error = pd.to_numeric(df.get("speed_abs_error_m_s", pd.Series(dtype=float)), errors="coerce")
    speed_error = pd.to_numeric(df.get("speed_error_m_s", pd.Series(dtype=float)), errors="coerce")
    matched_speed_error = speed_abs_error[np.isfinite(speed_abs_error)]
    summary = {
        "estimator": name,
        "frames": int(len(frames)),
        "samples": int(len(df)),
        "valid_samples": int(valid.sum()) if len(df) else 0,
        "valid_fraction": float(valid.mean()) if len(df) else 0.0,
        "quality_mean": float(quality.mean()) if len(df) else 0.0,
        "quality_p50": float(quality.quantile(0.5)) if len(df) else 0.0,
        "quality_p95": float(quality.quantile(0.95)) if len(df) else 0.0,
        "n_matches_mean": float(pd.to_numeric(df.get("n_matches", pd.Series(dtype=float)), errors="coerce").mean()) if len(df) else 0.0,
        "compute_s_mean": float(np.mean(compute)) if compute else 0.0,
        "compute_s_p95": float(np.percentile(compute, 95)) if compute else 0.0,
        "truth_speed_matched_samples": int(len(matched_speed_error)),
        "speed_abs_error_mean_m_s": float(matched_speed_error.mean()) if len(matched_speed_error) else None,
        "speed_abs_error_p95_m_s": float(matched_speed_error.quantile(0.95)) if len(matched_speed_error) else None,
        "speed_error_bias_mean_m_s": float(speed_error[np.isfinite(speed_error)].mean()) if np.isfinite(speed_error).any() else None,
    }
    if len(df) > 1:
        t = pd.to_numeric(df["t_sim_s"], errors="coerce").dropna()
        summary["sample_rate_hz"] = float((len(t) - 1) / (t.iloc[-1] - t.iloc[0])) if len(t) > 1 and t.iloc[-1] > t.iloc[0] else None
    else:
        summary["sample_rate_hz"] = None
    return df, summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--estimators", nargs="+", default=["sift", "lk"])
    ap.add_argument("--hfov-rad", type=float, default=1.74)
    ap.add_argument("--max-width", type=int, default=320)
    ap.add_argument("--sift-n-features", type=int, default=180)
    ap.add_argument("--sift-ratio", type=float, default=0.75)
    ap.add_argument("--sift-min-matches", type=int, default=8)
    ap.add_argument("--lk-max-corners", type=int, default=160)
    ap.add_argument("--lk-quality-level", type=float, default=0.01)
    ap.add_argument("--lk-min-distance", type=float, default=6.0)
    ap.add_argument("--lk-min-tracks", type=int, default=8)
    ap.add_argument("--out-dir", help="Optional output directory; defaults to <run_dir>/flow_estimator_comparison")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    rec_dir, frames, ranges, truth = load_recording(run_dir)
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else run_dir / "flow_estimator_comparison"
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    all_rows = []
    for name in args.estimators:
        kwargs = {}
        if name == "sift":
            kwargs = {
                "n_features": args.sift_n_features,
                "ratio": args.sift_ratio,
                "min_matches": args.sift_min_matches,
            }
        elif name == "lk":
            kwargs = {
                "max_corners": args.lk_max_corners,
                "quality_level": args.lk_quality_level,
                "min_distance": args.lk_min_distance,
                "min_tracks": args.lk_min_tracks,
            }
        df, summary = run_estimator(
            name,
            rec_dir,
            frames,
            ranges,
            truth,
            hfov_rad=args.hfov_rad,
            max_width=args.max_width,
            estimator_kwargs=kwargs,
        )
        df.to_csv(out_dir / f"{name}_flow_samples.csv", index=False)
        summaries.append(summary)
        all_rows.append(df)

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(out_dir / "summary.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    (out_dir / "summary.md").write_text(
        "# Phase 8J Estimator Replay Comparison\n\n"
        + df_to_markdown(summary_df)
        + "\n\n"
        "This compares estimator output on identical recorded frames only; it does not prove EKF fusion or flight stability.\n"
    )
    if all_rows:
        pd.concat(all_rows, ignore_index=True).to_csv(out_dir / "all_flow_samples.csv", index=False)

    print(summary_df.to_string(index=False))
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
