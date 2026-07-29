#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "generated_worlds"


def require_mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def rgba(values: list[float]) -> str:
    if len(values) != 4:
        raise ValueError("color values must have four RGBA components")
    return " ".join(f"{float(v):.3f}" for v in values)


def pose(values: list[float]) -> str:
    if len(values) != 6:
        raise ValueError("pose_m must have six values: x y z roll pitch yaw")
    return " ".join(f"{float(v):.6g}" for v in values)


def material(name: str, color: list[float]) -> str:
    color_text = rgba(color)
    return f"""
          <material>
            <ambient>{color_text}</ambient>
            <diffuse>{color_text}</diffuse>
            <specular>0.05 0.05 0.05 1</specular>
          </material>
          <!-- material_id: {html.escape(name)} -->
"""


def textured_material(name: str, image_uri: str) -> str:
    # PBR albedo_map is proven to render under the pinned `ogre` classic
    # engine (Step 0 render test, 2026-07-13: 400/400 SIFT keypoints).
    return f"""
          <material>
            <diffuse>1 1 1 1</diffuse>
            <specular>0.05 0.05 0.05 1</specular>
            <pbr>
              <metal>
                <albedo_map>{html.escape(image_uri)}</albedo_map>
                <roughness>1.0</roughness>
                <metalness>0.0</metalness>
              </metal>
            </pbr>
          </material>
          <!-- material_id: {html.escape(name)} -->
"""


def box_model(name: str, item: dict) -> str:
    size = item.get("size_m")
    if not isinstance(size, list) or len(size) != 3:
        raise ValueError(f"{name}: box size_m must have three values")
    size_text = " ".join(f"{float(v):.6g}" for v in size)
    pose_text = pose(item.get("pose_m", [0, 0, float(size[2]) / 2, 0, 0, 0]))
    color = item.get("color", [0.5, 0.5, 0.5, 1.0])
    return f"""
    <model name="{html.escape(name)}">
      <static>true</static>
      <pose>{pose_text}</pose>
      <link name="link">
        <collision name="collision">
          <geometry>
            <box><size>{size_text}</size></box>
          </geometry>
        </collision>
        <visual name="visual">
          <geometry>
            <box><size>{size_text}</size></box>
          </geometry>
{material(name, color)}
        </visual>
      </link>
    </model>
"""


def cylinder_model(name: str, item: dict) -> str:
    radius = float(item.get("radius_m", 1.0))
    length = float(item.get("length_m", 1.0))
    pose_text = pose(item.get("pose_m", [0, 0, length / 2, 0, 0, 0]))
    color = item.get("color", [0.5, 0.5, 0.5, 1.0])
    return f"""
    <model name="{html.escape(name)}">
      <static>true</static>
      <pose>{pose_text}</pose>
      <link name="link">
        <collision name="collision">
          <geometry>
            <cylinder>
              <radius>{radius:.6g}</radius>
              <length>{length:.6g}</length>
            </cylinder>
          </geometry>
        </collision>
        <visual name="visual">
          <geometry>
            <cylinder>
              <radius>{radius:.6g}</radius>
              <length>{length:.6g}</length>
            </cylinder>
          </geometry>
{material(name, color)}
        </visual>
      </link>
    </model>
"""


