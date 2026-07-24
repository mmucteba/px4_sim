# Project Log

## 2026-07-17 — Phase 8M quick-smoke failures diagnosed: root-owned ULog dir + wall-clock landing timeout (fixes implemented, NOT flight-proven)

Afternoon session. Phase 8M (quick stock-vs-SIFT-vs-LK GNSS-on flow smokes)
was started without a phase doc; the three-flow batch
(`20260717_124807_phase8m_quick_three_flow_gnsson`) was interrupted, and the
QGC-enabled SIFT smoke failed twice. Diagnosis from
`experiments/batches/20260717_130600_phase8m_quick_sift_gnsson_qgc/`:

1. **Root cause 1 — root-owned PX4 log dir.** The interrupted 12:11
   gnssloss22c attempt ran PX4 as root, creating
   `build/px4_sitl_default/rootfs/log/2026-07-17/` owned `root:root`.
   Every later run (as user `px4`) hit
   `ERROR [logger] Can't open log file ... errno: 13` → "no new ulog found
   to copy" → all ULog gates failed even though the 12:59 SIFT flight
   completed its full profile. Fixed: `chown -R px4:px4` on the dir; the
   stranded 13.7 MB partial ULog moved to the 22c run folder as
   `logs/flight_partial_interrupted.ulg` (run stays non-accepted).
   Rule reinforced: never launch PX4 as root.
2. **Root cause 2 — landing wait used wall clock at ~0.06x sim rate.**
   13 sim s of hold took 208 wall s, but `wait_for_landing_complete` got a
   raw 30 s wall timeout (~2 sim s) → guaranteed
   `landing wait timed out` (13:06 run). Fixed in
   `scripts/runner/auto_takeoff_land_pxh_truth.py`: the landing wait now
   scales `land_timeout_s` by `sim_time_wall_multiplier` (30x) like the
   takeoff/hover waits, and logs the budget in notes. `land_timeout_s` is
   therefore now a **sim-time** budget; the interim 240 s wall-clock
   workaround in `phase8m_quick_sift_gnsson_qgc.yaml` was reverted to 30.

Status honesty: both fixes are implemented and compile-checked only. The
verification rerun (batch `20260717_132033`) was killed on user request
before takeoff; its partial run/batch folders were deleted. Orphaned
`gz-transport-topic` truth-echo processes and a stale Xvfb from earlier
runs were also killed; `/tmp/px4-sock-*` cleared. No accepted Phase 8M
evidence exists yet.

Open items: rerun `phase8m_quick_sift_gnsson_qgc` to flight-prove both
fixes; complete the stock/SIFT/LK three-flow comparison; write
`docs/phases/phase_08m_*.md`; disk is at 95% (1.9G free) — gzip today's
console logs before the next batch.

## 2026-07-17 — Session handoff: Gate 6b day (loops solved, GNSS-loss open)

Compact state for the next session. Everything below is evidenced in
`experiments/batches/manual_gate6_isolation_20260717/` and the entries under
this one.

What was proven today, in order:

1. The Gate 6 GNSS-on loops were NOT a delay problem. The EKF position
   tracked truth (GNSS-pinned) while the EKF velocity was corrupted by flow
   fusion; the velocity-mode controller flew the corrupted estimate to the
   setpoint, so the physical vehicle looped at up to 10x the commanded
   speed while every plot looked "correct".
2. Root cause 1: bridge flow axis contract rotated 90 deg vs PX4 EKF2
   (`optical_flow_control.cpp:125`). Proven three ways: PX4 source, truth
   regression (R^2 0.99, two runs), and flight A/B. Fix: `axis_map: "-x-y"`.
   Gate 4's `-yx` acceptance is superseded (its analyzer checked the bridge
   against its own assumption, spawn yaw baked in — not against PX4).
3. Root cause 2: at the default flow noise floor, LK flow out-weighted
   GNSS/mag, dragged heading ~25 deg during the in-climb fusion start
   (fusion measured to start ~+2 s after liftoff at ~1.4 m), and sustained
   a residual loop. Fix: `EKF2_OF_N_MIN 0.5`. Range-gating the fusion start
   (`send_min_range_m`) was tested and rejected — EKF already starts flow
   in-air; the gate made things worse.
4. Result: GNSS-on fused-flow leg accepted — path `1.2x` intended,
   straightness `0.977`, hmax `0.70 m`, flow fused `99.4%`/rejected `0%`
   (run `20260717_112523_...axisfix140_ofn05...`). GNSS-only control floor:
   straightness `0.995`, hmax `0.44 m`.
5. GNSS-loss smoke on that config: rejected — flyaway. Loss (SIM_GPS_USED 0
   = sats 0, EKF correctly refuses no-fix GNSS) at ~+6 s; de-weighted flow
   could not anchor velocity; controller amplified drift to `35+ m/s`; LK
   clamp/feature loss silenced flow at +19 s; failsafe fired correctly at
   +24 s (`EKF2_NOAID_TOUT` 5 s -> AUTO_LAND); truth flew E `~1.4 km`, EKF
   believed SW `~1.7 km` (yaw ~170 deg wrong -> mirrored dead reckoning).
   Route plot: `.../diagnosis/route_overlay_gnssloss_flyaway.png`.

Config/infra changes that persist:

- `scripts/runner/auto_takeoff_land_pxh_truth.py`: new generic
  `extra_px4_params` scenario mapping (applied via pxh when flow bridge
  enabled).
- New analysis scripts: `diagnose_gate6_velocity_loop.py`,
  `fit_flow_contract_from_truth.py`.
- New scenarios: `phase8l_lk_gnsson_ofoff_*`, `phase8l_lk_gnsson_axisfix140_*`
  (+ `_rgate`, `_ofn05` variants), `phase8l_lk_gnssloss20_*`,
  `phase8l_lk_gnssloss22c_*` (bounded variant, built but never flown —
  killed during PX4 build on user request).

Open problem (the actual research question, now with a measured baseline):
GNSS-denied velocity aiding. `EKF2_OF_N_MIN 0.5` is right for GNSS-on but
too weak as the only aid. Candidate directions, none tested: loss-triggered
flow re-weighting, velocity cap (`MPC_XY_VEL_MAX` via `extra_px4_params`)
to keep flow observable during divergence, position-hold instead of
velocity setpoints during denial, replicates to quantify variance.

Key numbers to beat: unaided flyaway drift `~1.4 km` in `~35 s` after loss;
GNSS-on fused floor hmax `0.70 m`.

## 2026-07-17 — CORRECTION to the GNSS-loss flyaway entry below (deeper ULog forensics)

Re-analysis of run
`20260717_114717_phase8l_lk_gnssloss20_axisfix140_ofn05_...` using PX4's own
`vehicle_gps_position` topic and `vehicle_status` corrects three claims in
the entry below (times relative to `takeoff_time` in `vehicle_status`):

1. **The divergence did NOT start before the GNSS loss.** That claim came
   from a truth-clock alignment artifact. GPS shows position ~= 0 and
   velocity ~= 0 until satellites dropped 10 -> 0 at `~+6 s`; the physical
   runaway begins immediately after (`+7 s: 0.5 m/s`, `+12 s: 6.5`,
   `+15 s: 15`, `+20 s: 35 m/s`). Takeoff itself was clean.
2. **The effective loss time was `~+6 s` after takeoff**, not the reported
   10 s plan (offboard engaged `+5.3 s`; the loss command landed right
   after), and not the requested 20 s.
3. **Failsafes were configured and DID fire.** The applied profile was
   `NAV_DLL_ACT 2, COM_POS_FS_EPH 5, COM_POS_LOW_ACT 3,
   EKF2_NOAID_TOUT 5000000` (see `commands_sent`). Flow fusion died at
   `+19.2 s` (LK clamp/feature loss at `30+ m/s`), and exactly 5 s later
   (`+24.3 s`) `failsafe=True` with nav_state -> AUTO_LAND. The vehicle
   then descended while drifting ballistically; no landing detected before
   the run window ended.

Corrected mechanism: with GNSS gone, the de-weighted flow
(`EKF2_OF_N_MIN 0.5`) was too weak to anchor the velocity state; drift
began within ~1 s, the offboard velocity controller chased the biased
estimate and accelerated the vehicle; rising flow rates then hit the LK
`max_flow_rate` clamp (7.4 rad/s) and feature-tracking limits, which
silenced the flow measurement entirely and left pure dead reckoning. EKF
believed it flew SW `(-247, -1670) m` while truth went E
`(+86, +1435) m` — the QGC map shows the EKF hallucination, physically the
vehicle went the opposite way (yaw estimate diverged to `~170 deg`).

Standing conclusion unchanged: GNSS-loss experiments need bounded damage
(velocity cap) and the de-weighted-flow-only aiding question is open.

## 2026-07-17 — First GNSS-loss smoke on the Gate 6b config: rejected (flyaway); replicates now mandatory

Ran `05_gnssloss20_ofn05` (accepted Gate 6b config `axis_map -x-y` +
`EKF2_OF_N_MIN 0.5`, GNSS lost via `SIM_GPS_USED 0`, requested 20 s clipped
by the runner to effective 10 s after takeoff):
`experiments/runs/20260717_114717_phase8l_lk_gnssloss20_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth`.

Result: **flyaway ~1.4 km at up to ~50 m/s, no failsafe**. Key evidence:

- Divergence started **before** the commanded loss: truth speed `2 m/s` at
  `+4.7 s`, `5 m/s` at `+6.6 s` after takeoff — same takeoff-transient
  class as the rejected range-gate run. GNSS velocity fusion stopped being
  accepted at `~+5.5 s` (innovation rejection during the divergence), the
  commanded loss then removed the anchor for good.
- Cascade at speed: LK feature count and quality collapsed to `0` by
  `+14.4 s`, tilted TF03 read `inf` (`40%` finite overall), EKF
  dead-reckoning on `63%` of samples, EKF and truth ended `>3 km` apart.
- `default_px4` failsafe profile has position failsafes disabled
  (`NAV_DLL_ACT 0`, `COM_POS_LOW_ACT 0`), so the runaway was unbounded.
- Runner gate correctly failed the run (`ulog_distance_sensor_ok=False`);
  postprocess was completed manually (truth parsed, ULog extracted) for
  this analysis.

Interpretation, stated carefully: this run does **not** prove the Gate 6b
config cannot fly GNSS-denied. It proves (a) the takeoff transient is a
run-to-run coin flip (run D was clean once; runs C and E diverged), so
single-run conclusions on this config are invalid; and (b) an unbounded
divergence destroys the flow sensor's own observability (speed kills
feature tracking, tilt kills the rangefinder), so GNSS-loss experiments
must bound the damage to produce usable drift data.

Next action (updated): 3 replicates of the GNSS-on run-D config to
quantify takeoff-transient variance first. Then re-run GNSS-loss smoke
with a velocity cap (`MPC_XY_VEL_MAX` ~2 m/s) and a failsafe profile that
lands instead of flying away, and only lose GNSS after a verified-stable
cruise (loss at a fixed leg position, not a takeoff-relative timer that
the runner clips).

## 2026-07-17 — Gate 6b closed: straight GNSS-on fused-flow leg achieved (axis fix + flow noise floor)

Continued the Gate 6b isolation series in
`experiments/batches/manual_gate6_isolation_20260717/` with two more
single-variable runs on top of the `axisfix140` config:

- Run C `axisfix140_rgate` (`send_min_range_m: 1.5`, flow quality zeroed
  below 1.5 m AGL): **rejected hypothesis**. Worse than the axis-fix run on
  every metric (truth path `90.8 m`, endpoint error `24.1 m`, hmax
  `19.3 m`). EKF heading ran to `~180 deg`, `vel_test_ratio` pegged `2.0`,
  three emergency yaw resets, heading aiding inactive afterwards.
  Measured fusion start: EKF2 already starts flow aiding only in-air
  (`+2.1 s` after liftoff at `1.35 m` in run B); the gate only moved it to
  `+4.1 s` at `1.42 m`, so the intended variable barely changed. n=1
  caveat recorded.
- Run D `axisfix140_ofn05` (`ekf2_of_n_min: 0.5`, default `0.15`):
  **accepted**. Straight leg with flow fused: truth path `12.8 m` (`1.2x`
  intended), straightness `0.977` (GNSS-only control `0.995`), cross-track
  end error `0.15 m`, along-track overshoot `1.87 m`, hmax `0.70 m`,
  flow fused `99.4%` / rejected `0.0%` (was `55%/44%`), truth speed
  `0.229 m/s` vs `0.2` setpoint, yaw error mean `9.2 deg` (was `25 deg`),
  test ratios `<= 0.03`.

Mechanism now fully explained: (1) the 90-deg axis contract bug made flow
report rotated velocity -> rotating feedback -> loops (fixed by
`axis_map: "-x-y"`); (2) at the default flow noise floor the LK flow
out-weighted GNSS/mag, dragged the heading `~25 deg` during the in-climb
fusion start, and sustained a smaller loop (fixed by `ekf2_of_n_min: 0.5`).
Flow fused fraction (healthy `>90%`) adopted as a key gate metric; the
Gate 6 runs sat at `45-60%` fused with `36-54%` rejected.

Evidence: `experiments/batches/manual_gate6_isolation_20260717/README.md`,
`.../diagnosis/route_overlay_gate6b.png` (4-run comparison),
per-run `vel_yaw_diag_*.png` and `innovations_*.png`.

Limitations: single run per config (no replicates yet); `EKF2_OF_N_MIN=0.5`
de-weights flow, which is the right GNSS-on posture but its GNSS-loss
holding performance is unproven; the `~9 deg` residual yaw offset and the
`1.87 m` along-track overshoot remain to be characterized; rotation-content
inflation (`~1.3x` over the 100-deg FOV) is still present, just no longer
dominant.

Next action: replicate run D (3x) to confirm stability, then rerun the
Gate 6 delay sweep with `axis_map: "-x-y"` + `ekf2_of_n_min: 0.5` to close
Gate 6 properly, then proceed to Gate 7 / GNSS-loss smoke with the flow
fused-fraction gate (`>= 0.9`) added to acceptance.

## 2026-07-17 — Gate 6b: flow axis contract was 90 deg off PX4; root cause of looped routes found and fix flight-proven

Question: why did every GNSS-on Gate 6 run loop physically while the EKF
position tracked truth?

Answer chain (evidence in
`experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson/velocity_loop_diagnosis/`
and `experiments/batches/manual_gate6_isolation_20260717/`):

1. New diagnosis script `scripts/analysis/diagnose_gate6_velocity_loop.py`:
   EKF **velocity** was decorrelated from truth (corr `0.03-0.25`, gap
   `1.6-2.4 m/s`) while GNSS pinned EKF **position** to truth. Velocity-mode
   control drove the corrupted estimate to the `0.2 m/s` setpoint, so the
   real vehicle swung at up to `3.7 m/s` -> loops with honest position plots.
   All test ratios stayed `< 1`; the EKF never rejected anything.
2. New contract-fit script `scripts/analysis/fit_flow_contract_from_truth.py`
   (regression of sent flow vs truth body velocity/rates, R^2 `0.98-0.999`):
   bridge sent `flow_x ~ -vbx/h`, `flow_y ~ -vby/h`; PX4 EKF2 fuses
   `flow_x ~ +vby/h + wx`, `flow_y ~ -vbx/h + wy`
   (`optical_flow_control.cpp:125`). The translation contract was rotated
   90 deg. `SENS_FLOW_ROT` was `0`. Gate 4 missed it because its analyzer
   checked the bridge against its own assumed signs (spawn yaw baked in),
   not against PX4's fusion convention. **Historical conclusion at the time:
   Gate 4 `axis_map: "-yx"` was superseded by `"-x-y"`. Phase 8N later
   superseded the sign part of this conclusion and identifies `"xy"` as the
   sign-correct contract.**
3. Isolation control run (`ekf2_of_ctrl: 0`, flow sent not fused, GNSS on):
   `experiments/runs/20260717_092410_phase8l_lk_gnsson_ofoff_...` —
   accepted, straight leg, truth end `10.39 m` vs intended `10.6 m`,
   hmax `0.44 m`. Route/runner/controller proven sane.
4. Fix run (`axis_map: "-x-y"`, fused, delay 140):
   `experiments/runs/20260717_094226_phase8l_lk_gnsson_axisfix140_...` —
   accepted by the runner gate; all route metrics improved about `2x`
   (truth path `50.4 m` vs `87-120 m`, endpoint error to intended `4.07 m`
   vs `7.75-28.42 m`, hmax `4.41 m` vs `8-16 m`) but the route is **still
   looped** (straightness `0.22` vs control `0.995`). Post-run contract fit
   matches PX4 translation exactly, so the residual loop comes from the
   remaining rotation-compensation leak, the takeoff-seeded heading offset,
   and/or timing. Route overlay:
   `experiments/batches/manual_gate6_isolation_20260717/diagnosis/route_overlay_gate6b.png`.

New scenario configs:
`experiments/configs/mvp/scenarios/phase8l_lk_gnsson_ofoff_flat_rural_phototex_noon.yaml`,
`experiments/configs/mvp/scenarios/phase8l_lk_gnsson_axisfix140_flat_rural_phototex_noon.yaml`.

Remaining limitations: bounded oscillation persists (truth speed mean
`0.91 m/s` vs `0.2` setpoint), `~25 deg` EKF heading offset seeded at takeoff
by early flow fusion, rotation content `~1.3x` body rate over the 100-deg FOV
(NaN-gyro onboard compensation under-removes it), variable bridge compute
latency `40-170 ms` vs fixed `EKF2_OF_DELAY`. Delay was never the primary
variable; the Gate 6 sweep varied the wrong thing.

Next action: attack the residual loop with one variable — remove the
rotation-compensation leak by restricting the LK average to the central part
of the 100-deg FOV (or sending bridge gyro integrals matched to the flow
convention), rerun the `axisfix140` scenario, and compare straightness and
path length against `0.995` / `10.45 m` (control) and `0.22` / `50.4 m`
(current fix run). Delay sweep and GNSS-loss smoke stay blocked until the
fused-flow route is straight.

## 2026-07-17 — Phase 8L Gate 6 delay sweep exposes route-control blocker

Ran the Phase 8L Gate 6 GNSS-on LK timing/delay sweep:
`experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson`.

Batch result: **Rejected** (`4 / 5` cases accepted). Delay candidates:
`60, 90, 111, 140, 180 ms`.

Key result: the sweep did **not** identify delay as the primary issue. Route
overlay plots show every Gazebo truth route is looped/curved instead of the
intended straight `+10.6 m` PX4-Y leg (`vy=0.2 m/s`, active wait target `53 s`).
The truth path lengths were `86.89-120.42 m`, or `8.2x-11.4x` the intended
straight path. Route comparison artifacts:
`experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson/route_comparison/route_comparison.md`.

Per-delay route metrics:
- `60 ms`: gate pass, truth endpoint `15.79 / -6.55 m` X/Y, endpoint error to
  intended `23.31 m`, path length `105.74 m`, hmax `12.69 m`.
- `90 ms`: gate pass, truth endpoint `-6.22 / 4.37 m`, endpoint error
  `8.80 m`, path length `86.89 m`, hmax `7.79 m`.
- `111 ms`: gate fail (`flow_fusion_rows`, `flow_active_fraction`,
  `flow_reject_ratio`, `distance_sensor_ok`), truth endpoint
  `4.42 / -9.62 m`, endpoint error `20.70 m`, path length `120.42 m`,
  hmax `13.56 m`.
- `140 ms`: gate pass, truth endpoint `-1.75 / 3.05 m`, endpoint error
  `7.75 m`, path length `93.77 m`, hmax `8.60 m`.
- `180 ms`: gate pass, truth endpoint `28.24 / 7.40 m`, endpoint error
  `28.42 m`, path length `112.35 m`, hmax `15.62 m`.

Bridge health was not the blocker in these runs: all candidates sent
`1678-1686` real flow rows at about `30.32 Hz`, with finite range fraction
`1.0` and nonzero quality. Next action is to isolate why the GNSS-on
`velocity_xy_position_z` route bends into loops before running Gate 7 or any
GNSS-loss smoke.

## 2026-07-17 — Phase 8L Gate 5 gyro transport proof accepted

Continued the Phase 8L sanity ladder through the yaw rotation/gyro gate.
Implemented an opt-in gyro path for the LK MAVLink bridge while preserving the
existing NaN-gyro baseline as the default.

Code changes:
- `src/databoss_sim/flow/px4_adapter.py` can now populate
  `integrated_xgyro/ygyro/zgyro` in `OPTICAL_FLOW_RAD`, defaulting to NaN when
  no gyro integral is supplied.
- `scripts/sim/flow_mavlink_bridge.py` now supports `--gyro-mode nan`,
  `--gyro-mode gz_body`, and `--gyro-mode gz_camera_down_y90`. The Gazebo modes
  integrate IMU angular velocity over the exact LK frame interval.
- `scripts/runner/auto_takeoff_land_pxh_truth.py` now passes the bridge gyro
  mode and Gazebo IMU topic, and can enable yaw-target local hold with
  `control.use_yaw: true`.
- `scripts/analysis/sensor_contract_report.py` now has a `rotation` gate that
  summarizes flow rates, gyro availability, gyro-rate stats, and range
  coupling.

Accepted batch:
`experiments/batches/20260717_065827_phase8l_03_rotation_gyro_baseline`
(`2 / 2` cases accepted).

NaN-gyro baseline:
`experiments/runs/20260717_065831_phase8l_rotation_yaw_nan_gyro_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- bridge real rows/rate: `1228` / `30.33 Hz`
- gyro mode: `nan`
- gyro available fraction: `0.0`
- range finite fraction/median: `1.0` / `2.4198 m`
- flow abs mean rad/s x/y: `0.2716 / 0.3102`
- truth horizontal max: `3.1961 m`
- accepted

Gazebo-gyro candidate:
`experiments/runs/20260717_071103_phase8l_rotation_yaw_gzgyro_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- bridge real rows/rate: `1225` / `30.33 Hz`
- gyro mode: `gz_camera_down_y90`
- gyro available fraction: `1.0`
- range finite fraction/median: `1.0` / `2.3317 m`
- flow abs mean rad/s x/y: `0.3283 / 0.4021`
- gyro abs mean rad/s x/y/z: `0.0689 / 0.0802 / 0.1033`
- truth horizontal max: `3.1369 m`
- accepted

Interpretation: the bridge now has a working gyro transport path comparable to
the field contract used by real optical-flow sensors. This does not yet prove
EKF optical-flow rejection improves, because the yaw-target test used position
hold and produced real lateral station-keeping motion. Treat Gate 5 as a
sensor-contract proof, not a flight-performance fix. Next: Gate 6 timing/delay
sweep and Gate 7 GNSS-on fusion proof before any GNSS-loss smoke.

## 2026-07-16 — Phase 8L sensor sanity ladder implemented

Implemented Phase 8L planning artifacts and automation scaffolding after the
rejected 8K smoke made it clear that more `vy`/LK/EKF tuning would hide the
real sensor-contract uncertainty.

Created `docs/phases/phase_08l_sensor_sanity_ladder.md`, new `phase8l_*`
scenario and batch YAMLs, and `scripts/analysis/sensor_contract_report.py`.
The report script emits `sensor_contract_report.json` / `.md` from saved run
evidence: camera texture/feature stats, range finite fraction, bridge
rate/quality/matches, flow fusion, truth metrics, and gate pass/fail. It also
supports a no-sim static audit of the DATABOSS `x500_cam_lidar_down` model,
PX4 stock `x500_flow`, the phototex world, and the bridge contract
(`OPTICAL_FLOW_RAD` with `distance=-1`, NaN gyro fields, separate
`distance_sensor`, current `axis_map: "-yx"` hypothesis).

Modified `scripts/runner/run_scenario_pxh_end_to_end.py` so the report runs
only for scenarios that opt in via `analysis.sensor_contract_report`; older
phases keep their previous acceptance behavior. Phase 8L configs require the
gate for scene, axis, fusion, and loss smoke runs. Timing sweep reports are
non-fatal so all delay candidates can complete and be compared.

Known limitation: the current offboard sender can command position/velocity
only and does not enable yaw commands, roll, pitch, or yaw-rate setpoints.
Therefore the Phase 8L rotation/gyro batch records roll/pitch/yaw-rate cases
as disabled until an attitude-setpoint sender is added.

Next command:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/analysis/sensor_contract_report.py --static-audit
```

## 2026-07-16 — Post-repair probe runs logged; Phase 8K bounded-flow candidate started

Catch-up record of the late-morning runs that followed the LK fusion repair
(none previously logged):

- `experiments/runs/20260716_112613_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  (batch `20260716_112610_phase8j_lk_fixed_50s_probe`): completed, **Rejected**.
  Fixed LK fused only a ~5 s window (t≈22–27 s, 153 samples, while GNSS was
  still on), then quality pinned to 0. Gazebo truth shows the vehicle stayed at
  ≤2.74 m true altitude but drifted off the ±120 m texture patch, rangefinder
  went `inf`, and it ended ~2.5 km off-map at −247 m while EKF height read
  +25.4 m.
- `experiments/runs/20260716_113937_phase8j_d_loss_flow_lk_flat_rural_phototex_600m_noon_pxh_takeoff_land_truth`
  (batch `20260716_113933_phase8j_lk_fixed_50s_probe_600m`): incomplete, no
  validation.md. Bridge log: 649 samples, only 133 sent, 620 primes.
- `experiments/runs/20260716_120043_phase8j_d_loss_flow_sift_flat_rural_phototex_600m_noon_pxh_takeoff_land_truth`
  (batch `20260716_120039_phase8j_all_flow_50s_600m`): incomplete, no
  validation.md.
- `experiments/runs/20260716_121246_phase8j_d_loss_flow_sift_flat_rural_phototex_600m_noon_pxh_takeoff_land_truth`
  (batch `20260716_121243_phase8j_all_flow_50s_600m`): completed, **Rejected**.
  Max height 4.71 m, `distance_sensor` max 60.78 m vs height diff 56.07 m —
  rangefinder/height disagreement on the 600 m world.
- `experiments/runs/20260716_122747_phase8j_d_loss_flow_lk_flat_rural_phototex_600m_noon_pxh_takeoff_land_truth`:
  died ~3 min in. PX4 booted and set home but never armed; `flow_bridge.log`
  is 0 bytes (bridge never came up, so the fail-closed pre-arm gate aborted);
  console log ballooned to 95 MB of empty `pxh>` prompts. 600 m-world launch
  reliability is an open issue, out of scope for 8K.

Decision: the 112613 forensics show the remaining divergence mechanism is the
bridge's own protective gating, not LK tracking quality. The middle third of
processed frames had mean quality 0.0 with mean 79 tracked matches — the
`lk_max_flow_rate_rad_s: 1.2` gate (which zero-qualities the sample AND
re-primes the tracker) was the zeroing mechanism, and `reset_on_unsent: true`
plus the range send-gates (`inf` fails the finite check) latched the stream
dead after patch escape. Stock's contract is always-publish with honest
quality; PX4 guards via `SENS_FLOW_MAXR` 8, `EKF2_OF_QMIN`, and
`EKF2_OF_GATE`.

Started **Phase 8K bounded-flow candidate**
(`docs/phases/phase_08k_bounded_flow_candidate.md`): YAML-only bridge
reconfiguration to the stock contract — `lk_max_flow_rate_rad_s` 1.2→7.4,
`send_min_quality` 20→0, `send_min_matches` 8→0, `reset_on_unsent`
true→false, `send_min/max_range_m` removed. New scenario
`experiments/configs/mvp/scenarios/phase8k_d_loss_flow_lk_flat_rural_phototex_noon.yaml`
and batch
`experiments/configs/mvp/batches/phase8k_lk_bounded_50s_replicates.yaml`
(3x 50 s outage on the 240 m world, compared against the accepted stock 3/3
batch `20260716_081558`). Results will be appended when the batch and the
comparison report finish.

## 2026-07-16 — Phase 8J LK fusion repaired after failed live-LK comparison

The initial Phase 8J live LK 3x long runs were not valid estimator judgments:
they published raw `sensor_optical_flow`, but PX4 never created
`vehicle_optical_flow`, so EKF2 had 0 optical-flow aid rows and 0 fused/rejected
samples. Root cause was startup timing. The first camera-derived LK flow arrived
after arming, while PX4's `sensors` module only instantiates the
`VehicleOpticalFlow` worker while disarmed.

Fixes implemented:
- Bridge startup primes: zero-quality `OPTICAL_FLOW_RAD` is sent immediately
  after MAVLink connection, independent of camera frames.
- Runner pre-arm gate now fails closed when required MAVLink flow rows are not
  observed.
- LK estimator now uses median/MAD inlier displacement and a max flow-rate gate.
- LK scenario now uses `rate_hz: 40` to avoid 30 Hz camera -> 15 Hz bridge
  aliasing.

Accepted proof run:
`experiments/runs/20260716_111242_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`.

Evidence: 396 `sensor_optical_flow` rows, 395 `vehicle_optical_flow` rows, 332
`estimator_aid_src_optical_flow` rows, 150 fused / 0 rejected, and
`cs_opt_flow` active fraction 0.278. Startup primes: 72 rows; real LK sent
rows: 150. Repair report and plots:
`experiments/comparisons/phase8j_lk_fusion_repair/report.md`.

