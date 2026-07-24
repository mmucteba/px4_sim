"""Phase 17C Tier B world generation: turn structured user input into a new
world YAML + generated SDF, reusing build_gazebo_world.py's own
build_sdf()/write_manifest() rather than reimplementing SDF assembly. No
process/port interaction at all - pure file generation, so this never
touches gz-sim/PX4 in any way.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from databoss_sim.dashboard.config import PROJECT_ROOT

WORLDS_CONFIG_DIR = PROJECT_ROOT / "experiments" / "configs" / "mvp" / "worlds"
GENERATED_WORLDS_DIR = PROJECT_ROOT / "generated_worlds"

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "worlds"))
from build_gazebo_world import build_sdf, write_manifest  # noqa: E402


def generate_world(new_name: str, world_fields: dict[str, Any]) -> dict[str, str]:
    """world_fields must contain at least size_m/texture/lighting/wind
    (required mappings per build_gazebo_world.py's own require_mapping()
    calls); objects/pad are optional. Writes a NEW world YAML and a NEW
    generated SDF - both 409-equivalent (FileExistsError) if new_name
    already exists as either, never overwriting an existing world (several
    are cited as frozen artifacts by accepted phase docs)."""
    config_path = WORLDS_CONFIG_DIR / f"{new_name}.yaml"
    sdf_path = GENERATED_WORLDS_DIR / f"{new_name}.sdf"

    if config_path.exists():
        raise FileExistsError(f"world config already exists: {new_name}")
    if sdf_path.exists():
        raise FileExistsError(f"generated SDF already exists: {new_name}")

    world = dict(world_fields)
    world["name"] = new_name
    full_config = {"world": world}

    WORLDS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        yaml.safe_dump(full_config, f, sort_keys=False)

    try:
        sdf, parsed_world = build_sdf(config_path)
    except Exception:
        config_path.unlink(missing_ok=True)
        raise

    GENERATED_WORLDS_DIR.mkdir(parents=True, exist_ok=True)
    sdf_path.write_text(sdf)
    write_manifest(sdf_path, config_path, parsed_world)

    return {
        "world_config_path": str(config_path.relative_to(PROJECT_ROOT)),
        "sdf_path": str(sdf_path.relative_to(PROJECT_ROOT)),
    }
