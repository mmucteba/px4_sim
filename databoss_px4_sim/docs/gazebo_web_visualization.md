# Gazebo Web Visualization (Mac Browser Live View)

Status: Proven live on 2026-07-10 for normal generated worlds. Native
generated-terrain heightmap rendering in the Mac browser was repaired and
proven live on 2026-07-13.

## What this is

A browser-based 3D observer view of the running Gazebo simulation, served
from the headless VM over a websocket. It is a **viewer/monitor only**:

- DATABOSS remains the automation engine.
- QGroundControl remains the operator monitor for PX4 state.
- The web view shows the external observer camera, **not** the drone's
  downward camera sensor stream.

## Architecture and port convention

```text
gz sim (headless, GZ_PARTITION=databoss_<world>_<runner pid>)
  └─ gz-launch WebsocketServer          port 9003  (raw bridge, runner-managed)
       └─ enum-patch proxy              port 9002  (browser entry point)
            └─ SSH tunnel over Tailscale
                 └─ Mac browser: app.gazebosim.org/visualization
```

- **9003** = raw bridge. Started automatically by the runner when the
  scenario contains `visualization.gazebo_web.enabled: true` (both
  `phase8c_web_camera_*` scenarios now default to `port: 9003`).
- **9002** = enum-patch proxy. Browsers must connect through it (see bug
  below). Start it manually before or during a run:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py \
  --listen-port 9002 --upstream ws://127.0.0.1:9003