def ground_model(world: dict) -> str:
    size = require_mapping(world.get("size_m"), "world.size_m")
    texture = require_mapping(world.get("texture"), "world.texture")
    x = float(size.get("x", 120))
    y = float(size.get("y", 120))
    pattern = str(texture.get("visual_pattern", "uniform_field"))
    primary = texture.get("primary_color", [0.3, 0.4, 0.2, 1.0])
    secondary = texture.get("secondary_color", primary)
    tile = float(texture.get("tile_size_m", 10))

    visuals: list[str] = []
    if pattern in ("image_field", "checker_field"):
        # Solid base slab under the thin tiles. The tiles are 4 mm boxes and the
        # downward single-point gpu_lidar slipped through the hairline gaps
        # between abutting tile visuals -> inf ranges, badly (run
        # 20260713_131822 legy: 82% inf while the camera stayed at quality 255).
        # A continuous slab, emitted as both collision and visual geometry, gives
        # every ray that misses a tile an unbroken fallback ground surface.
        base_color = rgba(texture.get("base_color", [0.35, 0.35, 0.32, 1.0]))
        base_thickness_m = 0.2
        base_top_z_m = 0.001
        base_center_z_m = base_top_z_m - base_thickness_m / 2
        visuals.append(
            f"""
        <collision name="ground_base_collision">
          <pose>0 0 {base_center_z_m:.6g} 0 0 0</pose>
          <geometry>
            <box><size>{x:.6g} {y:.6g} {base_thickness_m:.6g}</size></box>
          </geometry>
        </collision>
        <visual name="ground_base">
          <pose>0 0 {base_center_z_m:.6g} 0 0 0</pose>
          <geometry>
            <box><size>{x:.6g} {y:.6g} {base_thickness_m:.6g}</size></box>
          </geometry>
          <material>
            <ambient>{base_color}</ambient>
            <diffuse>{base_color}</diffuse>
            <specular>0.02 0.02 0.02 1</specular>
          </material>
        </visual>
"""
        )
    if pattern == "image_field":
        # Grid of image-textured tiles: each tile UV-maps the full image once
        # (SDF box faces do not repeat textures), so the texture repeats every
        # tile_size_m. Tile size >= several camera footprints so repetition is
        # invisible frame-to-frame at flow scale.
        image = texture.get("image")
        if not image:
            raise ValueError("texture.image is required for visual_pattern=image_field")
        image_path = (PROJECT_ROOT / str(image)).resolve()
        if not image_path.exists():
            raise ValueError(f"texture.image not found: {image_path}")
        image_uri = f"file://{image_path}"
        nx = max(1, int(round(x / tile)))
        ny = max(1, int(round(y / tile)))
        tile_x = x / nx
        tile_y = y / ny
        for ix in range(nx):
            for iy in range(ny):
                cx = -x / 2 + tile_x * (ix + 0.5)
                cy = -y / 2 + tile_y * (iy + 0.5)
                visuals.append(
                    f"""
        <visual name="tile_{ix}_{iy}">
          <pose>{cx:.6g} {cy:.6g} 0.002 0 0 0</pose>
          <geometry>
            <box><size>{tile_x:.6g} {tile_y:.6g} 0.004</size></box>
          </geometry>
{textured_material(f"tile_{ix}_{iy}", image_uri)}
        </visual>
"""
                )
    elif pattern == "checker_field":
        nx = max(1, int(round(x / tile)))
        ny = max(1, int(round(y / tile)))
        tile_x = x / nx
        tile_y = y / ny
        for ix in range(nx):
            for iy in range(ny):
                cx = -x / 2 + tile_x * (ix + 0.5)
                cy = -y / 2 + tile_y * (iy + 0.5)
                color = primary if (ix + iy) % 2 == 0 else secondary
                visuals.append(
                    f"""
        <visual name="tile_{ix}_{iy}">
          <pose>{cx:.6g} {cy:.6g} 0.002 0 0 0</pose>
          <geometry>
            <box><size>{tile_x:.6g} {tile_y:.6g} 0.004</size></box>
          </geometry>
{material(f"tile_{ix}_{iy}", color)}
        </visual>
"""
                )
    else:
        visuals.append(
            f"""
        <visual name="uniform_ground">
          <pose>0 0 0.001 0 0 0</pose>
          <geometry>
            <box><size>{x:.6g} {y:.6g} 0.002</size></box>
          </geometry>
{material("uniform_ground", primary)}
        </visual>
"""
        )

    return f"""
    <model name="databoss_ground">
      <static>true</static>
      <link name="ground_link">
        <collision name="ground_collision">
          <pose>0 0 -0.005 0 0 0</pose>
          <geometry>
            <box><size>{x:.6g} {y:.6g} 0.01</size></box>
          </geometry>
        </collision>
{''.join(visuals)}
      </link>
    </model>
"""


def launch_pad_model(world: dict) -> str:
    """Default takeoff/landing pad (Phase 17C, user-requested 2026-07-24):
    build_gazebo_world.py had zero concept of a physical pad before this -
    the drone spawned at a bare coordinate on the flat ground plane. Named
    `databoss_launch_pad` to match the naming convention already referenced
    (but never defined on disk) by scripts/worlds/add_serefli_flow_texture_overlay.py
    for a different, special-case terrain world - no existing SDF markup
    was found to copy visually, so this is a new, simple static platform
    matching this file's own box-model conventions.

    world.pad: {enabled: true, pose_m: [x, y], size_m: [w, d]} - all
    optional, defaults to a 1.2x1.2 m pad at the world origin (matches the
    (0,0) start_pose used by essentially every real scenario today).
    Emitted by default; set world.pad.enabled: false to omit.
    """
    pad = world.get("pad")
    pad = pad if isinstance(pad, dict) else {}
    if not pad.get("enabled", True):
        return ""

    pose_xy = pad.get("pose_m", [0.0, 0.0])
    if not isinstance(pose_xy, list) or len(pose_xy) != 2:
        raise ValueError("world.pad.pose_m must have two values: x y")
    size_xy = pad.get("size_m", [1.2, 1.2])
    if not isinstance(size_xy, list) or len(size_xy) != 2:
        raise ValueError("world.pad.size_m must have two values: width depth")

    x, y = float(pose_xy[0]), float(pose_xy[1])
    w, d = float(size_xy[0]), float(size_xy[1])
    thickness = 0.02
    # Sits just above the ground-tile visuals (which top out around z=0.004,
    # see ground_model()) so it never z-fights with them.
    center_z = 0.006 + thickness / 2
    color = pad.get("color", [0.85, 0.78, 0.15, 1.0])  # pad-yellow, distinct from ground

    return f"""
    <model name="databoss_launch_pad">
      <static>true</static>
      <pose>{x:.6g} {y:.6g} {center_z:.6g} 0 0 0</pose>
      <link name="link">
        <collision name="collision">
          <geometry>
            <box><size>{w:.6g} {d:.6g} {thickness:.6g}</size></box>
          </geometry>
        </collision>
        <visual name="visual">
          <geometry>
            <box><size>{w:.6g} {d:.6g} {thickness:.6g}</size></box>
          </geometry>
{material("databoss_launch_pad", color)}
        </visual>
      </link>
    </model>
"""


