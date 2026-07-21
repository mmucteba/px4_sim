#!/usr/bin/env python3
"""Websocket proxy for Gazebo web visualization of camera-equipped vehicles.

gz-launch 7.1.2 (Harmonic) WebsocketServer omits file-level enums
(PixelFormatType, SphericalCoordinatesType) from its `protos` response.
The gzweb client (app.gazebosim.org/visualization) then throws
"no such Type or Enum '.gz.msgs.PixelFormatType' in Type .gz.msgs.CameraSensor"
while decoding any scene that contains a camera sensor, and the viewport
stays gray. This proxy sits in front of the real bridge and appends the
missing enum definitions to the protos response.

Normal operation only patches the protobuf definition response. Scene frames,
geometry, meshes, and terrain assets are forwarded unchanged.

Usage (bridge on 9003, browser connects to 9002):

    python3 scripts/sim/gz_websocket_enum_patch_proxy.py \
        --listen-port 9002 --upstream ws://127.0.0.1:9003 \
        --log-file /tmp/gz_ws_proxy_frames.log

Requires the `websockets` package (installed in the DATABOSS venv).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import websockets

ENUM_PATCH_PATH = Path(__file__).with_name("gz_missing_proto_enums.txt")
SYSTEM_DIST_PACKAGES = Path("/usr/lib/python3/dist-packages")
if str(SYSTEM_DIST_PACKAGES) not in sys.path:
    sys.path.append(str(SYSTEM_DIST_PACKAGES))

try:
    from gz.msgs10.scene_pb2 import Scene
except Exception:  # pragma: no cover - optional system package
    Scene = None

try:
    from gz.msgs10.bytes_pb2 import Bytes
except Exception:  # pragma: no cover - optional system package
    Bytes = None

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

DEFAULT_TERRAIN_ASSET_ROOT = Path(
    "/opt/databoss_px4_sim/generated_worlds/terrain"
)
ALLOWED_LOCAL_ASSET_SUFFIXES = {
    ".dae",
    ".jpg",
    ".jpeg",
    ".mtl",
    ".obj",
    ".png",
    ".stl",
}
HEIGHTMAP_SAMPLE_CACHE: dict[tuple[Path, float], tuple[int, int, tuple[float, ...]]] = {}
TERRAIN_ABSOLUTE_MESH = (
    "/opt/databoss_px4_sim/generated_worlds/terrain/"
    "serefli_koschisar_web_mesh/serefli_koschisar_web_terrain/"
    "meshes/web_terrain.dae"
)
TERRAIN_MODEL_URI_MESH = (
    "model://serefli_koschisar_web_terrain/meshes/web_terrain.dae"
)


def parse_asset_request(msg: str) -> str | None:
    """Return the requested asset URI from a gz-launch websocket text frame."""
    parts = msg.split(",", 3)
    if len(parts) == 4 and parts[0] == "asset":
        return parts[3]
    return None


def path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_local_asset(uri: str, roots: list[Path]) -> Path | None:
    """Resolve an asset URI to an allowlisted local file, if possible."""
    parsed = urlparse(unquote(uri))
    if parsed.scheme == "file":
        candidate = Path(parsed.path)
    elif parsed.scheme == "" and uri.startswith("/"):
        candidate = Path(unquote(uri))
    else:
        return None

    if candidate.suffix.lower() not in ALLOWED_LOCAL_ASSET_SUFFIXES:
        return None

    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        return None

    for root in roots:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError:
            continue
        if path_is_relative_to(candidate, resolved_root):
            return candidate

    return None


def build_asset_response(uri: str, payload: bytes) -> bytes | None:
    if Bytes is None:
        return None
    msg = Bytes()
    msg.data = payload
    return f"asset,{uri},gz.msgs.Bytes,".encode("utf-8") + msg.SerializeToString()


def load_heightmap_samples(
    path: Path, size_z: float
) -> tuple[int, int, tuple[float, ...]] | None:
    """Load a Gazebo heightmap image as local z samples for gzweb."""
    if Image is None:
        return None

    cache_key = (path, float(size_z))
    if cache_key in HEIGHTMAP_SAMPLE_CACHE:
        return HEIGHTMAP_SAMPLE_CACHE[cache_key]

    image = Image.open(path)
    width, height = image.size
    raw = list(image.getdata())
    if not raw:
        return None

    if isinstance(raw[0], tuple):
        values = [sum(pixel[:3]) / (3 * 255.0) for pixel in raw]
    else:
        max_sample = 65535.0 if image.mode.startswith("I;16") else float(max(raw) or 1)
        values = [float(pixel) / max_sample for pixel in raw]

    heights = tuple(value * float(size_z) for value in values)
    result = (width, height, heights)
    HEIGHTMAP_SAMPLE_CACHE[cache_key] = result
    return result


def populate_scene_heightmap_samples(
    payload: bytes, roots: list[Path]
) -> tuple[bytes, int]:
    """Populate missing HeightmapGeom width/height/heights from local PNGs."""
    if Scene is None:
        return payload, 0

    scene = Scene()
    try:
        scene.ParseFromString(payload)
    except Exception:
        return payload, 0

    populated = 0
    for model in scene.model:
        for link in model.link:
            for visual in link.visual:
                try:
                    has_heightmap = visual.geometry.HasField("heightmap")
                except ValueError:
                    has_heightmap = False
                if not has_heightmap:
                    continue

                heightmap = visual.geometry.heightmap
                if not heightmap.filename:
                    continue

                local_heightmap = resolve_local_asset(heightmap.filename, roots)
                if local_heightmap is None or local_heightmap.suffix.lower() != ".png":
                    continue

                samples = load_heightmap_samples(local_heightmap, heightmap.size.z)
                if samples is None:
                    continue

                width, height, heights = samples
                if (
                    heightmap.width == width
                    and heightmap.height == height
                    and len(heightmap.heights) == len(heights)
                ):
                    continue

                heightmap.width = width
                heightmap.height = height
                del heightmap.heights[:]
                heightmap.heights.extend(heights)
                populated += 1

    if populated == 0:
        return payload, 0
    return scene.SerializeToString(), populated


def rewrite_scene_meshes(payload: bytes) -> tuple[bytes, int]:
    """Rewrite terrain mesh paths inside a gz.msgs.Scene payload.

    This is an opt-in experiment retained for debugging only. It did not fix
    app.gazebosim.org terrain mesh loading because the browser still could not
    resolve the packaged mesh/texture resources reliably.
    """
    if Scene is None or TERRAIN_ABSOLUTE_MESH.encode() not in payload:
        return payload, 0

    scene = Scene()
    try:
        scene.ParseFromString(payload)
    except Exception:
        return payload, 0

    rewrites = 0
    for model in scene.model:
        for link in model.link:
            for visual in link.visual:
                mesh = visual.geometry.mesh
                if mesh.filename == TERRAIN_ABSOLUTE_MESH:
                    mesh.filename = TERRAIN_MODEL_URI_MESH
                    rewrites += 1

    if rewrites == 0:
        return payload, 0
    return scene.SerializeToString(), rewrites


def patch_binary_scene_frame(
    msg: bytes, patch_payload
) -> tuple[bytes, int]:
    """Patch a Scene protobuf whether it is the whole frame or after CSV-ish headers."""
    parts = msg.split(b",", 3)
    if len(parts) == 4 and parts[0] == b"pub" and parts[2] == b"gz.msgs.Scene":
        prefix = b",".join(parts[:3]) + b","
        patched_payload, count = patch_payload(parts[3])
        if count:
            return prefix + patched_payload, count
        return msg, 0

    if b"," not in msg[:128]:
        patched, count = patch_payload(msg)
        if count:
            return patched, count

    # gz-launch websocket frames commonly prefix protobuf payloads with a few
    # comma-separated fields. Preserve that envelope if parsing the suffix works.
    search_from = 0
    for _ in range(8):
        comma = msg.find(b",", search_from)
        if comma < 0 or comma + 1 >= len(msg):
            break
        prefix = msg[: comma + 1]
        payload = msg[comma + 1 :]
        patched_payload, count = patch_payload(payload)
        if count:
            return prefix + patched_payload, count
        search_from = comma + 1

    return msg, 0


def rewrite_binary_frame(msg: bytes) -> tuple[bytes, int]:
    return patch_binary_scene_frame(msg, rewrite_scene_meshes)


def populate_binary_heightmap_frame(
    msg: bytes, roots: list[Path]
) -> tuple[bytes, int]:
    return patch_binary_scene_frame(
        msg, lambda payload: populate_scene_heightmap_samples(payload, roots)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen-port", type=int, default=9002)
    parser.add_argument("--upstream", default="ws://127.0.0.1:9003")
    parser.add_argument("--log-file", default=None, help="Optional frame log path")
    parser.add_argument(
        "--rewrite-terrain-mesh-model-uri",
        action="store_true",
        help=(
            "Experimental Sereflikochisar mesh URI rewrite. Off by default; "
            "normal terrain web fallback uses colored SDF tiles instead."
        ),
    )
    parser.add_argument(
        "--serve-generated-terrain-assets",
        action="store_true",
        help=(
            "Opt-in repair for app.gazebosim.org heightmap texture fetches. "
            "When the browser asks for an absolute local asset under "
            f"{DEFAULT_TERRAIN_ASSET_ROOT}, answer with a gz.msgs.Bytes asset "
            "frame instead of forwarding the request upstream."
        ),
    )
    parser.add_argument(
        "--populate-generated-terrain-heightmaps",
        action="store_true",
        help=(
            "Opt-in repair for generated terrain Scene messages. If a "
            "heightmap visual references a local PNG under the generated "
            "terrain asset root, populate gz.msgs.HeightmapGeom "
            "width/height/heights for the web client."
        ),
    )
    parser.add_argument(
        "--serve-local-assets-root",
        action="append",
        default=[],
        help=(
            "Additional local asset root to serve for browser asset requests. "
            "Repeatable; only common mesh/image suffixes are served."
        ),
    )
    args = parser.parse_args()

    patch = ENUM_PATCH_PATH.read_bytes()
    log_handle = open(args.log_file, "a", buffering=1) if args.log_file else None
    local_asset_roots = [Path(p) for p in args.serve_local_assets_root]
    if args.serve_generated_terrain_assets or args.populate_generated_terrain_heightmaps:
        local_asset_roots.insert(0, DEFAULT_TERRAIN_ASSET_ROOT)

    def log(line: str) -> None:
        if log_handle is not None:
            ts = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            log_handle.write(f"{ts} {line}\n")

    async def pump(src, dst, direction: str) -> None:
        try:
            async for msg in src:
                if direction == "C->S" and isinstance(msg, str):
                    asset_uri = parse_asset_request(msg)
                    if asset_uri is not None:
                        log(f"C->S asset request uri={asset_uri!r}")
                        local_asset = resolve_local_asset(
                            asset_uri, local_asset_roots
                        )
                        if local_asset is not None:
                            response = build_asset_response(
                                asset_uri, local_asset.read_bytes()
                            )
                            if response is not None:
                                await src.send(response)
                                log(
                                    "C->S asset served locally "
                                    f"{local_asset} ({len(response)}B frame)"
                                )
                                continue
                            log(
                                "C->S local asset match but gz.msgs.Bytes "
                                "is unavailable; forwarding upstream"
                            )
                if (
                    direction == "S->C"
                    and isinstance(msg, bytes)
                    and msg.startswith(b'syntax = "proto3"')
                ):
                    msg = msg + patch
                    log("S->C protos response patched with missing enums")
                elif (
                    args.rewrite_terrain_mesh_model_uri
                    and direction == "S->C"
                    and isinstance(msg, bytes)
                ):
                    patched_msg, rewrites = rewrite_binary_frame(msg)
                    if rewrites:
                        msg = patched_msg
                        log(
                            "S->C scene mesh filename patched "
                            f"{rewrites}x to {TERRAIN_MODEL_URI_MESH}"
                        )
                if (
                    args.populate_generated_terrain_heightmaps
                    and direction == "S->C"
                    and isinstance(msg, bytes)
                ):
                    patched_msg, populated = populate_binary_heightmap_frame(
                        msg, local_asset_roots
                    )
                    if populated:
                        msg = patched_msg
                        log(
                            "S->C scene heightmap samples populated "
                            f"{populated}x from local PNG"
                        )
                if isinstance(msg, bytes):
                    head = msg.split(b",", 3)[:3]
                    log(f"{direction} BINARY {len(msg)}B head={b','.join(head)[:120]!r}")
                else:
                    log(f"{direction} TEXT {len(msg)}c {msg[:200]!r}")
                await dst.send(msg)
        except websockets.exceptions.ConnectionClosed as exc:
            log(f"{direction} closed: code={exc.code} reason={exc.reason!r}")

    async def handle(client) -> None:
        log(f"client connected: {client.remote_address}")
        try:
            async with websockets.connect(args.upstream, max_size=None) as upstream:
                await asyncio.gather(
                    pump(client, upstream, "C->S"),
                    pump(upstream, client, "S->C"),
                )
        except Exception as exc:
            log(f"proxy error: {exc}")
        finally:
            log(f"client disconnected: {client.remote_address}")

    async def serve() -> None:
        async with websockets.serve(handle, "0.0.0.0", args.listen_port, max_size=None):
            print(
                f"enum-patch proxy listening on :{args.listen_port} -> {args.upstream}"
            )
            await asyncio.Future()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
