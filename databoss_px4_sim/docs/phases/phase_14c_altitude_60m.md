# Phase 14c — Altitude step 3: GNSS-denied optical flow at 60 m

Status: **Accepted as evidence; matrix rejected** (2026-07-21). Batch 3 of
the Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the Phase 14 six-case flat-world comparison at **60 m** AGL with genuine
GNSS-loss windows, Gazebo-truth alignment, and the larger 600 m photo-textured
field needed for altitude/footprint headroom.

## Inputs

- Batch YAML:
  `experiments/configs/mvp/batches/phase14c_altitude_60m.yaml`
- Batch run:
  `experiments/batches/20260721_205051_phase14c_altitude_60m`
- Final comparison report:
  `experiments/comparisons/20260721_phase14c_altitude_60m/report.md`

## Implementation

Copied the Phase 14b altitude scenarios to Phase 14c and changed:

- `route.altitude_agl_m: 60.0`
- `control.z_m: -60.0`
- world to `flat_rural_phototex_600m_noon`
- SIFT `send_max_range_m: 90.0`, leaving margin below the 100 m lidar max

The generated world validated with `gz sdf -k`. The batch command used the
full timing contract:

```bash
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14c_altitude_60m.yaml --continue-on-fail"
```

Rejected runs were postprocessed manually after the batch so all six cases have
full-window Gazebo-truth metrics.

## Results

| Case | Accepted | GNSS loss verified | Horizontal err max (m) | Max 3D err (m) | Truth drift end (m) | Notes |
|---|---:|---:|---:|---:|---:|---|
| LK loss | false | true | 575.19 | 582.59 | 64.94 | Diverged heavily after loss; range/height gate failed |
| SIFT loss | false | true | 419.25 | 427.28 | 70.07 | No longer bounded at 60 m; range/height gate failed |
| Stock loss r1 | false | true | 67.40 | 67.42 | 40.19 | Valid rejected baseline; stock camera far clip caveat |
| Stock loss r2 | false | true | 34.29 | 34.37 | 29.73 | Valid rejected baseline; repeatable GNSS loss |
| LK GNSS-on | true | n/a | 0.15 | 0.99 | 16.44 | Stayed GNSS-on; reference alignment good |
| No aid loss | false | true | 43.89 | 43.91 | 43.26 | Expected dead-reckoning drift baseline |

Batch summary:
`experiments/batches/20260721_205051_phase14c_altitude_60m/batch_summary.md`

Batch metrics:
`experiments/batches/20260721_205051_phase14c_altitude_60m/batch_metrics.md`

Comparison report and plots:
`experiments/comparisons/20260721_phase14c_altitude_60m/`

## Interpretation

Phase 14c is a valid 60 m evidence set, but the matrix is rejected. All
GNSS-loss cases independently show GPS loss in ULog (`fix_type < 3`; runner
status `gnss_loss_verified=true`, observed `fix_type=0.0`) and completed the
50 s post-loss observation window, so no run was suspicious enough to rerun.

At 60 m on the flat 600 m photo-textured field, SIFT loses the bounded behavior
seen twice at 35 m. LK and SIFT both diverged severely after loss. Stock is
preserved as a baseline only: PX4's stock optical-flow camera model has a
30 m far clip while this rung flies at 60 m AGL, so its 60 m result is a
baseline/simulator limitation, not a repaired method claim.

## Cleanup

After the report was generated and verified, Phase 14C raw ULogs were
losslessly compressed with `gzip -n` and checked with `gzip -t`. Phase 14B and
14C raw Gazebo-truth text logs were also gzipped after their reports existed to
restore disk headroom without deleting data.

## Files created

- `experiments/configs/mvp/batches/phase14c_altitude_60m.yaml`
- `experiments/configs/mvp/scenarios/phase14c_*_alt60m.yaml`
- `experiments/batches/20260721_205051_phase14c_altitude_60m/`
- `experiments/comparisons/20260721_phase14c_altitude_60m/`
- Six run folders under `experiments/runs/20260721_*phase14c*alt60m*`

## Next phase

Proceed to Phase 14d: dim/overcast flat-world lighting at safe 15 m altitude.
Do not treat 60 m flat/noon as an accepted method gate; treat it as measured
failure evidence that should inform later combined-condition interpretation.
