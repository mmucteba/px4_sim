"""Small on-disk registry for dashboard-launched jobs.

The dashboard polls this module frequently, so all reads stay bounded to
experiments/jobs plus small JSON files. It must not rebuild the experiments
index.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from databoss_sim.dashboard.config import (
    JOB_LOCK_PATH,
    JOB_LOG_CHUNK_BYTES,
    JOBS_DIR,
    PROJECT_ROOT,
    RUNS_DIR,
)

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.runner.run_batch_matrix_pxh import clean_stale_processes  # noqa: E402

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "crashed"}
_LOCK_GUARD = threading.Lock()
_RUNNING: dict[str, subprocess.Popen] = {}


class BusyError(RuntimeError):
    def __init__(self, active_job_id: str | None):
        self.active_job_id = active_job_id
        super().__init__(f"job already active: {active_job_id or 'unknown'}")


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    kind: Literal["flight", "world_smoke", "vehicle_install"] = "flight"
    status: Literal["starting", "running", "succeeded", "failed", "cancelling", "cancelled", "crashed"]
    scenario: str
    command: list[str]
    launch_script: str
    pid: int | None = None
    pgid: int | None = None
    started_utc: str
    finished_utc: str | None = None
    returncode: int | None = None
    run_dir: str | None = None
    note: str = ""
    cancel_requested_utc: str | None = None
    interrupted_by: str | None = None
    ignore_memory_guard: bool = False
    orphaned_from_previous_dashboard: bool = False
    hard_kill: bool = False
    stall_warning: str | None = None
    post_cancel_assertion: dict[str, Any] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def read_job(job_id: str) -> JobRecord:
    with _job_json_path(job_id).open("r") as f:
        return JobRecord.model_validate(json.load(f))


def write_job_atomic(record: JobRecord) -> None:
    directory = job_dir(record.job_id)
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f".job.{os.getpid()}.{threading.get_ident()}.tmp"
    with tmp_path.open("w") as f:
        json.dump(record.model_dump(mode="json"), f, indent=2)
        f.write("\n")
    os.replace(tmp_path, directory / "job.json")


def list_jobs(limit: int = 50) -> list[JobRecord]:
    if not JOBS_DIR.is_dir():
        return []
    entries: list[tuple[float, str]] = []
    with os.scandir(JOBS_DIR) as it:
        for entry in it:
            if not entry.is_dir(follow_symlinks=False):
                continue
            try:
                entries.append((entry.stat(follow_symlinks=False).st_mtime, entry.name))
            except FileNotFoundError:
                continue

    out: list[JobRecord] = []
    for _, name in sorted(entries, reverse=True):
        try:
            out.append(read_job(name))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            continue
        if len(out) >= limit:
            break
    return out


def _read_lock(lock_path: Path = JOB_LOCK_PATH) -> dict[str, Any] | None:
    try:
        with lock_path.open("r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_lock(job_id: str, pid: int, lock_path: Path = JOB_LOCK_PATH, *, owner_kind: str = "flight") -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"job_id": job_id, "pid": pid, "started_utc": utc_now()}
    if owner_kind != "flight":
        payload["kind"] = owner_kind
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload))
        f.write("\n")


def update_lock_pid(job_id: str, pid: int, lock_path: Path = JOB_LOCK_PATH, *, owner_kind: str = "flight") -> None:
    """Replace the just-acquired placeholder pid with the child process pid."""
    with _LOCK_GUARD:
        current = _read_lock(lock_path)
        if current and current.get("job_id") == job_id:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            _write_lock(job_id, pid, lock_path, owner_kind=owner_kind)


def _cmdline_contains(pid: int | None, needle: str) -> bool:
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    return needle in cmdline


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_owner_alive(data: dict[str, Any]) -> bool:
    job_id = str(data.get("job_id") or "")
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return False
    if data.get("kind") == "installer":
        return _process_alive(pid)
    launch_path = str(job_dir(job_id) / "launch.sh")
    return _process_alive(pid) and _cmdline_contains(pid, launch_path)


def acquire_lock(job_id: str, pid: int, lock_path: Path = JOB_LOCK_PATH, *, owner_kind: str = "flight") -> None:
    with _LOCK_GUARD:
        for attempt in range(2):
            try:
                _write_lock(job_id, pid, lock_path, owner_kind=owner_kind)
                return
            except FileExistsError:
                data = _read_lock(lock_path) or {}
                active_job_id = data.get("job_id")
                if _lock_owner_alive(data):
                    raise BusyError(str(active_job_id) if active_job_id else None)

                if active_job_id:
                    try:
                        stale = read_job(str(active_job_id))
                        if stale.status not in TERMINAL_STATUSES:
                            stale.status = "crashed"
                            stale.finished_utc = utc_now()
                            stale.interrupted_by = "stale_lock"
                            write_job_atomic(stale)
                    except (FileNotFoundError, json.JSONDecodeError, ValueError):
                        pass
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                if attempt == 0:
                    continue
                raise


def release_lock(lock_path: Path = JOB_LOCK_PATH) -> None:
    with _LOCK_GUARD:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def release_lock_for(job_id: str, lock_path: Path = JOB_LOCK_PATH) -> None:
    with _LOCK_GUARD:
        current = _read_lock(lock_path)
        if current is None or current.get("job_id") == job_id:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def active_job_id_from_lock(lock_path: Path = JOB_LOCK_PATH) -> str | None:
    data = _read_lock(lock_path)
    if not data:
        return None
    value = data.get("job_id")
    return str(value) if value else None


def _started_ts(record: JobRecord) -> float:
    try:
        return datetime.fromisoformat(record.started_utc).timestamp()
    except ValueError:
        return 0.0


def _finish_from_log_mtime(record: JobRecord) -> str:
    log_path = job_dir(record.job_id) / "console.log"
    try:
        return datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        return utc_now()


def _mark_terminal(record: JobRecord, status: str, returncode: int | None = None) -> JobRecord:
    record.status = status  # type: ignore[assignment]
    record.finished_utc = record.finished_utc or utc_now()
    record.returncode = returncode
    if record.run_dir is None:
        found = find_run_dir_for_job(record)
        if found is not None:
            record.run_dir = str(found)
    write_job_atomic(record)
    _RUNNING.pop(record.job_id, None)
    release_lock_for(record.job_id)
    return record


def reconcile(record: JobRecord) -> JobRecord:
    if record.status not in {"starting", "running", "cancelling"}:
        return record

    proc = _RUNNING.get(record.job_id)
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            if record.status == "running":
                _add_stall_warning(record)
            return record
        if record.status == "cancelling":
            return _mark_terminal(record, "cancelled", rc)
        return _mark_terminal(record, "succeeded" if rc == 0 else "failed", rc)

    launch_path = str(job_dir(record.job_id) / "launch.sh")
    alive = _process_alive(record.pid) and _cmdline_contains(record.pid, launch_path)
    if alive:
        if not record.orphaned_from_previous_dashboard:
            record.orphaned_from_previous_dashboard = True
            write_job_atomic(record)
        if record.status == "running":
            _add_stall_warning(record)
        return record

    record.status = "crashed"
    record.finished_utc = record.finished_utc or _finish_from_log_mtime(record)
    record.interrupted_by = record.interrupted_by or "dashboard_restart_or_process_exit"
    write_job_atomic(record)
    release_lock_for(record.job_id)
    sweep_stale_processes()
    return record


def _add_stall_warning(record: JobRecord) -> None:
    log_path = job_dir(record.job_id) / "console.log"
    try:
        idle_s = time.time() - log_path.stat().st_mtime
    except FileNotFoundError:
        return
    warning = f"console.log has not advanced for {int(idle_s)} seconds" if idle_s > 900 else None
    if record.stall_warning != warning:
        record.stall_warning = warning
        write_job_atomic(record)


def tail_file(path: Path, offset: int, max_bytes: int = JOB_LOG_CHUNK_BYTES) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"offset": offset, "next_offset": offset, "eof": True, "truncated": False, "text": ""}

    safe_offset = max(0, min(offset, size))
    read_bytes = max(0, min(max_bytes, size - safe_offset))
    with path.open("rb") as f:
        f.seek(safe_offset)
        data = f.read(read_bytes)

    next_offset = safe_offset + len(data)
    return {
        "offset": safe_offset,
        "next_offset": next_offset,
        "eof": next_offset >= size,
        "truncated": next_offset < size,
        "text": data.decode(errors="replace"),
    }


def find_run_dir_for_job(record: JobRecord) -> Path | None:
    if record.kind != "flight":
        return None
    if not RUNS_DIR.is_dir():
        return None
    stem = Path(record.scenario).stem
    min_mtime = _started_ts(record) - 5.0
    candidates: list[tuple[float, Path]] = []
    with os.scandir(RUNS_DIR) as it:
        for entry in it:
            if not entry.is_dir(follow_symlinks=False) or stem not in entry.name:
                continue
            try:
                mtime = entry.stat(follow_symlinks=False).st_mtime
            except FileNotFoundError:
                continue
            if mtime >= min_mtime:
                candidates.append((mtime, Path(entry.path)))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def probe_viz_ports() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for port in (9002, 9003):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            out[str(port)] = sock.connect_ex(("127.0.0.1", port)) == 0
    return out


def sweep_stale_processes() -> None:
    clean_stale_processes()
