# DATABOSS MVP Backend Contract

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

- gnss_on
- gnss_off_no_aiding
- synthetic_external_odometry
- synthetic_optical_flow_rangefinder
- image_optical_flow_rangefinder

## Hard rule

No MVP experiment is accepted without Gazebo ground-truth comparison.

## Environment condition references

Starting in Phase 7D, scenarios may reference reusable condition files.

Example:

conditions:
  lighting: experiments/configs/mvp/conditions/lighting/noon_clear.yaml
  wind: experiments/configs/mvp/conditions/wind/none.yaml
  texture: experiments/configs/mvp/conditions/texture/high_texture.yaml

Conditions are reusable presets. They do not replace the world, route, vehicle, GNSS, failsafe, or aiding fields.

The dashboard should eventually expose these as selectable environment knobs.

