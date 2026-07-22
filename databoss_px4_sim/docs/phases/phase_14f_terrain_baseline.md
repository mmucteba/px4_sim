# Phase 14f - Terrain baseline at 15 m

Status: **Accepted with limitations** (2026-07-22 UTC). Batch 6 of the
Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the six-case Phase 14 comparison on the `serefli_koschisar_flowtex`
terrain world at a safe 15 m AGL, with GNSS state checked from ULog evidence
for every case.

## Why this phase exists

This is the first full-stack GNSS-loss optical-flow batch on real heightmap
terrain. Previous terrain work proved the world, camera, and truth path, but
not the full GNSS-denied LK/SIFT/stock/no-aid comparison.

The central risk was height reference. `EKF2_RNG_A_HMAX=80` worked on flat
ground because rangefinder height matched absolute height above takeoff. On
terrain, the rangefinder reads height above varying ground, so Phase 14F used
`EKF2_RNG_A_HMAX=5` and accepted baro/terrain behavior as a measured
limitation instead of forcing flat-ground height fusion onto a relief world.

## In scope

- LK GNSS-loss.
- SIFT GNSS-loss.
- PX4 stock optical-flow GNSS-loss, two replicates.
- LK GNSS-on reference.
- Unaided GNSS-loss baseline.
- ULog-based GNSS state verification for every case.
- Terrain rangefinder/height behavior characterization.

## Out of scope

- Terrain dim/overcast lighting.
- 60 m terrain flight.
- Fixing PX4 stock-flow terrain altitude behavior.
- Fixing the no-loss runner closeout bug.

## Inputs

- Batch YAML:
  `experiments/configs/mvp/batches/phase14f_terrain_baseline_15m.yaml`
- Scenario YAMLs:
  `experiments/configs/mvp/scenarios/phase14f_*_serefli_koschisar_flowtex_alt15m.yaml`
- Terrain world:
  `generated_worlds/terrain/serefli_koschisar_flowtex/serefli_koschisar_flowtex.world`
- Final comparison report:
  `experiments/comparisons/20260722_phase14f_terrain_baseline_15m/report.md`

## Implementation

Created five Phase 14F scenario YAMLs plus the batch manifest. Terrain
scenarios retained the Phase 14 timing contract, 15 m AGL target, `vy=0.2
m/s`, and `skip_landing_command: true`.

During the first terrain GNSS-loss smoke, PX4 accepted `param set SIM_GPS_USED
0` but the ULog stayed at `fix_type=3`. The terrain world includes Gazebo
NavSat publishing, so PX4's Gazebo bridge needed to refresh the runtime
`SIM_GPS_USED` parameter inside the NavSat callback before publishing
`sensor_gps`.

PX4 patch applied:

`/opt/sim_px4/PX4-Autopilot/src/modules/simulation/gz_bridge/GZBridge.cpp`

```cpp
void GZBridge::navSatCallback(const gz::msgs::NavSat &msg)
{
    _sim_gps_used.update();

    const uint64_t timestamp = hrt_absolute_time();
```

PX4 was rebuilt with:

```bash
sudo -u px4 bash -lc "cd /opt/sim_px4/PX4-Autopilot || exit 1
deactivate 2>/dev/null || true
unset VIRTUAL_ENV PYTHONHOME PYTHONPATH
make px4_sitl gz_x500_cam_lidar_down"
```

All reported GNSS-loss runs were collected after that patch and verified from
ULog GPS topics.

## Commands

The batch runner was used for the closed-out cases:

```bash
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14f_terrain_baseline_15m.yaml --continue-on-fail"
```

The LK GNSS-on reference required a direct rerun because the no-loss runner
did not close out normally. PX4 was stopped gracefully, the ULog was recovered
from PX4 rootfs, and Gazebo truth was postprocessed with:

