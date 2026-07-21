# DATABOSS Current Project Context

This reference is a starting map. The auditor must verify all items against the live repository and latest runs.

## Architecture

```text
Scenario YAML
→ world/vehicle setup
→ PX4 + Gazebo
→ sensor or bridge processes
→ MAVLink/uORB/EKF2
→ automated mission
→ ULog + Gazebo truth + sensor logs
→ alignment, metrics, plots, report
```

## Repository boundary

```text
/opt/databoss_px4_sim
  configs, runners, analysis, worlds, runs, comparisons, docs

/opt/sim_px4/PX4-Autopilot
  PX4 source, build, simulator engine
```

## Truth and frames

- Gazebo truth is used for scoring.
- Gazebo world is z-up.
- PX4 local NED z is down.
- Every sensor estimate requires a declared frame, timestamp, and transform.

## Known stable milestones

- PX4/Gazebo headless launch
- GNSS-on baseline
- ULog extraction and plotting
- simulated GNSS loss through `SIM_GPS_USED`
- default and delayed-observation failsafe experiments
- Gazebo truth recording and alignment
- automated scenarios and batches
- ideal truth-fed external odometry pipeline
- stock Gazebo optical-flow benchmark
- LK optical-flow transport smoke test

## Current practical sensing direction

```text
downward monocular camera
+ downward TF03-style single-point rangefinder
+ PX4 onboard IMU
```

Primary environments are rural fields, ridges, valleys, mountains, and open land.

## Recent LK audit lessons

- The rejected LK GNSS-loss run was confounded by a different failsafe setup.
- The LK noise configuration did not gain trust with higher quality.
- LK quality was capped below PX4’s full expected range.
- GNSS-on acceptance was transport proof, not flow-only navigation proof.
- Axis/sign, focal-length scaling, integration timing, stale samples, delay, and rotational compensation remain code-level audit targets.
- GNSS should not be cut until continuous optical-flow fusion is verified.
