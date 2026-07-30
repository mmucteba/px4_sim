# Phase 20 - Vehicle Composition and Install Pipeline

Status: Accepted (2026-07-29)

## Goal

Make DATABOSS vehicle creation repeatable from a structured spec: compose the
repo-side Gazebo model and PX4 airframe, install them into a PX4 checkout,
register the airframe, refresh the patch/manifest pins, rebuild PX4, and keep
deployment checks trustworthy when a vehicle has only been generated.

## Why this phase exists

Hand-merged vehicle SDFs were good enough for the original
`x500_cam_lidar_down`, but they made the next sensor stack an archeology
exercise. Phase 20 gives the dashboard and CLI the same composition and install
path, with preflight checks for the host-specific traps that otherwise appear
only after a long build.

## Spec schema

The composer input is a mapping with:

- `name`: lowercase model directory name, `[a-z0-9_]+`.
- `base`: currently only `x500`.
- `base_airframe`: currently `4001_gz_x500`.
- `description`: written into `model.config` and the airframe comments.
- `sensors`: ordered list of `include`, `camera`, and `gpu_lidar` entries.
- `boot_params`: optional PX4 parameters written into the generated airframe.

Worked example:

```json
{
  "name": "x500_widecam_lidar_down",
  "base": "x500",
  "base_airframe": "4001_gz_x500",
  "description": "DATABOSS x500 with downward wide camera and finite-grid lidar.",
  "sensors": [
    {
      "kind": "camera",
      "link_name": "wide_camera_link",
      "sensor_name": "wide_camera",
      "hfov_rad": 2.0,
      "width": 1280,
      "height": 960,
      "rate_hz": 30,
      "mount": [0.08, 0, -0.04, 0, 1.5708, 0]
    },
    {
      "kind": "gpu_lidar",
      "link_name": "lidar_sensor_link",
      "housing": "LW20",
      "housing_mount": [0.08, 0, -0.079, 0, 1.57, 0],
      "mount": [0.08, 0, -0.05, 0, 1.57, 0],
      "sensor_name": "lidar",
      "sensor_pose": [0, 0, 0, 3.14, 0, 0],
      "h_samples": 3,
      "v_samples": 1,
      "h_min_angle_rad": -0.02,
      "h_max_angle_rad": 0.02,
      "v_min_angle_rad": 0,
      "v_max_angle_rad": 0,
      "range_min_m": 0.1,
      "range_max_m": 100.0,
      "range_resolution_m": 0.01,
      "rate_hz": 50,
      "visualize": true
    }
  ],
  "boot_params": {"EKF2_OF_DELAY": 111}
}
```

`include` entries merge an existing model and fix its discovered child link to
`base_link`. `camera` and `gpu_lidar` entries emit full parameterized sensor
blocks. Include-only rangefinders would be useless here: PX4's `LW20` and the
DATABOSS `afbr_s50` contain housing meshes, not sensors. Every rangefinder's
grid, angles, range, resolution, and rate must be declared in the vehicle SDF.

## Validation

- `base` must be `x500`: the wind-response patch targets `x500_base`, so a
  different base silently makes wind scenarios invalid.
- A `gpu_lidar` cannot be `1x1`: the 1-pixel depth render goes `inf` under
  roll; this produced 82% `inf` on East legs on 2026-07-13.
- Included child links are discovered from SDF instead of guessed. A guessed
  link name can still load in Gazebo but leave the sensor detached from the
  vehicle.

The golden gate is:

```bash
venv/bin/python scripts/sim/check_vehicle_composer.py
```

It composes both accepted vehicles and compares them against the checked-in
model/airframe outputs.

## Install

The dashboard can generate without installing via `POST /api/vehicles/generate`
with `write: true`. The CLI composes from a YAML/JSON-compatible spec and then
runs the install path:

```bash
venv/bin/python scripts/sim/add_vehicle.py --spec /path/to/spec.yaml
```

Install through the dashboard or the CLI. The install path syncs the model into
`Tools/simulation/gz/models/`, installs the generated airframe into
`ROMFS/px4fmu_common/init.d-posix/airframes/`, inserts exactly one airframe
line in the PX4 `CMakeLists.txt`, refreshes `deploy/px4/px4_pins.yaml`, and
regenerates `deploy/px4/0004-airframes-cmakelists-register.patch`.

A PX4 reconfigure/rebuild is required. `make px4_sitl gz_<model>` targets come
from a configure-time `file(GLOB ...*_gz_*)` in
`src/modules/simulation/gz_bridge/CMakeLists.txt:49`, and `rcS:56` resolves the
airframe by `ls` over the built `etc/` tree. Copying files into the source tree
does not create a runnable target.

The installer holds the job lock and refuses to run under a live flight/job. A
relink or rebuild under an active run destroys that run's PX4 process state, so
vehicle install is mutually exclusive with flights.

## Generated But Not Installed

`POST /api/vehicles/generate` with `write: true` and
`scripts/sim/add_vehicle.py` can leave a legitimate repo-side model and
airframe that are not yet in PX4 or pins. Deployment sync reports this as
`NOT_INSTALLED`, not a failure. Resolve it with:

```bash
venv/bin/python scripts/sim/add_vehicle.py --spec /path/to/spec.yaml
```

or the dashboard vehicle Install action. A model declared in
`deploy/px4/px4_pins.yaml` but absent from PX4 remains `MISSING_PX4` and fails;
a declared, installed model whose files differ remains `DRIFTED` and fails.

## Host Permission Notes

Two permission items are known on this host:

- `deploy/px4/` can be root-owned. A real deploy's bootstrap
  `fix_ownership()` covers `$PROJECT_ROOT` and repairs this.
- The PX4 tree's airframes `CMakeLists.txt` can be root-owned. Bootstrap does
  not cover this because `fix_ownership()` only chowns `$PROJECT_ROOT`; fix the
  PX4 checkout ownership separately.

## Current Vehicles

- `x500_cam_lidar_down`: registered, built, and flight-proven.
- `x500_ark_flow`: registered and built, but never flown. Zero scenarios
  reference it today, so it is one scenario away from a first flight.
- `afbr_s50`: housing-only submodel used by `x500_ark_flow`; it is pinned so
  deploy checks catch missing PX4 installs.
