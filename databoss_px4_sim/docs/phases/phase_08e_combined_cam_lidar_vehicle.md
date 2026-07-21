# Phase 8E — Combined Downward Camera + TF03-Style Rangefinder Vehicle

Status: Accepted (2026-07-10)

## Goal

Prove that one PX4 vehicle carrying both practical sensors — downward mono
camera and TF03-style downward single-point rangefinder — flies the generated
worlds with **both** sensor paths publishing simultaneously into the same
ULog, with QGC and the web viewer connected as standing run monitors.

## Why this phase exists

Phases 8C and 8D proved each sensor alone. The practical DATABOSS stack needs
them on one airframe: optical flow (camera) needs the rangefinder's AGL to
scale flow into velocity. This phase proves coexistence before any
optical-flow computation (8F+).

## In scope

- New vehicle `x500_cam_lidar_down`: merge of PX4 stock `x500_mono_cam_down`
  and `x500_lidar_down` (camera at `0 0 0.10`, pitched down; LW20 lidar link
  at `0 0 -0.05`, downward).
- New PX4 airframe `4022_gz_x500_cam_lidar_down` (sources `4001_gz_x500`).
- Both Phase 8C camera and Phase 8D rangefinder proofs in a single run.
- Standing operating rule: QGC stream to the Mac (100.109.200.5) and the
  Gazebo web bridge (9003, browser via enum-patch proxy on 9002) enabled for
  every run.

## Out of scope

- Optical-flow computation, EKF fusion of either sensor, sensor error models,
  terrain worlds (follow-up case after flat acceptance).

## Implementation

- Source of truth in DATABOSS: `src/databoss_sim/models/x500_cam_lidar_down/`
  and `src/databoss_sim/airframes/4022_gz_x500_cam_lidar_down`.
- Deployed copies in PX4 (engine extension, documented here):
  `Tools/simulation/gz/models/x500_cam_lidar_down/model.sdf`,
  `ROMFS/px4fmu_common/init.d-posix/airframes/4022_gz_x500_cam_lidar_down`,
  registered in the airframes `CMakeLists.txt`.
- Scenarios enable both `camera:` and `rangefinder:` proof sections
  (ogre + xvfb rendering, shared).
- Configs:
  - `experiments/configs/mvp/scenarios/phase8e_cam_lidar_flat_rural_high_texture_noon.yaml`
  - `experiments/configs/mvp/scenarios/phase8e_cam_lidar_flat_rural_low_texture_noon.yaml`
  - `experiments/configs/mvp/batches/phase8e_cam_lidar_generated_world_smoke.yaml`
    (QGC enabled in batch defaults)

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
python3 scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8e_cam_lidar_generated_world_smoke.yaml \
  --continue-on-fail
```

## Acceptance criteria

Per world, in one run:

1. Vehicle arms, takes off, hovers ~20 s (PX4-time gated), lands, disarms.
2. Camera: image topic seen, one frame captured (>1 KB sample).
3. Rangefinder: scan topic seen, one sample with finite positive range.
4. ULog: >50 `distance_sensor` rows, max distance vs max height within
   0.75 m.
5. Truth recorded, EKF-vs-truth alignment passes.
6. QGC MAVLink stream started; web bridge ready.

## Operator note (standing rule)

If QGroundControl connects during a flight it must stay connected until
landing — a mid-flight GCS disconnect triggers the datalink failsafe
(Hold → Return) under the `default_px4` profile, which ends the run early
(observed 2026-07-10, run 20260710_083447).

## Results

Accepted runs (model v2, lidar at x=+0.08):

| World | Run | Camera frame | Lidar in-flight range | ULog `distance_sensor` rows / max | Range-vs-height diff | H err mean/max |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| high texture | `20260710_133458` | 7.34 MB | 2.5160 m | 1620 / 2.5165 m | 0.113 m | 0.048 / 0.192 m |
| low texture | `20260710_132712` | 7.35 MB | 2.5263 m | 1605 / 2.5288 m | 0.121 m | 0.049 / 0.152 m |

Both runs: QGC MAVLink stream started, web bridge ready, truth recorded,
EKF-vs-truth alignment passed, landed and disarmed by landing.

## Terrain case (2026-07-10) — ACCEPTED

Run `20260710_135303_phase8e_cam_lidar_serefli_koschisar_terrain`: the v2
vehicle flew the operator-generated Şereflikoçhisar heightmap from the launch
pad. Lidar 2.5139 m at a 2.5 m hover with **0.002 m** ULog range-vs-height
agreement (both reference the pad top), 2773 `distance_sensor` rows, 7.2 MB
camera frame showing pad + satellite-textured terrain, alignment H mean
0.069 m. QGC connected live and web bridge ready. The complete practical
sensing stack is proven on one vehicle over real-DEM terrain.

## Design lesson (v1 → v2)

Model v1 mounted the lidar on the vehicle centerline. In the merged model the
camera assembly sits under the belly ~0.10 m below the lidar sensor, and the
lidar read a constant 0.100–0.110 m (its minimum range) for the whole flight —
staring at the camera housing instead of the ground (rejected runs
`20260710_130250`, `20260710_131117`; the stuck bottom-distance also disturbed
PX4 landing disarm). v2 offsets the lidar assembly 0.08 m forward,
side-by-side with the camera, as on real TF03 + camera mounts: lidar reads
true AGL and the camera frame verified clear of vehicle geometry in both
versions. The high-texture case additionally needed `land_timeout_s: 200`
(two rendering sensors slow the sim; the disarm message fell outside the
120 s window in an otherwise-clean run `20260710_131957`).

## Known limitations

- Both sensors remain ideal (no noise/latency/dropout models).
- Combined vehicle mass/inertia are the stock x500 values with both sensor
  bodies added; no re-tune of control gains (stock x500 gains).

## Next phase

Phase 8F — offline optical-flow validation on captured camera + rangefinder
data; plus terrain cases over `generated_worlds/terrain/serefli_koschisar`.
