# Phase 8F — Offline Modular Optical-Flow Validation (SIFT v1)

Status: Accepted with limitations (2026-07-11)

## Goal

Prove that DATABOSS's own modular optical-flow estimator (SIFT v1) reproduces
Gazebo-truth motion from recorded downward-camera frames + rangefinder AGL,
before any live PX4 integration.

## Why this phase exists

Operator decision (2026-07-10): the aiding method must be our own, modular,
and upgradable — not PX4's stock simulated flow. Per the sensor-integration
checklist, the algorithm gets a standalone offline validation before it feeds
the estimator (8G live bridge, 8H GNSS-denied comparison).

## Architecture (modularity contract)

`src/databoss_sim/flow/`: `FlowEstimator` ABC (`update(gray, t_s) → FlowSample`),
`SiftFlowEstimator` v1, `velocity.py` (flow + range → ground velocity, v1
without gyro compensation — hover-only validity, explicit limitation).
Estimators are selected by name via `make_estimator("sift")`; new algorithms
are a new module + registry entry.

Synthetic self-test (2026-07-10): a known 6 px shift on a textured image was
recovered exactly (0.022225 rad measured vs expected; velocity −0.556 m/s vs
−0.556). Algorithm math verified before any flight data.

## Data pipeline

- `scripts/sim/record_camera_frames.py` (system python3, gz.transport13 +
  cv2): subscribes camera image + lidar scan topics, saves rate-capped
  (10 Hz), downscaled (640 px) JPEG frames + `frames_index.csv` +
  `rangefinder.csv` into `<run>/flow_recording/`.
- Runner `flow_recording:` scenario section starts/stops the recorder with
  the flight; acceptance gains `flow_recording_ok`
  (≥ `min_frames` frames + range samples present).
- `scripts/analysis/validate_flow_offline.py`: runs the named estimator over
  a recording, derives speed, compares against truth speed (from the aligned
  truth CSV), writes `flow_validation/summary.{json,md}` + a speed/quality
  plot.

## Scenarios

- `phase8f_flow_rec_flat_rural_high_texture_noon.yaml`
- `phase8f_flow_rec_flat_rural_low_texture_noon.yaml`
- `phase8f_flow_rec_serefli_koschisar_terrain.yaml`

All: combined vehicle `x500_cam_lidar_down` v2, 60 s hover at 2.5 m, both
sensor proofs + recording, QGC + web monitors.

## Acceptance criteria

Per world:
1. Run accepted by the standard gates (flight, sensors, truth, alignment).
2. Recording: ≥ 100 frames at effective ≥ 5 Hz + rangefinder samples.
3. Offline validation: flow-derived speed error vs truth **mean < 0.15 m/s**
   at hover; quality > 0 for the large majority of samples on the
   high-texture world.
4. Texture comparison: quality/match counts visibly higher on high texture
   than low texture. Low-texture flow degradation or failure is an accepted
   RESULT (that comparison is what the 8B worlds exist for).

## Results

All three recording flights (2026-07-10 evening) were accepted by the standard
gates; offline SIFT v1 validation ran on each (terrain validated 2026-07-11).
All recordings ~7.6 Hz effective (cap 10 Hz), well above the ≥5 Hz criterion.

| World | Run | Frames | Valid | Quality mean | Matches mean | Speed err mean (m/s) | p95 |
|---|---|---|---|---|---|---|---|
| flat high texture | 20260710_180752 | 405 | 84.4 % | 48.0 | 19.0 | 0.017 | 0.040 |
| flat low texture | 20260710_182320 | 434 | 69.5 % | 26.1 | 11.2 | 0.033 | 0.059 |
| serefli_koschisar terrain | 20260710_183635 | 378 | 77.5 % | 58.7 | 23.2 | 0.022 | 0.060 |

Acceptance criteria evaluation:

1. Standard gates: all three runs `accepted=true` (flight, camera + lidar
   proofs, truth, alignment). PASS.
2. Recording: 405/434/378 frames at 7.59–7.60 Hz effective, rangefinder.csv
   present in all. PASS.
3. Speed error mean < 0.15 m/s at hover: 0.017 / 0.033 / 0.022. Quality > 0
   for the large majority on high texture (84.4 % valid). PASS.
4. Texture comparison: high vs low texture quality 48.0 vs 26.1, matches 19.0
   vs 11.2, valid fraction 84 % vs 70 % — degradation clearly visible and in
   the expected direction. PASS.

Notable: the satellite-textured real-DEM terrain gives the *best* feature
quality of all three worlds (58.7), confirming the rural/terrain environments
are favorable for image-based flow.

## Interpretation

The DATABOSS modular SIFT v1 estimator reproduces near-zero ground speed from
recorded frames + lidar AGL at hover on all three worlds without hallucinating
motion, and its quality metric tracks ground texture as designed. The offline
pipeline (recorder → estimator → truth comparison) is proven and reusable for
future estimator versions via `make_estimator(<name>)`.

## Known limitations

- v1 has no gyro compensation: valid at hover/near-level only.
- Camera and estimator are ideal-noise-free; no exposure/blur model.
- Truth-vs-flow time alignment uses window-midpoint matching (documented in
  the validator); adequate for hover statistics, to be tightened for routes.
- Validation is hover-only: it proves the estimator does not hallucinate
  motion at rest (plus the synthetic 6 px shift self-test for nonzero flow),
  but no in-flight translation route was flown. A slow translation case is
  carried into 8G/8H where flow must aid EKF2 during real motion.

## Files created or modified

- `src/databoss_sim/flow/{__init__,estimator,sift_estimator,velocity}.py`
- `scripts/sim/record_camera_frames.py`
- `scripts/analysis/validate_flow_offline.py`
- `scripts/runner/auto_takeoff_land_pxh_truth.py` (flow_recording section)
- three `phase8f_flow_rec_*.yaml` scenarios

## Next phase

Phase 8G — live modular flow bridge (`OPTICAL_FLOW_RAD` MAVLink) + GNSS-on
EKF2 fusion check.
