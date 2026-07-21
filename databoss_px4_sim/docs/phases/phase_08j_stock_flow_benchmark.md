# Phase 8J — PX4 stock flow benchmark + DATABOSS flow upgrade study

**CONTRACT SUPERSEDED 2026-07-17 / 2026-07-20** — the live-LK runs here used
the pre-Gate-6b `axis_map: "-yx"` contract, later found sign-inverted
(Phase 8N found `axis_map: "xy"` is sign-correct). The stock-flow benchmark
numbers are unaffected (stock flow doesn't use this bridge/axis contract at
all) and remain the trustworthy reference; the LK numbers here should be
re-measured under Phase 10/11. See
`docs/phases/phase_08l_sensor_sanity_ladder.md` and
`docs/phases/phase_08n_flow_sign_inversion_probe.md`.

Status: **Stock short/50s benchmark complete; GNSS-on proof needs range-probe
rerun** (2026-07-16).

Phase 8I proved the DATABOSS SIFT optical-flow bridge is connected and useful
for a short outage, but marginal over a 50 s GNSS outage. Phase 8J compares it
against PX4's stock Gazebo optical-flow stack, then tests a bridge-side
Lucas-Kanade estimator on the exact same recorded frames before any live
GNSS-denied claim is made.

## Scope

Stage A is a flight-system comparison:

- World: `flat_rural_phototex_noon`.
- Vehicle: PX4 stock `gz_x500_flow`.
- Profile: same 2.5 m AGL slow +Y GNSS-loss profile used by Phase 8I Case D.
- GNSS starts enabled, then loss is the accepted runtime command
  `param set SIM_GPS_USED 0`.
- Evidence: ULog, Gazebo truth, range, `sensor_optical_flow`,
  `vehicle_optical_flow`, `estimator_aid_src_optical_flow`, EKF-vs-truth
  metrics, and the standard status JSON.

Stage B is an estimator comparison:

- Replays identical DATABOSS `flow_recording` frames.
- Compares current `sift` against new `lk`.
- Reports delivered samples, rate, compute time, quality, match/track
  coverage, and optional expected-flow error against Gazebo truth.

PX4 stock flow is treated as a reference simulation stack, not proof of a real
sensor. Gazebo truth remains the judge.

## Implementation

Runner support added:

- New `stock_flow:` scenario section enables PX4 stock flow parameters without
  starting the DATABOSS bridge.
- `px4_prelaunch_param_overrides()` now sets high-rate optical-flow logging
  when either bridge or stock flow is active.
- Stock-flow runs force `SYS_HAS_GPS=1`, `EKF2_GPS_CTRL=7`,
  `SIM_GZ_EN_FLOW=1`, `SIM_GZ_EN_LIDAR=1`, and `EKF2_OF_CTRL=1` at boot so
  the benchmark can start with GNSS and then prove a runtime outage.
- Runtime log commands record the stock flow/EKF parameter state in
  `pxh_takeoff_land_truth_status.json`.

New stock scenarios:

- `experiments/configs/mvp/scenarios/phase8j_stock_flow_gnsson_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase8j_stock_flow_short_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase8j_stock_flow_50s_flat_rural_phototex_noon.yaml`

Batch:

- `experiments/configs/mvp/batches/phase8j_stock_flow_benchmark.yaml`

New estimator:

- `src/databoss_sim/flow/lk_estimator.py`
- Selected by `flow_bridge.estimator: lk`.
- Uses OpenCV `goodFeaturesToTrack` + pyramidal
  `calcOpticalFlowPyrLK`.
- Maintains active tracks, replenishes features away from existing tracks, and
  rejects tracks using forward/backward consistency plus displacement
  consistency.
- Preserves the existing `FlowSample` contract and bridge output path:
  `OPTICAL_FLOW_RAD`, existing `axis_map`, quality rescale, range gates,
  prime behavior, and MAVLink format.

LK scenario:

- `experiments/configs/mvp/scenarios/phase8j_d_loss_flow_lk_flat_rural_phototex_noon.yaml`

Analysis scripts:

- `scripts/analysis/compare_flow_estimators_replay.py`
  writes `flow_estimator_comparison/` inside a run folder.
- `scripts/analysis/report_phase8j_stock_flow_benchmark.py`
  writes `experiments/comparisons/phase8j_stock_vs_databoss_flow/`.

## Commands

Dry-run validation:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8j_stock_flow_benchmark.yaml \
  --dry-run --continue-on-fail
