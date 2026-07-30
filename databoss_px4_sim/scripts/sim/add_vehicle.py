#!/usr/bin/env python3
"""Compose and install a DATABOSS vehicle into a PX4 tree.

    venv/bin/python scripts/sim/add_vehicle.py --spec spec.yaml --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from databoss_sim.dashboard.vehicle_generation import compose_vehicle, write_vehicle  # noqa: E402
from databoss_sim.dashboard.vehicle_install import (  # noqa: E402
    DEFAULT_PX4_ROOT,
    PX4_AIRFRAMES_REL,
    PX4_MODELS_REL,
    StepResult,
    all_ok,
    install_vehicle,
)


def _repo_paths(project_root: Path, model_name: str, airframe_filename: str) -> list[Path]:
    return [
        project_root / "src" / "databoss_sim" / "models" / model_name / "model.sdf",
        project_root / "src" / "databoss_sim" / "models" / model_name / "model.config",
        project_root / "src" / "databoss_sim" / "airframes" / airframe_filename,
    ]


def _existing_airframe_filename(project_root: Path, model_name: str) -> str | None:
    airframes_dir = project_root / "src" / "databoss_sim" / "airframes"
    matches = sorted(path.name for path in airframes_dir.glob(f"*_gz_{model_name}"))
    return matches[0] if len(matches) == 1 else None


def _repo_files_identical(project_root: Path, composed: Any) -> bool:
    model_name = composed.airframe_filename.split("_gz_", 1)[1]
    paths = _repo_paths(project_root, model_name, composed.airframe_filename)
    expected = [composed.model_sdf, composed.model_config, composed.airframe]
    return all(path.is_file() and path.read_text() == text for path, text in zip(paths, expected))


def _repo_files_conflicts(project_root: Path, composed: Any) -> list[Path]:
    model_name = composed.airframe_filename.split("_gz_", 1)[1]
    conflicts: list[Path] = []
    for path, expected in zip(_repo_paths(project_root, model_name, composed.airframe_filename), [composed.model_sdf, composed.model_config, composed.airframe]):
        if path.exists() and (not path.is_file() or path.read_text() != expected):
            conflicts.append(path)
    return conflicts


def write_repo_vehicle(project_root: Path, composed: Any, *, dry_run: bool) -> StepResult:
    model_name = composed.airframe_filename.split("_gz_", 1)[1]
    models_dir = project_root / "src" / "databoss_sim" / "models"
    airframes_dir = project_root / "src" / "databoss_sim" / "airframes"
    commands = [
        f"write {models_dir / model_name / 'model.sdf'}",
        f"write {models_dir / model_name / 'model.config'}",
        f"write {airframes_dir / composed.airframe_filename}",
    ]
    if _repo_files_identical(project_root, composed):
        return StepResult("write_repo_vehicle", "OK", "repo-side composed vehicle files already match", commands)
    conflicts = _repo_files_conflicts(project_root, composed)
    if conflicts:
        return StepResult(
            "write_repo_vehicle",
            "FAIL",
            "refusing to overwrite divergent repo-side vehicle files: " + ", ".join(str(path) for path in conflicts),
            commands,
        )
    if dry_run:
        return StepResult("write_repo_vehicle", "DRY_RUN", "would write repo-side composed vehicle files", commands)
    written = write_vehicle(composed, models_dir, airframes_dir, overwrite=False)
    if not all(path.is_file() for path in written):
        return StepResult("write_repo_vehicle", "FAIL", "composer write did not create all expected files", commands)
    if not _repo_files_identical(project_root, composed):
        return StepResult("write_repo_vehicle", "FAIL", "repo-side files differ after composer write", commands)
    return StepResult("write_repo_vehicle", "OK", "wrote repo-side composed vehicle files", commands)


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
    parser.add_argument("--spec", type=Path, required=True, help="Vehicle spec YAML to compose")
    parser.add_argument("--dry-run", action="store_true", help="Preview the install sequence without mutating PX4")
    parser.add_argument("--px4-root", type=Path, default=DEFAULT_PX4_ROOT, help="PX4-Autopilot checkout to install into")
    parser.add_argument("--skip-build", action="store_true", help="Install files but do not run make")
    parser.add_argument("--pins-path", type=Path, default=None, help="PX4 pins YAML to update")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    parser.add_argument("--lock-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()

    project_root = args.project_root.expanduser().resolve()
    px4_root = args.px4_root.expanduser().resolve()
    pins_path = (args.pins_path or (project_root / "deploy" / "px4" / "px4_pins.yaml")).expanduser().resolve()
    patch_path = pins_path.parent / "0004-airframes-cmakelists-register.patch"
    lock_path = (args.lock_path or (project_root / "experiments" / "jobs" / ".active.lock")).expanduser().resolve()

    with args.spec.open() as f:
        raw_spec = yaml.safe_load(f)
    if not isinstance(raw_spec, dict):
        raise SystemExit(f"{args.spec} must contain a YAML mapping")

    composed = compose_vehicle(
        raw_spec,
        databoss_models_dir=project_root / "src" / "databoss_sim" / "models",
        px4_models_dir=px4_root / PX4_MODELS_REL,
        px4_airframes_dir=px4_root / PX4_AIRFRAMES_REL,
        allow_existing_name=True,
    )
    requested_name = str(raw_spec["name"])
    existing_airframe = _existing_airframe_filename(project_root, requested_name)
    if existing_airframe is not None and existing_airframe != composed.airframe_filename:
        composed = replace(
            composed,
            airframe_filename=existing_airframe,
            autostart_id=int(existing_airframe.split("_", 1)[0]),
            pins_entries={
                "models": [f"src/databoss_sim/models/{requested_name}"],
                "airframes": [f"src/databoss_sim/airframes/{existing_airframe}"],
            },
        )
    model_name = composed.airframe_filename.split("_gz_", 1)[1]

    results = [write_repo_vehicle(project_root, composed, dry_run=args.dry_run)]
    if results[-1].status != "FAIL" or args.dry_run:
        results.extend(
            install_vehicle(
                model_name,
                px4_root=px4_root,
                pins_path=pins_path,
                patch_path=patch_path,
                project_root=project_root,
                airframe_filename=composed.airframe_filename,
                build_target="px4_sitl_default",
                dry_run=args.dry_run,
                skip_build=args.skip_build,
                lock_path=lock_path,
            )
        )

    if args.json:
        print(json.dumps([result.asdict() for result in results], indent=2))
    else:
        _print_markdown(results)

    return 1 if (not args.dry_run and not all_ok(results)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