Next: rerun the long 3x comparison with fixed LK before claiming LK is
flight-better than SIFT.

## 2026-07-14 — Phase 8I flat A/B/C/D measured; live-flow D fails current thesis gate

Built and ran the Phase 8I flat phototex A/B/C/D matrix using the preserved
webgz setup: browser/proxy `9002`, runner raw bridge `9003`. Fixed runner
issues encountered during the campaign: stale `/tmp/px4-sock-0` blocked PX4
startup, airborne-duration waits now use `vehicle_local_position.timestamp`
instead of the sometimes-stale `vehicle_land_detected.timestamp`, and
observation-mode GNSS-loss cases no longer wait a full clean-landing timeout.
Batch cleanup now also kills stale offboard/external-odom/flow/camera helper
processes.

Flat results (`flat_rural_phototex_noon`, combined camera+TF03 vehicle,
2.5 m AGL, slow +Y local hold):
- A GNSS-on `20260714_070542`: accepted, horizontal mean/max/end
  0.062/0.116/0.098 m.
- B GNSS-loss/no-aiding `20260714_071532`: deliberate failure anchor; runner
  rejected range proof after runaway, but manual postprocess/align succeeded.
  Horizontal mean/max/end 23.792/174.797/73.679 m.
- C GNSS-loss/ideal odom `20260714_074250`: accepted after repair. The
  combined lidar vehicle cannot use EV height here; C now uses
  `EKF2_EV_CTRL=5` (EV horizontal position+velocity) and `EKF2_HGT_REF=2`
  (TF03 height). Horizontal mean/max/end 0.142/1.009/0.924 m.
- D GNSS-loss/live flow `20260714_075214`: bridge delivered (`455` sent rows)
  and PX4 fused some flow (`sensor_optical_flow_rows=449`,
  `cs_opt_flow_active_fraction=0.4569`, `flow_fused_count=146`), but the
  analyzer printed `flow_fusion_ok=False` due zero-heavy quality and rejection
  ratio (`flow_quality_zero_fraction=0.5256`,
  `flow_rejected_over_fused=0.3493`). D diverged worse than B:
  horizontal mean/max/end 100.986/246.245/225.512 m, height mean 39.837 m.

Conclusion: 8I flat-world comparison is measured, and current live-flow D is
not accepted. Do not spend terrain A/B/C/D until D is repaired on the favorable
flat phototex world.

## 2026-07-10 - Phase 8C - Downward camera visual proof corrected

Report:
docs/phases/phase_08c_downward_camera_proof.md

Accepted visual-proof runs:
- experiments/runs/20260710_063125_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth
- experiments/runs/20260710_063752_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth

Result:
Both generated worlds accepted `gz_x500_mono_cam_down`, captured a camera image after the airborne hover gate, rendered generated ground from the saved `camera_image_sample.txt`, landed cleanly, postprocessed Gazebo truth, and passed EKF-vs-truth alignment.

Important fix:
The earlier Phase 8C samples were captured immediately after `commander takeoff`, before the aircraft climbed. Rendering those samples showed only Gazebo background. The runner now probes the camera after the PX4-time airborne hover wait and before `commander land`.

Decision:
Phase 8C is now visually proven, not just topic-publication proven. Move next to Phase 8D TF03-style downward rangefinder proof.

## 2026-07-09 - Phase 8B - Generated worlds physically launchable

Proof folder:
experiments/runs/20260709_142806_phase8b_world_launch_proof

Result:
Both generated Phase 8B worlds launched independently in Gazebo headless mode as user `px4`, and the PX4 Gazebo `x500` model spawned successfully in each one.

Worlds accepted:
- flat_rural_high_texture_noon
- flat_rural_low_texture_noon

Evidence:
- `/world/<world>/create` service appeared for each world
- `gz.msgs.EntityFactory` returned `data: true` for each x500 spawn
- x500 appeared in Gazebo model list, pose topic, and model topics

Decision:
Phase 8B has moved from generated-and-valid to physically launchable. Remaining optional Phase 8B polish is visual screenshot/GUI confirmation of high-texture vs low-texture difference and direct PX4 SITL world-selection integration.

## 2026-07-09 - Phase 8B - Generated worlds PX4-flight-compatible

Report:
docs/phases/phase_08b_generated_world_px4_flight_proof.md

Accepted runs:
- experiments/runs/20260709_144318_phase8b_px4_flight_flat_rural_high_texture_noon_pxh_takeoff_land_truth
- experiments/runs/20260709_144526_phase8b_px4_flight_flat_rural_low_texture_noon_pxh_takeoff_land_truth

Result:
Both generated Phase 8B worlds accepted a PX4 SITL x500 takeoff, short hover, land, ULog copy, Gazebo truth postprocess, and EKF-vs-truth alignment.

Important fix:
Standalone generated worlds need spherical coordinates for Gazebo NavSat/global-position validity. The runner now sets PX4 home origin (`PX4_HOME_LAT=47.397742`, `PX4_HOME_LON=8.545594`, `PX4_HOME_ALT=488.0`) and runs truth/odometry Gazebo topic readers inside the same `GZ_PARTITION`.

Decision:
Phase 8B worlds are now PX4-flight-compatible. Move next to Phase 8C downward camera proof.

## 2026-07-02 - Phase 0 - Environment proof

Run folder:
experiments/runs/20260702_125639_phase00_env_proof_x500_takeoff_land

Result:
PX4 SITL + Gazebo headless successfully ran default X500. Vehicle armed, took off, EKF local position became valid, landed, disarmed, and produced a ULog.

Next:
Phase 1 GNSS ON baseline hover test.

## 2026-07-02 - Phase 1 - GNSS ON baseline

Run folder:
experiments/runs/20260702_133535_phase01_gnss_on_x500_hover60_alt2p5

Result:
GNSS ON X500 hover baseline log saved successfully.

Log:
experiments/runs/20260702_133535_phase01_gnss_on_x500_hover60_alt2p5/logs/flight.ulg

Next:
Phase 2 log extraction.

## 2026-07-02 - Phase 2 - ULog extraction

Run folder:
experiments/runs/20260702_133535_phase01_gnss_on_x500_hover60_alt2p5

Result:
ULog extraction succeeded. EKF local position, GPS, attitude, IMU, barometer, magnetometer, estimator status, and estimator innovations were exported to CSV.

Important baseline metrics:
- Duration: about 95.7 s
- GPS fix type: 3
- Satellites used: 10
- Max horizontal movement from start: about 0.056 m
- Estimated altitude reached: about 2.51 m

Next:
Create basic plots, then start Phase 3 GNSS OFF hover experiment.

## 2026-07-02 - Phase 3 attempt - GNSS loss not confirmed

Run folder:
experiments/runs/20260702_143053_phase03_gnss_loss_x500_failure_gps_off

Result:
Run saved and analyzed, but GNSS loss was not confirmed. GPS remained healthy with fix_type 3, 10 satellites, and gps_check_fail_flags 0.

Decision:
Do not use this run as the final GNSS OFF comparison.

Next:
Repeat Phase 3 with SYS_FAILURE_EN=1 before `failure gps off`.

## 2026-07-02 - Phase 3 attempt - SYS_FAILURE_EN GPS failure not confirmed

Run folder:
experiments/runs/20260702_143310_phase03_gnss_loss_x500_sys_failure_en_gps_off

Result:
PX4 accepted `failure gps off`, but the analyzed log still showed healthy GPS. This run is not accepted as the real GNSS OFF experiment.

Next:
Run a short method-discovery test to find a GNSS-loss method that visibly changes vehicle_gps_position or EKF GPS aiding.

## 2026-07-02 - Phase 3A - GNSS loss default PX4 failsafe

Run folder:
experiments/runs/20260702_144947_phase03_gnss_loss_x500_sim_gps_used0

Result:
GNSS loss was successfully confirmed using `param set SIM_GPS_USED 0`.

Evidence:
- GPS fix_type changed from 3 to 0
- satellites_used changed from 10 to 0
- eph changed from about 0.9 to 100
- epv changed from about 1.78 to 100
- gps_check_fail_flags changed to include 57 and 63

PX4 response:
PX4 entered failsafe blind land shortly after GNSS became invalid.

Decision:
This run is accepted as the default PX4 GNSS-loss failsafe behavior test.
It is not accepted as the 60-second GNSS-loss hover drift test.

Next:
Phase 3B will inspect and adjust SITL-only failsafe/navigation-loss settings to allow continued flight after GNSS loss.

## 2026-07-02 - Phase 3A - GNSS loss default PX4 failsafe

Run folder:
experiments/runs/20260702_144947_phase03_gnss_loss_x500_sim_gps_used0

Result:
GNSS loss was successfully confirmed using `param set SIM_GPS_USED 0`.

Evidence:
- GPS fix_type changed from 3 to 0
- satellites_used changed from 10 to 0
- eph changed from about 0.9 to 100
- epv changed from about 1.78 to 100
- gps_check_fail_flags changed to include 57 and 63

PX4 response:
PX4 entered failsafe blind land shortly after GNSS became invalid.

Decision:
This run is accepted as the default PX4 GNSS-loss failsafe behavior test.
It is not accepted as the 60-second GNSS-loss hover drift test.

Next:
Phase 3B will inspect and adjust SITL-only failsafe/navigation-loss settings to allow continued flight after GNSS loss.

## 2026-07-03 - Phase 3B - GNSS loss with SITL-only failsafe delay

Run folder:
experiments/runs/20260703_060645_phase03b_gnss_loss_x500_failsafe_delay

Result:
Accepted GNSS-loss drift-behavior run.

GNSS loss evidence:
- fix_type changed from 3 to 0
- satellites_used changed from 10 to 0
- eph changed from about 0.9 to 100
- epv changed from about 1.78 to 100
- gps_check_fail_flags included 57 and 63

SITL-only parameters changed:
- NAV_DLL_ACT: 2 -> 0
- COM_POS_FS_EPH: 5 -> 200
- COM_POS_LOW_ACT: 3 -> 0
- EKF2_NOAID_TOUT: 5000000 -> 120000000

Important metrics:
- Duration: about 310.4 s
- Max horizontal EKF movement from start: about 143.79 m
- Mean horizontal EKF movement from start: about 9.35 m

Decision:
Use this as the accepted Phase 3B GNSS-loss run.

Next:
Create GNSS ON vs GNSS LOSS comparison script, then move to Phase 4 ground truth.

## 2026-07-03 - GNSS ON vs Phase 3B comparison

Comparison folder:
experiments/comparisons/gnss_on_vs_phase03b_gnss_loss

Result:
First manual comparison completed.

Key metrics:
- GNSS ON max horizontal EKF movement from start: about 0.056 m
- GNSS-loss max horizontal EKF movement from start: about 143.790 m
- GNSS-loss GPS loss time detected at about 186.352 s
- GNSS-loss fix_type changed 3 -> 0
- GNSS-loss satellites_used changed 10 -> 0
- GNSS-loss eph/epv changed to 100

Decision:
Comparison accepted as EKF/local-position comparison.

Limitation:
No Gazebo ground truth yet. Phase 4 must compare EKF estimate against true simulated vehicle position.

## 2026-07-03 - QGroundControl over Tailscale connected

Result:
QGroundControl on Mac successfully connected to PX4 SITL on server over Tailscale.

Mac Tailscale IP:
100.109.200.5

Working PX4 command:
mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x

Validation:
PX4 mavlink status showed GCS heartbeat valid, partner IP 100.109.200.5, received QGC messages, and zero dropped packets.

---

# 2026-07-03 — Phase 3 QGC GNSS Experiments Completed

## Status

Phase 3 QGC-connected GNSS experiments are complete.

Accepted runs:

- Phase 3C: QGC-connected GNSS ON baseline
- Phase 3D-1: QGC-connected GNSS loss with default PX4 failsafe
- Phase 3D-2: QGC-connected GNSS loss with delayed failsafe

Rejected run:

- First Phase 3D-2 attempt was rejected because the GPS-invalid period happened at startup instead of after a clean valid-GPS flight period.

## Phase 3C — QGC-connected GNSS ON baseline

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_080636_phase03c_qgc_gnss_on_x500_hover60

Result:

ACCEPTED.

Key metrics:

- Duration: 70.232 s
- GPS fix_type: 3 to 3
- Satellites used: 10 to 10
- eph: 0.9 stable
- epv: 1.78 stable
- Max horizontal EKF movement from start: 0.0546 m
- Mean horizontal EKF movement from start: 0.0201 m
- gps_check_fail_flags_unique: [0]
- filter_fault_flags_unique: [0]

Interpretation:

Clean QGC-connected GNSS ON hover baseline. GPS stayed healthy and EKF horizontal movement stayed very small.

## Phase 3D-1 — QGC-connected GNSS loss default failsafe

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_081343_phase03d1_qgc_gnss_loss_default_failsafe

Result:

ACCEPTED.

Key metrics:

- Duration: 96.628 s
- Post-GNSS-loss duration: 12.780 s
- GPS fix_type: 3 to 0
- Satellites used: 10 to 0
- eph: 0.9 to 100
- epv: 1.78 to 100
- Max horizontal EKF movement from start: 0.209 m
- Max horizontal EKF movement from GNSS-loss point: 0.215 m
- gps_check_fail_flags_unique: [0, 57, 63]
- filter_fault_flags_unique: [0]

Interpretation:

GNSS loss was successfully injected while QGroundControl was connected. PX4 default behavior was immediate blind-land failsafe.

## Phase 3D-2 rejected attempt

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_081923_phase03d2_qgc_gnss_loss_failsafe_delay

Result:

REJECTED.

Reason:

SIM_GPS_USED was still 0 at startup, so GPS was invalid at the beginning of the log. The satellites plot showed satellites_used = 0 only at startup, then satellites_used became 10 and stayed 10. This was not a clean post-takeoff GNSS-loss drift test.

Replacement accepted run:

/opt/databoss_px4_sim/experiments/runs/20260703_082803_phase03d2_qgc_gnss_loss_failsafe_delay_attempt2

## Phase 3D-2 — QGC-connected GNSS loss with delayed failsafe

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_082803_phase03d2_qgc_gnss_loss_failsafe_delay_attempt2

Result:

ACCEPTED.

Parameters used during test:

- SIM_GPS_USED = 10 initially
- NAV_DLL_ACT = 0
- COM_POS_FS_EPH = 200
- COM_POS_LOW_ACT = 0
- EKF2_NOAID_TOUT = 120000000
- GNSS loss trigger: SIM_GPS_USED = 0

Restored after test:

- SIM_GPS_USED = 10
- NAV_DLL_ACT = 2
- COM_POS_FS_EPH = 5
- COM_POS_LOW_ACT = 3
- EKF2_NOAID_TOUT = 5000000

Key metrics:

- Duration: 237.652 s
- GPS loss time: 85.632 s
- Duration after GPS loss: 152.024 s
- GPS fix_type: 3 to 0
- Satellites used: 10 to 0
- eph: 0.9 to 100
- epv: 1.78 to 100
- GPS velocity max: 36.824 m/s
- Max horizontal EKF movement from start: 354.049 m
- Mean horizontal EKF movement from start: 74.828 m
- Max horizontal EKF movement from GNSS-loss point: 354.050 m
- Mean horizontal EKF movement from GNSS-loss point: 116.951 m
- End horizontal EKF movement from GNSS-loss point: 318.671 m
- gps_check_fail_flags_unique: [0, 57, 63]
- filter_fault_flags_unique: [0]

Interpretation:

This is the accepted QGC-connected delayed-failsafe GNSS-loss drift run. GNSS loss happened after a valid GPS period, the drone stayed airborne for about 152 seconds after GNSS loss, and PX4 EKF/local-position movement became very large.

Important limitation:

This is still EKF/local-position movement, not true physical position error. Phase 4 must add Gazebo ground truth.

## QGC Phase 3 comparison

Comparison folder:

/opt/databoss_px4_sim/experiments/comparisons/qgc_phase03c_03d1_03d2

Files:

- comparison_summary.md
- comparison_metrics.json
- comparison_metrics.csv
- compare_max_horizontal_from_start.png
- compare_post_loss_horizontal.png

Comparison summary:

| Run | Duration s | GPS loss detected | Post-loss duration s | Max horizontal from start m | Max horizontal from loss m |
|---|---:|---|---:|---:|---:|
| 3C_QGC_GNSS_ON | 70.232 | False | N/A | 0.055 | N/A |
| 3D1_QGC_GNSS_LOSS_DEFAULT_FAILSAFE | 96.628 | True | 12.780 | 0.209 | 0.215 |
| 3D2_QGC_GNSS_LOSS_DELAYED_FAILSAFE | 237.652 | True | 152.024 | 354.049 | 354.050 |

## Phase 3 conclusion

QGroundControl over Tailscale is working with PX4 SITL.

GNSS ON baseline remains stable with QGC connected.

GNSS loss injection with SIM_GPS_USED=0 works with QGC connected.

Default PX4 behavior after GNSS loss is blind-land failsafe.

Delayed-failsafe SITL configuration allows observation of large EKF/local-position movement after GNSS loss.

Next phase:

Phase 4 — add Gazebo ground truth logging and compare PX4 EKF local position against true simulated vehicle position.


---

# 2026-07-03 — Phase 4 Ground Truth Added

## Status

Phase 4A, 4B, 4C, and 4D completed.

## Phase 4A — Ground-truth topic discovery

Result:

ACCEPTED.

Canonical Gazebo ground-truth topic:

/world/default/dynamic_pose/info

Accepted model name:

x500_0

Rejected candidate:

/model/x500_0/odometry_with_covariance

Reason:

No publisher existed for /model/x500_0/odometry_with_covariance.

The accepted topic publishes gz.msgs.Pose_V and includes:

- header stamp sec/nsec
- pose name: x500_0
- position x/y/z
- orientation x/y/z/w

Coordinate note:

- Gazebo z is up-positive
- PX4 local z is NED-style, so altitude appears as negative z during takeoff
- For comparison, Gazebo height is treated as up-positive and PX4 height is computed as -local_z relative to start

## Phase 4B/4C — GNSS ON ground-truth alignment

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_084723_phase04b_ground_truth_gnss_on_x500_hover60

Result:

ACCEPTED.

Ground-truth rows:

7104

Gazebo truth duration:

140.616 s

PX4 ULog duration:

209.668 s

Alignment:

- PX4 takeoff crossing at height > 0.5 m: 144.020 s
- Gazebo takeoff crossing at height > 0.5 m: 59.700 s
- Time offset, Gazebo relative time to PX4 time: 84.320 s
- Overlap duration: 125.348 s
- Aligned samples: 15669

GNSS health:

- fix_type: 3 to 3
- satellites_used: 10 to 10
- gps_check_fail_flags_unique: [0]
- filter_fault_flags_unique: [0]

EKF vs Gazebo truth error:

- Horizontal error max: 0.163419 m
- Horizontal error mean: 0.028110 m
- Horizontal error end: 0.051933 m
- 3D error max: 0.349899 m
- 3D error mean: 0.035511 m
- 3D error end: 0.073491 m

Interpretation:

GNSS ON alignment works. EKF error against Gazebo truth stayed small, so the alignment method is accepted.

## Phase 4D — GNSS loss EKF vs Gazebo ground truth

Run:

/opt/databoss_px4_sim/experiments/runs/20260703_085326_phase04d_ground_truth_gnss_loss_failsafe_delay

Result:

ACCEPTED.

Ground-truth rows:

10877

Gazebo truth duration:

215.100 s

PX4 ULog duration:

224.824 s

GNSS loss validation:

- fix_type: 3 to 0
- satellites_used: 10 to 0
- eph: 0.9 to 100
- epv: 1.78 to 100
- gps_check_fail_flags_unique: [0, 57, 63]
- filter_fault_flags_unique: [0]

ULog EKF/local-position summary:

- Max horizontal EKF movement from start: 118.655638 m
- Mean horizontal EKF movement from start: 6.729084 m
- End EKF x: -2.413654 m
- End EKF y: -20.986320 m

Post-GNSS-loss EKF error vs Gazebo truth:

- Horizontal error max: 444.165833 m
- Horizontal error mean: 165.164637 m
- Horizontal error median: 155.547827 m
- Horizontal error end: 411.531472 m
- Height error max absolute: 0.870836 m
- Height error mean absolute: 0.148292 m
- Height error end absolute: 0.759841 m
- 3D error max: 444.166680 m
- 3D error mean: 165.164842 m
- 3D error end: 411.532174 m

Post-GNSS-loss movement from loss point:

PX4 EKF horizontal movement:

- Max: 118.676385 m
- Mean: 12.172998 m
- End: 21.139251 m

Gazebo truth horizontal movement:

- Max: 421.464765 m
- Mean: 162.047284 m
- End: 421.242142 m

Interpretation:

This is the first accepted true EKF-error GNSS-loss experiment.

After GNSS loss, the simulated vehicle physically moved about 421 m from the loss point, while PX4 EKF estimated only about 21 m end movement. The final horizontal EKF error versus Gazebo ground truth was about 411.53 m.

This is now the strongest research result so far because it measures true position error against Gazebo ground truth.

## Phase 4 conclusion

Gazebo ground truth logging is working.

PX4 ULog and Gazebo truth can be aligned using takeoff height crossing.

GNSS ON EKF error versus truth stays small.

GNSS loss with delayed failsafe produces very large true EKF position error.

Next step:

Package Phase 4 comparison and then prepare Phase 5, where aiding sources such as optical flow, VIO, LiDAR, or LiDAR-SLAM will be added and compared against this GNSS-loss baseline.

---

# 2026-07-03 — Phase 4 Ground Truth Comparison Package Created

Comparison folder:

/opt/databoss_px4_sim/experiments/comparisons/phase04_ground_truth_gnss_on_vs_loss

Files:

- comparison_summary.md
- comparison_metrics.csv
- comparison_metrics.json
- compare_horizontal_error_max.png
- compare_horizontal_error_mean.png
- compare_horizontal_error_end.png
- compare_3d_error_end.png

## Main comparison

GNSS ON ground-truth hover:

- Mean horizontal EKF error vs Gazebo truth: 0.028110 m
- Max horizontal EKF error vs Gazebo truth: 0.163419 m
- End horizontal EKF error vs Gazebo truth: 0.051933 m

GNSS loss delayed-failsafe run:

- Mean post-loss horizontal EKF error vs Gazebo truth: 165.164637 m
- Max post-loss horizontal EKF error vs Gazebo truth: 444.165833 m
- End post-loss horizontal EKF error vs Gazebo truth: 411.531472 m

## Key result

With GNSS ON, PX4 EKF stayed close to Gazebo truth.

After GNSS loss, the vehicle physically moved about 421 m from the GNSS-loss point, while PX4 EKF estimated only about 21 m of end horizontal movement from the loss point.

Final horizontal EKF error versus Gazebo truth was about 411.53 m.

This is now the main baseline result for future aiding-source comparisons.

---

# 2026-07-08 — Phase 8A 120 s A/B/C Long-Hover Comparison And QGC Runaway Analysis

Batch:

```text
/opt/databoss_px4_sim/experiments/batches/
20260708_075652_phase8a_position_height_three_case_120s
```

Cases:

```text
A: 20260708_075655_phase8a_compare_2p5m_gnss_on_no_aiding_pxh_takeoff_land_truth
B: 20260708_080114_phase8a_compare_2p5m_gnss_loss_no_aiding_pxh_takeoff_land_truth
C: 20260708_080532_phase8a_compare_2p5m_gnss_loss_external_position_height_pxh_takeoff_land_truth
```

Settings:

- Altitude: 2.5 m AGL.
- Hover: 120 s.
- GNSS loss: 10 s after takeoff.
- Post-loss observation: 110 s.
- Metrics comparison window: until `commander land`.
- Case C: external position + height aiding, `EKF2_EV_CTRL=3`.

## Batch result

All three cases were accepted by the automation.

QGC was connected for the run.

The EKF-vs-truth comparison now stops at the `commander land` command, so the long hover is preserved while post-land-command no-GNSS landing behavior does not pollute hover metrics.

## Cropped EKF-vs-truth metrics

| Case | Horizontal mean m | Horizontal max m | Horizontal end m |
|---|---:|---:|---:|
| A GNSS ON | 0.033 | 0.112 | 0.022 |
| B GNSS loss, no aiding | 88.299 | 332.347 | 164.779 |
| C GNSS loss, external position + height | 12.607 | 70.889 | 16.519 |

Interpretation:

Case C improved EKF-vs-Gazebo-truth agreement compared with Case B.

However, Case C did not prove stable station-keeping.

## Case C QGC runaway

QGroundControl showed the aircraft walking kilometers away from the takeoff point.

The logs confirm this was not just a QGC rendering issue:

```text
vehicle_visual_odometry rows: 5417
estimator_aid_src_ev_pos rows: 5425
estimator_aid_src_ev_hgt rows: 5425
EV height fusion: stable for essentially the whole run
EV velocity fusion: disabled, cs_ev_vel stayed false
xy_reset_counter: 4 -> 167
Gazebo truth / QGC displacement by end: about 9.0 km
```

External odometry bridge endpoint:

```text
x: -5156.99 m
y:  7425.20 m
horizontal distance: about 9040 m
```

This means the external odometry bridge followed Gazebo truth while the simulated aircraft physically ran away. The bridge path and QGC path agree.

## Root-cause hypotheses from logs

1. `EKF2_EV_DELAY=0` was not correct for the live bridge.

Observed aid delay in ULog:

```text
about 0.158 s
```

At tens of meters per second, this delay can create multi-meter horizontal innovations and repeated rejection/reset behavior.

2. External velocity is still not fused.

Current accepted comparison policy:

```text
EKF2_EV_CTRL=3
```

Meaning:

```text
fuse horizontal EV position
fuse vertical EV position/height
do not fuse EV velocity
do not fuse EV yaw
```

3. EKF-vs-truth metrics and station-keeping drift are different questions.

The current metrics show how well PX4 estimate matches Gazebo truth. They do not by themselves say whether the aircraft held the commanded hover point.

4. AUTO_LOITER/global-position behavior may not be the right hold mode for a local-only aiding test after GNSS loss.

If delay repair does not stop the runaway, test a local-frame hold method before declaring the external odometry source itself bad.

## Applied follow-up change

Case C scenario now includes:

```text
aiding.ekf2_ev_delay_ms: 160
```

The runner now applies that value to `EKF2_EV_DELAY` prelaunch and at runtime, and records it in the run status JSON.

Additional diagnostics added for the next run:

- `ekf_vs_ground_truth_metrics.json/md` now includes station-keeping displacement from the first aligned sample in the comparison window.
- `ekf_vs_ground_truth_aligned.csv` now includes PX4 and Gazebo horizontal displacement columns.
- Run status now records EV position/height rejected counts and `xy_reset_counter` delta.
- Batch metric summaries now include EV control, EV delay, EV position rejection count, XY reset delta, and truth drift end distance.

## Superseded next run

This next-run note was executed and then superseded by the Case D hard-fail below. The current next action is EV velocity source repair.

Original checklist:

- EV position rejection count.
- EV height fusion count.
- `xy_reset_counter`.
- QGC/Gazebo drift from the hover point.
- New truth station-keeping drift metric.
- EKF-vs-truth metrics until `commander land`.

Do not enable `EKF2_EV_CTRL=7` until velocity frame, timestamp, covariance, and innovation gates pass a controlled motion test.

# 2026-07-08 — Phase 8A 160 ms delay rerun and Case D preparation

The 120 s A/B/C batch was rerun with QGC connected and Case C `EKF2_EV_DELAY=160 ms`:

```text
experiments/batches/20260708_091923_phase8a_position_height_three_case_120s
```

All three cases were accepted by automation.

## Result until commander land

| Case | EKF/truth H mean m | EKF/truth H max m | Gazebo station drift end m |
|---|---:|---:|---:|
| A GNSS ON | 0.031 | 0.097 | 0.033 |
| B GNSS loss, no aiding | 101.926 | 414.744 | 412.705 |
| C GNSS loss, EV position + height | 9.242 | 57.602 | 3729.436 |

Case C improved EKF-vs-truth agreement versus no aiding, but station-keeping got much worse than Case B. This means the external odometry made the estimate follow the runaway better; it did not make the aircraft hold position.

Case C ULog evidence:

```text
EKF2_EV_CTRL: 3
EKF2_EV_DELAY: 160 ms
vehicle_visual_odometry rows: 5412
EV position fused count: 1191
EV position rejected count: 4223
EV height fused count: 5415
EV height rejected count: 0
EV velocity active count: 0
xy_reset_counter: 4 -> 165
```

Timeline evidence:

```text
commander takeoff command: t_rel 48.284 s
commander land command: t_rel 168.056 s
GNSS position/velocity aiding dropped: about t_rel 65.34 s
first EV position rejection: t_rel 67.552 s
EV height stayed fused
EV velocity stayed disabled
```

Interpretation:

- Delay tuning helped estimator/truth agreement but did not solve hover.
- EV horizontal position still suffered repeated rejection and local XY resets.
- AUTO_LOITER setpoints were dragged along with local-frame resets, so this was not a clean original-point hover hold.
- Since EV velocity was not fused, the next controlled diagnostic is position + height + velocity, still with yaw disabled.