def object_models(world: dict) -> str:
    chunks: list[str] = []
    for item in world.get("objects", []):
        if not isinstance(item, dict):
            raise ValueError("world.objects entries must be mappings")
        name = str(item.get("name", "unnamed_object"))
        kind = str(item.get("type", "box"))
        if kind == "box":
            chunks.append(box_model(name, item))
        elif kind == "cylinder":
            chunks.append(cylinder_model(name, item))
        else:
            raise ValueError(f"{name}: unsupported object type {kind!r}")
    return "".join(chunks)


def scene(world: dict) -> str:
    lighting = require_mapping(world.get("lighting"), "world.lighting")
    ambient = rgba(lighting.get("ambient", [0.55, 0.55, 0.55, 1.0]))
    background = rgba(lighting.get("background", [0.70, 0.82, 0.95, 1.0]))
    shadows = "true" if lighting.get("shadows_enabled", True) else "false"
    return f"""
    <scene>
      <ambient>{ambient}</ambient>
      <background>{background}</background>
      <shadows>{shadows}</shadows>
    </scene>
"""


def sun_light(world: dict) -> str:
    lighting = require_mapping(world.get("lighting"), "world.lighting")
    if not lighting.get("sun_enabled", True):
        return ""
    direction = lighting.get("sun_direction", [-0.35, 0.15, -0.92])
    if not isinstance(direction, list) or len(direction) != 3:
        raise ValueError("lighting.sun_direction must have three values")
    direction_text = " ".join(f"{float(v):.6g}" for v in direction)
    cast_shadows = "true" if lighting.get("shadows_enabled", True) else "false"
    return f"""
    <light name="sun" type="directional">
      <cast_shadows>{cast_shadows}</cast_shadows>
      <pose>0 0 10 0 0 0</pose>
      <diffuse>0.9 0.86 0.76 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>{direction_text}</direction>
    </light>
"""


def wind_config(world: dict) -> dict:
    """Ported verbatim from the Phase 16 wind mechanism (found live but
    uncommitted on the main checkout, 2026-07-24) - already validated
    there, not reinvented here."""
    wind = require_mapping(world.get("wind", {}), "world.wind")
    if not wind.get("enabled", False):
        return {"enabled": False}
    if wind.get("gusts_enabled", False):
        raise ValueError(
            "world.wind.gusts_enabled=true is not implemented (Phase 16 scope: steady wind only)"
        )
    mean_mps = float(wind.get("mean_mps", 0))
    direction = wind.get("direction_vector_enu")
    if not isinstance(direction, list) or len(direction) != 2:
        raise ValueError("world.wind.direction_vector_enu must have two values [east, north]")
    east, north = float(direction[0]), float(direction[1])
    norm = (east**2 + north**2) ** 0.5
    if norm == 0:
        raise ValueError("world.wind.direction_vector_enu must not be zero-length")
    return {
        "enabled": True,
        "mean_mps": mean_mps,
        "vx": mean_mps * east / norm,
        "vy": mean_mps * north / norm,
        "direction_vector_enu": [east / norm, north / norm],
    }


