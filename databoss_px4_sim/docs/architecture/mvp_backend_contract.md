# DATABOSS MVP Backend Contract

**Stale (2026-07-13 sketch).** Phase 12/13 established the actual, richer
run/comparison contract this document predates (see
`docs/phases/phase_13_dashboard_data_contract.md` and the Phase 17A
contracts under `src/databoss_sim/contracts/`, which are grounded directly
against real run data rather than this sketch). Two sections below are
additionally flagged as **wrong**, not just stale, per the Phase 17B audit
(2026-07-24) that traced every field to the actual runner code - see the
inline notes.

## Goal

Define the first stable config format for DATABOSS MVP experiments.

The dashboard will later generate or edit these YAML files. For now, we write them manually.

## Required scenario fields

run:
  name:
  description:

vehicle:
  model:
  px4_airframe:
  start_pose:

world:
  name:
  size_m:
  terrain:
  lighting:
  wind:
  objects:

route:
  name:
  type:
  altitude_agl_m:
  duration_s:
  waypoints:

gnss:
  start_enabled:
  loss_enabled:
  loss_time_s:
  restore_after_run:

aiding:
  mode:
  latency_ms:
  noise:
  dropout:

logging:
  record_ulog:
  record_gazebo_truth:
  record_sensor_logs:

analysis:
  align_with_gazebo_truth:
  generate_plots:
  generate_summary:

## Accepted MVP aiding modes

**Wrong, per Phase 17B audit (2026-07-24): only `synthetic_external_odometry`
is actually read by the runner** (`auto_takeoff_land_pxh_truth.py`:
`external_odom_enabled = aiding.get("mode") == "synthetic_external_odometry"`
is the only place `aiding.mode` is ever compared). Real optical-flow
scenarios don't use `aiding.mode` at all - they use the independent
`flow_bridge:`/`stock_flow:` YAML blocks instead. `gnss_on`/
`gnss_off_no_aiding` are controlled by the separate `gnss:` block, not this
enum. `synthetic_optical_flow_rangefinder`/`image_optical_flow_rangefinder`
are not implemented anywhere in the codebase. Do not build a dashboard
scenario editor that presents this as a working 5-value choice - it isn't.

- gnss_on
- gnss_off_no_aiding
- synthetic_external_odometry
- synthetic_optical_flow_rangefinder
- image_optical_flow_rangefinder

## Hard rule

No MVP experiment is accepted without Gazebo ground-truth comparison.

## Environment condition references

**Wrong, per Phase 17B audit (2026-07-24): this `conditions/*.yaml` preset
system was never physically wired into the world generator** and should
NOT be exposed as dashboard knobs - see
`experiments/configs/mvp/conditions/README.md` (marked deprecated) for the
full finding. Real physical world/environment control is the
`experiments/configs/mvp/worlds/*.yaml` schema that
`scripts/worlds/build_gazebo_world.py` actually reads (`texture`,
`lighting`, `wind`, `objects`), referenced from a scenario via
`world.sdf_path` + `condition_is_physical: true`. Phase 17D's scenario
editor exposes that real schema instead.

Starting in Phase 7D, scenarios may reference reusable condition files.

Example:

conditions:
  lighting: experiments/configs/mvp/conditions/lighting/noon_clear.yaml
  wind: experiments/configs/mvp/conditions/wind/none.yaml
  texture: experiments/configs/mvp/conditions/texture/high_texture.yaml

Conditions are reusable presets. They do not replace the world, route, vehicle, GNSS, failsafe, or aiding fields.

