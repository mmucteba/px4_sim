#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


def load_csv(run_dir: Path, name: str) -> pd.DataFrame:
    path = run_dir / "extracted_csv" / name
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def rel_time_s(df: pd.DataFrame) -> pd.Series:
    return (df["timestamp"] - df["timestamp"].iloc[0]) / 1_000_000.0


def horizontal_from_reference(df: pd.DataFrame, ref_index: int = 0) -> pd.Series:
    x0 = df["x"].iloc[ref_index]
    y0 = df["y"].iloc[ref_index]
    return ((df["x"] - x0) ** 2 + (df["y"] - y0) ** 2) ** 0.5


def find_gps_loss_time(gps: pd.DataFrame):
    t = rel_time_s(gps)

    bad = pd.Series(False, index=gps.index)

    if "fix_type" in gps.columns:
        bad = bad | (gps["fix_type"] < 3)

    if "satellites_used" in gps.columns:
        bad = bad | (gps["satellites_used"] <= 0)

    if "eph" in gps.columns:
        bad = bad | (gps["eph"] >= 50)

    if "epv" in gps.columns:
        bad = bad | (gps["epv"] >= 50)

    # Avoid detecting bad startup before the first good GPS.
    good = pd.Series(True, index=gps.index)
    if "fix_type" in gps.columns:
        good = good & (gps["fix_type"] >= 3)
    if "satellites_used" in gps.columns:
        good = good & (gps["satellites_used"] > 0)

    if not good.any():
        return None

    first_good_i = good[good].index[0]
    bad_after_good = bad & (gps.index > first_good_i)

    if not bad_after_good.any():
        return None

    return float(t.loc[bad_after_good[bad_after_good].index[0]])


def local_metrics(run_dir: Path, gps_loss_time_s=None):
    local = load_csv(run_dir, "vehicle_local_position.csv")
    t = rel_time_s(local)
    h_start = horizontal_from_reference(local, 0)

    result = {
        "duration_s": float(t.iloc[-1] - t.iloc[0]),
        "max_horizontal_from_start_m": float(h_start.max()),
        "mean_horizontal_from_start_m": float(h_start.mean()),
        "end_x_m": float(local["x"].iloc[-1]),
        "end_y_m": float(local["y"].iloc[-1]),
        "min_altitude_m": float((-local["z"]).min()),
        "max_altitude_m": float((-local["z"]).max()),
    }

    if gps_loss_time_s is not None:
        idx = int((t - gps_loss_time_s).abs().idxmin())
        h_loss = horizontal_from_reference(local, idx)
        after = t >= gps_loss_time_s

        result.update({
            "gps_loss_time_s": float(gps_loss_time_s),
            "duration_after_gps_loss_s": float(t[after].iloc[-1] - t[after].iloc[0]),
            "max_horizontal_from_gps_loss_m": float(h_loss[after].max()),
            "mean_horizontal_from_gps_loss_m": float(h_loss[after].mean()),
            "end_horizontal_from_gps_loss_m": float(h_loss.iloc[-1]),
        })

    return result


def gps_metrics(run_dir: Path):
    gps = load_csv(run_dir, "vehicle_gps_position.csv")
    result = {}

    for col in ["fix_type", "satellites_used", "eph", "epv", "vel_m_s"]:
        if col in gps.columns:
            result[f"{col}_min"] = float(gps[col].min())
            result[f"{col}_max"] = float(gps[col].max())

    result["gps_loss_time_s"] = find_gps_loss_time(gps)
    return result


def plot_xy_compare(baseline_dir: Path, loss_dir: Path, out_dir: Path):
    b = load_csv(baseline_dir, "vehicle_local_position.csv")
    l = load_csv(loss_dir, "vehicle_local_position.csv")

    plt.figure()
    plt.plot(b["x"], b["y"], label="GNSS ON baseline")
    plt.plot(l["x"], l["y"], label="GNSS loss")
    plt.xlabel("EKF local X [m]")
    plt.ylabel("EKF local Y [m]")
    plt.title("EKF Local XY: GNSS ON vs GNSS Loss")
    plt.axis("equal")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_ekf_xy.png", dpi=150)
    plt.close()


def plot_horizontal_compare(baseline_dir: Path, loss_dir: Path, out_dir: Path):
    b = load_csv(baseline_dir, "vehicle_local_position.csv")
    l = load_csv(loss_dir, "vehicle_local_position.csv")

    tb = rel_time_s(b)
    tl = rel_time_s(l)

    hb = horizontal_from_reference(b)
    hl = horizontal_from_reference(l)

    plt.figure()
    plt.plot(tb, hb, label="GNSS ON baseline")
    plt.plot(tl, hl, label="GNSS loss")
    plt.xlabel("Time [s]")
    plt.ylabel("Horizontal movement from start [m]")
    plt.title("Horizontal EKF Movement")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_horizontal_movement.png", dpi=150)
    plt.close()


def write_markdown(path: Path, summary: dict):
    lines = []
    lines.append("# GNSS ON vs GNSS LOSS Comparison")
    lines.append("")
    lines.append(f"Baseline run: `{summary['baseline_run']}`")
    lines.append(f"GNSS-loss run: `{summary['loss_run']}`")
    lines.append("")
    lines.append("## Key metrics")
    lines.append("")
    lines.append("| Metric | GNSS ON baseline | GNSS loss |")
    lines.append("|---|---:|---:|")

    b = summary["baseline"]["local"]
    l = summary["loss"]["local"]

    rows = [
        ("Duration [s]", b.get("duration_s"), l.get("duration_s")),
        ("Max horizontal from start [m]", b.get("max_horizontal_from_start_m"), l.get("max_horizontal_from_start_m")),
        ("Mean horizontal from start [m]", b.get("mean_horizontal_from_start_m"), l.get("mean_horizontal_from_start_m")),
        ("Max altitude [m]", b.get("max_altitude_m"), l.get("max_altitude_m")),
    ]

    for label, bv, lv in rows:
        lines.append(f"| {label} | {bv:.3f} | {lv:.3f} |")

    lines.append("")
    lines.append("## GNSS-loss detection")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary["loss"]["gps"], indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Important limitation")
    lines.append("")
    lines.append("This comparison uses PX4 EKF local position only. It does not yet compare against Gazebo ground truth. Phase 4 must add ground truth to calculate true position error.")
    lines.append("")

    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run-dir", required=True)
    parser.add_argument("--loss-run-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    baseline_dir = Path(args.baseline_run_dir).resolve()
    loss_dir = Path(args.loss_run_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    loss_gps = gps_metrics(loss_dir)
    baseline_gps = gps_metrics(baseline_dir)

    summary = {
        "baseline_run": str(baseline_dir),
        "loss_run": str(loss_dir),
        "baseline": {
            "gps": baseline_gps,
            "local": local_metrics(baseline_dir, baseline_gps.get("gps_loss_time_s")),
        },
        "loss": {
            "gps": loss_gps,
            "local": local_metrics(loss_dir, loss_gps.get("gps_loss_time_s")),
        },
    }

    (out_dir / "comparison_summary.json").write_text(json.dumps(summary, indent=2))
    write_markdown(out_dir / "comparison_summary.md", summary)

    plot_xy_compare(baseline_dir, loss_dir, out_dir)
    plot_horizontal_compare(baseline_dir, loss_dir, out_dir)

    print(f"OK: comparison written to {out_dir}")
    print(out_dir / "comparison_summary.md")


if __name__ == "__main__":
    main()
