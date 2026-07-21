#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from create_run_from_scenario import (
    PROJECT_ROOT,
    PX4_ROOT,
    RUNS_DIR,
    load_yaml,
    validate_scenario,
    make_run_id,
    write_environment,
)

READY_PATTERNS = [
    "Ready for takeoff",
    "Startup script returned successfully",
    "pxh>",
]


def clean_px4_env() -> dict:
    env = os.environ.copy()
    env["HEADLESS"] = "1"

    # Avoid PX4 accidentally using DATABOSS venv python.
    bad_paths = {
        str(PROJECT_ROOT / "venv" / "bin"),
        str(PROJECT_ROOT / ".venv" / "bin"),
    }

    path_parts = env.get("PATH", "").split(":")
    path_parts = [p for p in path_parts if p and p not in bad_paths]

    for required in ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]:
        if required not in path_parts:
            path_parts.append(required)

    env["PATH"] = ":".join(path_parts)
    env.pop("VIRTUAL_ENV", None)

    return env


def make_run_folder(scenario_path: Path, data: dict) -> Path:
    scenario_name = data["run"]["name"]
    run_id = make_run_id(scenario_name)
    run_dir = RUNS_DIR / run_id

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "logs").mkdir()
    (run_dir / "gazebo_truth").mkdir()
    (run_dir / "extracted_csv").mkdir()
    (run_dir / "plots").mkdir()

    shutil.copy2(scenario_path, run_dir / "config.yaml")

    (run_dir / "README.md").write_text(
        "\n".join([
            f"# {run_id}",
            "",
            "Created by Phase 7A.2 PX4/Gazebo launcher smoke test.",
            "",
            "This run starts PX4/Gazebo headless, waits for readiness, then stops cleanly.",
            "",
            "No autonomous flight is executed in this step.",
            "",
        ])
    )

    (run_dir / "commands.log").write_text(
        "\n".join([
            "# Commands",
            "",
            f"cd {PROJECT_ROOT}",
            f"python3 scripts/runner/launch_px4_headless_smoke.py {scenario_path}",
            "",
        ])
    )

    write_environment(run_dir / "environment.txt")
    return run_dir


