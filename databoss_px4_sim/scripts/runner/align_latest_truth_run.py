#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"


def latest_run() -> Path:
    runs = sorted(RUNS_DIR.glob("*pxh_takeoff_land_truth"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        raise FileNotFoundError("No *pxh_takeoff_land_truth run found.")
    return runs[0]


def read_csv_float_rows(path: Path) -> list[dict]:
    rows = []
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out = {}
            for k, v in row.items():
                if v is None:
                    out[k] = v
                    continue
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
            rows.append(out)
    return rows


def first_crossing_time(rows: list[dict], time_key: str, height_key: str, threshold_m: float) -> float | None:
    for row in rows:
        h = float(row[height_key])
        if h >= threshold_m:
            return float(row[time_key])
    return None


def interp(rows: list[dict], time_key: str, value_key: str, t: float) -> float | None:
    if not rows:
        return None

    if t < float(rows[0][time_key]) or t > float(rows[-1][time_key]):
        return None

    lo = 0
    hi = len(rows) - 1

    while hi - lo > 1:
        mid = (lo + hi) // 2
        if float(rows[mid][time_key]) <= t:
            lo = mid
        else:
            hi = mid

    r0 = rows[lo]
    r1 = rows[hi]
    t0 = float(r0[time_key])
    t1 = float(r1[time_key])

    if t1 == t0:
        return float(r0[value_key])

    a = (t - t0) / (t1 - t0)
    return float(r0[value_key]) * (1 - a) + float(r1[value_key]) * a


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    idx = (len(xs) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return xs[lo]
    a = idx - lo
    return xs[lo] * (1 - a) + xs[hi] * a


def find_land_command_t_rel_s(run_dir: Path, px4_t0_s: float) -> float | None:
    ulog_path = run_dir / "logs" / "flight.ulg"
    if not ulog_path.exists():
        return None

    try:
        from pyulog import ULog

        commands = ULog(str(ulog_path)).get_dataset("vehicle_command").data
        timestamps = commands["timestamp"]
        command_ids = commands["command"]

        for timestamp, command_id in zip(timestamps, command_ids):
            if int(command_id) == 21:
                return float(timestamp) * 1e-6 - px4_t0_s

    except Exception:
        return None

    return None


def resolve_truth_csv(run_dir: Path) -> Path:
    postprocess_json = run_dir / "postprocess_summary.json"
    if postprocess_json.exists():
        try:
            data = json.loads(postprocess_json.read_text())
            truth_csv = data.get("truth", {}).get("truth_csv")
            if truth_csv:
                path = Path(truth_csv)
                if path.exists():
                    return path
        except Exception:
            pass

    legacy = run_dir / "gazebo_truth" / "gazebo_ground_truth_x500_0.csv"
    if legacy.exists():
        return legacy

    candidates = sorted((run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
    if candidates:
        return candidates[0]

    return legacy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--takeoff-threshold-m", type=float, default=0.5)
    parser.add_argument(
        "--comparison-window",
        choices=["until-land-command", "full"],
        default="until-land-command",
        help="Limit EKF/truth comparison to the commanded hover window, ending at commander land by default.",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run().resolve()

    local_csv = run_dir / "extracted_csv" / "vehicle_local_position.csv"
    truth_csv = resolve_truth_csv(run_dir)

    if not local_csv.exists():
        print(f"ERROR: missing {local_csv}")
        return 1

    if not truth_csv.exists():
        print(f"ERROR: missing {truth_csv}")
        return 1

    local = read_csv_float_rows(local_csv)
    truth = read_csv_float_rows(truth_csv)

    if not local or not truth:
        print("ERROR: empty local or truth CSV")
        return 1

    px4_t0 = float(local[0]["timestamp"]) * 1e-6
    px4_x0 = float(local[0]["x"])
    px4_y0 = float(local[0]["y"])
    px4_z0 = float(local[0]["z"])

    gz_t0 = float(truth[0]["sim_time_s"])
    # Gazebo world frame is ENU (x=East, y=North); PX4 local frame is NED
    # (x=North, y=East). Horizontal mapping: NED_x = ENU_y, NED_y = ENU_x.
    # Raw x/y were compared directly before 2026-07-13; hover runs (zero
    # displacement) were unaffected, translation runs showed a fake error
    # equal to the traverse.
    gz_x0 = float(truth[0]["y"])
    gz_y0 = float(truth[0]["x"])
    gz_z0 = float(truth[0]["z"])

    px4_norm = []
    for r in local:
        z_rel = float(r["z"]) - px4_z0
        px4_norm.append({
            "t_rel_s": float(r["timestamp"]) * 1e-6 - px4_t0,
            "x_rel": float(r["x"]) - px4_x0,
            "y_rel": float(r["y"]) - px4_y0,
            "height_up": -z_rel,
            "raw_z": float(r["z"]),
        })

    gz_norm = []
    for r in truth:
        gz_norm.append({
            "t_rel_s": float(r["sim_time_s"]) - gz_t0,
            "x_rel": float(r["y"]) - gz_x0,
            "y_rel": float(r["x"]) - gz_y0,
            "height_up": float(r["z"]) - gz_z0,
            "raw_z": float(r["z"]),
        })

    px4_takeoff_t = first_crossing_time(px4_norm, "t_rel_s", "height_up", args.takeoff_threshold_m)
    gz_takeoff_t = first_crossing_time(gz_norm, "t_rel_s", "height_up", args.takeoff_threshold_m)

    if px4_takeoff_t is None or gz_takeoff_t is None:
        print("ERROR: could not find takeoff crossing for alignment")
        print(f"px4_takeoff_t={px4_takeoff_t}, gz_takeoff_t={gz_takeoff_t}")
        return 1

    # To compare at PX4 relative time t, sample Gazebo at:
    # gz_t = t - px4_takeoff_t + gz_takeoff_t
    aligned_rows = []
    uncropped_aligned_rows = 0
    cropped_after_comparison_end_rows = 0
    horizontal_errors = []
    height_errors = []
    errors_3d = []
    px4_horizontal_displacements = []
    gazebo_horizontal_displacements = []
    station_reference = None
    land_command_t_rel_s = find_land_command_t_rel_s(run_dir, px4_t0)
    comparison_end_t_rel_s = land_command_t_rel_s if args.comparison_window == "until-land-command" else None
    comparison_end_reason = (
        "commander_land_command"
        if comparison_end_t_rel_s is not None
        else ("full_run_requested" if args.comparison_window == "full" else "land_command_not_found")
    )

    for p in px4_norm:
        px4_t = p["t_rel_s"]
        gz_t = px4_t - px4_takeoff_t + gz_takeoff_t

        gx = interp(gz_norm, "t_rel_s", "x_rel", gz_t)
        gy = interp(gz_norm, "t_rel_s", "y_rel", gz_t)
        gh = interp(gz_norm, "t_rel_s", "height_up", gz_t)

        if gx is None or gy is None or gh is None:
            continue

        uncropped_aligned_rows += 1

        if comparison_end_t_rel_s is not None and px4_t > comparison_end_t_rel_s:
            cropped_after_comparison_end_rows += 1
            continue

        if station_reference is None:
            station_reference = {
                "px4_x_rel": p["x_rel"],
                "px4_y_rel": p["y_rel"],
                "gz_x_rel": gx,
                "gz_y_rel": gy,
            }

        ex = p["x_rel"] - gx
        ey = p["y_rel"] - gy
        eh = p["height_up"] - gh

        h_err = math.hypot(ex, ey)
        e3 = math.sqrt(ex * ex + ey * ey + eh * eh)
        px4_displacement = math.hypot(
            p["x_rel"] - station_reference["px4_x_rel"],
            p["y_rel"] - station_reference["px4_y_rel"],
        )
        gazebo_displacement = math.hypot(
            gx - station_reference["gz_x_rel"],
            gy - station_reference["gz_y_rel"],
        )

        horizontal_errors.append(h_err)
        height_errors.append(abs(eh))
        errors_3d.append(e3)
        px4_horizontal_displacements.append(px4_displacement)
        gazebo_horizontal_displacements.append(gazebo_displacement)

        aligned_rows.append({
            "px4_t_rel_s": px4_t,
            "gz_t_rel_s": gz_t,
            "px4_x_rel": p["x_rel"],
            "px4_y_rel": p["y_rel"],
            "px4_height_up": p["height_up"],
            "gz_x_rel": gx,
            "gz_y_rel": gy,
            "gz_height_up": gh,
            "err_x": ex,
            "err_y": ey,
            "err_height": eh,
            "horizontal_error_m": h_err,
            "abs_height_error_m": abs(eh),
            "error_3d_m": e3,
            "px4_horizontal_displacement_from_start_m": px4_displacement,
            "gazebo_horizontal_displacement_from_start_m": gazebo_displacement,
        })

    aligned_csv = run_dir / "ekf_vs_ground_truth_aligned.csv"
    with aligned_csv.open("w", newline="") as f:
        fieldnames = [
            "px4_t_rel_s",
            "gz_t_rel_s",
            "px4_x_rel",
            "px4_y_rel",
            "px4_height_up",
            "gz_x_rel",
            "gz_y_rel",
            "gz_height_up",
            "err_x",
            "err_y",
            "err_height",
            "horizontal_error_m",
            "abs_height_error_m",
            "error_3d_m",
            "px4_horizontal_displacement_from_start_m",
            "gazebo_horizontal_displacement_from_start_m",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(aligned_rows)

    def end_or_nan(values: list[float]) -> float:
        return values[-1] if values else float("nan")

    def stats(values: list[float]) -> dict:
        return {
            "max_m": max(values) if values else None,
            "mean_m": sum(values) / len(values) if values else None,
            "median_m": percentile(values, 0.5) if values else None,
            "p95_m": percentile(values, 0.95) if values else None,
            "end_m": end_or_nan(values),
        }

    comparison_window_ok = args.comparison_window == "full" or comparison_end_t_rel_s is not None

    metrics = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "takeoff_threshold_m": args.takeoff_threshold_m,
        "px4_takeoff_crossing_t_rel_s": px4_takeoff_t,
        "gazebo_takeoff_crossing_t_rel_s": gz_takeoff_t,
        "time_offset_gazebo_minus_px4_s": gz_takeoff_t - px4_takeoff_t,
        "comparison_window": args.comparison_window,
        "comparison_end_reason": comparison_end_reason,
        "land_command_t_rel_s": land_command_t_rel_s,
        "comparison_end_t_rel_s": comparison_end_t_rel_s,
        "comparison_window_ok": comparison_window_ok,
        "uncropped_aligned_rows": uncropped_aligned_rows,
        "cropped_after_comparison_end_rows": cropped_after_comparison_end_rows,
        "aligned_rows": len(aligned_rows),
        "comparison_start_t_rel_s": aligned_rows[0]["px4_t_rel_s"] if aligned_rows else None,
        "comparison_last_t_rel_s": aligned_rows[-1]["px4_t_rel_s"] if aligned_rows else None,
        "aligned_duration_s": aligned_rows[-1]["px4_t_rel_s"] - aligned_rows[0]["px4_t_rel_s"] if aligned_rows else 0.0,
        "horizontal_error": stats(horizontal_errors),
        "height_abs_error": stats(height_errors),
        "error_3d": stats(errors_3d),
        "station_keeping": {
            "reference": "first_aligned_sample_in_comparison_window",
            "px4_horizontal_displacement_from_start": stats(px4_horizontal_displacements),
            "gazebo_horizontal_displacement_from_start": stats(gazebo_horizontal_displacements),
        },
        "accepted": len(aligned_rows) > 0 and comparison_window_ok,
    }

    metrics_json = run_dir / "ekf_vs_ground_truth_metrics.json"
    metrics_json.write_text(json.dumps(metrics, indent=2))

    def fmt(value: float | None, digits: int = 3) -> str:
        if value is None:
            return "None"
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isnan(value_f):
            return "nan"
        return f"{value_f:.{digits}f}"

    metrics_md = run_dir / "ekf_vs_ground_truth_metrics.md"
    metrics_md.write_text(
        "\n".join([
            "# EKF vs Gazebo Ground Truth Metrics",
            "",
            f"- Run: `{run_dir}`",
            f"- Takeoff threshold: `{args.takeoff_threshold_m}` m",
            f"- PX4 takeoff crossing t_rel_s: `{fmt(px4_takeoff_t)}`",
            f"- Gazebo takeoff crossing t_rel_s: `{fmt(gz_takeoff_t)}`",
            f"- Gazebo-minus-PX4 time offset s: `{fmt(metrics['time_offset_gazebo_minus_px4_s'])}`",
            f"- Comparison window: `{metrics['comparison_window']}`",
            f"- Comparison end reason: `{metrics['comparison_end_reason']}`",
            f"- Land command t_rel_s: `{fmt(metrics['land_command_t_rel_s'])}`",
            f"- Comparison end t_rel_s: `{fmt(metrics['comparison_end_t_rel_s'])}`",
            f"- Comparison window OK: `{metrics['comparison_window_ok']}`",
            f"- Uncropped aligned rows: `{metrics['uncropped_aligned_rows']}`",
            f"- Cropped after comparison end rows: `{metrics['cropped_after_comparison_end_rows']}`",
            f"- Aligned rows: `{metrics['aligned_rows']}`",
            f"- Comparison start t_rel_s: `{fmt(metrics['comparison_start_t_rel_s'])}`",
            f"- Comparison last t_rel_s: `{fmt(metrics['comparison_last_t_rel_s'])}`",
            f"- Aligned duration s: `{fmt(metrics['aligned_duration_s'])}`",
            "",
            "## Horizontal error",
            "",
            f"- Max m: `{fmt(metrics['horizontal_error']['max_m'], 6)}`",
            f"- Mean m: `{fmt(metrics['horizontal_error']['mean_m'], 6)}`",
            f"- Median m: `{fmt(metrics['horizontal_error']['median_m'], 6)}`",
            f"- P95 m: `{fmt(metrics['horizontal_error']['p95_m'], 6)}`",
            f"- End m: `{fmt(metrics['horizontal_error']['end_m'], 6)}`",
            "",
            "## Height absolute error",
            "",
            f"- Max m: `{fmt(metrics['height_abs_error']['max_m'], 6)}`",
            f"- Mean m: `{fmt(metrics['height_abs_error']['mean_m'], 6)}`",
            f"- Median m: `{fmt(metrics['height_abs_error']['median_m'], 6)}`",
            f"- P95 m: `{fmt(metrics['height_abs_error']['p95_m'], 6)}`",
            f"- End m: `{fmt(metrics['height_abs_error']['end_m'], 6)}`",
            "",
            "## 3D error",
            "",
            f"- Max m: `{fmt(metrics['error_3d']['max_m'], 6)}`",
            f"- Mean m: `{fmt(metrics['error_3d']['mean_m'], 6)}`",
            f"- Median m: `{fmt(metrics['error_3d']['median_m'], 6)}`",
            f"- P95 m: `{fmt(metrics['error_3d']['p95_m'], 6)}`",
            f"- End m: `{fmt(metrics['error_3d']['end_m'], 6)}`",
            "",
            "## Station keeping displacement",
            "",
            f"- Reference: `{metrics['station_keeping']['reference']}`",
            f"- PX4 displacement max m: `{fmt(metrics['station_keeping']['px4_horizontal_displacement_from_start']['max_m'], 6)}`",
            f"- PX4 displacement end m: `{fmt(metrics['station_keeping']['px4_horizontal_displacement_from_start']['end_m'], 6)}`",
            f"- Gazebo truth displacement max m: `{fmt(metrics['station_keeping']['gazebo_horizontal_displacement_from_start']['max_m'], 6)}`",
            f"- Gazebo truth displacement end m: `{fmt(metrics['station_keeping']['gazebo_horizontal_displacement_from_start']['end_m'], 6)}`",
            "",
            "## Result",
            "",
            "Accepted." if metrics["accepted"] else "Rejected.",
            "",
        ])
    )

    print("== Align result ==")
    print(f"run_dir={run_dir}")
    print(f"comparison_window={metrics['comparison_window']}")
    print(f"comparison_end_reason={metrics['comparison_end_reason']}")
    print(f"land_command_t_rel_s={fmt(metrics['land_command_t_rel_s'])}")
    print(f"comparison_end_t_rel_s={fmt(metrics['comparison_end_t_rel_s'])}")
    print(f"comparison_window_ok={metrics['comparison_window_ok']}")
    print(f"uncropped_aligned_rows={metrics['uncropped_aligned_rows']}")
    print(f"cropped_after_comparison_end_rows={metrics['cropped_after_comparison_end_rows']}")
    print(f"aligned_rows={metrics['aligned_rows']}")
    print(f"aligned_duration_s={fmt(metrics['aligned_duration_s'])}")
    print(f"horizontal_error_mean_m={fmt(metrics['horizontal_error']['mean_m'], 6)}")
    print(f"horizontal_error_max_m={fmt(metrics['horizontal_error']['max_m'], 6)}")
    print(f"height_abs_error_mean_m={fmt(metrics['height_abs_error']['mean_m'], 6)}")
    print(f"error_3d_max_m={fmt(metrics['error_3d']['max_m'], 6)}")
    print(
        "gazebo_station_displacement_end_m="
        f"{fmt(metrics['station_keeping']['gazebo_horizontal_displacement_from_start']['end_m'], 6)}"
    )
    print(f"accepted={metrics['accepted']}")

    return 0 if metrics["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