Applied follow-up changes:

- Run status now records EV velocity aid rows, active count, fused count, and rejected count.
- Batch metrics now show EV velocity activity/rejection alongside EV position rejection and truth drift.
- Added Case D scenario:

```text
experiments/configs/mvp/scenarios/phase8a_compare_2p5m_gnss_loss_external_position_height_velocity.yaml
```

- Added four-case batch:

```text
experiments/configs/mvp/batches/phase8a_position_height_velocity_four_case_120s.yaml
```

Superseded command from before the Case D hard-fail:

```bash
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py experiments/configs/mvp/batches/phase8a_position_height_velocity_four_case_120s.yaml --only case_d_gnss_loss_external_position_height_velocity --continue-on-fail
```

# 2026-07-08 — Case D EV velocity hard-fail

Case D was run and rejected:

```text
experiments/runs/20260708_094705_phase8a_compare_2p5m_gnss_loss_external_position_height_velocity_pxh_takeoff_land_truth
```

The normal status/metrics files were not produced because Gazebo aborted before postprocess/align completed. PX4 did write a ULog in rootfs:

```text
/opt/sim_px4/PX4-Autopilot/build/px4_sitl_default/rootfs/log/2026-07-08/09_47_19.ulg
```

Observed failure:

```text
GNSS disabled: SIM_GPS_USED 0
Preflight Fail: Attitude failure (roll)
Preflight Fail: Imbalanced propeller detected
Gyro 0 clipping, not safe to fly!
mc_pos_control invalid setpoints
Gazebo motor aliasing warnings
Gazebo ODE collision assertion abort
```

ULog/bridge evidence:

```text
cs_ev_vel first true: t_rel 26.408 s
EV velocity fused: 820
EV velocity rejected: 414
first EV velocity rejection: t_rel 56.168 s
last EV velocity rejection test ratios: x 412.532, y 450.860, z 3036.095
first bridge speed > 20 m/s: sim_time 69.340
max bridge speed: 320.654 m/s
vehicle_local_position velocity near failure: about (478992, 386866, 60932) m/s
```

Conclusion:

```text
Current finite-difference EV velocity is unsafe for EKF fusion.
Case D is parked.
Do not rerun EKF2_EV_CTRL=7 long-hover tests until velocity source repair is complete.
```

Applied safety changes:

- `auto_takeoff_land_pxh_truth.py` now refuses external velocity fusion unless explicitly unlocked with `--allow-experimental-ev-velocity` or scenario `allow_experimental_velocity_fusion: true`.
- `run_batch_matrix_pxh.py` now skips disabled cases and rejects `--only` for disabled cases.
- Case D is marked disabled in `phase8a_position_height_velocity_four_case_120s.yaml`.
- A run-level failure summary was added at:

```text
experiments/runs/20260708_094705_phase8a_compare_2p5m_gnss_loss_external_position_height_velocity_pxh_takeoff_land_truth/case_d_failure_summary.md
```

Next work:

```text
Replace finite-difference bridge velocity with native/validated Gazebo velocity.
Add velocity sanity caps before feeding EKF2.
Run a short controlled velocity validation before returning to GNSS-loss long-hover tests.
```

# 2026-07-08 — ABC-first repair: Case C local hold

Reasoning:

```text
The previous Case C improved EKF-vs-truth error but still drifted kilometers in Gazebo truth.
That means external position/height aiding was reaching EKF2, but the vehicle was not being commanded by a robust local-position hold after global position became invalid.
ABC should therefore test station-keeping with a local control command, not only estimator agreement.
```

Implemented for the next ABC run:

- Added `scripts/runner/send_offboard_local_position_setpoint_mavlink.py`.
- Case C now uses `control.mode: offboard_local_position_hold`.
- The runner streams `SET_POSITION_TARGET_LOCAL_NED`, warms up, sends `commander mode offboard`, then disables GNSS.
- The Case C pre-loss timing remains 10 s total: 5 s after takeoff, 2 s setpoint warmup, 3 s after Offboard switch.
- The live Gazebo odometry bridge now sends zero velocity by default. Finite-difference velocity is explicit and capped.
- Case D / `EKF2_EV_CTRL=7` remains parked.

Run ABC next as `px4`:

```bash
cd /opt/databoss_px4_sim
venv/bin/python scripts/runner/run_batch_matrix_pxh.py experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml --continue-on-fail
```

Expected analysis:

```text
A should hold with GNSS.
B should drift/fail station-keeping after GNSS loss.
C should show whether external position+height plus Offboard local hold reduces station drift relative to B.
Compare only until commander land.
```

# 2026-07-08 — ABC rerun result and Case C repair

Run:

```text
experiments/batches/20260708_113455_phase8a_position_height_three_case_120s
```

Result:

```text
A accepted. Station drift end: about 0.063 m.
B accepted mechanically but drifted badly after GNSS loss. Station drift end: about 1253 m.
C entered Offboard in ULog, but the runner rejected it because the first status listener snapshot happened just before `nav_state=14`.
```

Case C evidence:

```text
Offboard nav_state=14 from about 56.656 s to 169.344 s.
SET_POSITION_TARGET_LOCAL_NED setpoint stayed at (0, 0, -2.5) during Offboard.
EV position fused initially, then horizontal EV position started rejecting at about 68.840 s.
At 80 s, EV position test ratios were about x=30.5, y=23.3.
EV position rejected count: 4312.
XY reset counter delta: 165.
```

Fixes applied:

- The runner now polls for Offboard mode instead of taking a single status snapshot.
- The loose `position_std_m: 1.0` plus `EKF2_EVP_GATE: 10` experiment failed fast and was rolled back.
- Case C is back to conservative EV position noise with `EKF2_EV_CTRL=3`.
- Next repair is horizontal velocity observability; do not keep widening EV position gates.

Next run should be Case C only first:

```bash
cd /opt/databoss_px4_sim
venv/bin/python scripts/runner/run_batch_matrix_pxh.py experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml --only case_c_gnss_loss_external_position_height --continue-on-fail
```

# 2026-07-08 — Case C repaired with ENU external odometry

Root cause found:

```text
The live Gazebo bridge was sending Gazebo x/y/z as MAVLink LOCAL_NED.
PX4 supports MAV_FRAME_LOCAL_ENU and converts ENU pose/velocity into NED internally.
Hover smoke tests passed because horizontal motion was tiny, but after GNSS loss the wrong frame label made horizontal EV innovations reject.
```

Implemented:

- `send_live_gazebo_odometry_mavlink.py` now supports `--mav-frame local_ned|local_enu`.
- The runner passes `aiding.mav_frame` into the bridge and records it in status/validation output.
- `send_odometry()` can now accept explicit MAVLink pose/velocity frames.
- Offboard local hold now supports `setpoint_mode: velocity_xy_position_z`.
- Repaired Case C uses zero XY velocity setpoints plus Z position hold, which is more tolerant of EKF XY resets than fixed absolute `(x,y)=(0,0)`.
- Repaired Case C uses `mav_frame: local_enu`, `EKF2_EV_CTRL=7`, `EKF2_EV_DELAY=0`, `position_std_m=0.10`, `velocity_std_m_s=1.00`, finite-difference velocity, and `velocity_reject_action: hold_last`.

Validation:

```text
experiments/batches/20260708_164925_phase8a_velocity_validation_short
```

Result:

```text
GNSS on validation accepted.
EV pos/hgt/vel rejected counts: 0 / 0 / 0.
XY reset delta: 0.
Station drift end: 0.041 m.
```

Repaired C-only GNSS-loss proof:

```text
experiments/batches/20260708_165223_phase8a_case_c_velocity_repair_60s
```

Result:

```text
Accepted.
Station drift end: 0.112 m.
EV pos/hgt/vel rejected counts: 0 / 0 / 0.
XY reset delta: 1.
```

Repaired ABC 60 s proof:

```text
experiments/batches/20260708_165632_phase8a_abc_repaired_velocity_60s
```

Result:

```text
A GNSS on/no aiding station drift end: 0.064 m.
B GNSS loss/no aiding station drift end: 100.817 m.
C repaired GNSS loss/external position+height+velocity station drift end: 0.039 m.
C repaired EV pos/hgt/vel rejected counts: 0 / 0 / 0.
C repaired XY reset delta: 2.
```

Conclusion:

```text
The repaired Case C now proves aiding helps for the 60 s GNSS-loss comparison.
Next step is a longer 120 s repaired ABC run if the report needs the same duration as the earlier failed batch.
```

# 2026-07-08 — Repaired ABC 120 s proof

Run:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s
```

Result:

```text
Accepted: 3 / 3 cases.
A GNSS on/no aiding station drift end: 0.016 m.
B GNSS loss/no aiding station drift end: 319.281 m.
C repaired GNSS loss/external position+height+velocity station drift end: 0.089 m.
C repaired EV pos/hgt/vel rejected counts: 0 / 0 / 0.
C repaired XY reset delta: 1.
```

Conclusion:

```text
The repaired Case C now proves aiding helps for both 60 s and 120 s GNSS-loss comparisons.
Use the 120 s batch as the main Phase 8A proof run.
```

Plot report:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s/phase8a_abc_repaired_plot_report.md
```

Generated plots:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s/plots/
```

Plot interpretation:

```text
Full-scale station-drift plots show Case B leaving the hover point by 319.281 m.
Zoomed plots show Case A and repaired Case C both staying inside the sub-meter hover region.
Case C keeps EV position, height, and velocity active with 0 / 0 / 0 rejected samples.
```

# 2026-07-09 — Phase 8A Case C stress matrix prepared

Batch:

```text
experiments/configs/mvp/batches/phase8a_case_c_stress_matrix_120s.yaml
```

Purpose:

```text
Keep the repaired Case C architecture fixed, then stress the currently wired external-odometry knobs:
- external odometry rate
- reported EV position/velocity covariance
- EKF2_EV_DELAY setting
```

References included:

```text
reference_a_gnss_on_no_aiding
reference_b_gnss_loss_no_aiding
```

Case C variants included:

```text
case_c_repaired_nominal_30hz_cov010_delay0
case_c_stress_rate_15hz
case_c_stress_rate_10hz
case_c_stress_rate_5hz
case_c_stress_cov_pos025_vel150
case_c_stress_cov_pos050_vel200
case_c_stress_evdelay_100ms
case_c_stress_evdelay_160ms
case_c_stress_combo_rate10_cov025_delay100
```

Important limitation:

```text
This matrix does not yet inject random measurement noise or odometry dropout.
The "cov" cases change the covariance/std values reported to PX4.
The "evdelay" cases change EKF2_EV_DELAY; they do not inject transport latency.
```

Dry-run validation:

```text
experiments/batches/20260709_071312_phase8a_case_c_stress_matrix_120s
```

Run command:

```bash
cd /opt/databoss_px4_sim
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py experiments/configs/mvp/batches/phase8a_case_c_stress_matrix_120s.yaml --continue-on-fail
```

After the batch completes:

```bash
cd /opt/databoss_px4_sim
venv/bin/python scripts/runner/summarize_batch_metrics.py --batch-dir <batch_dir>
```

# 2026-07-09 - Phase 8A real-error Case C matrix accepted

Cleanup before rerun:

```text
Old generated run folders from 20260702 through 20260708 were removed from experiments/runs.
This restored /opt from about 81 MB free to about 7.5 GB free.
Batch/report folders and July 9 runs were kept.
```

Batch:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s
```

Result:

```text
Accepted: 11 / 11 cases.
GNSS-on reference station drift end: 0.007 m.
GNSS-loss/no-aiding reference station drift end: 320.197 m.
Nominal repaired Case C station drift end: 0.113 m.
Worst real-error Case C station drift end: 0.215 m.
Worst real-error Case C: case_c_realerr_noise_strong_pos025_vel050.
Combo noise+latency+dropout Case C station drift end: 0.075 m.
```

Real disturbances tested:

```text
Gaussian EV measurement noise:
- mild: position 0.05 m, velocity 0.10 m/s
- medium: position 0.10 m, velocity 0.25 m/s
- strong: position 0.25 m, velocity 0.50 m/s

Real transport latency:
- 100 ms uncompensated
- 100 ms with EKF2_EV_DELAY=100

Recurring odometry dropout:
- 1 s every 10 s
- 2 s every 10 s

Combined case:
- medium noise + 100 ms latency + 1 s every 10 s dropout
```

Estimator/vehicle interpretation:

```text
The aided Case C variants stayed below 0.215 m station drift while the no-aiding
GNSS-loss reference drifted 320.197 m. The strong-noise case produced EV
position/height rejections (18 / 128 / 0), so that is the first visible stress
limit in this matrix. Dropout cases produced XY reset deltas 11 and 12, but
station keeping remained sub-meter.
```

Generated report and plots:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/phase8a_case_c_real_error_report.md
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/real_error_summary.md
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/plots/
```

# 2026-07-09 - Phase 8A frozen and Phase 8B started

Decision:

```text
Stop treating the A/B/C hover proof as the main experiment.
Freeze Phase 8A as the ideal truth-fed external-aiding upper-bound reference.
Move the active project phase to physical world generation and the practical
downward camera + TF03 + optical-flow path.
```

Clean build path:

```text
Scenario YAML
-> world generator
-> Gazebo world + vehicle + simulated sensors
-> sensor-processing layer
-> PX4 sensor interfaces / MAVLink
-> PX4 EKF2 and controllers
-> automated mission
-> ULog + Gazebo truth + sensor logs
-> metrics, plots, report
-> dashboard later
```

Active phase:

```text
Phase 8B - Physical World Generation
```

First Phase 8B artifacts created:

```text
docs/phases/phase_08b_physical_world_generation.md
scripts/worlds/build_gazebo_world.py
experiments/configs/mvp/worlds/flat_rural_high_texture_noon.yaml
experiments/configs/mvp/worlds/flat_rural_low_texture_noon.yaml
generated_worlds/flat_rural_high_texture_noon.sdf
generated_worlds/flat_rural_low_texture_noon.sdf
```

Next acceptance steps:

```text
1. Validate generated SDF structure.
2. Launch each generated world in Gazebo.
3. Confirm high-texture and low-texture worlds visibly differ.
4. Confirm PX4 can spawn an x500 in each generated world.
5. Save source YAML and generated SDF with every run that uses generated worlds.
```

# 2026-07-09 - Phase 8C downward camera proof accepted

Phase:

```text
Phase 8C - Downward Camera Proof
```

Accepted runs:

```text
experiments/runs/20260709_182405_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth
experiments/runs/20260709_183013_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Result:

```text
High texture: accepted, camera sample 14,745,833 bytes, ULog airborne 22.476 s, max height 2.526 m.
Low texture:  accepted, camera sample 14,745,831 bytes, ULog airborne 23.039 s, max height 2.420 m.
```

Alignment:

```text
High texture: H mean 0.051812 m, H max 0.150824 m, height mean 0.045718 m, 3D max 0.170387 m.
Low texture:  H mean 0.040649 m, H max 0.083098 m, height mean 0.071383 m, 3D max 0.137186 m.
```

Implementation notes:

```text
The default PX4 Gazebo Sensors config uses ogre2. On this headless VM, camera
proof runs crashed in EGL/DRM initialization. Phase 8C now writes a run-local
Gazebo server config override using render_engine=ogre and launches standalone
Gazebo through xvfb-run with llvmpipe.

Camera rendering slows Gazebo enough that host wall-clock hover is not a stable
proxy for PX4/ULog time. The simple auto-hover path now waits on PX4
vehicle_land_detected timestamps before commanding land.
```

Runtime setup added:

```text
px4 user added to video/render groups.
xvfb and mesa-utils installed.
```

Next:

```text
Phase 8D - TF03-style downward rangefinder proof inside the same generated worlds.
```

# 2026-07-10 - Gazebo web GUI feasibility check (server side proven)

Question:

```text
Can the Mac view live Gazebo worlds through a browser (gzweb / websocket) with our headless Harmonic setup?
```

Physically tested on this VM:

