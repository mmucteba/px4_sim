"""Launch and cancel dashboard-owned PX4 scenario jobs."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from databoss_sim.dashboard.config import (
    CANCEL_GRACE_S,
    MIN_AVAILABLE_MEM_MB,
    PROJECT_ROOT,
    RUN_AS_USER,
)
from databoss_sim.dashboard.job_registry import (
    JobRecord,
    _RUNNING,
    acquire_lock,
    job_dir,
    probe_viz_ports,
    read_job,
    release_lock,
    release_lock_for,
    sweep_stale_processes,
    update_lock_pid,
    utc_now,
    write_job_atomic,
)
from databoss_sim.dashboard.scenario_editing import SCENARIOS_DIR

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.runner.create_run_from_scenario import load_yaml, validate_scenario  # noqa: E402

_START_GUARD = threading.Lock()


class LaunchError(RuntimeError):
    status_code = 500

    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


class ScenarioNotFoundError(LaunchError):
    status_code = 404


class ScenarioValidationError(LaunchError):
    status_code = 422


class LaunchConflictError(LaunchError):
    status_code = 409


class LaunchServerError(LaunchError):
    status_code = 500


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    hover_s: float = 25.0
    startup_timeout_s: float = 150.0
    world_ready_timeout_s: float = 120.0
    land_timeout_s: float = 70.0
    gnss_start_used: int = 10
    gnss_loss_after_takeoff_s: float | None = None
    post_loss_hover_s: float | None = None
    failsafe_profile: Literal["default_px4", "delayed_observation"] | None = None
    global_position_timeout_s: float = 90.0
    global_position_stable_s: float = 5.0
    no_global_position_gate: bool = False
    qgc_ip: str = "100.109.200.5"
    note: str = ""
    ignore_memory_guard: bool = False
    # QGroundControl is always enabled; there is intentionally no opt-out field.


class WorldSmokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdf_path: str
    timeout_s: float = 120.0
    note: str = ""
    ignore_memory_guard: bool = False


def _quote(arg: str) -> str:
    return "'" + arg.replace("'", "'\"'\"'") + "'"


def _safe_stem(stem: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)


def resolve_scenario_path(scenario: str) -> Path:
    raw = Path(scenario).expanduser()
    candidates: list[Path]
    if raw.is_absolute():
        candidates = [raw]
    elif raw.suffix:
        candidates = [PROJECT_ROOT / raw]
    else:
        candidates = [
            PROJECT_ROOT / raw,
            SCENARIOS_DIR / raw,
            SCENARIOS_DIR / f"{raw}.yaml",
        ]

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            resolved = candidate
        if resolved.is_file():
            return resolved
    raise ScenarioNotFoundError(f"scenario file not found: {scenario}")


def scenario_arg_for_script(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def scan_host_processes() -> list[dict]:
    hits: list[dict] = []
    patterns = {
        "gz sim": "[g]z sim",
        "px4": "[p]x4_sitl_default/bin/px4",
        "gz-launch": "[g]z-launch",
        "Xvfb": "[X]vfb",
    }
    for label, pattern in patterns.items():
        proc = subprocess.run(
            ["pgrep", "-f", pattern],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        for line in proc.stdout.splitlines():
            try:
                hits.append({"label": label, "pid": int(line.strip())})
            except ValueError:
                continue
    return hits


def _mem_available_mb() -> int | None:
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (FileNotFoundError, ValueError):
        return None
    return None


def preflight(req: LaunchRequest) -> Path:
    scenario_path = resolve_scenario_path(req.scenario)
    try:
        errors = validate_scenario(load_yaml(scenario_path))
    except Exception as exc:
        raise ScenarioValidationError(f"scenario YAML failed to load: {exc}") from exc
    if errors:
        raise ScenarioValidationError({"scenario": str(scenario_path), "errors": errors})

    hits = scan_host_processes()
    if hits:
        raise LaunchConflictError({
            "message": "PX4/Gazebo/Xvfb process already running; use POST /api/jobs/cleanup first",
            "processes": hits,
        })

    mem_mb = _mem_available_mb()
    if mem_mb is not None and mem_mb < MIN_AVAILABLE_MEM_MB and not req.ignore_memory_guard:
        raise LaunchConflictError({
            "message": "MemAvailable below launch guard",
            "mem_available_mb": mem_mb,
            "min_available_mb": MIN_AVAILABLE_MEM_MB,
        })

    if os.geteuid() == 0:
        sudo_check = subprocess.run(
            ["sudo", "-n", "-u", RUN_AS_USER, "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if sudo_check.returncode != 0:
            raise LaunchServerError(
                f"cannot run as {RUN_AS_USER} via sudo -n: {sudo_check.stderr.strip()}"
            )

    return scenario_path


def _script_command(req: LaunchRequest) -> list[str]:
    scenario_path = resolve_scenario_path(req.scenario)
    cmd = [
        "venv/bin/python",
        "-u",
        "scripts/runner/run_scenario_pxh_end_to_end.py",
        scenario_arg_for_script(scenario_path),
        "--hover-s",
        str(req.hover_s),
        "--startup-timeout-s",
        str(req.startup_timeout_s),
        "--world-ready-timeout-s",
        str(req.world_ready_timeout_s),
        "--land-timeout-s",
        str(req.land_timeout_s),
        "--gnss-start-used",
        str(req.gnss_start_used),
        "--global-position-timeout-s",
        str(req.global_position_timeout_s),
        "--global-position-stable-s",
        str(req.global_position_stable_s),
        "--qgc-ip",
        req.qgc_ip,
    ]
    if req.failsafe_profile is not None:
        cmd.extend(["--failsafe-profile", req.failsafe_profile])
    if req.no_global_position_gate:
        cmd.append("--no-global-position-gate")
    if req.gnss_loss_after_takeoff_s is not None:
        cmd.extend(["--gnss-loss-after-takeoff-s", str(req.gnss_loss_after_takeoff_s)])
    if req.post_loss_hover_s is not None:
        cmd.extend(["--post-loss-hover-s", str(req.post_loss_hover_s)])
    return cmd


def build_launch_script(job_path: Path, req: LaunchRequest) -> Path:
    script = job_path / "launch.sh"
    cmd = _script_command(req)
    lines = [
        "#!/bin/bash",
        "cd /opt/databoss_px4_sim || exit 1",
        "export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4",
        "export PYTHONUNBUFFERED=1",
        "exec " + " \\\n  ".join(_quote(part) for part in cmd),
        "",
    ]
    script.write_text("\n".join(lines))
    script.chmod(0o755)
    if os.geteuid() == 0:
        import shutil

        shutil.chown(script, user=RUN_AS_USER)
    return script


def resolve_sdf_path(sdf_path: str) -> Path:
    raw = Path(sdf_path).expanduser()
    candidates = [raw] if raw.is_absolute() else [PROJECT_ROOT / raw]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            resolved = candidate
        if resolved.is_file():
            return resolved
    raise ScenarioNotFoundError(f"world SDF file not found: {sdf_path}")


def _smoke_command(req: WorldSmokeRequest) -> list[str]:
    sdf_path = resolve_sdf_path(req.sdf_path)
    return [
        "venv/bin/python",
        "-u",
        "-m",
        "databoss_sim.dashboard.world_smoke",
        scenario_arg_for_script(sdf_path),
        "--timeout-s",
        str(req.timeout_s),
    ]


def build_world_smoke_launch_script(job_path: Path, req: WorldSmokeRequest) -> Path:
    script = job_path / "launch.sh"
    cmd = _smoke_command(req)
    lines = [
        "#!/bin/bash",
        f"cd {_quote(str(PROJECT_ROOT))} || exit 1",
        "export PYTHONPATH=src${PYTHONPATH:+:$PYTHONPATH}",
        f"export DATABOSS_JOB_DIR={_quote(str(job_path))}",
        "export PYTHONUNBUFFERED=1",
        "exec " + " \\\n  ".join(_quote(part) for part in cmd),
        "",
    ]
    script.write_text("\n".join(lines))
    script.chmod(0o755)
    if os.geteuid() == 0:
        import shutil

        shutil.chown(script, user=RUN_AS_USER)
    return script


def _make_job_id(scenario_path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{_safe_stem(scenario_path.stem)}"


def _make_smoke_job_id(sdf_path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_world_smoke_{_safe_stem(sdf_path.stem)}"


def start_job(req: LaunchRequest) -> JobRecord:
    with _START_GUARD:
        scenario_path = preflight(req)
        job_id = _make_job_id(scenario_path)

        acquire_lock(job_id, os.getpid())
        try:
            path = job_dir(job_id)
            path.mkdir(parents=True, exist_ok=False)
            script = build_launch_script(path, req)
            console = path / "console.log"
            argv = ["/bin/bash", str(script)]
            if os.geteuid() == 0:
                argv = ["sudo", "-n", "-u", RUN_AS_USER, "/bin/bash", str(script)]

            record = JobRecord(
                job_id=job_id,
                kind="flight",
                status="starting",
                scenario=scenario_arg_for_script(scenario_path),
                command=argv,
                launch_script=str(script),
                pid=None,
                pgid=None,
                started_utc=utc_now(),
                note=req.note,
                ignore_memory_guard=req.ignore_memory_guard,
            )
            write_job_atomic(record)

            log_f = console.open("wb")
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    start_new_session=True,
                )
            finally:
                log_f.close()

            _RUNNING[job_id] = proc
            record.pid = proc.pid
            record.pgid = os.getpgid(proc.pid)
            record.status = "running"
            update_lock_pid(job_id, proc.pid)
            write_job_atomic(record)
            return record
        except Exception:
            release_lock()
            raise


def start_world_smoke_job(req: WorldSmokeRequest) -> JobRecord:
    with _START_GUARD:
        sdf_path = resolve_sdf_path(req.sdf_path)

        hits = scan_host_processes()
        if hits:
            raise LaunchConflictError({
                "message": "PX4/Gazebo/Xvfb process already running; use POST /api/jobs/cleanup first",
                "processes": hits,
            })

        mem_mb = _mem_available_mb()
        if mem_mb is not None and mem_mb < MIN_AVAILABLE_MEM_MB and not req.ignore_memory_guard:
            raise LaunchConflictError({
                "message": "MemAvailable below launch guard",
                "mem_available_mb": mem_mb,
                "min_available_mb": MIN_AVAILABLE_MEM_MB,
            })

        if os.geteuid() == 0:
            sudo_check = subprocess.run(
                ["sudo", "-n", "-u", RUN_AS_USER, "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if sudo_check.returncode != 0:
                raise LaunchServerError(
                    f"cannot run as {RUN_AS_USER} via sudo -n: {sudo_check.stderr.strip()}"
                )

        job_id = _make_smoke_job_id(sdf_path)
        acquire_lock(job_id, os.getpid())
        try:
            path = job_dir(job_id)
            path.mkdir(parents=True, exist_ok=False)
            script = build_world_smoke_launch_script(path, req)
            console = path / "console.log"
            argv = ["/bin/bash", str(script)]
            if os.geteuid() == 0:
                argv = ["sudo", "-n", "-u", RUN_AS_USER, "/bin/bash", str(script)]

            record = JobRecord(
                job_id=job_id,
                kind="world_smoke",
                status="starting",
                scenario=scenario_arg_for_script(sdf_path),
                command=argv,
                launch_script=str(script),
                pid=None,
                pgid=None,
                started_utc=utc_now(),
                note=req.note,
                ignore_memory_guard=req.ignore_memory_guard,
                run_dir=str(path),
            )
            write_job_atomic(record)

            log_f = console.open("wb")
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    start_new_session=True,
                )
            finally:
                log_f.close()

            _RUNNING[job_id] = proc
            record.pid = proc.pid
            record.pgid = os.getpgid(proc.pid)
            record.status = "running"
            update_lock_pid(job_id, proc.pid)
            write_job_atomic(record)
            return record
        except Exception:
            release_lock()
            raise


def _process_group_alive(pgid: int | None) -> bool:
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(pgid: int | None, sig: signal.Signals) -> None:
    if pgid is None:
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def cancel_job(job_id: str) -> JobRecord:
    record = read_job(job_id)
    if record.status == "cancelled":
        return record
    if record.status in {"succeeded", "failed", "crashed"}:
        return record

    if record.cancel_requested_utc is None:
        record.cancel_requested_utc = utc_now()
    record.status = "cancelling"
    record.interrupted_by = record.interrupted_by or "dashboard_cancel"
    write_job_atomic(record)

    _terminate_group(record.pgid, signal.SIGTERM)
    deadline = time.monotonic() + CANCEL_GRACE_S
    proc = _RUNNING.get(job_id)
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        if proc is None and not _process_group_alive(record.pgid):
            break
        time.sleep(1.0)

    hard_kill = False
    if proc is not None and proc.poll() is None:
        hard_kill = True
    if proc is None and _process_group_alive(record.pgid):
        hard_kill = True

    if hard_kill:
        _terminate_group(record.pgid, signal.SIGKILL)
        sweep_stale_processes()
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    remaining = scan_host_processes()
    record = read_job(job_id)
    record.status = "cancelled"
    record.finished_utc = record.finished_utc or utc_now()
    if proc is not None and proc.returncode is not None:
        record.returncode = proc.returncode
    record.hard_kill = hard_kill
    record.post_cancel_assertion = {
        "viz": probe_viz_ports(),
        "remaining_processes": remaining,
        "gz_or_px4_process_remains": bool(remaining),
    }
    write_job_atomic(record)
    _RUNNING.pop(job_id, None)
    release_lock_for(job_id)
    return record


def cleanup_stale_hosts() -> dict:
    sweep_stale_processes()
    return {"ok": True, "remaining_processes": scan_host_processes(), "viz": probe_viz_ports()}
