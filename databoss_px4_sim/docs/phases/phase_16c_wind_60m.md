# Phase 16c — Wind at 60 m Altitude

Status: **Accepted with limitations** (2026-07-24).

## Goal

Third and final rung of the Phase 16 wind roadmap: same LK/SIFT/unaided
x 2/5 m/s crosswind matrix as Phase 16a/16b, at 60 m AGL on the larger
`flat_rural_phototex_600m_noon` world -- the same world used by the
accepted, wind-off Phase 14c.

## In scope / Out of scope

Same as Phase 16a (see that doc) -- this batch changes only the altitude
and world size (240m -> 600m field, to give the 60 m route enough room).

## Implementation

Batch config: `experiments/configs/mvp/batches/phase16c_wind_60m.yaml`,
run via `run_batch_matrix_pxh.py --continue-on-fail`
(batch dir `experiments/batches/20260724_083213_phase16c_wind_60m`).
`startup_timeout_s` raised to 260 (vs 220 for 16a/16b) for the larger
world's known longer startup.

## Commands

```bash
cd /opt/databoss_px4_sim
python3 -u scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase16c_wind_60m.yaml --continue-on-fail
```
(run as user `px4`, QGC kept live throughout)

## Expected outputs

Same shape as Phase 16a/16b. Comparison report:
`experiments/comparisons/20260724_phase16c_wind_60m/report.md`.

## Acceptance criteria

Same as Phase 16a/16b.

## Results

6/6 cases flew cleanly (`ULog flight OK: True`, no `AUTO_TAKEOFF` stall,
reaching 60 m). Runner-level batch gate: `cases_run=6, accepted_count=0,
failed_count=6` -- all 6 hit the pre-existing rangefinder-tolerance gate
(expected to bite harder at 60 m, where the rangefinder-vs-height gap is
larger than at 15/35 m); `postprocess`/`align` run manually for all 6.

| Case | 2 m/s H mean / max | 5 m/s H mean / max |
|---|---|---|
| LK (GNSS-on reference) | 0.12 m / 0.48 m | 0.18 m / 1.07 m |
| SIFT (GNSS-loss, aided) | 3.79 m / 18.12 m | 7.92 m / 32.45 m |
| Unaided (GNSS-loss) | 14.36 m / 124.60 m | 11.06 m / 87.63 m |

Same ordering as 16a/16b (GNSS-on tightest, SIFT middle, unaided worst).
Per-run anomaly check: both SIFT GNSS-loss cases (2 m/s and 5 m/s) flagged
`unusual=True` by the climb-phase/persistent height-transient detector
(added during Phase 16b) -- peaks of 30.0 m (t=80.6s, residual 28.7 m) and
20.2 m (t=93.9s, residual 10.6 m) respectively. Both co-occur with an
already-explained `nav_state` departure from `OFFBOARD` (`AUTO_LAND` and
`DESCEND` respectively) driven by the offboard-signal-loss failsafe: once
that trips, the vehicle is actually descending under its own autopilot
logic, so truth height legitimately diverges from the pre-failsafe
tracking reference and the comparison window ends mid-descent rather than
settled. Consistent with the same pattern already documented in 16a/16b,
not a new failure mode. The other 4 cases (both LK, both unaided) came
back `unusual=False` (unaided cases show only the expected `info:`
GNSS-loss-drift and `AUTO_RTL` notes).

## Interpretation

Same conclusion as 16a/16b, holding at the full 60 m altitude: GNSS-on
stays tight regardless of wind, SIFT aiding bounds drift with
wind-dependent growth, unaided dead-reckons badly (route overlay shows
large loops, up to ~100m displacement). The Phase 16 wind roadmap's core
question -- does the LK/SIFT stack still hold under a steady crosswind --
is answered consistently across all three altitudes: yes, with the same
wind-dependent drift-growth pattern at every altitude tested.

## Known limitations

- Same pipeline-gate limitations as 16a/16b, more pronounced here (0/6
  cleared the runner's own rangefinder-tolerance gate, vs 1/6 at 15m/35m).
- Same climb-phase/persistent height-transient class of behavior as 16b,
  here manifesting as a persistent (not self-correcting) divergence tied
  to post-failsafe descent rather than a climb-phase transient -- both
  are instances of the same underlying detector, distinguished by whether
  the error settles.

## Files created or modified

- `experiments/configs/mvp/scenarios/*wind{2,5}ms*60m*`,
  `experiments/configs/mvp/worlds/flat_rural_phototex_600m_noon_wind{2,5}ms.yaml`
  (already existed from the initial batch build-out)
- `experiments/comparisons/20260724_phase16c_wind_60m/`

## Next phase

None planned yet -- this completes the 3-batch Phase 16 wind roadmap
(15 m / 35 m / 60 m, both at 2 m/s and 5 m/s). A natural follow-up (not
in scope here) would be a second wind direction or gusty/turbulent wind,
both explicitly deferred by the world builder's `gusts_enabled`
hard-stop; or characterizing the `AUTO_TAKEOFF` stall / climb-phase
height-transient mechanisms at the PX4 source level, deferred throughout
this phase in favor of empirical wind-speed selection.
