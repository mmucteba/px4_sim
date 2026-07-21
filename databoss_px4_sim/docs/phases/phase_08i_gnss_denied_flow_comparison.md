# Phase 8I — GNSS-denied A/B/C/D comparison (the target result)

**CONTRACT SUPERSEDED 2026-07-17 / 2026-07-20** — Case D here used the
pre-Gate-6b `axis_map: "-yx"` contract, later found sign-inverted (Phase 8N
found `axis_map: "xy"` is sign-correct). The A/B/C/D ordering and the
GNSS-denied loop-gain limit-cycle finding below remain the relevant
evidence; the specific D numbers were produced on a contract now known to
be wrong-signed and should be re-measured under Phase 10/11. See
`docs/phases/phase_08l_sensor_sanity_ladder.md` and
`docs/phases/phase_08n_flow_sign_inversion_probe.md`.

Status: **Accepted with limitations (flat) — Case D is duration-limited**
(2026-07-15). Flat A/B/C/D are measured and the thesis ordering holds at a
short (~35 s) GNSS outage: A GNSS-on 0.07 m, C ideal-odom 0.17 m, our live
camera+TF03 flow D 0.79 m, B no-aid ~25 m (run `170829` for D). But the 8I D
repair loop closed on a physical limit, not a green gate: the optical-flow-only
control loop is **marginally stable** and reliably **diverges over a full 50 s
outage (4/4 realizations, 2026-07-15)**. The remaining rejection is a loop-gain
limit-cycle, not a timing/plumbing fault — `EKF2_OF_DELAY` tuning was falsified
(45 ms made it worse). D is accepted as a **short-outage** capability with a
stated marginal-stability limitation; a robust long-outage D would need loop
damping (`EKF2_OF_N_MIN/MAX`), deferred by decision. Terrain A/B/C/D remains
paused. See the 2026-07-15 results subsection below.

## Pre-8I world prerequisites (added 2026-07-13)

The legx investigation (see `phase_08g_live_flow_bridge.md` results) proved
no existing world gives the downward camera real texture — flow aiding
cannot work over featureless ground. Before the 8I campaign:

1. **Flat world**: use `flat_rural_phototex_noon` (8G Step 1, procedural
   ~1 cm/px ground texture) as the favorable world instead of the solid-tile
   `flat_rural_high_texture_noon`. For 8I realism, re-enable shadows with an
   ANGLED sun (the 8G validation world runs shadows off; the vehicle's own
   shadow is a documented flow confound — the shadow setting must be frozen
   per case and recorded in the scenario YAMLs).
2. **Terrain world**: repair `serefli_koschisar` visuals so the camera sees
   texture — the heightmap `aerial.png` diffuse does not render under the
   pinned sensor engine, aerial.png is ~0.9 m/px (featureless at flow scale)
   and the spawn launch pad is a featureless gray 4×4 m surface. Needs: a
   working texture render path (engine result from 8G Step 0), a flow-scale
   detail-texture strategy, and either a textured or removed launch pad in
   the camera view.

   First repair proof (2026-07-13): `serefli_koschisar_flowtex` keeps the
   original heightmap collision and adds a visual-only flow-detail overlay
   plus a textured launch-pad visual. Smoke test launch/spawn passed, and run
   `20260713_154712` accepted with camera/range recording: 590 frames,
   rangefinder `974/974` finite, ULog distance sensor 1417 rows, ULog airborne
   20.808 s. SIFT keypoints on sampled frames: 45, 52, 56 early, then 400+
   after the camera sees the overlay (`frame_000190`, `000300`, `000500`).

## Goal

Measure, against Gazebo ground truth, what our own camera+TF03 optical-flow
aiding buys PX4 during GNSS loss — placed between the frozen Phase 8A
anchors: no aiding ≈ 320 m drift, ideal truth-fed odometry 0.075–0.215 m.
This is the research result the whole project exists to produce.

## Why this phase exists

Everything so far proves components: worlds (8B/9A), sensors (8C/8D/8E), the
estimator offline (8F), the live bridge and EKF2 fusion GNSS-on (8G). None of
it yet answers the thesis question: *how does PX4 navigate through a GNSS
outage with our flow stack vs without it?* 8I is the controlled experiment
that answers it.

## In scope

