#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is missing. Install with: python3 -m pip install pyyaml", file=sys.stderr)
    sys.exit(2)


PROJECT_ROOT = Path("/opt/databoss_px4_sim")
PX4_ROOT = Path("/opt/sim_px4/PX4-Autopilot")
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"


REQUIRED_TOP_LEVEL_KEYS = [
    "run",
    "vehicle",
    "world",
    "route",
    "gnss",
    "aiding",
    "logging",
    "analysis",
]


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def load_yaml(path: Path) -> dict:
    with path.open("r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Scenario YAML must parse as a dictionary.")
    return data


def validate_scenario(data: dict) -> list[str]:
    errors: list[str] = []

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"Missing top-level key: {key}")

    run = data.get("run", {})
    if not isinstance(run, dict) or not run.get("name"):
        errors.append("Missing run.name")

    vehicle = data.get("vehicle", {})
    if not isinstance(vehicle, dict) or not vehicle.get("model"):
        errors.append("Missing vehicle.model")

    route = data.get("route", {})
    if not isinstance(route, dict) or not route.get("name"):
        errors.append("Missing route.name")

    return errors


# --- Scenario-authoritative launch contract -----------------------------
# The scenario YAML's gnss/failsafe blocks describe what a run IS. For a
# long time only CLI flags were read, so launching a "gnssloss" scenario
# without --gnss-loss-after-takeoff-s silently ran GNSS-on, and a stock
# scenario without --failsafe-profile delayed_observation silently ran the
# default failsafe (documented trap, phase_10 / phase_12). These resolvers
# make the YAML authoritative when the CLI flag is absent, with the CLI
# preserved as an explicit override. Used by both auto_takeoff_land_pxh_truth
# (the direct path) and run_scenario_pxh_end_to_end (the wrapper path) so
# the two never diverge.

FAILSAFE_PROFILE_ALIASES = {
    "default": "default_px4",
    "default_px4": "default_px4",
    "delayed_observation": "delayed_observation",
}


def resolve_gnss_loss_after_takeoff_s(data: dict, cli_value):
    """Return (value_or_None, source) for the GNSS-loss trigger.

    source is 'cli' (flag given), 'scenario_yaml' (gnss.loss_enabled true),
    or 'none'. For offboard_local_position_hold the value only acts as a
    presence flag; the effective cut time is derived from the control block
    downstream, so the exact number here is not load-bearing for that mode.
    """
    if cli_value is not None:
        return cli_value, "cli"
    gnss = data.get("gnss", {}) or {}
    if bool(gnss.get("loss_enabled", False)):
        after = gnss.get("loss_after_takeoff_s")
        return (float(after) if after is not None else 10.0), "scenario_yaml"
    return None, "none"


def resolve_failsafe_profile(data: dict, cli_value):
    """Return (profile, source) mapping the scenario failsafe.profile.

    Maps the YAML alias 'default' to the runner's 'default_px4'. Raises
    ValueError on an unrecognised profile so a typo fails loud rather than
    silently falling back to the default profile.
    """
    if cli_value is not None:
        return cli_value, "cli"
    failsafe = data.get("failsafe", {}) or {}
    raw = failsafe.get("profile")
    if raw is None:
        return "default_px4", "default"
    key = str(raw).strip().lower()
    if key not in FAILSAFE_PROFILE_ALIASES:
        raise ValueError(
            f"unknown failsafe.profile in scenario: {raw!r} "
            f"(expected one of {sorted(FAILSAFE_PROFILE_ALIASES)})"
        )
    return FAILSAFE_PROFILE_ALIASES[key], "scenario_yaml"


def make_run_id(scenario_name: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in scenario_name)
    return f"{stamp}_{safe_name}"


def write_environment(path: Path) -> None:
    lines = [
        "# Environment",
        "",
        f"created_utc: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"hostname: {platform.node()}",
        f"platform: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        f"cwd: {os.getcwd()}",
        "",
        "## Paths",
        "",
        f"project_root: {PROJECT_ROOT}",
        f"px4_root: {PX4_ROOT}",
        "",
        "## Git",
        "",
        f"databoss_git: {sh(['git', '-C', str(PROJECT_ROOT), 'rev-parse', '--short', 'HEAD'])}",
        f"px4_git: {sh(['git', '-C', str(PX4_ROOT), 'rev-parse', '--short', 'HEAD'])}",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a DATABOSS run folder from a scenario YAML.")
    parser.add_argument("scenario", help="Path to scenario YAML")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not create files")
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

    scenario_name = data["run"]["name"]
    run_id = make_run_id(scenario_name)
    run_dir = RUNS_DIR / run_id

    print(f"Scenario: {scenario_path}")
    print(f"Run ID:   {run_id}")
    print(f"Run dir:  {run_dir}")

    if args.dry_run:
        print("Dry run OK. No files created.")
        return 0

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
            "Created by Phase 7A create_run_from_scenario.py.",
            "",
            "This is a prepared run folder. PX4/Gazebo execution is not automated yet.",
            "",
            "## Scenario",
            "",
            f"- name: {scenario_name}",
            f"- source: {scenario_path}",
            "",
        ])
    )

    (run_dir / "commands.log").write_text(
        "\n".join([
            "# Commands",
            "",
            f"cd {PROJECT_ROOT}",
            f"python3 scripts/runner/create_run_from_scenario.py {scenario_path}",
            "",
        ])
    )

    write_environment(run_dir / "environment.txt")

    (run_dir / "validation.md").write_text(
        "\n".join([
            "# Validation",
            "",
            "## Phase 7A first-step checks",
            "",
            "- [x] Scenario YAML parsed.",
            "- [x] Required top-level fields exist.",
            "- [x] Run folder created under experiments/runs/.",
            "- [x] config.yaml copied.",
            "- [x] README.md written.",
            "- [x] commands.log written.",
            "- [x] environment.txt written.",
            "- [x] No output written into PX4 source.",
            "",
            "## Result",
            "",
            "Prepared only. No flight executed yet.",
            "",
        ])
    )

    print("Created:")
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
