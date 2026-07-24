"""Phase 17C Tier B world generation: turn structured user input into a new
world YAML + generated SDF, reusing build_gazebo_world.py's own
build_sdf()/write_manifest() rather than reimplementing SDF assembly. No
process/port interaction at all - pure file generation, so this never
touches gz-sim/PX4 in any way.
"""

from __future__ import annotations

import json
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


def resolve_world_selection(world_name: str) -> dict[str, Any]:
    """The scenario-facing `world` block bundle for picking an EXISTING
    generated world - name/sdf_path/lighting/wind/texture/
    condition_is_physical all set together (FIELD_CLASSIFICATION marks
    each of these "derived", set only as this bundle, never individually).
    lighting/texture/wind are display labels only (not read for logic -
    the real physical wiring is world.sdf_path, confirmed Phase 17B), so
    an approximate label is fine when a preset name wasn't recorded.
    """
    manifest_path = GENERATED_WORLDS_DIR / f"{world_name}.manifest.json"
    sdf_path = GENERATED_WORLDS_DIR / f"{world_name}.sdf"
    if not sdf_path.is_file():
        raise FileNotFoundError(world_name)

    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        with manifest_path.open() as f:
            manifest = json.load(f)

    wind_enabled = manifest.get("wind_enabled", False)
    wind_mean = manifest.get("wind_mean_mps")
    wind_label = "none" if not wind_enabled else (f"wind_{wind_mean}mps" if wind_mean else "wind_enabled")

    return {
        "name": world_name,
        "sdf_path": str(sdf_path.relative_to(PROJECT_ROOT)),
        "lighting": manifest.get("lighting_preset") or f"{world_name}_lighting",
        "wind": wind_label,
        "texture": manifest.get("texture_preset") or f"{world_name}_texture",
        "condition_is_physical": True,
    }


def list_worlds() -> list[dict[str, Any]]:
    """Every already-generated world (Tier A picker) - read from each
    world's own .manifest.json sidecar, which build_gazebo_world.py's
    write_manifest() already produces. The bundle returned per world
    (name/sdf_path/lighting/wind/texture/condition_is_physical) matches
    exactly the "derived as a group" fields in FIELD_CLASSIFICATION - a
    scenario picks one of these, not the individual sub-fields."""
    if not GENERATED_WORLDS_DIR.is_dir():
        return []
    out = []
    for manifest_path in sorted(GENERATED_WORLDS_DIR.glob("*.manifest.json")):
        try:
            with manifest_path.open() as f:
                manifest = json.load(f)
        except Exception as e:
            out.append({"name": manifest_path.stem, "error": str(e)})
            continue
        world_name = manifest.get("world_name", manifest_path.stem)
        sdf_path = GENERATED_WORLDS_DIR / f"{world_name}.sdf"
        out.append({
            "name": world_name,
            "sdf_path": str(sdf_path.relative_to(PROJECT_ROOT)) if sdf_path.is_file() else None,
            "texture_preset": manifest.get("texture_preset"),
            "lighting_preset": manifest.get("lighting_preset"),
            "wind_enabled": manifest.get("wind_enabled"),
        })
    return out
