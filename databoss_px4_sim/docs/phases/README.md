# DATABOSS Phase Roadmap

## Completed / proven

- Phase 0 — PX4/Gazebo environment proof.
- Phase 1 — GNSS ON baseline.
- Phase 2 — ULog extraction and plotting.
- Phase 3 — GNSS loss method discovery.
- Phase 3A — GNSS loss default failsafe.
- Phase 3B — GNSS loss with delayed failsafe.
- Phase 3C/3D — QGroundControl monitoring over Tailscale.
- Phase 4 — Gazebo ground truth discovery and PX4/Gazebo truth alignment.
- Phase 5 — Repository/MVP structure.
- Phase 6 — MVP world and route configuration format.
- Phase 7A — Automated scenario runner.
- Phase 7B — Batch execution.
- Phase 7C — Comparison reporting.
- Phase 7D — Environment-condition config matrix.
- Phase 8A — Ideal external-odometry aiding upper-bound proof.
- Phase 8B — Physical world generation and generated-world PX4 flight proof.
- Phase 8C — Downward monocular camera proof in generated worlds.
- Phase 14A — Altitude step 1 at 15 m, accepted.
- Phase 14B — Altitude step 2 at 35 m, rejected as a matrix; SIFT GNSS-loss
  accepted in two observed runs, LK/stock/no-aid rejected or flaky. Details:
  `phase_14b_altitude_35m.md`.

## Proven / trustworthy building blocks (added 2026-07-20)

**Proven** — safe to build on without re-litigating:
- Environment + world generation (Phase 0, 8B, 9A).
- GNSS-on baseline (Phase 1).
- `SIM_GPS_USED 0` as the GNSS-loss mechanism (Phase 3) —
  `SYS_FAILURE_EN`/`failure gps off` is rejected, it never made GPS unhealthy.
- ENU→NED frame conversion, corrected 2026-07-13 (`../architecture/frames_and_alignment.md`).
- Camera + TF03 downward sensor path (Phase 8C/8D/8E).
- Ideal external-odometry aiding upper bound, frozen (Phase 8A: 0.007–0.215 m
  station drift vs. 320 m unaided).
- Offline SIFT flow validation, hover-only (Phase 8F).
- Stock PX4 flow bounded under GNSS-on and short (≤50 s) GNSS-loss, 2/3
  genuinely GNSS-denied replicates verified bounded (Phase 8J; corrected
  2026-07-20 — 1 of the original "3/3" replicates never actually lost GPS,
  see the detection-gap note below) — unaffected by the LK axis/sign
  issues below.
- Optical-flow sign convention: `axis_map: "xy"` (Phase 8N) — the previously
  "accepted" `-x-y`/`-yx` contracts were GNSS-on workarounds around a
  sign-inverted flow measurement, not sign-correct contracts.
- LK flow bounded under GNSS loss with `axis_map: "xy"` and
  `EKF2_OF_N_MIN=0.3`, 3/3 (Phase 10) — accepted with limitations; Run D's
  GNSS-data plot shows the actual `vehicle_gps_position` validity drop
  happens earlier than the status-file scheduled-loss timestamp, so future
  timing claims must be judged from ULog GNSS data.

- Matched one-variable three-way comparison of LK vs SIFT vs stock flow
  under a full ~50 s GNSS-off duration, all three bounded with zero rejected
  flow samples (Phase 11, corrected 2026-07-20) — the first trustworthy
  version of this comparison; every prior attempt (Phase 8J, 8M) ran at
  least one estimator on a contract later found broken. Stock's leg was
  itself first accepted on a run where GPS never actually dropped (see
  below); corrected numbers show stock bounded but not tighter than LK/SIFT.
- **Status-file `gnss_loss_detected`/`gnss_loss_ok` flags are not proof GPS
  was actually lost** (found 2026-07-20, Phase 11) — PX4 can accept and
  acknowledge `param set SIM_GPS_USED 0` without the simulated GPS driver
  ever actually dropping the fix. Confirmed on 3 historical "accepted"
  runs so far (Phase 11's `115202`, Phase 8J's `stock_50s_rep3`/`082532`,
  and a Phase 12 unaided-baseline launch attempt caught and discarded
  before it reached any doc). Any GNSS-loss run's acceptance must be
  verified by reading `vehicle_gps_position.fix_type`/`.satellites_used`
  directly from the ULog, not by trusting the status file. Not yet audited
  beyond Phase 8J, 11, and 12 — other historical GNSS-loss runs (Phase 3,
  8I, 8M, etc.) have not been re-checked.
