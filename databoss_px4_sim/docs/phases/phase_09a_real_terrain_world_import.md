# Phase 9A — Real-Terrain World Import (gazebo_terrain_generator)

Status: Accepted (2026-07-10); native web heightmap rendering repaired
(2026-07-13)

## Goal

Prove that real-world heightmap terrain worlds produced by
[gazebo_terrain_generator](https://github.com/saiaravind19/gazebo_terrain_generator)
(Mapbox elevation + satellite imagery, BSD-3) can be used as DATABOSS
experiment worlds: headless launch, PX4 spawn, full takeoff/hover/land run,
truth recording, and EKF-vs-truth alignment.

## Why this phase exists

Our target environments are rural fields, hills, ridges, valleys, and
mountain terrain. The YAML world generator (Phase 8B) produces flat primitive
worlds; this tool produces georeferenced heightmaps with real satellite
texture — physically real camera input for the optical-flow phases and real
AGL variation for the TF03 rangefinder work.

## In scope

- Tool checkout at `/opt/gazebo_terrain_generator` (263 MB, ships two sample worlds).
- Sample-world compatibility proof (no Mapbox key needed).
- PX4 flight proof in a terrain world through the existing runner.
- Provenance rules for imported worlds.

## Out of scope

- Custom world generation via the web UI / Mapbox key (next step after the flight proof).
- Browser visualization as scientific evidence. app.gazebosim.org is an
  operator monitor only; the original heightmap world remains the authoritative
  physics/camera world. Native browser rendering for generated terrain now uses
  the terrain proxy flags documented in `../gazebo_web_visualization.md`.
- 3D buildings.

## Evidence so far (2026-07-10)

Run folder: `experiments/runs/20260710_105803_phase9a_terrain_world_launch_spawn_proof`

Proven on the Joshimath sample (15.4 × 11.4 km heightmap, 3325 m relief,
spherical coordinates lat 30.567683 / lon 79.550647 / elev 1382.2):

1. World launches headless (server-only, user px4, own partition). Physics
   does not require the render engine.
2. Truth topics exist: `/world/Joshimath/dynamic_pose/info` etc.
3. PX4 `x500` spawns via the create service **only when the Gazebo server is
   launched with `GZ_SIM_RESOURCE_PATH` pointing at the PX4 models dir**
   (first attempt failed with `Unable to find uri[model://x500_base]`; the
   DATABOSS runner already sets this).
4. Collision is real: x500 dropped from z=5.0 settled at z=2.9970 on the
   helipad (top at 3.0 m), level, no fall-through.
5. Memory on the 3.7 GiB VM: ~1.6 GiB still available with world + vehicle
   loaded. Server-only terrain worlds are cheap.

## Terrain flight proof (2026-07-10) — ACCEPTED

Run: `experiments/runs/20260710_111630_phase9a_flight_joshimath_terrain_pxh_takeoff_land_truth`
Scenario: `experiments/configs/mvp/scenarios/phase9a_flight_joshimath_terrain.yaml`

- Plain `gz_x500`, spawn on the helipad via new `vehicle.start_pose` →
  `PX4_GZ_MODEL_POSE` runner support (z=3.3 m).
- PX4 home set from the world via new `world.home` runner support:
  console shows `Setting world origin to lat: 30.567683429, lon: 79.550647130, alt: 1382.20`.
- Armed, took off, hovered (ULog airborne 104.1 s, max height 2.549 m),
  landed, disarmed by landing. Run accepted end-to-end (exit 0).
- Gazebo truth recorded (18.7 MB raw) and EKF-vs-truth alignment passed on
  non-flat terrain: H mean 0.0580 m / max 0.1233 m, height mean 0.0179 m,
  3D max 0.1255 m — same error class as the flat generated worlds.

### Import-preparation rule discovered (required for every imported world)

The generator's world template declares world-level plugins (Physics,
UserCommands, SceneBroadcaster, Sensors, Imu, NavSat). World-level plugins
**override** PX4's `server.config` defaults, and the template omits
AirPressure and Magnetometer — PX4 then fails preflight with
`barometer 0 missing` and `Found 0 compass` (first attempt,
run `20260710_111123`, rejected). The import step must add:

