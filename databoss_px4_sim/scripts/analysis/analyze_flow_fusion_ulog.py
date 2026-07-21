#!/usr/bin/env python3
"""Analyze EKF2 optical-flow fusion evidence in a ULog.

Usage: analyze_flow_fusion_ulog.py <flight.ulg | run_dir> [--json out.json]

Reports (8G acceptance evidence; prototype of the 8G.3 runner analyzer):
- sensor_optical_flow: rows, rate, quality stats
- estimator_status_flags.cs_opt_flow: active sample count
- estimator_aid_src_optical_flow: fused / innovation_rejected counts
- vehicle_local_position.xy_reset_counter delta
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args()

    path = Path(args.path).resolve()
    ulg = path / "logs" / "flight.ulg" if path.is_dir() else path
    if not ulg.exists():
        print(f"ERROR: {ulg} not found", file=sys.stderr)
        return 1

    ulog = ULog(str(ulg))
    out: dict = {"ulog": str(ulg)}

    flow = dataset(ulog, "sensor_optical_flow")
    if flow is None:
        out["sensor_optical_flow_rows"] = 0
    else:
        t = np.asarray(flow["timestamp"], dtype=float) / 1e6
        q = np.asarray(flow["quality"], dtype=float)
        out["sensor_optical_flow_rows"] = int(len(t))
        out["sensor_optical_flow_rate_hz"] = round(float(len(t) / (t[-1] - t[0])), 2) if len(t) > 1 else None
        out["flow_quality_mean"] = round(float(q.mean()), 1)
        out["flow_quality_min"] = int(q.min())
        out["flow_quality_max"] = int(q.max())
        out["flow_quality_zero_fraction"] = round(float((q == 0).mean()), 4)

    flags = dataset(ulog, "estimator_status_flags")
    if flags is not None and "cs_opt_flow" in flags:
        cs = np.asarray(flags["cs_opt_flow"], dtype=int)
        out["cs_opt_flow_active_count"] = int(np.count_nonzero(cs))
        out["cs_opt_flow_total_count"] = int(len(cs))
        out["cs_opt_flow_active_fraction"] = round(float(cs.mean()), 4)
    else:
        out["cs_opt_flow_active_count"] = 0
        out["cs_opt_flow_total_count"] = int(len(flags["timestamp"])) if flags else 0

    aid = dataset(ulog, "estimator_aid_src_optical_flow")
    if aid is None:
        out["aid_src_optical_flow_rows"] = 0
        out["flow_fused_count"] = 0
        out["flow_rejected_count"] = 0
        out["flow_rejected_over_fused"] = None
    else:
        # fused / innovation_rejected are per-sample booleans in the aid-source topic
        fused = np.asarray(aid.get("fused", []), dtype=int)
        rejected = np.asarray(aid.get("innovation_rejected", []), dtype=int)
        out["aid_src_optical_flow_rows"] = int(len(aid["timestamp"]))
        out["flow_fused_count"] = int(fused.sum())
        out["flow_rejected_count"] = int(rejected.sum())
        out["flow_rejected_over_fused"] = (
            round(float(rejected.sum() / fused.sum()), 4) if fused.sum() else None
        )

    vlp = dataset(ulog, "vehicle_local_position")
    if vlp is not None and "xy_reset_counter" in vlp:
        rc = np.asarray(vlp["xy_reset_counter"], dtype=int)
        out["xy_reset_counter_delta"] = int(rc[-1] - rc[0])

    for k, v in out.items():
        print(f"{k}={v}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {args.json_out}")

    # Stream-presence evidence uses the aid-source topic: sensor_optical_flow
    # is rate-capped to ~1 Hz by the default logger profile, while
    # estimator_aid_src_optical_flow logs at the full EKF input rate
    # (verified in 8G.0: 42 rows vs 2097 rows for the same flight).
    ok = (
        out.get("aid_src_optical_flow_rows", 0) > 100
        and out.get("cs_opt_flow_active_count", 0) > 0
        and out.get("flow_fused_count", 0) > 100
        and (out.get("flow_rejected_over_fused") or 0) < 0.10
    )
    print(f"flow_fusion_ok={ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
