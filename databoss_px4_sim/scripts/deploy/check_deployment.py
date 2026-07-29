#!/usr/bin/env python3
"""Check that a host can reproduce DATABOSS PX4/Gazebo results.

    venv/bin/python scripts/deploy/check_deployment.py [--json] [--px4-root PATH]

This is intended to be the final bootstrap check. It validates deployed effects
in the PX4 tree, not just the presence of patch files.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

import yaml  # noqa: E402

from scripts.analysis.check_model_sync_and_fov import _sha256  # noqa: E402

DEFAULT_PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))
PINS_PATH = Path(os.environ.get("DATABOSS_PX4_PINS_PATH", PROJECT_ROOT / "deploy" / "px4" / "px4_pins.yaml"))
STATUS_ORDER = ("OK", "FAIL", "WARN", "SKIP")
COMPILED_PATCH_FILES = {"0001-gz-bridge-sim-gps-used.patch"}
RUN_EXEC_USER = "px4"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _load_pins() -> dict[str, Any]:
    with PINS_PATH.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{PINS_PATH} did not contain a YAML mapping")
    return data


def _resolve_px4_root(arg_px4_root: Path | None) -> Path:
    if arg_px4_root is not None:
        return arg_px4_root.expanduser().resolve()
    return DEFAULT_PX4_ROOT.expanduser().resolve()


def _patch_target_path(px4_root: Path, pins: dict[str, Any], patch: dict[str, str]) -> Path:
    target = Path(patch["target"])
    if patch.get("apply_in") == "gz_models_submodule":
        return px4_root / pins["gz_models_submodule"]["path"] / target
    return px4_root / target


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _cpp_function_body(text: str, qualified_name: str) -> str | None:
    match = re.search(
        rf"\b(?:void|auto|int|bool)\s+{re.escape(qualified_name)}\s*\([^)]*\)\s*\{{",
        text,
        flags=re.MULTILINE,
    )
    if not match:
        return None

    depth = 0
    for idx in range(match.end() - 1, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[match.start(): idx + 1]
    return None


def _run_command(cmd: list[str], cwd: Path | None = None, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _run_command_as_user(
    cmd: list[str],
    user: str,
    cwd: Path | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str] | None:
    def run(candidate: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return _run_command(candidate, cwd=cwd, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(
                candidate,
                124 if isinstance(exc, subprocess.TimeoutExpired) else 1,
                "",
                f"{type(exc).__name__}: {exc}",
            )

    try:
        target = pwd.getpwnam(user)
    except KeyError:
        return None

    if os.geteuid() == target.pw_uid:
        return run(cmd)

    runuser_path = shutil.which("runuser")
    if os.geteuid() == 0 and runuser_path:
        return run([runuser_path, "-u", user, "--", *cmd])

    sudo_path = shutil.which("sudo")
    if sudo_path:
        return run([sudo_path, "-n", "-u", user, *cmd])

    return None


def _combined_output(proc: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)


def _first_output_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("ninja: Entering directory"):
            return stripped
    return next((line.strip() for line in output.splitlines() if line.strip()), "")


def _px4_ninja_dry_run_command(build_dir: Path, extra_args: list[str] | None = None) -> list[str] | None:
    ninja_args = ["ninja", "-C", str(build_dir), *(extra_args or []), "-n"]

    try:
        owner = pwd.getpwuid(build_dir.stat().st_uid)
    except (KeyError, OSError):
        return ninja_args

    if owner.pw_name != "px4" or os.geteuid() == owner.pw_uid:
        return ninja_args

    runuser_path = shutil.which("runuser")
    if os.geteuid() == 0 and runuser_path:
        return [runuser_path, "-u", "px4", "--", *ninja_args]

    sudo_path = shutil.which("sudo")
    if sudo_path:
        return [sudo_path, "-n", "-u", "px4", *ninja_args]

    return None


def _ninja_pending_target_count(output: str) -> int | None:
    totals = []
    for line in output.splitlines():
        match = re.match(r"\[\s*\d+/(\d+)\]", line.strip())
        if match:
            totals.append(int(match.group(1)))
    if totals:
        return max(totals)
    return None


def _first_ninja_explain_line(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ninja explain:"):
            return stripped
    return None


def _check_px4_build_up_to_date(px4_root: Path, pins: dict[str, Any]) -> CheckResult:
    build_target = pins["px4"]["build_target"]
    build_dir = px4_root / "build" / build_target
    if not build_dir.is_dir():
        return CheckResult(
            "px4 build up to date",
            "FAIL",
            f"{build_dir} is missing - PX4 was never built; run `make {build_target}` to completion first",
        )

    if shutil.which("ninja") is None:
        return CheckResult("px4 build up to date", "SKIP", "ninja executable is not on PATH")

    dry_run_cmd = _px4_ninja_dry_run_command(build_dir)
    if dry_run_cmd is None:
        return CheckResult(
            "px4 build up to date",
            "SKIP",
            f"{build_dir} is owned by px4, but sudo/runuser is unavailable to run the dry-run as px4",
        )

    try:
        proc = _run_command(dry_run_cmd, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            "px4 build up to date",
            "FAIL",
            f"PX4 build dry-run failed: {type(exc).__name__}: {exc}",
        )
    output = _combined_output(proc)
    if proc.returncode != 0:
        detail = _first_output_line(output) or f"ninja dry-run exited {proc.returncode}"
        return CheckResult("px4 build up to date", "FAIL", f"PX4 build dry-run failed: {detail}")

    if "no work to do" in output.lower():
        return CheckResult("px4 build up to date", "OK", f"`ninja -C {build_dir} -n` reports no work to do")

    pending = _ninja_pending_target_count(output)
    explain = None
    explain_cmd = _px4_ninja_dry_run_command(build_dir, ["-d", "explain"])
    if explain_cmd is not None:
        try:
            explain_proc = _run_command(explain_cmd, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            explain = None
        else:
            explain = _first_ninja_explain_line(_combined_output(explain_proc))

    count = str(pending) if pending is not None else "an unknown number of"
    plural = "target" if pending == 1 else "targets"
    detail = (
        f"PX4 build is {count} {plural} behind; every run invokes `make px4_sitl` and "
        "the runner's startup timeout will interrupt it, so no run can arm. "
        f"Run `make {build_target}` to completion first."
    )
    if explain:
        detail = f"{detail} First ninja reason: {explain}"
    return CheckResult("px4 build up to date", "FAIL", detail)


def _check_compiled_freshness(px4_root: Path, pins: dict[str, Any], binary: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    patches_by_file = {patch["file"]: patch for patch in pins["patches"]}
    build_target = pins["px4"]["build_target"]

    for patch_file in sorted(COMPILED_PATCH_FILES):
        patch = patches_by_file[patch_file]
        source = _patch_target_path(px4_root, pins, patch)
        if not source.is_file():
            results.append(CheckResult(
                "px4 compiled GZBridge.cpp",
                "FAIL",
                f"cannot compare compiled freshness; {source} is missing",
            ))
        elif source.stat().st_mtime > binary.stat().st_mtime:
            results.append(CheckResult(
                "px4 compiled GZBridge.cpp",
                "FAIL",
                f"GZBridge.cpp is newer than the built binary - rerun `make {build_target}` "
                "or GNSS loss will silently not work",
            ))
        else:
            results.append(CheckResult(
                "px4 compiled GZBridge.cpp",
                "OK",
                f"{binary} is newer than or same age as {source}",
            ))

    return results


def _check_staged_airframes(px4_root: Path, pins: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    build_target = pins["px4"]["build_target"]
    romfs_dir = px4_root / "ROMFS" / "px4fmu_common" / "init.d-posix" / "airframes"
    staged_dir = px4_root / "build" / build_target / "etc" / "init.d-posix" / "airframes"

    for name in pins["airframes"]:
        romfs_path = romfs_dir / name
        staged_path = staged_dir / name
        romfs_hash = _sha256(romfs_path)
        staged_hash = _sha256(staged_path)
        if romfs_hash is None:
            results.append(CheckResult(
                f"px4 staged {name}",
                "FAIL",
                f"{romfs_path} is missing from PX4 ROMFS",
            ))
        elif staged_hash is None:
            results.append(CheckResult(
                f"px4 staged {name}",
                "FAIL",
                f"{staged_path} is missing - rerun `make {build_target}` so the airframe is staged",
            ))
        elif romfs_hash != staged_hash:
            results.append(CheckResult(
                f"px4 staged {name}",
                "FAIL",
                f"staged airframe differs from ROMFS source - rerun `make {build_target}`; "
                f"source={romfs_path} sha256={romfs_hash[:12]}, staged={staged_path} sha256={staged_hash[:12]}",
            ))
        else:
            results.append(CheckResult(
                f"px4 staged {name}",
                "OK",
                f"{staged_path} matches {romfs_path} sha256={romfs_hash[:12]}",
            ))

    return results


def _check_patch_effects(px4_root: Path, pins: dict[str, Any]) -> list[CheckResult]:
    patches_by_file = {patch["file"]: patch for patch in pins["patches"]}
    results: list[CheckResult] = []

    patch = patches_by_file["0001-gz-bridge-sim-gps-used.patch"]
    path = _patch_target_path(px4_root, pins, patch)
    text = _read_text(path)
    if text is None:
        results.append(CheckResult(
            "0001 sim gps polling",
            "FAIL",
            f"{patch['file']} is not deployed; {path} is missing - GNSS loss will not actually happen silently",
        ))
    else:
        body = _cpp_function_body(text, "GZBridge::navSatCallback")
        if body and "_sim_gps_used.update()" in body:
            results.append(CheckResult("0001 sim gps polling", "OK", f"{path} polls SIM_GPS_USED in navSatCallback"))
        else:
            results.append(CheckResult(
                "0001 sim gps polling",
                "FAIL",
                f"{patch['file']} is missing in {path} - GNSS loss will not actually happen silently",
            ))

    patch = patches_by_file["0002-server-config-wind-effects.patch"]
    path = _patch_target_path(px4_root, pins, patch)
    text = _read_text(path)
    if text and "gz-sim-wind-effects-system" in text:
        results.append(CheckResult("0002 wind system plugin", "OK", f"{path} loads gz-sim-wind-effects-system"))
    else:
        results.append(CheckResult(
            "0002 wind system plugin",
            "FAIL",
            f"{patch['file']} is missing in {path} - wind will not apply silently; every wind scenario is invalid",
        ))

    patch = patches_by_file["0003-x500-base-enable-wind.patch"]
    path = _patch_target_path(px4_root, pins, patch)
    text = _read_text(path)
    if text and "<enable_wind>true</enable_wind>" in text:
        results.append(CheckResult("0003 x500 base wind response", "OK", f"{path} enables wind on x500_base"))
    else:
        results.append(CheckResult(
            "0003 x500 base wind response",
            "FAIL",
            f"{patch['file']} is missing in {path} - vehicle will ignore wind silently; every wind scenario is invalid",
        ))

    patch = patches_by_file["0004-airframes-cmakelists-register.patch"]
    path = _patch_target_path(px4_root, pins, patch)
    text = _read_text(path)
    missing = [name for name in pins["airframes"] if not text or name not in text]
    if not missing:
        results.append(CheckResult("0004 airframe registration", "OK", f"{path} registers {', '.join(pins['airframes'])}"))
    else:
        results.append(CheckResult(
            "0004 airframe registration",
            "FAIL",
            f"{patch['file']} is missing in {path} - airframes {', '.join(missing)} will not exist in PX4 ROMFS",
        ))

    return results


def _check_px4(px4_root: Path, pins: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []

    if px4_root.is_dir():
        results.append(CheckResult("px4 root", "OK", str(px4_root)))
    else:
        results.append(CheckResult("px4 root", "FAIL", f"{px4_root} does not exist"))

    git_dir = px4_root / ".git"
    if git_dir.exists():
        results.append(CheckResult("px4 git repo", "OK", f"{px4_root} has .git"))
        proc = _run_command(["git", "rev-parse", "HEAD"], cwd=px4_root)
        head = proc.stdout.strip()
        pinned = pins["px4"]["commit"]
        if proc.returncode != 0 or not head:
            results.append(CheckResult("px4 head", "WARN", f"could not read HEAD: {proc.stderr.strip()}"))
        elif head == pinned:
            results.append(CheckResult("px4 head", "OK", f"HEAD matches pinned commit {pinned}"))
        else:
            results.append(CheckResult("px4 head", "WARN", f"HEAD {head} differs from pinned {pinned}; newer PX4 may be deliberate"))
    else:
        results.append(CheckResult("px4 git repo", "FAIL", f"{px4_root} is not a git repo"))
        results.append(CheckResult("px4 head", "SKIP", "no git repo"))

    build_target = pins["px4"]["build_target"]
    results.append(_check_px4_build_up_to_date(px4_root, pins))

    binary = px4_root / "build" / build_target / "bin" / "px4"
    if binary.is_file():
        results.append(CheckResult("px4 binary", "OK", str(binary)))
    else:
        results.append(CheckResult("px4 binary", "FAIL", f"{binary} is missing - rerun make {build_target}"))
        results.append(CheckResult("px4 compiled GZBridge.cpp", "SKIP", "binary missing"))
        results.extend(_check_staged_airframes(px4_root, pins))
        return results

    results.extend(_check_compiled_freshness(px4_root, pins, binary))
    results.extend(_check_staged_airframes(px4_root, pins))

    return results


def _check_airframes(px4_root: Path, pins: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    databoss_dir = PROJECT_ROOT / "src" / "databoss_sim" / "airframes"
    px4_dir = px4_root / "ROMFS" / "px4fmu_common" / "init.d-posix" / "airframes"

    for name in pins["airframes"]:
        databoss_path = databoss_dir / name
        px4_path = px4_dir / name
        databoss_hash = _sha256(databoss_path)
        px4_hash = _sha256(px4_path)
        if databoss_hash is None:
            results.append(CheckResult(name, "FAIL", f"{databoss_path} is missing"))
        elif px4_hash is None:
            results.append(CheckResult(name, "FAIL", f"{px4_path} is missing from PX4 ROMFS"))
        elif databoss_hash != px4_hash:
            results.append(CheckResult(name, "FAIL", f"{databoss_path} and {px4_path} differ"))
        else:
            results.append(CheckResult(name, "OK", f"byte-identical sha256={databoss_hash[:12]}"))

    return results


def _check_models(px4_root: Path, pins: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    databoss_dir = PROJECT_ROOT / "src" / "databoss_sim" / "models"
    px4_models_dir = px4_root / pins["gz_models_submodule"]["path"] / "models"
    note = "presence only; content drift is checked by scripts/analysis/check_model_sync_and_fov.py"

    for name in pins["models"]:
        databoss_model = databoss_dir / name
        px4_model = px4_models_dir / name
        missing = []
        if not databoss_model.is_dir():
            missing.append(str(databoss_model))
        if not px4_model.is_dir():
            missing.append(str(px4_model))
        if missing:
            results.append(CheckResult(name, "FAIL", f"missing {', '.join(missing)}; {note}"))
        else:
            results.append(CheckResult(name, "OK", f"exists in DATABOSS and PX4; {note}"))

    return results


def _check_gazebo(pins: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    gz_path = shutil.which("gz")
    if gz_path is None:
        results.append(CheckResult("gz on PATH", "FAIL", "gz executable is not on PATH"))
        results.append(CheckResult("gz sim version", "SKIP", "gz executable missing"))
    else:
        results.append(CheckResult("gz on PATH", "OK", gz_path))
        proc = _run_command(["gz", "sim", "--versions"], timeout=8)
        output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
        expected = str(pins["gazebo"]["gz_sim_version"])
        expected_major = expected.split(".", 1)[0]
        matches = re.findall(r"(?:gz-sim|Gazebo Sim|sim).*?(\d+)\.", output, flags=re.IGNORECASE)
        if not matches:
            matches = re.findall(r"\b(\d+)\.\d+(?:\.\d+)?\b", output)
        if proc.returncode != 0:
            results.append(CheckResult("gz sim version", "WARN", f"gz sim --versions failed: {output}"))
        elif expected_major in matches:
            results.append(CheckResult("gz sim version", "OK", f"reported Gazebo Sim major {expected_major}; pinned {expected}"))
        else:
            results.append(CheckResult("gz sim version", "WARN", f"expected Gazebo Sim major {expected_major}; output: {output}"))

    xvfb = shutil.which("Xvfb")
    if xvfb:
        results.append(CheckResult("Xvfb on PATH", "OK", xvfb))
    else:
        results.append(CheckResult("Xvfb on PATH", "FAIL", "Xvfb executable is not on PATH"))

    web_config = Path(pins["gazebo"]["web_launch_config"])
    if web_config.is_file():
        results.append(CheckResult("web launch config", "OK", str(web_config)))
    else:
        results.append(CheckResult("web launch config", "FAIL", f"{web_config} is missing"))

    return results


def _python_import_check(python_path: Path, modules: list[str]) -> tuple[str, str, dict[str, Any]]:
    if not python_path.is_file():
        return "FAIL", f"{python_path} is missing", {}

    code = """