def wind_block(wind: dict) -> str:
    if not wind.get("enabled", False):
        return ""
    # The gz-sim WindEffects *system* is loaded globally via PX4's
    # server.config (same mechanism as Physics/SceneBroadcaster/etc), not
    # embedded as a per-world <plugin> here. Mixing an SDF-embedded
    # WindEffects plugin with server-config-supplied systems on the same
    # world entity was found to deadlock gz-sim during system startup
    # (SystemManager never got past loading WindEffects, so SceneBroadcaster
    # never advertised /world/.../scene/info and the world hung forever) --
    # reproduced and fixed 2026-07-22 (Phase 16). This tag only supplies the
    # wind *data* the globally-loaded system reads.
    return f"""
    <wind>
      <linear_velocity>{wind['vx']:.6g} {wind['vy']:.6g} 0</linear_velocity>
    </wind>
"""


def build_sdf(config_path: Path) -> tuple[str, dict]:
    data = yaml.safe_load(config_path.read_text())
    root = require_mapping(data, "config")
    world = require_mapping(root.get("world"), "world")
    name = str(world.get("name", config_path.stem))
    generator = require_mapping(world.get("generator", {}), "world.generator")
    sdf_version = str(generator.get("sdf_version", "1.9"))
    wind = wind_config(world)

    sdf = f"""<?xml version="1.0" ?>
<sdf version="{html.escape(sdf_version)}">
  <world name="{html.escape(name)}">
{scene(world)}
{sun_light(world)}
{wind_block(wind)}
{ground_model(world)}
{launch_pad_model(world)}
{object_models(world)}
  </world>
</sdf>
"""
    ET.fromstring(sdf)
    return sdf, world


def write_manifest(out_path: Path, config_path: Path, world: dict):
    wind = wind_config(world)
    manifest = {
        "generated_sdf": str(out_path),
        "source_yaml": str(config_path),
        "world_name": world.get("name"),
        "texture_preset": require_mapping(world.get("texture"), "world.texture").get("preset"),
        "lighting_preset": require_mapping(world.get("lighting"), "world.lighting").get("preset"),
        "wind_enabled": wind["enabled"],
        "wind_mean_mps": wind.get("mean_mps"),
        "wind_direction_vector_enu": wind.get("direction_vector_enu"),
    }
    out_path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def apply_wind_to_world_file(source_path: Path, mean_mps: float, direction_vector_enu: list[float]) -> str:
    """Inject a <wind> block into an EXISTING world SDF/.world file's text,
    regardless of which pipeline generated it (Phase 17C, user-requested
    2026-07-24: "is it feasible to separate [terrain choice] and wind" -
    yes, because <wind> is just a sibling element of <world>'s ground
    content, not something build_gazebo_world.py's flat-ground generator
    owns). Returns the new SDF text; never modifies source_path itself -
    callers write it to a new file.

    Only ever inject the bare <wind> data tag - never a per-world
    <plugin> block for WindEffects. PX4's own server.config
    (src/modules/simulation/gz_bridge/server.config) already globally
    loads gz-sim-wind-effects-system for every world
    (entity_name="*" entity_type="world"), and GZ_SIM_SERVER_CONFIG_PATH
    is set in the shared subprocess env for standalone Gazebo launches
    too (auto_takeoff_land_pxh_truth.py) - so terrain worlds get it just
    as much as flat-ground ones, even though terrain worlds otherwise
    embed their own full self-contained <plugin> list. Embedding a
    second, per-world WindEffects <plugin> on top of that duplicates the
    system and deadlocks Gazebo startup - confirmed for the flat-ground
    case in Phase 16 (2026-07-22), and confirmed again for a terrain
    world (2026-07-26): the identical terrain loads in ~9s with only the
    <wind> tag added, but hangs indefinitely (no progress for 240+s, no
    crash, no error) with a duplicate <plugin> block also present. Fixed
    by never adding that block, for either world architecture.
    """
    text = source_path.read_text()
    if "<wind>" in text:
        raise ValueError(f"{source_path} already has a <wind> block - refusing to double-inject")

    wind = wind_config({"wind": {
        "enabled": True, "mean_mps": mean_mps, "direction_vector_enu": direction_vector_enu,
    }})
    insertion = wind_block(wind)

    # Insert right before the closing </world> tag - valid regardless of
    # the file's internal structure/ordering, never touches existing content.
    match = re.search(r"</world>", text)
    if not match:
        raise ValueError(f"{source_path}: no </world> closing tag found - not a valid world SDF")
    new_text = text[: match.start()] + insertion + text[match.start() :]

    ET.fromstring(new_text)  # validate before returning
    return new_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("configs", nargs="+", help="World YAML files to convert to SDF.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--no-manifest", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    for raw_config in args.configs:
        config_path = Path(raw_config).resolve()
        sdf, world = build_sdf(config_path)
        name = str(world.get("name", config_path.stem))
        out_path = out_dir / f"{name}.sdf"
        out_path.write_text(sdf)
        if not args.no_manifest:
            write_manifest(out_path, config_path, world)
        print(f"generated={out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
