# Phase 8D — TF03-Style Downward Rangefinder Proof

Status: Accepted (2026-07-10)

## Goal

Prove that a PX4 Gazebo x500 with a TF03-style downward single-point
rangefinder can fly in the generated Phase 8B worlds, publish range data in
Gazebo, and deliver that range into PX4 as a `distance_sensor` uORB message
visible in the ULog.

## Why this phase exists

The practical DATABOSS sensing stack is downward camera + TF03-style downward
single-point LiDAR + IMU. Phase 8C proved the camera path; Phase 9A proved
real-terrain worlds. Phase 8D proves the rangefinder path standalone, before
combining sensors (8E) and before any optical-flow or fusion claims. The
sensor-integration checklist requires a standalone validation phase for every
simulated sensor.

## In scope

- `gz_x500_lidar_down` (PX4 stock model: x500 + LW20 body + single-point
  downward `gpu_lidar`) flying in both generated Phase 8B flat worlds.
- Gazebo scan topic existence and one captured range sample during flight.
- PX4-side evidence: `distance_sensor` rows in the ULog with a max distance
  consistent with the hover height.
- Existing takeoff/hover/land automation, Gazebo truth recording, and
  EKF-vs-truth alignment.

## Out of scope

- TF03-specific noise, bias, dropout, latency, or reflectance modeling.
- EKF2 range-height fusion or terrain-following claims (PX4 defaults only).
- Combined camera + rangefinder vehicle (Phase 8E).
- Terrain worlds (follow-up case after flat acceptance; Phase 9A worlds give
  real AGL variation).

## Inputs

- Generated worlds: `generated_worlds/flat_rural_high_texture_noon.sdf`,
  `generated_worlds/flat_rural_low_texture_noon.sdf`.
- PX4 model `x500_lidar_down`, airframe `4016_gz_x500_lidar_down`.
- Runner: `scripts/runner/auto_takeoff_land_pxh_truth.py` (extended).

## Sensor definition (checklist)

- Physical model: single-point `gpu_lidar`, sensor link at
  `0 0 -0.05 0 1.57 0` relative to `base_link` (downward boresight), 1×1
  beam, range 0.1–100 m, resolution 0.01 m, update 50 Hz, **no simulated
  noise/bias/latency/dropout** — an ideal sensor; this phase is a
  publication proof, not a TF03 error model.
- Source frame: sensor frame, single range along boresight.
- Software path:
  `gpu_lidar → /world/<world>/model/<model>/link/lidar_sensor_link/sensor/lidar/scan
  → PX4 gz_bridge laserScantoLidarSensorCallback (SIM_GZ_EN_LIDAR=1 default)
  → distance_sensor uORB (ROTATION_DOWNWARD_FACING) → ULog`.
- Timestamp: PX4 `hrt_absolute_time()` at bridge receipt.
- Units: meters.
- Rendering dependency: `gpu_lidar` uses the render engine → requires the
  Phase 8C ogre + xvfb path on this headless VM.

## Implementation

Runner (`auto_takeoff_land_pxh_truth.py`) gains a `rangefinder:` scenario
section mirroring `camera:`:

- `proof_enabled`, `scan_topic` override, `probe_timeout_s`,
  `render_engine`, `xvfb_enabled`, `xvfb_server_args`, `min_ulog_rows`,
  `height_agreement_tolerance_m`.
- In-flight probe after the airborne hover gate: topic listed + one scan
  sample captured + range value parsed from the LaserScan textproto.
- Post-flight ULog analysis: `distance_sensor` row count and max
  `current_distance` compared against ULog max height (flat ground ⇒ the
  two should agree within tolerance).
- Rendering setup (server config override, xvfb, LIBGL software) applies
  when camera **or** rangefinder proof is enabled.

Configs:

- `experiments/configs/mvp/scenarios/phase8d_rangefinder_flat_rural_high_texture_noon.yaml`
- `experiments/configs/mvp/scenarios/phase8d_rangefinder_flat_rural_low_texture_noon.yaml`
- `experiments/configs/mvp/batches/phase8d_downward_rangefinder_generated_world_smoke.yaml`

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
python3 scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8d_downward_rangefinder_generated_world_smoke.yaml \
  --continue-on-fail
```

## Expected outputs

Per world, one run folder additionally containing:

- `rangefinder/gz_topics.txt`, `rangefinder/rangefinder_topic_info.txt`,
  `rangefinder/rangefinder_scan_sample.txt`
- `rangefinder_*` fields in `pxh_takeoff_land_truth_status.json` and
  `validation.md`
- ULog with `distance_sensor` rows

## Acceptance criteria

Per world:

1. Vehicle arms, takes off, hovers ~20 s (PX4-time gated), lands, disarms.
2. Gazebo scan topic exists during flight; one sample captured with a
   finite positive range.
3. ULog contains > 50 `distance_sensor` rows.
4. ULog max `distance_sensor.current_distance` agrees with ULog max height
   within 0.75 m.
5. Gazebo truth recorded and EKF-vs-truth alignment passes.

## Results

Batch `experiments/batches/20260710_124609_phase8d_downward_rangefinder_generated_world_smoke`: 2/2 accepted.

| World | Run | In-flight sample range | ULog `distance_sensor` rows | ULog max distance | Range-vs-height diff | H err mean/max |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| high texture | `20260710_124613` | 2.5115 m | 2031 | 2.5116 m | 0.113 m | 0.036 / 0.120 m |
| low texture | `20260710_124954` | 2.5110 m | 2205 | 2.5111 m | 0.150 m | 0.031 / 0.085 m |

Both runs: armed, hovered ~26 s airborne at ~2.6 m, landed, disarmed by
landing; Gazebo truth recorded; EKF-vs-truth alignment passed.

## Interpretation

- The full TF03-analog path is proven:
  `gpu_lidar → gz scan topic → gz_bridge → distance_sensor uORB → ULog`.
  ~2000 rows over a ~26 s flight ≈ the expected ~50 Hz plus ground time.
- The in-flight scan sample (≈2.511 m at a 2.5 m hover) and the ULog max
  distance agree to the millimeter — the same measurement seen at both ends
  of the pipe.
- The 0.11–0.15 m range-vs-height difference is explained by sensor mount
  offset (0.05 m below base) plus the EKF height overshoot moment not
  coinciding with the lidar max — well inside the 0.75 m tolerance.
- Texture has no effect on the rangefinder (as expected for a ray sensor);
  the two worlds serve as a repeatability check here.

## Known limitations

- Ideal sensor: no TF03 noise/bias/latency/dropout/reflectance model.
- Height agreement uses EKF height over flat ground; not a terrain-profile
  validation (terrain case follows after flat acceptance).
- No claim about EKF2 range fusion; fusion parameters remain PX4 defaults.

## Files created or modified

- `docs/phases/phase_08d_downward_rangefinder_proof.md` (this file)
- `scripts/runner/auto_takeoff_land_pxh_truth.py`
- scenario + batch YAMLs listed above

## Next phase

Phase 8E — combined downward camera + TF03-style rangefinder vehicle;
plus a Phase 8D terrain case over `serefli_koschisar`.