import importlib
import json
import sys

modules = sys.argv[1].split(",")
missing = {}
versions = {}
for name in modules:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        missing[name] = f"{type(exc).__name__}: {exc}"
    else:
        if name == "cv2":
            versions["cv2"] = getattr(module, "__version__", None)

print(json.dumps({"missing": missing, "versions": versions}, sort_keys=True))
sys.exit(1 if missing else 0)
"""
    proc = _run_command([str(python_path), "-c", code, ",".join(modules)], timeout=30)
    raw = proc.stdout.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    missing = payload.get("missing") or {}
    versions = payload.get("versions") or {}
    if missing:
        detail = "; ".join(f"{name}: {error}" for name, error in missing.items())
        return "FAIL", detail, versions
    if proc.returncode != 0:
        return "FAIL", proc.stderr.strip() or raw or f"{python_path} import check failed", versions

    cv2_version = versions.get("cv2", "unknown")
    return "OK", f"imports succeeded; cv2={cv2_version}", versions


def _path_stat_detail(path: Path) -> str:
    try:
        st = path.stat()
    except OSError as exc:
        return f"target stat error={type(exc).__name__}: {exc}"
    return f"target owner={st.st_uid}:{st.st_gid}, mode={stat.filemode(st.st_mode)}"


def _check_python() -> list[CheckResult]:
    results: list[CheckResult] = []
    main_python = PROJECT_ROOT / "venv" / "bin" / "python"
    bridge_python = PROJECT_ROOT / "venv_bridge" / "bin" / "python"
    main_modules = ["numpy", "pandas", "matplotlib", "cv2", "pyulog", "pymavlink", "yaml", "fastapi"]
    bridge_modules = ["gz.transport13", "gz.msgs10", "cv2", "pymavlink"]

    main_status, main_detail, main_versions = _python_import_check(main_python, main_modules)
    bridge_status, bridge_detail, bridge_versions = _python_import_check(bridge_python, bridge_modules)
    results.append(CheckResult("main venv imports", main_status, main_detail))
    results.append(CheckResult("bridge venv imports", bridge_status, bridge_detail))

    main_cv2 = main_versions.get("cv2")
    bridge_cv2 = bridge_versions.get("cv2")
    if not main_cv2 or not bridge_cv2:
        results.append(CheckResult("cv2 split", "SKIP", f"main cv2={main_cv2}, bridge cv2={bridge_cv2}"))
    elif main_cv2 == bridge_cv2:
        results.append(CheckResult(
            "cv2 split",
            "WARN",
            f"main cv2={main_cv2}, bridge cv2={bridge_cv2}; versions are equal but the split is deliberate",
        ))
    else:
        results.append(CheckResult("cv2 split", "OK", f"main cv2={main_cv2}, bridge cv2={bridge_cv2}"))

    return results


def _check_paths() -> list[CheckResult]:
    results: list[CheckResult] = []
    for relative in ("experiments", "generated_worlds"):
        path = PROJECT_ROOT / relative
        target = path.resolve(strict=False)
        if not path.is_dir():
            results.append(CheckResult(relative, "FAIL", f"{path} resolves to {target}; path is missing"))
            continue
        if not os.access(path, os.W_OK):
            detail = (
                f"{path} resolves to {target}; write probe failed for target {target}; "
                f"{_path_stat_detail(target)}, current euid={os.geteuid()}"
            )
            results.append(CheckResult(relative, "FAIL", detail))
            continue
        if relative == "generated_worlds":
            sdf_count = len(list(path.rglob("*.sdf")))
            world_count = len(list(path.rglob("*.world")))
            results.append(CheckResult(
                relative,
                "OK",
                f"{path} resolves to {target}; target is writable; {sdf_count} .sdf + {world_count} .world",
            ))
        else:
            results.append(CheckResult(relative, "OK", f"{path} resolves to {target}; target is writable"))

    return results


def _git_safe_directory_for_user(repo_path: Path, user: str) -> tuple[Path, str | None]:
    repo_path = repo_path.expanduser().resolve(strict=False)
    candidates = [repo_path, *repo_path.parents]
    last_output = ""

    for candidate in candidates:
        proc = _run_command_as_user(
            [
                "git",
                "-c",
                f"safe.directory={candidate}",
                "-C",
                str(repo_path),
                "rev-parse",
                "--show-toplevel",
            ],
            user,
        )
        if proc is None:
            return repo_path, f"cannot run git as {user}; user is missing or sudo/runuser is unavailable"

        output = _combined_output(proc)
        if output:
            last_output = output
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve(strict=False), None

    return repo_path, last_output or "could not resolve git toplevel"


def _check_provenance() -> list[CheckResult]:
    safe_dir, safe_error = _git_safe_directory_for_user(PROJECT_ROOT, RUN_EXEC_USER)
    if safe_error:
        return [CheckResult(
            "databoss git provenance",
            "FAIL",
            f"could not resolve DATABOSS git toplevel as {RUN_EXEC_USER}: {_first_output_line(safe_error)}",
        )]

    proc = _run_command_as_user(
        [
            "git",
            "-c",
            f"safe.directory={safe_dir}",
            "-C",
            str(PROJECT_ROOT),
            "describe",
            "--always",
            "--dirty",
        ],
        RUN_EXEC_USER,
    )
    if proc is None:
        return [CheckResult(
            "databoss git provenance",
            "FAIL",
            f"cannot run git as {RUN_EXEC_USER}; user is missing or sudo/runuser is unavailable",
        )]

    output = _combined_output(proc)
    describe = proc.stdout.strip()
    if proc.returncode == 0 and describe:
        return [CheckResult(
            "databoss git provenance",
            "OK",
            f"git describe as {RUN_EXEC_USER}: {describe} (safe.directory={safe_dir})",
        )]

    detail = _first_output_line(output) or f"git describe exited {proc.returncode}"
    return [CheckResult(
        "databoss git provenance",
        "FAIL",
        f"git describe as {RUN_EXEC_USER} failed: {detail} (safe.directory={safe_dir})",
    )]


def check_patches(px4_root: Path | None = None) -> list[CheckResult]:
    pins = _load_pins()
    resolved_px4_root = _resolve_px4_root(px4_root)
    return _check_patch_effects(resolved_px4_root, pins)


def run_all_checks(px4_root: Path | None = None) -> dict[str, list[CheckResult]]:
    pins = _load_pins()
    resolved_px4_root = _resolve_px4_root(px4_root)
    return {
        "patches": check_patches(resolved_px4_root),
        "px4": _check_px4(resolved_px4_root, pins),
        "airframes": _check_airframes(resolved_px4_root, pins),
        "models": _check_models(resolved_px4_root, pins),
        "gazebo": _check_gazebo(pins),
        "python": _check_python(),
        "paths": _check_paths(),
        "provenance": _check_provenance(),
    }


def _summary(results: dict[str, list[CheckResult]]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for group in results.values():
        for result in group:
            counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _print_text(results: dict[str, list[CheckResult]]) -> None:
    rows = [
        (group, result.name, result.status, result.detail)
        for group, group_results in results.items()
        for result in group_results
    ]
    widths = [
        max(len("group"), *(len(row[0]) for row in rows)),
        max(len("check"), *(len(row[1]) for row in rows)),
        max(len("status"), *(len(row[2]) for row in rows)),
    ]
    print(f"{'group':<{widths[0]}}  {'check':<{widths[1]}}  {'status':<{widths[2]}}  detail")
    print(f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  {'-' * 6}")
    for group, name, status, detail in rows:
        print(f"{group:<{widths[0]}}  {name:<{widths[1]}}  {status:<{widths[2]}}  {detail}")

    counts = _summary(results)
    print(", ".join(f"{counts.get(status, 0)} {status}" for status in STATUS_ORDER))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text table")
    parser.add_argument("--px4-root", type=Path, default=None, help="PX4-Autopilot checkout to inspect")
    args = parser.parse_args()

    results = run_all_checks(args.px4_root)
    counts = _summary(results)

    if args.json:
        print(json.dumps({
            "results": {
                group: [asdict(result) for result in group_results]
                for group, group_results in results.items()
            },
            "summary": counts,
        }, indent=2, sort_keys=True))
    else:
        _print_text(results)

    return 1 if counts.get("FAIL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