```text
gz-sim 8.14.0 (Harmonic), gz-launch 7.1.2, libgz-launch-websocket-server.so present.
Headless gz sim -s with generated_worlds/flat_rural_high_texture_noon.sdf as px4 user (GZ_PARTITION=databoss_webgui_test).
gz launch -v 4 /usr/share/gz/gz-launch7/configs/websocket.gzlaunch in the same partition.
No protobuf duplicate-message crash (2024 Harmonic gz-launch bug already fixed in 7.1.2; no package update needed).
Port 9002 listened; raw websocket upgrade returned HTTP/1.1 101 Switching Protocols.
Test processes stopped and port closed afterward.

# 2026-07-10 - Gazebo web GUI feasibility check (server side proven)

Question:

```text
Can the Mac view live Gazebo worlds through a browser (gzweb / websocket) with our headless Harmonic setup?
```

Physically tested on this VM:

```text
gz-sim 8.14.0 (Harmonic), gz-launch 7.1.2, libgz-launch-websocket-server.so present.
Headless gz sim -s with generated_worlds/flat_rural_high_texture_noon.sdf as px4 user (GZ_PARTITION=databoss_webgui_test).
gz launch -v 4 /usr/share/gz/gz-launch7/configs/websocket.gzlaunch in the same partition.
No protobuf duplicate-message crash (2024 Harmonic gz-launch bug already fixed in 7.1.2; no package update needed).
Port 9002 listened; raw websocket upgrade returned HTTP/1.1 101 Switching Protocols.
Test processes stopped and port closed afterward.
```

Not yet proven:

```text
Browser client rendering of our generated worlds from the Mac (bandwidth, frame rate, materials).
Live streaming during an actual PX4 flight run.
```

Integration constraint:

```text
The runner sets GZ_PARTITION=databoss_<world>_<pid> per run; the websocket bridge only sees topics when started with the identical partition value.
```

Decision:

```text
Web GUI is a viewer/monitor only. DATABOSS remains the automation engine; QGC remains the operator monitor.
```

# 2026-07-10 - Gazebo web GUI live flight viewing proven from the Mac (with enum-patch proxy)

Live test:

```text
Runner scenario phase8c_web_camera flew x500_mono_cam_down in the generated high-texture world
with the runner-managed websocket bridge. The Mac browser (app.gazebosim.org/visualization over
an SSH tunnel to ws://localhost:9002) initially showed only a gray viewport.
```

Root cause (confirmed via a frame-logging websocket proxy and the browser console):

```text
gz-launch 7.1.2 WebsocketServer omits file-level enums from its `protos` response.
Exactly two are missing: PixelFormatType and SphericalCoordinatesType.
The gzweb client throws "no such Type or Enum '.gz.msgs.PixelFormatType' in Type
.gz.msgs.CameraSensor" while decoding the Scene proto, so any world whose vehicle
carries a camera sensor never renders. Camera-less vehicles are unaffected.
```

Fix (implemented and proven live):

```text
scripts/sim/gz_websocket_enum_patch_proxy.py
scripts/sim/gz_missing_proto_enums.txt
```

The proxy listens on 9002, forwards to the real bridge on 9003, and appends the two missing
enum definitions to the protos response. With the patch the client decoded the scene,
subscribed to dynamic_pose at 15 Hz, fetched all x500 meshes/textures through the asset op,
and rendered the world and vehicle live in the Mac browser.

Additional findings:

```text
1. The bridge only serves mesh/texture assets when launched with the sim environment
   (GZ_SIM_RESOURCE_PATH). The runner-managed bridge has it; manually started bridges must set it.
2. The WebsocketServer segfaults on malformed frames (e.g. "scene" with an empty world name
   killed the listener once). Treat the bridge as disposable; restart it if 9002/9003 stops listening.
3. The bridge must run in the same GZ_PARTITION as the sim (databoss_<world>_<runner pid>).
4. Scenario variant experiments/configs/mvp/scenarios/
   phase8c_web_camera_flat_rural_high_texture_noon_bridge9003.yaml puts the runner bridge on 9003
   so the enum-patch proxy can own 9002.
```

Accepted runs today with web bridge enabled:

```text
experiments/runs/20260710_075811_phase8c_web_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth (accepted, 180 s hover)
experiments/runs/20260710_083447_phase8c_web_camera_flat_rural_high_texture_noon_bridge9003_pxh_takeoff_land_truth (240 s hover, live-viewed from the Mac)
```

Mac connection recipe:

```text
ssh -N -L 9002:127.0.0.1:9002 root@100.78.93.35
Browser: https://app.gazebosim.org/visualization -> ws://localhost:9002, no auth key.
```

Upstream note:

```text
The missing-enum defect belongs in gazebosim/gz-launch. Consider filing an issue with the
protos-blob evidence; the proxy remains our local workaround until a fixed package lands.
```

# 2026-07-10 - Web GUI live view confirmed by operator; settings frozen for next runs

Operator confirmation from the Mac browser:

- The generated checkered ground rendered with the correct sky-blue background.
- The x500_mono_cam_down rendered with full carbon-fiber meshes, props, camera body, and textures.
- Live pose streaming animated the vehicle during the 240 s hover run 20260710_083447.

Settings applied for future runs:

- Both phase8c_web_camera_* scenarios now set visualization.gazebo_web.port: 9003.
- Port convention frozen: 9003 = raw runner-managed bridge, 9002 = enum-patch proxy (browser entry point).
- Proxy tool committed to the repo: scripts/sim/gz_websocket_enum_patch_proxy.py plus scripts/sim/gz_missing_proto_enums.txt.
- The websockets package was installed into the DATABOSS venv.
- Operator guide created: docs/gazebo_web_visualization.md.
- Phase roadmap README updated with the web visualization note.

# 2026-07-10 - 240 s web-viewer run 20260710_083447 result: rejected (honest), live view still proven

Result:

- Run 20260710_083447 was REJECTED by the acceptance gate: ULog airborne 102.88 s vs required ~192 s.
- Cause from console evidence: "Connection to ground station lost" -> datalink-loss failsafe ->
  Hold -> Return (default_px4 profile sets NAV_DLL_ACT 2) -> early landing.
  The QGC MAVLink stream to the Mac counted as a GCS; when it dropped mid-hover PX4 returned home.
- Everything else passed: camera probe (14,745,836-byte sample), landing, truth recording.
- The run's purpose (live Mac browser viewing through the enum-patch proxy) was achieved and
  operator-confirmed before the failsafe.

Lesson for long web-viewer hovers, pick one:

- keep QGroundControl open on the Mac for the whole flight, or
- pass --no-qgc to the runner, or
- use a failsafe profile with NAV_DLL_ACT 0 (delayed_observation) when the flight is observation-only.

Cleanup: the session-scratchpad proxy instance was stopped; future proxy starts must use
scripts/sim/gz_websocket_enum_patch_proxy.py.

# 2026-07-10 - Phase 9A opened: real-terrain world import (gazebo_terrain_generator)

Decision: integrate real-world heightmap terrain (Mapbox DEM + satellite texture) as a world-import
path alongside the Phase 8B YAML generator. Terrain track runs before Phase 8D per operator decision.

Tool: https://github.com/saiaravind19/gazebo_terrain_generator (BSD-3), cloned to
/opt/gazebo_terrain_generator. Targets Gazebo Harmonic; we run 8.14.0.

Physically proven today (evidence: experiments/runs/20260710_105803_phase9a_terrain_world_launch_spawn_proof):

- Joshimath sample heightmap world (15.4x11.4 km, 3325 m relief) launches headless server-only.
- Truth topics present under /world/Joshimath/.
- x500 spawns via create service only when the Gazebo server has GZ_SIM_RESOURCE_PATH
  (first attempt failed: "Unable to find uri[model://x500_base]"; the runner already sets it).
- Collision real: x500 dropped from z=5 settled at z=2.9970 on the helipad top (3.0 m), no fall-through.
- Memory headroom OK on the 3.7 GiB VM (~1.6 GiB available with world + vehicle).

Phase doc: docs/phases/phase_09a_real_terrain_world_import.md

Next action: terrain flight proof - scenario YAML for the Joshimath world plus runner support for
spawn pose above ground (PX4_GZ_MODEL_POSE) and PX4 home from the world's spherical_coordinates.

# 2026-07-10 - Phase 9A terrain flight proof ACCEPTED (Joshimath heightmap)

Accepted run: experiments/runs/20260710_111630_phase9a_flight_joshimath_terrain_pxh_takeoff_land_truth

- Runner gained vehicle.start_pose -> PX4_GZ_MODEL_POSE and world.home -> PX4_HOME_LAT/LON/ALT.
- PX4 flew the imported Joshimath heightmap world (real Himalayan DEM + satellite texture) from the
  helipad at the true coordinates (lat 30.5677, lon 79.5506, alt 1382.2 m): armed, hovered 104 s
  at 2.55 m, landed, disarmed. EKF-vs-truth alignment on non-flat terrain: H mean 0.058 m,
  H max 0.123 m, 3D max 0.126 m - same error class as flat worlds.
- First attempt (run 20260710_111123) was honestly rejected: no barometer/compass. Root cause:
  the terrain generator's world template declares world-level plugins which override PX4's
  server.config, and omits AirPressure + Magnetometer. Import-preparation rule documented in
  docs/phases/phase_09a_real_terrain_world_import.md; the imported world now carries the four
  added sensor systems.
- Web bridge ran on 9003 with the enum-patch proxy on 9002 for live Mac viewing during the flight.
  Whether the browser renders heightmap geometry is pending operator confirmation. Superseded
  2026-07-13: native generated-terrain browser rendering is proven with the terrain proxy flags.

# 2026-07-10 - Web viewer heightmap rendering confirmed (partial: texture too large)

Superseded on 2026-07-13: the issue was not simply texture size. Native
generated-terrain browser rendering now works through the terrain proxy flags
that serve local assets and populate inline heightmap samples.

Operator screenshot + proxy frame log during the accepted Joshimath flight:

- Helipad and x500 rendered fully in the Mac browser.
- Heightmap terrain RENDERED as geometry: the client requested and received the terrain assets
  through the bridge asset op (aerial.png 58,798,085 bytes; normal_map.png 6,186,335 bytes).
- The satellite albedo texture did not visibly apply - terrain shows as a dark normal-mapped
  surface. Cause attributed to texture size: a ~59 MB stitched PNG exceeds practical browser/WebGL
  texture limits.

Rule for custom terrain worlds intended for web viewing: keep polygons small / zoom moderate so
aerial.png stays in the low tens of MB or less. Desktop-quality terrain visuals in the browser are
not required for experiments; Gazebo truth and ULog remain the evidence chain.

# 2026-07-10 - First operator-generated terrain world flown and ACCEPTED (Sereflikochisar)

The full custom-world pipeline is proven end to end:

```text
operator draws polygon in gazebo_terrain_generator UI (:8080 over Tailscale, Mapbox key browser-side)
-> world saved directly into generated_worlds/terrain/_generator_output/ (GAZEBO_TERRAIN_OUTPUT_PATH)
-> DATABOSS import: promote, add 4 missing PX4 sensor plugins, PROVENANCE.yaml
-> scenario YAML with world.home from spherical_coordinates + start_pose
-> runner flight with truth + alignment + web bridge
```

World: serefli_koschisar (Sereflikochisar, Turkey; lat 38.9667, lon 33.5646, elev 1079.4 m;
193x191 m polygon, 55 m relief, aerial.png 60 KB).

Attempt 1 (run 20260710_113538, rejected): armed on bare terrain at the spawn pin, slid/tipped
during takeoff -> "Attitude failure (roll)" -> disarmed by failsafe. A probe grid showed the whole
pin area slopes 14-20 deg; no flat ground.

Fix: injected a static 4x4x0.5 m flat launch pad at the spawn point (top z=1.3) into the world as a
documented DATABOSS import step (equivalent to the generator's own helipad option), spawn z=1.65.

Attempt 2 (run 20260710_114339, ACCEPTED): armed on the pad, took off, hovered (ULog airborne
103.5 s, max height 2.585 m), landed, disarmed by landing. Truth 19.1 MB. Alignment:
H mean 0.051 m / max 0.127 m, height mean 0.075 m, 3D max 0.154 m.

Rules for future generated worlds:

- Enable the generator's helipad option OR drop the pin on flat ground OR let the import step add a pad.
- Small polygons (a few hundred meters) keep textures tiny and load fast on the 4 GB VM.

# 2026-07-10 - Correction: web viewer does not apply heightmap albedo textures (size was not the cause)

Superseded on 2026-07-13: this was an incomplete diagnosis. The hosted browser
client needed local asset delivery plus inline `HeightmapGeom` samples; the
terrain proxy now supplies both for generated terrain worlds.

The earlier entry attributed Joshimath's untextured web terrain to the 58.8 MB aerial.png.
The Sereflikochisar flight disproves that: its aerial.png is 60 KB and the browser still shows
dark normal-mapped geometry without satellite imagery, while pad and vehicle render fully.
Conclusion: the gzweb client renders heightmap geometry but does not implement Gazebo's
heightmap texture-blend material, regardless of texture size.

Impact: web viewer stays a flight monitor (geometry + vehicle + live poses). The onboard camera
renders server-side in ogre - satellite texture visibility for optical flow must be proven in the
camera-over-terrain phase, not in the browser.

# 2026-07-10 - Phase 9A closed before web fix: camera-over-terrain proof ACCEPTED.

Final proof (run 20260710_115726, accepted): gz_x500_mono_cam_down flew to 8.08 m over the
operator-generated Sereflikochisar heightmap, captured a 13.4 MB downward camera frame, landed,
aligned with truth (H mean 0.053 m, 3D max 0.110 m). The rendered frame shows the satellite
albedo texture on the terrain: the ogre server-side pipeline delivers real terrain imagery to
the onboard camera - the required input for the optical-flow phases.

Third import rule discovered: the generator template hardcodes render_engine ogre2 in the world
Sensors plugin (overrides runner server config) -> EGL segfault on this headless VM (first camera
attempt, run 20260710_115147). Fix: set it to ogre. Cosmetic side effect: no procedural sky in ogre.

Texture rule: 60 KB aerial (~0.5 m/px) is blurry at 8 m altitude; generate optical-flow worlds
with higher zoom / smaller polygons.

Phase 9A status at this point: accepted with web-render limitations.
Superseded 2026-07-13: native terrain browser rendering now works through the
terrain proxy flags; texture fidelity for onboard camera evidence is still
governed by generation zoom.
Consolidated import-preparation checklist: docs/phases/phase_09a_real_terrain_world_import.md.

Next phase per operator decision: Phase 8D - TF03-style downward rangefinder proof
(flat worlds first, then re-run over terrain).

# 2026-07-10 - 80 m camera frame over generated terrain (accepted)

Run 20260710_120342_phase9a_camera80m_serefli_koschisar_terrain (accepted): mono_cam_down at
79.94 m over the operator-generated world. The rendered frame
(camera/camera_frame_terrain_80m.png) captures the full drawn polygon - gullies, ridges, launch
pad, and world boundary - with the satellite texture crisp at this altitude. Visual match with
the operator's original Mapbox polygon screenshot. Scenario:
experiments/configs/mvp/scenarios/phase9a_camera80m_serefli_koschisar_terrain.yaml.

# 2026-07-10 - Phase 8D ACCEPTED: TF03-style downward rangefinder proof (both flat worlds)

Batch: experiments/batches/20260710_124609_phase8d_downward_rangefinder_generated_world_smoke (2/2 accepted).

- gz_x500_lidar_down (x500 + LW20 single-point downward gpu_lidar, 0.1-100 m, 50 Hz, ideal sensor)
  flew both generated flat worlds through the extended runner.
- Proven path: gpu_lidar -> /world/<w>/model/<m>/link/lidar_sensor_link/sensor/lidar/scan
  -> PX4 gz_bridge (SIM_GZ_EN_LIDAR=1) -> distance_sensor uORB -> ULog.
- Evidence per world: in-flight scan sample ~2.511 m at 2.5 m hover; ULog distance_sensor rows
  2031/2205; ULog max distance agrees with in-flight sample to the mm; range-vs-height diff
  0.113/0.150 m (tolerance 0.75 m); truth + alignment passed (H mean 0.036/0.031 m).
- Runner gained the rangefinder: scenario section (probe + ULog distance_sensor analysis);
  gpu_lidar reuses the ogre+xvfb rendering path. Both checks wired into acceptance.
- Disk cleanup beforehand freed ~600 MB (uv/pip caches, crash dumps, stale rootfs ULogs).

Phase doc: docs/phases/phase_08d_downward_rangefinder_proof.md (Accepted).
Next: Phase 8E - combined camera + rangefinder vehicle; plus an 8D terrain case over serefli_koschisar.

# 2026-07-10 - Phase 8E ACCEPTED: combined camera + TF03 rangefinder vehicle (both flat worlds)

New DATABOSS vehicle x500_cam_lidar_down (source: src/databoss_sim; deployed into PX4 as
airframe 4022 + Tools/simulation/gz/models/x500_cam_lidar_down): downward mono camera and
TF03-style downward gpu_lidar on one x500.

Design lesson: v1 mounted the lidar on the centerline; the camera assembly sits ~0.10 m below
the lidar sensor and the lidar read a constant 0.100 m (min range) - staring at the camera
housing (rejected runs 20260710_130250/131117; the stuck bottom distance also disturbed landing
disarm). v2 offsets the lidar 0.08 m forward, side-by-side like real TF03+camera mounts.
Root cause spotted by the operator from the web-view screenshot.

Accepted evidence (runs 20260710_133458 high / 20260710_132712 low):
camera frames 7.34/7.35 MB, lidar 2.516/2.526 m at 2.5 m hover, distance_sensor rows 1620/1605,
range-vs-height diff 0.113/0.121 m, alignment H mean 0.048/0.049 m. QGC stream + web bridge
active in both runs per the new standing monitoring rule. land_timeout_s raised to 200 for
dual-rendering-sensor runs.

Phase doc: docs/phases/phase_08e_combined_cam_lidar_vehicle.md (Accepted).
Next: Phase 8F offline optical-flow validation; terrain cases over serefli_koschisar.

# 2026-07-10 - Phase 8E terrain case ACCEPTED: combined vehicle over Sereflikochisar

Run 20260710_135303_phase8e_cam_lidar_serefli_koschisar_terrain (accepted): the x500_cam_lidar_down
v2 flew the operator-generated heightmap from the launch pad with both sensors publishing, QGC
connected (operator live) and web bridge ready per the standing monitoring rule.

Evidence: lidar 2.5139 m at 2.5 m hover with ULog range-vs-height agreement of 0.002 m (both
reference the pad top); 2773 distance_sensor rows; camera frame 7.2 MB showing the pad and the
satellite-textured terrain; alignment H mean 0.069 m / max 0.151 m.

The complete practical sensing stack (downward camera + TF03-style lidar + IMU) is now proven on
one vehicle over real-DEM terrain. Scenario:
experiments/configs/mvp/scenarios/phase8e_cam_lidar_serefli_koschisar_terrain.yaml.

Next: Phase 8F - offline optical-flow validation using camera frames + lidar AGL from 8E-style runs.

# 2026-07-10 - New analysis tool: three-source height comparison plot

scripts/analysis/plot_lidar_truth_ekf_height.py <run_dir> plots distance_sensor (lidar),
Gazebo truth height, and EKF height on one PX4 timeline. First output:
experiments/runs/20260710_135303_phase8e_cam_lidar_serefli_koschisar_terrain_pxh_takeoff_land_truth/plots/lidar_vs_truth_vs_ekf_height.png

Reading (terrain 8E run): on the pad the lidar reads its 0.18 m mounting height; at hover
lidar 2.51 m = truth displacement 2.34 m + 0.18 m mount offset (mm-level agreement between the
two physical references), while EKF sits ~0.16 m above truth (height err mean 0.073 / max
0.168 m in this run - baro-referenced EKF height drifts more over terrain than in flat worlds).
After touchdown the forward-offset beam reads ~0.68 m - past the pad edge down the slope; the
landing itself was clean.

# 2026-07-10 - Roadmap cleared: road to the GNSS-denied result

Planning session with the operator. Remaining path frozen in docs/phases/README.md:
8F offline optical-flow validation (frame-stream recorder + low-res flow camera variant +
slow translation route) -> 8G live flow bridge (own camera pipeline; PX4 x500_flow allowed only
as plumbing rehearsal) -> 8H GNSS-on fusion check -> 8I A/B/C/D GNSS-denied comparison against
the frozen 8A anchors -> 12 MVP report -> 13 dashboard.

Next session starts Phase 8F with the frame-stream recorder.

# 2026-07-11 - Phase 8F ACCEPTED (with limitations): offline SIFT v1 flow validation, all 3 worlds

The missing terrain validation was run today (validate_flow_offline.py on run
20260710_183635_phase8f_flow_rec_serefli_koschisar_terrain); the two flat-world
validations were already in place from 2026-07-10.

Results (speed err mean vs truth at hover, criterion < 0.15 m/s):
- flat high texture  (20260710_180752): 0.017 m/s, valid 84.4%, quality 48.0, matches 19.0
- flat low texture   (20260710_182320): 0.033 m/s, valid 69.5%, quality 26.1, matches 11.2
- serefli terrain    (20260710_183635): 0.022 m/s, valid 77.5%, quality 58.7, matches 23.2

All recordings ~7.6 Hz effective (>=5 Hz criterion). Texture comparison in the
expected direction (high >> low). Satellite-textured real-DEM terrain gives the
BEST feature quality of all worlds - good news for the rural/terrain focus.
Limitation kept explicit: hover-only + synthetic-shift self-test; no in-flight
translation route yet (carried into 8G/8H). No gyro compensation in v1.

Phase doc: docs/phases/phase_08f_offline_flow_validation.md (Accepted with limitations).
Next: Phase 8G - live modular flow bridge (own pipeline -> OPTICAL_FLOW_RAD -> PX4).

# 2026-07-11 - Research note: PX4 stock gz-sim optical flow is REAL image-based KLT, not synthetic

Source-tree exploration of /opt/sim_px4/PX4-Autopilot (informs 8G plumbing rehearsal):

- Tools/simulation/gz/models/x500_flow: downward flow camera (100x100 px,
  hfov 0.733, 50 Hz, custom gz sensor type "optical_flow" on flow_link) PLUS a
  downward single-ray gpu_lidar (0.1-100 m, 50 Hz) - same sensor pairing as our
  x500_cam_lidar_down.
- src/modules/simulation/gz_plugins/optical_flow/ OpticalFlowSystem: subscribes
  the rendered camera image and runs OpticalFlowOpenCV::calcFlow() from the
  PX4-OpticalFlow submodule (real KLT tracker used on PX4Flow hardware). NOT
  truth-derived. Publishes px4::msgs::OpticalFlow (integrated_x/y,
  integration_time_us, quality) on .../flow_link/sensor/optical_flow/optical_flow.
- GZBridge.cpp subscribeOpticalFlow() (SIM_GZ_EN_FLOW, default 1) -> uORB
  sensor_optical_flow (pixel_flow, quality, integration_timespan_us; PAW3902
  constants, max_flow_rate 7.4 rad/s, max ground distance 30 m). Distance and
  delta_angle deliberately NOT set - range comes from the separate
  distance_sensor stream (gpu_lidar bridge), gyro from vehicle IMU.
- Airframe 4021_gz_x500_flow = 4001_gz_x500 + SYS_HAS_GPS 0, SIM_GPS_USED 0,
  EKF2_GPS_CTRL 0. Relies on defaults EKF2_OF_CTRL=1, EKF2_RNG_CTRL=1,
  SIM_GZ_EN_FLOW=1, SIM_GZ_EN_LIDAR=1 -> flyable GNSS-denied out of the box.
- EKF2 flow fusion gates: EKF2_OF_CTRL, EKF2_OF_QMIN(_GND), EKF2_OF_GATE,
  EKF2_OF_N_MIN/MAX, EKF2_OF_DELAY. Start conditions require HAGL within sensor
  range AND (valid terrain estimate OR other horizontal aiding OR range as
  height ref) - so GNSS-denied flow effectively REQUIRES the downward
  rangefinder feeding the terrain estimate. Matches our camera+TF03 stack.

Implications for 8G/8H: x500_flow is a legitimate plumbing rehearsal target
(SIM_GZ_EN_FLOW path end-to-end), and our live bridge must deliver flow rates +
quality on sensor_optical_flow (via MAVLink OPTICAL_FLOW_RAD) while the TF03
distance_sensor stream independently provides HAGL. EKF2_OF_QMIN will gate our
quality metric - calibrate it against the 8F quality distributions (26-59).

# 2026-07-11 - Phase 8G PLANNED: live modular flow bridge (plan + decisions frozen)

Planning session with the operator. Full plan in docs/phases/phase_08g_live_flow_bridge.md.
Operator requirement: maximum modularity - frames in, EKF-ready flow out; new
algorithms enter via the make_estimator registry + --replay regression + open-loop
scenario, never by editing the bridge. Operator chose: stock x500_flow rehearsal
first (8G.0) before our own bridge.

Sub-phases: 8G.0 rehearsal -> 8G.1 bridge+adapter (replay-tested, no sim) ->
8G.2 live OPEN-LOOP (EKF2_OF_CTRL=0, sign + latency calibration via two slow
translation legs vs truth) -> 8G.3 CLOSED-LOOP fusion on all 3 worlds (absorbs
the old 8H GNSS-on fusion check; 8H dropped as standalone).

Key decisions (evidence in phase doc):
- D1 interpreter split: dedicated venv_bridge (--system-site-packages from system
  python3 for gz.transport13 + pip pymavlink). Single process.
- D2 timestamps: PX4 mavlink_receiver ARRIVAL-stamps OPTICAL_FLOW_RAD (ignores
  time_usec) -> measure pipeline latency in 8G.2, bake EKF2_OF_DELAY (reboot-
  required) into airframe 4022.
- D3 gyro: send NaN integrated gyros -> EKF2 falls back to vehicle IMU (same as
  stock GZBridge path). No gyro comp needed in estimator v1.
- D4 distance: send -1 (unknown); HAGL comes from the proven TF03 distance_sensor
  stream. SENS_FLOW_MINHGT/MAXHGT set explicitly.
- D5 signs: parameterized axis map in a pure px4_adapter.py + MANDATORY open-loop
  translation-leg sign gate before any fusion.
- D6 quality: bridge-level linear rescale of raw SIFT quality (26-59 in 8F) to
  0-255, calibrated from the 8F recordings; then set EKF2_OF_QMIN from the
  rescaled distributions.
- D7 rehearsal runs GNSS-ON (re-enable GPS params at pxh over airframe 4021).

Next action: rerun 8F validations for fresh plots, then 8G.1 (venv_bridge +
px4_adapter.py + flow_mavlink_bridge.py + replay regression).

# 2026-07-11 - Phase 8G.1 DONE: bridge + adapter built and replay-regression-proven

New: venv_bridge (system python3 --system-site-packages + pymavlink 2.4.49 - one
interpreter with gz.transport13 + cv2 + MAVLink), src/databoss_sim/flow/px4_adapter.py
(pure FlowSample->OPTICAL_FLOW_RAD: 8 named axis maps, NaN gyros, distance -1,
quality rescale; self-test), scripts/sim/flow_mavlink_bridge.py (live gz camera ->
registry estimator -> OPTICAL_FLOW_RAD; --replay regression mode; --dry-run).

Evidence:
- Replay over the 8F high-texture recording reproduces the offline baseline
  EXACTLY (404/404 samples, <=5e-8 rad diff, quality identical) -> cv2 4.13 vs
  5.0 drift risk retired.
- Real send path: 404/404 messages, no exceptions; quality rescale verified.
- Sign convention pinned from PX4 source: stock OpticalFlowSystem sends raw
  scene-displacement flow with no swap/flip from an identically mounted camera
  -> default axis_map "xy"; empirical 8G.2 translation-leg gate still mandatory.
- Compute time (this VM): 640px SIFT 85ms mean/112ms p95 (marginal at 10 Hz);
  480px 46/61ms with validity ~unchanged -> live scenarios use max_width 480.

Also: 8F validations rerun (deterministic, same numbers); plots reviewed - low
texture shows 1.2-1.4 m/s bad-match spikes, the concrete argument for the D6
quality gate before closed-loop fusion.

Next: 8G.0 stock x500_flow rehearsal (reference ULog), then 8G.2 runner
integration + open-loop scenario.

# 2026-07-11 - New analysis tool: horizontal-error plot (per run + comparison)

scripts/analysis/plot_horizontal_error.py <run_dir>... [--out png] plots
ekf_vs_ground_truth_aligned.csv horizontal_error_m on the PX4 timeline; one run
-> <run>/plots/horizontal_error.png, several runs -> overlay comparison.

Generated for the three 8F runs plus
experiments/comparisons/phase8f_horizontal_error_three_worlds.png.
Reading (GNSS ON - this is the GNSS-on EKF baseline for the 8I comparison):
high texture mean 0.049 m / max 0.116 m; low texture 0.050 / 0.137;
serefli terrain 0.033 / 0.095. All cm-level; texture does not matter while
GNSS anchors the EKF - the spread only appears when GNSS drops (8I).

# 2026-07-13 - Phase 8G.0 ACCEPTED: stock x500_flow fusion-path rehearsal (reference ULog)

Run experiments/runs/20260713_062909_phase8g0_x500_flow_rehearsal (attempt 3).
Full path proven: gz KLT flow -> SIM_GZ_EN_FLOW -> sensor_optical_flow -> EKF2.
cs_opt_flow active 85% airborne; steady-hover rejected/fused 6.9% (<10% gate);
1044 fused total; xy resets only pre-takeoff (GPS runtime enable, D7 confirmed
live with satellites_used=10 over the GNSS-off airframe).

Hard-won launch/automation lessons (all in the phase doc):
- Render engine: PX4_GZ_SIM_RENDER_ENGINE=ogre is the ONLY working knob in
  PX4-managed mode (gz_env.sh clobbers GZ_SIM_SERVER_CONFIG_PATH; ogre2
  segfaults on this VM). Attempt 1 rejected.
- COM_RC_IN_MODE 4 required before arming or the RC-loss failsafe escalates
  to AUTO_LAND mid-hover (attempt 2 rejected, run 20260713_062101 preserved).
- RTF ~= 0.25 with the 50 Hz flow camera -> wall-clock waits must be ~4x the
  desired sim duration.
- Logger: sensor_optical_flow is capped to ~1 Hz by the default profile; use
  estimator_aid_src_optical_flow (~45 Hz) for stream/fusion evidence.
  analyze_flow_fusion_ulog.py criterion corrected accordingly.

# 2026-07-13 - Phase 8G.2 prep: runner flow_bridge section + open-loop scenarios + quality calibration

- Runner auto_takeoff_land_pxh_truth.py gained the flow_bridge: scenario
  section (mirrors flow_recording/aiding: cfg parse, onboard mavlink 14600
  reuse, EKF2_OF_*/SENS_FLOW_* pxh params, Popen venv_bridge bridge after
  flight-ready, finally-stop, flow_bridge_sent_rows acceptance flag, status
  JSON + prints). Compiles clean.
- Three 8G.2 open-loop scenarios (EKF2_OF_CTRL=0, GNSS on, high-texture flat
  world, max_width 480): hover 60s; +X leg 0.5 m/s; +Y leg 0.5 m/s. The legs
  reuse the existing offboard velocity_xy_position_z machinery - no new
  control code.
- Quality calibration from the 8F recordings: rescale window [20,100]->0..255,
  EKF2_OF_QMIN=17 (~raw 25). Honest finding: the low-texture bad-match spikes
  (quality 20-40) sit INSIDE the good low-texture quality range - quality
  alone cannot fully gate them; EKF2_OF_GATE innovation gating is the second
  line, and low-texture degradation stays an accepted RESULT to measure.

Next: fly the open-loop hover scenario, then the legx/legy sign gates; write
analyze_flow_bridge_openloop.py (delivery %, latency -> EKF2_OF_DELAY, signs).

# 2026-07-13 - Superseded generated terrain web fallback experiment (32x32 colored tiles)

Superseded later on 2026-07-13 by the native heightmap web-render fix below.
Do not use this colored-tile fallback as the normal generated-terrain browser
process.

Sereflikochisar browser diagnostic completed. `/scene/info` contains the terrain
heightmap plus `height_map.png`, `aerial.png`, `normal_map.png`, and the launch
pad, so Gazebo server + SceneBroadcaster are sending terrain correctly. The
enum proxy is not the terrain failure path; normal proxy operation remains
protobuf enum repair only.

Rejected during this experiment before the later native fix: native heightmap
material (geometry only / dark surface), Collada textured mesh (browser fetches
`/opt/...` paths from app.gazebosim.org and 404s), embedded texture data URI,
Collada vertex colors, Fuel-style `model://` package, and Scene mesh URI
rewrite.

Fallback process tested here: make a separate `*_web_mesh` fallback world with
`scripts/worlds/heightmap_to_web_mesh_world.py --visual-mode colored_tiles
--tile-count 32`. This renders a 32x32 SDF box mosaic sampled from `aerial.png`
for the browser monitor while preserving the original heightmap collision. The
original imported terrain world remains the source of truth for PX4 physics,
onboard camera, optical-flow evidence, and provenance. The 512x512 stress test
generated a 151 MB SDF and did not load reliably on this VM.

# 2026-07-13 - Native generated terrain heightmap web render repaired

Sereflikochisar native heightmap rendering is now proven in the hosted Gazebo
web viewer. The correct process is to run the original terrain world, keep the
raw `gz-launch` websocket bridge on 9003, and connect the browser through
`scripts/sim/gz_websocket_enum_patch_proxy.py` on 9002 with:

```text
--serve-generated-terrain-assets
--populate-generated-terrain-heightmaps
```

Root cause: the browser first requests absolute `/opt/.../aerial.png` and
`/opt/.../normal_map.png` paths from `app.gazebosim.org`, producing expected
404s before websocket asset fallback; it also expects inline
`HeightmapGeom.width`, `HeightmapGeom.height`, and `HeightmapGeom.heights`,
while Gazebo's Scene message only includes the heightmap filename, textures,
size, and origin. The proxy now serves allowlisted local generated-terrain PNG
assets as `gz.msgs.Bytes` and populates height samples from local
`height_map.png`.

Important guardrail fixed during live testing: patched Scene frames must retain
the websocket frame header (`pub,scene,gz.msgs.Scene,`). Stripping that header
causes a gray viewport even when the Scene protobuf itself is valid. A healthy
run logs `S->C scene heightmap samples populated 1x from local PNG`, a binary
Scene head beginning with `pub,scene,gz.msgs.Scene`, and local asset responses
for `aerial.png` plus `normal_map.png`.

Operational update: generated terrain worlds under `generated_worlds/terrain/*`
should use the native proxy path for web monitoring. `_web_mesh` colored-tile
worlds are emergency/debug fallback only, not the accepted default. The
Sereflikochisar heightmap used in the proof is 513x513 16-bit samples over
about 193x191 m (~0.38 m sample spacing in the generated image), with a
207x207 aerial texture (~0.93 m/px).

## 2026-07-13 — 8G.2 open-loop hover PASSED (first live bridge flight) + upcoming phase docs

- First live flight of OUR bridge: run
  `20260713_064248_phase8g_flow_openloop_hover_flat_rural_high_texture_noon_pxh_takeoff_land_truth`
  end-to-end accepted; 331 OPTICAL_FLOW_RAD samples sent in-flight
  (EKF2_OF_CTRL=0, GNSS on, EKF-vs-truth 0.0506 m mean — unaffected, as designed).
- `analyze_flow_bridge_openloop.py` PASS: latency (frame sim time → PX4
  arrival) median 38 ms / p90 46 ms → EKF2_OF_DELAY candidate ≈ 104 ms
  (median + integration_dt/2); confirm on full-rate leg runs before baking
  into airframe 4022.
- Analyzer fix: quality-0 samples (integrated 0,0) excluded from value
  matching — zeros match any zero row and fabricated multi-second latency tails.
- RTF note: x500_cam_lidar_down on generated flat world ≈ 0.9 RTF (the 0.25
  figure is specific to stock x500_flow's 50 Hz flow camera).
- Upcoming phase docs created (all Planned): 8I GNSS-denied A/B/C/D
  (`phase_08i_gnss_denied_flow_comparison.md`), 12 MVP comparison report
  (`phase_12_mvp_comparison_report.md`), 13 dashboard data contract
  (`phase_13_dashboard_data_contract.md`). Phases README updated.
- Next: legx/legy open-loop sign-gate flights (`--expect vx/vy`, closes D5),
  then 8G.3 closed-loop fusion on 3 worlds.

## 2026-07-13 (later) — 8G.2 legx gate FAILED; three project-wide findings; unblock plan approved

Flights: `20260713_090116_..._legx_...` (rejected — wall-clock duration bug,
see RTF below) and `20260713_091302_..._legx_...` (flight physically correct:
27.7 sim-s airborne, 2.503 m alt, ~10.3 m traverse at ~0.5 m/s; bridge
delivery 127/127 at full-rate logging, latency median 34 ms / p90 42 ms →
EKF2_OF_DELAY ≈ 100 ms, confirming the hover run's 104 ms).
Analyzer `--expect vx`: sign correct, but magnitude 0.155× expected, no axis
dominance → gate FAILED. Root-cause evidence in the run folder (saved frames
+ phase correlation): the images themselves show ~0.1 px shift where ~7 px
was expected — the estimator honestly reported what the camera saw.

Findings (each supersedes an earlier record):

1. **Truth aligner ENU→NED axis bug — FIXED.**
   `scripts/runner/align_latest_truth_run.py` compared PX4 NED x/y directly
   against Gazebo ENU x/y. Correct mapping: `NED_x = ENU_y`, `NED_y = ENU_x`
   (height was already handled). All previously accepted comparisons were
   hover (zero displacement) so they are numerically unaffected; the 8A
   drift anchors are magnitude-dominated and survive. The legx fake error
   5.43 m mean dropped to 0.144 m mean / 0.332 m max after the fix. The
   skill's "px4_x = gazebo_x − x0" frame rule was corrected the same day.
2. **RTF correction: x500_cam_lidar_down ≈ 0.09–0.10, NOT 0.9.** Proven by
   bridge CSV wall-vs-sim stamps on both the hover run (0.087) and legx
   (0.099). The 2026-07-13 morning "RTF ≈ 0.9" note was a mis-measure.
   Practical rule: runner `--hover-s`/`--land-timeout-s` are WALL clock —
   multiply desired sim seconds by ~10 (30 sim-s leg → `--hover-s 310
   --land-timeout-s 300`).
3. **No world gives the camera real texture.**
   `flat_rural_high_texture_noon` is solid-color 5 m box tiles (the world
   generator has no image-texture support); the serefli camera view is a
   featureless gray 4×4 m launch pad plus a heightmap whose `aerial.png`
   diffuse does not render — and aerial.png is 207×207 px over 192 m
   (~0.9 m/px), featureless at flow scale anyway. Additionally the noon
   drone shadow sits dead-center in the downward camera and moves with the
   vehicle, so SIFT's median displacement reports ~0 flow at healthy
   quality. Consequence: 8F's "feature quality" numbers came from tile/pad
   edges; the hover-run bridge "pass" was plumbing-only (zero flow is
   correct at hover). The 8G.2 leg gates are BLOCKED on a textured world.

Operator decisions: flat photo-texture world first (serefli texture repair
deferred to pre-8I); shadows OFF in the flow-validation world (angled sun +
shadows for 8I realism, documented).

Approved plan (recorded in `phase_08g_live_flow_bridge.md` and the phases
README): Step 0 prove image-texture rendering under pinned `ogre` classic
(procedural ~1 cm/px texture via new `scripts/worlds/make_ground_texture.py`;
fallback ogre2 `--headless-rendering`) → Step 1 extend
`build_gazebo_world.py` (texture.image + scene.shadows) → world
`flat_rural_phototex_noon` + cloned open-loop scenarios → Step 2 re-fly
legx/legy gates (close D5, confirm OF_DELAY) → Step 3 8G.3 closed-loop +
airframe 4022 OF_DELAY + 8G.4 docs → Step 4 pre-8I serefli repair → 8I →
12 → 13.

Also this session: every-frame camera recording enabled
(`flow_recording.rate_hz: 0` in the three 8G.2 scenarios; ~30.3 Hz sim
verified, ~11 KB/frame) — this is what made the diagnosis possible.

## 2026-07-13 (later 2) — phototex re-fly, analyzer magnitude fix, D5 axis map

Executed Steps 0–1 of the approved plan (texture renders under `ogre`
classic; `flat_rural_phototex_noon` built + camera-proofed). First phototex
legx run (`20260713_122205`, identity axis_map on the wire) proved the world
is fixed: +X body at 0.5 m/s → +0.209 rad/s on the camera x-axis, cross ≈ 0,
vs v/AGL = 0.197 (1.06×). The estimator reads the textured ground correctly.

Second finding this pass: the analyzer was mis-measuring magnitude, not just
the world. `analyze_flow_bridge_openloop.py` selected "moving" samples by
`|flow| > median`, dragging in takeoff/acceleration transients (reported
ratios 1.35× then 0.003×). Rewrote it to take the longest contiguous
above-threshold window from the RAW Gazebo truth CSV (same gz sim-time base
as the sent flow), trim `--leg-trim-s` (2.5 s) each end, and average only the
steady plateau; read truth velocity via ENU→NED (NED_x=ENU_y); and enforce
`magnitude_ok` in the pass criteria (previously computed but ungated).
Re-run on the plateau: legx magnitude 1.06×.

D5 (camera→body axis map) is now an empirical, falsifiable test. legx raw
puts flow on camera x; PX4 wants +X body on integrated_y. Map `-yx`
(integrated_x=−cam_y, integrated_y=+cam_x) fixes legx; set in all three 8G
scenarios. Confirmation: legx `20260713_125325` in flight, legy next. D5
closes only if BOTH orthogonal legs gate green under the same `-yx` — under
`-yx`, legy (+Y body) must show integrated_x dominant and NEGATIVE, else the
map is inconsistent.

Standing operator rule recorded: every run keeps BOTH the QGroundControl
MAVLink link (runner default, no `--no-qgc`, target 100.109.200.5) and the
gz-web viz connection (raw runner bridge on 9003; browser through the
enum-patch proxy on 9002 to avoid the gray gz-web missing-enum failure) live
for real-time monitoring.

## 2026-07-13 (later 3) — D5 CLOSED (both leg gates green) + lidar base-slab fix

Both open-loop leg gates pass under `axis_map: -yx`:
- legx (`20260713_130549`, +X/North): dominant integrated_y +0.205, sign +,
  magnitude_ratio 1.073 → openloop_ok.
- legy (`20260713_131822`, +Y/East): dominant integrated_x −0.222, sign −,
  magnitude_ratio 1.163 → openloop_ok.
Both match the PX4 convention; delivery 1.0, latency median 43 ms, OF_DELAY
candidate 109 ms both legs. Truth alignment healthy (≤0.22 m mean, ≤0.47 m max).
D5 (camera→body axis map) is empirically gated on two orthogonal translations.

Two tooling bugs fixed: (a) runner passed `--axis-map -yx` positionally →
argparse ate the leading-dash value and the bridge died with zero flow (run
`125325` lost its bridge) → now `--axis-map=VALUE`; (b) analyzer AGL used
`range_m.median()` over a column containing `inf` → median could land on inf
and zero the expected rate → now median over finite positive readings, reports
`agl_finite_fraction`.

Rangefinder/world bug found via `agl_finite_fraction` (blocks 8G.3, not the
flow gates): on legy the RAW downward gpu_lidar was inf 82% of the time (legx
0%). Truth correlation shows it is NOT attitude (roll/pitch ≈ 0°) — it tracks
world +x and persists while the vehicle sits landed at wx≈15 m; the camera held
quality 255 over the same ground. Cause: the single-point lidar slips through
the hairline gaps between the 36 thin (4 mm) tile visuals, asymmetric in x.
First fix attempt emitted a visual-only `ground_base`; legy re-fly
`20260713_134012` improved only to 25.8% finite RAW range, so it did NOT
verify the repair. Final fix: `build_gazebo_world.py` emits the base slab as
both collision and visual geometry (full size, 0.2 m thick, top just below the
tile top). World regenerated; legy re-fly `20260713_143843` verifies finite
lidar before 8G.3: RAW rangefinder 4440/4440 finite (100.0%) through
`t_sim=100.84s`; bridge range 644/644 finite; analyzer
`agl_finite_fraction=1.0`, `openloop_ok=True`, `magnitude_ratio=1.063`,
OF_DELAY candidate 111 ms. Runner `accepted=False` only because the generic
flight-duration gate requested 310 s and the vehicle landed after 36.73 s;
postprocess accepted the truth/ULog extraction.

## 2026-07-13 (later 4) — 8G.3 primary closed-loop fusion GREEN + webgz preserved

Closed-loop phototex primary `20260713_151744` passes with the correct operator
connection setup preserved: browser/proxy on 9002, raw runner `gz-launch`
bridge on 9003 (direct raw 9002 is the gray gz-web missing-enum path).
Runner accepted the flight: `accepted=True`, `landing_ok=True`,
`qgc_connected=True`, `flow_bridge_sent_rows=813`, `flow_bridge_ok=True`,
`ulog_airborne_duration_s=62.72` for `requested_airborne_s=60.0`,
`ulog_max_height_up_m=2.505`.

Lidar/base-slab repair held under closed-loop flight: RAW rangefinder
`5580/5580` finite (100.0%) through `t_sim=123.5s`; ULog distance sensor
`3649` rows, max 2.510 m, height agreement 0.006 m.

ULog optical-flow fusion analyzer is green:
`flow_fusion_ok=True`, `sensor_optical_flow_rows=525`,
`aid_src_optical_flow_rows=527`, `cs_opt_flow_active_count=65/95` (68.4%),
`flow_fused_count=443`, `flow_rejected_count=21`,
`flow_rejected_over_fused=0.0474`, `xy_reset_counter_delta=2`.
Postprocess accepted truth/ULog extraction:
`truth_rows=23401`, `truth_duration_s=112.672`, `csv_count=10`.

Two runner lifecycle bugs were fixed during the 8G.3 cleanup. First,
local-hold timing now waits on PX4 sim time (`vehicle_land_detected.timestamp`)
instead of wall-clock sleep; the earlier `20260713_145514` attempt looked like
~68 s wall-clock in QGroundControl but only logged 12.888 s airborne in ULog.
Second, recorder / flow bridge / offboard setpoint sender durations now share
a local-hold auxiliary wall budget (`sim_time_wall_multiplier`, default 15);
without this, `20260713_150539` lost the offboard sender at its old 390 s wall
cap. Accepted run `151744` launched all three helpers with `--duration-s
1222.0`. Also replaced the unconditional post-land `sleep(land_timeout_s)`
with a landing-complete poll so future runs do not sit for the full timeout
after `Disarmed by landing`.

## 2026-07-13 (later 5) — 8G.4 wrap + pre-8I serefli flow-texture proof

8G.4 documentation/handoff completed: phase README now marks Phase 8G
Accepted and current work as pre-8I / 8I. Phase 8I status now reflects that
8G is no longer blocking; flat-world prep can use `flat_rural_phototex_noon`.

Started the pre-8I `serefli_koschisar` texture repair. Added
`scripts/worlds/add_serefli_flow_texture_overlay.py`, which generates
`generated_worlds/terrain/serefli_koschisar_flowtex/` by preserving the
original heightmap collision, copying the terrain assets, texturing the launch
pad visual with `ground_speckle_2048.png`, and adding a visual-only
flow-detail overlay over the route area. Smoke test accepted:
`serefli_koschisar_flowtex: launch=True spawn=True accepted=True`.

Pre-8I proof scenario
`experiments/configs/mvp/scenarios/phase8i_pre_serefli_flowtex_camera_proof.yaml`
accepted in run `20260713_154712`: `accepted=True`, `landing_ok=True`,
`qgc_connected=True`, `camera_probe_ok=True`, `rangefinder_probe_ok=True`,
`flow_recording_frames=590`, ULog airborne 20.808 s, max height 2.484 m,
ULog distance sensor 1417 rows with height agreement 0.012 m. Postprocess
accepted (`truth_rows=3848`, `truth_duration_s=28.698`, `csv_count=10`).
Rangefinder CSV is finite `974/974` (100.0%) through `t_sim=33.52s`.

Texture evidence: sampled recorded frames are no longer featureless. SIFT
counts on the repaired camera stream: `frame_000020=45`, `000080=52`,
`000140=56`, then the overlay reaches the feature cap:
`frame_000190=400`, `000300=401`, `000500=401`. Visual inspection of
`frame_000300` shows the downward camera sees the green flow-detail surface,
not the old gray pad / coarse aerial texture.

Terrain RTF is slower than flat (~0.05 during this proof), so the runner
`sim_time_wall_multiplier` default was increased from 15 to 30 and reused for
auto-hover and local-hold sim-time wait timeouts. Webgz cleanup remained
healthy: raw 9003 closed after the run; only the 9002 enum-patch proxy remains.

## 2026-07-14 — Phase 8I flat comparison plots/report generated

Created the flat Phase 8I A/B/C/D comparison report at
`experiments/comparisons/phase8i_flat_phototex_abcd_20260714/report.md`.
The report now includes a compact `Current Connection Map` covering the live
optical-flow chain, separate TF03/range chain, frame table, actual
frequencies, and `axis_map=-yx` meaning/correctness.
Artifacts include `comparison_metrics.*`, `aiding_setup.*`,
`stream_frequencies.*`, `lidar_truth_metrics_d.*`,
`lidar_truth_matched_d.csv`, route overlays (`routes_xy_overlay.png`,
`routes_xy_nearfield.png`, `route_xy_ekf_vs_truth.png`), horizontal/height
error time series and bar plots, `flow_d_diagnostics.png`, plus
`lidar_truth_height_d.png` and `lidar_truth_scatter_d.png` for D lidar input
vs Gazebo truth. Also embedded three real D camera samples with metadata in
`camera_samples_d.json`: early invalid/blue `frame_000300` at
`t_sim=21.780s`, valid textured-ground `frame_000500` at `t_sim=28.380s`,
and late edge/out-of-field `frame_000900` at `t_sim=41.580s`.

Flat comparison headline: A GNSS-on mean horizontal error 0.062 m; B GNSS-loss
no-aiding 23.792 m; C GNSS-loss ideal odom 0.142 m; D GNSS-loss live flow
100.986 m with 225.512 m end error and 39.837 m mean height error. D moved
real optical-flow data into PX4 (`455` bridge rows, `146` fused, `51`
rejected), but the flat-world thesis gate remains failed: live flow does not
yet beat the no-aiding baseline, so terrain 8I stays paused until D is
repaired or the gate is deliberately redefined.

Setup/frequency snapshot: A/B have no aiding; C uses truth-fed external
odometry (`EKF2_EV_CTRL=5`, `EKF2_HGT_REF=2`, configured 30 Hz, actual sent
stream 94.39 Hz in sim time); D uses live SIFT flow (`axis_map=-yx`,
`EKF2_OF_CTRL=1`, `EKF2_OF_QMIN=17`, configured 10 Hz, actual bridge stream
7.58 Hz in sim time). D lidar/truth comparison shows finite ranges are scaled
correctly after subtracting the 0.174 m landed offset: raw recorder and bridge
input both have ~0.041 m mean absolute height error against Gazebo truth on
finite rows. The practical problem is finite coverage during divergence
(raw 45.1%, bridge 40.7%), not finite-row lidar scale.

Current D flow algorithm documented in the report: Gazebo RGB camera stream is
converted to grayscale, downscaled to `max_width=480`, processed by OpenCV
SIFT (`n_features=400`, Lowe ratio 0.75, min 8 matches), reduced to median
matched-pixel displacement, converted to integrated radians via
`rad=px/focal_px` (`hfov_rad=1.74`), mapped with `axis_map=-yx`, quality
remapped from `[20,100]` to `[1,255]`, and sent as MAVLink
`OPTICAL_FLOW_RAD`. The flow message carries `distance=-1`; PX4 height aiding
comes from the separate TF03-style `distance_sensor`.

Input-validity diagnosis added after visual review: D has a useful middle
window, but not a stable full-run input stream. At the start, the flow bridge
is already running while the camera/lidar are not yet seeing useful ground
texture (`quality=0`, `n_matches=0`, range near `0.174m` until about
`t_sim=23.067s`). Useful SIFT matches begin around `t_sim=23.199s`. Later,
the estimator runaway carries the vehicle to the edge of the finite phototex
field; raw lidar first becomes `inf` at `t_sim=41.14s` after the last finite
reading of `27.815m`, and camera `frame_000900` shows the ground texture
leaving the image. D repair should therefore gate flow activation on valid
texture/range after takeoff and add a containment/abort or larger field before
burning terrain runs.

## 2026-07-14 (later) — D repair loop: EKF feed path fixed, OF acceptance still open

Root cause found for the "EKF not receiving our optical flow" symptom: the
bridge used range/quality gates while landed, so it could stay completely
silent before arming. PX4 then did not reliably start the
`vehicle_optical_flow` path for the later valid samples. Added
`prime_on_unsent` to `scripts/sim/flow_mavlink_bridge.py`: gated-out samples
can now send zero-integrated, quality-0 `OPTICAL_FLOW_RAD` prime packets while
CSV accounting separates real valid samples (`sent`) from MAVLink activity
(`mavlink_sent`) and records `n_prime_sent`. The runner now passes
`--prime-on-unsent` from scenario YAML and records the setting in status JSON.

Current Case D baseline YAML:
`experiments/configs/mvp/scenarios/phase8i_d_loss_flow_flat_rural_phototex_noon.yaml`.
The active flow setup is `rate_hz=20`, `max_width=320`,
`sift_n_features=180`, `sift_ratio=0.75`, `sift_min_matches=8`,
`axis_map=-yx`, send range gate `[0.8, 60.0] m`, send quality gate `20`,
`reset_on_unsent=true`, `prime_on_unsent=true`, `EKF2_OF_CTRL=1`,
`EKF2_OF_QMIN=17`. The control profile is still the slow local-Y route,
not hover: `vy_m_s=0.2`, `z_m=-2.5`, `skip_landing_command=true`.
No `EKF2_OF_GATE`, `EKF2_OF_N_MIN`, or `EKF2_OF_N_MAX` override is active
after reverting the exploratory tuning runs.

Accepted baseline repair run `20260714_170829` proves end-to-end EKF feeding:
GNSS loss effective at 19.0 s after takeoff, 527 real flow packets plus 53
prime packets, ULog `sensor_optical_flow` and `vehicle_optical_flow` at about
14.00 Hz, `estimator_aid_src_optical_flow` at about 13.93 Hz, and 408 fused /
133 rejected OF aid samples. EKF-vs-Gazebo horizontal error was 0.787 m mean
and 1.841 m max; lidar stayed healthy (`ulog_distance_sensor_rows=2299`,
max 2.365 m, height diff 0.150 m). Strict analyzer still marks flow fusion
not green because the rejected/fused ratio is 0.326.

Exploratory run `20260714_172753` with `EKF2_OF_GATE=5.0` was accepted and
generated plots under
`experiments/runs/20260714_172753_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/phase8i_last_run/`.
It reduced OF rejects (432 fused / 97 rejected) but worsened route max error
to 5.121 m and showed reset/route-bending behavior. Treat it as diagnostic,
not the current baseline. The scenario YAML was restored after this run.

Invalid/parked D repair runs: `20260714_171848` soft OF noise tuning
(`EKF2_OF_N_MIN=0.30`, `EKF2_OF_N_MAX=0.70`) drifted badly and was stopped;
`20260714_172628` failed startup due a stale PX4 socket/process. Stale PX4
state was cleaned. Current WebGZ rule is to preserve the old user-facing
proxy on `9002`; runner-owned raw `9003` should be free before and after
runs. Disk is tight: `/opt` and `/tmp` are about 99% used with roughly 612 MB
free, so prune only invalid/failed runs before more batch work.

Current interpretation: yes, PX4/EKF2 is being fed with our camera flow now,
but the delivered EKF rate is about 14 Hz rather than the configured 20 Hz,
and the remaining technical problem is optical-flow acceptance after GNSS
loss: innovations/rejections/resets bend the local estimate, and the position
controller follows that estimate. Next repair work should compare against
`170829`, keep `vy=0.2` timing consistent, and focus on message timing/frame
acceptance/OF delay before changing broad EKF gates again.

## 2026-07-15 — D repair closed: timing falsified, Case D accepted as duration-limited

Closed the 8I Case D repair loop. Result: D is accepted **with limitations** as
a short-outage capability, not a robust GNSS-denied solution.

Diagnosis (new `scripts/analysis/diagnose_flow_rejections.py`, run on `170829`):
the flow itself is accurate against Gazebo truth (truth expected-flow RMS
0.57 rad/s vs OF observation ~0.46; mean scale = `v/h`). All 133 rejects are
post-loss; signature is a loop-gain limit-cycle (`corr(innov,obs) = -0.92`,
vehicle physically oscillating ~0.8 m/s vs 0.2 commanded), not bad flow.

Delay lever falsified. Added `flow_bridge.ekf2_of_delay` -> `EKF2_OF_DELAY`
plumbing (runner + scenario + status, mirrors `ekf2_of_gate`). Testing 111 ->
45 ms made fusion worse (reject/fused `0.326 -> 1.156`; run `20260715_102252`,
pruned). True async-pipeline latency >= 111 ms; delay held at 111. `EKF2_OF_DELAY`
timing is not the fix.

World enlarged 120 -> 240 m (`flat_rural_phototex_noon.yaml`, 144 tiles,
+/-120 m textured; SDF regenerated) to remove any off-field confound. Confirmed
the nominal good D (`170829`) stays within 9 m with 100% finite rangefinder;
the earlier blue-sky/inf frames were from diverged runs, not a small world.

Clean contemporaneous flat batch `20260715_110133`
(`phase8i_gnss_denied_flow_abcd_flat_60s.yaml`, equal 60 s / 50 s-post-loss
windows, enlarged world): A `110137` 0.110 m and C `112223` 0.194 m clean; B
`111229` failed as the intended anchor; D `113204` **diverged** (max alt 29.7 m,
max horiz 44 m, reject/fused 1.29, flow-starved 333 samples).

Robustness characterization at the 50 s profile: **4/4 diverged**. D re-run
(delay=111, light `phase8i_d_varirun_*` scenario, frames off): `113204`,
`121400`, `122221`, `123107` all diverge, reject/fused 1.29 / 1.42 / 2.58 / 1.38,
flow-starved 239-333 samples (vs the bounded `170829`'s 527 at a 35 s window).
Positive-feedback failure: divergence -> vehicle leaves good texture / tilts /
climbs -> fewer usable SIFT samples -> weaker aiding -> more divergence.

Accepted conclusion: our camera+TF03 optical-flow aiding holds GNSS-denied
horizontal position to ~0.8 m for a ~35 s outage (`170829`, beats no-aid B by
~32x, same order as ideal-odom C 0.17 m; short-outage comparison at
`experiments/comparisons/phase8i_flat_phototex_abcd_final_20260715/`), but the
optical-flow-only control loop is marginally stable and reliably diverges over a
50 s outage. The only remaining lever is loop damping (`EKF2_OF_N_MIN/MAX`),
deferred by decision. Terrain A/B/C/D stays paused. Disk managed by pruning
invalid/superseded runs (operator sign-off each time); QGC + gz-web kept live on
every run. Prunable now: the 3 light `phase8i_d_varirun_*` runs and the diverged
batch D `113204`.

## 2026-07-16 — Housekeeping: pruned superseded 8I/8G runs + Phase 8A raw ULogs

Freed disk before choosing the next science direction. `/` was **99% full,
439 MB free** (`experiments/runs/` 6.8 G across 99 folders). Pruned two approved
tiers (operator sign-off on the exact list; every accepted result and cited
anchor protected):

- **Tier 1 — superseded / failed 8I + incomplete 8G (~760 MB):** `20260714_145016`
  (first full D, failed the thesis gate; superseded by `170829`), `20260713_150539`
  (incomplete 8G fusion, no status JSON; superseded by accepted `151744`),
  `20260715_113204` (diverged batch D), `20260714_121003` (unaccepted 20 s D),
  `20260714_165428` (flew to 42 m, out of world), the 3 light `phase8i_d_varirun_*`
  runs (`121400`/`122221`/`123107`), and two failed startup stubs
  (`20260714_065847`, `_065937`).
- **Tier 3 — Phase 8A Case-C exploration raw ULogs (~2.9 GB):** the 29
  `experiments/runs/20260709_*phase8a*` folders (compare / stress_c / realerr_c
  variants). Phase 8A is frozen; its conclusions, per-case metrics, and plots are
  fully preserved in the self-contained batch dirs
  (`batches/20260709_104321_…real_error_matrix`, `…071312`/`…072307`) — verified no
  symlinks into `runs/` and unreferenced by any comparison, so only raw per-run
  logs were removed. Frozen anchor still reproducible from
  `real_error_summary.csv` (0.075 / 0.113 / 0.215 m).

Result: **439 MB -> 4.0 G free** (99% -> 89%), run count 99 -> 59. Protected set
verified intact after deletion: final-comparison A/B/C/D (`131232`/`132230`/
`133217`/`170829`), accepted 8G `151744`, and
`experiments/comparisons/phase8i_flat_phototex_abcd_final_20260715/` (report +
15 plots). Kept the OF-gate diagnostic run `20260714_172753` (documented negative
result). Next science direction (terrain A/B/C/D vs long-outage D damping vs
Phase 12 report) is a separate decision, now unblocked on disk.

## 2026-07-16 — Phase 8J implemented: stock PX4 flow benchmark + LK replay path

Implemented the Phase 8J scaffolding to compare PX4 stock Gazebo optical flow
against the current DATABOSS SIFT bridge, then test an LK bridge estimator on
identical recorded frames before any live claim. New doc:
`docs/phases/phase_08j_stock_flow_benchmark.md`.

Runner changes: new `stock_flow:` scenario section for `gz_x500_flow`, high-rate
optical-flow ULog profile when stock or bridge flow is active, boot-time stock
flow params (`SYS_HAS_GPS=1`, `SIM_GZ_EN_FLOW=1`, `SIM_GZ_EN_LIDAR=1`,
`EKF2_OF_CTRL=1`) so the stock reference can start GNSS-on and then use the
accepted runtime outage command `param set SIM_GPS_USED 0`.

New configs: stock GNSS-on proof, stock short outage, stock 50 s outage, LK
DATABOSS bridge candidate, and `phase8j_stock_flow_benchmark.yaml`. New tools:
`scripts/analysis/report_phase8j_stock_flow_benchmark.py` and
`scripts/analysis/compare_flow_estimators_replay.py`. New estimator:
`src/databoss_sim/flow/lk_estimator.py` with OpenCV GFTT + pyramidal LK,
forward/backward consistency, displacement-consistency rejection, active track
refresh, and the existing `FlowSample`/MAVLink bridge contract.

Stage B smoke replay completed on accepted run `20260714_170829` with output at
`experiments/comparisons/phase8j_lk_replay_170829/`: `lk` processed the same
1397 frames with 1079 valid samples / 0.999 valid fraction, mean compute
0.0142 s, p95 compute 0.0195 s, mean truth-speed absolute error 0.165 m/s.
`sift` had 1077 valid samples / 0.771 valid fraction, mean compute 0.0389 s,
p95 compute 0.0501 s, mean truth-speed absolute error 0.160 m/s. Read: LK is
worth a live candidate run on coverage/rate, but stock-flow Stage A is still
the next benchmark step.

## 2026-07-16 — Phase 8J stock PX4 flow batch run

Ran `phase8j_stock_flow_benchmark.yaml` as `px4`:
`experiments/batches/20260716_072127_phase8j_stock_flow_benchmark/`.
Batch result is **Rejected** only because case 1 (`stock_gnsson_proof`,
`20260716_072130`) got `rangefinder_probe_ok=False` from a live `inf` sample;
the run still flew and ULog distance sensor proof passed
(`ulog_distance_sensor_ok=True`, 3211 rows).

The actual GNSS-loss benchmark cases accepted:

- Short outage stock run `20260716_072559`: GNSS loss detected, 2594
  `sensor_optical_flow` rows at 50.02 Hz, `cs_opt_flow_active_fraction=0.6667`,
  1878 fused / 0 rejected, reject/fused 0.0, XY reset delta 3. Gazebo-truth
  alignment accepted: mean/max horizontal error 0.511 / 1.600 m; mean height
  error 0.446 m.
- 50 s outage stock run `20260716_072949`: GNSS loss detected, 3317
  `sensor_optical_flow` rows at 50.01 Hz, `cs_opt_flow_active_fraction=0.7037`,
  2608 fused / 0 rejected, reject/fused 0.0, XY reset delta 2. Gazebo-truth
  alignment accepted: mean/max horizontal error 0.709 / 1.798 m; mean height
  error 0.205 m.

Updated comparison report:
`experiments/comparisons/phase8j_stock_vs_databoss_flow/report.md`. Current
read: PX4 stock Gazebo flow is a strong simulation reference and stays bounded
over the 50 s outage where DATABOSS SIFT diverged in Phase 8I. The gap is
consistent with stock flow delivering 50 Hz KLT-style flow with zero aid-source
rejects versus DATABOSS SIFT's lower-rate, rejection-prone path. Next step is a
live LK DATABOSS candidate run only after accepting the Stage B replay evidence.

## 2026-07-16 — Phase 8J report filled with fresh DATABOSS SIFT 50 s row

Ran the current baseline SIFT bridge scenario
`phase8i_d_loss_flow_flat_rural_phototex_noon.yaml` as a 50 s post-loss
observation run:
`experiments/runs/20260716_074422_phase8i_d_loss_flow_flat_rural_phototex_noon_pxh_takeoff_land_truth`.
Operator requested no landing wait; the run completed the observation window,
exited rejected, and preserved ULog/truth/flow evidence. Manual postprocess and
full-window alignment succeeded.

SIFT 50 s result: GNSS loss detected, bridge sent 326 rows, flow recording 1819
frames, ULog `sensor_optical_flow` 586 rows at 10.52 Hz, `cs_opt_flow` active
fraction 0.3182, 116 fused / 301 rejected, reject/fused 2.5948, XY reset delta
3. Truth alignment: mean/max horizontal error 207.506 / 1479.910 m; mean/max
height error 14.083 / 186.728 m; max logged height 42.94 m; distance sensor
proof failed after divergence. Verdict: **diverged**.

Updated `experiments/comparisons/phase8j_stock_vs_databoss_flow/report.md` with
all four rows: stock short, SIFT short, stock 50 s, SIFT 50 s. Direct read:
stock 50 s stayed bounded (max horizontal 1.798 m, 2608 fused / 0 rejected at
50.01 Hz), while SIFT 50 s diverged (max horizontal 1479.910 m, reject/fused
2.595 at 10.52 Hz). The failure mechanism is not a missing bridge; it is
low-rate/rejection-prone flow feeding a positive feedback loop that exits the
valid range/texture regime.

Rangefinder timeline checked for `20260716_074422` and saved at
`experiments/comparisons/phase8j_stock_vs_databoss_flow/rangefinder_timeline_20260716_074422.md`.
Bridge range first becomes usable at `t_sim=22.10 s`, first flickers to `inf`
at `43.30 s`, last bridge-sent sample is `48.147 s`, last usable finite range
is `49.680 s`, and sustained `inf` begins at `49.700 s`. The world is
`240 x 240 m` (`+/-120 m` half extent); vehicle center first crosses `+/-120 m`
at `47.792 s`. Since first `inf` occurs earlier at `x=71.4 m`, `y=-8.0 m`, the
first range failure is not simply the center leaving the map: vehicle tilt is
already about `87 deg`, so the downward ray is effectively sideways. After map
exit, range becomes mostly/sustained invalid.

## 2026-07-16 — Phase 8J 3x long replicate stock-vs-current-SIFT benchmark

Created repeat batch
`experiments/configs/mvp/batches/phase8j_stock_vs_sift_50s_replicates.yaml`
and report tool `scripts/analysis/report_phase8j_stock_vs_sift_replicates.py`.
Ran six long no-land-wait runs on the existing 240x240 m
`flat_rural_phototex_noon` world:
`experiments/batches/20260716_081558_phase8j_stock_vs_sift_50s_replicates/`.

Batch result: **Rejected** overall because the SIFT divergent repeats correctly
failed flight acceptance, but all six evidence folders were preserved. Report:
`experiments/comparisons/phase8j_stock_vs_sift_50s_replicates/report.md`.

Results:

- PX4 stock `gz_x500_flow`: bounded 3/3. Worst max horizontal error 8.368 m;
  sensor optical flow 50.01 Hz in all repeats; flow rejects 0/0/0; range OK in
  all repeats.
- Current DATABOSS SIFT bridge: bounded 1/3, diverged 2/3. Divergent max
  horizontal errors 459.793 m and 724.171 m; bounded repeat max horizontal
  2.543 m. Mean sensor optical-flow rate 11.76 Hz, mean reject/fused 1.332.
- SIFT rate diagnostics: camera frames arrived at 30.3 Hz with 33 ms sim
  spacing. Current `flow_bridge.rate_hz=20` gives `min_period=50 ms`, so the
  bridge aliases valid sends to every other frame: 66 ms / 15.15 Hz. Divergent
  repeats then degrade below that as range/quality collapse. SIFT median
  compute was 72.8, 104.5, and 94.35 ms wall; at RTF 0.088-0.116 this is
  26-29% of the wall budget per camera frame, so compute explains wall-clock
  crawl, not the 15.15 Hz sim-time alias.

Conclusion: stock PX4 Gazebo flow is the simulation reference and remains
bounded with 50 Hz flow and zero optical-flow aid-source rejects. Current SIFT
is variance-heavy: it can finish one long outage when range/features stay
healthy, but 2/3 repeats collapse after range/quality degradation and EKF
rejection. Next technical move is a one-variable bridge rate test
(`rate_hz: 40` or uncapped) plus LK replay/live candidate, not broad EKF gate
tuning.

## 2026-07-16 — Phase 8J live LK replicate batch added to report

Ran the LK bridge scenario
`experiments/configs/mvp/scenarios/phase8j_d_loss_flow_lk_flat_rural_phototex_noon.yaml`
three times on the same 50 s no-land-wait outage profile:
`experiments/batches/20260716_090906_phase8j_lk_50s_replicates/`.
Batch result: **Rejected**, accepted 0/3. Run folders:

- `experiments/runs/20260716_090909_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- `experiments/runs/20260716_092040_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- `experiments/runs/20260716_093220_phase8j_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`

Regenerated the comprehensive report with stock, SIFT, and LK rows plus plots:
`experiments/comparisons/phase8j_stock_vs_sift_50s_replicates/report.md`.
Plots are in
`experiments/comparisons/phase8j_stock_vs_sift_50s_replicates/plots/`.

LK result: all three LK repeats diverged by Gazebo truth. Max horizontal errors
were 132.604 m, 82.110 m, and 96.772 m; max height errors were 98.427 m,
82.410 m, and 46.472 m. LK did improve bridge compute margin: median compute
52.45-56.7 ms wall versus SIFT's 72.8-104.5 ms, with the same 30.3 Hz camera
source and the same 20 Hz cap alias to 66 ms. However, ULog evidence shows
LK did **not** activate EKF optical-flow fusion: `cs_opt_flow_active_fraction=0`
and 0 fused / 0 rejected optical-flow aid-source samples in all three rows.

Conclusion: LK is not accepted for live GNSS-denied navigation yet. It is a
compute improvement but currently a fusion/integration failure. Next narrow
task is LK fusion-debug against the accepted SIFT row and PX4 stock reference:
MAVLink fields, quality scaling, integrated-flow signs/magnitudes, integration
time, distance coupling, and EKF innovation gate evidence. Only after LK fusion
is active should the bridge-rate cap be changed to 40 Hz or uncapped for a
one-variable rate test.

## 2026-07-16 — Phase 8L Gate 2 scene proof repaired and accepted

Implemented the Phase 8L sensor sanity ladder machinery:
`scripts/analysis/sensor_contract_report.py`, opt-in runner integration, new
Phase 8L scenario/batch configs, and
`docs/phases/phase_08l_sensor_sanity_ladder.md`.

Gate 1 static contract audit saved:
`experiments/inspections/phase8l_static_contract_audit.json`.

First Gate 2 scene hover run
`experiments/runs/20260716_172812_phase8l_scene_hover_flat_rural_phototex_noon_pxh_takeoff_land_truth`
initially rejected because the first report sampled the entire camera capture.
That included early takeoff frames: `frame_000000.jpg` was sky-like while the
rangefinder still read `0.174 m`. Mid-hover and end frames were downward
textured ground. Fixed the analyzer to infer a hover-valid scene window from
rangefinder data and use that window for scene pass/fail while preserving
full-capture diagnostics.

Recomputed old evidence after the fix:

- hover window: `26.64-49.38 s`
- camera frames in window: `689` at `30.35 Hz`
- hover texture cell fraction mean: `1.0`
- hover feature count median/min: `600 / 600`
- hover range median: `2.5019 m`
- full-capture texture cell fraction remained `0.75`, exposing the early
  transition frames instead of hiding them

Fresh Gate 2 rerun accepted:
`experiments/batches/20260716_190312_phase8l_01_scene_hover_sanity`, run
`experiments/runs/20260716_190315_phase8l_scene_hover_flat_rural_phototex_noon_pxh_takeoff_land_truth`.
Key metrics: scene window `26.36-51.2 s`, 753 camera frames at `30.34 Hz`,
texture cell fraction `1.0`, feature median `600`, range finite fraction
`1.0`, range median `2.4992 m`, truth horizontal max `0.1146 m`.

Interpretation: the hover scene/camera/range contract over origin is good. The
original rejection was a report-windowing bug, not a too-small-world or bad
hover texture failure. The next hard stop is Gate 3: roll/pitch/yaw
attitude/rotation proof is not yet runnable because the current offboard sender
supports position/velocity setpoints only. Do not proceed to axis, timing,
fusion, or GNSS-loss smoke until Gate 3 tooling exists or the limitation is
explicitly accepted.

## 2026-07-16 — Phase 8L Gate 3 attitude pose proof accepted

Implemented standalone Gate 3 tool
`scripts/worlds/prove_phase8l_attitude_pose.py`. The tool creates temporary
pose-specific Gazebo worlds with a static `model://x500_cam_lidar_down` include
at 2.5 m AGL, records camera and LiDAR topics with
`scripts/sim/record_camera_frames.py`, and writes
`attitude_pose_report.json` / `.md` under `experiments/inspections/`.

During smoke testing, standalone model include initially failed because
`x500_cam_lidar_down` had no `model.config`; added it in both the DATABOSS
source model and deployed PX4 Gazebo model folder. The first tool version also
pointed at the wrong Gazebo plugin directory and treated a post-recording
Gazebo Python binding shutdown return code as failed evidence. Fixed both:
plugin path now matches the PX4 runner
`build/px4_sitl_default/src/modules/simulation/gz_plugins`, and `record_ok`
means usable camera/range CSV evidence exists while preserving
`record_returncode`.

Accepted full Gate 3 report:
`experiments/inspections/20260716_213752_phase8l_attitude_pose_proof/attitude_pose_report.md`.
JSON:
`experiments/inspections/20260716_213752_phase8l_attitude_pose_proof/attitude_pose_report.json`.

Result: **Accepted**, 13/13 poses passed:

- level
- roll `+/-5 deg`, `+/-10 deg`
- pitch `+/-5 deg`, `+/-10 deg`
- yaw `+/-45 deg`, `+/-90 deg`

Key metrics across poses:

- camera texture cell fraction: `1.0` for every pose
- sky-like fraction: `0.045-0.083`
- feature median: `600`
- LiDAR finite fraction: `1.0`
- LiDAR median range: `2.687-2.744 m`
- attitude-corrected range error: `0.169-0.205 m`, within the `0.30 m` gate

Interpretation: camera/LiDAR pose is sane for small roll/pitch and yaw
attitudes; the camera remains ground-looking and textured, and LiDAR remains
finite. Dynamic rotation-only gyro proof is still separate and blocked until a
yaw-rate/attitude setpoint path exists, but Gate 4 open-loop LK axis proof is
now unblocked.

Next command:

```bash
cd /opt/databoss_px4_sim || exit 1
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_02_openloop_axis_lk_four_legs.yaml --continue-on-fail
```

## 2026-07-16 — Phase 8L Gate 4 LK translation axis proof accepted

Ran the four signed open-loop LK axis legs with GNSS on and
`EKF2_OF_CTRL=0`.

Accepted batch:
`experiments/batches/20260716_222415_phase8l_02_openloop_axis_lk_four_legs`.

Result: **Accepted**, 4/4 cases passed.

Per-leg axis contract metrics:

- `+PX4 X/North`:
  `experiments/runs/20260716_222419_phase8l_openloop_lk_px_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant `+0.12457 rad/s`, cross `+0.00197 rad/s`
  - magnitude ratio `1.0517`
  - range finite fraction `1.0`
- `-PX4 X/North`:
  `experiments/runs/20260716_223525_phase8l_openloop_lk_nx_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant `-0.12494 rad/s`, cross `+0.00039 rad/s`
  - magnitude ratio `1.0413`
  - range finite fraction `1.0`
- `+PX4 Y/East`:
  `experiments/runs/20260716_224619_phase8l_openloop_lk_py_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant `-0.12506 rad/s`, cross `-0.00094 rad/s`
  - magnitude ratio `1.0445`
  - range finite fraction `1.0`
- `-PX4 Y/East`:
  `experiments/runs/20260716_225726_phase8l_openloop_lk_ny_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant `+0.12671 rad/s`, cross `-0.00146 rad/s`
  - magnitude ratio `1.0465`
  - range finite fraction `1.0`

Interpretation: camera pose, LiDAR pose, and LK translation axis mapping are no
longer the likely source of the 8K GNSS-loss rejection. The current
`axis_map: "-yx"` contract is accepted for translation. Gate 5 rotation/gyro
sanity remains the next hard stop before timing sweep, GNSS-on fusion proof, or
GNSS-loss smoke.

Harness/config changes made during the run:

- `scripts/runner/auto_takeoff_land_pxh_truth.py` now treats
  `control.skip_landing_command: true` as `landing_required=False`.
- Gate 4 scenario side camera recording was reduced to `rate_hz: 5`,
  `max_width: 320` to fit the evidence on the nearly full `/opt` partition;
  the LK bridge settings stayed unchanged.
- Freed space by deleting interrupted/non-accepted Gate 4 partials and failed
  non-accepted Phase 8I artifacts; accepted 8J/8K evidence was not touched.

## 2026-07-17 — Disk cleanup to unblock Phase 8L Gate 5

`/` was at 99% (462M free). Cleanup performed, no accepted evidence deleted:

- systemd journal vacuumed to 200M, apt cache cleaned, `/var/crash` cleared
  (~2.5G freed).
- All `px4_gazebo_console.log` files >1M across `experiments/runs/` gzipped in
  place (82 files, ~915M freed). Content preserved as `.log.gz`; any tool that
  reads the raw console log must decompress or use `zcat`/`zgrep`.
- Per user instruction, `logs/flight.ulg` deleted from 22 run folders that are
  unreferenced in PROJECT_LOG, phase docs, batch summaries, and comparisons
  (10 ULogs existed, ~180M). The folders themselves and their other artifacts
  were kept. Affected runs are pre-8L iterations from 2026-07-09..07-16
  (phase8b/8c/8f/8g0/8g/8i-pre/8i dupes/8j interrupted, phase9a probes).

Result: 462M free → 4.0G free (89% used).

Limitation: the 22 unreferenced folders no longer have ULog evidence; they were
already non-load-bearing. Referenced/accepted runs are untouched except for
console-log gzip.

Next action: implement yaw-rate offboard setpoint mode in
`scripts/runner/send_offboard_local_position_setpoint_mavlink.py` and enable
the `yaw_rate_baseline` case in
`experiments/configs/mvp/batches/phase8l_03_rotation_gyro_baseline.yaml`
(Phase 8L Gate 5).

## 2026-07-17 — Phase 8M two-case LK vs stock PX4 flow route comparison

Added and ran:
`experiments/configs/mvp/batches/phase8m_lk_vs_stock_flow_50s_compare.yaml`.

Purpose: compare the repaired DATABOSS LK flow bridge against stock PX4 Gazebo
optical flow on the same 50 s GNSS-loss slow +Y route, with QGC and gzweb
enabled and `control.skip_landing_command: true`.

Accepted batch:
`experiments/batches/20260717_132613_phase8m_lk_vs_stock_flow_50s_compare`.

Result: **Accepted**, 2/2 cases passed.

- `lk_bounded_50s`:
  `experiments/runs/20260717_132616_phase8k_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - QGC connected, gzweb OK, GNSS loss OK, ULog copied.
  - LK bridge OK with `1635` sent flow rows.
  - Gazebo truth displacement end: `7.216998 m`.
  - Horizontal EKF-vs-truth error mean/max/end:
    `1.170414 / 3.228127 / 2.055280 m`.
- `stock_px4_flow_50s`:
  `experiments/runs/20260717_133641_phase8j_stock_flow_50s_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - QGC connected, gzweb OK, GNSS loss OK, ULog copied.
  - Stock PX4 flow enabled.
  - Gazebo truth displacement end: `9.696796 m`.
  - Horizontal EKF-vs-truth error mean/max/end:
    `0.272036 / 1.129225 / 1.129225 m`.

Interpretation: both cases completed the 50 s GNSS-loss observation window and
are valid for route comparison. Stock PX4 optical flow followed the intended
~10 m slow +Y route much more closely. The LK bridge stayed alive and accepted,
but the physical route under-ran the commanded displacement and had larger
horizontal estimator disagreement.

Route comparison plots/report generated:
`experiments/batches/20260717_132613_phase8m_lk_vs_stock_flow_50s_compare/route_compare/route_compare_report.md`.

Generated plots include truth route overlay, per-case EKF-vs-truth route
panels, route progress over time, horizontal/height error time series, and route
end/error bar charts.

Report extended with EKF optical-flow aid-source fusion/rejection data from
`estimator_aid_src_optical_flow` in each ULog:

- `lk_bounded_50s`: `1625` optical-flow aid samples, `893` fused/accepted,
  `732` not fused, `713` innovation rejected, fused fraction `55.0%`, max
  test ratio `6.768`.
- `stock_px4_flow_50s`: `3776` optical-flow aid samples, `3645`
  fused/accepted, `131` not fused, `0` innovation rejected, fused fraction
  `96.5%`, max test ratio `0.521`.

Additional plots generated:
`optical_flow_fused_rejected_counts.png`,
`optical_flow_fused_rejected_timeseries.png`,
`optical_flow_cumulative_fused_rejected.png`,
`optical_flow_test_ratio_timeseries.png`, and
`optical_flow_innovation_observation_timeseries.png`.

## 2026-07-17 — Phase 8L LK GNSS-loss failsafe-isolation rerun

Added:

- `experiments/configs/mvp/scenarios/phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/batches/phase8l_lk_failsafe_isolation.yaml`

Purpose: test the audit hypothesis that the rejected LK GNSS-loss result was
confounded by strict default failsafe settings. This rerun kept the Gate 6b LK
measurement configuration fixed and changed only the safety/failsafe conditions
requested by the audit: `delayed_observation` failsafe and
`MPC_XY_VEL_MAX=2.0`.

Commands:

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_lk_failsafe_isolation.yaml \
  --dry-run --continue-on-fail
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_lk_failsafe_isolation.yaml \
  --continue-on-fail
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/runner/postprocess_latest_truth_run.py \
  --run-dir experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/runner/align_latest_truth_run.py \
  --run-dir experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/analysis/analyze_flow_fusion_ulog.py \
  experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth \
  --json experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth/flow_fusion_ulog.json
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python \
  scripts/analysis/sensor_contract_report.py \
  --run-dir experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth \
  --gate loss
```

Run:
`experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth`

Batch:
`experiments/batches/20260717_143620_phase8l_lk_failsafe_isolation`

Result: **Rejected**.

- Failsafe profile applied: `failsafe_profile=delayed_observation`,
  `failsafe_profile_ok=True`.
- Applied safety command included `param set MPC_XY_VEL_MAX 2.0`.
- GNSS loss detected, but effective timing was still `10.0 s` after takeoff
  despite a requested `20.0 s`, so the timing-reference ambiguity remains.
- Bridge evidence: `flow_bridge_sent_rows=1016`, `flow_recording_frames=1975`.
- ULog distance sensor validation failed: `distance_sensor_ok=False`,
  max distance sensor `3.63 m`, distance/height disagreement `22.24 m`.
- ULog flight reached `25.86 m` max height above start.
- EKF-vs-Gazebo-truth metrics rejected:
  - horizontal error mean/max/end `564.44 / 2515.98 / 2515.98 m`
  - height absolute error max `203.96 m`
  - Gazebo truth station displacement end `2535.97 m`
- Optical-flow fusion was present but unhealthy:
  - `sensor_optical_flow_rows=1082`
  - `aid_src_optical_flow_rows=1014`
  - fused/rejected `399 / 82`
  - rejection/fused ratio `0.2055`
  - `cs_opt_flow_active_fraction=0.4135`
  - `xy_reset_counter_delta=3`
- Sensor contract `loss` gate rejected: bridge present/rate OK and GNSS loss
  detected, but quality was frequently zeroed, optical-flow active fraction
  failed, distance sensor failed, and truth horizontal/height boundedness
  failed.

Interpretation: the strict default failsafe profile was a real confound in the
earlier LK GNSS-loss rejection, but it was not the sole cause. With the delayed
observation profile and 2 m/s velocity cap applied, the Gate 6b LK flow-only
configuration still failed physically against Gazebo truth.

Limitation: this does not invalidate the later accepted Phase 8M LK-vs-stock
route comparison, because Phase 8M used the Phase 8K bounded scenario contract
and has its own accepted evidence. The next LK-loss work should either compare
the accepted Phase 8M LK contract under one-variable changes, or first repair
the GNSS-loss timing reference so requested and effective loss timing match.

## 2026-07-20 — Phase 8M LK route root-cause inspection

Created:

- `experiments/inspections/20260720_phase8m_route_root_cause_report.md`

Purpose: explain why the Phase 8M LK route remains bad even though the
LK-vs-stock batch was marked accepted.

Result: the route sender is not the primary cause. The Offboard sender uses
`MAV_FRAME_LOCAL_NED`, and the saved setpoint log confirms `vx=0.0`,
`vy=0.2`, `z=-2.5`, `use_yaw=False`. The same route is straight when flow is
disabled with GNSS on (`10.447 m` path, `10.392 m` end, straightness `0.995`)
and also straight in the stock PX4 flow GNSS-loss run (`9.842 m` path,
`9.697 m` end, straightness `0.985`).

The Phase 8M LK case is physically oscillatory:

- truth path length `64.412 m`
- truth end displacement `7.217 m`
- straightness `0.112`
- average path speed `0.975 m/s`

The batch used the older Phase 8K LK scenario:

- `axis_map: "-yx"`
- no `EKF2_OF_N_MIN=0.5` override
- `EKF2_OF_DELAY=111`

This is the same loop-prone contract superseded by Gate 6b. The Gate 6b
GNSS-on repaired LK route (`axis_map: "-x-y"`, `EKF2_OF_N_MIN=0.5`) was
straight: path `12.760 m`, end `12.463 m`, straightness `0.977`.

Bridge/PX4 evidence:

- Phase 8M LK bridge median sent angular-rate magnitude `0.6334 rad/s`,
  p95 `1.1513 rad/s`, max `1.5297 rad/s`.
- Phase 8M stock EKF observed-flow median `0.0834 rad/s`, matching the
  expected `~0.08 rad/s` for `0.2 m/s` at `2.5 m` AGL.
- Phase 8M LK EKF observed-flow median `0.6204 rad/s`.
- LK fused/rejected `893 / 713`; stock fused/rejected `3645 / 0`.
- Velocity/yaw diagnosis: LK truth speed `1.343 m/s`, EKF speed
  `0.442 m/s`, velocity gap `1.098 m/s`, yaw error mean/max
  `26.1 / 39.7 deg`; stock truth speed `0.173 m/s`, EKF speed
  `0.200 m/s`, velocity gap `0.035 m/s`, yaw error `4.3 / 4.3 deg`.

Interpretation: the remaining route problem is an LK
measurement/EKF/controller feedback problem, not a route-command bug. Phase 8M
acceptance means the run completed and saved evidence; it does not mean LK
route quality reached stock parity.

Next action superseded by the Phase 8N sign probe below: the repair axis
candidate is now `axis_map: "xy"`, not `"-x-y"`.

## 2026-07-20 — Phase 8N short `xy` vs old Gate 6b sign probe

Created:

- `experiments/configs/mvp/scenarios/phase8n_lk_xy_gnsson_short_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/batches/phase8n_lk_xy_vs_old_short_gnsson.yaml`
- `experiments/configs/mvp/batches/phase8n_lk_old_short_gnsson_rerun.yaml`
- `experiments/inspections/20260720_phase8n_xy_vs_old_short_gnsson_compare.md`
- `docs/phases/phase_08n_flow_sign_inversion_probe.md`

Cleaned before and after runs: stopped stale PX4/Gazebo/flow processes,
verified port `9003`, removed `/tmp/px4-sock-0`. During the first old-baseline
attempt, PX4 logger hit `errno:28` because `/opt` was full. Removed only
today's incomplete interrupted run artifacts (`20260720_064316...`,
`20260720_063507...`, and the partial `20260720_070731...`) and reran the old
baseline.

Accepted short GNSS-on runs:

- `xy`: `experiments/runs/20260720_070148_phase8n_lk_xy_gnsson_short_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- old Gate 6b: `experiments/runs/20260720_071532_phase8l_lk_gnsson_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth`

Comparison:

- Both accepted end-to-end and copied ULogs.
- `xy` truth route: path `4.812 m`, end `4.440 m`, straightness `0.923`,
  max cross-track N `0.418 m`; EKF-vs-truth horizontal mean/max
  `0.260 / 0.438 m`.
- old Gate 6b truth route: path `6.162 m`, end `6.044 m`, straightness
  `0.981`, max cross-track N `0.077 m`; EKF-vs-truth horizontal mean/max
  `0.727 / 1.459 m`.
- Flow fusion was high for both: `xy` fused/rejected `762 / 0`; old Gate 6b
  fused/rejected `771 / 0`.
- Sign sentinel: `xy` body-X corr/gain versus GPS body velocity
  `+0.811 / +1.012`; old Gate 6b `-0.899 / -1.075`.

Interpretation: `xy` is the sign-correct bridge candidate. The old
`-x-y + EKF2_OF_N_MIN=0.5` config still flies GNSS-on because GNSS is present
and flow is de-weighted, but its ULog flow-derived velocity is inverted on the
active axis. Do not treat the old config as a sign-correct contract. Before the
next acceptance run, update sign-sensitive analyzers that still encode the old
wire convention.

## 2026-07-20 — Phase 8N analyzer/sign-contract fix

Implemented:

- Added `scripts/analysis/check_flow_velocity_sign.py`, the ULog sign sentinel
  that compares PX4 `estimator_optical_flow_vel.vel_body` against independent
  GNSS velocity rotated into body frame. A truth source path exists for future
  GNSS-loss work but is not yet the accepted sign authority.
- Updated `scripts/analysis/fit_flow_contract_from_truth.py` to label its
  output as a MAVLink wire-side fit before EKF2's internal negation.
- Updated `scripts/analysis/analyze_flow_bridge_openloop.py` and
  `scripts/analysis/sensor_contract_report.py` to describe the legacy axis
  gate as a wire-local check only, with the new sentinel as final sign
  authority.
- Updated `src/databoss_sim/flow/px4_adapter.py` notes to say Phase 8N
  supersedes the old Gate 6b `-x-y` workaround and confirms `xy` as the
  sign-correct bridge candidate.
- Hooked `scripts/runner/run_scenario_pxh_end_to_end.py` to run the GPS-backed
  sign sentinel by default for GNSS-on flow-bridge scenarios and write
  `flow_velocity_sign.json`. GNSS-loss scenarios must explicitly opt in after
  the truth-backed source is validated. The sentinel gates acceptance only when
  `analysis.flow_velocity_sign_required: true`.
- Enabled that required sign gate in the new Phase 8N `xy` scenario.
- Updated current phase docs and the Phase 8M inspection next-test guidance so
  future repair runs use `axis_map: "xy"`.

Verification:

- Python compile check passed for all edited Python files.
- `check_flow_velocity_sign.py` accepted the Phase 8N `xy` run:
  active body-X corr/gain `+0.8113 / +1.0116`.
- The same sentinel rejected the old `-x-y + EKF2_OF_N_MIN=0.5` run:
  active body-X corr/gain `-0.8994 / -1.0746`.

Decision: use `axis_map: "xy"` going forward. Treat open-loop wire signs as
transport evidence only; do not use them as the final optical-flow sign
acceptance gate.

## 2026-07-20 — Project cleanup + Phase 10/11 scaffolding

Context: `/opt` was at 29 MB free (100% full) mid-session; recovered to
~1003 MB free during the Phase 8N work. `experiments/runs/` (9.4 GB) is the
dominant consumer, and 25+ phase docs had accumulated overlapping/superseded
content. This pass cleaned dead code, dead docs, and duplicate run folders,
and scaffolded the next two phases now that Phase 8N settled the optical-flow
sign question.

Deleted (dead code, zero external callers/consumers verified before removal):

- `scripts/runner/auto_takeoff_land_pxh_truth.py.phase8a_backup`
- `scripts/runner/auto_takeoff_land_pxh.py` (superseded by `..._truth.py`)
- `scripts/analysis/report_phase7d_all_conditions.py`,
  `report_phase7d_all_conditions_v3.py`, `report_phase7d_from_case_logs.py`,
  `report_phase7d_from_metrics_md.py` (4 competing, unreferenced Phase 7D
  reporters)
- `experiments/configs/mvp/routes/{square_50m,straight_50m_out_and_back,
  tower_inspection_visual,hover_60s}.yaml` (orphaned waypoint-route
  scaffolding; no script ever loaded `routes/`, real route config is the
  `control:` block in each scenario YAML)
- `__pycache__` dirs under `scripts/` and `src/databoss_sim/`

Created:

- `docs/phases/phase_10_gnss_loss_flow_aiding_repair.md` (Planned →
  In progress: step 1 already done via the Phase 8N analyzer fix above)
- `docs/phases/phase_11_three_way_flow_comparison.md` (Planned, blocked on
  Phase 10) — the user's actual target: LK vs SIFT vs stock flow, one
  variable changed, each case individually proven fault-free before any
  comparison number is trusted
- `docs/phases/archive/legacy_phase_notes.md` — verbatim relocation of
  stale/duplicate `docs/phases/README.md` sections (old "Superseded ...
  kept for history" 8F/8D blocks, a duplicate "Next phases" list, the stale
  "Current Phase 8B/8C focus" write-ups, the Phase 7D freeze note). No
  content was reworded or dropped, only moved, per the project rule against
  rewriting history.

Edited: `docs/phases/phase_08k_bounded_flow_candidate.md`,
`phase_08g_live_flow_bridge.md`, `phase_08i_gnss_denied_flow_comparison.md`,
`phase_08j_stock_flow_benchmark.md` each got a superseded-contract banner
pointing to Gate 6b and Phase 8N; original recorded numbers below the
banners are untouched. `docs/phases/README.md` gained a "Proven /
trustworthy building blocks" ledger and Phase 10/11 entries in "Upcoming
phases."

Run-folder cleanup (citation-verified against every run ID mentioned in
`docs/`, `experiments/comparisons/`, and `experiments/inspections/` — not
`experiments/batches/`, which mirrors run folders 1:1 and is not real
citation evidence; all removed folders predate 2026-07-20 so none overlap
today's live Phase 8N/8N-analyzer work):

- 4× phase8g (2 rehearsal, 2 no-`validation.md` legy runs) — ~62 MB
- 14× phase8i early-iteration A/B/C duplicates, superseded by the cited
  final A/C/D runs and the terrain camera proof — ~369 MB
- 3× phase8j uncited `d_loss_flow_lk` clones — ~171 MB
- 1× phase8m uncited `quick_sift_gnsson` duplicate — ~130 MB

Total: 22 run folders deleted. `df -h /opt`: 1005 MB free before → 1.7 GB
free after. `experiments/runs/` folder count: 106 → 84. Everything else
(phase8c/8d/8e/8k/8l/9a, and anything dated 2026-07-20) was left untouched —
phase8l in particular is an active parameter sweep, not duplicate junk.

Next action: Phase 10 steps 2-5 — a GNSS-on replicate (n>1) on `axis_map:
"xy"` with the fixed analyzers, deliberate `EKF2_OF_N_MIN` tuning, the
GNSS-loss timing-reference repair, then GNSS-loss aiding-strength tuning.
Only once Phase 10 is accepted does Phase 11's three-way comparison become
valid.

## 2026-07-20 — Phase 10 first bounded GNSS-loss LK result

Created:

- `experiments/configs/mvp/scenarios/phase10_lk_xy_gnsson_nmin03_short_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon.yaml`

GNSS-on tuning check (`axis_map: "xy"`, `EKF2_OF_N_MIN=0.3`, up from Phase
8N's untuned 0.15 default): accepted run `20260720_083128_...`. Sign
sentinel confirmed positive/correct again (body-X corr `0.7713`, gain
`0.9776`), flow fusion `612/622` fused, `0` rejected. Took 3 attempts —
2 hit an intermittent PX4 Offboard-mode-entry failure
(`accepts_offboard_setpoints` never reaching `True`), a pre-existing ~4%
base rate across run history (72 prior successes / 3 prior failures),
resolved both times by removing the leftover `/tmp/px4-sock-0` from the
previous attempt before retrying. Unrelated to the flow-sign work.

GNSS-loss attempt on the same contract: the first two invocations
silently ran GNSS-on only, because `auto_takeoff_land_pxh_truth.py` and
`run_scenario_pxh_end_to_end.py` read GNSS-loss timing and hover duration
from CLI flags only (`--gnss-loss-after-takeoff-s`, `--hover-s`) — the
scenario YAML's `gnss.loss_after_takeoff_s`/`route.duration_s` fields are
not wired to anything, matching `README.md`'s own "unknown YAML fields...
verify the actual parser" warning. Corrected by passing
`--hover-s 60 --gnss-loss-after-takeoff-s 20` explicitly.

With that fixed, run `20260720_085443_...` produced the **first-ever
bounded GNSS-loss LK result** in this project's history:

- Truth-path straightness `0.978` (path `10.340 m`, end `10.108 m`,
  matching the intended ~10 m route almost exactly).
- Optical-flow fused/rejected: `1590 / 0`.
- EKF-vs-truth horizontal error mean/max: `0.594 m / 1.097 m`.
- EKF-vs-truth height error mean: `0.258 m`.
- Effective GNSS-loss timing again didn't match the request (10 s effective
  vs. 20 s requested) — the known timing-reference bug reproduced, but did
  not prevent a bounded result this time.

For comparison, every prior GNSS-loss attempt on the sign-inverted `-x-y`
contract diverged: `~1.4 km` and `2515.98 m` max horizontal error. This
run's max horizontal error was `1.097 m` — roughly three orders of
magnitude smaller, and the truth path is a clean straight leg, not a loop.

Interpretation: the optical-flow sign inversion was very likely the entire
root cause of every prior GNSS-loss divergence, not merely a contributing
factor alongside a control-loop damping problem as originally hypothesized.
Fixing the sign plus a moderate, non-swept `EKF2_OF_N_MIN=0.3` was enough to
pass all five of Phase 10's acceptance criteria on the first properly
GNSS-loss-configured attempt.

`docs/phases/phase_10_gnss_loss_flow_aiding_repair.md` updated to **Accepted
with limitations** — this is n=1 pending a replicate (in progress at time of
writing), the timing-reference bug is still open, and `EKF2_OF_N_MIN=0.3`
was a first guess, not a swept optimum. SIFT reconfirmation (step 5) not
started.

Next action: finish the GNSS-loss replicate; if it also passes, Phase 10 is
ready to close and Phase 11's three-way comparison (LK vs SIFT vs stock,
one variable, matched conditions) becomes valid to start.

## 2026-07-20 — Phase 10 GNSS-loss result confirmed on replicate (n=2/2)

Replicate run `20260720_090850_phase10_lk_xy_gnssloss20_nmin03_...` (same
`axis_map: "xy"`, `EKF2_OF_N_MIN=0.3` contract, same 20 s-requested/10 s
-effective GNSS-loss timing) also passed every acceptance criterion:

- Truth-path straightness `0.964` (path `10.314 m`, end `9.944 m`).
- Optical-flow fused/rejected: `1584 / 0`.
- EKF-vs-truth horizontal error mean/max: `0.751 m / 1.661 m`.
- EKF-vs-truth height error mean: `0.173 m`.

Combined with the first run (`085443`: straightness `0.978`, fused/rejected
`1590/0`, horizontal error mean/max `0.594 m / 1.097 m`), Phase 10's core
finding is now n=2/2: the optical-flow sign fix alone (no loop-damping or
aiding-strength tuning beyond a single reasonable `EKF2_OF_N_MIN=0.3`
choice) resolves the GNSS-loss divergence that blocked this project since
Phase 8L. `docs/phases/phase_10_gnss_loss_flow_aiding_repair.md` updated
accordingly; remaining open items are the timing-reference bug (reproduced
on both runs) and SIFT reconfirmation (step 5), neither of which blocks
starting Phase 11.

Next action: Phase 11 — three-way LK vs SIFT vs stock-flow comparison, one
variable, matched world/route/GNSS-loss timing, each case individually
passing Phase 10's criteria before any comparison number is trusted.

## 2026-07-20 — Phase 10 comparison plots for the two GNSS-loss LK replicates

Created:

- `scripts/analysis/plot_phase10_gnssloss_pair.py`
- `experiments/comparisons/20260720_phase10_gnssloss_lk_xy_nmin03_pair/`

Generated plots:

- `plots/route_ekf_vs_gazebo_truth_overlay.png`
- `plots/route_ekf_vs_gazebo_truth_panels.png`
- `plots/gazebo_truth_route_progress.png`
- `plots/horizontal_error_vs_gazebo_truth.png`
- `plots/height_error_vs_gazebo_truth.png`
- `plots/optical_flow_fusion_fraction_1s.png`
- `plots/optical_flow_aid_sample_rate_1s.png`
- `plots/optical_flow_test_ratio_1s.png`
- `plots/summary_metric_bars.png`

Summary from `summary.csv`:

- `085443`: validation accepted, metrics JSON accepted flag false due
  `land_command_not_found`; effective GNSS loss `10.0 s`; truth path/end
  `10.34 / 10.11 m`; straightness `0.978`; max horizontal error `1.10 m`;
  optical flow fused/rejected `1590 / 0`.
- `090850`: validation accepted, metrics JSON accepted flag false due
  `land_command_not_found`; effective GNSS loss `10.0 s`; truth path/end
  `10.31 / 9.94 m`; straightness `0.964`; max horizontal error `1.66 m`;
  optical flow fused/rejected `1584 / 0`.

Interpretation: both low-level flight validations and Phase 10 acceptance
criteria remain good, but the per-run metrics JSON `accepted=false` flags are
bookkeeping artifacts from using `comparison_window=until-land-command` on
skip-landing runs (`control.skip_landing_command=true`). Future analysis for
this scenario shape should use a full/explicit observation window so the
machine acceptance flag matches the phase-level evidence.

## 2026-07-20 — Phase 10 dedicated 50 s GNSS-off test (Run D) + timing-bug root cause

User asked for an explicit, longer 50 s GNSS-off duration test of LK, and
flagged live that an earlier attempt on this scenario was "just hovering,
no velocity" — correct catch: that attempt hit the same intermittent PX4
Offboard-entry failure documented above (`accepts_offboard_setpoints` stuck
`False`, `nav_state: 4`/AUTO_LOITER), so the streamed `vy=0.2` setpoints
were being sent correctly but ignored. Killed the stuck run, cleared
`/tmp/px4-sock-0`, reran.

Found the root cause of the "requested vs. effective GNSS-loss time"
mismatch that's been reproducing since Phase 8L: in
`control.mode: offboard_local_position_hold`, `auto_takeoff_land_pxh_truth.py`
computes effective loss time as `start_after_takeoff_s + warmup_s +
control.gnss_loss_after_offboard_s` (YAML field, default `3.0`) and
**ignores** the `--gnss-loss-after-takeoff-s` CLI value entirely for this
control mode — that flag only drives a different, non-offboard-hold control
path. Every one of this phase's runs used the default `gnss_loss_after_offboard_s=3.0`,
giving `5 + 2 + 3 = 10.0 s` effective regardless of what was requested on
the CLI. Not yet fixed; now understood and documented rather than mysterious.

Created `experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon.yaml`,
same `axis_map: "xy"` / `EKF2_OF_N_MIN=0.3` contract, explicitly passing
`--post-loss-hover-s 50` (instead of relying on the `hover_s − effective_loss`
default that happened to also equal 50 in runs `085443`/`090850`) for an
unambiguous full 50 s GNSS-off window — the same duration SIFT diverges 4/4
at (Phase 8I) and stock flow is proven bounded 3/3 at (Phase 8J).

Accepted run `20260720_104301_...`:

- Truth-path straightness `0.974` (path `10.201 m`, end `9.936 m`).
- Optical-flow fused/rejected: `1595 / 0`.
- EKF-vs-truth horizontal error mean/max: `0.398 m / 1.093 m` — the best of
  the three Phase 10 GNSS-loss runs so far.
- EKF-vs-truth height error mean: `0.260 m`.

This is Phase 10's third independent passing GNSS-loss run (n=3/3), and the
first with an explicit, unambiguous 50 s GNSS-off duration rather than an
incidental one. Metrics are tightly clustered across all three runs
(straightness `0.964`–`0.978`, max horizontal error `1.09`–`1.66 m`).
`docs/phases/phase_10_gnss_loss_flow_aiding_repair.md` updated accordingly.

Next action: Phase 11 — three-way LK vs SIFT vs stock-flow comparison at
this same 50 s GNSS-off duration, one variable, matched world/route, each
case individually passing Phase 10's criteria.

## 2026-07-20 — Latest Phase 10 run GNSS-data plots generated

User asked for a GNSS data graph for the latest run. The latest Phase 10 run
is `20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_...`, so generated
ULog-based GNSS plots directly from `logs/flight.ulg`.

Created:

- `scripts/analysis/plot_gnss_data_run.py`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_data_over_time.png`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_position_trace.png`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_data_summary.json`

Key ULog evidence from `gnss_data_summary.json`:

- `vehicle_gps_position` rows: `1955`.
- Fix states present: `fix_type` `[0, 3]`.
- Satellites used: min/max `0 / 10`.
- GPS accuracy jump: `eph` `0.9 m -> 100.0 m`, `epv` `1.78 m -> 100.0 m`.
- Takeoff threshold crossing: `11.874 s` after ULog start.
- Offboard accepted: `14.086 s`.
- Observed GPS validity loss: `14.382 s`, which is `2.508 s` after takeoff.
- Status/schedule GNSS-loss timestamp: `21.874 s` (`10.0 s` after takeoff).

Interpretation: the plot confirms GNSS data really becomes invalid
(`fix_type=3`, `10` sats -> `fix_type=0`, `0` sats), but it also exposes a
deeper timing-contract mismatch than the already documented CLI/status bug:
the actual ULog GNSS-validity transition happens shortly after Offboard is
accepted, not at the status-file scheduled timestamp. Treat ULog GNSS topics
as the authority for GNSS-loss timing until the runner's local-hold timing
bookkeeping is fixed.

## 2026-07-20 — Phase 11 LK/SIFT/stock comparison plots generated; stock rejected

User asked for the full plot pack ("routes, GNSS, fusion rate") and why SIFT
looked strong while earlier attempts failed. Created:

- `scripts/analysis/plot_phase11_three_way_flow_comparison.py`
- `experiments/comparisons/20260720_phase11_three_way_flow_comparison/report.md`
- `experiments/comparisons/20260720_phase11_three_way_flow_comparison/summary.csv`
- `experiments/comparisons/20260720_phase11_three_way_flow_comparison/summary.json`
- `experiments/comparisons/20260720_phase11_three_way_flow_comparison/plots/`

Runs compared:

- LK: `20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_...`
- SIFT: `20260720_111755_phase11_sift_xy_gnssloss_off50s_...`
- Stock: `20260720_112920_phase11_stock_gnssloss_off50s_...`

The later stock folder `20260720_113659_phase11_stock_gnssloss_off50s_...`
is incomplete (no status JSON / metrics JSON), so it was not used.

Key result:

- LK fixed: validation accepted, horizontal max `1.093 m`, height max
  `0.377 m`, optical flow fused/rejected `1595 / 0`, `cs_opt_flow=0.731`,
  truth straightness `0.974`.
- SIFT xy: validation accepted, horizontal max `1.214 m`, height max
  `0.389 m`, optical flow fused/rejected `804 / 0`, `cs_opt_flow=0.762`,
  truth straightness `0.986`.
- Stock: validation rejected, horizontal max `19.852 m`, height max
  `13.033 m`, optical flow fused/rejected `815 / 2`, `cs_opt_flow=0.256`,
  truth straightness `0.755`, distance-sensor height mismatch `10.32 m`.

Interpretation: SIFT worked well here because it used the corrected
`axis_map: "xy"` bridge sign and maintained clean EKF flow fusion. The earlier
LK failures were bad-contract failures (sign inversion and/or no active flow
fusion), not proof that SIFT was fundamentally better than a healthy LK
contract. Phase 10's corrected LK now performs at least as well as SIFT on
this matched candidate. Phase 11 remains **in progress**, not accepted,
because the stock comparator failed its own height/range/flight gates.

Next action: repair or rerun the Phase 11 stock case until it individually
passes, then regenerate the same comparison pack.

## 2026-07-20 — Phase 11 stock-flow intermittent failsafe root-cause digging + clean three-way result

User asked to dig into why the stock candidate failed and whether optical
flow was actually reaching the EKF, since it "should at least work like
SIFT." Investigated directly rather than assuming:

- Confirmed `sensor_optical_flow`/`vehicle_optical_flow`/`estimator_aid_src_optical_flow`
  published gap-free at ~50 Hz for the *entire* flight in the rejected
  `112920` run — flow was never missing. The real signature: fusion was
  100% (500/500) from t=30-40s, then **0% fused from t=50s onward** for the
  rest of the flight, correlating with `sensor_optical_flow` quality
  collapsing from ~215 mean (0% zero-quality) to ~127 mean (50%
  zero-quality) and staying there.
- Compared against two historical accepted Phase 8J stock benchmarks
  (`072949`, `133641`): both sustain 215+ quality, 0% zero-fraction, and
  ~100% fusion from t=30s all the way to the end of the flight — the
  mid-flight collapse does not happen in the historical record.
- Ruled out configuration as the cause: full `config.yaml` diff between the
  rejected run and a historical accepted run showed only cosmetic
  differences (names/descriptions/unused doc fields); PX4 build git hash
  (`994dec2c41`) and prelaunch parameter overrides were byte-identical.
- Tried reverting stock's GNSS-loss timing/failsafe profile from the
  LK/SIFT-matched values back to stock's own Phase 8J-proven values
  (`gnss_loss_after_offboard_s=12`, `failsafe.profile: delayed_observation`,
  effective loss at 19 s) as a hypothesis fix — user caught live that this
  retry *also* hit the same `mc_pos_control` failsafe ("stop and wait" /
  "blind descent"), ruling out timing/failsafe-profile as the cause too.
- Checked system resource state: 635 MB swap in use, ~600 MB free RAM on
  the 3.7 GB VM, load average 2.09/3.33/4.01, disk still tight — plausible
  contributor (swap activity causing camera-render frame-timing jitter),
  but incomplete as a full explanation since LK's own bridge (also
  camera-rendering-dependent) ran with perfect sustained quality (255, 0%
  zero) in the same pressured environment moments earlier in the session.
  Found 5 leaked `gz-transport-topic` truth-listener processes accumulated
  since as far back as Jul 13 (one at 684+ CPU-minutes) as a candidate
  contributor to the resource pressure; did not kill them — that action was
  blocked by the permission system as inconsistent with this session's own
  earlier characterization of them as normal standing monitors, and with
  the standing instruction to keep viz/link connections live across runs.
- A third identical attempt (unchanged scenario, same timing/failsafe as
  the historical Phase 8J config) **passed cleanly**: zero failsafe events,
  quality sustained 210-217 for the full flight, matching the historical
  pattern exactly. Confirms the failure is intermittent, not a deterministic
  regression — but root cause remains unconfirmed.

Regenerated the Phase 11 three-way comparison
(`scripts/analysis/plot_phase11_three_way_flow_comparison.py`,
`experiments/comparisons/20260720_phase11_three_way_flow_comparison/`,
originally built by another session against the rejected `112920` stock run)
against the clean `115202` run instead — fixed ownership (was
`nobody:nogroup`, blocking writes as `px4`) and corrected the hardcoded
interpretation prose that still described the old rejected numbers.

**Final Phase 11 result — all three Accepted, matched conditions (same
world, route, ~50 s GNSS-off duration):**

| Case | Max horizontal error | Max height error | Flow fused/rejected | Straightness |
|---|---:|---:|---:|---:|
| LK (`axis_map: xy`, `EKF2_OF_N_MIN=0.3`) | 1.093 m | 0.377 m | 1595/0 | 0.974 |
| SIFT (`axis_map: xy`, own delay/n_min) | 1.214 m | 0.389 m | 804/0 | 0.986 |
| Stock (own Phase 8J config, untouched) | 0.188 m | 0.259 m | 2778/0 | 0.974 |

Zero rejected flow samples across all three. Stock is tightest on this
single run; LK/SIFT are close behind and both fully bounded — a real,
trustworthy one-variable comparison, unlike every prior attempt (Phase 8J,
8M) which ran at least one estimator on a since-discredited contract.

Notable secondary finding: SIFT's corrected-sign result being clean directly
challenges Phase 8I's "diverges 4/4 at 50 s" finding, since that result was
measured on the same sign-inverted `axis_map: "-yx"` contract that caused
every LK GNSS-loss failure. Not yet re-verified at n>1, flagged as a
reopened question in `docs/phases/phase_11_three_way_flow_comparison.md`.

`docs/phases/phase_11_three_way_flow_comparison.md` updated to **Accepted
with limitations**. `docs/phases/README.md` updated.

Next action: (1) a few more stock-flow replicates with resource monitoring
to try to pin down the intermittent failsafe/quality-collapse root cause;
(2) a SIFT-specific replicate matrix re-testing Phase 8I's duration-limit
claim under the corrected sign contract; (3) eventually the
multi-world/condition matrix and Phase 12's MVP report.

## 2026-07-20 — Phase 11 stock comparator was never GNSS-denied; corrected

User asked directly whether the accepted stock run (`115202`) was actually
a GNSS-denied run. Checked `vehicle_gps_position` straight from each ULog
(not the status-file flags) across all four Phase 11/10 candidates:

| Run | fix_type/satellites over the flight | GPS actually cut? |
|---|---|---|
| LK Run D (`104301`) | drops to `0`/`0` at t≈14.4 s, stays down | Yes |
| SIFT (`111755`) | drops to `0`/`0` at t≈15.2 s, stays down | Yes |
| Stock accepted (`115202`) | `fix_type=3`, `sats=10` for all 2105 rows, 0–69 s | **No** |
| Stock rejected (`112920`) | drops to `0`/`0` at t≈19.9 s — then hits `mc_pos_control` failsafe | Yes |

`115202`'s PX4 console log shows `param set SIM_GPS_USED 0` was sent and
acknowledged (`SIM_GPS_USED: curr: 10 -> new: 0`) at the scheduled time,
identically to the other three runs — so the command executed, but the
simulated GPS driver kept publishing a healthy fix anyway. The runner's own
`gnss_loss_detected`/`gnss_loss_ok` status flags both read `True` for this
run, which is how it was accepted without catching the problem: those
flags only confirm the command was sent, not that the fix actually dropped.
This is now documented as a required verification step for any future
GNSS-loss run.

Consequence: `112920`'s failure (`mc_pos_control` failsafe, quality
collapse) is now confirmed to coincide with a *genuine* GPS loss, which
weakens the earlier resource-pressure hypothesis and reopens stock's
GNSS-loss behavior as a real pass/fail question rather than an unexplained
intermittent quirk.

Reran the stock scenario to get a valid comparator (same command as
before: `--hover-s 72 --gnss-loss-after-takeoff-s 19 --post-loss-hover-s 50
--failsafe-profile delayed_observation`):
- First retry (`121901`) never took off — hit the pre-existing
  `global_position_ready` gate, which timed out after 90 s with
  `vehicle_global_position` listener fields all `None` despite healthy raw
  GPS. A launch-time flake, unrelated to the GNSS-loss question; cleared
  `/tmp/px4-sock-0` and deleted the folder (no flight data, 9.8 MB).
- Second retry (`122327`) flew cleanly and genuinely lost GPS: `fix_type`/
  `satellites_used` drop to `0` at t≈22.4 s, stay down for the rest of a
  72.9 s flight, zero `mc_pos_control` failsafe events, flow quality
  zero-fraction only 10.9%. Ran postprocessing/alignment manually (the
  wrapper script exits non-zero on `accepted=false`, which here is only the
  known skip-landing/`comparison_window` bookkeeping artifact, not a real
  failure).

**Corrected stock numbers (`122327`, genuinely GNSS-denied):** max
horizontal error `1.347 m` (was `0.188 m`), max height error `0.628 m`
(was `0.259 m`), flow fused/rejected `2839/0`, `cs_opt_flow` fraction
`0.705`, truth straightness `0.973`, path/end `12.08 m / 11.76 m`. Stock is
now the *loosest* of the three on this run, not the tightest — the
original "stock is tightest, most mature path" conclusion was an artifact
of testing a GNSS-available flight, not a real finding.

Updated `scripts/analysis/plot_phase11_three_way_flow_comparison.py` to
point at `122327` and rewrote its hardcoded interpretation text; regenerated
`experiments/comparisons/20260720_phase11_three_way_flow_comparison/`
(the "Observed GPS loss after takeoff" column, previously `n/a` for stock,
now reads `5.856` — this was the tell that first prompted the question).
Rewrote `docs/phases/phase_11_three_way_flow_comparison.md`'s status
banner, Results table, Interpretation, Known limitations, Files, and Next
phase sections to reflect the correction; `112920` reclassified as
confirmed-genuine-GNSS-loss evidence, `115202` reclassified as invalid
(kept for the record, no longer the comparator).

Next action: stock-flow replicate matrix under GNSS loss with direct
`vehicle_gps_position` verification on every run (n=2 so far: one bounded,
one failed — not enough to know if stock is reliably bounded, reliably
fails past some condition, or genuinely flaky); consider hardening the
runner scripts to verify the GPS fix actually drops rather than trusting
the `param set` acknowledgment.

## 2026-07-21 — Phase 12: unified comparison report system + GNSS-on/unaided matrix

User asked for a durable "comparison report system" (their words: "MVP of
the dashboard") instead of another one-off report — one manifest-driven
generator that includes camera inputs, plots, routes, GNSS data, algorithm
config, and world settings for any set of runs, extensible by hand as
world/sensor variants get added later. Also asked to accept stock's
rangefinder dropout as a known characteristic rather than fixing it this
phase.

**Phase 0, disk headroom**: `/opt` was at 424 MB free (99% full) before
starting. Re-ran the citation-verification methodology from an earlier,
now-stale cleanup plan and found most of its "reclaimable duplicates" are
actually cited/load-bearing (Phase 8G accepted evidence, the phase8l delay
sweep behind `EKF2_OF_DELAY=140`, phase8i/8j high-citation groups) — did
not touch those. Deleted 6 genuinely zero-citation, no-`flight.ulg` run
folders (~71 MB). Primary lever was lossless compression, not deletion:
`gzip`'d `gazebo_ground_truth_raw.txt` (~7-8x measured ratio; nothing
reads the raw file, only its derived CSV) in ~18 large pre-2026-07-20 run
folders, each verified byte-identical via md5sum before removing the
plaintext. Reached >1 GB free before launching any new runs.

**Disk-cost bug found and fixed**: the LK/SIFT GNSS-loss scenario YAMLs'
`flow_recording: {rate_hz: 0, ...}` does not mean "off" — in
`scripts/sim/record_camera_frames.py`, `rate_hz: 0` means "no throttling,
save every frame," which was silently costing ~150 MB/run (measured on
`104301`/`111755`'s existing `flow_recording/` folders). New Phase 12
scenarios use `rate_hz: 2` (measured ~10-13 MB/run).

**New scripts** (`scripts/analysis/`): `comparison_manifest.py` (shared
`Case` dataclass + `load_manifest()`), `build_unified_comparison_report.py`
(manifest-driven Markdown report — config, GPS-guard, world/sensor
settings, camera-frame samples, EKF/flow metrics, per-case notes),
`plot_unified_comparison.py` (manifest-driven plots, color=algorithm,
linestyle=GNSS state). Both replace hardcoded `CASES` lists from the
Phase 11 scripts with a YAML manifest
(`experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/manifest.yaml`)
so future cases are data, not code.

**GPS guard**: every case's manifest `gnss_state` tag is now independently
re-verified against `vehicle_gps_position`/`sensor_gps` `fix_type` read
directly from the ULog, regardless of the status-file flags — the fix for
the exact gap that let `115202` through in Phase 11. Verified working by
deliberately adding `115202` to a test manifest tagged `loss`: the guard
correctly fired a Verdict banner and per-case MISMATCH row. Also verified:
regeneration is byte-stable on an unchanged manifest, and a dummy manifest
entry with a new `world_variant` tag appears in the report/plots with zero
script changes.

**Real bug found and fixed during this phase**: YAML 1.1 parses a bare
`gnss_state: on` as the Python boolean `True`, not the string `"on"` — this
silently broke the guard's comparison for both GNSS-on cases (falsely
flagged as mismatches). Fixed by quoting `"on"` in the manifest; a `gnss_state: on`
(unquoted) anywhere in a future manifest edit will reproduce this exact
bug — worth remembering.

**3 new runs launched, mid-plan the user narrowed scope**: originally
planned GNSS-on runs for all three algorithms; user redirected to "one
GNSS-on reference is enough, compare others against it" (skipped stock's
GNSS-on — its scenario YAML `phase12_stock_gnsson_off70s_...yaml` is
scaffolded but unused) and asked instead for "GNSS loss after takeoff but
no aiding or optical flow" — a pure dead-reckoning baseline.
- `20260720_144108` LK GNSS-on: accepted, max horizontal error `0.251 m`,
  flow-sign sentinel reconfirmed `axis_map: xy` (corr=0.891, gain=1.011).
- `20260720_145508` SIFT GNSS-on: accepted, max horizontal error `0.498 m`,
  sign sentinel corr=0.811, gain=1.011.
- Unaided GNSS-loss baseline (`phase12_noaid_gnssloss_off70s_...yaml`, no
  `flow_bridge`/`stock_flow` block at all) took **3 attempts**: attempt 1
  (`150815`) — offboard mode requested while PX4 was still mid
  auto-takeoff, rejected, nav_state never left AUTO_TAKEOFF/AUTO_LOITER; a
  one-off launch race, deleted (44 MB). Attempt 2 (`152420`) — offboard
  engaged fine this time, but GPS never actually dropped despite
  `SIM_GPS_USED 0` being sent and acknowledged (the third confirmed
  instance of the `115202`-class bug in this project's history now);
  deleted (62 MB). Attempt 3 (`20260721_061308`) — succeeded on both
  fronts: GPS genuinely dropped at t=26.3s, offboard engaged
  (`nav_state=14` confirmed), and the EKF **diverged to max horizontal
  error 30.079 m** (height climbed from ~2.5m true to an EKF estimate peak
  of ~11m) with nothing to correct it. `accepted=False` from the runner is
  just its rangefinder-height-agreement gate correctly flagging that real
  divergence, not a bookkeeping artifact — ran postprocess/align manually
  to get full metrics.

**Headline result**: LK/SIFT/stock under GNSS-loss stayed within
`0.58-1.35 m` max horizontal error; the unaided baseline diverged to
`30.08 m` — a ~20-50x reduction from adding any of the three flow-aiding
approaches, on identical world/route/timing. First time this project has
had a clean same-report before/after comparison for its core claim.

Updated `docs/phases/phase_12_mvp_comparison_report.md` (full rewrite —
previous version targeted a stale pre-Phase-11 scope and a script that was
never built), `docs/phases/README.md` (new Proven-building-blocks bullets
for the report system and the aiding-vs-unaided finding; Phase 12/13
upcoming-phases entries updated), `docs/phases/phase_13_dashboard_data_contract.md`
(noted its schema work should now derive from Phase 12's actual manifest
format).

Next action: Phase 13 (dashboard data contract, schema+index, no UI) is
now unblocked; a stock GNSS-on replicate would close the one remaining
matrix gap; future world-lighting/shadow/reflectivity and sensor-range
variants get added to the same manifest by hand, one run at a time, per
explicit user direction this phase.

## 2026-07-21 — Phase 14 roadmap started; Phase 14a (15 m) + four robust runner fixes

User set the real target: fly GNSS-denied in a **dark terrain world at 60 m**,
reached step by step ("everything gets harder each time"). Built an 8-batch
difficulty ladder (`phase_14_difficulty_roadmap.md`): altitude 15→35→60 m on
flat/noon, then dim lighting, then terrain, converging on dark-terrain-60 m.
Wind deferred (not implemented; the world builder rejects wind.enabled=true).

**Phase 14a = batch 1 (15 m).** Six cases (LK/SIFT/stock GNSS-loss, stock
replicate pair, one LK GNSS-on — dropped the second GNSS-on per user, "just
run one gnss on" — and an unaided baseline). Result: aided horizontal error
1.5–2.2 m vs **60.24 m unaided**, height ~1 m for all — the 2.5 m Phase 12
story holds at 6x altitude. Report:
`experiments/comparisons/20260721_phase14a_altitude_15m/`.

Getting a *valid* 15 m GNSS-loss run required fixing four separate,
long-standing runner problems — each with a **reusable** primitive, not a
per-run patch (this was the user's explicit ask: "robust reusable solutions"):

1. **Scenario YAML wasn't authoritative for GNSS-loss / failsafe.** Only the
   CLI flags were read; a `gnssloss` scenario launched without
   `--gnss-loss-after-takeoff-s` silently ran GNSS-on (the phase_10 trap,
   which bit us again here — user caught it and audited it precisely). Fix:
   `resolve_gnss_loss_after_takeoff_s` / `resolve_failsafe_profile` in
   `create_run_from_scenario.py`, used by both the direct runner and the
   wrapper. YAML authoritative, CLI override.

2. **The `SIM_GPS_USED 0` flake was silent.** Command acknowledged
   (`curr:10->new:0`) but the Gazebo GPS keeps a nominal fix. Old
   `gnss_loss_detected` only checked the console text. Fix:
   `confirm_gnss_loss()` polls `vehicle_gps_position`, re-asserts up to 5x,
   and fails the run loudly if the sensor never drops. Caught two flakes this
   batch (stock r1, noaid); both cleared on retry.

3. **Loss timing tuned for 2.5 m cut GPS mid-climb at 15 m** → EKF vertical
   velocity blew up to −48 m/s → 279 m runaway. Fix:
   `wait_for_target_altitude()` cuts GPS only at stable hold altitude; one
   scenario now works at any altitude, no per-altitude timing tuning.

4. **Absolute height unobservable above 5 m after GNSS loss.**
   `EKF2_RNG_A_HMAX=5` means the rangefinder only constrains height above
   terrain there; baro drift carried absolute height + terrain up together
   (EKF 15 m, truth 7 m, rangefinder correctly 7 m; 7.6 m median error). Fix:
   raise `EKF2_RNG_A_HMAX` to 80 via a **universal** `extra_px4_params`
   (moved out of the flow-bridge-only block). Height error 7.6 m → 0.28 m.
   Valid on flat terrain; the terrain batches (6–8) must revisit.

User also spotted that flaked runs correlated with "battery unhealthy"
warnings. Investigated: SITL battery drains full→empty in 60 s
(`SIM_BAT_DRAIN`) but floors at 50%, so charge level shouldn't warn —
"battery unhealthy" is a data-health failsafe, most likely a symptom of the
same stale/degraded Gazebo instance that causes the GPS flake (the stock r1
retry recovered on a plain fresh run, no battery param). Added
`SIM_BAT_DRAIN:3000, SIM_BAT_MIN_PCT:95` to remove the confound going
forward; the fail-loud gate + retry is what actually recovers the flakes. A
blanket `pkill gz` was avoided — it would kill concurrent parallel-session
runs.

Also added `open_ulog()` to `comparison_manifest.py` for transparent
gzipped-ULog reads, and gzipped 74 historical ULogs (1.1 GB → ~0.4 GB,
lossless, md5-verified) since raw ULogs are the biggest disk cost.

Next action: Phase 14b (35 m) — same six-case matrix, altitude 35 m; the
altitude gate and HMAX=80 already cover it with no new tuning.

## 2026-07-21 — Phase 14b 35 m batch executed; matrix rejected, SIFT accepted

User found the first Phase 14b attempt was launched through the raw runner
without the full timing contract (`--hover-s 90 --post-loss-hover-s 50`), so it
ended too early and only recorded 91 flow frames. Added the missing rerunnable
batch YAML:

`experiments/configs/mvp/batches/phase14b_altitude_35m.yaml`

Dry-run confirmed all six cases now expand with `--hover-s 90` and
`--post-loss-hover-s 50`; stock uses scenario timing
`--gnss-loss-after-takeoff-s 19.0`, while LK/SIFT/no-aid use the scenario
10 s trigger. The real batch ran here:

`experiments/batches/20260721_151916_phase14b_altitude_35m`

Local runtime repairs before/during the run:
- Freed disk by removing regenerated `extracted_csv` directories plus old
  system crash/journal files. Raw ULogs, truth logs, reports, and run folders
  were preserved.
- Fixed PX4 SITL logger permissions. A root-owned
  `/opt/sim_px4/PX4-Autopilot/build/px4_sitl_default/rootfs/log/2026-07-21`
  directory caused `ERROR [logger] Can't open log file` when running as user
  `px4`; restored the log tree to `px4:px4`.
- Stopped one already-landed GNSS-on run's PX4 process so the runner would not
  wait out the long no-loss hover timeout after early landing. The ULog was
  already closed and copied; the case remains rejected. Then patched
  `wait_for_airborne_duration()` in
  `scripts/runner/auto_takeoff_land_pxh_truth.py` so future runs stop the wait
  immediately when early landing is observed after the vehicle has been
  airborne.

Batch result: **Rejected** (`accepted_count=1`, `failed_count=5`). Evidence:

`experiments/batches/20260721_151916_phase14b_altitude_35m/batch_metrics.md`

Key full-window Gazebo-truth metrics:
- LK GNSS-loss: `gnss_loss_verified=True`, 50 s post-loss achieved, rejected;
  max horizontal error `20.84 m`, max height error `13.47 m`, truth drift end
  `64.30 m`.
- SIFT GNSS-loss: **accepted**; max horizontal error `1.71 m`, max height
  error `0.90 m`, truth drift end `10.64 m`.
- Stock GNSS-loss rep1: `gnss_loss_verified=True`, rejected; max horizontal
  error `55.97 m`, max height error `7.32 m`, truth drift end `60.64 m`.
- Stock GNSS-loss rep2: rejected by the fail-loud GPS gate
  (`gnss_loss_verified=False`, fix stayed valid); not interpretable as a
  GNSS-denied performance run.
- LK GNSS-on reference: rejected by flight validation after failsafe/landing
  timing, but aligned close to truth; max horizontal error `1.10 m`, truth
  drift end `0.04 m`.
- No-aid GNSS-loss: `gnss_loss_verified=True`, rejected/divergent baseline;
  max horizontal error `82.56 m`, truth drift end `82.44 m`.

Interpretation: the timing bug is fixed and the 35 m batch produced valid
evidence. Phase 14b is rejected as a matrix because only SIFT remains bounded
and accepted at this altitude. LK and stock, which were acceptable at 15 m, no
longer meet the 35 m acceptance gates in this run. The no-aid baseline diverges
as expected.

Next action: decide whether Phase 14 should tune LK at 35 m or continue with
SIFT as the only accepted 35 m aided candidate.

## 2026-07-21 — Phase 14b stock rep2 rerun: GPS flake resolved, stock still rejected

Reran only `stock_gnssloss_35m_rep2` from
`experiments/configs/mvp/batches/phase14b_altitude_35m.yaml`, preserving the
original full-batch data:

`experiments/batches/20260721_192017_phase14b_altitude_35m`

New run:

`experiments/runs/20260721_192020_phase14b_stock_gnssloss_off50s_flat_rural_phototex_noon_alt35m_pxh_takeoff_land_truth`

This rerun fixed the original stock rep2 GPS-drop flake. The status file shows
`gnss_loss_verified=True`, `gnss_loss_observed_fix_type=0.0`,
`post_loss_hover_s=50.0`, and `airborne_hover_wait_ok=True`; the 50 s
post-loss window was actually flown.

Result: **Rejected**, but now for real performance/validation reasons rather
than missing GNSS loss. Full-window truth alignment:

- Truth drift end: `35.920 m`
- Horizontal error mean/max: `6.268956 m` / `40.886425 m`
- Max 3D error: `40.908680 m`
- ULog distance-sensor gate failed:
  `ulog_distance_sensor_ok=False`,
  `ulog_distance_sensor_max_m=41.680171966552734`,
  `ulog_distance_sensor_height_diff_m=6.214900970458984`

Interpretation: stock r2 is now interpretable and confirms the Phase 14b stock
path is not bounded enough at 35 m under the current gates. The original r2
run remains preserved; the rerun is the usable evidence for that replicate.

## 2026-07-21 — Phase 14b SIFT rep2 rerun accepted

Reran only `sift_xy_gnssloss_35m` from
`experiments/configs/mvp/batches/phase14b_altitude_35m.yaml` as a second SIFT
replicate, preserving the original full-batch data:

`experiments/batches/20260721_193527_phase14b_altitude_35m`

New run:

`experiments/runs/20260721_193531_phase14b_sift_xy_gnssloss_off50s_flat_rural_phototex_noon_alt35m_pxh_takeoff_land_truth`

Result: **Accepted** (`accepted_count=1`, `failed_count=0`). The command used
the Phase 14b timing contract (`--hover-s 90`, `--post-loss-hover-s 50`), GNSS
loss was verified (`fix_type=0.0`), `airborne_hover_wait_ok=True`,
`ulog_flight_ok=True`, and `ulog_distance_sensor_ok=True`.

Full-window truth alignment:

- Truth drift end: `10.254 m`
- Horizontal error mean/max: `0.539414 m` / `1.469144 m`
- Max 3D error: `1.509481 m`
- ULog height/range disagreement: `0.11055 m`

Interpretation: the SIFT 35 m result is repeatable in the observed pair. The
main Phase 14b matrix remains rejected because LK, stock, and no-aid failed,
but SIFT is now the only aided 35 m candidate with two accepted GNSS-loss
runs. Caveat: the separate sensor-contract timing report rejected its own
camera-frame timing gate (`~1.91 Hz` recorded in the scene window), so capture
instrumentation timing should be tracked separately from end-to-end flight
acceptance.

## 2026-07-21 — Phase 14b report created; results accepted as evidence

Created the final Phase 14b comparison report:

`experiments/comparisons/20260721_phase14b_altitude_35m/report.md`

Supporting generated artifacts:

- `experiments/comparisons/20260721_phase14b_altitude_35m/manifest.yaml`
- `experiments/comparisons/20260721_phase14b_altitude_35m/summary.csv`
- `experiments/comparisons/20260721_phase14b_altitude_35m/summary.json`
- `experiments/comparisons/20260721_phase14b_altitude_35m/plots/`
- `experiments/comparisons/20260721_phase14b_altitude_35m/camera_samples/`

Final decision for this rung: accept the results as the Phase 14b evidence set
without relabeling failed runs as passes. The full matrix remains rejected as
an all-method gate, but the evidence set is accepted:

- SIFT GNSS-loss accepted and repeatable at 35 m: r1 H max `1.714957 m`, r2
  H max `1.469144 m`.
- Stock GNSS-loss rejected in two valid runs: r1 H max `55.967629 m`, clean r2
  H max `40.886425 m`.
- LK GNSS-loss rejected: H max `20.842661 m`.
- No-aid GNSS-loss rejected/divergent: H max `82.564907 m`.

The report excludes the original full-batch stock rep2 GPS flake from aggregate
plots and uses the clean stock rep2 rerun instead. It also records the stock
simulator caveat: PX4's stock `optical_flow` Gazebo camera has a `30 m` far
clip, while Phase 14b flew at `35 m`; this is accepted as a stock-baseline
limitation rather than repaired in this phase.

## 2026-07-21 — Phase 14 roadmap fleshed out for batches 2–8

Turned the umbrella `phase_14_difficulty_roadmap.md` from a one-line-per-batch
table into full executable specs for all remaining batches (14b–14h),
baking in what batch 1 taught us. Added per batch: exact prep (what to copy,
which knobs change), the new-engineering item, "what could break" at that
rung, and an acceptance gate — plus a dependency graph, a "What batch 1
changed for every batch after it" section, and four carried-forward open
risks.

Key structural decisions recorded in the roadmap:
- **14b (35 m) and 14c (60 m) are config-only drop-ins** — the
  `wait_for_target_altitude()` gate + HMAX=80 from batch 1 mean altitude
  batches need no code/tuning, just copied scenario YAML + a new manifest.
- **60 m batches (3, 5, 8) switch to the existing 600 m field**
  (`flat_rural_phototex_600m_noon`): at 60 m the camera footprint half-width
  is ~70 m, so the 240 m field risks a driftier run leaving texture / losing
  finite rangefinder. Batch 3 also owes an explicit lidar-headroom check
  (60 m vs 100 m max, 3-sample fan).
- **Dim preset (14d) keeps shadows OFF** — real overcast is diffuse (soft/no
  shadows) and shadows-off also dodges the documented SIFT self-shadow
  poisoning trap; dim = low ambient + low sun elevation only.
- **Terrain height reference is the #1 carried risk (14f).** HMAX=80 is a
  flat-ground trick; on terrain the rangefinder reads height-above-terrain,
  not absolute altitude. Batch 6's real deliverable is the height-reference
  decision (revert HMAX / use `EKF2_TERR_*` / hybrid), carried into 7 and 8.
- **Terrain lighting is the only Python code item (14g):**
  `heightmap_to_web_mesh_world.py` has zero lighting support; port the
  sun/ambient/shadow knobs the flat scene builder already exposes.
- Endgame (14h, dark terrain @ 60 m) hard-gated on 3, 4, 6, 7 each Accepted.

No runs this step — planning/docs only. Next action unchanged: launch
Phase 14b once disk headroom is cleared (currently ~360 MB free, 100% used).

## 2026-07-21 — Phase 14c 60 m altitude batch completed and reported

Created and ran the Phase 14c altitude-60 m batch:

`experiments/configs/mvp/batches/phase14c_altitude_60m.yaml`

Batch folder:

`experiments/batches/20260721_205051_phase14c_altitude_60m`

The batch used the full timing contract (`hover_s=90`,
`post_loss_hover_s=50`) and the 600 m photo-textured field
(`flat_rural_phototex_600m_noon`) for 60 m camera-footprint headroom. All
GNSS-loss cases verified actual GPS loss (`fix_type=0.0`) and completed the
50 s post-loss window; the GNSS-on reference stayed GNSS-on throughout. No run
was suspicious enough to rerun.

Final result: **accepted as evidence; matrix rejected**.

- LK GNSS-loss rejected: H max `575.190331 m`, max 3D `582.589305 m`, truth
  drift end `64.938 m`.
- SIFT GNSS-loss rejected: H max `419.250984 m`, max 3D `427.275966 m`, truth
  drift end `70.066 m`.
- Stock GNSS-loss rejected in two valid replicates: H max `67.404116 m` and
  `34.290274 m`. Stock remains caveated because PX4's stock optical-flow
  camera has a 30 m far clip while this rung flies at 60 m AGL.
- LK GNSS-on reference accepted: H max `0.149769 m`, max 3D `0.994396 m`.
- No-aid GNSS-loss rejected as expected: H max `43.893925 m`, max 3D
  `43.905962 m`.

Created the final report and plots:

`experiments/comparisons/20260721_phase14c_altitude_60m/report.md`

After report generation, Phase 14C ULogs were gzipped and verified with
`gzip -t`; Phase 14B/14C raw Gazebo-truth text logs were also gzipped
losslessly to restore disk headroom for the next batches.

## 2026-07-21 — Phase 14d dim-light 15 m batch completed and reported

Created the dim flat-world preset, generated and validated its SDF, then ran
the Phase 14D six-case batch:

`experiments/configs/mvp/batches/phase14d_dim_lighting_15m.yaml`

Batch folder:

`experiments/batches/20260721_221131_phase14d_dim_lighting_15m`

Final result: **accepted as valid evidence with caveats**. All GNSS-loss runs
verified actual GPS loss (`fix_type=0.0` in runner-observed evidence and
`fix_type < 3` in ULog), and the LK GNSS-on reference stayed GNSS-on. No run
was suspicious enough to rerun.

- LK GNSS-loss accepted: H max `1.445526 m`, max 3D `1.465072 m`.
- SIFT GNSS-loss accepted but caveated: H max `2.021739 m`, max 3D
  `2.048288 m`; the vehicle landed early after dim-light feature/match
  degradation (`airborne_hover_wait_ok=false`, ULog airborne duration
  `43.624 s`).
- Stock GNSS-loss accepted in two valid replicates: H max `0.712371 m` and
  `0.450337 m`.
- LK GNSS-on reference accepted: H max `0.150936 m`, max 3D `0.483560 m`.
- No-aid GNSS-loss rejected as expected after dead-reckoning divergence: H max
  `40.918047 m`, max 3D `40.920585 m`.

Created the final report and plots:

`experiments/comparisons/20260721_phase14d_dim_lighting_15m/report.md`

After report generation, Phase 14D ULogs and raw Gazebo-truth text logs were
gzipped and verified with `gzip -t`. Older already reported raw Gazebo-truth
logs from previous phases were also gzipped losslessly to restore disk
headroom for the remaining batches.

## 2026-07-22 — Phase 14e dim-light 60 m characterization batch completed and reported

Created the 600 m dim/overcast flat-world preset, generated and validated its
SDF, then ran the Phase 14E six-case batch:

`experiments/configs/mvp/batches/phase14e_dim_lighting_60m.yaml`

Batch folder:

`experiments/batches/20260721_230820_phase14e_dim_lighting_60m`

Final result: **accepted as characterization evidence; matrix rejected**. All
GNSS-loss runs verified actual GPS loss (`fix_type=0.0` in runner-observed
evidence and `fix_type < 3` in ULog), recorded Gazebo truth, copied ULogs, and
were postprocessed/aligned against Gazebo truth. The LK GNSS-on reference did
not request GNSS loss, stayed GNSS-on, and remained close to truth, but its LK
flow velocity sign sentinel rejected under the dim 60 m condition. No run was
suspicious enough to rerun.

- LK GNSS-loss rejected after severe divergence: H max `771.272241 m`, max 3D
  `772.692147 m`.
- SIFT GNSS-loss rejected: H max `61.730776 m`, max 3D `77.151877 m`; valid
  loss evidence, but the vehicle landed during the requested post-loss hover.
- Stock GNSS-loss rejected in two valid replicates: H max `46.402739 m` and
  `51.870340 m`. Stock remains caveated because PX4's stock optical-flow
  camera has a 30 m far clip while this rung flies at 60 m AGL.
- LK GNSS-on reference accepted by flight/GNSS evidence: H max `0.363965 m`,
  max 3D `0.943113 m`; flow sign sentinel rejected and is documented as a
  reference caveat.
- No-aid GNSS-loss rejected as expected: H max `67.896167 m`, max 3D
  `67.938136 m`.

Created the final report and plots:

`experiments/comparisons/20260721_phase14e_dim_lighting_60m/report.md`

After report generation, Phase 14E ULogs and raw Gazebo-truth text logs were
gzipped losslessly and verified with `gzip -t`. Next action: start Phase 14F,
the terrain baseline at a safe 15-35 m altitude, with the height-reference
strategy as the main decision because flat-ground `EKF2_RNG_A_HMAX=80` does
not directly transfer to terrain relief.

## 2026-07-22 — Phase 14f terrain baseline completed and reported

Created and ran the Phase 14F terrain-baseline batch at 15 m AGL on
`serefli_koschisar_flowtex`:

`experiments/configs/mvp/batches/phase14f_terrain_baseline_15m.yaml`

Final report and plots:

`experiments/comparisons/20260722_phase14f_terrain_baseline_15m/report.md`

Final result: **accepted with limitations**. This is accepted as terrain
characterization evidence, not as a universal method-win gate. All GNSS-loss
cases in the final report verified actual GPS loss from ULog GPS topics
(`fix_type=0` during the loss window), and the LK GNSS-on reference stayed
GNSS-on (`fix_type=3`, satellites=10) with no `SIM_GPS_USED 0` command.

Terrain GNSS-loss initially exposed a PX4 Gazebo bridge issue: the terrain
world's NavSat path kept publishing GPS fix data after `SIM_GPS_USED` was set
to 0. PX4 was patched in:

`/opt/sim_px4/PX4-Autopilot/src/modules/simulation/gz_bridge/GZBridge.cpp`

`GZBridge::navSatCallback()` now refreshes `_sim_gps_used` before publishing
`sensor_gps`. Post-patch GNSS-loss runs showed the expected ULog `fix_type=0`
state.

Results:

- LK GNSS-loss accepted: H max `1.190626 m`, max 3D `1.203275 m`.
- SIFT GNSS-loss accepted: H max `2.477781 m`, max 3D `2.496030 m`; bounded
  but worse than LK in this terrain batch.
- Stock GNSS-loss replicate 1 accepted as rejected-performance evidence: H max
  `51.264044 m`, max 3D `51.265280 m`; valid GNSS loss, but rangefinder/terrain
  validation and altitude behavior remained caveated.
- Stock GNSS-loss replicate 2 accepted as rejected-performance evidence: H max
  `109.051513 m`, max 3D `109.054747 m`; valid GNSS loss and confirms unstable
  stock behavior on this terrain batch.
- LK GNSS-on reference accepted with manual-closeout caveat: H max
  `0.236535 m`, max 3D `0.339562 m`; the no-loss direct runner failed to stop
  cleanly, so PX4 was stopped gracefully, the ULog was recovered from PX4
  rootfs, and Gazebo truth was postprocessed with model
  `x500_cam_lidar_down_0`.
- No-aid GNSS-loss accepted as catastrophic-baseline evidence: H max
  `417.551074 m`, max 3D `456.022449 m`; valid GNSS loss, climbed to about
  `204.5 m`, and diverged.

The report generator was also corrected so the "World And Environment
Settings" section no longer hardcodes the older flat-world wording; it now
states that world/lighting/texture/wind values are read from each run's copied
`config.yaml`.

After report generation, Phase 14F ULogs and raw Gazebo-truth text logs were
gzipped losslessly and verified with `gzip -t`. Free space after compression:
about `1.3G` on `/opt`. Next action: Phase 14G terrain dim-light, which
requires porting dim/overcast lighting controls into the terrain world
generator before running the same six-case terrain matrix.

## 2026-07-22 — Phase 14g dim-terrain batch completed and reported

Added terrain dim-light support to:

`scripts/worlds/heightmap_to_web_mesh_world.py`

The new `--lighting-preset dim_overcast_no_shadows` path updates the generated
terrain SDF scene/light fields after the proven terrain visual replacement
step. Generated and validated:

`generated_worlds/terrain/serefli_koschisar_flowtex_dim/serefli_koschisar_flowtex_dim.world`

Validation:

`gz sdf -k generated_worlds/terrain/serefli_koschisar_flowtex_dim/serefli_koschisar_flowtex_dim.world`

Result: `Valid`.

Created and ran the Phase 14G dim-terrain batch:

`experiments/configs/mvp/batches/phase14g_terrain_dim_15m.yaml`

Final report and plots:

`experiments/comparisons/20260722_phase14g_terrain_dim_15m/report.md`

Final result: **accepted with limitations**. All GNSS-loss cases verified
actual GPS loss from ULog GPS topics (`fix_type=0` during the loss window).
The LK GNSS-on reference stayed `fix_type=3` with 10 satellites and had no
`SIM_GPS_USED 0` command, but required manual closeout because the known
no-loss runner long-hold issue remains. The no-aid case was rerun directly
after cleaning the stale Gazebo websocket state left by the interrupted
GNSS-on reference.

Results:

- LK GNSS-loss accepted: H max `2.265130 m`, max 3D `2.278644 m`.
- SIFT GNSS-loss accepted with sensor-contract caveat: H max `3.667356 m`,
  max 3D `3.678212 m`; SIFT sent fewer bridge rows and the sensor-contract
  report rejected.
- Stock GNSS-loss replicate 1 accepted as rejected-performance evidence:
  H max `46.131063 m`, max 3D `46.135581 m`; valid GNSS loss, rangefinder /
  terrain validation reject.
- Stock GNSS-loss replicate 2 accepted as rejected-performance evidence:
  H max `46.217468 m`, max 3D `46.221783 m`; repeatable stock drift near
  46 m.
- LK GNSS-on reference accepted with manual-closeout caveat: H max
  `0.137504 m`, max 3D `0.509660 m`.
- No-aid GNSS-loss accepted as rejected-performance baseline: H max
  `38.263638 m`, max 3D `38.273682 m`; valid GNSS loss, no optical-flow
  bridge or stock flow.

Interpretation: the dim-terrain code path is usable, and LK/SIFT remain
bounded at 15 m, but both degrade versus Phase 14F default-light terrain.
Stock/no-aid remain valid behavior baselines rather than passes.

After report generation, Phase 14G ULogs and raw Gazebo-truth text logs were
gzipped losslessly and verified with `gzip -t`. Free space after compression:
about `1.1G` on `/opt`. Next action: Phase 14H, the final dim-terrain 60 m
endgame, using the Phase 14G dim terrain world and Phase 14F HMAX=5 terrain
height-reference strategy.

## 2026-07-22 — Phase 14h dim-terrain 60 m endgame completed and reported

Created and ran the final Phase 14H dim-terrain 60 m matrix:

`experiments/configs/mvp/batches/phase14h_dark_terrain_60m.yaml`

Final report and plots:

`experiments/comparisons/20260722_phase14h_dark_terrain_60m/report.md`

Final result: **accepted with limitations**. This is accepted as endgame
characterization evidence, not a clean method-win gate. All report cases
matched their manifest GNSS state according to ULog GPS topics. Scratch
attempts without complete evidence were marked with `SCRATCH_INVALID_RUN.md`
and excluded.

Results:

- LK GNSS-loss accepted as rejected-performance evidence: H max `34.594090 m`,
  max 3D `34.595394 m`; flow delivery and GNSS loss were valid, but
  terrain/rangefinder validation rejected after drift.
- SIFT GNSS-loss accepted as rejected-performance evidence: H max
  `96.102057 m`, max 3D `96.102465 m`; severe dim-terrain 60 m degradation.
- Stock GNSS-loss replicate 1 accepted as rejected-performance evidence:
  H max `38.077281 m`, max 3D `38.084020 m`.
- Stock GNSS-loss replicate 2 accepted as rejected-performance evidence:
  H max `54.451558 m`, max 3D `54.461421 m`; one prior r2 attempt aborted
  before OFFBOARD/GNSS loss and was excluded as scratch.
- LK GNSS-on reference accepted with manual-closeout caveat: H max
  `0.131828 m`, max 3D `0.954246 m`; ULog `vehicle_gps_position` and
  `sensor_gps` stayed `fix_type=3`, satellites=10 throughout.
- No-aid GNSS-loss accepted as rejected-performance baseline: H max
  `36.011929 m`, max 3D `36.023582 m`.

Interpretation: the 60 m dim-terrain stack is validly characterized. LK
degraded but remained below SIFT in this endgame condition; stock/no-aid are
baselines, not passes. The tight GNSS-on LK reference shows the truth/alignment
pipeline itself stayed healthy at 60 m dim terrain.

After report generation, Phase 14H evidence ULogs and raw Gazebo-truth text
logs were gzipped losslessly and verified with `gzip -t`; scratch large
artifacts were also compressed. Phase 14A-14H are now complete.

## 2026-07-22 — Phase 14G dark-terrain 15 m repeat batch completed

Ran a fresh six-case Phase 14G dim/dark-terrain repeat at 15 m AGL after the
Phase 14H endgame:

`experiments/configs/mvp/batches/phase14g_terrain_dim_15m.yaml`

Final repeat report and plots:

`experiments/comparisons/20260722_phase14g_dark_terrain_15m_repeat/report.md`

Final result: **accepted** as repeat characterization evidence. All report
cases matched their manifest GNSS state according to ULog GPS topics. The
GNSS-loss runs showed PX4 GPS dropping to `fix_type=0` and
`satellites_used=0`; the LK GNSS-on reference stayed GNSS-on throughout.
Stock/no-aid runner validation `false` was accepted as expected drift
baseline behavior, not a data-quality rejection.

Results:

- LK GNSS-loss accepted: H mean `0.960 m`, H max `2.776 m`.
- SIFT GNSS-loss accepted: H mean `1.183 m`, H max `3.382 m`.
- Stock GNSS-loss replicate 1 accepted as drift baseline: H mean `7.395 m`,
  H max `61.213 m`.
- Stock GNSS-loss replicate 2 accepted as drift baseline: H mean `7.232 m`,
  H max `63.695 m`.
- LK GNSS-on reference accepted: H mean `0.053 m`, H max `0.115 m`.
- No-aid GNSS-loss accepted as drift baseline: H mean `4.873 m`, H max
  `34.863 m`.

After each run, the ULog and raw Gazebo-truth text were gzipped losslessly and
verified with `gzip -t`. Free space after report generation and compression:
about `426M` on `/opt`.

## 2026-07-24 — Found and fixed: Phase 14G/14H dim-terrain scenarios were flying against a browser-only visual substitute, not real terrain

While building Phase 17 dashboard support for picking terrain worlds and
applying wind to them, traced why one terrain world
(`generated_worlds/terrain/serefli_koschisar_flowtex_dim/`) had a
colored-tiles fingerprint (1024 `terrain_tile_<row>_<col>` visuals, a
32x32 grid) identical to the confirmed browser-visualization-only
substitute pattern documented in Phase 17B. Its own
`PROVENANCE.yaml` confirms it: `generator:
scripts/worlds/heightmap_to_web_mesh_world.py`, `terrain_visual:
{visual_mode: colored_tiles, tile_count: 32, ...}`. That script's own
docstring is explicit about why this mode exists: gzweb (the *browser*
viewer) cannot reliably render real heightmap materials/textures, so
this mode substitutes a grid of flat colored boxes for the browser view
only - "the source heightmap remains available as the collision
geometry; this script only replaces the browser-facing visual in a
separate output world."

That substitute was never meant to be flown in. It was, though: all 10
Phase 14G/14H scenarios needing the dim/overcast lighting condition
point `world.sdf_path` directly at it (LK/SIFT/stock/no-aid, both GNSS
states, both 15 m and 60 m altitude - see
`phase_14g_terrain_baseline_15m`/`phase_14h_terrain_endgame_60m` and
their batch/comparison docs). No non-substitute dim-lighting terrain
world existed to use instead - it isn't that the wrong file was picked
by mistake, the correct one was never generated. Collision physics used
the real heightmap regardless (confirmed: the substitute's own
`<collision><geometry><heightmap>` is unchanged from the source terrain,
per that script's own design), so GNSS-loss dead-reckoning and IMU/EKF
conclusions from these runs are not affected. What every one of these
runs' downward/optical-flow camera actually saw for its whole flight was
the blocky 32x32 substitute, not the real aerial-textured terrain - this
matters specifically for the LK/SIFT optical-flow quality being tested.

Fix: generated `generated_worlds/terrain/serefli_koschisar_flowtex_dim_real/`
by applying the exact same `dim_overcast_no_shadows` lighting preset
(same `LIGHTING_PRESETS` dict, same `apply_lighting_preset()` function,
reused directly - not reinvented) to the real, non-substitute
`serefli_koschisar_flowtex.world` instead of going through the
colored_tiles pipeline at all. Confirmed the result: 0 `terrain_tile_`
visuals, 4 `<heightmap>` references (real terrain unchanged), sun
direction keeps the source world's documented X=0 constraint (gz-sim/
OGRE2 shadow-map artifact when both horizontal components are nonzero).
See its `PROVENANCE.yaml` for the full record.

Updated all 10 affected scenario YAMLs' `world.name`/`world.sdf_path` to
point at the corrected world (surgical two-line diff per file, nothing
else touched). **Historical run results in `experiments/runs/` and
already-published phase docs/comparisons are untouched** - they remain
an accurate record of what actually happened (camera saw the
substitute). This fix only changes what a *future* re-run of these
scenario files will do. Whether the affected Phase 14G/14H optical-flow
conclusions need to be re-run against the corrected world, or footnoted
as substitute-terrain results, is a call for whoever owns that phase's
acceptance - not made here.

Also fixed two related dashboard bugs found while building this: the
`is_browser_substitute` detector was a loose substring match on the
provenance `generator` field, which false-flagged the new corrected
world (its own provenance truthfully mentions the same script name in a
different context) - narrowed to the authoritative
`terrain_visual.visual_mode == "colored_tiles"` field instead. And a
missing `ValueError` handler in the scenario-creation endpoint turned a
substitute-world rejection into a raw 500 instead of a clean 422.
