# Phase 16a — Wind at 15 m Altitude

Status: **Accepted with limitations** (2026-07-23).

## Goal

First rung of the Phase 16 wind roadmap (see `phase_16_wind_roadmap.md`):
does the LK/SIFT GNSS-denied optical-flow stack, and the plain GNSS-on
reference case, hold up under a steady crosswind at 15 m AGL -- the same
altitude and world (`flat_rural_phototex_noon`) as the accepted,
wind-off Phase 14a.

## In scope

- 3-case matrix (LK GNSS-on reference, SIFT GNSS-loss aided, unaided
  GNSS-loss baseline) x 2 wind speeds (2 m/s, 5 m/s) = 6 runs.
- Physically-enabled steady crosswind via gz-sim's `WindEffects` plugin,
  blowing toward ENU north, perpendicular to the commanded vy=0.2 m/s
  East-bound flight path.
- Per-run truth-vs-EKF route plotting and anomaly checking
  (`plot_route_single_run.py`) on every flight.

## Out of scope

- Gusty/turbulent wind (`gusts_enabled` hard-stops in the world builder).
- Wind directions other than the one perpendicular crosswind case.
- Root-causing the `AUTO_TAKEOFF` setpoint stall at the PX4 source level
  (see roadmap doc -- extensively investigated, not resolved, worked
  around by wind-speed selection instead).

## Implementation

Wind speeds finalized as **2 m/s and 5 m/s** after a bisection at this
same altitude found 6/7 m/s unreliably (7 m/s consistently) triggering the
`AUTO_TAKEOFF` stall -- see roadmap doc "Wind speed selection" for the
full investigation and the two runner bugs fixed along the way
(takeoff-detection blind spot, GCS-connection-loss failsafe).

Batch config: `experiments/configs/mvp/batches/phase16a_wind_15m.yaml`,
run via `run_batch_matrix_pxh.py --continue-on-fail` (batch dir
`experiments/batches/20260723_190514_phase16a_wind_15m`).

## Commands

```bash
cd /opt/databoss_px4_sim
python3 -u scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase16a_wind_15m.yaml --continue-on-fail
```
(run as user `px4`, QGC kept live throughout -- no `--no-qgc`)

## Expected outputs

Six run folders under `experiments/runs/`, each with `validation.md`,
`ekf_vs_ground_truth_metrics.json`, `route_anomaly_check.json`, and
`plots/route_truth_vs_ekf.png` / `route_error_timeseries.png`. Comparison
report: `experiments/comparisons/20260723_phase16a_wind_15m/report.md`
(+ 6 cross-case plots, 6 per-run dashboards).

## Acceptance criteria

Flight-level: `ULog flight OK: True`, no `AUTO_TAKEOFF` stall, GNSS
on/off state independently verified against ULog
(`build_unified_comparison_report.py`'s `gps_guard` check). Route-level:
`plot_route_single_run.py`'s `unusual` flag, reviewed per case.

## Results

6/6 cases flew cleanly (`ULog flight OK: True` on every case, no stall).
Runner-level batch gate: `cases_run=6, accepted_count=1, failed_count=5`
-- the 5 "failed" are the pre-existing, non-blocking rangefinder-tolerance
gate (4 cases) and flow-velocity-sign-correlation sentinel (affects the
2 GNSS-on-with-optical-flow-recording cases too), not flight failures;
`postprocess`/`align` were run manually for the 4 cases the runner
short-circuited on its own gate before those steps, recovering full
truth-vs-EKF data for all 6.

Horizontal drift, GNSS-on reference vs SIFT-aided vs unaided
(`experiments/comparisons/20260723_phase16a_wind_15m/report.md`):

| Case | 2 m/s H mean / max | 5 m/s H mean / max |
|---|---|---|
| LK (GNSS-on reference) | 0.08 m / 0.20 m | 0.13 m / 0.70 m |
| SIFT (GNSS-loss, aided) | 1.69 m / 5.34 m | 8.97 m / 24.41 m |
| Unaided (GNSS-loss) | 21.10 m / 138.62 m | 20.18 m / 80.42 m |

Per-run anomaly check: 5/6 `unusual=False` (only the pre-existing
GNSS-loss-drift and offboard-signal-loss-RTL `info:` notes already
expected for the unaided/SIFT cases). The unaided 5 m/s case is flagged
`unusual=True` by the height-transient detector added during Phase 16b
(height error peaks at 29.8 m at t=64.3s and does not settle, end-of-run
residual 27.0 m) -- consistent with the same case's already-documented
`nav_state` departure to `AUTO_RTL`: once the failsafe trips, the vehicle
is actively climbing/repositioning toward home under its own autopilot
logic rather than holding the original commanded altitude, so the
comparison window catches it mid-maneuver rather than settled. Not a new
failure, just the same explained divergence surfaced more precisely.

## Interpretation

Clean, expected ordering: GNSS-on stays pinned near zero regardless of
wind; SIFT aiding holds drift to single/low-double digits but error grows
~5x from 2 to 5 m/s wind; unaided dead-reckons badly in both conditions
(the route overlay shows it looping, EKF position estimate freezing near
origin while truth is blown 80-140 m away) -- the expected no-aiding
failure mode, not a bug.

## Known limitations

- 5/6 cases fail the rangefinder-tolerance and/or flow-sign-sentinel
  gates (pre-existing, non-blocking, already characterized -- see
  roadmap and Phase 14 docs).
- `AUTO_TAKEOFF` stall root cause remains uncharacterized at the PX4
  source level; worked around via wind-speed selection (2/5 m/s), not
  fixed.

## Files created or modified

- `experiments/configs/mvp/{worlds,scenarios,batches}/*wind{2,5}ms*15m*`
- `experiments/comparisons/20260723_phase16a_wind_15m/` (manifest, report,
  plots, camera samples)
- `scripts/runner/auto_takeoff_land_pxh_truth.py` (takeoff-detection +
  OFFBOARD-confirmation fixes, shared across all phases)
- `scripts/analysis/plot_route_single_run.py` (new)

## Next phase

Phase 16b (35 m altitude), same 2/5 m/s matrix, config-only drop-in.
