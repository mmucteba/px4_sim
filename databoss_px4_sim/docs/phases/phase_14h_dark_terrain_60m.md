# Phase 14h - Dim-light terrain at 60 m

Status: **Accepted with limitations** (2026-07-22 UTC). Batch 8 of the
Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the final Phase 14 endgame matrix on real heightmap terrain with
dim/overcast lighting at 60 m AGL, preserving ULog/truth evidence and
independently checking GNSS state for every case.

## Why this phase exists

Phase 14G proved the dim-terrain path at 15 m. Phase 14H combines the hardest
ingredients already isolated in earlier batches: 60 m altitude, terrain
relief, dim/overcast lighting, and GNSS loss. This is characterization
evidence, not a clean pass/fail method-win gate.

## Inputs

- Batch YAML:
  `experiments/configs/mvp/batches/phase14h_dark_terrain_60m.yaml`
- Scenario YAMLs:
  `experiments/configs/mvp/scenarios/phase14h_*_serefli_koschisar_flowtex_dim_alt60m.yaml`
- Dim terrain world:
  `generated_worlds/terrain/serefli_koschisar_flowtex_dim/serefli_koschisar_flowtex_dim.world`
- Final comparison report:
  `experiments/comparisons/20260722_phase14h_dark_terrain_60m/report.md`

## Execution notes

The initial full batch was stopped after the 60 m LK/SIFT cases showed the
long wall-time behavior expected at this altitude. The phase was then run
one case at a time so each ULog could be preserved before launching the next
case. Scratch attempts are marked with `SCRATCH_INVALID_RUN.md` and excluded
from the report.

The LK GNSS-on reference was manually closed out after valid no-loss evidence
was collected. Its ULog was copied from PX4 rootfs before any next launch,
then Gazebo truth and ULog CSVs were postprocessed manually.

## Results

| Case | Status | GNSS state verified | Horizontal err max (m) | Max 3D err (m) | Notes |
|---|---:|---:|---:|---:|---|
| LK loss | rejected by runner; accepted as behavior evidence | loss verified | 34.594 | 34.595 | Flow delivered; terrain/rangefinder validation diverged during drift |
| SIFT loss | rejected by runner; accepted as behavior evidence | loss verified | 96.102 | 96.102 | Severe dim-terrain 60 m degradation |
| Stock loss r1 | rejected by runner; accepted as behavior evidence | loss verified | 38.077 | 38.084 | Valid rejected-performance repeat |
| Stock loss r2 | rejected by runner; accepted as behavior evidence | loss verified | 54.452 | 54.461 | Replacement r2 after one invalid pre-offboard abort |
| LK GNSS-on | accepted with manual closeout caveat | stayed on | 0.132 | 0.954 | `fix_type=3`, satellites=10 throughout |
| No-aid loss | rejected by runner; accepted as baseline evidence | loss verified | 36.012 | 36.024 | No optical-flow bridge or stock flow |

Comparison report and plots:

`experiments/comparisons/20260722_phase14h_dark_terrain_60m/`

## Interpretation

Phase 14H is valid endgame evidence with limitations. All report cases match
their manifest GNSS state according to ULog GPS topics. LK GNSS-loss remained
bounded better than SIFT, but drift was large at 60 m dim terrain. SIFT
degraded the most. Stock and no-aid runs are retained as valid
rejected-performance baselines, not passes.

The GNSS-on LK reference proves the 60 m dim-terrain stack itself can remain
tightly aligned when GPS is available; the GNSS-loss degradation is therefore
not a basic truth/alignment failure.

## Scratch exclusions

- `20260722_034525_phase14h_lk_xy_gnssloss...`: interrupted before ULog
  preservation; rerun replaced it.
- `20260722_035617_phase14h_sift_xy_gnssloss...`: batch stopped before
  evidence completion; rerun replaced it.
- `20260722_043106_phase14h_stock_gnssloss...`: aborted before OFFBOARD and
  GNSS-loss commands; replacement r2 completed.

## Cleanup

After report generation, all six Phase 14H evidence ULogs and raw
Gazebo-truth text logs were compressed with `gzip` and verified with
`gzip -t`. Scratch large artifacts were also compressed losslessly.

## Files created or modified

- `experiments/configs/mvp/batches/phase14h_dark_terrain_60m.yaml`
- `experiments/configs/mvp/scenarios/phase14h_*_serefli_koschisar_flowtex_dim_alt60m.yaml`
- `experiments/comparisons/20260722_phase14h_dark_terrain_60m/`
- Six evidence run folders under `experiments/runs/*phase14h*`

## Next phase

Phase 14 is complete. Next work should be analysis and packaging of the
Phase 14A-14H campaign, not another hidden batch.
