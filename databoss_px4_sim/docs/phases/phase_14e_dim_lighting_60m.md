# Phase 14e - Dim-light flat-world stress at 60 m

Status: **Accepted as characterization evidence; matrix rejected**
(2026-07-22 UTC). Batch 5 of the Phase 14 difficulty roadmap
(`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the Phase 14 six-case comparison under the combined flat-world stressor:
dim/overcast lighting plus 60 m AGL on the larger 600 m photo-textured field.

This was not treated as a method acceptance gate because Phase 14C already
showed the current methods are not bounded at 60 m under noon lighting.

## Inputs

- World YAML:
  `experiments/configs/mvp/worlds/flat_rural_phototex_600m_dim.yaml`
- Generated SDF:
  `generated_worlds/flat_rural_phototex_600m_dim.sdf`
- Batch YAML:
  `experiments/configs/mvp/batches/phase14e_dim_lighting_60m.yaml`
- Batch run:
  `experiments/batches/20260721_230820_phase14e_dim_lighting_60m`
- Final comparison report:
  `experiments/comparisons/20260721_phase14e_dim_lighting_60m/report.md`

## Implementation

Created a 600 m dim/overcast flat photo-textured world by applying the Phase
14D lighting preset to the Phase 14C 600 m field. The SDF validated with
`gz sdf -k`. Six Phase 14E scenarios reused the full Phase 14 timing contract:
90 s hover command budget, GNSS cut at stable altitude, and 50 s post-loss
observation for GNSS-loss cases.

```bash
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14e_dim_lighting_60m.yaml --continue-on-fail"
```

All rejected cases were manually postprocessed and aligned after the batch so
all six runs have Gazebo-truth metrics.

## Results

| Case | Accepted | GNSS state verified | Horizontal err max (m) | Max 3D err (m) | Truth drift end (m) | Notes |
|---|---:|---:|---:|---:|---:|---|
| LK loss | false | loss verified | 771.27 | 772.69 | 81.20 | Severe divergence at dim 60 m |
| SIFT loss | false | loss verified | 61.73 | 77.15 | 25.22 | Valid loss; landed during requested post-loss hover |
| Stock loss r1 | false | loss verified | 46.40 | 46.42 | 28.95 | Valid repeat; stock 30 m camera far-clip caveat |
| Stock loss r2 | false | loss verified | 51.87 | 51.93 | 49.90 | Valid repeat; consistent procedural evidence |
| LK GNSS-on | true | stayed on | 0.36 | 0.94 | 16.41 | Flight-valid reference; flow sign sentinel rejected |
| No aid loss | false | loss verified | 67.90 | 67.94 | 57.11 | Expected dead-reckoning divergence |

Batch summary:
`experiments/batches/20260721_230820_phase14e_dim_lighting_60m/batch_summary.md`

Batch metrics:
`experiments/batches/20260721_230820_phase14e_dim_lighting_60m/batch_metrics.md`

Comparison report and plots:
`experiments/comparisons/20260721_phase14e_dim_lighting_60m/`

## Interpretation

Phase 14E is a valid combined-condition characterization set. Every GNSS-loss
case independently showed GPS loss in ULog evidence (`fix_type < 3`; runner
observed `fix_type=0.0`) and copied a ULog. The LK GNSS-on reference did not
request GNSS loss, stayed GNSS-on, and remained close to Gazebo truth.

The combined dim-light plus 60 m condition rejected the comparison matrix. LK
diverged severely, SIFT and stock were procedurally valid but not bounded, and
the unaided baseline diverged as expected. Stock remains a caveated reference
because PX4's stock optical-flow camera model has a 30 m far clip while this
rung flies at 60 m AGL.

No run was suspicious enough to rerun. Rejections are retained as real evidence
of method/environment limits, not discarded test failures.

## Cleanup

After report generation, Phase 14E raw ULogs and raw Gazebo-truth text logs
were compressed with `gzip -9` and verified with `gzip -t`.

## Files created

- `experiments/configs/mvp/worlds/flat_rural_phototex_600m_dim.yaml`
- `generated_worlds/flat_rural_phototex_600m_dim.sdf`
- `experiments/configs/mvp/batches/phase14e_dim_lighting_60m.yaml`
- `experiments/configs/mvp/scenarios/phase14e_*_dim_alt60m.yaml`
- `experiments/batches/20260721_230820_phase14e_dim_lighting_60m/`
- `experiments/comparisons/20260721_phase14e_dim_lighting_60m/`
- Six run folders under `experiments/runs/*phase14e*dim_alt60m*`

## Next phase

Proceed to Phase 14F: first full-stack GNSS-loss terrain baseline on the
`serefli_koschisar` terrain world at a safe 15-35 m altitude. The main decision
is height reference strategy, because `EKF2_RNG_A_HMAX=80` is a flat-ground
assumption and terrain relief makes rangefinder height above ground differ
from absolute altitude.
