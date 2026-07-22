# Phase 14d — Dim-light flat-world stress at 15 m

Status: **Accepted as valid evidence with caveats** (2026-07-21). Batch 4 of
the Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the Phase 14 six-case comparison under dim/overcast lighting at a safe
15 m AGL, isolating lighting/contrast from the already-tested altitude axis.

## Inputs

- World YAML: `experiments/configs/mvp/worlds/flat_rural_phototex_dim.yaml`
- Batch YAML: `experiments/configs/mvp/batches/phase14d_dim_lighting_15m.yaml`
- Batch run: `experiments/batches/20260721_221131_phase14d_dim_lighting_15m`
- Final comparison report:
  `experiments/comparisons/20260721_phase14d_dim_lighting_15m/report.md`

## Implementation

Created a dim flat photo-textured world from the noon world with lower ambient
light, darker background, lower sun elevation, and shadows disabled. The SDF
validated with `gz sdf -k`. Six Phase 14D scenarios reused the Phase 14A/14B
timing contract:

```bash
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14d_dim_lighting_15m.yaml --continue-on-fail"
```

The no-aid rejected case was postprocessed manually after the batch so all six
cases have Gazebo-truth alignment and metrics.

## Results

| Case | Accepted | GNSS loss verified | Horizontal err max (m) | Max 3D err (m) | Truth drift end (m) | Notes |
|---|---:|---:|---:|---:|---:|---|
| LK loss | true | true | 1.45 | 1.47 | 10.98 | Bounded under dim light |
| SIFT loss | true | true | 2.02 | 2.05 | 2.65 | Valid evidence, but landed early after dim-light feature/match degradation |
| Stock loss r1 | true | true | 0.71 | 0.76 | 10.42 | Bounded |
| Stock loss r2 | true | true | 0.45 | 0.55 | 10.59 | Bounded repeat |
| LK GNSS-on | true | n/a | 0.15 | 0.48 | 16.66 | Stayed GNSS-on; flow sign sentinel accepted |
| No aid loss | false | true | 40.92 | 40.92 | 39.48 | Expected dead-reckoning divergence/descent |

Batch summary:
`experiments/batches/20260721_221131_phase14d_dim_lighting_15m/batch_summary.md`

Batch metrics:
`experiments/batches/20260721_221131_phase14d_dim_lighting_15m/batch_metrics.md`

Comparison report and plots:
`experiments/comparisons/20260721_phase14d_dim_lighting_15m/`

## Interpretation

Phase 14D is a valid dim-light evidence set. All GNSS-loss runs independently
show GPS loss in ULog evidence (`fix_type < 3`; runner observed
`fix_type=0.0`) and copied ULogs. The LK GNSS-on reference stayed GNSS-on.
No run was suspicious enough to rerun.

Dim lighting at 15 m did not break LK or stock. SIFT remained metrically
bounded over its aligned window, but the run is caveated because the vehicle
landed early (`airborne_hover_wait_ok=false`, ULog airborne duration
`43.624 s`) after SIFT feature/match quality degraded in dim light. The no-aid
baseline completed the 50 s post-loss observation window and then diverged
under dead reckoning, which is the expected baseline failure.

## Cleanup

After report generation, Phase 14D raw ULogs and raw Gazebo-truth text logs
were compressed with `gzip -n` and verified with `gzip -t`. Older already
reported raw Gazebo-truth logs from previous phases were also gzipped
losslessly to maintain disk headroom for the remaining batches.

## Files created

- `experiments/configs/mvp/worlds/flat_rural_phototex_dim.yaml`
- `generated_worlds/flat_rural_phototex_dim.sdf`
- `experiments/configs/mvp/batches/phase14d_dim_lighting_15m.yaml`
- `experiments/configs/mvp/scenarios/phase14d_*_dim_alt15m.yaml`
- `experiments/batches/20260721_221131_phase14d_dim_lighting_15m/`
- `experiments/comparisons/20260721_phase14d_dim_lighting_15m/`
- Six run folders under `experiments/runs/20260721_*phase14d*dim_alt15m*`

## Next phase

Proceed to Phase 14E: dim/overcast lighting combined with 60 m altitude on a
600 m flat photo-textured field. Because Phase 14C already showed 60 m is not
bounded for LK/SIFT, Phase 14E should be framed as combined-condition
characterization, not as a method acceptance gate.
