#!/usr/bin/env python3
"""Create a Sereflikochisar terrain variant with flow-scale camera texture.

The imported terrain collision and coarse aerial heightmap stay intact. This
adds a visual-only textured overlay over the route area plus a textured launch
pad visual so the downward camera does not see the old gray pad / coarse
0.9 m/px aerial image during optical-flow runs.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_vec3(text: str) -> tuple[float, float, float]:
    values = [float(item) for item in text.split()]
    if len(values) != 3:
        raise ValueError(f"expected vec3, got {text!r}")
    return values[0], values[1], values[2]


def read_heightmap_spec(world_text: str) -> tuple[str, tuple[float, float, float], tuple[float, float, float]]:
    visual_match = re.search(
        r"<visual\s+name=\"ground_visual\">(?P<body>.*?)</visual>",
        world_text,
        flags=re.DOTALL,
    )
    if not visual_match:
        raise ValueError("source world does not contain visual name=\"ground_visual\"")

    body = visual_match.group("body")
    uri_match = re.search(r"<uri>([^<]+)</uri>", body)
    size_matches = re.findall(r"<size>([^<]+)</size>", body)
    pos_match = re.search(r"<pos>([^<]+)</pos>", body)
    size_text = next((item.strip() for item in size_matches if len(item.split()) == 3), None)
    if not (uri_match and size_text and pos_match):
        raise ValueError("ground_visual heightmap is missing uri, size, or pos")

    return (
        uri_match.group(1).strip(),
        parse_vec3(size_text),
        parse_vec3(pos_match.group(1).strip()),
    )


def height_values(height_path: Path, z_size: float) -> np.ndarray:
    image = Image.open(height_path)
    raw = np.asarray(image)
    if raw.ndim != 2:
        raise ValueError(f"expected grayscale heightmap, got shape {raw.shape}")
    arr = raw.astype(np.float64)
    max_value = float(np.iinfo(raw.dtype).max) if np.issubdtype(raw.dtype, np.integer) else 1.0
    return arr / max_value * z_size


def terrain_z(
    heights: np.ndarray,
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
    x: float,
    y: float,
) -> float:
    width, depth, _ = size
    ox, oy, oz = origin
    col_f = (x - (ox - width / 2.0)) / width * (heights.shape[1] - 1)
    row_f = ((oy + depth / 2.0) - y) / depth * (heights.shape[0] - 1)
    row = min(heights.shape[0] - 1, max(0, round(row_f)))
    col = min(heights.shape[1] - 1, max(0, round(col_f)))
    return oz + float(heights[row, col])


def textured_material(texture_uri: str) -> str:
    return f"""<material>
                        <ambient>1 1 1 1</ambient>
                        <diffuse>1 1 1 1</diffuse>
                        <specular>0 0 0 1</specular>
                        <pbr>
                            <metal>
                                <albedo_map>{texture_uri}</albedo_map>
                                <roughness>1.0</roughness>
                                <metalness>0.0</metalness>
                            </metal>
                        </pbr>
                    </material>"""


def build_overlay_model(
    heights: np.ndarray,
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
    texture_uri: str,
    overlay_size_m: float,
    tile_size_m: float,
    z_offset_m: float,
) -> str:
    count = int(round(overlay_size_m / tile_size_m))
    if count < 1:
        raise ValueError("overlay size must contain at least one tile")
    start = -overlay_size_m / 2.0 + tile_size_m / 2.0
    visuals: list[str] = []
    material = textured_material(texture_uri)
    thickness = 0.03

    for ix in range(count):
        x = start + ix * tile_size_m
        for iy in range(count):
            y = start + iy * tile_size_m
            z = terrain_z(heights, size, origin, x, y) + z_offset_m
            visuals.append(
                f"""            <visual name=\"flow_tile_{ix:03d}_{iy:03d}\">
                <pose>{x:.6f} {y:.6f} {z - thickness / 2.0:.6f} 0 0 0</pose>
                <geometry>
                    <box>
                        <size>{tile_size_m:.6f} {tile_size_m:.6f} {thickness:.6f}</size>
                    </box>
                </geometry>
                {material}
            </visual>"""
            )

    return f"""        <model name=\"databoss_flow_detail_overlay\">
            <static>true</static>
            <link name=\"visual_tiles\">
{chr(10).join(visuals)}
            </link>
        </model>
