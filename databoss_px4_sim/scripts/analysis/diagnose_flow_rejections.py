#!/usr/bin/env python3
"""Diagnose *why* EKF2 rejects optical-flow samples in a Phase 8I Case D run.

Phase 8I D-repair (2026-07-15): the strict green gate
(`analyze_flow_fusion_ulog.py`) fails only on `flow_rejected/fused < 0.10`.
This tool classifies the rejected samples so the fix targets the real cause
(message timing / EKF2_OF_DELAY / post-loss transient) instead of broad gates.

Reads a run dir (or a .ulg) and, when present, the sibling
`flow_bridge/flow_bridge_sent.csv` and `end_to_end_status.json`.

Outputs, all referenced to the GNSS-loss instant:
- GNSS-loss time (satellites_used -> 0) and takeoff time.
- estimator_aid_src_optical_flow: fused / rejected counts, test_ratio stats,
  effective applied OF delay = (timestamp - timestamp_sample).
- vehicle_local_position.xy_reset_counter: delta + reset times.
- Reject classification:
    (a) post-loss transient  -> within [loss, loss+transient_s] or near a reset
    (b) systematic           -> steady-state rejects spread across the outage
    (c) compute-latency       -> steady rejects vs bridge compute_s / send-rate
- Steady-state rejected/fused ratio with the transient excluded (what the
  gate would read if the settling window were not counted).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from pyulog import ULog


def dataset(ulog: ULog, name: str):
    try:
        return ulog.get_dataset(name).data
    except (KeyError, IndexError, ValueError):
        return None


def rel_seconds(ts_us: np.ndarray, t0_us: float) -> np.ndarray:
    return (np.asarray(ts_us, dtype=float) - t0_us) / 1e6


def find_gnss_loss_t(ulog: ULog, t0_us: float) -> float | None:
    for topic in ("vehicle_gps_position", "sensor_gps"):
        gps = dataset(ulog, topic)
        if gps is None or "satellites_used" not in gps:
            continue
        t = np.asarray(gps["timestamp"], dtype=float)
        sats = np.asarray(gps["satellites_used"], dtype=float)
        # first sample where sats drops to 0 after having been > 0
        had_fix = sats > 0
        if not had_fix.any():
            continue
        first_fix_i = int(np.argmax(had_fix))
        lost = np.where((sats[first_fix_i:] == 0))[0]
        if len(lost):
            return float((t[first_fix_i + lost[0]] - t0_us) / 1e6)
    return None


def load_bridge_csv(run_dir: Path) -> list[dict]:
    csv_path = run_dir / "flow_bridge" / "flow_bridge_sent.csv"
    if not csv_path.exists():
        return []
    with csv_path.open() as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="run dir or flight.ulg")
    ap.add_argument("--transient-s", type=float, default=2.0,
                    help="post-loss settling window counted as transient (s)")
    ap.add_argument("--reset-guard-s", type=float, default=0.5,
                    help="+/- window around an xy_reset counted as transient (s)")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    path = Path(args.path).resolve()
    run_dir = path if path.is_dir() else path.parent.parent
    ulg = path / "logs" / "flight.ulg" if path.is_dir() else path
    if not ulg.exists():
        print(f"ERROR: {ulg} not found", file=sys.stderr)
        return 1

    ulog = ULog(str(ulg))
    out: dict = {"ulog": str(ulg)}

    vlp = dataset(ulog, "vehicle_local_position")
    aid = dataset(ulog, "estimator_aid_src_optical_flow")
    if vlp is None or aid is None:
        print("ERROR: missing vehicle_local_position or aid_src_optical_flow", file=sys.stderr)
        return 1

    t0_us = float(min(vlp["timestamp"][0], aid["timestamp"][0]))

    # --- GNSS loss + takeoff reference ---
    loss_t = find_gnss_loss_t(ulog, t0_us)
    status_p = run_dir / "end_to_end_status.json"
    status = json.loads(status_p.read_text()) if status_p.exists() else {}
    out["gnss_loss_t_rel_s"] = round(loss_t, 3) if loss_t is not None else None
    out["gnss_loss_after_takeoff_s_cfg"] = status.get("gnss_loss_after_takeoff_s")

    # --- xy resets ---
    rc = np.asarray(vlp["xy_reset_counter"], dtype=int)
    vlp_t = rel_seconds(vlp["timestamp"], t0_us)
    reset_idx = np.where(np.diff(rc) != 0)[0] + 1
    reset_times = vlp_t[reset_idx].tolist()
    out["xy_reset_counter_delta"] = int(rc[-1] - rc[0])
    out["xy_reset_times_rel_s"] = [round(x, 3) for x in reset_times]

    # --- aid-source optical flow ---
    aid_t = rel_seconds(aid["timestamp"], t0_us)
    aid_ts_sample = np.asarray(aid["timestamp_sample"], dtype=float)
    applied_delay_ms = (np.asarray(aid["timestamp"], dtype=float) - aid_ts_sample) / 1e3
    fused = np.asarray(aid["fused"], dtype=int).astype(bool)
    rejected = np.asarray(aid["innovation_rejected"], dtype=int).astype(bool)
    tr0 = np.asarray(aid["test_ratio[0]"], dtype=float)
    tr1 = np.asarray(aid["test_ratio[1]"], dtype=float)
    inn0 = np.asarray(aid["innovation[0]"], dtype=float)
    inn1 = np.asarray(aid["innovation[1]"], dtype=float)

    n_fused = int(fused.sum())
    n_rej = int(rejected.sum())
    out["aid_rows"] = int(len(aid_t))
    out["flow_fused_count"] = n_fused
    out["flow_rejected_count"] = n_rej
    out["flow_rejected_over_fused"] = round(n_rej / n_fused, 4) if n_fused else None
    out["applied_of_delay_ms_mean"] = round(float(np.mean(applied_delay_ms)), 2)
    out["applied_of_delay_ms_std"] = round(float(np.std(applied_delay_ms)), 2)
    out["applied_of_delay_ms_p95"] = round(float(np.percentile(applied_delay_ms, 95)), 2)

    # transient mask: post-loss settling OR near a reset
    transient = np.zeros(len(aid_t), dtype=bool)
    if loss_t is not None:
        transient |= (aid_t >= loss_t) & (aid_t <= loss_t + args.transient_s)
    for rt in reset_times:
        transient |= np.abs(aid_t - rt) <= args.reset_guard_s

    rej_transient = int((rejected & transient).sum())
    rej_steady = int((rejected & ~transient).sum())
    fused_steady = int((fused & ~transient).sum())
    out["rejected_transient"] = rej_transient
    out["rejected_steady"] = rej_steady
    out["steady_rejected_over_fused"] = (
        round(rej_steady / fused_steady, 4) if fused_steady else None
    )

    # innovation bias on rejected vs fused (systematic-timing signature)
    def bias(mask):
        if not mask.any():
            return None
        return [round(float(np.mean(inn0[mask])), 5), round(float(np.mean(inn1[mask])), 5)]
    out["innovation_mean_rejected"] = bias(rejected)
    out["innovation_mean_fused"] = bias(fused)
    out["test_ratio_p95_all"] = [round(float(np.percentile(tr0, 95)), 3),
                                 round(float(np.percentile(tr1, 95)), 3)]
    if rejected.any():
        out["test_ratio_median_rejected"] = [round(float(np.median(tr0[rejected])), 3),
                                             round(float(np.median(tr1[rejected])), 3)]

    # --- bridge compute / rate ---
    rows = load_bridge_csv(run_dir)
    if rows:
        comp = np.array([float(r["compute_s"]) for r in rows if r.get("compute_s")])
        twall = np.array([float(r["t_wall_s"]) for r in rows if r.get("t_wall_s")])
        tsim = np.array([float(r["t_frame_sim_s"]) for r in rows if r.get("t_frame_sim_s")])
        mavlink_sent = np.array([int(r.get("mavlink_sent", 0)) for r in rows])
        out["bridge_rows"] = len(rows)
        out["bridge_compute_s_mean"] = round(float(comp.mean()), 4)
        out["bridge_compute_s_p95"] = round(float(np.percentile(comp, 95)), 4)
        out["bridge_compute_s_max"] = round(float(comp.max()), 4)
        if len(tsim) > 1:
            out["bridge_sim_rate_hz"] = round(float((len(tsim) - 1) / (tsim[-1] - tsim[0])), 2)
        if len(twall) > 1:
            out["bridge_wall_rate_hz"] = round(float((len(twall) - 1) / (twall[-1] - twall[0])), 2)
        out["bridge_mavlink_sent"] = int(mavlink_sent.sum())

    # ---- verdict heuristic ----
    dominant = "unknown"
    if n_rej:
        frac_transient = rej_transient / n_rej
        if frac_transient >= 0.6:
            dominant = "(a) post-loss/reset transient"
        elif out.get("steady_rejected_over_fused") and out["steady_rejected_over_fused"] >= 0.10:
            im = out["innovation_mean_rejected"] or [0, 0]
            if max(abs(im[0]), abs(im[1])) > 0.02:
                dominant = "(b) systematic timing/frame bias"
            else:
                dominant = "(b/c) steady rejects, low innovation bias -> latency/rate"
    out["dominant_reject_class"] = dominant

    # ---- print ----
    print(f"# Flow-rejection diagnosis: {ulg}")
    order = [
        "gnss_loss_t_rel_s", "gnss_loss_after_takeoff_s_cfg",
        "aid_rows", "flow_fused_count", "flow_rejected_count",
        "flow_rejected_over_fused", "steady_rejected_over_fused",
        "rejected_transient", "rejected_steady",
        "xy_reset_counter_delta", "xy_reset_times_rel_s",
        "applied_of_delay_ms_mean", "applied_of_delay_ms_std", "applied_of_delay_ms_p95",
        "innovation_mean_rejected", "innovation_mean_fused",
        "test_ratio_p95_all", "test_ratio_median_rejected",
        "bridge_rows", "bridge_mavlink_sent", "bridge_sim_rate_hz", "bridge_wall_rate_hz",
        "bridge_compute_s_mean", "bridge_compute_s_p95", "bridge_compute_s_max",
        "dominant_reject_class",
    ]
    for k in order:
        if k in out:
            print(f"{k} = {out[k]}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
