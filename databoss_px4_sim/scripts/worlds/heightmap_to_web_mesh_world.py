#!/usr/bin/env python3
"""Generate browser fallback worlds from generated Gazebo terrain heightmaps.

Use this only for generated terrain worlds under generated_worlds/terrain/*.
The source heightmap remains available as the collision geometry; this script
only replaces the browser-facing visual in a separate output world.

The reliable app.gazebosim.org fallback is:

    --visual-mode colored_tiles --tile-count 32

That mode renders the satellite image as a grid of flat SDF box visuals colored
from aerial.png. It is intentionally "box by box": gzweb handles primitive
materials reliably, while heightmap materials, Collada textures, embedded data
URIs, vertex colors, and model:// packaged mesh paths were all tested and were
not reliable in the browser. Mesh modes are retained for experiments.
"""
from __future__ import annotations

import argparse
import html
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
    arr = raw.astype(np.float64)
    if arr.ndim != 2:
        raise ValueError(f"expected grayscale heightmap, got shape {arr.shape}")
    max_value = float(np.iinfo(raw.dtype).max) if np.issubdtype(raw.dtype, np.integer) else 1.0
    return arr / max_value * z_size


def write_collada(
    out_path: Path,
    texture_path: Path,
    heights: np.ndarray,
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
    stride: int,
    material_mode: str,
) -> tuple[int, int]:
    rows = list(range(0, heights.shape[0], stride))
    cols = list(range(0, heights.shape[1], stride))
    if rows[-1] != heights.shape[0] - 1:
        rows.append(heights.shape[0] - 1)
    if cols[-1] != heights.shape[1] - 1:
        cols.append(heights.shape[1] - 1)

    width, depth, _ = size
    ox, oy, oz = origin
    max_row = heights.shape[0] - 1
    max_col = heights.shape[1] - 1

    positions: list[str] = []
    texcoords: list[str] = []
    colors: list[str] = []
    texture = Image.open(texture_path).convert("RGB")
    texture_arr = np.asarray(texture, dtype=np.float64) / 255.0
    tex_h, tex_w, _ = texture_arr.shape
    for r in rows:
        for c in cols:
            x = ox - width / 2.0 + width * (c / max_col)
            y = oy + depth / 2.0 - depth * (r / max_row)
            z = oz + float(heights[r, c])
            positions.append(f"{x:.6f} {y:.6f} {z:.6f}")
            texcoords.append(f"{c / max_col:.6f} {r / max_row:.6f}")
            tex_x = min(tex_w - 1, max(0, round((c / max_col) * (tex_w - 1))))
            tex_y = min(tex_h - 1, max(0, round((r / max_row) * (tex_h - 1))))
            red, green, blue = texture_arr[tex_y, tex_x]
            colors.append(f"{red:.6f} {green:.6f} {blue:.6f} 1.000000")

    col_count = len(cols)
    triangles: list[str] = []
    for r_idx in range(len(rows) - 1):
        for c_idx in range(len(cols) - 1):
            v00 = r_idx * col_count + c_idx
            v10 = v00 + 1
            v01 = (r_idx + 1) * col_count + c_idx
            v11 = v01 + 1
            triangles.append(f"{v00} {v00} {v00} {v01} {v01} {v01} {v10} {v10} {v10}")
            triangles.append(f"{v10} {v10} {v10} {v01} {v01} {v01} {v11} {v11} {v11}")

    position_text = " ".join(positions)
    texcoord_text = " ".join(texcoords)
    color_text = " ".join(colors)
    triangle_text = " ".join(triangles)
    vertex_count = len(positions)
    triangle_count = len(triangles)

    if material_mode == "texture":
        library_images = """    <image id=\"terrain_texture_image\" name=\"terrain_texture_image\">
      <init_from>../materials/textures/aerial.png</init_from>
    </image>"""
        effect_params = """        <newparam sid=\"terrain_surface\">
          <surface type=\"2D\"><init_from>terrain_texture_image</init_from></surface>
        </newparam>
        <newparam sid=\"terrain_sampler\">
          <sampler2D><source>terrain_surface</source></sampler2D>
        </newparam>"""
        diffuse = """<texture texture=\"terrain_sampler\" texcoord=\"TEXCOORD\"/>"""
        color_source = ""
        color_input = ""
    elif material_mode == "vertex_color":
        library_images = ""
        effect_params = ""
        diffuse = "<color>1 1 1 1</color>"
        color_source = f"""        <source id=\"terrain_colors\">
          <float_array id=\"terrain_colors_array\" count=\"{vertex_count * 4}\">{color_text}</float_array>
          <technique_common>
            <accessor source=\"#terrain_colors_array\" count=\"{vertex_count}\" stride=\"4\">
              <param name=\"R\" type=\"float\"/>
              <param name=\"G\" type=\"float\"/>
              <param name=\"B\" type=\"float\"/>
              <param name=\"A\" type=\"float\"/>
            </accessor>
          </technique_common>
        </source>
"""
        color_input = '          <input semantic=\"COLOR\" source=\"#terrain_colors\" offset=\"2\"/>\n'
    else:
        raise ValueError(f"unsupported material_mode: {material_mode}")

    out_path.write_text(
        f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<COLLADA xmlns=\"http://www.collada.org/2005/11/COLLADASchema\" version=\"1.4.1\">
  <asset>
    <unit name=\"meter\" meter=\"1\"/>
    <up_axis>Z_UP</up_axis>
  </asset>
  <library_images>
{library_images}
  </library_images>
  <library_effects>
    <effect id=\"terrain_effect\">
      <profile_COMMON>
{effect_params}
        <technique sid=\"common\">
          <lambert>
            <diffuse>{diffuse}</diffuse>
          </lambert>
        </technique>
      </profile_COMMON>
    </effect>
  </library_effects>
  <library_materials>
    <material id=\"terrain_material\" name=\"terrain_material\">
      <instance_effect url=\"#terrain_effect\"/>
    </material>
  </library_materials>
  <library_geometries>
    <geometry id=\"terrain_geometry\" name=\"terrain_geometry\">
      <mesh>
        <source id=\"terrain_positions\">
          <float_array id=\"terrain_positions_array\" count=\"{vertex_count * 3}\">{position_text}</float_array>
          <technique_common>
            <accessor source=\"#terrain_positions_array\" count=\"{vertex_count}\" stride=\"3\">
              <param name=\"X\" type=\"float\"/>
              <param name=\"Y\" type=\"float\"/>
              <param name=\"Z\" type=\"float\"/>
            </accessor>
          </technique_common>
        </source>
        <source id=\"terrain_texcoords\">
          <float_array id=\"terrain_texcoords_array\" count=\"{vertex_count * 2}\">{texcoord_text}</float_array>
          <technique_common>
            <accessor source=\"#terrain_texcoords_array\" count=\"{vertex_count}\" stride=\"2\">
              <param name=\"S\" type=\"float\"/>
              <param name=\"T\" type=\"float\"/>
            </accessor>
          </technique_common>
        </source>
{color_source}        <vertices id=\"terrain_vertices\">
          <input semantic=\"POSITION\" source=\"#terrain_positions\"/>
        </vertices>
        <triangles material=\"terrain_material\" count=\"{triangle_count}\">
          <input semantic=\"VERTEX\" source=\"#terrain_vertices\" offset=\"0\"/>
          <input semantic=\"TEXCOORD\" source=\"#terrain_texcoords\" offset=\"1\" set=\"0\"/>
{color_input}          <p>{triangle_text}</p>
        </triangles>
      </mesh>
    </geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id=\"Scene\" name=\"Scene\">
      <node id=\"terrain\" name=\"terrain\">
        <instance_geometry url=\"#terrain_geometry\">
          <bind_material>
            <technique_common>
              <instance_material symbol=\"terrain_material\" target=\"#terrain_material\">
                <bind_vertex_input semantic=\"TEXCOORD\" input_semantic=\"TEXCOORD\" input_set=\"0\"/>
              </instance_material>
            </technique_common>
          </bind_material>
        </instance_geometry>
      </node>
    </visual_scene>
  </library_visual_scenes>
  <scene><instance_visual_scene url=\"#Scene\"/></scene>
</COLLADA>
""",
        encoding="utf-8",
    )
    return vertex_count, triangle_count


def build_world_text(source_text: str, output_world_name: str) -> str:
    mesh_visual = """<visual name=\"ground_visual_mesh\">
                    <geometry>
                        <mesh>
                            <uri>mesh/web_terrain.dae</uri>
                        </mesh>
                    </geometry>
                </visual>"""
    text = re.sub(r"<world name=\"[^\"]+\">", f"<world name=\"{output_world_name}\">", source_text, count=1)
    text = re.sub(
        r"<visual\s+name=\"ground_visual\">.*?</visual>",
        mesh_visual,
        text,
        count=1,
        flags=re.DOTALL,
    )
    return text


def build_model_uri_world_text(source_text: str, output_world_name: str, model_name: str) -> str:
    mesh_visual = f"""<visual name=\"ground_visual_model_mesh\">
                    <geometry>
                        <mesh>
                            <uri>model://{model_name}/meshes/web_terrain.dae</uri>
                        </mesh>
                    </geometry>
                </visual>"""
    text = re.sub(r"<world name=\"[^\"]+\">", f"<world name=\"{output_world_name}\">", source_text, count=1)
    return re.sub(
        r"<visual\s+name=\"ground_visual\">.*?</visual>",
        mesh_visual,
        text,
        count=1,
        flags=re.DOTALL,
    )


def write_model_package(model_dir: Path, model_name: str) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.config").write_text(
        f"""<?xml version='1.0'?>
<model>
  <name>{html.escape(model_name)}</name>
  <version>1.0</version>
  <sdf version='1.9'>model.sdf</sdf>
  <description>Sereflikochisar terrain web-view mesh fallback.</description>
</model>
""",
        encoding="utf-8",
    )
    (model_dir / "model.sdf").write_text(
        f"""<?xml version='1.0'?>
<sdf version='1.9'>
  <model name='{html.escape(model_name)}'>
    <static>true</static>
    <link name='link'>
      <visual name='visual'>
        <geometry>
          <mesh>
            <uri>model://{html.escape(model_name)}/meshes/web_terrain.dae</uri>
          </mesh>
        </geometry>
      </visual>
    </link>
  </model>
</sdf>
""",
        encoding="utf-8",
    )


def build_colored_tile_visuals(
    heights: np.ndarray,
    texture_path: Path,
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
    tile_count: int,
) -> str:
    texture = Image.open(texture_path).convert("RGB")
    texture_arr = np.asarray(texture, dtype=np.float64) / 255.0
    tex_h, tex_w, _ = texture_arr.shape
    width, depth, _ = size
    ox, oy, oz = origin
    max_row = heights.shape[0] - 1
    max_col = heights.shape[1] - 1
    tile_w = width / tile_count
    tile_d = depth / tile_count
    tile_thickness = 0.08

    visuals: list[str] = []
    for row in range(tile_count):
        r0 = round(row / tile_count * max_row)
        r1 = round((row + 1) / tile_count * max_row)
        tex_r0 = round(row / tile_count * (tex_h - 1))
        tex_r1 = round((row + 1) / tile_count * (tex_h - 1))
        for col in range(tile_count):
            c0 = round(col / tile_count * max_col)
            c1 = round((col + 1) / tile_count * max_col)
            tex_c0 = round(col / tile_count * (tex_w - 1))
            tex_c1 = round((col + 1) / tile_count * (tex_w - 1))

            z = oz + float(np.mean(heights[r0 : r1 + 1, c0 : c1 + 1]))
            rgb = np.mean(texture_arr[tex_r0 : tex_r1 + 1, tex_c0 : tex_c1 + 1], axis=(0, 1))
            red, green, blue = [float(value) for value in rgb]
            x = ox - width / 2.0 + (col + 0.5) * tile_w
            y = oy + depth / 2.0 - (row + 0.5) * tile_d
            visuals.append(
                f"""                <visual name=\"terrain_tile_{row:03d}_{col:03d}\">
                    <pose>{x:.6f} {y:.6f} {z - tile_thickness / 2.0:.6f} 0 0 0</pose>
                    <geometry>
                        <box>
                            <size>{tile_w:.6f} {tile_d:.6f} {tile_thickness:.6f}</size>
                        </box>
                    </geometry>
                    <material>
                        <ambient>{red:.6f} {green:.6f} {blue:.6f} 1</ambient>
                        <diffuse>{red:.6f} {green:.6f} {blue:.6f} 1</diffuse>
                        <specular>0 0 0 1</specular>
                    </material>
                </visual>"""
            )
    return "\n".join(visuals)


def build_colored_tile_world_text(
    source_text: str,
    output_world_name: str,
    heights: np.ndarray,
    texture_path: Path,
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
    tile_count: int,
) -> str:
    tile_visuals = build_colored_tile_visuals(heights, texture_path, size, origin, tile_count)
    text = re.sub(r"<world name=\"[^\"]+\">", f"<world name=\"{output_world_name}\">", source_text, count=1)
    return re.sub(
        r"<visual\s+name=\"ground_visual\">.*?</visual>",
        tile_visuals,
        text,
        count=1,
        flags=re.DOTALL,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-world",
        default="generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_worlds/terrain/serefli_koschisar_web_mesh",
    )
    parser.add_argument("--output-world-name", default="serefli_koschisar_web_mesh")
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument(
        "--visual-mode",
        choices=("mesh", "colored_tiles", "fuel_model_mesh"),
        default="colored_tiles",
        help=(
            "Browser-facing terrain visual representation. Use colored_tiles "
            "for generated terrain worlds viewed in app.gazebosim.org."
        ),
    )
    parser.add_argument(
        "--tile-count",
        type=int,
        default=32,
        help=(
            "Tiles per side for colored_tiles. 32 is the process default; "
            "64 is heavier but usable; 512 generated a 151 MB SDF and did not load."
        ),
    )
    parser.add_argument("--model-name", default="serefli_koschisar_web_terrain")
    args = parser.parse_args()

    if args.stride < 1:
        raise ValueError("--stride must be >= 1")
    if args.tile_count < 1:
        raise ValueError("--tile-count must be >= 1")

    source_world = (PROJECT_ROOT / args.source_world).resolve()
    output_dir = (PROJECT_ROOT / args.output_dir).resolve()
    source_dir = source_world.parent
    if args.visual_mode == "fuel_model_mesh":
        output_mesh_dir = output_dir / args.model_name / "meshes"
        output_texture_dir = output_dir / args.model_name / "materials" / "textures"
    else:
        output_mesh_dir = output_dir / "mesh"
        output_texture_dir = output_mesh_dir
    output_mesh_dir.mkdir(parents=True, exist_ok=True)
    output_texture_dir.mkdir(parents=True, exist_ok=True)

    source_text = source_world.read_text(encoding="utf-8")
    height_uri, size, origin = read_heightmap_spec(source_text)
    height_path = (source_dir / height_uri).resolve()
    aerial_path = source_dir / "mesh" / "aerial.png"
    normal_path = source_dir / "mesh" / "normal_map.png"

    shutil.copy2(height_path, output_mesh_dir / "height_map.png")
    shutil.copy2(aerial_path, output_texture_dir / "aerial.png")
    if normal_path.exists():
        shutil.copy2(normal_path, output_texture_dir / "normal_map.png")

    heights = height_values(height_path, size[2])
    material_mode = "texture" if args.visual_mode == "fuel_model_mesh" else "vertex_color"
    vertices, triangles = write_collada(
        output_mesh_dir / "web_terrain.dae",
        output_texture_dir / "aerial.png",
        heights,
        size,
        origin,
        args.stride,
        material_mode,
    )

    output_world = output_dir / f"{args.output_world_name}.world"
    if args.visual_mode == "colored_tiles":
        world_text = build_colored_tile_world_text(
            source_text,
            args.output_world_name,
            heights,
            output_mesh_dir / "aerial.png",
            size,
            origin,
            args.tile_count,
        )
    elif args.visual_mode == "fuel_model_mesh":
        write_model_package(output_dir / args.model_name, args.model_name)
        world_text = build_model_uri_world_text(source_text, args.output_world_name, args.model_name)
    else:
        world_text = build_world_text(source_text, args.output_world_name)
    output_world.write_text(world_text, encoding="utf-8")

    print(f"source_world={source_world}")
    print(f"output_world={output_world}")
    print(f"mesh={output_mesh_dir / 'web_terrain.dae'}")
    print(f"vertices={vertices}")
    print(f"triangles={triangles}")
    print(f"stride={args.stride}")
    print(f"visual_mode={args.visual_mode}")
    if args.visual_mode == "fuel_model_mesh":
        print(f"model={output_dir / args.model_name}")
    if args.visual_mode == "colored_tiles":
        print(f"tiles={args.tile_count * args.tile_count}")


if __name__ == "__main__":
    main()
