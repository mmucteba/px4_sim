# PX4 Deployment Pins

These files describe the PX4/Gazebo state required to reproduce DATABOSS
results on a fresh host. The patches are intentionally small because each one
protects a specific physical assumption in the experiments.

## Pinned Inputs

- PX4: `994dec2c4101b01424f0cd46aae29f3e5b3c6f64`
  (`v1.18.0-alpha1-486-g994dec2c41`)
- PX4 build target: `px4_sitl_default`
- PX4 Gazebo models submodule: `Tools/simulation/gz` at
  `bb0b9cf974acf4f1bcb5f5fcf80b88841562dea9`
- Gazebo distro: `gz-harmonic`
- Gazebo Sim version: `8.14.0`

## Patch Inventory

- `0001-gz-bridge-sim-gps-used.patch` applies from the PX4 root and adds
  `_sim_gps_used.update();` inside `GZBridge::navSatCallback`. Without it,
  `param set SIM_GPS_USED 0` is not picked up and GNSS loss does not actually
  happen. This fails silently.
- `0002-server-config-wind-effects.patch` applies from the PX4 root and loads
  the `gz-sim-wind-effects-system` Gazebo plugin. Without it, wind scenarios
  run without physical wind. This fails silently.
- `0003-x500-base-enable-wind.patch` must be applied from inside
  `Tools/simulation/gz` because its paths are submodule-relative. It adds
  `<enable_wind>true</enable_wind>` to `models/x500_base/model.sdf`. Without
  it, the vehicle ignores wind even when the world has wind. This fails
  silently.
- `0004-airframes-cmakelists-register.patch` applies from the PX4 root and
  registers airframes `4022_gz_x500_cam_lidar_down` and
  `4023_gz_x500_ark_flow` in the ROMFS CMake list. Without it, those airframes
  are not built into PX4; this fails loudly when the airframe is missing.

## Rebuild Requirement

After applying `0001` or `0004`, rebuild PX4:

```bash
make px4_sitl_default
```

Do not skip this. For `0001`, a checkout can look patched while
`build/px4_sitl_default/bin/px4` still contains the old compiled bridge
behavior. For `0004`, the source airframes must be staged into
`build/px4_sitl_default/etc/init.d-posix/airframes/`. In either stale-build
state the deployment appears correct and can still produce invalid results.

Run the deployment checker as the final bootstrap step:

```bash
venv/bin/python scripts/deploy/check_deployment.py
```