- Four cases per world, only the aiding variable changes:
  - **A** — GNSS on throughout (stable reference).
  - **B** — GNSS loss mid-flight, no aiding (failure class, ~320 m anchor).
  - **C** — GNSS loss + frozen ideal truth-fed odometry (8A upper anchor,
    rerun under the frozen 8I variables so all four are contemporaneous).
  - **D** — GNSS loss + **our live camera+TF03 flow bridge** (EKF2_OF_CTRL=1,
    TF03 distance_sensor owns HAGL) ← the thesis case.
- Two worlds: `flat_rural_phototex_noon` (favorable; replaces the solid-tile
  `flat_rural_high_texture_noon` — see pre-8I prerequisites) and
  `serefli_koschisar` terrain (realistic; requires the texture repair above.
  The "best 8F feature quality" previously credited to this world came from
  launch-pad edges, not ground texture — superseded 2026-07-13).
- GNSS loss via the accepted runtime method `param set SIM_GPS_USED 0`
  (restore `10` after the run).
- Housekeeping before the campaign: prune superseded run folders (operator
  sign-off required).

## Out of scope

- Low-texture world (documented 8G limitation; can be a follow-up case).
- Gyro-compensated flow, aggressive maneuvers, wind.
- New estimator work — the estimator is frozen at its 8G-accepted config.
- Dashboard/report tooling (Phases 12–13).

## Inputs

- 8G-accepted bridge config: estimator `sift`, `max_width 480`, `axis_map`
  `-yx` as gated by D5, quality rescale [20,100] → 0..255,
  `EKF2_OF_QMIN 17`, calibrated `EKF2_OF_DELAY=111` (airframe 4022 default).
- Frozen flight profile: same route, altitude (2.5 m AGL), durations,
  loss timing (`loss_after_takeoff_s`), failsafe profile, spawn pose across
  all four cases. Record every frozen variable in each scenario YAML.
- 8A anchors and GNSS-on baseline (0.033–0.050 m mean horizontal error) for
  interpretation.

## Implementation

- 8 scenario YAMLs: `phase8i_{a_gnsson,b_loss_noaid,c_loss_idealodom,d_loss_flow}`
  × `{flat_rural_phototex_noon, serefli_koschisar}`, differing ONLY in
  the `gnss:` and aiding (`aiding:` / `flow_bridge:`) sections.
- Existing runner machinery covers all cases (GNSS loss automation from 7B,
  external odometry from 8A, flow bridge from 8G) — target: zero new control
  code, batch YAML under `experiments/batches/`.
- Comparison analyzer: `scripts/analysis/report_phase8i_flat_comparison.py`
  writes the per-case metrics table, aiding setup table, stream-frequency
  table, route overlays, error plots, D flow diagnostics, D lidar-vs-Gazebo
  truth comparison, and markdown report into
  `experiments/comparisons/phase8i_flat_phototex_abcd_20260714/`.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
# per case (as user px4, detached; wall time per RTF of the world):
sudo -u px4 python3 scripts/runner/run_scenario_pxh_end_to_end.py \
  experiments/configs/mvp/scenarios/phase8i_<case>_<world>.yaml \
  --hover-s <s> --land-timeout-s 300
