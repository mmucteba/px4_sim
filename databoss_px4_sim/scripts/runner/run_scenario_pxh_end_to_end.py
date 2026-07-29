#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import yaml

from create_run_from_scenario import (
    resolve_gnss_loss_after_takeoff_s,
    resolve_failsafe_profile,
)

PYTHON = sys.executable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"


def run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)
    return proc.wait(), "".join(chunks)


def latest_truth_run_after(start_ts: float) -> Path | None:
    candidates = []
    for p in RUNS_DIR.glob("*pxh_takeoff_land_truth"):
        try:
            if p.stat().st_mtime >= start_ts - 5:
                candidates.append(p)
        except FileNotFoundError:
            pass

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DATABOSS PXH scenario end-to-end.")
    parser.add_argument("scenario", help="Scenario YAML path")
    parser.add_argument("--hover-s", type=float, default=25.0)
    parser.add_argument("--startup-timeout-s", type=float, default=150.0)
    parser.add_argument("--world-ready-timeout-s", type=float, default=120.0)
    parser.add_argument("--land-timeout-s", type=float, default=70.0)
    parser.add_argument("--qgc-ip", default="100.109.200.5")
    parser.add_argument("--qgc-local-port", type=int, default=14555)
    parser.add_argument("--qgc-remote-port", type=int, default=14550)
    parser.add_argument("--qgc-rate", type=int, default=1000000)
    parser.add_argument("--no-qgc", action="store_true")
    parser.add_argument("--gnss-start-used", type=int, default=10)
    parser.add_argument("--gnss-loss-after-takeoff-s", type=float, default=None)
    parser.add_argument("--post-loss-hover-s", type=float, default=None)
    parser.add_argument("--failsafe-profile", choices=["default_px4", "delayed_observation"], default=None,
                        help="Override scenario failsafe.profile. If omitted, the scenario YAML's failsafe.profile is authoritative.")
    parser.add_argument("--global-position-timeout-s", type=float, default=90.0)
    parser.add_argument("--global-position-stable-s", type=float, default=5.0)
    parser.add_argument("--no-global-position-gate", action="store_true")
    parser.add_argument("--allow-experimental-ev-velocity", action="store_true")
    args = parser.parse_args()

    scenario = Path(args.scenario).resolve()
    if not scenario.exists():
        print(f"ERROR: scenario not found: {scenario}", file=sys.stderr)
        return 1

    with scenario.open() as f:
        scenario_cfg = yaml.safe_load(f) or {}
    control_cfg = scenario_cfg.get("control", {}) if isinstance(scenario_cfg, dict) else {}
    analysis_cfg = scenario_cfg.get("analysis", {}) if isinstance(scenario_cfg, dict) else {}
    flow_bridge_cfg = scenario_cfg.get("flow_bridge", {}) if isinstance(scenario_cfg, dict) else {}

    # Resolve the GNSS-loss / failsafe launch contract from the scenario
    # YAML (CLI stays an explicit override) BEFORE branching on it, so the
    # wrapper and the runner it spawns never disagree about whether this is
    # a GNSS-loss run. Forward the resolved values explicitly to the runner.
    args.gnss_loss_after_takeoff_s, gnss_loss_source = resolve_gnss_loss_after_takeoff_s(
        scenario_cfg, args.gnss_loss_after_takeoff_s
    )
    try:
        args.failsafe_profile, failsafe_profile_source = resolve_failsafe_profile(
            scenario_cfg, args.failsafe_profile
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"gnss_loss_source={gnss_loss_source} "
          f"resolved_gnss_loss_after_takeoff_s={args.gnss_loss_after_takeoff_s}")
    print(f"failsafe_profile_source={failsafe_profile_source} "
          f"resolved_failsafe_profile={args.failsafe_profile}")

    skip_landing_command = bool(control_cfg.get("skip_landing_command", False))
    sensor_contract_report_enabled = bool(analysis_cfg.get("sensor_contract_report", False))
    sensor_contract_gate_required = bool(analysis_cfg.get("sensor_contract_gate_required", False))
    sensor_contract_gate = str(analysis_cfg.get("sensor_contract_gate", "auto"))
    flow_bridge_enabled = bool(flow_bridge_cfg.get("enabled", False))
    flow_velocity_sign_default = flow_bridge_enabled and args.gnss_loss_after_takeoff_s is None
    flow_velocity_sign_enabled = bool(analysis_cfg.get("check_flow_velocity_sign", flow_velocity_sign_default))
    flow_velocity_sign_required = bool(analysis_cfg.get("flow_velocity_sign_required", False))
    flow_velocity_sign_source = str(analysis_cfg.get("flow_velocity_sign_source", "gps"))

    start_ts = time.time()
    step_results = []

    flight_cmd = [
        PYTHON,
        "scripts/runner/auto_takeoff_land_pxh_truth.py",
        str(scenario),
        "--hover-s",
        str(args.hover_s),
        "--startup-timeout-s",
        str(args.startup_timeout_s),
        "--world-ready-timeout-s",
        str(args.world_ready_timeout_s),
        "--land-timeout-s",
        str(args.land_timeout_s),
        "--gnss-start-used",
        str(args.gnss_start_used),
        "--failsafe-profile",
        args.failsafe_profile,
        "--global-position-timeout-s",
        str(args.global_position_timeout_s),
        "--global-position-stable-s",
        str(args.global_position_stable_s),
    ]

    if args.no_global_position_gate:
        flight_cmd.append("--no-global-position-gate")

    if args.allow_experimental_ev_velocity:
        flight_cmd.append("--allow-experimental-ev-velocity")

    if args.no_qgc:
        flight_cmd.append("--no-qgc")
    else:
        flight_cmd.extend([
            "--qgc-ip",
            args.qgc_ip,
            "--qgc-local-port",
            str(args.qgc_local_port),
            "--qgc-remote-port",
            str(args.qgc_remote_port),
            "--qgc-rate",
            str(args.qgc_rate),
        ])

    if args.gnss_loss_after_takeoff_s is not None:
        flight_cmd.extend([
            "--gnss-loss-after-takeoff-s",
            str(args.gnss_loss_after_takeoff_s),
        ])

    if args.post_loss_hover_s is not None:
        flight_cmd.extend([
            "--post-loss-hover-s",
            str(args.post_loss_hover_s),
        ])

    print("== Step 1: automated PX4/Gazebo flight with truth ==")
    rc, out = run_cmd(flight_cmd, PROJECT_ROOT)
    step_results.append({"step": "flight_with_truth", "returncode": rc, "output_tail": out[-4000:]})
    if rc != 0:
        print("ERROR: flight step failed", file=sys.stderr)
        return rc

    run_dir = latest_truth_run_after(start_ts)
    if run_dir is None:
        print("ERROR: could not locate latest truth run folder", file=sys.stderr)
        return 1

    print(f"Detected run_dir: {run_dir}")

    print("== Step 2: postprocess ==")
    post_cmd = [
        PYTHON,
        "scripts/runner/postprocess_latest_truth_run.py",
        "--run-dir",
        str(run_dir),
    ]
    rc, out = run_cmd(post_cmd, PROJECT_ROOT)
    step_results.append({"step": "postprocess", "returncode": rc, "output_tail": out[-4000:]})
    if rc != 0:
        print("ERROR: postprocess step failed", file=sys.stderr)
        return rc

    print("== Step 3: align EKF vs Gazebo truth ==")
    align_cmd = [
        PYTHON,
        "scripts/runner/align_latest_truth_run.py",
        "--run-dir",
        str(run_dir),
        "--comparison-window",
        "full" if skip_landing_command else "until-land-command",
    ]
    rc, out = run_cmd(align_cmd, PROJECT_ROOT)
    step_results.append({"step": "align", "returncode": rc, "output_tail": out[-4000:]})
    if rc != 0:
        print("ERROR: align step failed", file=sys.stderr)
        return rc

    if flow_velocity_sign_enabled:
        print("== Step 4: flow velocity sign sentinel ==")
        sign_cmd = [
            PYTHON,
            "scripts/analysis/check_flow_velocity_sign.py",
            str(run_dir),
            "--source",
            flow_velocity_sign_source,
            "--out",
            str(run_dir / "flow_velocity_sign.json"),
        ]
        rc, out = run_cmd(sign_cmd, PROJECT_ROOT)
        step_results.append({"step": "flow_velocity_sign", "returncode": rc, "output_tail": out[-4000:]})
        if rc != 0 and flow_velocity_sign_required:
            print("ERROR: flow velocity sign sentinel failed", file=sys.stderr)
            return rc

    if sensor_contract_report_enabled:
        print("== Step 5: sensor contract report ==")
        report_cmd = [
            PYTHON,
            "scripts/analysis/sensor_contract_report.py",
            "--run-dir",
            str(run_dir),
            "--gate",
            sensor_contract_gate,
        ]
        rc, out = run_cmd(report_cmd, PROJECT_ROOT)
        step_results.append({"step": "sensor_contract_report", "returncode": rc, "output_tail": out[-4000:]})
        if rc != 0 and sensor_contract_gate_required:
            print("ERROR: sensor contract gate failed", file=sys.stderr)
            return rc

    status_path = run_dir / "end_to_end_status.json"
    status = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": str(scenario),
        "run_dir": str(run_dir),
        "qgc_enabled": not args.no_qgc,
        "qgc_ip": args.qgc_ip,
        "qgc_local_port": args.qgc_local_port,
        "qgc_remote_port": args.qgc_remote_port,
        "failsafe_profile": args.failsafe_profile,
        "failsafe_profile_source": failsafe_profile_source,
        "global_position_gate_enabled": not args.no_global_position_gate,
        "global_position_timeout_s": args.global_position_timeout_s,
        "global_position_stable_s": args.global_position_stable_s,
        "gnss_start_used": args.gnss_start_used,
        "gnss_loss_source": gnss_loss_source,
        "gnss_loss_after_takeoff_s": args.gnss_loss_after_takeoff_s,
        "post_loss_hover_s": args.post_loss_hover_s,
        "skip_landing_command": skip_landing_command,
        "comparison_window": "full" if skip_landing_command else "until-land-command",
        "flow_velocity_sign_enabled": flow_velocity_sign_enabled,
        "flow_velocity_sign_required": flow_velocity_sign_required,
        "flow_velocity_sign_source": flow_velocity_sign_source,
        "accepted": True,
        "steps": step_results,
    }
    status_path.write_text(json.dumps(status, indent=2))

    final_summary = run_dir / "final_summary.md"
    metrics_md = run_dir / "ekf_vs_ground_truth_metrics.md"
    post_md = run_dir / "postprocess_summary.md"
    validation_md = run_dir / "validation.md"

    final_summary.write_text(
        "\n".join([
            "# Final End-to-End Summary",
            "",
            f"- Scenario: `{scenario}`",
            f"- Run folder: `{run_dir}`",
            f"- End-to-end status: `Accepted`",
            "",
            "## Included reports",
            "",
            f"- Validation: `{validation_md}`",
            f"- Postprocess: `{post_md}`",
            f"- EKF vs truth metrics: `{metrics_md}`",
            f"- Flow velocity sign sentinel: `{run_dir / 'flow_velocity_sign.json'}`",
            "",
            "## Result",
            "",
            "Accepted.",
            "",
        ])
    )

    print("== End-to-end result ==")
    print("accepted=True")
    print(f"run_dir={run_dir}")
    print(f"status={status_path}")
    print(f"final_summary={final_summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
