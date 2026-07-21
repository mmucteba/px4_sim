# Phase 11 — Three-way flow comparison (LK vs SIFT vs stock)

Status: **Accepted with limitations** (2026-07-20, corrected). All three
cases — LK, SIFT, and PX4 stock flow — passed individually under matched
conditions (same world, route, ~50 s GNSS-off duration). **Correction:**
the stock comparator originally accepted here (`115202`) was found to have
never actually lost GPS — `param set SIM_GPS_USED 0` was sent and
acknowledged by PX4, but `vehicle_gps_position` kept publishing a healthy
fix (`fix_type=3`, `satellites_used=10`) for the entire flight. That run
was a GNSS-available result mislabeled as GNSS-denied. It has been replaced
with `122327`, a rerun on the identical scenario where `vehicle_gps_position`
was verified directly from the ULog to actually drop. See Results and Known
limitations before treating stock's numbers as fully settled.

## Goal

Produce three clean runs — LK, SIFT, and PX4 stock optical flow — that are
each individually free of algorithm-pipeline faults, changing only the
estimator between them, so the resulting performance comparison is actually
trustworthy. This is the comparison every prior attempt (Phase 8J, Phase 8M)
tried to make before the underlying algorithms were proven sound, which is
why their numbers don't hold up under inspection.

## Why this phase exists

Phase 8J and Phase 8M both produced "LK vs stock" and "SIFT vs stock"
numbers, but neither compared apples to apples: the LK contract used in
both was later found loop-prone (Gate 6b) and then sign-inverted (Phase 8N).
A comparison against a known-broken estimator config isn't a performance
comparison, it's a bug report. Phase 11 exists to run the comparison for
real, once Phase 10 has a repaired LK contract.

## In scope

- One reference world: `flat_rural_phototex_noon` (the established primary
  texture world for flow work).
- One route, one GNSS-loss timing/duration, held identical across all three
  cases — only the estimator (`lk` / `sift` / PX4 stock `gz_x500_flow`)
  changes.
- LK using Phase 10's repaired, GNSS-loss-proven contract.
- SIFT re-verified under the exact same conditions (not reused from an
  older run on a different route/timing).
- Stock flow as the already-proven reference (Phase 8J: bounded 3/3 under
  GNSS-on and short/50 s GNSS-loss).