- **Unified comparison report system, manifest-driven** (Phase 12,
  2026-07-21) — `scripts/analysis/{comparison_manifest,
  build_unified_comparison_report, plot_unified_comparison}.py` generate a
  traceable Markdown report from any set of runs tagged by algorithm/GNSS
  state/world variant in a YAML manifest, with GPS state independently
  re-verified per case from the ULog (would have caught `115202`
  automatically). Adding a future world-lighting or sensor variant is a
  manifest edit, not a code change. First matrix: LK/SIFT/stock x
  GNSS-on/GNSS-loss plus a fully unaided GNSS-loss baseline —
  `docs/phases/phase_12_mvp_comparison_report.md`.
- **Optical-flow aiding reduces worst-case GNSS-loss position error by
  ~20-50x** (Phase 12) — a fully unaided GNSS-loss run (no flow bridge, no
  stock flow) diverged to `30.08 m` max horizontal error; LK/SIFT/stock
  under the same GNSS-loss timing stayed within `0.58-1.35 m`. First clean
  "before vs after" evidence for the project's core claim, same
  world/route/timing across all cases.

**Not yet proven / reopened**:
- SIFT beyond a ~35 s GNSS-loss outage (Phase 8I said diverges 4/4 at 50 s,
  but that was measured on the sign-inverted `-yx` contract — Phase 11's
  clean SIFT result on the corrected `xy` contract reopens this question,
  not yet re-verified at n>1).
- Stock flow's reliability under genuine GNSS loss — n=2 confirmed-genuine
  GNSS-denied stock attempts so far, one bounded (`122327`) and one hit a
  `mc_pos_control` failsafe (`112920`). Not enough replicates to know
  whether stock is reliably bounded, fails past some condition, or
  genuinely flaky; the earlier "intermittent, resource-pressure-suspected"
  framing is superseded now that the failing attempt is known to have
  genuinely lost GPS.

## Phase 9A — Real-terrain world import (2026-07-10)

Accepted. Real-world heightmap worlds from
gazebo_terrain_generator (Mapbox DEM + satellite texture) are proven as
DATABOSS experiment worlds: sample world (Joshimath) and operator-generated
world (Şereflikoçhisar) both flew accepted runs with truth alignment, and the
onboard downward camera sees the satellite texture through the ogre pipeline.
Native browser heightmap rendering is also proven when the terrain-only proxy
flags are enabled; `_web_mesh` colored tiles are emergency fallback only.
Import checklist and evidence: `phase_09a_real_terrain_world_import.md`.
Web-viewer live monitoring recipe: `../gazebo_web_visualization.md`.

## Phase 8D — TF03-style downward rangefinder proof (2026-07-10)

Accepted. `gz_x500_lidar_down` flew both generated flat worlds; the
single-point downward gpu_lidar reached PX4 as `distance_sensor` (2031/2205
ULog rows, range agrees with hover height within 0.15 m). Evidence:
`phase_08d_downward_rangefinder_proof.md`.

## Phase 8E — Combined camera + TF03 vehicle (2026-07-10)

Accepted. New DATABOSS vehicle `x500_cam_lidar_down` (airframe 4022) carries
both practical sensors; both publish in one run in both flat worlds. v1→v2
design lesson (lidar side-by-side with camera) recorded in
`phase_08e_combined_cam_lidar_vehicle.md`. QGC + web bridge are standing run
monitors from here on.

## Phase 8F — Offline optical-flow validation (2026-07-11)

Accepted with limitations. DATABOSS's own modular SIFT v1 flow estimator
validated offline against Gazebo truth on all three worlds (flat high/low
texture + serefli_koschisar terrain): speed error mean 0.017 / 0.033 / 0.022
m/s at hover (criterion < 0.15), texture-quality comparison in the expected
direction, terrain gives the best feature quality (58.7). Recordings ~7.6 Hz.
Hover-only; translation-route validation deferred to 8G/8H. Details in
`phase_08f_offline_flow_validation.md`.

## Phase 8G — Live modular optical-flow bridge (2026-07-13)

