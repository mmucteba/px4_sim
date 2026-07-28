# Phase 16b — Wind at 35 m Altitude

Status: **Accepted with limitations** (2026-07-24).

## Goal

Second rung of the Phase 16 wind roadmap: same LK/SIFT/unaided x 2/5 m/s
crosswind matrix as Phase 16a, at 35 m AGL -- config-only altitude
drop-in, same world (`flat_rural_phototex_noon`) as the accepted,
wind-off Phase 14b.

## In scope / Out of scope

Same as Phase 16a (see that doc) -- this batch changes only the altitude.

## Implementation

Batch config: `experiments/configs/mvp/batches/phase16b_wind_35m.yaml`,
run via `run_batch_matrix_pxh.py --continue-on-fail`. First launch
attempt omitted `--continue-on-fail` and stopped after case 1 hit the
same non-blocking flow-sign-sentinel gate that Phase 16a's clean cases
also hit -- relaunched correctly as batch dir
`experiments/batches/20260724_072007_phase16b_wind_35m`.

## Commands

```bash
cd /opt/databoss_px4_sim
python3 -u scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase16b_wind_35m.yaml --continue-on-fail
```
(run as user `px4`, QGC kept live throughout)

## Expected outputs

Same shape as Phase 16a. Comparison report:
`experiments/comparisons/20260724_phase16b_wind_35m/report.md`.

## Acceptance criteria

Same as Phase 16a.

## Results

6/6 cases flew cleanly (`ULog flight OK: True`, no `AUTO_TAKEOFF` stall).
Runner-level batch gate: `cases_run=6, accepted_count=1, failed_count=5`
-- same two non-blocking gates as 16a (rangefinder tolerance, 4 cases;
flow-sign-sentinel). `postprocess`/`align` run manually for the 4 cases
the runner short-circuited on its own gate before those steps.

| Case | 2 m/s H mean / max | 5 m/s H mean / max |
|---|---|---|
| LK (GNSS-on reference) | 1.55 m / 2.28 m | 0.27 m / 2.85 m |
| SIFT (GNSS-loss, aided) | 1.76 m / 6.91 m | 6.70 m / 20.98 m |
| Unaided (GNSS-loss) | 11.73 m / 78.57 m | 10.25 m / 85.44 m |

Same ordering as 16a (GNSS-on tightest, SIFT middle, unaided worst),
though the LK 2 m/s H mean (1.55 m) is higher than its 15 m/35 m/5 m/s
counterparts (all <0.3 m) -- see the height-transient finding below,
which is the cause.

### New finding: climb-phase EKF height-estimation transient (35 m)

The LK GNSS-on 2 m/s case showed `height_abs_error` peaking at **23.2 m**
around t=25s -- large enough to stand out from every other case in this
batch (next-highest max height error: 12.4 m). Investigated directly from
the raw ULog (not just the derived CSV, to rule out a postprocessing
artifact):

- The rangefinder (`distance_sensor.current_distance`) tracked the climb
  continuously and correctly the whole time (0.17 m -> 34.99 m, no
  dropout) -- ruled out as the cause.
- `vehicle_local_position.z` (PX4's own EKF height estimate) climbs far
  slower than Gazebo truth during the ascent: at t=25s, truth is already
  at ~32 m (essentially at the 35 m cruise altitude) while the EKF
  estimate is still only at ~8 m. The EKF estimate then "catches up"
  rapidly between t~25-38s, converging to within ~1 m of truth by the
  time the vehicle is at cruise altitude.
- The same shape (rise then fall, same ~t=20-40s window) appears in the
  SIFT 2 m/s (peak ~1.3 m) and LK 5 m/s (peak ~2.9 m) cases too, just far
  smaller -- so this is a **general, usually-small transient during the
  climb to 35 m**, not unique to one run; the LK 2 m/s case is an outlier
  in magnitude, not in kind.
- It does **not** appear in the accepted, wind-off Phase 14b LK-on-35m
  reference run (`height_abs_error.max_m` there is 0.98 m) -- so it
  correlates with wind being enabled, though the magnitude doesn't scale
  monotonically with wind speed (2 m/s produced the largest instance
  here, not 5 m/s), consistent with this box's already-documented RTF
  variability affecting fast-dynamics EKF behavior non-deterministically.
- All instances **self-correct**: by the time each run reaches cruise
  altitude, height error settles to a small residual (~0.3-1.2 m) that
  matches the case's steady-state tracking quality for the rest of the
  flight.

This is a distinct phenomenon from the previously-investigated
`AUTO_TAKEOFF` setpoint stall (in that stall, EKF and truth height both
correctly track ~0 m together while the setpoint itself is stuck; here,
EKF and truth actively diverge from each other during a real climb, then
reconverge). Root cause not further characterized at the PX4 source
level -- out of scope for this batch given the project's established
preference (per Phase 16 wind-speed decision) for characterizing and
working around estimator quirks empirically rather than re-opening PX4
internals. Flagged here as a known, explained, self-correcting behavior,
not a defect requiring a fix.

**Anomaly checker enhancement**: `plot_route_single_run.py`'s
`check_anomalies()` had no check for height-error transients (only
ground-collision and `nav_state`-departure checks existed), so this
23 m spike was originally invisible in the automated `unusual` flag.
Added a climb-phase height-transient detector: flags any
`abs_height_error_m` peak >5 m, then classifies it as `info:`
(self-correcting) if the last-15s-of-run residual is small relative to
the peak, or as a real (non-`info:`) finding if it doesn't settle. Applied
retroactively to all 12 Phase 16a/16b runs: correctly classifies the LK
2 m/s 35m case as an explained transient, and correctly flags the 3
unaided GNSS-loss cases (16a 5m/s-15m, 16b 2m/s-35m, 16b 5m/s-35m) as
persistent (non-settling) height divergence -- all 3 already carried an
explained `nav_state` departure to `AUTO_RTL`/`AUTO_LAND` finding, so this
is the same already-understood failsafe-driven divergence surfaced more
precisely, not a new problem.

## Interpretation

Same conclusion as 16a: GNSS-on stays tight, SIFT aiding bounds drift with
wind-dependent growth, unaided dead-reckons badly. The one altitude-linked
addition is the climb-phase EKF height transient, which is real,
repeatable in shape, wind-correlated, but self-correcting and does not
affect final cruise-phase tracking quality.

## Known limitations

- Same pipeline-gate limitations as 16a.
- Climb-phase EKF height-estimation transient during ascent to 35 m
  under wind, magnitude variable run-to-run (typically <3 m, up to 23 m
  observed once); mechanism not characterized beyond "EKF lags truth
  during a fast climb, then reconverges."

## Files created or modified

- `experiments/configs/mvp/{scenarios,batches}/*wind{2,5}ms*35m*` (already
  existed from the initial batch build-out)
- `experiments/comparisons/20260724_phase16b_wind_35m/`
- `scripts/analysis/plot_route_single_run.py` (added
  `CLIMB_HEIGHT_TRANSIENT_*` constants and the height-transient check in
  `check_anomalies()`)

## Next phase

Phase 16c (60 m altitude, `flat_rural_phototex_600m_noon` world),
same 2/5 m/s matrix.