```

Offline estimator replay on the accepted short-outage D run:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/analysis/compare_flow_estimators_replay.py \
  experiments/runs/20260714_170829_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth \
  --estimators sift lk --max-width 320 \
  --out-dir experiments/comparisons/phase8j_lk_replay_170829
```

Stock benchmark flights:

```bash
cd /opt/databoss_px4_sim || exit 1
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8j_stock_flow_benchmark.yaml \
  --continue-on-fail
```

Comparison report:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/analysis/report_phase8j_stock_flow_benchmark.py \
  --stock-short-run <stock_short_run_dir> \
  --stock-long-run <stock_50s_run_dir> \
  --databoss-short-run experiments/runs/20260714_170829_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth \
  --databoss-long-run <databoss_50s_run_dir>
```

## Acceptance criteria

- Stock-flow run has valid Gazebo truth alignment.
- GNSS loss is proven with `SIM_GPS_USED=0` and GPS fusion stopping.
- `cs_opt_flow` is active during the outage.
- Report includes fused/rejected counts, rejected/fused ratio, flow sample
  rate, range health, XY reset count, horizontal/height truth error, and a
  divergence verdict.
- `lk` is only accepted for live GNSS-denied testing if offline replay improves
  or matches SIFT accuracy while increasing delivered rate/coverage.

## Current baseline references

- DATABOSS SIFT short-outage anchor:
  `experiments/runs/20260714_170829_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- Known long-outage behavior: Phase 8I 50 s D realizations diverged 4/4.
- Local PX4 stock sim reference: `gz_x500_flow` uses a 100x100 downward
  optical-flow camera at 50 Hz, HFOV `0.733038`, KLT/OpenCV flow, and a
  downward 50 Hz rangefinder.

## First Stage B replay result

Artifact:
`experiments/comparisons/phase8j_lk_replay_170829/summary.md`.

On the accepted short-outage DATABOSS SIFT run `20260714_170829`, replaying
the same 1397 recorded frames at `max_width=320` gave:

| Estimator | Valid samples | Valid fraction | Mean compute s | P95 compute s | Mean speed abs error m/s | P95 speed abs error m/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sift` | 1077 | 0.771 | 0.0389 | 0.0501 | 0.1596 | 0.3511 |
| `lk` | 1079 | 0.999 | 0.0142 | 0.0195 | 0.1653 | 0.3474 |

Interpretation: `lk` is a strong live-candidate on rate/coverage and roughly
matches SIFT by truth-derived speed error, but this is not yet flight
acceptance. It must still be flown only after the stock-flow Stage A benchmark
anchors the reference behavior.

## First Stage A stock-flow result

Batch:
`experiments/batches/20260716_072127_phase8j_stock_flow_benchmark/`.

The batch result is rejected overall because the GNSS-on proof run
`20260716_072130` had `rangefinder_probe_ok=False` from a live probe sample of
`inf`. That run still flew and logged valid ULog distance-sensor evidence
(`ulog_distance_sensor_ok=True`), but it should be rerun or relaxed only if a
separate GNSS-on fusion proof is required.

The two GNSS-loss benchmark rows accepted:

| Run | Window | Mean horiz err m | Max horiz err m | Mean height err m | Flow rows/rate | Fused/rejected | `cs_opt_flow` active | XY reset delta |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `20260716_072559` | short | 0.511 | 1.600 | 0.446 | 2594 / 50.02 Hz | 1878 / 0 | 0.667 | 3 |
| `20260716_072949` | 50 s | 0.709 | 1.798 | 0.205 | 3317 / 50.01 Hz | 2608 / 0 | 0.704 | 2 |

Comparison report:
`experiments/comparisons/phase8j_stock_vs_databoss_flow/report.md`.

Read: PX4 stock Gazebo flow is dramatically cleaner than our current SIFT
bridge on the same flat slow +Y benchmark: full-rate 50 Hz flow, zero optical
flow aid-source rejects, and bounded 50 s outage behavior. This supports the
Phase 8J direction: first improve bridge estimator/rate/coverage (`lk`), then
test live before touching broad EKF gate tuning.

## Fresh DATABOSS SIFT 50 s comparison row

Run:
`experiments/runs/20260716_074422_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth`.

This was the requested no-land-wait/killed evidence style: the run completed
the 50 s post-loss observation window, did not wait for landing, exited
rejected, and preserved ULog/truth/flow evidence. Manual postprocess and
full-window alignment succeeded.

| System | Window | Verdict | Mean/max horiz err m | Mean/max height err m | Flow rows/rate | Fused/rejected | Reject/fused | `cs_opt_flow` active |
| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |
| PX4 stock flow `20260716_072949` | 50 s | bounded | 0.709 / 1.798 | 0.205 / 0.879 | 3317 / 50.01 Hz | 2608 / 0 | 0.000 | 0.704 |
| DATABOSS SIFT `20260716_074422` | 50 s | diverged | 207.506 / 1479.910 | 14.083 / 186.728 | 586 / 10.52 Hz | 116 / 301 | 2.595 | 0.318 |

Why SIFT failed here: the bridge did not disappear, but it delivered far lower
rate/coverage than stock flow and EKF2 rejected more flow than it fused. Once
the loop bent, the vehicle left the good regime, distance sensing went invalid,
dead reckoning became active, and the estimator/controller ran away. That is
the same positive-feedback pattern seen in Phase 8I, now directly contrasted
against bounded stock PX4 flow.

## 3x 50 s replicate benchmark on the 240x240 world

Stock/SIFT batch:
`experiments/batches/20260716_081558_phase8j_stock_vs_sift_50s_replicates/`.

LK batch:
`experiments/batches/20260716_090906_phase8j_lk_50s_replicates/`.

Report:
`experiments/comparisons/phase8j_stock_vs_sift_50s_replicates/report.md`.

Command:

```bash
cd /opt/databoss_px4_sim || exit 1
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8j_stock_vs_sift_50s_replicates.yaml \
  --continue-on-fail

sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/analysis/report_phase8j_stock_vs_sift_replicates.py \
  --batch-dir experiments/batches/20260716_081558_phase8j_stock_vs_sift_50s_replicates \
  --batch-dir experiments/batches/20260716_090906_phase8j_lk_50s_replicates \
  --repair-missing
```

All six runs used the existing `flat_rural_phototex_noon` 240x240 m generated
world, 2.5 m AGL, slow +Y command, GNSS starting enabled, runtime outage via
`SIM_GPS_USED=0`, 50 s post-loss observation, and `skip_landing_command: true`.

Batch result was rejected overall by design because divergent SIFT runs fail
the flight acceptance gate, but all six evidence folders were preserved:

| System | Runs | Bounded | Diverged | Worst horiz max m | Mean sensor flow Hz | Reject/fused mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PX4 stock `gz_x500_flow` | 3 | 3 | 0 | 8.368 | 50.01 | 0.000 |
| DATABOSS current SIFT bridge | 3 | 1 | 2 | 724.171 | 11.76 | 1.332 |
| DATABOSS LK bridge | 3 | 0 | 3 | 132.604 | 13.99 | n/a |

Per-run read:

| Case | Verdict | Max horiz err m | Max height err m | Flow Hz | Fused/rejected | Range OK |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `stock_50s_rep1` (`081602`) | bounded | 1.900 | 0.972 | 50.01 | 2618 / 0 | yes |
| `stock_50s_rep2` (`082047`) | bounded | 8.368 | 5.223 | 50.01 | 2628 / 0 | yes |
| `stock_50s_rep3` (`082532`) | bounded, **but GPS never actually dropped** — invalid GNSS-loss evidence, see note below | 0.262 | 0.326 | 50.01 | 2631 / 0 | yes |

**2026-07-20 correction:** re-checked `vehicle_gps_position` directly from
each run's ULog (prompted by a similar finding in Phase 11). `rep1` and
`rep2` genuinely lost GPS (`fix_type`/`satellites_used` drop to `0` around
t≈16.5-16.8 s and stay down). `rep3` (`082532`) never lost GPS at all —
`fix_type=3`, `satellites_used=10` for all 2023 samples, despite the same
`SIM_GPS_USED 0` loss command being sent. So this table's "3/3 bounded"
claim is really **2/3 genuinely GNSS-denied and bounded, 1/3 a
GNSS-available flight mislabeled as GNSS-denied** — the same detection gap
documented in `docs/phases/phase_11_three_way_flow_comparison.md`
("Known limitations"). Stock flow's short/50 s GNSS-loss claim is not
fully retracted (2/3 real replicates are still bounded), but it is weaker
than "3/3" and has not been re-run to close the gap.
| `sift_50s_rep1` | diverged | 459.793 | 53.431 | 9.94 | 131 / 133 | no |
| `sift_50s_rep2` | bounded | 2.543 | 0.558 | 14.32 | 518 / 217 | yes |
| `sift_50s_rep3` | diverged | 724.171 | 81.251 | 11.03 | 112 / 287 | no |
| `lk_50s_rep1` | diverged | 132.604 | 98.427 | 13.89 | 0 / 0 | no |
| `lk_50s_rep2` | diverged | 82.110 | 82.410 | 13.98 | 0 / 0 | no |
| `lk_50s_rep3` | diverged | 96.772 | 46.472 | 14.09 | 0 / 0 | no |

Plots were added beside the report:
`experiments/comparisons/phase8j_stock_vs_sift_50s_replicates/plots/`.

The bridge rate diagnosis is now measured on three SIFT and three LK long
runs:

- Raw camera frames: `30.3 Hz` with `33 ms` median sim spacing in all bridge
  runs.
- Current bridge cap: `flow_bridge.rate_hz=20`, so `min_period=50 ms`.
- A 33 ms frame source gated by 50 ms cannot produce 20 Hz. It accepts every
  second frame, giving an expected valid-send cadence of `66 ms`, or
  `15.15 Hz`.
- The one bounded SIFT run delivered valid logical sends at `15.15 Hz`; the
  two divergent runs degraded below that after range/quality collapse.
- SIFT compute median was 72.8, 104.5, and 94.35 ms wall. With real-time
  factors around 0.088-0.116, that compute is about 26-29% of the wall budget
  per camera frame. It explains wall-clock crawl, not the 15.15 Hz sim-time
  alias.
- LK compute median was 52.45-56.7 ms wall, roughly half the wall-budget share
  of SIFT (13-15% versus 26-29%). LK therefore improved compute margin, but it
  did not fix the live flight.

Interpretation: PX4 stock flow is not physically real VIO, but it is a stable
simulation reference: 50 Hz flow, synchronized range, and zero flow aid-source
rejects across these repeats. Current SIFT is variance-heavy: it can complete a
50 s outage when range and feature quality remain healthy, but 2/3 repeats
diverged after late range/quality collapse. LK is **not accepted** as a live
fix yet: all three LK repeats diverged, and ULog evidence shows
`cs_opt_flow_active_fraction=0` with 0 fused / 0 rejected optical-flow
aid-source samples. That is a fusion/integration problem to debug before rate
or EKF tuning. The next narrow task is to compare LK MAVLink message fields,
quality scaling, integrated-flow magnitudes/signs, integration time, and range
coupling against the accepted SIFT row and the PX4 stock reference. Only after
LK fusion is active should we test `rate_hz: 40` or uncapped bridge delivery.

## LK fusion repair proof

Repair run:
`experiments/runs/20260716_111242_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`.

Repair report:
`experiments/comparisons/phase8j_lk_fusion_repair/report.md`.

What changed:

- `flow_mavlink_bridge.py` now sends zero-quality startup
  `OPTICAL_FLOW_RAD` primes immediately after MAVLink connection. This
  advertises `sensor_optical_flow` while PX4 is still disarmed, allowing the
  PX4 `sensors` module to create `VehicleOpticalFlow`.
- The runner now fails closed if the configured pre-arm MAVLink flow samples
  are not observed.
- LK now uses median/MAD displacement inliers and a maximum flow-rate sanity
  gate instead of mean/std raw track averaging.
- The LK scenario uses `rate_hz: 40` so the 30 Hz camera source is not aliased
  down to 15 Hz by a 20 Hz bridge gate.

Evidence from the accepted proof run:

| Measurement | Value |
| --- | ---: |
| `sensor_optical_flow` rows | 396 |
| `vehicle_optical_flow` rows | 395 |
| `estimator_aid_src_optical_flow` rows | 332 |
| Optical-flow fused / rejected | 150 / 0 |
| `cs_opt_flow` active fraction | 0.278 |
| Startup prime rows | 72 |
| Real LK sent rows | 150 |
| Sensor flow rate | 18.21 Hz |
| Sensor flow-rate p99 | 1.179 rad/s |

Conclusion: LK is no longer blocked at the PX4 integration layer. The previous
3x LK result remains valid as a record of the broken startup path, but it is
not the current LK implementation. The next required comparison is a fresh 3x
long stock/SIFT/fixed-LK matrix before claiming LK is flight-better than SIFT.