Accepted on the phototex primary world. DATABOSS's camera pipeline reached
PX4 through OPTICAL_FLOW_RAD, D5 was gated on two orthogonal open-loop legs
(`axis_map: -yx`), `EKF2_OF_DELAY=111` was baked into airframe 4022, and
GNSS-on closed-loop EKF2 optical-flow fusion passed in run `20260713_151744`
(`flow_fusion_ok=True`, 443 fused samples, 4.74% rejected/fused). Details:
`phase_08g_live_flow_bridge.md`.

## Current phase

Phase 8L — Full sensor sanity ladder before more flow tuning.

In progress (2026-07-16). Gate 1 static audit is saved, Gate 2 scene/range
hover proof passes, Gate 3 standalone camera/LiDAR attitude pose proof passes
all 13 level/roll/pitch/yaw poses, and Gate 4 four signed open-loop LK
translation legs pass with `axis_map: "-yx"`. Gate 5 yaw rotation/gyro sanity
also passes: the NaN baseline remains valid, and the Gazebo-IMU candidate
feeds finite same-window `OPTICAL_FLOW_RAD` gyro integrals with
`gyro_available_fraction=1.0`. Current accepted Gate 5 batch:
`experiments/batches/20260717_065827_phase8l_03_rotation_gyro_baseline`.
Limitation: the yaw-target proof includes position-hold lateral motion, so it
proves gyro transport, not pure rotation-only EKF improvement.

Gate 6 timing/delay sweep completed and was rejected as a route-control
blocker (all five delay candidates flew loops). Gate 6b (2026-07-17) then
found a GNSS-on workaround: `axis_map: "-x-y"` plus
`EKF2_OF_N_MIN=0.5` flew straight under GNSS because flow was de-weighted
relative to GNSS. Phase 8N supersedes the sign part of that conclusion:
the ULog flow-velocity sentinel proves `axis_map: "xy"` is the sign-correct
bridge candidate, while old `-x-y` is inverted after EKF2 ingestion.

A first GNSS-loss smoke on that config was rejected: with GNSS denied the
de-weighted flow could not anchor velocity, the controller amplified the
drift into a `~1.4 km` flyaway, flow self-destructed at speed, and the EKF
dead-reckoned `~1.7 km` in the mirrored direction (failsafe fired correctly
at `EKF2_NOAID_TOUT`). GNSS-denied aiding strength is the open problem.
Details: `phase_08l_sensor_sanity_ladder.md` and `docs/PROJECT_LOG.md`
(2026-07-17 entries, including the correction block).

Failsafe-isolation rerun `20260717_143624` applied
`delayed_observation` and `MPC_XY_VEL_MAX=2.0` to the old Gate 6b workaround
and was still rejected against Gazebo truth: max horizontal error
`2515.98 m`, Gazebo station displacement end `2535.97 m`, max height error
`203.96 m`, and flow rejection/fused ratio `0.2055`. This confirms default
failsafe was a confound, not the sole cause. The effective GNSS-loss timing
remained `10 s` after takeoff despite a `20 s` request.

Route-quality inspection on 2026-07-20 explains the remaining Phase 8M LK route
problem: the accepted LK-vs-stock batch used the older Phase 8K LK contract
(`axis_map: "-yx"`, no `EKF2_OF_N_MIN=0.5`) and therefore reproduced the known
loop-prone measurement/EKF/controller feedback path. Phase 8M LK traveled
`64.4 m` of truth path to end only `7.2 m` away (straightness `0.112`), while
stock flow traveled `9.8 m` to end `9.7 m` away (straightness `0.985`).
Inspection report:
`experiments/inspections/20260720_phase8m_route_root_cause_report.md`.

Phase 8N short GNSS-on sign probe (2026-07-20) accepted two short route runs:
`axis_map: "xy"` and old Gate 6b `axis_map: "-x-y"` with
`EKF2_OF_N_MIN=0.5`. Both flew the route under GNSS, but the ULog
flow-velocity sign sentinel separated them: `xy` body-X corr/gain was
`+0.811 / +1.012`, while old `-x-y` was `-0.899 / -1.075`. Conclusion:
`xy` is the sign-correct bridge candidate; old Gate 6b was a GNSS-on
workaround that de-weighted inverted flow. Details:
`phase_08n_flow_sign_inversion_probe.md`.

