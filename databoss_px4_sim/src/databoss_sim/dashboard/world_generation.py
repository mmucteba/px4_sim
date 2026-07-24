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

TERRAIN_WORLDS_DIR = GENERATED_WORLDS_DIR / "terrain"

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "worlds"))
from build_gazebo_world import apply_wind_to_world_file, build_sdf, write_manifest  # noqa: E402


def _is_colored_tiles_substitute(world_file: Path) -> bool:
    """Detect the gzweb browser-only visual substitute (Phase 17B hard
    rule: this must never be the world a scenario actually flies in - see
    heightmap_to_web_mesh_world.py's own docstring, "the source heightmap
    remains available as the collision geometry; this script only
    replaces the browser-facing visual in a separate output world").

    Checked two ways: a sibling PROVENANCE.yaml explicitly recording
    `generator: .../heightmap_to_web_mesh_world.py` (authoritative when
    present), or the terrain_tile_<row>_<col> visual-name fingerprint
    that generator's colored_tiles mode always emits (confirmed against
    two real examples, 2026-07-24: serefli_koschisar_web_mesh and
    serefli_koschisar_flowtex_dim both match, the latter despite having
    no "web"/"mesh" hint in its own name - name alone is not reliable).
    """
    provenance_path = world_file.parent / "PROVENANCE.yaml"
    if provenance_path.is_file():
        try:
            with provenance_path.open() as f:
                provenance = yaml.safe_load(f)
            generator = ((provenance or {}).get("world") or {}).get("generator", "")
            if "heightmap_to_web_mesh_world.py" in str(generator):
                return True
        except Exception:
            pass
    try:
        text = world_file.read_text()
    except OSError:
        return False
    return "terrain_tile_" in text


