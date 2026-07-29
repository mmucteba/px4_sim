#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GENERATED_WORLDS = PROJECT_ROOT / "generated_worlds"
PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))
PX4_GZ_ROOT = PX4_ROOT / "Tools/simulation/gz"
PX4_MODELS = PX4_GZ_ROOT / "models"
PX4_SERVER_CONFIG = PX4_GZ_ROOT / "server.config"
X500_SDF = PX4_MODELS / "x500" / "model.sdf"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_command(
    args: list[str],
    env: dict[str, str],
    timeout_s: float,
    cwd: Path = PROJECT_ROOT,
) -> dict:
    started = time.time()
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "timeout": False,
            "elapsed_s": time.time() - started,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "returncode": None,
            "stdout": exc.stdout or "",
            "timeout": True,
            "elapsed_s": time.time() - started,
        }


def load_world_name(sdf_path: Path) -> str:
    manifest_path = sdf_path.with_suffix(".manifest.json")
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text())
        world_name = data.get("world_name")
        if isinstance(world_name, str) and world_name:
            return world_name
    return sdf_path.stem


def make_env(world_name: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GZ_PARTITION"] = f"databoss_phase8b_{world_name}_{os.getpid()}"
    env["GZ_SIM_RESOURCE_PATH"] = str(PX4_MODELS)
    env["GZ_SIM_SERVER_CONFIG_PATH"] = str(PX4_SERVER_CONFIG)
    # Keep Gazebo transport local and deterministic for repeatable CI-style proof runs.
    env.setdefault("GZ_IP", "127.0.0.1")
    return env


def command_ok(result: dict) -> bool:
    return result.get("returncode") == 0 and not result.get("timeout")


def wait_for_create_service(
    world_name: str,
    env: dict[str, str],
    timeout_s: float,
    poll_s: float,
) -> tuple[bool, dict]:
    service_name = f"/world/{world_name}/create"
    deadline = time.time() + timeout_s
    last_result: dict = {}
    while time.time() < deadline:
        last_result = run_command(["gz", "service", "-l"], env, timeout_s=5)
        if command_ok(last_result) and service_name in last_result["stdout"].splitlines():
            return True, last_result
        time.sleep(poll_s)
    return False, last_result


def stop_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=5)
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def spawn_x500(world_name: str, model_name: str, env: dict[str, str], timeout_s: float) -> dict:
    request = (
        f'sdf_filename: "{X500_SDF}" '
        f'name: "{model_name}" '
        "allow_renaming: false "
        "pose: { position: { x: 0 y: 0 z: 0.25 } orientation: { w: 1 } }"
    )
    return run_command(
        [
            "gz",
            "service",
            "-s",
            f"/world/{world_name}/create",
            "--reqtype",
            "gz.msgs.EntityFactory",
            "--reptype",
            "gz.msgs.Boolean",
            "--timeout",
            str(int(timeout_s * 1000)),
            "--req",
            request,
        ],
        env,
        timeout_s=timeout_s + 2,
    )