```bash
python scripts/runner/postprocess_latest_truth_run.py \
  --run-dir experiments/runs/20260722_022053_phase14f_lk_xy_gnsson_off70s_serefli_koschisar_flowtex_alt15m_pxh_takeoff_land_truth \
  --model-name x500_cam_lidar_down_0

python scripts/runner/align_latest_truth_run.py \
  --run-dir experiments/runs/20260722_022053_phase14f_lk_xy_gnsson_off70s_serefli_koschisar_flowtex_alt15m_pxh_takeoff_land_truth \
  --comparison-window full
```

## Expected outputs

- One comparison report with six cases.
- ULog GNSS-state verification in the report.
- Gazebo truth alignment metrics for all six cases.
- Gzipped ULogs and raw Gazebo truth after report generation.

## Acceptance criteria

- GNSS-loss cases must show actual GPS loss in ULog GPS topics, not just a
  successful `SIM_GPS_USED 0` command.
- GNSS-on reference must stay GNSS-on in ULog GPS topics.
- Rejected stock/no-aid behavior is accepted as evidence only if ULog/truth
  artifacts exist and GNSS state is correct.
- Terrain height/rangefinder caveats must be recorded instead of hidden.

## Results

| Case | Status | GNSS state verified | Horizontal err max (m) | Max 3D err (m) | Notes |
|---|---:|---:|---:|---:|---|
| LK loss | accepted | loss verified | 1.191 | 1.203 | Bounded terrain GNSS-loss result |
| SIFT loss | accepted | loss verified | 2.478 | 2.496 | Bounded but worse than LK |
| Stock loss r1 | rejected by runner; accepted as behavior evidence | loss verified | 51.264 | 51.265 | Climbed to about 29.4 m; rangefinder/terrain caveat |
| Stock loss r2 | rejected by runner; accepted as behavior evidence | loss verified | 109.052 | 109.055 | Replicate confirmed unstable stock behavior |
| LK GNSS-on | accepted with manual closeout caveat | stayed on | 0.237 | 0.340 | `fix_type=3`, satellites=10 throughout |
| No-aid loss | rejected by runner; accepted as catastrophic baseline | loss verified | 417.551 | 456.022 | Climbed to about 204.5 m and diverged |

Comparison report and plots:

`experiments/comparisons/20260722_phase14f_terrain_baseline_15m/`

## Interpretation

Phase 14F is valid terrain-baseline evidence. LK and SIFT stayed bounded
under verified GNSS loss on the terrain texture at 15 m, with LK tighter than
SIFT in this batch. PX4 stock flow and unaided runs had correct GNSS-loss
state and usable logs, but their behavior was unstable and remained rejected
by validation gates; those runs are retained as real performance evidence, not
discarded as suspicious tests.

The initial terrain GNSS-loss failure was a real simulator-integration issue:
PX4's Gazebo bridge was not refreshing `SIM_GPS_USED` in the NavSat callback.
After the bridge patch, all GNSS-loss cases showed ULog `fix_type=0` during
the loss window.

## Known limitations

- PX4 source is locally patched in `GZBridge.cpp`; this must be carried
  forward or upstreamed before future terrain GNSS-loss batches.
- The LK GNSS-on reference was manually closed out because the no-loss direct
  runner did not stop normally.
- Stock/no-aid terrain runs trip rangefinder/altitude validation checks; they
  are accepted as rejected-performance evidence only.
- Terrain lighting is still default/noon; dim terrain requires Phase 14G code.

## Cleanup

After report generation, Phase 14F ULogs and raw Gazebo-truth text logs were
compressed with `gzip -9` and verified with `gzip -t`.

## Files created or modified

- `experiments/configs/mvp/batches/phase14f_terrain_baseline_15m.yaml`
- `experiments/configs/mvp/scenarios/phase14f_*_serefli_koschisar_flowtex_alt15m.yaml`
- `experiments/comparisons/20260722_phase14f_terrain_baseline_15m/`
- Six accepted-evidence run folders under `experiments/runs/*phase14f*`
- `/opt/sim_px4/PX4-Autopilot/src/modules/simulation/gz_bridge/GZBridge.cpp`

## Next phase

Proceed to Phase 14G: port dim/overcast lighting controls into the terrain
world generator and run the same terrain matrix at safe 15 m altitude.
