# Phase 14b — Altitude step 2: GNSS-denied optical flow at 35 m

Status: **Accepted as evidence; matrix rejected** (2026-07-21). Batch 2 of
the Phase 14 difficulty roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

Run the Phase 14a six-case flat-world comparison at **35 m** AGL with the
same commanded +Y local-hold profile, genuine GNSS-loss windows, and Gazebo
truth alignment.

## Why this phase exists

Phase 14a proved the 15 m rung. This rung tests whether the same optical-flow
contracts still hold when the camera footprint is larger and apparent image
motion is weaker.

## In scope

- `flat_rural_phototex_noon`
- 35 m AGL
- LK GNSS-loss, SIFT GNSS-loss, stock PX4 flow GNSS-loss x2
- LK GNSS-on reference
- unaided GNSS-loss baseline
- full 50 s post-loss observation for GNSS-loss cases

## Out of scope

- Terrain height-reference changes
- New optical-flow tuning
- New world generation
- Dashboard/report UI work

## Inputs

- Batch YAML:
  `experiments/configs/mvp/batches/phase14b_altitude_35m.yaml`
- Batch run:
  `experiments/batches/20260721_151916_phase14b_altitude_35m`

## Implementation

Added the missing Phase 14b batch YAML. The key fix is that the batch path now
uses:

```text
hover_s: 90
post_loss_hover_s: 50
```

This prevents the earlier raw-run mistake where `auto_takeoff_land_pxh_truth.py`
defaulted to `--hover-s 25` and only recorded a short post-loss window. The
batch also sets per-case failsafe profiles explicitly so stock/no-aid keep
`delayed_observation` while LK/SIFT keep `default_px4`.

Two local runtime issues were fixed before and during the batch:

- Freed disk by removing regenerated `extracted_csv` folders and old system
  crash/journal files. Raw ULogs, truth logs, reports, and run folders were
  preserved.
- Fixed PX4 SITL logger permissions:
  `/opt/sim_px4/PX4-Autopilot/build/px4_sitl_default/rootfs/log` had a
  root-owned `2026-07-21` folder, causing `ERROR [logger] Can't open log file`.
  The tree was restored to `px4:px4`.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14b_altitude_35m.yaml --dry-run

sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase14b_altitude_35m.yaml --continue-on-fail"
```

Rejected-but-valid runs were postprocessed manually to get full-window
Gazebo-truth metrics:

```bash
sudo -u px4 bash -c "cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
export MPLCONFIGDIR=/tmp/databoss-matplotlib-px4
venv/bin/python scripts/runner/summarize_batch_metrics.py \
  --batch-dir experiments/batches/20260721_151916_phase14b_altitude_35m"