- Each case must individually pass Phase 10's acceptance criteria
  (straightness, fused fraction, rejection count, bounded EKF-vs-truth
  error) before being used in the comparison — a case that merely
  "completed the observation window" does not qualify (this is the exact
  mistake Phase 8M's acceptance made).

## Out of scope

- Multi-world / multi-condition matrix — explicit follow-on once this
  single-world comparison is accepted.
- Any new estimator algorithm.

## Inputs

- Phase 10's repaired LK contract and acceptance evidence.
- `docs/phases/phase_08j_stock_flow_benchmark.md` (stock flow reference
  numbers, unaffected by the axis/sign issue).
- `docs/phases/phase_08i_gnss_denied_flow_comparison.md` (SIFT's known
  ~35 s duration limit, to be reconfirmed under matched conditions here).

## Implementation

Phase 10 produced the bounded LK config, so the Phase 11 candidate set was
generated. Scenarios held world/route/estimator-neutral parameters
constant where it made sense, but each estimator kept its own natural
tuning where forcing identical values would have been a miscalibration,
not a control: LK uses `axis_map: "xy"`, `EKF2_OF_N_MIN=0.3`,
`EKF2_OF_DELAY=140` (Phase 10's proven contract); SIFT uses the same
sign-corrected `axis_map: "xy"` (the sign bug is a bridge-transform issue,
not LK-specific) but its own established `EKF2_OF_DELAY=111` and default
`EKF2_OF_N_MIN`; stock flow doesn't go through the DATABOSS bridge at all,
so it kept its own Phase 8J-proven `stock_flow:` config untouched.

- LK reference: `20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_...`
  (Phase 10 Run D, accepted).
- SIFT candidate: `20260720_111755_phase11_sift_xy_gnssloss_off50s_...`
  (accepted, first attempt).
- Stock candidate: `20260720_122327_phase11_stock_gnssloss_off50s_...`
  (accepted, fourth attempt — see Known limitations. Confirmed genuinely
  GNSS-denied by reading `vehicle_gps_position` directly from the ULog:
  `fix_type`/`satellites_used` drop to `0` at t≈22.4 s and stay down for
  the rest of the flight).

Three earlier stock attempts are preserved as evidence, not used as the
comparator: `20260720_112920_...` (completed, rejected — GPS genuinely cut
at t≈19.9 s, `mc_pos_control` failsafe fired 8 times, optical-flow quality
collapsed), `20260720_113659_...` (killed mid-flight, same failsafe pattern
observed live, no ULog), and `20260720_115202_...` (completed, looked clean
at the time and was briefly accepted, but **GPS never actually dropped** —
`vehicle_gps_position` stayed `fix_type=3`/`satellites_used=10` for the
entire flight despite `SIM_GPS_USED 0` being sent and acknowledged by PX4.
Its tight numbers reflected a GNSS-available flight, not a GNSS-denied one,
and it was reclassified as invalid once this was caught).

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
MPLCONFIGDIR=/tmp/databoss-matplotlib \
  venv/bin/python scripts/analysis/plot_phase11_three_way_flow_comparison.py
```

## Expected outputs

Three run folders (LK, SIFT, stock), each passing acceptance individually,
plus a comparison report under `experiments/comparisons/` with every number
traceable to a run ID (matching the traceability rule Phase 12 will need).

## Acceptance criteria

- All three cases individually pass Phase 10's bounded-flight criteria.
- One-variable control: world, route, GNSS-loss timing/duration identical
  across all three; only the estimator differs.
- Comparison report cites every run folder by ID for every number reported.

## Results

Comparison artifacts generated under:

`experiments/comparisons/20260720_phase11_three_way_flow_comparison/`

Key metrics, all three Accepted:

| Case | Vehicle | Max horizontal error | Max height error | Flow fused/rejected | `cs_opt_flow` fraction | Truth straightness | Truth path/end |
|---|---|---:|---:|---:|---:|---:|---:|
| LK fixed | `gz_x500_cam_lidar_down` | `1.093 m` | `0.377 m` | `1595 / 0` | `0.731` | `0.974` | `10.20 m / 9.94 m` |
| SIFT xy | `gz_x500_cam_lidar_down` | `1.214 m` | `0.389 m` | `804 / 0` | `0.762` | `0.986` | `9.78 m / 9.64 m` |
| Stock | `gz_x500_flow` | `1.347 m` | `0.628 m` | `2839 / 0` | `0.705` | `0.973` | `12.08 m / 11.76 m` |

(Stock numbers corrected 2026-07-20 — see the status banner above. The
previous table showed `115202`'s `0.188 m` / `0.259 m`, which was a
GNSS-available flight, not a GNSS-denied one.)

Generated plots include route overlays/panels, horizontal and height error,
PX4 height vs Gazebo truth, Gazebo route progress, GNSS fix/satellite
validity, GNSS `eph/epv`, optical-flow fusion fraction, aid-source sample
rate, innovation test ratio, flow quality, EKF control-status flags,
distance-sensor current distance, and summary metric bars.

## Interpretation

All three estimators, once given a correctly-configured contract, hold a
bounded, straight GNSS-denied route over a full 50 s outage — the actual
target result this phase and Phase 10 exist to establish. Zero rejected
optical-flow aid samples across all three cases.

**SIFT did not fail this time.** Every prior SIFT GNSS-loss result (Phase
8I: diverges 4/4 at 50 s) used the sign-inverted `axis_map: "-yx"` bridge
contract. Given the same sign-correction that fixed LK (Phase 8N/10) applies
to SIFT too — it's a bridge coordinate-transform bug, not an LK-specific
one — the corrected SIFT run here cleanly fused `804 / 0` aid samples and
stayed bounded (`1.214 m` max horizontal error). This strongly suggests
SIFT's documented divergence was also primarily a sign-inversion casualty,
not a SIFT-specific loop-instability problem as previously believed. Worth
a dedicated Phase 8I re-verification, since this reopens a finding that was
treated as settled.

**Stock flow is not the tightest of the three on genuinely GNSS-denied
data.** The first accepted stock run (`115202`, `0.188 m` max horizontal
error) looked like the best of the three, but it was later found to have
never actually lost GPS — see Known limitations. The corrected comparator
(`122327`), verified to genuinely lose GPS at t≈22.4 s, is bounded but the
*loosest* of the three on this single run: `1.347 m` max horizontal error
and `0.628 m` max height error, vs. LK's `1.093 m`/`0.377 m` and SIFT's
`1.214 m`/`0.389 m`. This should not be read as "stock is worse" without
more replicates — n=1 for stock's valid result vs. LK's n=3 — but it does
mean the original "stock is tightest, most mature path" narrative was an
artifact of an invalid test, not a real finding.

**A real, unresolved intermittent failure surfaced during stock's
testing**, independent of the above result — see Known limitations. One of
the two earlier "failsafe" attempts (`112920`) is now confirmed to have
genuinely lost GPS before failing, so that failsafe is real evidence of a
stock GNSS-loss failure mode, not just a resource-pressure artifact as
previously hypothesized. Whether stock reliably survives GNSS loss (as in
`122327`) or reliably fails (as in `112920`) is still open — this is now
the primary open question for stock flow, ahead of the comparison numbers
themselves.

## Known limitations

- **`SIM_GPS_USED 0` can be sent and acknowledged by PX4 without actually
  cutting the simulated GPS fix** — this is the central finding of this
  phase's correction. In run `115202`, the console log shows PX4 accepted
  the param change (`SIM_GPS_USED: curr: 10 -> new: 0`) at the scheduled
  time, and the runner's own `gnss_loss_detected`/`gnss_loss_ok` status
  flags both reported `True` — but `vehicle_gps_position` kept publishing
  `fix_type=3`, `satellites_used=10` for the entire 70 s flight when read
  directly from the ULog. The run was accepted as a clean GNSS-denied
  comparator on the strength of the status-file flags alone, which was a
  methodology gap: **the status-file's `gnss_loss_detected`/`gnss_loss_ok`
  flags are not sufficient evidence that GPS was actually lost** — they
  should be treated as "the command was sent," not "the fix actually
  dropped." `vehicle_gps_position.fix_type`/`.satellites_used` must be
  checked directly against the ULog before trusting any GNSS-loss run's
  numbers. LK's 3 runs and SIFT's 1 run were all independently re-verified
  this way and genuinely lost GPS; only the original stock comparator was
  affected. Root cause of why the param change didn't propagate to the GPS
  driver on that one launch is **not isolated** — retrying the identical
  scenario twice more reproduced a GPS fix that dropped correctly both
  times (`112920` at t≈19.9 s, `122327` at t≈22.4 s), so this looks like a
  rare launch-time race rather than a deterministic config issue, but it
  has only been seen/ruled out on this one scenario so far.
- **Stock flow hit an `mc_pos_control` failsafe on the one attempt
  (`112920`) confirmed to have genuinely lost GPS among the three earlier
  attempts**, unrelated to the axis/sign work: `Failsafe: stop and wait` /
  `Failsafe: blind descent` fired repeatedly, and `sensor_optical_flow`
  quality collapsed from a healthy ~215 mean (0% zero-quality) down to a
  degraded ~127 mean (50% zero-quality) partway through the flight — this
  pattern does not occur in the historical accepted Phase 8J stock
  benchmarks. The corrected comparator (`122327`) also genuinely lost GPS
  and did **not** hit this failsafe (flow quality zero-fraction only
  10.9%, no failsafe events). So stock's behavior under genuine GNSS loss
  is now confirmed inconsistent — bounded once, failed once — rather than
  "intermittent but usually fine" as previously framed. What was ruled out
  for the `112920` failure: scenario config, PX4 build hash, and prelaunch
  parameters are byte-identical to the passing attempts (verified by diff);
  GNSS timing/failsafe profile were changed once as a hypothesis fix but
  the failure recurred on the proven timing too, ruling that out; the world
  SDF file is unmodified since before the historical good runs. Resource
  pressure was the leading suspect but is now weaker evidence than before,
  since `112920`'s failure is now known to coincide with a genuine GPS
  loss rather than being an unexplained anomaly on an otherwise-identical
  run.
- LK and SIFT are each single candidate runs in this specific Phase 11
  matched-comparison shape; LK's own repeatability is separately proven at
  n=3 by Phase 10, but SIFT and stock are n=1 here (and stock's n=1 valid
  result sits alongside one confirmed-genuine GNSS-loss failure, so stock's
  true reliability is less settled than LK's or SIFT's).
- Per-run `ekf_vs_ground_truth_metrics.json` still reports `accepted=false`
  for skip-landing runs due to `comparison_window=until-land-command` and
  `land_command_not_found`; validation status plus ULog/truth-aligned
  metrics are the classification source here, not that JSON flag.
- GNSS timing remains an observed-vs-scheduled mismatch (same root cause
  Phase 10 documented): actual `vehicle_gps_position` validity drops a few
  seconds earlier than the status-file's scheduled timestamp in these
  ULogs.

## Files created or modified

- `experiments/configs/mvp/scenarios/phase11_sift_xy_gnssloss_off50s_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase11_stock_gnssloss_off50s_flat_rural_phototex_noon.yaml`
- `experiments/runs/20260720_111755_phase11_sift_xy_gnssloss_off50s_..._pxh_takeoff_land_truth/`
- `experiments/runs/20260720_112920_phase11_stock_gnssloss_off50s_..._pxh_takeoff_land_truth/`
  (rejected — genuine GPS loss, `mc_pos_control` failsafe — kept as evidence)
- `experiments/runs/20260720_115202_phase11_stock_gnssloss_off50s_..._pxh_takeoff_land_truth/`
  (**reclassified invalid** — GPS never actually dropped despite the loss
  command being sent/acknowledged; kept as evidence of the detection gap,
  no longer used as the comparator)
- `experiments/runs/20260720_122327_phase11_stock_gnssloss_off50s_..._pxh_takeoff_land_truth/`
  (accepted, the corrected comparator — GPS verified genuinely lost)
- `scripts/analysis/plot_phase11_three_way_flow_comparison.py` (updated to
  point at the verified `122327` stock run; interpretation text corrected
  to describe the GPS-not-cut finding and stock's inconsistent behavior)
- `experiments/comparisons/20260720_phase11_three_way_flow_comparison/`
  (regenerated with the corrected stock run)
- `docs/phases/phase_11_three_way_flow_comparison.md`
- `docs/phases/README.md`
- `docs/PROJECT_LOG.md`

## Next phase

Phase 11's core goal (a trustworthy one-variable comparison) is met, but
stock's leg needs replicates before the comparison is fully trustworthy.
Threads remaining before moving to the multi-world/condition matrix:

1. **Stock-flow replicate matrix under GNSS loss, with `vehicle_gps_position`
   verified directly from each ULog** (not just the status-file flags) —
   `122327` was bounded, `112920` failed, both genuinely GNSS-denied; n=2
   is not enough to know if stock is reliably bounded, reliably fails past
   some duration/condition, or genuinely flaky. This supersedes the old
   resource-pressure hypothesis as the framing — it's now an open
   pass/fail question, not just a quality-collapse mystery.
2. Consider hardening `auto_takeoff_land_pxh_truth.py`/
   `run_scenario_pxh_end_to_end.py` to verify `vehicle_gps_position`
   actually drops after the loss command, rather than trusting that the
   `param set` was acknowledged — this phase's `115202` mistake could
   recur silently on any future run.
3. Re-open Phase 8I's SIFT ~35 s duration-limit finding given this phase's
   evidence that the old result was likely contaminated by the same sign
   bug that affected LK — a SIFT-specific replicate matrix (short and 50 s
   outages, corrected `axis_map: "xy"`) would settle whether SIFT's real
   duration limit is actually longer than previously documented.

Then: multi-world/condition comparison matrix (unnumbered, future), Phase 12
(MVP comparison report), Phase 13 (dashboard, kept for later).
