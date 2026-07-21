# Phase 7D — Scenario and World Condition Matrix Freeze

## Goal

Freeze how DATABOSS describes scenarios, reusable environment conditions, and test matrices before Phase 8A synthetic external odometry aiding.

This phase does not add a new aiding source yet.

It defines the clean config structure for:

- world
- route
- vehicle
- altitude
- lighting
- wind
- texture
- GNSS profile
- failsafe profile
- aiding mode
- sensor noise
- sensor latency
- dropout
- batch matrix role

## Why this phase exists

Phase 7A, 7B, and 7C proved that DATABOSS can:

- run PX4/Gazebo automatically
- cut GNSS with SIM_GPS_USED=0
- record Gazebo truth
- extract PX4 ULog data
- align PX4 EKF against Gazebo truth
- run GNSS-loss case matrices
- compare default failsafe behavior against delayed-observation behavior

Before Phase 8A, the project needs one clean layer for world/environment conditions.

Without this layer, synthetic odometry, optical flow, VIO, LiDAR, wind, lighting, and dashboard controls would all become mixed together.

## Key rule

A scenario is the full experiment recipe.

A scenario combines:

world + route + vehicle + altitude + GNSS profile + failsafe profile + aiding mode + environment conditions

A condition is a reusable environment preset.

Examples:

- lighting/noon_clear
- wind/none
- texture/high_texture

A batch combines multiple scenarios or scenario variants.

## Matrix types

### Capability matrix

Purpose:

Can this aiding method work at all?

Use easy conditions:

- wind: none
- lighting: noon_clear
- texture: high_texture
- route: simple

Phase 8A synthetic external odometry belongs here.

### Stress matrix

Purpose:

When does this aiding method fail?

Use harder conditions:

- wind: crosswind_5ms
- lighting: sunset_low_angle
- texture: low_texture
- sensor latency
- dropout
- higher altitude
- longer route

Stress matrices should come after the aiding method works in calm conditions.

## Condition hierarchy

experiments/configs/mvp/conditions/
├── lighting/
├── wind/
├── texture/
└── disturbance/

## Phase 7D acceptance criteria

Phase 7D is accepted when:

- this document exists
- experiments/configs/mvp/conditions exists
- lighting presets exist
- wind presets exist
- texture presets exist
- one GNSS ON scenario references conditions
- one GNSS-loss delayed scenario references conditions
- one Phase 7D smoke batch exists
- no PX4/Gazebo run is required
- existing Phase 7A/7B/7C runners are not broken

## Next phase

Phase 8A — Synthetic External Odometry Aiding.

## Results

Accepted.

Created reusable MVP condition presets for:

- lighting
- wind
- texture

Created two schema smoke scenarios:

- GNSS ON hover at 10m with noon clear, wind none, high texture
- GNSS-loss delayed-observation hover at 10m with noon clear, wind none, high texture

Created one Phase 7D smoke batch:

- phase7d_environment_matrix_smoke

No PX4/Gazebo run was required for this phase.

## Acceptance status

Phase 7D accepted.

The project is ready for Phase 8A — Synthetic External Odometry Aiding.