```

## Results

| Case | Accepted | GNSS loss verified | Horizontal err max (m) | Height err max (m) | Truth drift end (m) | Notes |
|---|---:|---:|---:|---:|---:|---|
| LK loss | false | true | 20.84 | 13.47 | 64.30 | Full 50 s window; failed range/flight validation after divergence |
| SIFT loss | true | true | 1.71 | 0.90 | 10.64 | Accepted end-to-end |
| Stock loss r1 | false | true | 55.97 | 7.32 | 60.64 | Full 50 s window; failed range/flight validation |
| Stock loss r2 | false | false | n/a | n/a | n/a | GPS-drop flake; fix stayed valid |
| LK GNSS-on | false | n/a | 1.10 | 0.98 | 0.04 | Good truth alignment, but flight validation rejected after failsafe/landing timing |
| No aid loss | false | true | 82.56 | 1.03 | 82.44 | Expected divergent baseline |

Batch summary:
`experiments/batches/20260721_151916_phase14b_altitude_35m/batch_summary.md`

Batch metrics:
`experiments/batches/20260721_151916_phase14b_altitude_35m/batch_metrics.md`

Final comparison report:
`experiments/comparisons/20260721_phase14b_altitude_35m/report.md`

### Stock rep2 rerun

The original stock rep2 data was preserved. A single-case rerun was launched
with `--only stock_gnssloss_35m_rep2`:

`experiments/batches/20260721_192017_phase14b_altitude_35m`

New run:

`experiments/runs/20260721_192020_phase14b_stock_gnssloss_off50s_flat_rural_phototex_noon_alt35m_pxh_takeoff_land_truth`

This rerun fixed the GPS-drop flake (`gnss_loss_verified=True`,
`fix_type=0.0`) and completed the 50 s post-loss window, but still rejected:
max horizontal error `40.89 m`, max 3D error `40.91 m`, truth drift end
`35.92 m`, and rangefinder/height agreement failed by `6.21 m`.

### SIFT rep2 rerun

To verify the accepted SIFT result was repeatable, a second single-case SIFT
replicate was launched with `--only sift_xy_gnssloss_35m`:

`experiments/batches/20260721_193527_phase14b_altitude_35m`

New run:

`experiments/runs/20260721_193531_phase14b_sift_xy_gnssloss_off50s_flat_rural_phototex_noon_alt35m_pxh_takeoff_land_truth`

Result: **Accepted**. GNSS loss was verified (`fix_type=0.0`), the 50 s
post-loss window completed, the ULog flight gate passed, and the distance
sensor gate passed with height/range disagreement of only `0.11 m`.

Full-window Gazebo-truth metrics:

- Truth drift end: `10.254 m`
- Horizontal error mean/max: `0.539414 m` / `1.469144 m`
- Max 3D error: `1.509481 m`
- ULog airborne duration: `71.34 s`

The separate sensor-contract timing report still marks its own timing gate
false because the recorded camera-frame rate in the scene window was only
`1.91 Hz`. That does not override the end-to-end acceptance, but it remains a
sensor-recording limitation to track.

## Interpretation

Phase 14b is rejected as a matrix because only SIFT passed the full acceptance
path. The result still proves the timing fix: valid GNSS-loss cases ran the
full requested 50 s post-loss window with `gnss_loss_verified=True`. The
original stock rep2 correctly failed loud as the known GPS-drop flake; its
single-case rerun fixed the GPS flake but still rejected on drift/range gates.

At 35 m on this flat world:

- SIFT remains bounded under genuine GNSS loss in two observed runs.
- LK no longer matches its 15 m performance and diverges late in the outage.
- Stock flow is not reliable at this rung in the observed replicates.
- The no-aid baseline diverges strongly, as expected.
- The LK GNSS-on reference stayed close to truth, but the runner rejected the
  case after a failsafe/landing timing path; interpret its metrics as reference
  evidence, not an accepted case.

## Known limitations

- Rejected flight-step cases only received postprocess/alignment after the
  runner returned. Their metrics are valid Gazebo-truth measurements, but their
  end-to-end status remains rejected.
- The original stock rep 2 from the full batch is not interpretable as
  GNSS-denied performance because GNSS loss was not verified. The later
  single-case rerun is interpretable and rejected on drift/range gates.
- The SIFT rep2 sensor-contract timing report rejected its camera-frame timing
  gate even though the end-to-end run accepted; use the truth-alignment and
  flight status as the performance result, and track camera capture timing as a
  separate instrumentation issue.
- The no-loss hover wait could take a long wall-time timeout after an early
  failsafe landing; this affected the LK GNSS-on case. The runner was patched
  after the batch so future runs fail immediately once early landing is observed
  after becoming airborne.
- Disk remains tight after the run; future batches should start with at least
  several GB free.

## Files created or modified

- `experiments/configs/mvp/batches/phase14b_altitude_35m.yaml`
- `experiments/batches/20260721_151916_phase14b_altitude_35m/`
- `experiments/batches/20260721_192017_phase14b_altitude_35m/`
- `experiments/batches/20260721_193527_phase14b_altitude_35m/`
- `experiments/comparisons/20260721_phase14b_altitude_35m/`
- Six run folders under `experiments/runs/20260721_*phase14b*alt35m*`
- `scripts/runner/auto_takeoff_land_pxh_truth.py`
- This document
- `docs/PROJECT_LOG.md`
- `docs/phases/README.md`

## Next phase

Decide whether to tune LK at 35 m or move the difficulty roadmap forward with
SIFT as the only accepted 35 m aided candidate.
