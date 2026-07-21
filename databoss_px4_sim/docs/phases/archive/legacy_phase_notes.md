# Legacy phase notes (relocated from `docs/phases/README.md`)

These sections were moved verbatim out of the active roadmap on 2026-07-20
to keep `README.md` scannable. Nothing here was reworded or deleted — this
is historical record only; see `README.md` for current phase status.

---

Superseded heading below kept for history:

Phase 8F — Offline optical-flow validation
(plus terrain cases for 8D/8E over `generated_worlds/terrain/serefli_koschisar`)

---

Superseded heading below kept for history:

Phase 8D — TF03-Style Downward Rangefinder Proof

Status:

```text
Phase 8B generated worlds are PX4-flight-compatible.
Phase 8C proved downward camera publication in both generated worlds.
Phase 8D should add a downward rangefinder/ray sensor proof in the same worlds.
```

Latest proof:

```text
experiments/runs/20260709_142806_phase8b_world_launch_proof
```

Both generated worlds launched in Gazebo headless mode, and the PX4 Gazebo `x500` model spawned successfully in each world through the Gazebo create service.

Latest PX4 flight proof:

```text
experiments/runs/20260709_144318_phase8b_px4_flight_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260709_144526_phase8b_px4_flight_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Both generated worlds accepted a PX4 x500 takeoff, short hover, land, ULog copy, Gazebo truth postprocess, and EKF-vs-truth alignment.

Latest Phase 8C downward camera proof:

```text
experiments/runs/20260710_063125_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260710_063752_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Both generated worlds accepted `gz_x500_mono_cam_down`, published a downward camera image topic, captured one delayed image message after the airborne hover gate, rendered visible generated ground from that message, flew a 20 s requested hover with PX4-time gating, landed, postprocessed Gazebo truth, and passed EKF-vs-truth alignment.

---

## Next phases

- Phase 8G — Live optical-flow bridge (8G.3 absorbs the 8H GNSS-on fusion
  check).
- Phase 8I — GNSS-denied camera + TF03 comparison.
- Phase 12 — MVP comparison report.
- Phase 13 — Dashboard backend contract.

(Duplicate of the "Upcoming phases" list in `README.md`; kept here for
history.)

---

## Current Phase 8B focus

Phase 8B turns world YAML files into actual Gazebo SDF worlds.

First required artifacts:

```text
scripts/worlds/build_gazebo_world.py
experiments/configs/mvp/worlds/flat_rural_high_texture_noon.yaml
experiments/configs/mvp/worlds/flat_rural_low_texture_noon.yaml
generated_worlds/flat_rural_high_texture_noon.sdf
generated_worlds/flat_rural_low_texture_noon.sdf
```

Acceptance target:

- done: both SDF files parse as XML
- done: both worlds launch independently in Gazebo headless mode
- done: PX4 Gazebo `x500` model can spawn in each generated world
- done: PX4 x500 can take off, hover, land, and align EKF with Gazebo truth in each generated world
- done: source YAML and generated SDF artifacts are saved
- remaining visual polish: capture screenshots or GUI inspection proving high-texture and low-texture grounds visibly differ

## Current Phase 8C focus

Phase 8C uses `gz_x500_mono_cam_down` in the generated high-texture and low-texture worlds.

First required artifacts:

```text
docs/phases/phase_08c_downward_camera_proof.md
experiments/configs/mvp/scenarios/phase8c_camera_flat_rural_high_texture_noon.yaml
experiments/configs/mvp/scenarios/phase8c_camera_flat_rural_low_texture_noon.yaml
experiments/configs/mvp/batches/phase8c_downward_camera_generated_world_smoke.yaml
```

Acceptance target:

- done: PX4 camera vehicle can take off, hover, and land in each generated world
- done: Gazebo truth parses `x500_mono_cam_down_0`
- done: EKF-vs-truth alignment passes
- done: downward camera image topic exists during flight
- done: at least one image sample is captured after the airborne hover gate and renders generated ground

Renderer note:

```text
Use camera.render_engine=ogre with xvfb_enabled=true for this headless VM.
The default ogre2/EGL path crashed in the Gazebo camera render thread.
Camera samples must be captured after the airborne hover gate; early post-takeoff samples can render only background/near-ground artifacts.
```

Web visualization note (2026-07-10, proven live from the Mac):

```text
Both phase8c_web_camera_* scenarios start the runner-managed websocket bridge on port 9003.
Browsers connect through the enum-patch proxy on port 9002
(scripts/sim/gz_websocket_enum_patch_proxy.py); the raw gz-launch 7.1.2 bridge
omits the PixelFormatType/SphericalCoordinatesType enums and camera-vehicle
scenes fail to render without the proxy.
Full recipe and rules: docs/gazebo_web_visualization.md.
```

---

## Phase 7D — Scenario and World Condition Matrix Freeze

Purpose:

Freeze reusable world-condition presets and scenario matrix structure before Phase 8A synthetic external odometry aiding.

Phase 7D is a config/schema phase. It does not require a PX4/Gazebo run.