**Phase 10 — GNSS-loss flow-aiding repair (2026-07-20) — Accepted with
limitations.** The Phase 8N sign fix (`axis_map: "xy"`) plus a tuned
`EKF2_OF_N_MIN=0.3` produced the first-ever bounded GNSS-loss LK result,
confirmed on two independent replicates including a dedicated, explicit
50 s GNSS-off duration test matching the SIFT/stock-flow benchmark
convention (n=3/3): truth-path straightness 0.964–0.978, flow
fused/rejected 1584–1595/0, EKF-vs-truth horizontal error under 1.7 m max
— versus the old sign-inverted contract's `~1.4 km` and `2515.98 m`
flyaways. The optical-flow sign inversion was very likely the entire root
cause of every prior GNSS-loss divergence, not a separate control-loop
damping problem as originally hypothesized. Open items: the GNSS-loss
timing-reference bug (root cause now found — `offboard_local_position_hold`
mode computes effective loss time from `control.gnss_loss_after_offboard_s`
and ignores the `--gnss-loss-after-takeoff-s` CLI value entirely — not yet
fixed; Run D's ULog GNSS stream also shows the actual GPS-validity drop
earlier than the scheduled/status timestamp) and SIFT reconfirmation. Details:
`phase_10_gnss_loss_flow_aiding_repair.md`. This unblocks Phase 11.

## Previous phase (8K)

Phase 8K — Bounded-flow candidate (LK bridge matches the stock flow contract).

Implemented on 2026-07-16. 8K reconfigured the LK bridge to the stock gz flow
contract (always send, honest quality, 7.4 rad/s sanity limit; PX4 owns
rejection). Smoke run `20260716_134001` showed the bridge starvation problem
was fixed but the vehicle still failed via EKF rejection and range/scene
collapse, so the phase is paused pending 8L sanity proof. Details:
`phase_08k_bounded_flow_candidate.md`.

## Previous phase (8J)

Phase 8J — PX4 stock flow benchmark + DATABOSS flow upgrade study.

Implemented and flown on 2026-07-16. 8J adds a staged comparison against PX4
stock `gz_x500_flow` on `flat_rural_phototex_noon` and an identical-frame
estimator replay path for the new DATABOSS `lk` bridge estimator. The 3x long
replicate result is now available: stock flow bounded 3/3, current SIFT bounded
1/3 and diverged 2/3, and live LK diverged 3/3 because EKF optical-flow fusion
never activated (`cs_opt_flow_active_fraction=0`, fused/rejected 0/0). Bridge
low Hz is dominated by 20 Hz cap aliasing on a 30.3 Hz camera source plus
separate estimator/render wall-clock cost. Details:
`phase_08j_stock_flow_benchmark.md`.

Repair update: fixed LK now reaches PX4 `vehicle_optical_flow` and EKF2
optical-flow aid after bridge startup primes and robust median/MAD LK gating.
Proof run `20260716_111242` produced 395 `vehicle_optical_flow` rows, 332 aid
rows, 150 fused / 0 rejected, and `cs_opt_flow` active. A fresh 3x long matrix
is still required before accepting LK as a flight-performance upgrade.

## Earlier phase (8I)

Phase 8I — GNSS-denied camera + TF03 comparison, D repair loop.

Flat A/B/C/D has been measured on `flat_rural_phototex_noon`. The first full
D result fed some live flow but failed the thesis gate; newer repair runs now
prove the live flow path reaches PX4/EKF2 after the `prime_on_unsent` pre-arm
fix. Best current D repair run is `20260714_170829`: accepted, GNSS loss
effective at 19.0 s after takeoff, 527 real flow packets + 53 prime packets,
PX4/EKF optical-flow topics at about 14 Hz, 408 fused / 133 rejected, and
0.787 m / 1.841 m mean/max horizontal EKF-vs-Gazebo error. The remaining
problem is EKF optical-flow rejection/reset behavior and route bending after
GNSS loss, not a missing connection.

The current D YAML is restored to the baseline config (`rate_hz: 20`,
`max_width: 320`, `sift_n_features: 180`, `axis_map: "-yx"`, range gate
`[0.8, 60.0]`, `prime_on_unsent: true`, `vy_m_s: 0.2`,
`skip_landing_command: true`). The `EKF2_OF_GATE=5.0` run `20260714_172753`
is exploratory only: it reduced rejects but worsened route max error, so it is
not the baseline.

