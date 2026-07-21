# Phase 8B - Generated World PX4 Flight Proof

Date: 2026-07-09

## Goal

Prove the generated Phase 8B Gazebo worlds are compatible with a real PX4 SITL x500 takeoff, hover, land, ULog copy, Gazebo truth recording, and EKF-vs-truth alignment.

This is stronger than the earlier spawn proof:

```text
generated world -> standalone Gazebo -> PX4 standalone attach -> PX4 x500 spawn -> takeoff/hover/land -> truth alignment
```

## Implementation Change

`scripts/runner/auto_takeoff_land_pxh_truth.py` is now world-aware.

For non-default generated worlds, the runner:

- resolves `world.name` / `world.sdf_path` from the scenario YAML
- launches the generated SDF as standalone Gazebo
- starts PX4 with `PX4_GZ_STANDALONE=1`
- sets `PX4_GZ_WORLD` and a unique `GZ_PARTITION`
- sets PX4 home origin for NavSat/global-position validity
- records truth from `/world/<world>/dynamic_pose/info`

Important fix:

The first attempt booted PX4 but `vehicle_global_position` never became valid because the standalone generated world had no spherical coordinates. The runner now sets:

```text
PX4_HOME_LAT=47.397742
PX4_HOME_LON=8.545594
PX4_HOME_ALT=488.0
```

PX4 then applies spherical coordinates through Gazebo before flight.

## Configs

```text
experiments/configs/mvp/scenarios/phase8b_px4_flight_flat_rural_high_texture_noon.yaml
experiments/configs/mvp/scenarios/phase8b_px4_flight_flat_rural_low_texture_noon.yaml
experiments/configs/mvp/batches/phase8b_generated_world_px4_flight_smoke.yaml
```

## Accepted Runs

| World | Run | Airborne s | Max Height m | H Error Mean m | H Error Max m | Truth Rows | Accepted |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `flat_rural_high_texture_noon` | `experiments/runs/20260709_144318_phase8b_px4_flight_flat_rural_high_texture_noon_pxh_takeoff_land_truth` | 21.032 | 2.476 | 0.032459 | 0.175841 | 5555 | yes |
| `flat_rural_low_texture_noon` | `experiments/runs/20260709_144526_phase8b_px4_flight_flat_rural_low_texture_noon_pxh_takeoff_land_truth` | 23.772 | 2.545 | 0.037863 | 0.109267 | 5605 | yes |

Batch wrappers:

```text
experiments/batches/20260709_144315_phase8b_generated_world_px4_flight_smoke
experiments/batches/20260709_144523_phase8b_generated_world_px4_flight_smoke
```

## Decision

Phase 8B generated worlds are PX4-flight-compatible.

We can now use these worlds as the base for Phase 8C downward camera proof and Phase 8D TF03-style rangefinder proof.