```

Dependency: the `websockets` package (already installed in the DATABOSS
venv on 2026-07-10).

For generated terrain worlds under `generated_worlds/terrain/*`, use the same
port convention but start the proxy with the terrain-only flags:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py \
  --listen-port 9002 \
  --upstream ws://127.0.0.1:9003 \
  --serve-generated-terrain-assets \
  --populate-generated-terrain-heightmaps \
  --log-file /tmp/gz_ws_proxy_frames.log
```

## Why the proxy is required (gz-launch bug)

gz-launch 7.1.2 (Harmonic) `WebsocketServer` omits **file-level enums** from
its `protos` response. Exactly two are missing: `PixelFormatType` and
`SphericalCoordinatesType`. The gzweb client then throws:

```text
Error: no such Type or Enum '.gz.msgs.PixelFormatType' in Type .gz.msgs.CameraSensor
```

while decoding the Scene message, and the viewport stays gray. This breaks
**every vehicle that carries a camera sensor** (`x500_mono_cam_down`,
`x500_depth`, ...). Camera-less vehicles (`x500`, `x500_lidar_down`) render
without the proxy.

By default, the proxy appends the two missing enum definitions
(`scripts/sim/gz_missing_proto_enums.txt`) to the `protos` response and
forwards scene frames unchanged. The generated-terrain behavior is opt-in:
`--serve-generated-terrain-assets` and
`--populate-generated-terrain-heightmaps` should be used only for local terrain
worlds under `/opt/databoss_px4_sim/generated_worlds/terrain`. Consider filing
the missing-enum defect upstream at gazebosim/gz-launch.

## Generated Terrain Worlds

This section applies only to generated terrain worlds under
`generated_worlds/terrain/*`. Do not apply these flags to flat generated worlds
or other worlds that already render correctly in the browser.

Preferred path: run the original generated terrain world, not a `_web_mesh`
copy, and connect the browser through the terrain-enabled proxy on port 9002.
For example, for Sereflikochisar:

```text
generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world
```

The 2026-07-13 diagnostic found that Gazebo and SceneBroadcaster do send the
terrain visual: `/world/serefli_koschisar/scene/info` contains
`ground_visual`, `HEIGHTMAP`, `height_map.png`, `aerial.png`,
`normal_map.png`, and the launch-pad box. The browser-side gap was twofold:

- the browser first tries to fetch absolute `/opt/.../aerial.png` and
  `/opt/.../normal_map.png` paths from `app.gazebosim.org`, which produces
  expected `404` rows before it falls back to websocket asset requests;
- Gazebo's Scene message carries the heightmap filename, texture, size, and
  origin, but this browser client expects inline `HeightmapGeom` samples:
  `width`, `height`, and `heights`.

The terrain-enabled proxy repairs only those two generated-terrain web-viewer
gaps. It serves allowlisted local PNG assets as `gz.msgs.Bytes` and populates
height samples from the local `height_map.png`. It must preserve the websocket
frame header (`pub,scene,gz.msgs.Scene,`); if the viewport goes gray after a
scene patch, check `/tmp/gz_ws_proxy_frames.log` for that header.

Expected browser/network behavior:

- Initial `404` rows for `aerial.png` and `normal_map.png` under
  `https://app.gazebosim.org/opt/...` are normal.
- Successful fallback appears as `data:image/png;base...` image rows.
- The rendered terrain should be the native heightmap surface with satellite
  texture, not a 32x32 tile mosaic.

Expected proxy log snippets:

```text
S->C protos response patched with missing enums
S->C scene heightmap samples populated 1x from local PNG
S->C BINARY ... head=b'pub,scene,gz.msgs.Scene'
C->S asset request uri='/opt/.../normal_map.png'
C->S asset served locally /opt/.../normal_map.png
C->S asset request uri='/opt/.../aerial.png'
C->S asset served locally /opt/.../aerial.png
```

### Emergency Colored-Tile Fallback

Use the colored-tile fallback only when the native terrain proxy path is not
available or the hosted browser client changes again. Do not make `_web_mesh`
worlds the default process.

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/worlds/heightmap_to_web_mesh_world.py \
  --source-world generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world \
  --output-dir generated_worlds/terrain/serefli_koschisar_web_mesh \
  --output-world-name serefli_koschisar_web_mesh \
  --visual-mode colored_tiles \
  --tile-count 32
```

Tile-count guidance:

- `32` = emergency fallback default, 1024 tile visuals, practical browser monitor.
- `64` = 4096 tile visuals, more detail, noticeably heavier.
- `512` = 262144 tile visuals, about a 151 MB world file in the
  Sereflikochisar test; Gazebo did not survive loading it on this VM.

The fallback visual appears "box by box" because it is a compatibility mosaic
of SDF box visuals sampled from `aerial.png`. The original terrain world
remains the authoritative source for PX4 physics, onboard camera frames,
optical-flow evidence, and provenance.

## Mac connection recipe

```bash
# Terminal (keep open). Server = ubuntu-4gb-fsn1-2 on Tailscale.
ssh -N -L 9002:127.0.0.1:9002 root@100.78.93.35
```

Browser: open `https://app.gazebosim.org/visualization`, set

```text
Websocket URL:      ws://localhost:9002
Authorization Key:  (leave empty)
```

`ws://localhost` counts as a secure context, so the HTTPS page may use it;
a direct `ws://<tailscale-ip>:9002` would be blocked as mixed content.

## Operational rules

1. The bridge only serves mesh/texture assets when its environment contains
   `GZ_SIM_RESOURCE_PATH` (PX4 models dir). The runner-managed bridge
   inherits the sim environment and is correct. A manually started bridge
   without it delivers empty assets: the world renders, the drone does not.
2. The bridge must run in the same `GZ_PARTITION` as the sim. The runner
   handles this; for manual bridges read the partition from the run's
   status JSON (`databoss_<world>_<runner pid>`).
3. The WebsocketServer **segfaults on malformed frames** (observed with a
   `scene` request with an empty world name). If 9003 stops listening,
   restart the bridge; the sim and PX4 are unaffected.
4. The bridge lives and dies with its run. When a run finishes, the browser
   view drops; reconnect after the next run starts.
5. Default publication is 15 Hz (`visualization.gazebo_web.publication_hz`).
   Raise only when needed.
6. For generated terrain browser runs, keep 9003 as the raw bridge and 9002 as
   the terrain-enabled proxy. The browser should still connect to
   `ws://localhost:9002`.

## Known limitations

- Observer view only; the downward camera image topic is separate.
- Rendering fidelity is not identical to desktop Gazebo. Simple meshes and
  primitive materials render fine. Generated terrain heightmaps render in the
  browser only through the opt-in terrain proxy flags described above.
- One gz-launch bug workaround in the path (the proxy). Re-test without the
  proxy after any gz-launch package upgrade.

## Evidence

```text
experiments/runs/20260710_075811_phase8c_web_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth  (accepted)
experiments/runs/20260710_083447_phase8c_web_camera_flat_rural_high_texture_noon_bridge9003_pxh_takeoff_land_truth  (live-viewed from the Mac)
generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world  (native heightmap live-viewed from the Mac on 2026-07-13)
docs/PROJECT_LOG.md entries dated 2026-07-10
docs/PROJECT_LOG.md entries dated 2026-07-13
```