Flat 8I is now **closed and accepted with limitations** (2026-07-15; see Upcoming
phases below and `phase_08i_gnss_denied_flow_comparison.md`). Terrain A/B/C/D
remains paused even though `serefli_koschisar_flowtex` has a valid camera/range
proof (`20260713_154712`). Housekeeping done 2026-07-16: pruned superseded/failed
8I/8G runs + the frozen Phase 8A raw-ULog cluster, taking `/` from 439 MB to
**4.0 G free** (accepted results and cited anchors preserved). The next science
direction — terrain A/B/C/D, a long-outage D loop-damping study, or the Phase 12
report — is undecided. Preserve WebGZ proxy `9002` and keep runner raw `9003`
free between runs on any future run.

## Upcoming phases

- **Phase 8J — PX4 stock flow benchmark + DATABOSS LK study** — Stock
  benchmark, 3x current-SIFT long replicate, and 3x live-LK replicate report
  complete. Next action is LK fusion-debug, then a one-variable bridge-rate
  test; broad EKF gate tuning remains later.
  `phase_08j_stock_flow_benchmark.md`.
- **Phase 8I — GNSS-denied A/B/C/D comparison** — Accepted with limitations
  (flat, 2026-07-15). Our camera+TF03 flow D holds GNSS-denied position to
  ~0.8 m for a ~35 s outage (beats no-aid B ~32x, near ideal-odom C), but the
  optical-flow-only loop is marginally stable and diverges over a 50 s outage
  (4/4 realizations); `EKF2_OF_DELAY` tuning was falsified. D is a duration-
  limited short-outage capability; robust long-outage D would need loop damping
  (deferred). Terrain paused. `phase_08i_gnss_denied_flow_comparison.md`.
- **Phase 10 — GNSS-loss flow-aiding repair** — Accepted with limitations
  (2026-07-20). LK bounded under actual GNSS loss on `axis_map: "xy"` +
  `EKF2_OF_N_MIN=0.3`, confirmed n=3/3 including a dedicated 50 s-off test.
  Open: timing-reference bug (root cause found), SIFT reconfirmation.
  `phase_10_gnss_loss_flow_aiding_repair.md`.
- **Phase 11 — Three-way flow comparison** — Accepted with limitations
  (2026-07-20, corrected). LK, SIFT, and stock flow all bounded under
  matched ~50 s GNSS-off conditions, zero rejected flow samples across all
  three (max horizontal error 1.09–1.35 m, straightness 0.97–0.99). Stock's
  leg was first accepted on a run where GPS never actually dropped despite
  the loss command being acknowledged; corrected to a verified-genuine
  GNSS-loss run, which is bounded but the loosest of the three, not the
  tightest as first reported. SIFT's corrected-sign result reopens Phase
  8I's "diverges 4/4 at 50 s" finding — that was measured on the
  sign-inverted contract. Stock's reliability under genuine GNSS loss is
  still open (n=2: one bounded, one hit `mc_pos_control` failsafe).
  `phase_11_three_way_flow_comparison.md`.
- **Phase 12 — Unified comparison report system** — Accepted (2026-07-21).
  Manifest-driven Markdown report/plot generator with an independent
  ULog-based GPS-state guard; first matrix is LK/SIFT/stock x
  GNSS-on/GNSS-loss plus a fully unaided baseline (`30.08 m` max horizontal
  error unaided vs `0.58-1.35 m` aided). Stock GNSS-on skipped by decision;
  world/sensor variants deferred to future manifest additions.
  `phase_12_mvp_comparison_report.md`.
- **Phase 13 — Dashboard data contract** — Planned, blocked on Phase 12
  (now unblocked/next). Schemas + experiments index derived from Phase
  12's actual manifest/report format, no UI.
  `phase_13_dashboard_data_contract.md`.
