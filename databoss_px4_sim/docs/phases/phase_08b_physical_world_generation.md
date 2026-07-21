# Phase 8B - Physical World Generation

## Goal

Turn DATABOSS world YAML files into actual Gazebo SDF worlds.

Phase 8A is now frozen as the ideal external-aiding upper-bound reference. Phase 8B starts the practical sensor path by making the world itself physical and inspectable before adding camera, TF03, or optical-flow processing.

Pipeline:

```text
world YAML
-> world builder
-> generated Gazebo SDF
-> Gazebo launch
-> PX4 x500 spawn
```

## Scope

First world family:

```text
flat_rural_high_texture_noon
flat_rural_low_texture_noon
```

Implemented first:

- flat ground geometry
- high-texture vs low-texture ground materials
- noon-style sun and ambient light
- shadows
- static rural landmarks
- saved YAML and generated SDF artifacts

Not included yet:

- wind physics
- GNSS-loss performance claims
- camera processing
- TF03 simulation
- optical flow
- dashboard work

## Required Files

```text
scripts/worlds/build_gazebo_world.py

experiments/configs/mvp/worlds/flat_rural_high_texture_noon.yaml
experiments/configs/mvp/worlds/flat_rural_low_texture_noon.yaml

generated_worlds/flat_rural_high_texture_noon.sdf
generated_worlds/flat_rural_low_texture_noon.sdf
```

## Acceptance Criteria

Phase 8B is accepted when:

- both SDF files are generated from YAML
- both SDF files parse as XML
- both worlds launch independently in Gazebo
- high-texture and low-texture grounds visibly differ
- PX4 can spawn an x500 in each generated world
- the world YAML and generated SDF are saved with each run

## Launch Proof

Status: physically launchable in Gazebo headless mode.

Proof run:

```text
experiments/runs/20260709_142806_phase8b_world_launch_proof
```

Result:

```text
flat_rural_high_texture_noon: Gazebo launch yes, x500 spawn yes, accepted yes
flat_rural_low_texture_noon:  Gazebo launch yes, x500 spawn yes, accepted yes
```

Evidence captured:

- Gazebo create service `/world/<world>/create` appeared for each generated world.
- PX4 Gazebo `x500` model spawned through `gz.msgs.EntityFactory`.
- The x500 entity was visible through Gazebo model list, pose topic, and model topics.
- The proof was run as user `px4`.

Proof runner:

```text
scripts/worlds/prove_gazebo_world_spawn.py
```

## PX4 Flight Proof

Status: PX4-flight-compatible in Gazebo headless mode.

Report:

```text
docs/phases/phase_08b_generated_world_px4_flight_proof.md
```

Accepted runs:

```text
experiments/runs/20260709_144318_phase8b_px4_flight_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260709_144526_phase8b_px4_flight_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Result:

```text
flat_rural_high_texture_noon: PX4 x500 takeoff/hover/land accepted
flat_rural_low_texture_noon:  PX4 x500 takeoff/hover/land accepted
```

Key implementation detail:

Generated worlds run as standalone Gazebo worlds. PX4 attaches with `PX4_GZ_STANDALONE=1`, a unique `GZ_PARTITION`, and a fixed PX4 home origin so Gazebo NavSat can publish valid global position.

## Current Step

Phase 8B has moved from generated-and-valid to physically launchable and PX4-flight-compatible.

Remaining optional Phase 8B polish before Phase 8C:

- capture visual screenshots to confirm the high-texture and low-texture worlds differ as expected
- keep the generated-world proof runner as the acceptance guard for new worlds

## Next Phases

```text
Phase 8C - Downward camera proof
Phase 8D - TF03-style downward rangefinder proof
Phase 8E - Combined camera + TF03 vehicle
Phase 8F - Offline optical-flow validation
Phase 8G - Live optical-flow bridge
Phase 8H - GNSS-on optical-flow fusion check
Phase 8I - GNSS-denied camera + TF03 comparison
```
