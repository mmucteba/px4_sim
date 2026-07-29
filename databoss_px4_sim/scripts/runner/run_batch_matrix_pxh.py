#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCH_RUNS_DIR = PROJECT_ROOT / "experiments" / "batches"
PYTHON = sys.executable

RUN_DIR_RE = re.compile(r"run_dir=(/opt/databoss_px4_sim/experiments/runs/[^\s]+)")


def clean_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


def clean_stale_processes() -> None:
    patterns = [
        "/opt/sim_px4/PX4-Autopilot/build/px4_sitl_default/bin/px4",
        "px4_sitl",
        "gz topic",
        "gz sim",
        "gzserver",
        "gzclient",
        "ruby.*gz",
        "scripts/runner/send_offboard_local_position_setpoint_mavlink.py",
        "scripts/runner/send_live_gazebo_odometry_mavlink.py",
        "scripts/sim/record_camera_frames.py",
        "scripts/sim/flow_mavlink_bridge.py",
    ]

    for pattern in patterns:
        subprocess.run(
            ["pkill", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for socket_path in glob.glob("/tmp/px4-sock-*"):
        try:
            Path(socket_path).unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            # Root-owned sockets can happen after manual/root PX4 launches.
            # The operator must remove those once before running as user px4.
            pass

    time.sleep(3)


def run_cmd(cmd: list[str], log_path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path.write_text(proc.stdout)
    return proc.returncode, proc.stdout


def find_run_dir(output: str) -> str | None:
    matches = RUN_DIR_RE.findall(output)
    if not matches:
        return None
    return matches[-1]


def build_case_cmd(defaults: dict, case: dict) -> list[str]:
    scenario = case.get("scenario", defaults.get("scenario"))
    startup_timeout_s = case.get("startup_timeout_s", defaults.get("startup_timeout_s", 150))
    land_timeout_s = case.get("land_timeout_s", defaults.get("land_timeout_s", 70))
    hover_s = case.get("hover_s", defaults.get("hover_s", 25))

    qgc_default = defaults.get("qgc", {})
    qgc_case = case.get("qgc", {})

    qgc_enabled = qgc_case.get("enabled", qgc_default.get("enabled", True))
    qgc_ip = qgc_case.get("ip", qgc_default.get("ip", "100.109.200.5"))
    qgc_local_port = qgc_case.get("local_port", qgc_default.get("local_port", 14555))
    qgc_remote_port = qgc_case.get("remote_port", qgc_default.get("remote_port", 14550))
    qgc_rate = qgc_case.get("rate", qgc_default.get("rate", 1000000))

    failsafe_profile = case.get("failsafe_profile", defaults.get("failsafe_profile", "default_px4"))
    global_position_timeout_s = case.get("global_position_timeout_s", defaults.get("global_position_timeout_s", 90))
    global_position_stable_s = case.get("global_position_stable_s", defaults.get("global_position_stable_s", 5))
    global_position_gate_enabled = case.get(
        "global_position_gate_enabled",
        defaults.get("global_position_gate_enabled", True),
    )
    gnss_start_used = case.get("gnss_start_used", defaults.get("gnss_start_used", 10))
    gnss_loss_after_takeoff_s = case.get("gnss_loss_after_takeoff_s", defaults.get("gnss_loss_after_takeoff_s"))
    post_loss_hover_s = case.get("post_loss_hover_s", defaults.get("post_loss_hover_s"))

    cmd = [
        PYTHON,
        "scripts/runner/run_scenario_pxh_end_to_end.py",
        scenario,
        "--hover-s",
        str(hover_s),
        "--startup-timeout-s",
        str(startup_timeout_s),
        "--land-timeout-s",
        str(land_timeout_s),
        "--gnss-start-used",
        str(gnss_start_used),
        "--failsafe-profile",
        str(failsafe_profile),
        "--global-position-timeout-s",
        str(global_position_timeout_s),
        "--global-position-stable-s",
        str(global_position_stable_s),
    ]

    if not global_position_gate_enabled:
        cmd.append("--no-global-position-gate")

    if qgc_enabled:
        cmd.extend([
            "--qgc-ip",
            str(qgc_ip),
            "--qgc-local-port",
            str(qgc_local_port),
            "--qgc-remote-port",
            str(qgc_remote_port),
            "--qgc-rate",
            str(qgc_rate),
        ])
    else:
        cmd.append("--no-qgc")

    if gnss_loss_after_takeoff_s is not None:
        cmd.extend(["--gnss-loss-after-takeoff-s", str(gnss_loss_after_takeoff_s)])

    if post_loss_hover_s is not None:
        cmd.extend(["--post-loss-hover-s", str(post_loss_hover_s)])

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Run rerunnable DATABOSS case matrix.")
    parser.add_argument("batch_yaml")
    parser.add_argument("--only", default=None, help="Run one case by name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-fail", action="store_true")
    parser.add_argument("--no-clean-before-case", action="store_true")
    args = parser.parse_args()

    batch_yaml = Path(args.batch_yaml).resolve()
    if not batch_yaml.exists():
        print(f"ERROR: batch YAML not found: {batch_yaml}", file=sys.stderr)
        return 1

    cfg = yaml.safe_load(batch_yaml.read_text())
    batch_name = cfg.get("batch", {}).get("name", batch_yaml.stem)
    defaults = cfg.get("defaults", {})
    configured_cases = cfg.get("cases", [])

    if args.only:
        cases = [c for c in configured_cases if c.get("name") == args.only]
        if not cases:
            print(f"ERROR: case not found: {args.only}", file=sys.stderr)
            return 1
        if cases[0].get("disabled"):
            reason = cases[0].get("disabled_reason", "no reason provided")
            print(f"ERROR: case disabled: {args.only}: {reason}", file=sys.stderr)
            return 1
    else:
        disabled_cases = [c for c in configured_cases if c.get("disabled")]
        for case in disabled_cases:
            reason = case.get("disabled_reason", "no reason provided")
            print(f"Skipping disabled case {case.get('name')}: {reason}")
        cases = [c for c in configured_cases if not c.get("disabled")]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    batch_dir = BATCH_RUNS_DIR / f"{timestamp}_{clean_name(batch_name)}"
    batch_dir.mkdir(parents=True, exist_ok=False)
    (batch_dir / "case_logs").mkdir()

    results = []

    for i, case in enumerate(cases, start=1):
        name = case["name"]
        cmd = build_case_cmd(defaults, case)
        log_path = batch_dir / "case_logs" / f"{i:02d}_{clean_name(name)}.log"

        print()
        print(f"== Case {i}/{len(cases)}: {name} ==")
        print(" ".join(cmd))

        if args.dry_run:
            rc = 0
            output = "DRY RUN ONLY\n\n" + " ".join(cmd) + "\n"
            log_path.write_text(output)
            run_dir = None
            accepted = None
        else:
            if not args.no_clean_before_case:
                print("Cleaning stale PX4/Gazebo processes before case...")
                clean_stale_processes()

            rc, output = run_cmd(cmd, log_path)
            print(output[-4000:])
            run_dir = find_run_dir(output)
            accepted = rc == 0

        results.append({
            "index": i,
            "name": name,
            "returncode": rc,
            "accepted": accepted,
            "run_dir": run_dir,
            "log_path": str(log_path),
            "cmd": cmd,
            "case": case,
        })

        if rc != 0 and not args.continue_on_fail:
            break

    accepted_count = sum(1 for r in results if r["accepted"] is True)
    failed_count = sum(1 for r in results if r["accepted"] is False)

    if args.dry_run:
        batch_accepted = None
        result_label = "Dry run"
    else:
        batch_accepted = failed_count == 0 and len(results) == len(cases)
        result_label = "Accepted" if batch_accepted else "Rejected"

    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "batch_yaml": str(batch_yaml),
        "batch_dir": str(batch_dir),
        "batch_name": batch_name,
        "dry_run": args.dry_run,
        "total_cases_requested": len(cases),
        "cases_run": len(results),
        "accepted_count": accepted_count,
        "failed_count": failed_count,
        "accepted": batch_accepted,
        "result_label": result_label,
        "results": results,
    }

    (batch_dir / "batch_summary.json").write_text(json.dumps(summary, indent=2))

    lines = [
        "# Batch Summary",
        "",
        f"- Batch: `{batch_name}`",
        f"- Batch YAML: `{batch_yaml}`",
        f"- Dry run: `{args.dry_run}`",
        f"- Cases run: `{len(results)}`",
        f"- Accepted: `{accepted_count}`",
        f"- Failed: `{failed_count}`",
        f"- Result: `{result_label}`",
        "",
        "## Cases",
        "",
        "| # | Case | Accepted | Run folder | Log |",
        "|---:|---|---:|---|---|",
    ]

    for r in results:
        lines.append(
            f"| {r['index']} | `{r['name']}` | `{r['accepted']}` | `{r['run_dir']}` | `{r['log_path']}` |"
        )

    lines.append("")
    (batch_dir / "batch_summary.md").write_text("\n".join(lines))

    print()
    print("== Batch result ==")
    print(f"batch_dir={batch_dir}")
    print(f"dry_run={args.dry_run}")
    print(f"cases_run={len(results)}")
    print(f"accepted_count={accepted_count}")
    print(f"failed_count={failed_count}")
    print(f"accepted={batch_accepted}")
    print(f"result={result_label}")
    print(f"summary={batch_dir / 'batch_summary.md'}")

    return 0 if args.dry_run or batch_accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