# then, for the completed flat 8I comparison:
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/analysis/report_phase8i_flat_comparison.py
```

## Expected outputs

Standard run folders per case + one comparison folder with:
metrics JSON/table (horizontal drift vs truth during the outage window,
height hold, mission completion, EKF resets, fusion coverage for D),
overlay horizontal-error plot, and a validation report.

## Acceptance criteria

- All 8 runs accepted by the standard runner contract (truth alignment ok).
- The GNSS-loss condition is proven in each B/C/D ULog (satellites_used → 0,
  EKF GPS fusion stops).
- Case D: `cs_opt_flow` active during the outage; flow fused (not rejected)
  through the outage window.
- Ordering sanity: drift(B) ≫ drift(D) and drift(C) ≤ drift(D) in both
  worlds. No specific number is promised for D — the measured value IS the
  result, whatever it is.
- Comparison artifacts saved and reproducible from the batch YAML.

## Results

### Flat phototex A/B/C/D (2026-07-14)

Frozen profile: `flat_rural_phototex_noon`, combined camera+TF03 vehicle,
2.5 m AGL, slow +Y local-hold (`vy_m_s: 0.2`), 60 s requested window,
GNSS loss at 10 s for B/C/D, web browser through proxy `9002` and runner raw
bridge `9003`.

| Case | Run | Runner result | GNSS loss | Mean horiz err m | Max horiz err m | End horiz err m | Mean height err m | Notes |
|---|---|---:|---:|---:|---:|---:|---:|---|
| A GNSS on | `20260714_070542` | accepted | no | 0.062 | 0.116 | 0.098 | 0.129 | Baseline stable; gazebo displacement end 10.90 m. |
| B loss/no aiding | `20260714_071532` | rejected by range proof, postprocessed | yes | 23.792 | 174.797 | 73.679 | 1.921 | Deliberate failure anchor; range proof went `inf` after runaway, but truth/ULog aligned. |
| C loss/ideal odom | `20260714_074250` | accepted | yes | 0.142 | 1.009 | 0.924 | 0.130 | Repaired C uses EV horizontal position+velocity only (`EKF2_EV_CTRL=5`) and TF03 height (`EKF2_HGT_REF=2`). |
| D loss/live flow | `20260714_075214` | rejected by range proof, postprocessed | yes | 100.986 | 246.245 | 225.512 | 39.837 | Flow bridge sent 455 rows and fused some flow, but D diverged worse than B. |

D ULog flow evidence (`20260714_075214`): `sensor_optical_flow_rows=449`,
`cs_opt_flow_active_fraction=0.4569`, `flow_fused_count=146`,
`flow_rejected_count=51`, `flow_rejected_over_fused=0.3493`,
`flow_quality_zero_fraction=0.5256`. The analyzer printed
`flow_fusion_ok=False`.

Conclusion: the flat-world thesis comparison is now measured, and the result
is a failure of the current live-flow aiding configuration, not a missing
pipeline piece. D does not yet buy navigation through GNSS loss; it makes the
run worse than no aiding in this profile. Terrain A/B/C/D should not be burned
until D is repaired on the favorable flat world.

Comparison artifacts:

- Report: `experiments/comparisons/phase8i_flat_phototex_abcd_20260714/report.md`
- Report section: `Current Connection Map` documents the live optical-flow
  chain, separate TF03/range chain, frame table, actual frequencies, and
  `axis_map=-yx` meaning/correctness.
- Metrics/data: `comparison_metrics.csv`, `comparison_metrics.json`,
  `aiding_setup.csv`, `aiding_setup.json`, `stream_frequencies.csv`,
  `stream_frequencies.json`, `lidar_truth_metrics_d.csv`,
  `lidar_truth_metrics_d.json`, `lidar_truth_matched_d.csv`,
  `camera_samples_d.json`
- Plots: `routes_xy_overlay.png`, `routes_xy_nearfield.png`,
  `route_xy_ekf_vs_truth.png`, `horizontal_error_timeseries.png`,
  `height_error_timeseries.png`, `horizontal_error_bars.png`,
  `height_error_bars.png`, `flow_d_diagnostics.png`,
  `lidar_truth_height_d.png`, `lidar_truth_scatter_d.png`,
  `camera_sample_d_early_invalid_frame_000300.jpg`,
  `camera_sample_d_valid_texture_frame_000500.jpg`,
  `camera_sample_d_late_edge_frame_000900.jpg`

Current setup/frequency snapshot from the report:

- A/B: no aiding, `EKF2_HGT_REF=1`, offboard setpoints at 20.00 Hz.
- C: truth-fed external odometry, configured 30 Hz, actual sent stream
  94.39 Hz in sim time, `EKF2_EV_CTRL=5`, `EKF2_HGT_REF=2`.
- D: live SIFT flow bridge, configured 10 Hz, actual sent stream 7.58 Hz
  in sim time, `axis_map=-yx`, `EKF2_OF_CTRL=1`, `EKF2_OF_QMIN=17`.
- D current flow stack: Gazebo RGB camera frame -> grayscale/downscale
  (`max_width=480`) -> OpenCV SIFT (`n_features=400`, Lowe ratio 0.75,
  min 8 matches) -> median matched-pixel displacement -> pinhole
  `rad=px/focal_px` with `hfov_rad=1.74` -> MAVLink `OPTICAL_FLOW_RAD`.
  Optical-flow messages carry `distance=-1`; height comes from the separate
  TF03-style `distance_sensor` stream.
- D lidar/truth finite-row comparison: raw recorder 3244 rows at 50.00 Hz
  with 45.1% finite positive ranges; bridge input 455 rows at 7.58 Hz with
  40.7% finite positive ranges. After subtracting the 0.174 m landed range
  offset, finite lidar height matches Gazebo truth tightly
  (mean abs error 0.041 m for both streams), so the D failure is tied to
  range going `inf`/fusion behavior during divergence, not a finite-sample
  lidar scale mismatch.
- Camera samples: D `frame_000300.jpg` at `t_sim=21.780s` shows the early
  invalid/blue view before useful ground texture; `frame_000500.jpg` at
  `t_sim=28.380s` shows the valid textured-ground middle window;
  `frame_000900.jpg` at `t_sim=41.580s` shows the late edge/out-of-field view.

Input-validity diagnosis for D: the bridge begins while the camera/lidar are
not yet seeing useful ground (`quality=0`, `n_matches=0`, range near
`0.174m` until about `t_sim=23.067s`). Useful SIFT matches begin around
`t_sim=23.199s`, but the estimator has already started diverging. Later the
runaway carries the vehicle to the edge of the finite phototex field; raw lidar
first becomes `inf` at `t_sim=41.14s` after the last finite reading of
`27.815m`, and the late camera frame confirms ground texture leaving the view.

### D repair loop current state (2026-07-14 late)

Main pipeline fix: PX4 did not reliably create/use `vehicle_optical_flow` when
the bridge stayed silent while landed. The bridge now supports
`prime_on_unsent`: when real flow is gated out, it sends zero-quality
`OPTICAL_FLOW_RAD` prime packets before takeoff while keeping real-flow
accounting separate (`mavlink_sent` vs `sent`, plus `n_prime_sent`). That
starts the PX4 optical-flow path early; real valid samples then feed EKF2 once
range/texture gates pass.

Current Case D scenario is back on the baseline repair config:
`rate_hz: 20`, `max_width: 320`, `sift_n_features: 180`,
`sift_ratio: 0.75`, `sift_min_matches: 8`, `axis_map: "-yx"`,
`send_min_range_m: 0.8`, `send_max_range_m: 60.0`,
`send_min_quality: 20`, `prime_on_unsent: true`, `reset_on_unsent: true`,
`EKF2_OF_CTRL=1`, `EKF2_OF_QMIN=17`, `vy_m_s: 0.2`, and
`skip_landing_command: true`. No `EKF2_OF_GATE`, `EKF2_OF_N_MIN`, or
`EKF2_OF_N_MAX` override is active in the current YAML.

| Run | Condition | Accepted | Real flow / prime | PX4/EKF flow rate | EKF fused / rejected | EKF-vs-truth horiz mean / max | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| `20260714_170008` | no-loss probe | wrapper bookkeeping failed | - | - | 199 / 0 | - | Proved clean OF fusion when GNSS remains available; runner wrapper failed because no-loss observation mode was not its target path. |
| `20260714_170829` | baseline GNSS-loss D | yes | 527 / 53 | `sensor_optical_flow` ~14.00 Hz, `estimator_aid_src_optical_flow` ~13.93 Hz | 408 / 133 | 0.787 / 1.841 m | Best current D repair run; strict flow gate still fails because rejected/fused is 0.326. |
| `20260714_171848` | soft OF noise tuning | stopped/invalid | - | - | - | - | `EKF2_OF_N_MIN=0.30`, `EKF2_OF_N_MAX=0.70` caused worse drift/dead reckoning; reverted. |
| `20260714_172628` | rerun attempt | startup failed | - | - | - | - | Stale PX4 socket/process issue; cleaned. |
| `20260714_172753` | exploratory `EKF2_OF_GATE=5.0` | yes | 527 / 53 | `sensor_optical_flow` ~13.96 Hz, `estimator_aid_src_optical_flow` ~13.91 Hz | 432 / 97 | 0.797 / 5.121 m | Fewer rejects but worse route max and reset behavior; not the current baseline. Plots exist under this run. |

Current answer to "are we feeding EKF with flow?": **yes**. The bridge target
is 20 Hz, but the realized flow entering PX4/EKF is about 14 Hz on the latest
accepted runs. The likely bottleneck is SIFT compute plus sim timing; reducing
feature count and image width helped but did not reach full 20 Hz.

Current answer to "why did it not hover?": the D profile is not an XY hover
test. It commands `vy_m_s: 0.2`, so about 9-10 m of local-Y displacement is
expected over the observation window. The looped/curved route is not from the
command alone; it appears when EKF optical-flow innovations are rejected or
the estimator resets after GNSS loss, and the position controller follows the
local estimate.

Current blockers:

- Strict analyzer still marks D flow fusion not green because the reject/fused
  ratio is too high even though many samples are fused.
- Gate-only tuning (`EKF2_OF_GATE=5.0`) reduced rejects but made the max route
  error worse; broad noise/gate changes are not accepted as the fix.
- We need one contained, repeatable D run with the same `vy=0.2` route and
  timing, lower rejection/reset behavior, and the baseline YAML restored.
- Disk is nearly full (`/opt` and `/tmp` about 99% used, ~612 MB free), so
  invalid runs should be pruned before more batch work.

Next D-focused work: clean only invalid/failed runs, keep WebGZ proxy `9002`
as the preserved user-facing bridge, ensure runner-owned raw `9003` is free
before each run, then investigate message timing/frame acceptance and OF-delay
behavior with the baseline `170829` result as the comparison target.

### D repair close-out: timing falsified, D is duration-limited (2026-07-15)

The D repair loop was closed this day. Findings, in order:

1. **Rejections are a post-outage control limit-cycle, not bad flow.** Against
   Gazebo truth, the flow is accurate (truth expected-flow RMS 0.57 rad/s vs
   OF observation RMS ~0.46; mean scale matches `v/h`). On `170829` all 133
   rejects occur after GNSS loss (t=22 s); zero while GPS is up. Signature is
   loop-gain, `corr(innovation, observation) = -0.92`, with the vehicle
   physically oscillating (truth speed ~0.8 m/s vs 0.2 commanded).

2. **`EKF2_OF_DELAY` tuning is falsified.** A scenario/runner knob was added
   (`flow_bridge.ekf2_of_delay` -> `param set EKF2_OF_DELAY`; mirrors
   `ekf2_of_gate`). Reducing 111 -> 45 ms made fusion **worse**, reject/fused
   `0.326 -> 1.156`, test_ratio[rej] `[1.1,0.9] -> [3.1,5.3]` (run
   `20260715_102252`, since pruned). True async-pipeline latency is >= 111 ms.
   Delay held at 111. Timing is not the lever.

3. **World enlarged 120 -> 240 m** (`flat_rural_phototex_noon.yaml`,
   144 tiles, +/-120 m textured) to remove any off-field/blue-sky confound for
   longer/driftier runs. Confirmed the *nominal* good D (`170829`) stays within
   9 m and keeps the rangefinder 100% finite; the earlier blue-sky/inf frames
   came from diverged runs, not a world-size limit.

4. **Clean contemporaneous flat batch** `20260715_110133`
   (`phase8i_gnss_denied_flow_abcd_flat_60s.yaml`, equal 60 s / 50 s-post-loss
   windows, enlarged world): **A** `110137` 0.110 m, **C** `112223` 0.194 m
   both clean; **B** `111229` failed as the intended anchor; **D** `113204`
   **diverged** (max alt 29.7 m, max horiz 44 m, reject/fused 1.29, flow-starved
   333 samples).

5. **Robustness characterization at the 50 s profile: 4/4 diverged.** Case D
   re-run (delay=111, light `phase8i_d_varirun_*` scenario, frame-save off):

   | Realization | flow samples | reject/fused | max horiz | max alt | verdict |
   |---|---:|---:|---:|---:|---|
   | `113204` (batch) | 333 | 1.29 | 44 m | 30 m | DIVERGED |
   | vari-1 `121400` | 274 | 1.42 | 55 m | 30 m | DIVERGED |
   | vari-2 `122221` | 319 | 2.58 | 371 m | 5.5 m | DIVERGED |
   | vari-3 `123107` | 239 | 1.38 | 50 m | 27 m | DIVERGED |
   | `170829` (35 s window) | 527 | 0.326 | ~9 m | 2.5 m | BOUNDED, 0.79 m |

   Every 50 s realization diverges and is flow-starved (239-333 samples vs the
   bounded run's 527): a positive-feedback failure (divergence -> vehicle leaves
   good texture / tilts / climbs -> fewer usable SIFT samples -> weaker aiding
   -> more divergence).

**Accepted conclusion (with limitations):** our camera+TF03 optical-flow aiding
holds GNSS-denied horizontal position to ~0.8 m for a **~35 s** outage
(`170829`, beats no-aid B by ~32x, same order as ideal-odom C), but the
optical-flow-only control loop is **marginally stable and reliably diverges
over a 50 s outage (4/4)**. D is a proven *short-outage* capability, not a
robust general GNSS-denied solution in this profile. The only remaining lever
is loop damping (`EKF2_OF_N_MIN/MAX`), deliberately deferred. Short-outage
comparison artifacts:
`experiments/comparisons/phase8i_flat_phototex_abcd_final_20260715/`.

Runner/config fixes made during 8I:

- Batch cleanup now removes stale `/tmp/px4-sock-*` when permitted and kills
  stale offboard/external-odom/flow/camera helper processes.
- Airborne-duration waits use `vehicle_local_position.timestamp` as the
  simulated clock because `vehicle_land_detected.timestamp` can stop updating
  while airborne.
- Observation-mode GNSS-loss cases wait only briefly after the land command;
  clean landing is not an acceptance requirement for deliberate failure cases.
- External odometry runtime parameter setup now honors scenario
  `aiding.ekf2_hgt_ref` instead of forcing `EKF2_HGT_REF=3`.
- Case C was repaired for the combined lidar vehicle: EV horizontal
  position+velocity is fused, while TF03/range owns height.

## Interpretation

Flat D failure mode from the original A/B/C/D batch: the flow bridge delivered
and PX4 fused some optical-flow samples, but quality was zero-heavy and
rejection rate was high. During GNSS loss, the estimator/range state diverged
(`ulog_max_height_up_m=21.56` and range proof ended at `inf`), and the
horizontal error exceeded the B no-aiding anchor. The newer D repair loop has
fixed the EKF-feeding path and improved the run dramatically, but D is not yet
accepted as a thesis result because OF rejection/resets still bend the route
after GNSS loss. The next work item remains D-focused repair, not terrain
expansion.

## Known limitations

Simulation-only (software rendering, lockstep); hover/slow-translation
profile; no wind; quality gate limitation on low texture documented in 8G.

**Case D marginal stability (2026-07-15):** the optical-flow-only control loop
is only conditionally stable. It holds GNSS-denied position for ~35 s outages
but diverges over 50 s (4/4 realizations), with a divergence -> flow-starvation
positive feedback. `EKF2_OF_DELAY` tuning does not fix it (falsified);
robustness would require loop damping (`EKF2_OF_N_MIN/MAX`), which was
deliberately not pursued. D results are therefore scoped to short (~35 s)
outages. The favorable-world D result also does not transfer to terrain (paused)
or to low-texture / windy / aggressive-maneuver conditions.

## Files created or modified

- `scripts/worlds/add_serefli_flow_texture_overlay.py`
- `generated_worlds/terrain/serefli_koschisar_flowtex/`
- `experiments/configs/mvp/scenarios/phase8i_pre_serefli_flowtex_camera_proof.yaml`
- `experiments/configs/mvp/scenarios/phase8i_{a,b,c,d}_*_*.yaml`
- `experiments/configs/mvp/batches/phase8i_gnss_denied_flow_abcd_60s.yaml`
- `scripts/analysis/report_phase8i_flat_comparison.py`
- `scripts/analysis/plot_phase8i_flow_run.py`
- `experiments/comparisons/phase8i_flat_phototex_abcd_20260714/`
- `scripts/sim/flow_mavlink_bridge.py`
- `scripts/runner/auto_takeoff_land_pxh_truth.py` (added `EKF2_OF_DELAY` knob)
- `scripts/runner/run_batch_matrix_pxh.py`
- `scripts/analysis/diagnose_flow_rejections.py` (2026-07-15, D1 diagnosis)
- `scripts/analysis/plot_phase8i_d_robustness.py` (2026-07-15, D drift + reject
  plots for the final comparison)
- `experiments/configs/mvp/scenarios/phase8i_d_varirun_flat_rural_phototex_noon.yaml`
  (2026-07-15, light frame-off scenario for D robustness sampling)
- `experiments/configs/mvp/batches/phase8i_gnss_denied_flow_abcd_flat_60s.yaml`
  (2026-07-15, flat-only clean batch)
- `experiments/configs/mvp/worlds/flat_rural_phototex_noon.yaml`
  (2026-07-15, enlarged 120 -> 240 m) + regenerated
  `generated_worlds/flat_rural_phototex_noon.sdf`
- `experiments/comparisons/phase8i_flat_phototex_abcd_final_20260715/`
  (2026-07-15, short-outage A/B/C/D with the correct D=`170829`)

## Next phase

Flat D is accepted as a duration-limited (~35 s) short-outage capability with a
stated marginal-stability limitation. Options for a follow-up, none started:
(a) loop-damping study (`EKF2_OF_N_MIN/MAX`) for robust long-outage D;
(b) 35 s-scoped clean A/B/C/D campaign if a bounded-window result is wanted;
(c) terrain A/B/C/D (still paused). Phase 12 reporting can proceed on the
accepted short-outage flat result.