def apply_wind(source_world_name: str, new_name: str, mean_mps: float, direction_vector_enu: list[float]) -> dict[str, Any]:
    """Add wind to an EXISTING world (flat or terrain) as a new, separately
    named world - decoupled from how that world's ground/terrain was
    generated (Phase 17C, user-requested 2026-07-24). Never modifies the
    source; 409-equivalent (FileExistsError) if new_name already exists.

    Refuses a browser-substitute source for the same reason
    resolve_world_selection() does - adding wind to a world that should
    never be flown in doesn't make it flyable.
    """
    matches = [w for w in list_worlds() if w["name"] == source_world_name]
    if not matches:
        raise FileNotFoundError(source_world_name)
    source = matches[0]
    if source.get("is_browser_substitute"):
        raise ValueError(
            f"{source_world_name} is a browser-visualization-only substitute (colored_tiles) - "
            "refusing to build a wind variant of a world that must never be flown in."
        )

    source_path = PROJECT_ROOT / source["sdf_path"]
    is_terrain = source["kind"] == "terrain"
    ext = source_path.suffix  # .sdf or .world
    out_dir = source_path.parent if is_terrain else GENERATED_WORLDS_DIR
    out_path = out_dir / f"{new_name}{ext}"
    if out_path.exists():
        raise FileExistsError(f"world already exists: {new_name}")

    new_text = apply_wind_to_world_file(source_path, mean_mps, direction_vector_enu)
    out_path.write_text(new_text)

    if not is_terrain:
        # Flat worlds carry a .manifest.json sidecar other tooling reads
        # (list_flat_worlds(), resolve_world_selection()) - keep it real
        # rather than leaving the new world manifest-less.
        wind = {"enabled": True, "mean_mps": mean_mps, "direction_vector_enu": direction_vector_enu}
        manifest = {
            "generated_sdf": str(out_path),
            "source_yaml": None,
            "world_name": new_name,
            "texture_preset": source.get("texture_preset"),
            "lighting_preset": source.get("lighting_preset"),
            "wind_enabled": True,
            "wind_mean_mps": mean_mps,
            "wind_direction_vector_enu": direction_vector_enu,
            "derived_from": source_world_name,
        }
        out_path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    return {
        "name": new_name,
        "kind": source["kind"],
        "sdf_path": str(out_path.relative_to(PROJECT_ROOT)),
        "derived_from": source_world_name,
        "terrain_wind_untested": is_terrain,
    }


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
    generated world - all its fields set together (FIELD_CLASSIFICATION
    marks each "derived", set only as this bundle, never individually).

    Refuses (ValueError) to select a browser-only colored_tiles substitute
    - the Phase 17B hard rule enforced at last: "the launch endpoint
    [scenario-generation] must always resolve world.sdf_path to the real
    mesh world... reject a scenario that accidentally points at a
    colored_tiles output." This is the point that rule was written for;
    10 real Phase 14g/14h scenarios in this exact repo picked exactly this
    kind of substitute before this check existed (2026-07-24 finding).

    Flat and terrain worlds return different label shapes: flat worlds
    get lighting/wind/texture labels (display only, real wiring is
    sdf_path); terrain worlds get a `home` GPS-origin block instead
    (real - sets PX4_HOME_LAT/LON/ALT), read from the world's own
    PROVENANCE.yaml when present.
    """
    matches = [w for w in list_worlds() if w["name"] == world_name]
    if not matches:
        raise FileNotFoundError(world_name)
    world = matches[0]

    if world.get("is_browser_substitute"):
        raise ValueError(
            f"{world_name} is a browser-visualization-only substitute (colored_tiles) - "
            "it must never be the world a scenario actually flies in. Pick the "
            "non-substitute terrain world instead, or generate a proper one."
        )

    sdf_path = PROJECT_ROOT / world["sdf_path"]

    if world["kind"] == "terrain":
        provenance_path = sdf_path.parent / "PROVENANCE.yaml"
        home = {}
        if provenance_path.is_file():
            with provenance_path.open() as f:
                provenance = yaml.safe_load(f) or {}
            # Two real, different PROVENANCE.yaml schemas exist (2026-07-24
            # finding): direct gazebo_terrain_generator imports put
            # spherical_coordinates at the top level; heightmap_to_web_mesh_world.py
            # outputs nest everything under a `world:` key. Check both.
            home = provenance.get("spherical_coordinates") or (provenance.get("world") or {}).get(
                "spherical_coordinates", {}
            )
        return {
            "name": world_name,
            "sdf_path": world["sdf_path"],
            "home": {
                "lat_deg": home.get("lat_deg"),
                "lon_deg": home.get("lon_deg"),
                "alt_m": home.get("elevation_m"),
            },
            "condition_is_physical": True,
        }

    wind_enabled = world.get("wind_enabled", False)
    wind_mean = None  # not carried in list_flat_worlds() today; re-read manifest for it
    manifest_path = GENERATED_WORLDS_DIR / f"{world_name}.manifest.json"
    if manifest_path.is_file():
        with manifest_path.open() as f:
            wind_mean = json.load(f).get("wind_mean_mps")
    wind_label = "none" if not wind_enabled else (f"wind_{wind_mean}mps" if wind_mean else "wind_enabled")

    return {
        "name": world_name,
        "sdf_path": world["sdf_path"],
        "lighting": world.get("lighting_preset") or f"{world_name}_lighting",
        "wind": wind_label,
        "texture": world.get("texture_preset") or f"{world_name}_texture",
        "condition_is_physical": True,
    }


def list_flat_worlds() -> list[dict[str, Any]]:
    """Flat-ground worlds (build_gazebo_world.py's own pipeline) - read
    from each world's .manifest.json sidecar."""
    if not GENERATED_WORLDS_DIR.is_dir():
        return []
    out = []
    for manifest_path in sorted(GENERATED_WORLDS_DIR.glob("*.manifest.json")):
        try:
            with manifest_path.open() as f:
                manifest = json.load(f)
        except Exception as e:
            out.append({"name": manifest_path.stem, "kind": "flat", "error": str(e)})
            continue
        world_name = manifest.get("world_name", manifest_path.stem)
        sdf_path = GENERATED_WORLDS_DIR / f"{world_name}.sdf"
        out.append({
            "name": world_name,
            "kind": "flat",
            "sdf_path": str(sdf_path.relative_to(PROJECT_ROOT)) if sdf_path.is_file() else None,
            "texture_preset": manifest.get("texture_preset"),
            "lighting_preset": manifest.get("lighting_preset"),
            "wind_enabled": manifest.get("wind_enabled"),
            "is_browser_substitute": False,
        })
    return out


def list_terrain_worlds() -> list[dict[str, Any]]:
    """Real-terrain (heightmap-based) worlds - a structurally different
    pipeline from build_gazebo_world.py (Phase 9A gazebo_terrain_generator
    + serefli_koschisar-style imports), no .manifest.json convention, and
    living under generated_worlds/terrain/ rather than generated_worlds/
    directly. `.world` files, not `.sdf`.

    Deduplicated by name (2026-07-24 finding: real duplicates exist on
    disk under different paths - e.g. `terrain/Joshimath.world` is an
    earlier, incomplete copy missing 4 PX4-required sensor plugins that
    `terrain/Joshimath/Joshimath.world` has; `_generator_output/` holds
    raw/intermediate tool output, not a finished world). Preference order:
    exclude anything under `_generator_output/`, then prefer the
    `<name>/<name>.world` canonical-nested form over a bare top-level file
    when both exist, matching the convention every properly-organized
    world here already follows.

    is_browser_substitute (Phase 17B hard rule) is checked per file and
    surfaced explicitly rather than silently excluded - the dashboard
    should let an operator SEE that a world exists and know it's unsafe
    to fly, not just hide it and leave the mystery. resolve_world_selection()
    is the actual enforcement point that refuses to select one for a
    scenario.
    """
    if not TERRAIN_WORLDS_DIR.is_dir():
        return []

    candidates: dict[str, Path] = {}
    for world_file in sorted(TERRAIN_WORLDS_DIR.glob("**/*.world")):
        if "_generator_output" in world_file.parts:
            continue
        name = world_file.stem
        is_canonical_nested = world_file.parent.name == name
        existing = candidates.get(name)
        if existing is None:
            candidates[name] = world_file
        elif is_canonical_nested and existing.parent.name != name:
            candidates[name] = world_file  # prefer the nested form found later

    out = []
    for name, world_file in sorted(candidates.items()):
        out.append({
            "name": name,
            "kind": "terrain",
            "sdf_path": str(world_file.relative_to(PROJECT_ROOT)),
            "texture_preset": None,
            "lighting_preset": None,
            "wind_enabled": None,
            "is_browser_substitute": _is_colored_tiles_substitute(world_file),
        })
    return out


def list_worlds() -> list[dict[str, Any]]:
    """All selectable worlds, flat + terrain together - the bundle
    returned per world (name/sdf_path/...) matches exactly the "derived as
    a group" fields in FIELD_CLASSIFICATION - a scenario picks one of
    these, not the individual sub-fields."""
    return list_flat_worlds() + list_terrain_worlds()
