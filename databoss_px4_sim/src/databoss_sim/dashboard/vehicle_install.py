"""Install composed DATABOSS vehicles into a PX4 tree.

Every mutating helper is parameterized by ``px4_root`` and ``dry_run`` so the
dashboard, CLI, and tests can exercise the same path without touching the live
PX4 checkout.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from databoss_sim.dashboard.config import JOB_LOCK_PATH, PROJECT_ROOT, RUN_AS_USER
from databoss_sim.dashboard.job_registry import (
    BusyError,
    JobRecord,
    _RUNNING,
    acquire_lock,
    active_job_id_from_lock,
    job_dir,
    release_lock,
    release_lock_for,
    update_lock_pid,
    utc_now,
    write_job_atomic,
)

DEFAULT_PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))
DEFAULT_PINS_PATH = PROJECT_ROOT / "deploy" / "px4" / "px4_pins.yaml"
DEFAULT_PATCH_PATH = PROJECT_ROOT / "deploy" / "px4" / "0004-airframes-cmakelists-register.patch"
DEFAULT_MODELS_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "models"
DEFAULT_AIRFRAMES_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "airframes"

PX4_MODELS_REL = Path("Tools/simulation/gz/models")
PX4_AIRFRAMES_REL = Path("ROMFS/px4fmu_common/init.d-posix/airframes")
PX4_AIRFRAMES_CMAKE_REL = PX4_AIRFRAMES_REL / "CMakeLists.txt"
PATCH_REL_TARGET = "ROMFS/px4fmu_common/init.d-posix/airframes/CMakeLists.txt"
_START_GUARD = threading.Lock()


@dataclass(frozen=True)
class StepResult:
    step: str
    status: str
    message: str
    commands: list[str]

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(step: str, message: str, commands: list[str] | None = None) -> StepResult:
    return StepResult(step, "OK", message, commands or [])


def _skip(step: str, message: str, commands: list[str] | None = None) -> StepResult:
    return StepResult(step, "SKIP", message, commands or [])


def _fail(step: str, message: str, commands: list[str] | None = None) -> StepResult:
    return StepResult(step, "FAIL", message, commands or [])


def _dry(step: str, message: str, commands: list[str]) -> StepResult:
    return StepResult(step, "DRY_RUN", message, commands)


def _repo_model_dir(name: str, project_root: Path) -> Path:
    return project_root / "src" / "databoss_sim" / "models" / name


def _repo_airframe_path(filename: str, project_root: Path) -> Path:
    return project_root / "src" / "databoss_sim" / "airframes" / filename


def _discover_airframe_filename(name: str, project_root: Path) -> str | None:
    airframes_dir = project_root / "src" / "databoss_sim" / "airframes"
    matches = sorted(path.name for path in airframes_dir.glob(f"*_gz_{name}"))
    return matches[0] if len(matches) == 1 else None


def _shell_join(cmd: list[str]) -> str:
    return " ".join(shlex_quote(part) for part in cmd)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)


def _git_cmd(px4_root: Path, *args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={px4_root}", "-C", str(px4_root), *args]


def _dircmp_identical(left: Path, right: Path) -> bool:
    if not left.is_dir() or not right.is_dir():
        return False
    cmp = filecmp.dircmp(left, right)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    for filename in cmp.common_files:
        if not filecmp.cmp(left / filename, right / filename, shallow=False):
            return False
    return all(_dircmp_identical(left / name, right / name) for name in cmp.common_dirs)


def _write_access_result(label: str, path: Path, *, project_root: Path) -> StepResult:
    if not path.exists():
        return _fail(label, f"{path} is missing; create it before installing vehicles")
    if os.access(path, os.W_OK):
        return _ok(label, f"{path} is writable")
    if path == project_root / "deploy" / "px4":
        return _fail(
            label,
            f"{path} is not writable by uid {os.geteuid()}; this is project-tree ownership drift. "
            f"scripts/deploy/bootstrap.sh fix_ownership() covers {project_root}, so a real deploy repairs this with "
            f"`sudo chown -R px4:px4 {project_root}`.",
        )
    if label == "preflight write PX4 CMakeLists":
        return _fail(
            label,
            f"{path} is not writable by uid {os.geteuid()}; scripts/deploy/bootstrap.sh fix_ownership() only chowns "
            f"{project_root}, not the PX4 checkout, so fix ownership or permissions on this PX4 tree path separately.",
        )
    return _fail(label, f"{path} is not writable by uid {os.geteuid()}; fix ownership or permissions, then retry")


def _registered_airframe_names(cmake_path: Path) -> list[str]:
    if not cmake_path.is_file():
        return []
    names: list[str] = []
    for line in cmake_path.read_text().splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\d+_[A-Za-z0-9_.-]+", stripped):
            names.append(stripped)
    return names


def preflight(
    name: str,
    *,
    px4_root: Path,
    pins_path: Path,
    project_root: Path = PROJECT_ROOT,
    lock_path: Path = JOB_LOCK_PATH,
    allowed_active_job_id: str | None = None,
) -> list[StepResult]:
    results: list[StepResult] = []
    filename = _discover_airframe_filename(name, project_root)
    model_dir = _repo_model_dir(name, project_root)
    if model_dir.is_dir():
        results.append(_ok("preflight repo model", f"{model_dir} exists"))
    else:
        results.append(_fail("preflight repo model", f"{model_dir} is missing; run the vehicle composer first"))

    if filename is None:
        results.append(_fail(
            "preflight repo airframe",
            f"no unique `*_gz_{name}` file exists under {project_root / 'src/databoss_sim/airframes'}; run the composer first",
        ))
    else:
        airframe_path = _repo_airframe_path(filename, project_root)
        if airframe_path.is_file():
            results.append(_ok("preflight repo airframe", f"{airframe_path} exists"))
        else:
            results.append(_fail("preflight repo airframe", f"{airframe_path} is missing; run the vehicle composer first"))

    write_targets = [
        ("preflight write PX4 models", px4_root / PX4_MODELS_REL),
        ("preflight write PX4 airframes", px4_root / PX4_AIRFRAMES_REL),
        ("preflight write PX4 CMakeLists", px4_root / PX4_AIRFRAMES_CMAKE_REL),
        ("preflight write deploy/px4", pins_path.parent),
    ]
    for label, path in write_targets:
        results.append(_write_access_result(label, path, project_root=project_root))

    cmd = _git_cmd(px4_root, "diff", "--", PATCH_REL_TARGET)
    proc = _run(cmd, timeout=15)
    if proc.returncode == 0:
        results.append(_ok("preflight PX4 git", f"`git -C {px4_root} diff -- {PATCH_REL_TARGET}` works", [_shell_join(cmd)]))
    else:
        results.append(_fail("preflight PX4 git", f"`git -C {px4_root} diff -- {PATCH_REL_TARGET}` failed: {_combined(proc)}", [_shell_join(cmd)]))

    if filename is not None:
        autostart_id = filename.split("_", 1)[0]
        registered = _registered_airframe_names(px4_root / PX4_AIRFRAMES_CMAKE_REL)
        conflict = next((entry for entry in registered if entry.split("_", 1)[0] == autostart_id), None)
        if conflict == filename:
            results.append(_ok("preflight autostart id", f"autostart id {autostart_id} is already registered for {filename}"))
        elif conflict:
            results.append(_fail(
                "preflight autostart id",
                f"autostart id {autostart_id} from {filename} is already registered as {conflict}; choose a free id or regenerate the vehicle",
            ))
        else:
            results.append(_ok("preflight autostart id", f"autostart id {autostart_id} is not registered"))

    active_job_id = active_job_id_from_lock(lock_path)
    if active_job_id and active_job_id == allowed_active_job_id:
        results.append(_ok("preflight flight interlock", f"active job lock is this install job: {active_job_id}"))
    elif active_job_id:
        results.append(_fail("preflight flight interlock", f"flight/job {active_job_id} is active; wait for it to finish before rebuilding PX4"))
    else:
        results.append(_ok("preflight flight interlock", f"no active job lock at {lock_path}"))

    return results


def install_model(
    name: str,
    *,
    px4_root: Path,
    dry_run: bool,
    project_root: Path = PROJECT_ROOT,
) -> StepResult:
    source = _repo_model_dir(name, project_root)
    dest = px4_root / PX4_MODELS_REL / name
    cmd = ["rsync", "-a", "--delete", f"{source}/", f"{dest}/"]
    if not source.is_dir():
        return _fail("install_model", f"{source} is missing; run the composer first", [_shell_join(cmd)])
    if dest.is_dir() and _dircmp_identical(source, dest):
        return _ok("install_model", f"{dest} already matches {source}")
    if dry_run:
        return _dry("install_model", f"would sync {source} to {dest}", [_shell_join(cmd)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(cmd, timeout=60)
    if proc.returncode != 0:
        return _fail("install_model", f"rsync failed: {_combined(proc)}", [_shell_join(cmd)])
    return _ok("install_model", f"synced {source} to {dest}", [_shell_join(cmd)])


def install_airframe(
    filename: str,
    *,
    px4_root: Path,
    dry_run: bool,
    project_root: Path = PROJECT_ROOT,
) -> StepResult:
    source = _repo_airframe_path(filename, project_root)
    dest = px4_root / PX4_AIRFRAMES_REL / filename
    cmd = ["install", "-m", "0644", str(source), str(dest)]
    if not source.is_file():
        return _fail("install_airframe", f"{source} is missing; run the composer first", [_shell_join(cmd)])
    if dest.is_file() and filecmp.cmp(source, dest, shallow=False) and (dest.stat().st_mode & 0o777) == 0o644:
        return _ok("install_airframe", f"{dest} already matches {source} with mode 0644")
    if dry_run:
        return _dry("install_airframe", f"would install {source} to {dest}", [_shell_join(cmd)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(cmd, timeout=20)
    if proc.returncode != 0:
        return _fail("install_airframe", f"install failed: {_combined(proc)}", [_shell_join(cmd)])
    return _ok("install_airframe", f"installed {source} to {dest}", [_shell_join(cmd)])


def _airframe_id(filename: str) -> int:
    prefix = filename.split("_", 1)[0]
    if not prefix.isdigit():
        raise ValueError(f"airframe filename {filename!r} does not start with a numeric autostart id")
    return int(prefix)


def _insert_airframe_line(text: str, filename: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    if any(line.strip() == filename for line in lines):
        return text, False
    start = next((idx for idx, line in enumerate(lines) if line.strip() == "px4_add_romfs_files("), None)
    if start is None:
        raise ValueError("CMakeLists.txt has no px4_add_romfs_files( block")
    end = next((idx for idx in range(start + 1, len(lines)) if lines[idx].strip() == ")"), None)
    if end is None:
        raise ValueError("CMakeLists.txt px4_add_romfs_files( block has no closing parenthesis")

    new_id = _airframe_id(filename)
    gz_indices: list[int] = []
    closest_lower: tuple[int, int] | None = None
    first_gz: int | None = None
    for idx in range(start + 1, end):
        stripped = lines[idx].strip()
        if re.fullmatch(r"\d+_gz_[A-Za-z0-9_.-]+", stripped):
            gz_indices.append(idx)
            first_gz = idx if first_gz is None else first_gz
            existing_id = _airframe_id(stripped)
            if existing_id < new_id and (closest_lower is None or existing_id > closest_lower[0]):
                closest_lower = (existing_id, idx)
    if closest_lower is not None:
        insert_at = closest_lower[1] + 1
    else:
        insert_at = first_gz if first_gz is not None else end

    indent = "\t"
    for idx in reversed(gz_indices):
        match = re.match(r"^(\s*)", lines[idx])
        if match:
            indent = match.group(1)
            break
    lines.insert(insert_at, f"{indent}{filename}\n")
    return "".join(lines), True


def register_airframe(filename: str, *, px4_root: Path, dry_run: bool) -> StepResult:
    path = px4_root / PX4_AIRFRAMES_CMAKE_REL
    if not path.is_file():
        return _fail("register_airframe", f"{path} is missing; create the PX4 airframes CMakeLists.txt first")
    text = path.read_text()
    try:
        updated, changed = _insert_airframe_line(text, filename)
    except ValueError as exc:
        return _fail("register_airframe", str(exc))
    if not changed:
        return _ok("register_airframe", f"{filename} is already registered in {path}")
    command = f"edit {path}: insert {filename} inside px4_add_romfs_files(...)"
    if dry_run:
        return _dry("register_airframe", f"would register {filename} in {path}", [command])
    try:
        path.write_text(updated)
    except OSError as exc:
        return _fail("register_airframe", f"failed to write {path}: {type(exc).__name__}: {exc}", [command])
    return _ok("register_airframe", f"registered {filename} in {path}", [command])


def regenerate_patch(*, px4_root: Path, patch_path: Path, dry_run: bool) -> StepResult:
    cmd = _git_cmd(px4_root, "diff", "--", PATCH_REL_TARGET)
    apply_cmd = _git_cmd(px4_root, "apply", "--check", "-R", str(patch_path))
    commands = [f"{_shell_join(cmd)} > {shlex_quote(str(patch_path))}", _shell_join(apply_cmd)]
    if dry_run:
        return _dry("regenerate_patch", f"would regenerate and reverse-apply-check {patch_path}", commands)
    proc = _run(cmd, timeout=20)
    if proc.returncode != 0:
        return _fail("regenerate_patch", f"git diff failed: {_combined(proc)}", commands)

    try:
        previous = patch_path.read_bytes() if patch_path.exists() else None
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(proc.stdout)
    except OSError as exc:
        return _fail("regenerate_patch", f"failed to write {patch_path}: {type(exc).__name__}: {exc}", commands)
    verify = _run(apply_cmd, timeout=20)
    if verify.returncode != 0:
        if previous is None:
            try:
                patch_path.unlink()
            except FileNotFoundError:
                pass
        else:
            try:
                patch_path.write_bytes(previous)
            except OSError as exc:
                return _fail(
                    "regenerate_patch",
                    f"reverse apply check failed and restoring previous patch content also failed: {type(exc).__name__}: {exc}",
                    commands,
                )
        return _fail(
            "regenerate_patch",
            f"reverse apply check failed; restored previous patch content: {_combined(verify)}",
            commands,
        )
    if previous == patch_path.read_bytes():
        return _ok("regenerate_patch", f"{patch_path} already matches the PX4 CMakeLists diff and reverse-applies cleanly", commands)
    return _ok("regenerate_patch", f"wrote {patch_path} and verified it reverse-applies cleanly", commands)


def _load_yaml_pins(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a YAML mapping")
    return data


def _bootstrap_parser_cmd(key: str, pins_path: Path) -> list[str]:
    awk = (
        r"/^[^[:space:]#][^:]*:[[:space:]]*($|#)/ {"
        r'current = $0; sub(/:.*/, "", current); in_key = (current == key); next'
        r"} "
        r"in_key && /^[[:space:]]*-[[:space:]]*/ {"
        r'item = $0; sub(/^[[:space:]]*-[[:space:]]*/, "", item); '
        r'sub(/[[:space:]]*#.*/, "", item); gsub(/^[[:space:]]+|[[:space:]]+$/, "", item); '
        r'if (item != "" && item !~ /:/) { print item }'
        r"}"
    )
    return ["awk", "-v", f"key={key}", awk, str(pins_path)]


def _bootstrap_scalar_sequence(key: str, pins_path: Path) -> list[str]:
    cmd = _bootstrap_parser_cmd(key, pins_path)
    proc = _run(cmd, timeout=10)
    if proc.returncode != 0:
        raise ValueError(f"bootstrap awk parser failed for {key}: {_combined(proc)}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _insert_yaml_scalar_item(text: str, key: str, value: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    key_re = re.compile(rf"^{re.escape(key)}:\s*(?:#.*)?$")
    key_idx = next((idx for idx, line in enumerate(lines) if key_re.match(line.rstrip("\n"))), None)
    if key_idx is None:
        suffix = "" if text.endswith("\n") or not text else "\n"
        return text + suffix + f"{key}:\n  - {value}\n", True

    next_top = next(
        (idx for idx in range(key_idx + 1, len(lines)) if re.match(r"^[^ \t#][^:]*:", lines[idx])),
        len(lines),
    )
    item_indices: list[int] = []
    existing: set[str] = set()
    for idx in range(key_idx + 1, next_top):
        match = re.match(r"^(\s*)-\s*(.*?)\s*(?:#.*)?$", lines[idx].rstrip("\n"))
        if not match:
            continue
        item = match.group(2).strip()
        if item and ":" not in item:
            item_indices.append(idx)
            existing.add(item)
    if value in existing:
        return text, False
    indent = "  "
    if item_indices:
        indent = re.match(r"^(\s*)", lines[item_indices[-1]]).group(1)  # type: ignore[union-attr]
    insert_at = item_indices[-1] + 1 if item_indices else next_top
    lines.insert(insert_at, f"{indent}- {value}\n")
    return "".join(lines), True


def _validate_pins_sequences(pins_path: Path, expected_model: str, expected_airframe: str) -> None:
    data = _load_yaml_pins(pins_path)
    for key, expected in (("models", expected_model), ("airframes", expected_airframe)):
        values = data.get(key)
        if not isinstance(values, list) or expected not in values:
            raise ValueError(f"PyYAML did not find {expected!r} in {key}: {values!r}")
        awk_values = _bootstrap_scalar_sequence(key, pins_path)
        if expected not in awk_values:
            raise ValueError(f"bootstrap awk parser did not find {expected!r} in {key}: {awk_values!r}")


def add_pins_entries(name: str, filename: str, *, pins_path: Path, dry_run: bool) -> StepResult:
    model_entry = name
    airframe_entry = filename
    commands = [
        f"edit {pins_path}: ensure models contains {model_entry}",
        f"edit {pins_path}: ensure airframes contains {airframe_entry}",
        _shell_join(_bootstrap_parser_cmd("models", pins_path)),
        _shell_join(_bootstrap_parser_cmd("airframes", pins_path)),
    ]
    if not pins_path.is_file():
        return _fail("add_pins_entries", f"{pins_path} is missing", commands)
    try:
        original = pins_path.read_text()
    except OSError as exc:
        return _fail("add_pins_entries", f"failed to read {pins_path}: {type(exc).__name__}: {exc}", commands)
    updated, model_changed = _insert_yaml_scalar_item(original, "models", model_entry)
    updated, airframe_changed = _insert_yaml_scalar_item(updated, "airframes", airframe_entry)
    if not model_changed and not airframe_changed:
        try:
            _validate_pins_sequences(pins_path, model_entry, airframe_entry)
        except ValueError as exc:
            return _fail("add_pins_entries", str(exc), commands)
        return _ok("add_pins_entries", f"{model_entry} and {airframe_entry} are already present in {pins_path}", commands)
    if dry_run:
        return _dry("add_pins_entries", f"would append missing pins entries to {pins_path}", commands)
    try:
        previous = pins_path.read_bytes()
        pins_path.write_text(updated)
    except OSError as exc:
        return _fail("add_pins_entries", f"failed to write {pins_path}: {type(exc).__name__}: {exc}", commands)
    try:
        _validate_pins_sequences(pins_path, model_entry, airframe_entry)
    except ValueError as exc:
        try:
            pins_path.write_bytes(previous)
        except OSError as restore_exc:
            return _fail(
                "add_pins_entries",
                f"pins validation failed and restoring previous content also failed: {type(restore_exc).__name__}: {restore_exc}",
                commands,
            )
        return _fail("add_pins_entries", f"pins validation failed; restored previous content: {exc}", commands)
    return _ok("add_pins_entries", f"updated {pins_path} and validated with PyYAML plus bootstrap awk parser", commands)


def build_px4(*, px4_root: Path, build_target: str, dry_run: bool) -> StepResult:
    cmd = ["make", build_target]
    if dry_run:
        return _dry("build_px4", f"would run PX4 build target {build_target}", [_shell_join(cmd)])
    proc = _run(cmd, cwd=px4_root, timeout=60 * 60)
    if proc.returncode != 0:
        return _fail("build_px4", f"PX4 build failed: {_combined(proc)}", [_shell_join(cmd)])
    return _ok("build_px4", f"PX4 build target {build_target} completed", [_shell_join(cmd)])


def run_verification(
    *,
    px4_root: Path,
    pins_path: Path,
    project_root: Path = PROJECT_ROOT,
    dry_run: bool,
) -> StepResult:
    python = sys.executable
    deploy_cmd = [python, str(project_root / "scripts" / "deploy" / "check_deployment.py"), "--px4-root", str(px4_root)]
    sync_cmd = [
        python,
        str(project_root / "scripts" / "analysis" / "check_model_sync_and_fov.py"),
        "--px4-models-dir",
        str(px4_root / PX4_MODELS_REL),
    ]
    commands = [_shell_join(deploy_cmd), _shell_join(sync_cmd)]
    if dry_run:
        return _dry("run_verification", "would run deployment and model/FOV checks", commands)
    env = {**os.environ, "DATABOSS_PX4_PINS_PATH": str(pins_path)}
    deploy = _run(deploy_cmd, env=env, timeout=120)
    sync = _run(sync_cmd, env=env, timeout=120)
    failures = []
    if deploy.returncode != 0:
        failures.append(f"check_deployment failed: {_combined(deploy)}")
    if sync.returncode != 0:
        failures.append(f"check_model_sync_and_fov failed: {_combined(sync)}")
    if failures:
        return _fail("run_verification", "\n".join(failures), commands)
    return _ok("run_verification", "deployment and model/FOV checks passed", commands)


def install_vehicle(
    name: str,
    *,
    px4_root: Path = DEFAULT_PX4_ROOT,
    pins_path: Path = DEFAULT_PINS_PATH,
    patch_path: Path = DEFAULT_PATCH_PATH,
    project_root: Path = PROJECT_ROOT,
    airframe_filename: str | None = None,
    build_target: str = "px4_sitl_default",
    dry_run: bool = False,
    skip_build: bool = False,
    lock_path: Path = JOB_LOCK_PATH,
    preacquired_lock_job_id: str | None = None,
) -> list[StepResult]:
    filename = airframe_filename or _discover_airframe_filename(name, project_root)
    results = preflight(
        name,
        px4_root=px4_root,
        pins_path=pins_path,
        project_root=project_root,
        lock_path=lock_path,
        allowed_active_job_id=preacquired_lock_job_id,
    )
    if filename is None:
        return results
    if any(result.status == "FAIL" for result in results) and not dry_run:
        return results

    lock_job_id = f"vehicle_install:{name}:{os.getpid()}"
    lock_acquired = False
    if preacquired_lock_job_id and not dry_run:
        results.append(_ok("install lock", f"using pre-acquired dashboard job lock {preacquired_lock_job_id}"))
    elif not dry_run:
        try:
            acquire_lock(lock_job_id, os.getpid(), lock_path, owner_kind="installer")
            lock_acquired = True
            results.append(_ok("install lock", f"acquired install lock {lock_path} as {lock_job_id}"))
        except BusyError as exc:
            results.append(_fail("install lock", f"flight/job {exc.active_job_id or 'unknown'} is active; refusing to install"))
            return results
    else:
        results.append(_dry("install lock", f"would acquire install lock {lock_path}", [f"acquire_lock({lock_job_id!r}, {os.getpid()})"]))

    try:
        for step in (
            install_model(name, px4_root=px4_root, dry_run=dry_run, project_root=project_root),
            install_airframe(filename, px4_root=px4_root, dry_run=dry_run, project_root=project_root),
            register_airframe(filename, px4_root=px4_root, dry_run=dry_run),
            regenerate_patch(px4_root=px4_root, patch_path=patch_path, dry_run=dry_run),
            add_pins_entries(name, filename, pins_path=pins_path, dry_run=dry_run),
        ):
            results.append(step)
            if step.status == "FAIL" and not dry_run:
                return results

        if skip_build:
            results.append(_skip("build_px4", "--skip-build requested; PX4 was not rebuilt"))
            results.append(_skip("run_verification", "--skip-build requested; run verification after a successful PX4 rebuild"))
        else:
            build_result = build_px4(px4_root=px4_root, build_target=build_target, dry_run=dry_run)
            results.append(build_result)
            if build_result.status == "FAIL" and not dry_run:
                return results
            results.append(run_verification(px4_root=px4_root, pins_path=pins_path, project_root=project_root, dry_run=dry_run))
        return results
    finally:
        if lock_acquired:
            release_lock_for(lock_job_id, lock_path)


def all_ok(results: list[StepResult]) -> bool:
    return not any(result.status == "FAIL" for result in results)


def _safe_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "vehicle"


def _make_install_job_id(name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_vehicle_install_{_safe_stem(name)}"


def _build_install_script(job_path: Path, name: str, job_id: str) -> Path:
    script = job_path / "launch.sh"
    cmd = [
        "venv/bin/python",
        "-u",
        "-m",
        "databoss_sim.dashboard.vehicle_install",
        "--name",
        name,
        "--json",
        "--preacquired-lock-job-id",
        job_id,
    ]
    lines = [
        "#!/bin/bash",
        f"cd {_shell_join([str(PROJECT_ROOT)])} || exit 1",
        "export PYTHONPATH=src${PYTHONPATH:+:$PYTHONPATH}",
        f"export DATABOSS_JOB_DIR={_shell_join([str(job_path)])}",
        "export PYTHONUNBUFFERED=1",
        "exec " + " \\\n  ".join(shlex_quote(part) for part in cmd),
        "",
    ]
    script.write_text("\n".join(lines))
    script.chmod(0o755)
    if os.geteuid() == 0:
        shutil.chown(script, user=RUN_AS_USER)
    return script


def start_vehicle_install_job(name: str) -> JobRecord:
    """Start a dashboard-managed vehicle install process.

    The request thread only creates the job record and child process. The
    expensive install/build/verification path runs from launch.sh and writes
    to console.log, matching flight and world-smoke jobs.
    """
    with _START_GUARD:
        if _discover_airframe_filename(name, PROJECT_ROOT) is None:
            raise FileNotFoundError(f"no generated repo airframe for vehicle: {name}")

        job_id = _make_install_job_id(name)
        acquire_lock(job_id, os.getpid(), owner_kind="installer")
        try:
            path = job_dir(job_id)
            path.mkdir(parents=True, exist_ok=False)
            script = _build_install_script(path, name, job_id)
            console = path / "console.log"
            argv = ["/bin/bash", str(script)]
            if os.geteuid() == 0:
                argv = ["sudo", "-n", "-u", RUN_AS_USER, "/bin/bash", str(script)]

            record = JobRecord(
                job_id=job_id,
                kind="vehicle_install",
                status="starting",
                scenario=name,
                command=argv,
                launch_script=str(script),
                pid=None,
                pgid=None,
                started_utc=utc_now(),
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
            update_lock_pid(job_id, proc.pid, owner_kind="installer")
            write_job_atomic(record)
            return record
        except Exception:
            release_lock()
            raise


def _print_markdown(results: list[StepResult]) -> None:
    print("| step | status | message |")
    print("| --- | --- | --- |")
    for result in results:
        message = result.message.replace("\n", "<br>")
        print(f"| {result.step} | {result.status} | {message} |")
        if result.commands:
            print(f"| {result.step} commands |  | `{' ; '.join(result.commands)}` |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="DATABOSS model directory name to install")
    parser.add_argument("--px4-root", type=Path, default=DEFAULT_PX4_ROOT)
    parser.add_argument("--pins-path", type=Path, default=DEFAULT_PINS_PATH)
    parser.add_argument("--patch-path", type=Path, default=DEFAULT_PATCH_PATH)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--lock-path", type=Path, default=JOB_LOCK_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--preacquired-lock-job-id", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    results = install_vehicle(
        args.name,
        px4_root=args.px4_root.expanduser().resolve(),
        pins_path=args.pins_path.expanduser().resolve(),
        patch_path=args.patch_path.expanduser().resolve(),
        project_root=args.project_root.expanduser().resolve(),
        dry_run=args.dry_run,
        skip_build=args.skip_build,
        lock_path=args.lock_path.expanduser().resolve(),
        preacquired_lock_job_id=args.preacquired_lock_job_id,
    )
    if args.json:
        print(json.dumps([result.asdict() for result in results], indent=2))
    else:
        _print_markdown(results)
    return 1 if (not args.dry_run and not all_ok(results)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