def prove_one_world(
    sdf_path: Path,
    out_dir: Path,
    startup_timeout_s: float,
    spawn_timeout_s: float,
    settle_s: float,
    poll_s: float,
) -> dict:
    world_name = load_world_name(sdf_path)
    model_name = f"x500_phase8b_{world_name}"
    world_dir = out_dir / world_name
    world_dir.mkdir(parents=True, exist_ok=True)
    console_log = world_dir / "gz_console.log"
    env = make_env(world_name)

    result: dict = {
        "world_name": world_name,
        "sdf_path": str(sdf_path),
        "model_name": model_name,
        "x500_sdf": str(X500_SDF),
        "gz_partition": env["GZ_PARTITION"],
        "launch_ok": False,
        "create_service_seen": False,
        "spawn_service_ok": False,
        "spawn_response": "",
        "model_list_seen": False,
        "pose_seen": False,
        "accepted": False,
    }

    with console_log.open("w") as log_file:
        process = subprocess.Popen(
            ["gz", "sim", "-r", "-s", "-v", "2", str(sdf_path)],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        result["gz_pid"] = process.pid
        try:
            service_seen, service_list = wait_for_create_service(
                world_name,
                env,
                timeout_s=startup_timeout_s,
                poll_s=poll_s,
            )
            result["create_service_seen"] = service_seen
            result["service_list_before_spawn"] = service_list
            result["launch_ok"] = process.poll() is None and service_seen

            if not result["launch_ok"]:
                result["gz_exit_code"] = process.poll()
                return result

            spawn = spawn_x500(world_name, model_name, env, timeout_s=spawn_timeout_s)
            result["spawn_service"] = spawn
            result["spawn_response"] = spawn.get("stdout", "")
            result["spawn_service_ok"] = command_ok(spawn) and "data: true" in spawn.get(
                "stdout", ""
            )

            time.sleep(settle_s)

            model_list = run_command(["gz", "model", "--list"], env, timeout_s=5)
            result["model_list"] = model_list
            result["model_list_seen"] = command_ok(model_list) and model_name in model_list.get(
                "stdout", ""
            )

            pose_info = run_command(
                [
                    "gz",
                    "topic",
                    "-e",
                    "-n",
                    "1",
                    "-t",
                    f"/world/{world_name}/pose/info",
                ],
                env,
                timeout_s=5,
            )
            result["pose_info"] = pose_info
            result["pose_seen"] = command_ok(pose_info) and model_name in pose_info.get(
                "stdout", ""
            )

            services_after = run_command(["gz", "service", "-l"], env, timeout_s=5)
            topics_after = run_command(["gz", "topic", "-l"], env, timeout_s=5)
            result["service_list_after_spawn"] = services_after
            result["topic_list_after_spawn"] = topics_after
            result["x500_topics_seen"] = command_ok(topics_after) and (
                f"/model/{model_name}/" in topics_after.get("stdout", "")
            )

            result["gz_exit_code"] = process.poll()
            result["accepted"] = bool(
                result["launch_ok"]
                and result["spawn_service_ok"]
                and (result["model_list_seen"] or result["pose_seen"] or result["x500_topics_seen"])
            )
            return result
        finally:
            stop_process_group(process)
            result["gz_stopped"] = process.poll() is not None
            result["gz_final_exit_code"] = process.poll()


def write_report(out_dir: Path, results: list[dict], started_at: str) -> Path:
    accepted_count = sum(1 for item in results if item.get("accepted"))
    report_path = out_dir / "phase8b_world_launch_proof.md"
    lines = [
        "# Phase 8B World Launch Proof",
        "",
        f"Started UTC: `{started_at}`",
        f"Proof dir: `{out_dir}`",
        f"Result: `{accepted_count}/{len(results)} worlds accepted`",
        "",
        "## Summary",
        "",
        "| World | Gazebo launch | x500 spawn | Entity proof | Accepted |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        entity_proof = []
        if item.get("model_list_seen"):
            entity_proof.append("model list")
        if item.get("pose_seen"):
            entity_proof.append("pose topic")
        if item.get("x500_topics_seen"):
            entity_proof.append("x500 topics")
        lines.append(
            "| {world} | {launch} | {spawn} | {proof} | {accepted} |".format(
                world=item["world_name"],
                launch="yes" if item.get("launch_ok") else "no",
                spawn="yes" if item.get("spawn_service_ok") else "no",
                proof=", ".join(entity_proof) if entity_proof else "none",
                accepted="yes" if item.get("accepted") else "no",
            )
        )
    lines.extend(
        [
            "",
            "## Acceptance Meaning",
            "",
            "This proof launches each generated SDF world in Gazebo server mode and uses the Gazebo world create service to spawn the PX4 `x500` model from the PX4 Gazebo model store.",
            "",
            "It proves the generated world files are physically launchable and compatible with the x500 Gazebo entity path. It does not yet prove a full PX4 flight in those worlds; that belongs to the next Phase 8B/8C integration step.",
            "",
            "## Per-World Artifacts",
            "",
        ]
    )
    for item in results:
        world_dir = out_dir / item["world_name"]
        lines.extend(
            [
                f"### {item['world_name']}",
                "",
                f"- SDF: `{item['sdf_path']}`",
                f"- x500 SDF: `{item['x500_sdf']}`",
                f"- Gazebo console: `{world_dir / 'gz_console.log'}`",
                f"- JSON result: `{world_dir / 'result.json'}`",
                f"- GZ partition: `{item['gz_partition']}`",
                "",
            ]
        )
    report_path.write_text("\n".join(lines) + "\n")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch generated Gazebo worlds and prove PX4 x500 can spawn."
    )
    parser.add_argument(
        "worlds",
        nargs="*",
        type=Path,
        help="Generated SDF world paths. Defaults to generated_worlds/*.sdf.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/tmp") / f"databoss_phase8b_world_launch_proof_{utc_stamp()}",
        help="Directory for proof logs and report.",
    )
    parser.add_argument("--startup-timeout-s", type=float, default=30.0)
    parser.add_argument("--spawn-timeout-s", type=float, default=10.0)
    parser.add_argument("--settle-s", type=float, default=2.0)
    parser.add_argument("--poll-s", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worlds = args.worlds or sorted(DEFAULT_GENERATED_WORLDS.glob("*.sdf"))
    if not worlds:
        print(f"No generated SDF worlds found in {DEFAULT_GENERATED_WORLDS}", file=sys.stderr)
        return 2
    missing = [path for path in [PX4_MODELS, PX4_SERVER_CONFIG, X500_SDF] if not path.exists()]
    if missing:
        print("Missing PX4 Gazebo assets:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 2

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for sdf_path in worlds:
        result = prove_one_world(
            sdf_path=sdf_path.resolve(),
            out_dir=out_dir,
            startup_timeout_s=args.startup_timeout_s,
            spawn_timeout_s=args.spawn_timeout_s,
            settle_s=args.settle_s,
            poll_s=args.poll_s,
        )
        results.append(result)
        result_path = out_dir / result["world_name"] / "result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True))
        print(
            "{world}: launch={launch} spawn={spawn} accepted={accepted}".format(
                world=result["world_name"],
                launch=result.get("launch_ok"),
                spawn=result.get("spawn_service_ok"),
                accepted=result.get("accepted"),
            ),
            flush=True,
        )

    summary = {
        "started_at": started_at,
        "out_dir": str(out_dir),
        "accepted": all(item.get("accepted") for item in results),
        "accepted_count": sum(1 for item in results if item.get("accepted")),
        "world_count": len(results),
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    report_path = write_report(out_dir, results, started_at)
    print(f"report={report_path}")
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