```xml
gz-sim-air-pressure-system
gz-sim-magnetometer-system
gz-sim-contact-system
gz-sim-apply-link-wrench-system
```

The imported `generated_worlds/terrain/Joshimath/Joshimath.world` carries
these under a `DATABOSS import preparation` comment.

## Remaining acceptance criteria

1. PX4 x500 flies takeoff/hover/land in the Joshimath world through
   `run_scenario_pxh_end_to_end.py` with truth recording and EKF-vs-truth
   alignment passing.
2. Runner/scenario support for terrain worlds:
   - vehicle spawn pose above local ground (helipad top), e.g. via
     `PX4_GZ_MODEL_POSE` from `vehicle.start_pose`;
   - PX4 home lat/lon/alt taken from the world's `<spherical_coordinates>`
     for georeferenced GNSS honesty.
3. Every imported world stored with provenance: source polygon, zoom, tile
   source, generation date, tool commit.
4. Custom world generated for our own area of interest via the web UI
   (needs the user's Mapbox key, server on :8080 over Tailscale).

## Custom-world proof (2026-07-10) — ACCEPTED

First operator-generated world flown end to end:
`generated_worlds/terrain/serefli_koschisar` (Şereflikoçhisar, Turkey;
lat 38.9667 / lon 33.5646 / elev 1079.4 m; 193×191 m, 55 m relief).

- Attempt 1 (run `20260710_113538`) rejected honestly: armed on bare
  terrain at the pin, tipped during takeoff → `Attitude failure (roll)` →
  disarmed by failsafe. Probe grid: the whole pin area slopes 14–20°.
- Import step gained a **flat launch pad** injection (static 4×4×0.5 m box,
  top z=1.3, equivalent to the generator's helipad option); spawn z=1.65.
- Attempt 2 (run `20260710_114339`) **accepted**: airborne 103.5 s, max
  height 2.585 m, landed and disarmed on/near the pad. Alignment: H mean
  0.051 m / max 0.127 m, height mean 0.075 m, 3D max 0.154 m.

Spawn-site rule: enable the generator's helipad option, drop the pin on
flat ground, or let the import step add a pad — 14–20° slopes flip the
x500 during takeoff spin-up.

## Camera-over-terrain proof (2026-07-10) — ACCEPTED

Run: `experiments/runs/20260710_115726_phase9a_camera_serefli_koschisar_terrain_pxh_takeoff_land_truth`
Scenario: `experiments/configs/mvp/scenarios/phase9a_camera_serefli_koschisar_terrain.yaml`

- `gz_x500_mono_cam_down` climbed to 8.08 m over the Şereflikoçhisar
  heightmap (airborne 43.4 s), captured a 13.4 MB downward camera frame,
  landed, disarmed. Alignment: H mean 0.053 m / max 0.100 m, 3D max 0.110 m.
- The rendered frame (`camera/camera_frame_terrain.png`, 1280×960) shows the
  launch pad and the **satellite albedo texture applied to the heightmap** —
  the ogre server-side render pipeline delivers real terrain imagery to the
  onboard camera. This is the input the optical-flow phases need.
- First attempt (run `20260710_115147`) segfaulted in EGL: the generator's
  template hardcodes `<render_engine>ogre2</render_engine>` in its
  world-level Sensors plugin, which overrides the runner's ogre server
  config. Import-preparation rule #3: set it to `ogre` for this headless VM
  (side effect: `Sky not supported by ogre` — cosmetic only).
- Texture-resolution rule: the 60 KB aerial (≈0.5 m/px over 193 m) renders
  blurry at 8 m altitude. Generate optical-flow worlds with higher zoom /
  smaller polygons for sharper ground detail.

## Import-preparation checklist (consolidated)

For every world imported from gazebo_terrain_generator:

1. Add the four missing PX4 sensor system plugins (AirPressure,
   Magnetometer, Contact, ApplyLinkWrench).
2. Set the world Sensors plugin `render_engine` to `ogre` (headless VM).
3. Ensure a flat launch site: generator helipad option, flat pin placement,
   or inject a DATABOSS launch pad.
4. Write `PROVENANCE.yaml` (location, coordinates, size, tool, date).
5. Scenario must carry `world.home` from the world's spherical_coordinates
   and a `vehicle.start_pose` above the pad.
6. If the generated terrain world must be shown in the Gazebo web viewer, run
   the original world through the terrain-enabled websocket proxy:
   `--serve-generated-terrain-assets` and
   `--populate-generated-terrain-heightmaps`. Do not switch to a `_web_mesh`
   world unless the native proxy path is unavailable.

## Browser Rendering for Generated Terrain Worlds (2026-07-13)

Scope: generated terrain worlds under `generated_worlds/terrain/*` only.
Flat generated worlds and normal mesh worlds should keep the standard web path.

Diagnostic result from Şereflikoçhisar:

- `/world/serefli_koschisar/scene/info` includes `ground_visual`, `HEIGHTMAP`,
  `height_map.png`, `aerial.png`, `normal_map.png`, and the launch-pad box.
  Gazebo and SceneBroadcaster are sending the terrain.
- The browser initially fetches absolute terrain PNG paths from
  `app.gazebosim.org/opt/...`, which creates expected `404` rows. It then
  falls back to websocket `asset` requests.
- The hosted browser client expects inline `HeightmapGeom` samples
  (`width`, `height`, `heights`), but Gazebo's Scene message only carries the
  heightmap filename, texture, size, and origin.

Accepted web-monitor process:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py \
  --listen-port 9002 \
  --upstream ws://127.0.0.1:9003 \
  --serve-generated-terrain-assets \
  --populate-generated-terrain-heightmaps \
  --log-file /tmp/gz_ws_proxy_frames.log
```

Use the original generated terrain world, for example:

```text
generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world
```

Expected evidence:

```text
S->C scene heightmap samples populated 1x from local PNG
S->C BINARY ... head=b'pub,scene,gz.msgs.Scene'
C->S asset served locally /opt/.../normal_map.png
C->S asset served locally /opt/.../aerial.png
```

The browser may still show initial `404` rows for `aerial.png` and
`normal_map.png` under `app.gazebosim.org`; those are not a failure if the
subsequent websocket asset responses appear and the terrain renders.

Rejected normal-process strategies: Collada textured mesh (`/opt/...` paths
404 under app.gazebosim.org), embedded texture data URIs, Collada vertex
colors, Fuel-style `model://` packages, Scene mesh URI rewrite, and large
colored-tile stress worlds.

Emergency fallback only:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/worlds/heightmap_to_web_mesh_world.py \
  --source-world generated_worlds/terrain/serefli_koschisar/serefli_koschisar.world \
  --output-dir generated_worlds/terrain/serefli_koschisar_web_mesh \
  --output-world-name serefli_koschisar_web_mesh \
  --visual-mode colored_tiles \
  --tile-count 32
```

This renders the satellite texture as a 32x32 mosaic of SDF box visuals, which
is why it appears "box by box". Keep it as an emergency browser compatibility
fallback only. Use the original terrain world for accepted PX4 flights, camera
captures, optical-flow evidence, normal web monitoring, and terrain provenance.

## Known limitations

- The hosted gzweb browser client needs the generated-terrain proxy flags above
  for native heightmap rendering. Without them, expect missing asset `404`s,
  dark/gray terrain, or a gray viewport.
- Sample worlds fetch a helipad model from Fuel on first launch (internet).
- Disk on the VM is 88% full; watch tile caches when generating worlds.

## Next phase step

Use the accepted terrain worlds in the 8G/8I optical-flow and GNSS-denied
matrices; generate additional custom areas only with provenance and a planned
disk budget.
