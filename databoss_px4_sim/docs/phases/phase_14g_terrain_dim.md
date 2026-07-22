# Phase 14g - Dim-light terrain at 15 m

Status: **Accepted with limitations** (2026-07-22 UTC). Batch 7 of the
Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the six-case Phase 14 comparison on real heightmap terrain with
dim/overcast lighting at a safe 15 m AGL, using the Phase 14F terrain
height-reference strategy and ULog GNSS-state guard.

## Why this phase exists

Phase 14F proved the full stack on terrain under default/noon lighting.
Phase 14G adds the next condition: dim terrain. This isolates terrain-lighting
effects before the final Phase 14H 60 m dim-terrain endgame.

## In scope

- Add dim/overcast lighting support to the terrain world conversion path.
- Generate and validate `serefli_koschisar_flowtex_dim`.
- Run LK, SIFT, stock replicate pair, LK GNSS-on, and no-aid GNSS-loss cases.
- Verify every GNSS state from ULog GPS topics.

## Out of scope

- 60 m terrain flight.
- Literal night/exposure modeling.
- Wind.
- Fixing PX4 stock-flow terrain validation behavior.
- Fixing the no-loss runner long-hold bug.

## Inputs

- Terrain lighting generator:
  `scripts/worlds/heightmap_to_web_mesh_world.py`
- Generated dim terrain world:
  `generated_worlds/terrain/serefli_koschisar_flowtex_dim/serefli_koschisar_flowtex_dim.world`
- Provenance:
  `generated_worlds/terrain/serefli_koschisar_flowtex_dim/PROVENANCE.yaml`
- Batch YAML:
  `experiments/configs/mvp/batches/phase14g_terrain_dim_15m.yaml`
- Scenario YAMLs:
  `experiments/configs/mvp/scenarios/phase14g_*_serefli_koschisar_flowtex_dim_alt15m.yaml`
- Final comparison report:
  `experiments/comparisons/20260722_phase14g_terrain_dim_15m/report.md`

## Implementation

Added `--lighting-preset` support to
`scripts/worlds/heightmap_to_web_mesh_world.py`. The new
`dim_overcast_no_shadows` preset updates the generated terrain SDF scene,
directional sun, and GUI ambient/background fields while leaving the terrain
visual replacement path unchanged.

The terrain dim preset uses the Phase 14D dim/overcast ambient/background and
shadows-off policy, with a terrain-safe y-only low sun direction
`0.0 0.35 -0.55`. The source terrain world documents a shadow-map artifact
when both horizontal sun components are nonzero, so this keeps the dim-light
intent without reintroducing that terrain-specific rendering risk.

Generated and validated the world:

```bash
venv/bin/python scripts/worlds/heightmap_to_web_mesh_world.py \
  --source-world generated_worlds/terrain/serefli_koschisar_flowtex/serefli_koschisar_flowtex.world \
  --output-dir generated_worlds/terrain/serefli_koschisar_flowtex_dim \
  --output-world-name serefli_koschisar_flowtex_dim \
  --visual-mode colored_tiles \
  --tile-count 32 \
  --lighting-preset dim_overcast_no_shadows

gz sdf -k generated_worlds/terrain/serefli_koschisar_flowtex_dim/serefli_koschisar_flowtex_dim.world
```

SDF validation result: `Valid`.

## Commands

Batch launch:

```bash
sudo -u px4 bash -lc "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14g_terrain_dim_15m.yaml --continue-on-fail"
```

The LK GNSS-on reference entered the known no-loss long-hold path and was
manually closed out after valid GNSS-on evidence was collected. The no-aid
case was rerun directly after cleaning stale Gazebo websocket state.

## Expected outputs

- Six evidence runs with ULog/truth artifacts.
- Unified comparison report and plots.
- ULog GPS-state verification for every case.
- Gzipped ULogs and raw Gazebo truth after report generation.

## Acceptance criteria

- Dim terrain SDF validates and is physically used by the runs.
- GNSS-loss cases show actual ULog GPS loss (`fix_type < 3`).
- GNSS-on reference stays ULog GPS-on (`fix_type >= 3`).
- Rejected stock/no-aid behavior is retained only when ULog/truth evidence is
  complete and GNSS state is correct.

## Results

| Case | Status | GNSS state verified | Horizontal err max (m) | Max 3D err (m) | Notes |
|---|---:|---:|---:|---:|---|
| LK loss | accepted | loss verified | 2.265 | 2.279 | Bounded; degraded vs Phase 14F default-light terrain |
| SIFT loss | accepted with sensor-contract caveat | loss verified | 3.667 | 3.678 | Bounded; fewer bridge rows and sensor-contract reject |
| Stock loss r1 | rejected by runner; accepted as behavior evidence | loss verified | 46.131 | 46.136 | Rangefinder/terrain validation reject |
| Stock loss r2 | rejected by runner; accepted as behavior evidence | loss verified | 46.217 | 46.222 | Repeatable stock drift near 46 m Hmax |
| LK GNSS-on | accepted with manual closeout caveat | stayed on | 0.138 | 0.510 | `fix_type=3`, satellites=10 throughout |
| No-aid loss | rejected by runner; accepted as baseline evidence | loss verified | 38.264 | 38.274 | No optical-flow bridge or stock flow |

Comparison report and plots:

`experiments/comparisons/20260722_phase14g_terrain_dim_15m/`

## Interpretation

Phase 14G is valid dim-terrain characterization evidence. The new terrain
lighting code path worked, the generated SDF validated, and the report's ULog
GPS guard found no GNSS-state mismatch.

LK and SIFT stayed bounded under dim terrain at 15 m, but both worsened
relative to Phase 14F default-light terrain. Stock remained a caveated
terrain baseline and produced repeatable drift around 46 m. The no-aid case
was much worse than aided LK/SIFT but less catastrophic than the Phase 14F
no-aid terrain-default run; it is still a rejected-performance baseline, not
a pass.

## Known limitations

- PX4 source still depends on the Phase 14F `GZBridge::navSatCallback()`
  `SIM_GPS_USED` refresh patch for terrain GNSS-loss validity.
- The LK GNSS-on reference was manually closed out because the no-loss runner
  long-hold issue remains.
- Stock/no-aid terrain runs still trip rangefinder/altitude validation gates.
- Dim/overcast is simulated lighting, not camera exposure/noise or literal
  night.

## Cleanup

After report generation, all six Phase 14G ULogs and raw Gazebo-truth text
logs were compressed with `gzip -9` and verified with `gzip -t`.

## Files created or modified

- `scripts/worlds/heightmap_to_web_mesh_world.py`
- `generated_worlds/terrain/serefli_koschisar_flowtex_dim/`
- `experiments/configs/mvp/batches/phase14g_terrain_dim_15m.yaml`
- `experiments/configs/mvp/scenarios/phase14g_*_serefli_koschisar_flowtex_dim_alt15m.yaml`
- `experiments/comparisons/20260722_phase14g_terrain_dim_15m/`
- Six evidence run folders under `experiments/runs/*phase14g*`

## Next phase

Proceed to Phase 14H: dim terrain at 60 m AGL, reusing the Phase 14G dim
terrain world and Phase 14F terrain height-reference strategy.