- **Phase 14 — Difficulty roadmap (flat/noon/low-alt → dark terrain @ 60 m)**
  — In progress. 8-batch ladder adding one world/altitude condition at a
  time. All 8 batches now have detailed executable specs (per-batch prep,
  knob changes, what-could-break, acceptance gate), a dependency graph, and
  four carried-forward open risks — the flat altitude batches (2, 3) are
  config-only drop-ins thanks to the 14a primitives; only batches 4, 6, 7
  touch anything beyond scenario YAML, and only 7 touches Python (terrain
  lighting). `phase_14_difficulty_roadmap.md`.
  - **Phase 14a — 15 m altitude** — Accepted (2026-07-21). LK/SIFT/stock
    optical flow holds GNSS-denied flight at 15 m (1.5–2.2 m horizontal vs
    60 m unaided). Produced four reusable runner primitives that de-risk the
    rest of the roadmap: YAML-authoritative gnss/failsafe resolver;
    `confirm_gnss_loss()` fail-loud GPS-drop verification (kills the silent
    SIM_GPS_USED flake); `wait_for_target_altitude()` altitude-independent
    loss timing; universal `extra_px4_params` + `EKF2_RNG_A_HMAX` so the
    rangefinder anchors absolute height at altitude on flat terrain.
    `phase_14a_altitude_15m.md`.

Numbering note: phases 10 and 11, originally left as headroom when the
2026-07-10 roadmap jumped from 8I to 12/13, are now allocated (above); 8H
was absorbed into 8G.3; Phase 9A (real-terrain world import) ran
out-of-order as world infrastructure and is Accepted.

Historical duplicate write-ups (Phase 8F/8D superseded blocks, the
duplicate "Next phases" list, the stale "Current Phase 8B/8C focus"
sections, and the Phase 7D freeze note) relocated to
`docs/phases/archive/legacy_phase_notes.md` (2026-07-20 cleanup) — no
content changed, only moved.

## Road to the GNSS-denied result (planned 2026-07-10)

The remaining work in one line: turn the proven camera+TF03 data into a
navigation aid, fuse it, and measure where it lands between the frozen 8A
anchors (no aiding: 320 m drift / ideal aiding: 0.075–0.215 m).

- **8F — Offline optical-flow validation** (ACCEPTED 2026-07-11, hover-only;
  translation route deferred to 8G/8H). Original gaps noted: continuous
  timestamped frame recording (single probe frame today; likely a low-res
  camera variant for the 4 GB VM) and a slow translation route (hover has no
  flow; offboard sender from 8A exists). OpenCV flow + lidar AGL + intrinsics
  → velocity vs Gazebo truth velocity, offline. Flat world first, then
  serefli_koschisar.
- **8G — Live optical-flow bridge**: same math live → OPTICAL_FLOW_RAD →
  PX4. Decision recorded: PX4's built-in x500_flow/OpticalFlowSystem may be
  used only as a plumbing rehearsal; the research claim requires our own
  camera→flow pipeline.
- **8H — GNSS-on fusion check**: EKF2_OF_CTRL on; ULog proof of fusion
  (aid-source innovations, no rejection storm), no degradation with GNSS on.
- **8I — GNSS-denied comparison (the target result)**: A GNSS-on / B loss
  no-aiding / C loss + ideal odometry (frozen) / D loss + camera+TF03 flow.
  Station drift vs truth, height hold, mission completion; flat + terrain.
  Side result: lidar-aided height vs baro height (see the 8E terrain height
  plot — EKF height drifts ~0.16 m over terrain while lidar tracks AGL).
- **12 — MVP comparison report**; **13 — dashboard backend contract** (last).

Housekeeping before the 8I matrices: prune superseded run folders with
operator sign-off (disk ~4 GB free); optional upstream issue for the
gz-launch missing-enum bug.

## Phase 8A status

Phase 8A is frozen as the ideal external-aiding upper-bound reference.

The repaired 120 s A/B/C comparison passed:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s
```

The real-error 120 s Case C matrix also passed:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s
```

Latest accepted Phase 8A result:

```text
GNSS-on reference station drift end: 0.007 m.
GNSS-loss/no-aiding reference station drift end: 320.197 m.
Nominal repaired Case C station drift end: 0.113 m.
Worst real-error Case C station drift end: 0.215 m.
Combo noise+latency+dropout Case C station drift end: 0.075 m.
```

Phase 8A proves:

```text
Gazebo truth -> local ENU position/velocity -> MAVLink ODOMETRY -> EKF2 EV fusion
```

It does not prove camera, TF03, optical-flow, VIO, LiDAR odometry, or LiDAR-SLAM performance.

## Core rule

PX4 is the engine.

DATABOSS is the research workspace.

Do not write experiment outputs into PX4 source.
