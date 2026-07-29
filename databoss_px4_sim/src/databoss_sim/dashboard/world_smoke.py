"""Gazebo world-load smoke test used by the dashboard job runner."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from databoss_sim.dashboard.config import PROJECT_ROOT

PX4_GZ_ROOT = Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz")
PX4_MODELS = PX4_GZ_ROOT / "models"
PX4_SERVER_CONFIG = PX4_GZ_ROOT / "server.config"
X500_SDF = PX4_MODELS / "x500" / "model.sdf"


class _SmokeCancelled(Exception):
    pass


def read_declared_world_name(sdf_path: Path) -> str | None:
    try:
        root = ET.parse(sdf_path).getroot()
    except (ET.ParseError, OSError):
        return None
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "world":
            name = elem.attrib.get("name")
            return name if isinstance(name, str) and name else None
    return None


def expected_world_name(sdf_path: Path) -> str:
    manifest_path = sdf_path.with_suffix(".manifest.json")
    try:
        with manifest_path.open("r") as f:
            data = json.load(f)
        world_name = data.get("world_name")
        if isinstance(world_name, str) and world_name.strip():
            return world_name
    except (OSError, json.JSONDecodeError):
        pass
    return sdf_path.stem


def _sidecar_path(sdf_path: Path) -> Path:
    return Path(str(sdf_path) + ".smoke.json")


def write_smoke_sidecar(sdf_path: Path, result: dict) -> Path:
    path = _sidecar_path(sdf_path)
    payload = dict(result)
    payload["checked_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_smoke_sidecar(sdf_path: Path) -> dict | None:
    try:
        with _sidecar_path(sdf_path).open("r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _make_env(world_name: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GZ_PARTITION"] = f"databoss_world_smoke_{world_name}_{os.getpid()}_{int(time.time() * 1000)}"
    env["GZ_SIM_RESOURCE_PATH"] = str(PX4_MODELS)
    env["GZ_SIM_SERVER_CONFIG_PATH"] = str(PX4_SERVER_CONFIG)
    env.setdefault("GZ_IP", "127.0.0.1")
    return env


def _run_gz_query(args: list[str], env: dict[str, str], timeout_s: float = 5.0) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or ""


def _console_tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _stop_process_group(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _result(
    sdf_path: Path,
    declared: str | None,
    expected: str,
    started: float,
    timeout_s: float,
    stage: str,
    ok: bool,
    error: str = "",
    gz_returncode: int | None = None,
    console_log: Path | None = None,
) -> dict:
    return {
        "ok": ok,
        "stage": stage,
        "declared_world_name": declared,
        "expected_world_name": expected,
        "sdf": str(sdf_path),
        "elapsed_s": round(time.time() - started, 3),
        "timeout_s": timeout_s,
        "error": error,
        "gz_returncode": gz_returncode,
        "console_tail": _console_tail(console_log) if console_log is not None else "",
    }


def _wait_for_service(
    service: str,
    stage: str,
    process: subprocess.Popen[str],
    env: dict[str, str],
    deadline: float,
) -> tuple[bool, str]:
    while time.time() < deadline:
        if process.poll() is not None:
            return False, f"gz sim exited before {stage}: returncode={process.returncode}"
        rc, out = _run_gz_query(["gz", "service", "-i", "--service", service], env)
        if rc == 0 and "Service providers" in out:
            return True, ""
        time.sleep(1.0)
    return False, f"timeout waiting for {service}"


def _spawn_x500(world_name: str, model_name: str, env: dict[str, str], timeout_s: float) -> tuple[bool, str]:
    request = (
        f'sdf_filename: "{X500_SDF}" '
        f'name: "{model_name}" '
        "allow_renaming: false "
        "pose: { position: { x: 0 y: 0 z: 0.25 } orientation: { w: 1 } }"
    )
    rc, out = _run_gz_query(
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
    return rc == 0 and "data: true" in out, out


def smoke_test_world(sdf_path: Path, timeout_s: float = 120.0, model_name: str = "smoke_x500") -> dict:
    started = time.time()
    sdf_path = sdf_path.resolve()
    declared = read_declared_world_name(sdf_path)
    expected = expected_world_name(sdf_path)
    print(f"stage=world_name declared={declared!r} expected={expected!r}", flush=True)
    if declared != expected:
        return _result(
            sdf_path,
            declared,
            expected,
            started,
            timeout_s,
            "world_name",
            False,
            f"declared world name {declared!r} does not match expected world name {expected!r}",
        )

    job_dir = Path(os.environ.get("DATABOSS_JOB_DIR", "/tmp")).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    console_log = job_dir / "gz_console.log"
    env = _make_env(expected)
    deadline = time.time() + timeout_s
    process: subprocess.Popen[str] | None = None
    old_sigterm = signal.getsignal(signal.SIGTERM)
    old_sigint = signal.getsignal(signal.SIGINT)
    result: dict | None = None
    current_stage = "start"

    def _cancel(signum: int, _frame: Any) -> None:
        raise _SmokeCancelled(f"received signal {signum}")

    with console_log.open("w") as log_file:
        signal.signal(signal.SIGTERM, _cancel)
        signal.signal(signal.SIGINT, _cancel)
        try:
            try:
                process = subprocess.Popen(
                    ["gz", "sim", "-r", "-s", "-v", "2", str(sdf_path)],
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                result = _result(
                    sdf_path, declared, expected, started, timeout_s, "start", False, str(exc), None, console_log
                )
                return result

            current_stage = "scene_info"
            print(f"stage=scene_info service=/world/{expected}/scene/info", flush=True)
            ok, error = _wait_for_service(f"/world/{expected}/scene/info", "scene_info", process, env, deadline)
            if not ok:
                result = _result(
                    sdf_path, declared, expected, started, timeout_s, "scene_info", False, error,
                    process.poll(), console_log,
                )
                return result

            current_stage = "create_service"
            print(f"stage=create_service service=/world/{expected}/create", flush=True)
            ok, error = _wait_for_service(f"/world/{expected}/create", "create_service", process, env, deadline)
            if not ok:
                result = _result(
                    sdf_path, declared, expected, started, timeout_s, "create_service", False, error,
                    process.poll(), console_log,
                )
                return result

            current_stage = "spawn"
            remaining = max(1.0, deadline - time.time())
            spawn_timeout_s = min(30.0, remaining)
            print(f"stage=spawn model={model_name}", flush=True)
            ok, spawn_out = _spawn_x500(expected, model_name, env, spawn_timeout_s)
            if not ok:
                result = _result(
                    sdf_path, declared, expected, started, timeout_s, "spawn", False,
                    spawn_out.strip() or "x500 spawn service returned false", process.poll(), console_log,
                )
                return result

            result = _result(
                sdf_path, declared, expected, started, timeout_s, "done", True, "", process.poll(), console_log
            )
            return result
        except _SmokeCancelled as exc:
            result = _result(
                sdf_path, declared, expected, started, timeout_s, "cancelled", False, str(exc),
                process.poll(), console_log,
            )
            return result
        except Exception as exc:
            result = _result(
                sdf_path, declared, expected, started, timeout_s, current_stage, False, str(exc),
                process.poll(), console_log,
            )
            return result
        finally:
            _stop_process_group(process)
            if result is not None:
                result["gz_returncode"] = process.poll()
                result["console_tail"] = _console_tail(console_log)
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a Gazebo world load and x500 spawn.")
    parser.add_argument("sdf", type=Path)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--model-name", default="smoke_x500")
    args = parser.parse_args(argv)

    result = smoke_test_world(args.sdf, timeout_s=args.timeout_s, model_name=args.model_name)
    sidecar = write_smoke_sidecar(args.sdf, result)
    print(f"stage=sidecar path={sidecar}", flush=True)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