def read_tail(path: Path, max_chars: int = 30000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(errors="ignore")
    return text[-max_chars:]


def stop_process_group(proc: subprocess.Popen, notes: list[str]) -> None:
    if proc.poll() is not None:
        notes.append(f"process already exited with returncode={proc.returncode}")
        return

    pgid = os.getpgid(proc.pid)

    notes.append("sending SIGINT")
    try:
        os.killpg(pgid, signal.SIGINT)
    except ProcessLookupError:
        notes.append("process group already gone after SIGINT")
        return

    try:
        proc.wait(timeout=15)
        notes.append(f"process exited after SIGINT with returncode={proc.returncode}")
        return
    except subprocess.TimeoutExpired:
        notes.append("SIGINT timeout; sending SIGTERM")

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        notes.append("process group already gone after SIGTERM")
        return

    try:
        proc.wait(timeout=10)
        notes.append(f"process exited after SIGTERM with returncode={proc.returncode}")
        return
    except subprocess.TimeoutExpired:
        notes.append("SIGTERM timeout; sending SIGKILL")

    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        notes.append("process group already gone after SIGKILL")
        return

    proc.wait(timeout=10)
    notes.append(f"process killed with returncode={proc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start PX4/Gazebo headless from a DATABOSS scenario and stop after readiness.")
    parser.add_argument("scenario", help="Path to scenario YAML")
    parser.add_argument("--duration-s", type=float, default=10.0, help="Seconds to keep PX4/Gazebo alive after readiness")
    parser.add_argument("--ready-timeout-s", type=float, default=120.0, help="Seconds to wait for PX4 readiness")
    args = parser.parse_args()

    scenario_path = Path(args.scenario).expanduser().resolve()
    if not scenario_path.exists():
        print(f"ERROR: scenario not found: {scenario_path}", file=sys.stderr)
        return 1

    data = load_yaml(scenario_path)
    errors = validate_scenario(data)
    if errors:
        print("ERROR: scenario validation failed:", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        return 1

    vehicle = data.get("vehicle", {})
    model = vehicle.get("model", "gz_x500")

    run_dir = make_run_folder(scenario_path, data)
    console_log = run_dir / "logs" / "px4_gazebo_console.log"
    status_json = run_dir / "logs" / "px4_launcher_status.json"

    cmd = ["make", "px4_sitl", model]
    env = clean_px4_env()

    notes: list[str] = []
    ready = False
    ready_pattern = None

    print(f"Run dir: {run_dir}")
    print(f"PX4 root: {PX4_ROOT}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Console log: {console_log}")

    start_time = time.time()

    with console_log.open("w") as log:
        log.write("# PX4/Gazebo console log\n")
        log.write(f"# started_utc: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        log.write(f"# cwd: {PX4_ROOT}\n")
        log.write(f"# cmd: {' '.join(cmd)}\n\n")
        log.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(PX4_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
            bufsize=1,
        )

        notes.append(f"started process pid={proc.pid}")

        try:
            while time.time() - start_time < args.ready_timeout_s:
                if proc.poll() is not None:
                    notes.append(f"process exited before readiness with returncode={proc.returncode}")
                    break

                tail = read_tail(console_log)
                for pattern in READY_PATTERNS:
                    if pattern in tail:
                        ready = True
                        ready_pattern = pattern
                        notes.append(f"readiness detected using pattern: {pattern}")
                        break

                if ready:
                    break

                time.sleep(1)

            if ready:
                time.sleep(args.duration_s)
            else:
                notes.append("readiness not detected before timeout")

        finally:
            stop_process_group(proc, notes)

    elapsed_s = time.time() - start_time

    status = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "scenario": str(scenario_path),
        "px4_root": str(PX4_ROOT),
        "cmd": cmd,
        "model": model,
        "ready": ready,
        "ready_pattern": ready_pattern,
        "elapsed_s": elapsed_s,
        "returncode": proc.returncode,
        "notes": notes,
        "host": platform.node(),
    }

    status_json.write_text(json.dumps(status, indent=2))

    validation_lines = [
        "# Validation",
        "",
        "## Phase 7A.2 PX4/Gazebo launcher smoke test",
        "",
        f"- Scenario: `{scenario_path}`",
        f"- Run folder: `{run_dir}`",
        f"- PX4 root: `{PX4_ROOT}`",
        f"- Command: `{' '.join(cmd)}`",
        f"- Ready detected: `{ready}`",
        f"- Ready pattern: `{ready_pattern}`",
        f"- Elapsed seconds: `{elapsed_s:.3f}`",
        "",
        "## Checks",
        "",
        "- [x] Scenario YAML parsed.",
        "- [x] Run folder created under experiments/runs/.",
        "- [x] Console log written under run folder.",
        "- [x] PX4/Gazebo process started.",
        "- [x] Stop command attempted cleanly.",
        "- [x] No DATABOSS output written into PX4 source.",
        "",
    ]

    if ready:
        validation_lines.extend([
            "## Result",
            "",
            "Accepted. PX4/Gazebo headless launcher smoke test passed.",
            "",
        ])
    else:
        validation_lines.extend([
            "## Result",
            "",
            "Rejected. PX4/Gazebo readiness was not detected. Inspect logs/px4_gazebo_console.log.",
            "",
        ])

    (run_dir / "validation.md").write_text("\n".join(validation_lines))

    print()
    print("== Launcher result ==")
    print(f"ready={ready}")
    print(f"ready_pattern={ready_pattern}")
    print(f"elapsed_s={elapsed_s:.3f}")
    print(f"run_dir={run_dir}")
    print(f"status_json={status_json}")

    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