"""


def texture_launch_pad(world_text: str, texture_uri: str) -> str:
    pad_match = re.search(
        r"(<model\s+name=\"databoss_launch_pad\">.*?<visual\s+name=\"v\">)(?P<body>.*?)(</visual>.*?</model>)",
        world_text,
        flags=re.DOTALL,
    )
    if not pad_match:
        raise ValueError("source world does not contain databoss_launch_pad visual")
    body = pad_match.group("body")
    body = re.sub(
        r"<material>.*?</material>",
        textured_material(texture_uri),
        body,
        count=1,
        flags=re.DOTALL,
    )
    return world_text[: pad_match.start("body")] + body + world_text[pad_match.end("body") :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-world",
        default="generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_worlds/terrain/serefli_koschisar_flowtex",
    )
    parser.add_argument("--output-world-name", default="serefli_koschisar_flowtex")
    parser.add_argument(
        "--texture",
        default="generated_worlds/textures/ground_speckle_2048.png",
    )
    parser.add_argument("--overlay-size-m", type=float, default=80.0)
    parser.add_argument("--tile-size-m", type=float, default=20.0)
    parser.add_argument("--z-offset-m", type=float, default=0.08)
    args = parser.parse_args()

    source_world = (PROJECT_ROOT / args.source_world).resolve()
    source_dir = source_world.parent
    output_dir = (PROJECT_ROOT / args.output_dir).resolve()
    output_mesh_dir = output_dir / "mesh"
    texture_src = (PROJECT_ROOT / args.texture).resolve()
    texture_name = "flow_detail.png"
    texture_uri = f"mesh/{texture_name}"

    output_mesh_dir.mkdir(parents=True, exist_ok=True)
    for rel in ("mesh/height_map.png", "mesh/aerial.png", "mesh/normal_map.png"):
        src = source_dir / rel
        if src.exists():
            shutil.copy2(src, output_dir / rel)
    shutil.copy2(texture_src, output_mesh_dir / texture_name)

    world_text = source_world.read_text(encoding="utf-8")
    height_uri, size, origin = read_heightmap_spec(world_text)
    heights = height_values((source_dir / height_uri).resolve(), size[2])

    world_text = re.sub(
        r"<world name=\"[^\"]+\">",
        f"<world name=\"{args.output_world_name}\">",
        world_text,
        count=1,
    )
    world_text = texture_launch_pad(world_text, texture_uri)
    overlay = build_overlay_model(
        heights,
        size,
        origin,
        texture_uri,
        args.overlay_size_m,
        args.tile_size_m,
        args.z_offset_m,
    )
    world_text = world_text.replace("    </world>", overlay + "\n    </world>", 1)

    output_world = output_dir / f"{args.output_world_name}.world"
    output_world.write_text(world_text, encoding="utf-8")
    if (source_dir / "model.config").exists():
        shutil.copy2(source_dir / "model.config", output_dir / "model.config")
    if (source_dir / "PROVENANCE.yaml").exists():
        shutil.copy2(source_dir / "PROVENANCE.yaml", output_dir / "PROVENANCE.yaml")

    print(f"source_world={source_world}")
    print(f"output_world={output_world}")
    print(f"texture={output_mesh_dir / texture_name}")
    print(f"overlay_size_m={args.overlay_size_m}")
    print(f"tile_size_m={args.tile_size_m}")
    print(f"tiles={int(round(args.overlay_size_m / args.tile_size_m)) ** 2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
