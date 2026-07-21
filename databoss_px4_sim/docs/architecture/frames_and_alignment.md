# Frames and truth alignment

Status: authoritative since 2026-07-13 (created after the 8G.2 legx
investigation fixed the ENU→NED axis bug).

## Conventions

- **Gazebo world frame: ENU** — X = East, Y = North, Z = up.
- **PX4 local frame: NED** — X = North, Y = East, Z = down.

## Horizontal mapping (the part that was wrong before 2026-07-13)

```text
NED_x (North) = ENU_y
NED_y (East)  = ENU_x
NED_z (Down)  = -ENU_z
```

Both sides are normalized to their initial pose before comparison
(relative displacement), and both height channels are compared as
height-up, so the vertical channel was always correct.

History: `scripts/runner/align_latest_truth_run.py` compared PX4 NED x/y
directly against Gazebo ENU x/y until 2026-07-13. Every accepted comparison
until then was a hover flight (zero horizontal displacement), so the bug was
numerically invisible. The first translating flight (8G.2 legx, run
`20260713_091302`) showed a fake horizontal error equal to the traverse
(5.43 m mean); with the correct mapping the real error is 0.144 m mean /
0.332 m max (GNSS-on class). The 8A drift anchors are magnitude-dominated
(EKF wanders, truth near-stationary) and survive unchanged.

## Time alignment

- PX4 side: ULog `timestamp` (µs, lockstep ⇒ tracks sim time).
- Gazebo side: `sim_time_s` from the dynamic-pose truth recorder.
- Both streams are zeroed at their own takeoff crossing
  (`--takeoff-threshold-m`, default in `align_latest_truth_run.py`), then
  truth is sampled at PX4-relative timestamps.

## Wall clock vs sim clock (RTF)

x500_cam_lidar_down (30 Hz camera + 50 Hz lidar, software rendering) runs at
**RTF ≈ 0.09–0.10** (measured from bridge CSV wall-vs-sim stamps on runs
`20260713_064248` and `20260713_091302`). Runner durations
(`--hover-s`, `--land-timeout-s`, internal `time.sleep`s in
`auto_takeoff_land_pxh_truth.py`) are WALL clock: multiply the desired sim
duration by ~10. A 30 sim-s offboard leg needs `--hover-s 310
--land-timeout-s 300`. All physical/EKF metrics are computed in sim time and
are unaffected by RTF.

## Rules for new sensors/estimators

Every new sensor or estimator must explicitly document: source frame, target
frame, timestamp source, transform, axis signs, units, covariance, update
rate, latency behavior — and must be validated with a TRANSLATING flight,
not only hover (hover hides horizontal-axis and sign errors; that is exactly
how the aligner bug survived until 8G.2).
