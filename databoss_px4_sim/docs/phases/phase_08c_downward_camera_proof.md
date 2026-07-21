# Phase 8C - Downward Camera Proof

## Goal

Prove that a PX4 Gazebo x500 with a downward monocular camera can run inside the generated Phase 8B worlds.

Acceptance for the first proof:

- generated world launches in Gazebo headless mode
- PX4 attaches in standalone mode and spawns `x500_mono_cam_down_0`
- PX4 can arm, take off, hover briefly, land, and disarm
- Gazebo truth is recorded for the camera vehicle model
- EKF-vs-truth alignment passes
- the downward camera image topic exists during flight
- at least one camera image message is captured after the airborne hover gate

## Configs

```text
experiments/configs/mvp/scenarios/phase8c_camera_flat_rural_high_texture_noon.yaml
experiments/configs/mvp/scenarios/phase8c_camera_flat_rural_low_texture_noon.yaml
experiments/configs/mvp/batches/phase8c_downward_camera_generated_world_smoke.yaml
```

## Expected Camera Topic

```text
/world/<world>/model/x500_mono_cam_down_0/link/camera_link/sensor/camera/image
```

## Notes

Phase 8C is only a camera publication proof. It does not yet claim optical-flow quality, feature tracking quality, EKF optical-flow fusion, or GNSS-denied performance.

## 2026-07-10 Visual Proof Correction

The 2026-07-09 Phase 8C runs proved topic publication, flight, logging, and EKF-vs-truth alignment, but rendering the saved camera samples showed a visual timing bug: the runner captured the image immediately after `commander takeoff`, before the vehicle had climbed. That early downward view rendered only Gazebo background color because the camera was still effectively at the near-ground/near-clip condition.

Fix:

```text
scripts/runner/auto_takeoff_land_pxh_truth.py
```

The camera probe now runs after the simple auto-hover airborne-duration wait and before `commander land`. The PX4 `x500_mono_cam_down` model orientation remains at the upstream down-facing `+1.5707` pitch convention.

Accepted visual-proof runs:

```text
experiments/runs/20260710_063125_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260710_063752_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Batch summaries:

```text
experiments/batches/20260710_063121_phase8c_downward_camera_generated_world_smoke/batch_summary.md
experiments/batches/20260710_063748_phase8c_downward_camera_generated_world_smoke/batch_summary.md
```

Rendered proof PNGs were created from the saved `camera_image_sample.txt` files with:

```bash
venv/bin/python scripts/analysis/render_gz_camera_image_textproto.py \
  <run>/camera/camera_image_sample.txt \
  /tmp/<output>.png
```

Visual result:

- high-texture run renders the checker-field ground and x500 shadow
- low-texture run renders the uniform green field and x500 shadow

Proof details:

| World | Accepted | Camera sample | ULog airborne | Max height | H mean | H max | Height mean |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| high texture | yes | 7,355,968 bytes | 23.042 s | 2.498 m | 0.065620 m | 0.172002 m | 0.024628 m |
| low texture | yes | 7,332,265 bytes | 23.204 s | 2.514 m | 0.034800 m | 0.132544 m | 0.015231 m |

## 2026-07-09 Result

Phase 8C is accepted in both generated Phase 8B worlds.

Accepted runs:

```text
experiments/runs/20260709_182405_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260709_183013_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Batch summaries:

```text
experiments/batches/20260709_182401_phase8c_downward_camera_generated_world_smoke/batch_summary.md
experiments/batches/20260709_183010_phase8c_downward_camera_generated_world_smoke/batch_summary.md
```

Proof details:

| World | Accepted | Camera sample | ULog airborne | Max height | H mean | H max | Height mean |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| high texture | yes | 14,745,833 bytes | 22.476 s | 2.526 m | 0.051812 m | 0.150824 m | 0.045718 m |
| low texture | yes | 14,745,831 bytes | 23.039 s | 2.420 m | 0.040649 m | 0.083098 m | 0.071383 m |

Both runs:

- launched the generated world in standalone Gazebo mode
- spawned `gz_x500_mono_cam_down` as `x500_mono_cam_down_0`
- captured one message from the downward mono camera image topic
- recorded Gazebo dynamic-pose truth for the camera vehicle
- armed, took off, stayed airborne long enough for the ULog flight check, landed, and disarmed
- passed postprocess and EKF-vs-Gazebo-truth alignment

## Headless Renderer Finding

The first camera attempt failed before takeoff because the default PX4 Gazebo sensor config uses `ogre2`, which tried to initialize EGL/DRM on this headless VM and crashed in the render thread.

The accepted Phase 8C path uses:

```text
camera.render_engine: ogre
camera.xvfb_enabled: true
camera.xvfb_server_args: "-screen 0 1280x1024x24"
camera.headless_rendering: false
```

Runtime setup added during this phase:

```text
px4 user added to video/render groups
xvfb and mesa-utils installed
```

The runner writes a run-local Gazebo server config override such as:

```text
logs/gz_server_ogre.config
```

That keeps the PX4 source config untouched while letting camera-proof runs use the server renderer that works on this host.

## Timing Finding

Camera rendering slows Gazebo enough that host wall-clock hover is not a reliable proxy for PX4/ULog flight time. The runner now waits on PX4 `vehicle_land_detected` timestamps for the simple auto-hover path, using the same effective airborne threshold as the ULog acceptance check.
