#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCHES_DIR = PROJECT_ROOT / "experiments" / "batches"


def latest_batch() -> Path:
    batches = list({p.parent for p in BATCHES_DIR.glob("*/batch_summary.json")})

    if not batches:
        raise FileNotFoundError("No batch folder with batch_summary.json found.")

    def is_real_batch(path: Path) -> bool:
        try:
            return not bool(json.loads((path / "batch_summary.json").read_text()).get("dry_run"))
        except Exception:
            return False

    real_batches = [path for path in batches if is_real_batch(path)]
    candidates = real_batches or batches
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def get_nested(d: dict, path: list[str], default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", default=None)
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).resolve() if args.batch_dir else latest_batch().resolve()
    summary_path = batch_dir / "batch_summary.json"

    if not summary_path.exists():
        print(f"ERROR: missing {summary_path}")
        return 1

    batch = json.loads(summary_path.read_text())
    rows = []

    for result in batch.get("results", []):
        run_dir_raw = result.get("run_dir")
        run_dir = Path(run_dir_raw) if run_dir_raw else None

        status = {}
        metrics = {}

        if run_dir and (run_dir / "logs" / "pxh_takeoff_land_truth_status.json").exists():
            status = json.loads((run_dir / "logs" / "pxh_takeoff_land_truth_status.json").read_text())

        if run_dir and (run_dir / "ekf_vs_ground_truth_metrics.json").exists():
            metrics = json.loads((run_dir / "ekf_vs_ground_truth_metrics.json").read_text())

        external_odom_enabled = bool(status.get("external_odom_enabled"))

        row = {
            "case": result.get("name"),
            "accepted": result.get("accepted"),
            "run_dir": run_dir_raw,
            "qgc_mavlink_started": status.get("qgc_mavlink_started"),
            "gnss_loss_requested": status.get("gnss_loss_requested"),
            "gnss_loss_after_takeoff_s": status.get("gnss_loss_after_takeoff_s"),
            "post_loss_hover_s": status.get("post_loss_hover_s"),
            "gnss_loss_detected": status.get("gnss_loss_detected"),
            "truth_recorded": status.get("truth_recorded"),
            "ulog_copied": status.get("ulog_copied"),
            "external_odom_ev_ctrl": status.get("external_odom_ev_ctrl") if external_odom_enabled else None,
            "external_odom_rate_hz": status.get("external_odom_rate_hz") if external_odom_enabled else None,
            "external_odom_ev_delay_ms": status.get("external_odom_ev_delay_ms") if external_odom_enabled else None,
            "external_odom_latency_ms": status.get("external_odom_latency_ms") if external_odom_enabled else None,
            "external_odom_position_std_m": status.get("external_odom_position_std_m") if external_odom_enabled else None,
            "external_odom_velocity_std_m_s": status.get("external_odom_velocity_std_m_s") if external_odom_enabled else None,
            "external_odom_inject_position_noise_std_m": status.get("external_odom_inject_position_noise_std_m") if external_odom_enabled else None,
            "external_odom_inject_velocity_noise_std_m_s": status.get("external_odom_inject_velocity_noise_std_m_s") if external_odom_enabled else None,
            "external_odom_dropout_enabled": status.get("external_odom_dropout_enabled") if external_odom_enabled else None,
            "external_odom_dropout_start_after_s": status.get("external_odom_dropout_start_after_s") if external_odom_enabled else None,
            "external_odom_dropout_period_s": status.get("external_odom_dropout_period_s") if external_odom_enabled else None,
            "external_odom_dropout_duration_s": status.get("external_odom_dropout_duration_s") if external_odom_enabled else None,
            "external_odom_dropout_probability": status.get("external_odom_dropout_probability") if external_odom_enabled else None,
            "external_odom_mav_frame": status.get("external_odom_mav_frame") if external_odom_enabled else None,
            "ulog_ev_vel_active_count": status.get("ulog_ev_vel_active_count"),
            "ulog_ev_pos_rejected_count": status.get("ulog_ev_pos_rejected_count"),
            "ulog_ev_hgt_rejected_count": status.get("ulog_ev_hgt_rejected_count"),
            "ulog_ev_vel_rejected_count": status.get("ulog_ev_vel_rejected_count"),
            "ulog_xy_reset_counter_delta": status.get("ulog_xy_reset_counter_delta"),
            "comparison_window": metrics.get("comparison_window"),
            "comparison_end_reason": metrics.get("comparison_end_reason"),
            "land_command_t_rel_s": metrics.get("land_command_t_rel_s"),
            "uncropped_aligned_rows": metrics.get("uncropped_aligned_rows"),
            "cropped_after_comparison_end_rows": metrics.get("cropped_after_comparison_end_rows"),
            "aligned_rows": metrics.get("aligned_rows"),
            "aligned_duration_s": metrics.get("aligned_duration_s"),
            "horizontal_mean_m": get_nested(metrics, ["horizontal_error", "mean_m"]),
            "horizontal_max_m": get_nested(metrics, ["horizontal_error", "max_m"]),
            "horizontal_p95_m": get_nested(metrics, ["horizontal_error", "p95_m"]),
            "horizontal_end_m": get_nested(metrics, ["horizontal_error", "end_m"]),
            "height_abs_mean_m": get_nested(metrics, ["height_abs_error", "mean_m"]),
            "height_abs_max_m": get_nested(metrics, ["height_abs_error", "max_m"]),
            "error_3d_mean_m": get_nested(metrics, ["error_3d", "mean_m"]),
            "error_3d_max_m": get_nested(metrics, ["error_3d", "max_m"]),
            "error_3d_end_m": get_nested(metrics, ["error_3d", "end_m"]),
            "truth_station_end_m": get_nested(
                metrics,
                ["station_keeping", "gazebo_horizontal_displacement_from_start", "end_m"],
            ),
            "truth_station_max_m": get_nested(
                metrics,
                ["station_keeping", "gazebo_horizontal_displacement_from_start", "max_m"],
            ),
        }
        rows.append(row)

    csv_path = batch_dir / "batch_metrics.csv"
    md_path = batch_dir / "batch_metrics.md"

    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Batch Metrics",
        "",
        f"- Batch folder: `{batch_dir}`",
        f"- Cases: `{len(rows)}`",
        "",
        "| Case | Accepted | Window | EV ctrl | EV rate Hz | EV delay ms | Actual latency ms | Inject pos noise m | Inject vel noise m/s | Dropout | EV pos std m | EV vel std m/s | EV frame | EV vel active | EV pos rej | EV vel rej | XY resets | Truth drift end m | Land cmd s | Cropped rows | GNSS loss s | Post-loss hover s | H mean m | H max m | 3D max m | Run |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for r in rows:
        def fmt(v, digits=6):
            if v is None:
                return ""
            if isinstance(v, float):
                return f"{v:.{digits}f}"
            return str(v)

        lines.append(
            "| "
            + " | ".join([
                f"`{r['case']}`",
                f"`{r['accepted']}`",
                fmt(r["comparison_window"]),
                fmt(r["external_odom_ev_ctrl"], 0),
                fmt(r["external_odom_rate_hz"], 1),
                fmt(r["external_odom_ev_delay_ms"], 0),
                fmt(r["external_odom_latency_ms"], 0),
                fmt(r["external_odom_inject_position_noise_std_m"], 3),
                fmt(r["external_odom_inject_velocity_noise_std_m_s"], 3),
                (
                    ""
                    if r["external_odom_dropout_enabled"] is None
                    else (
                        f"{r['external_odom_dropout_enabled']}"
                        f"/{fmt(r['external_odom_dropout_duration_s'], 1)}s"
                        f"@{fmt(r['external_odom_dropout_period_s'], 1)}s"
                        f"/p={fmt(r['external_odom_dropout_probability'], 2)}"
                    )
                ),
                fmt(r["external_odom_position_std_m"], 3),
                fmt(r["external_odom_velocity_std_m_s"], 3),
                fmt(r["external_odom_mav_frame"]),
                fmt(r["ulog_ev_vel_active_count"], 0),
                fmt(r["ulog_ev_pos_rejected_count"], 0),
                fmt(r["ulog_ev_vel_rejected_count"], 0),
                fmt(r["ulog_xy_reset_counter_delta"], 0),
                fmt(r["truth_station_end_m"], 3),
                fmt(r["land_command_t_rel_s"], 3),
                fmt(r["cropped_after_comparison_end_rows"], 0),
                fmt(r["gnss_loss_after_takeoff_s"], 3),
                fmt(r["post_loss_hover_s"], 3),
                fmt(r["horizontal_mean_m"], 6),
                fmt(r["horizontal_max_m"], 6),
                fmt(r["error_3d_max_m"], 6),
                f"`{r['run_dir']}`",
            ])
            + " |"
        )

    lines.append("")
    md_path.write_text("\n".join(lines))

    print("== Batch metrics summary ==")
    print(f"batch_dir={batch_dir}")
    print(f"csv={csv_path}")
    print(f"md={md_path}")
    print(f"rows={len(rows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
