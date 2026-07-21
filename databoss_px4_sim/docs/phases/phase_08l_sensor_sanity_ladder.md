# Phase 8L — Full Sensor Sanity Ladder Before More Flow Tuning

Status: In progress

## Goal

Prove the DATABOSS camera/range/flow contracts one gate at a time before any
more `vy`, LK, or EKF tuning. Phase 8K showed that the LK bridge can reach EKF2
but still gets rejected and then loses physical scene/range; 8L stops the
repair loop until each upstream sensor assumption has evidence.

## Why this phase exists

The rejected Phase 8K smoke (`20260716_134001`) fixed bridge starvation but
still failed with high optical-flow rejection, `distance_sensor_ok=False`, and
late-run camera/range collapse. PX4 stock flow survives the same profile, so
the remaining difference must be isolated across scene, pose, axis mapping,
timing, gyro/rotation behavior, range coupling, and EKF fusion.

## In scope

- New opt-in `sensor_contract_report.json` per Phase 8L run.
- Static model/world/bridge contract audit.
- Scene hover proof.
- Four signed LK open-loop translation legs with `EKF2_OF_CTRL=0`.
- GNSS-on LK delay sweep and fusion proof.
- Staged GNSS-loss smoke only after earlier gates pass.

## Out of scope

- Full 3x GNSS-loss LK replicate batch until all sanity gates pass.
- Roll/pitch attitude oscillation flights until an attitude-setpoint runner is
  added. The current offboard sender supports position/velocity/yaw target
  commands, but not roll, pitch, body-rate, or attitude oscillation setpoints.

## Inputs

- Primary world: `flat_rural_phototex_noon`
- Vehicle: `gz_x500_cam_lidar_down`
- Current bridge hypothesis: LK, `axis_map: "-yx"`, `rate_hz: 40`,
  `lk_max_flow_rate_rad_s: 7.4`, no bridge-side send gating, NaN gyro fields
  by default, optional same-window Gazebo IMU gyro integration for Gate 5,
  `distance=-1` in `OPTICAL_FLOW_RAD`.
- Existing reference: PX4 stock `gz_x500_flow` bounded 3/3 in Phase 8J.

## Implementation

Added `scripts/analysis/sensor_contract_report.py`. It can:

- emit a static audit of DATABOSS and stock model poses plus bridge contract;
- summarize camera frame rate and texture/feature coverage, using an inferred
  hover-valid scene window from rangefinder data for scene pass/fail while
  preserving full-capture diagnostics;
- summarize range finite fraction and range statistics;
- summarize bridge sent rate, quality, and matches;
- summarize EKF optical-flow fusion and truth metrics when available;
- evaluate Phase 8L gates as machine-readable pass/fail checks.

`scripts/runner/run_scenario_pxh_end_to_end.py` now runs this report only when
a scenario opts in with:

```yaml
analysis:
  sensor_contract_report: true
  sensor_contract_gate: scene|axis|timing|fusion|loss
  sensor_contract_gate_required: true|false
```

Older phases are not affected.

Added `scripts/worlds/prove_phase8l_attitude_pose.py` for Gate 3. It creates
pose-specific standalone Gazebo worlds with a static `x500_cam_lidar_down`
include at 2.5 m AGL, records camera/range topics, and checks each requested
roll/pitch/yaw pose. The combined vehicle now also has `model.config` in both
the DATABOSS source model and deployed PX4 Gazebo model folder so standalone
`model://x500_cam_lidar_down` includes resolve like stock PX4 models.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1

# Gate 1: static contract audit, no sim.
venv/bin/python scripts/analysis/sensor_contract_report.py --static-audit

# Gate 2: scene/range hover proof.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_01_scene_hover_sanity.yaml --continue-on-fail

# Gate 3: standalone camera/LiDAR attitude pose proof.
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/worlds/prove_phase8l_attitude_pose.py \
  --record-s 8 --startup-timeout-s 35

# Gate 4: four signed translation legs, only after gate 3 passes.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_02_openloop_axis_lk_four_legs.yaml --continue-on-fail

# Gate 5: yaw rotation sanity, NaN gyro baseline vs Gazebo-IMU gyro candidate.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_03_rotation_gyro_baseline.yaml --continue-on-fail

# Gate 6: timing/delay sweep, after axis proof passes.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_04_timing_delay_sweep_gnsson.yaml --continue-on-fail

# Gate 7: GNSS-on fusion proof.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_05_gnsson_fusion_proof.yaml --continue-on-fail

# Gate 8: staged GNSS-loss smoke, only after gates 1-7 pass.
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8l_06_gnss_loss_smoke_staged.yaml --continue-on-fail
```

## Expected outputs

Each completed run should contain:

- `sensor_contract_report.json`
- `sensor_contract_report.md`
- normal run evidence: `validation.md`, `flow_recording/`, `flow_bridge/`,
  `flow_fusion_ulog.json` when flow is enabled, and truth-alignment metrics
  when postprocess succeeds.

## Acceptance criteria

Hard stops:

- camera sees blank/sky-like scene at hover;
- LiDAR finite fraction below 0.95 in normal hover or axis proof;
- any translation leg has wrong dominant sign;
- GNSS-on fusion rejected/fused ratio is >=0.10 after delay selection;
- truth postprocess/alignment is missing or permission-blocked.

GNSS-loss smoke acceptance:

- horizontal max <=10 m accepted, 10-25 m accepted with limitations;
- height absolute max <=10 m;
- rejected/fused <=0.25 for smoke, <=0.10 for final;
- finite range fraction >=0.9 before any drift event.

## Results

Gate 1 static audit completed and saved:

- `experiments/inspections/phase8l_static_contract_audit.json`

Gate 2 initially rejected on run
`experiments/runs/20260716_172812_phase8l_scene_hover_flat_rural_phototex_noon_pxh_takeoff_land_truth`
because the first implementation sampled the full camera capture, including
early takeoff frames. Visual inspection showed frame `000000` was sky-like
while the rangefinder still read `0.174 m`; mid/end hover frames were
downward-looking textured ground. The report was repaired to infer a hover
scene window from rangefinder data and use that window for scene pass/fail.
The same evidence then passed:

- hover window: `26.64-49.38 s`
- camera frames in window: `689` at `30.35 Hz`
- hover texture cell fraction mean: `1.0`
- hover feature count median/min: `600 / 600`
- hover range finite fraction: `1.0`
- hover range median: `2.5019 m`
- full-capture texture fraction remained `0.75`, correctly preserving the
  early sky/transition evidence

Fresh Gate 2 rerun accepted:

- batch:
  `experiments/batches/20260716_190312_phase8l_01_scene_hover_sanity`
- run:
  `experiments/runs/20260716_190315_phase8l_scene_hover_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- scene window: `26.36-51.2 s`
- camera frames in window: `753` at `30.34 Hz`
- hover texture cell fraction mean: `1.0`
- hover feature count median: `600`
- hover range finite fraction: `1.0`
- hover range median: `2.4992 m`
- truth horizontal max: `0.1146 m`
- result: `Accepted`

Gate 3 standalone camera/LiDAR attitude pose proof accepted:

- report:
  `experiments/inspections/20260716_213752_phase8l_attitude_pose_proof/attitude_pose_report.md`
- JSON:
  `experiments/inspections/20260716_213752_phase8l_attitude_pose_proof/attitude_pose_report.json`
- poses: level, roll `+/-5/+/-10 deg`, pitch `+/-5/+/-10 deg`, yaw
  `+/-45/+/-90 deg`
- all 13 poses accepted
- camera texture cell fraction: `1.0` for every pose
- camera sky-like fraction: `0.045-0.083`
- feature median: saturated at `600`
- LiDAR finite fraction: `1.0` for every pose
- LiDAR median range: `2.687-2.744 m`
- attitude-corrected range error: `0.169-0.205 m`, within the `0.30 m` gate

Gate 4 open-loop LK axis proof accepted:

- batch:
  `experiments/batches/20260716_222415_phase8l_02_openloop_axis_lk_four_legs`
- cases accepted: `4 / 4`
- `+PX4 X/North` run:
  `experiments/runs/20260716_222419_phase8l_openloop_lk_px_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant flow rate: `+0.12457 rad/s`
  - cross-axis flow rate: `+0.00197 rad/s`
  - magnitude ratio: `1.0517`
  - range finite fraction: `1.0`
- `-PX4 X/North` run:
  `experiments/runs/20260716_223525_phase8l_openloop_lk_nx_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant flow rate: `-0.12494 rad/s`
  - cross-axis flow rate: `+0.00039 rad/s`
  - magnitude ratio: `1.0413`
  - range finite fraction: `1.0`
- `+PX4 Y/East` run:
  `experiments/runs/20260716_224619_phase8l_openloop_lk_py_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant flow rate: `-0.12506 rad/s`
  - cross-axis flow rate: `-0.00094 rad/s`
  - magnitude ratio: `1.0445`
  - range finite fraction: `1.0`
- `-PX4 Y/East` run:
  `experiments/runs/20260716_225726_phase8l_openloop_lk_ny_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - dominant flow rate: `+0.12671 rad/s`
  - cross-axis flow rate: `-0.00146 rad/s`
  - magnitude ratio: `1.0465`
  - range finite fraction: `1.0`

All four legs passed sign, axis dominance, magnitude ratio, bridge-rate,
bridge-quality, and finite-range checks. This accepts the current
`axis_map: "-yx"` contract for the LK bridge translation path.

Gate 4 harness fixes:

- `scripts/runner/auto_takeoff_land_pxh_truth.py` now treats
  `control.skip_landing_command: true` as `landing_required=False`, matching
  the scenario intent and avoiding false flight-wrapper rejection.
- Gate 4 scenario camera side-recording was reduced to `rate_hz: 5`,
  `max_width: 320`; the LK bridge itself remained unchanged at `rate_hz: 40`,
  `max_width: 320`.

Gate 5 yaw rotation/gyro sanity accepted:

- batch:
  `experiments/batches/20260717_065827_phase8l_03_rotation_gyro_baseline`
- cases accepted: `2 / 2`
- NaN-gyro baseline run:
  `experiments/runs/20260717_065831_phase8l_rotation_yaw_nan_gyro_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - bridge real rows: `1228`
  - bridge real rate: `30.33 Hz`
  - bridge quality median: `215`
  - gyro mode: `nan`
  - gyro available fraction: `0.0`
  - range finite fraction: `1.0`
  - range median: `2.4198 m`
  - flow abs mean rad/s x/y: `0.2716 / 0.3102`
  - truth horizontal max: `3.1961 m`
  - Gazebo station displacement end: `1.1998 m`
  - result: `Accepted`
- Gazebo-gyro candidate run:
  `experiments/runs/20260717_071103_phase8l_rotation_yaw_gzgyro_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - bridge real rows: `1225`
  - bridge real rate: `30.33 Hz`
  - bridge quality median: `207`
  - gyro mode: `gz_camera_down_y90`
  - gyro available fraction: `1.0`
  - range finite fraction: `1.0`
  - range median: `2.3317 m`
  - flow abs mean rad/s x/y: `0.3283 / 0.4021`
  - gyro abs mean rad/s x/y/z: `0.0689 / 0.0802 / 0.1033`
  - truth horizontal max: `3.1369 m`
  - Gazebo station displacement end: `0.8079 m`
  - result: `Accepted`

Gate 5 implementation notes:

- `scripts/sim/flow_mavlink_bridge.py` now supports `--gyro-mode nan`,
  `--gyro-mode gz_body`, and `--gyro-mode gz_camera_down_y90`.
- The candidate mode integrates Gazebo IMU angular velocity over the same
  frame interval as the LK optical-flow sample and sends finite
  `integrated_xgyro/ygyro/zgyro` fields in `OPTICAL_FLOW_RAD`.
- The accepted DATABOSS downward camera mount is represented as
  `gz_camera_down_y90`, mapping body angular velocity to camera-frame gyro as
  `[-wz, wy, wx]`.
- `scripts/runner/auto_takeoff_land_pxh_truth.py` passes the bridge gyro mode
  and the Gazebo IMU topic, and can now enable yaw-target local hold with
  `control.use_yaw: true`.

Gate 6 timing/delay sweep completed and rejected:

- batch:
  `experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson`
- cases accepted by wrapper/report: `4 / 5`
- batch result: `Rejected`
- route comparison:
  `experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson/route_comparison/route_comparison.md`
- route plots:
  - `route_comparison/all_gazebo_truth_routes_with_expected.png`
  - `route_comparison/all_px4_ekf_routes_with_expected.png`
  - `route_comparison/truth_vs_ekf_by_delay.png`
  - `route_comparison/horizontal_error_over_time.png`
- intended route for all five cases: approximately `+10.60 m` in PX4 Y/East
  and `0 m` in PX4 X/North (`vy=0.2 m/s`, active wait target `53 s`)
- delay `60 ms`:
  - gate result: `pass`
  - truth endpoint X/Y: `15.79 / -6.55 m`
  - endpoint error to intended route: `23.31 m`
  - truth path length: `105.74 m` (`10.0x` intended path length)
  - max horizontal EKF-vs-truth error: `12.69 m`
- delay `90 ms`:
  - gate result: `pass`
  - truth endpoint X/Y: `-6.22 / 4.37 m`
  - endpoint error to intended route: `8.80 m`
  - truth path length: `86.89 m` (`8.2x` intended path length)
  - max horizontal EKF-vs-truth error: `7.79 m`
- delay `111 ms`:
  - gate result: `fail`
  - failed checks:
    `flow_fusion_rows`, `flow_active_fraction`, `flow_reject_ratio`,
    `distance_sensor_ok`
  - truth endpoint X/Y: `4.42 / -9.62 m`
  - endpoint error to intended route: `20.70 m`
  - truth path length: `120.42 m` (`11.4x` intended path length)
  - max horizontal EKF-vs-truth error: `13.56 m`
- delay `140 ms`:
  - gate result: `pass`
  - truth endpoint X/Y: `-1.75 / 3.05 m`
  - endpoint error to intended route: `7.75 m`
  - truth path length: `93.77 m` (`8.8x` intended path length)
  - max horizontal EKF-vs-truth error: `8.60 m`
- delay `180 ms`:
  - gate result: `pass`
  - truth endpoint X/Y: `28.24 / 7.40 m`
  - endpoint error to intended route: `28.42 m`
  - truth path length: `112.35 m` (`10.6x` intended path length)
  - max horizontal EKF-vs-truth error: `15.62 m`

Gate 6 route conclusion:

- `EKF2_OF_DELAY` changes the details, but all five physical Gazebo truth
  routes are looped/curved and far longer than the intended straight
  `+Y` leg.
- The bridge itself stayed healthy in every case: real flow rows were
  `1678-1686`, real bridge rate was about `30.32 Hz`, range finite fraction
  was `1.0`, and quality stayed nonzero.
- Therefore the current hard problem is not just optical-flow delay. Before
  GNSS-loss smoke, the route/control/flow feedback loop must be isolated with
  GNSS-on truth route plots as the primary evidence.

### Gate 6b — root-cause isolation of the looped routes (2026-07-17)

Question answered: with GNSS on, EKF position tracked truth almost perfectly
in every Gate 6 run, so why did the vehicle physically loop?

Diagnosis evidence (batch
`experiments/batches/20260717_072640_phase8l_04_timing_delay_sweep_gnsson/velocity_loop_diagnosis/`,
script `scripts/analysis/diagnose_gate6_velocity_loop.py`):

- EKF horizontal velocity was decorrelated from truth velocity
  (corr `0.03-0.25`, mean gap `1.6-2.4 m/s`) while EKF position stayed
  GNSS-pinned to truth. The controller flew in velocity mode, so it drove the
  corrupted velocity estimate to the `0.2 m/s` setpoint while the physical
  speed swung at up to `3.7 m/s` -> loops with truthful position plots.
- EKF heading took a `~70 deg` error excursion at takeoff (flow innovations
  coupling into yaw while yaw variance was high) and never fully recovered.
- All EKF test ratios stayed below `1.0`: flow and GNSS were blended, never
  rejected, so the filter reported itself healthy.

Root cause (script `scripts/analysis/fit_flow_contract_from_truth.py`,
regression of sent flow rates against Gazebo-truth body velocity and body
rates, R^2 `0.98-0.999`, two runs):

- The bridge sent `flow_x ~ -vbx/h`, `flow_y ~ -vby/h`.
- PX4 EKF2 fuses `flow_x ~ +vby/h + wx`, `flow_y ~ -vbx/h + wy`
  (PX4-Autopilot `src/modules/ekf2/EKF/aid_sources/optical_flow/
  optical_flow_control.cpp:125-126`).
- The sent translation contract was therefore rotated 90 deg from what PX4
  fuses. `SENS_FLOW_ROT` was explicitly `0`, so nothing masked it.
- Why Gate 4 missed it: the open-loop analyzer's expected signs
  (`analyze_flow_bridge_openloop.py`) were self-derived with the spawn yaw
  baked in (the x500 spawns facing East, NED yaw 90 deg) and were never
  checked against PX4's own fusion convention. Gate 4 proved the bridge
  self-consistent, not PX4-consistent. The Gate 4 `axis_map: "-yx"`
  translation acceptance is superseded by this gate.

Isolation tests (manual pair,
`experiments/batches/manual_gate6_isolation_20260717/`):

- A control, flow sent but not fused (`ekf2_of_ctrl: 0`), GNSS on, same
  scenario/route as `delay140`:
  `experiments/runs/20260717_092410_phase8l_lk_gnsson_ofoff_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - accepted; straight leg flown, truth end displacement `10.39 m`
    (intended `10.6 m`), max horizontal EKF-vs-truth error `0.44 m`,
    truth speed `0.192 m/s` vs setpoint `0.2 m/s`, yaw error `~5.8 deg`,
    all test ratios `<= 0.02`.
  - Proves route, runner, controller, and GNSS-on estimation are sane;
    flow fusion was the culprit.
- B fix candidate, `axis_map: "-x-y"`, fusion on, `EKF2_OF_DELAY=140`:
  `experiments/runs/20260717_094226_phase8l_lk_gnsson_axisfix140_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  - accepted by the runner gate; route improved on every metric but is
    **still looped**, not straight: truth path `50.4 m` (`4.8x` intended,
    was `8.2-11.4x`), endpoint error to intended `4.07 m`
    (was `7.75-28.42 m`), straightness `0.22` (control: `0.995`),
    horizontal EKF-vs-truth error mean `2.28 m` / max `4.41 m`
    (was max `8-16 m`). Route overlay:
    `experiments/batches/manual_gate6_isolation_20260717/diagnosis/route_overlay_gate6b.png`.
  - Post-run contract fit now matches PX4 translation exactly:
    `flow_x = +1.04*vby/h`, `flow_y = -1.01*vbx/h`.
  - Remaining defects: bounded velocity oscillation (truth speed mean
    `0.91 m/s` vs setpoint `0.2`), steady `~25 deg` EKF heading offset
    seeded at takeoff, and rotation content fitted at `~1.3x` body rate
    (wide 100-deg FOV inflates mean LK flow under rotation, so PX4's
    onboard-gyro NaN-substitution under-compensates), plus variable bridge
    compute latency (`40-170 ms`) against the fixed delay parameter.

Follow-up isolation runs (same batch folder):

- Run C `axisfix140_rgate` (`send_min_range_m: 1.5`): **rejected**. Worse on
  every metric (path `90.8 m`, endpoint error `24.1 m`, hmax `19.3 m`),
  heading ran to `~180 deg` with three emergency yaw resets and heading
  aiding dropping out. Measured fusion start shows EKF2 already begins flow
  aiding in-air at `~+2 s` / `1.35 m` altitude; the gate barely moved that
  (`+4.1 s` / `1.42 m`), so timing is not a usable lever here.
- Run D `axisfix140_ofn05` (`ekf2_of_n_min: 0.5`): **accepted**. Straight
  fused-flow leg: path `12.8 m` (`1.2x`), straightness `0.977` (control
  `0.995`), cross-track end error `0.15 m`, hmax `0.70 m`, flow fused
  `99.4%` / rejected `0.0%`, yaw error mean `9.2 deg`, test ratios
  `<= 0.03`.

Gate 6b status: root cause **Accepted** and the loop is closed with two
config changes: `axis_map: "-x-y"` (translation contract 90 deg off PX4)
plus `ekf2_of_n_min: 0.5` (default flow noise floor let LK flow out-weight
GNSS/mag, drag heading `~25 deg` during the in-climb fusion start, and
sustain a residual loop). Flow fused fraction (healthy `> 90%`) is adopted
as a gate metric: the looping runs sat at `45-60%` fused.

Supersession note (2026-07-20): Phase 8N rechecked the sign with PX4's own
`estimator_optical_flow_vel` against GPS body velocity. That sentinel showed
`axis_map: "xy"` has the correct post-EKF velocity sign, while this Gate 6b
`axis_map: "-x-y"` workaround is inverted but can still fly GNSS-on when
`EKF2_OF_N_MIN=0.5` de-weights flow. Treat the Gate 6b route acceptance as a
GNSS-on workaround, not the final sign contract.

GNSS-loss failsafe-isolation rerun (2026-07-17):

- Scenario:
  `experiments/configs/mvp/scenarios/phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon.yaml`
- Batch:
  `experiments/batches/20260717_143620_phase8l_lk_failsafe_isolation`
- Run:
  `experiments/runs/20260717_143624_phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth`
- Goal: isolate the failsafe/safety-condition variable by keeping the Gate 6b
  LK measurement configuration fixed and applying `delayed_observation` plus
  `MPC_XY_VEL_MAX=2.0`.
- Config/evidence:
  - `failsafe_profile=delayed_observation`
  - `failsafe_profile_ok=True`
  - applied commands included `NAV_DLL_ACT 0`, `COM_POS_FS_EPH 200`,
    `COM_POS_LOW_ACT 0`, `EKF2_NOAID_TOUT 120000000`, and
    `MPC_XY_VEL_MAX 2.0`
  - `flow_bridge_sent_rows=1016`, `flow_recording_frames=1975`
  - effective GNSS loss was still `10.0 s` after takeoff even though the CLI
    requested `20.0 s`, preserving the known timing ambiguity
- Result: **Rejected**.
  - End-to-end runner rejected because `distance_sensor_ok=False`; ULog max
    height above start was `25.86 m`, and distance-sensor/height disagreement
    was `22.24 m`.
  - EKF-vs-Gazebo-truth alignment rejected: horizontal error mean/max/end
    `564.44 / 2515.98 / 2515.98 m`, height absolute error max
    `203.96 m`, and Gazebo truth station displacement end `2535.97 m`.
  - Optical-flow fusion was present but unhealthy: `399` fused, `82`
    rejected, rejection/fused ratio `0.2055`, `cs_opt_flow` active fraction
    `0.4135`, and `xy_reset_counter_delta=3`.
  - Sensor contract `loss` gate rejected: bridge present/rate OK and GNSS loss
    detected, but quality was frequently zeroed, optical-flow active fraction
    failed, distance sensor failed, and truth horizontal/height boundedness
    failed.

Interpretation: the strict default failsafe profile was a real confound in the
earlier rejected LK loss run, but this isolation test shows it was not the sole
cause. With delayed-observation failsafe and a 2 m/s velocity cap applied, the
Gate 6b LK flow-only configuration still did not bound the vehicle against
Gazebo truth. This run does **not** invalidate the later Phase 8M accepted
LK-vs-stock route comparison, because that comparison used the Phase 8K bounded
scenario contract and had separate accepted evidence.

## Interpretation

The original Gate 2 rejection was an analyzer/windowing bug, not proof that the
hover scene is bad. The world and downward camera are adequate at hover over
origin: the camera sees textured ground, feature counts saturate the detector,
and the rangefinder is finite and near 2.5 m.

Gate 3 proves the camera/LiDAR pose is sane for the small roll/pitch/yaw
attitudes expected before axis/fusion work. The static standalone proof also
keeps PX4 controller dynamics out of this check, so failures would have been
sensor/world geometry failures. No such failure was found.

Gate 4 proves the LK bridge translation axis contract on all four signed legs.
The main difference from the earlier flight failures is not camera orientation
or LiDAR orientation: those contracts are now proven.

Gate 5 proves that the bridge can preserve the NaN-gyro baseline and can also
feed finite same-window gyro integrals from Gazebo IMU without breaking the
run. This narrows the suspected gap with PX4 stock flow: the system no longer
lacks a gyro transport path. However, the yaw-target test is not a perfect
pure-rotation experiment. Position hold produced real station-keeping motion,
so the measured optical flow is a rotation/translation/controller mixture.
Gate 5 should be treated as a gyro-path sanity proof, not proof that gyro
compensation improves EKF optical-flow innovations.

Gate 6 shows the route itself is not sane under the moving GNSS-on flow-fusion
profile. Even with GNSS enabled, every delay candidate flew a looped physical
route rather than the intended short straight `+Y` leg. This blocks Gate 7 and
GNSS-loss smoke until the route/control/flow feedback issue is isolated.

## Known limitations

- Standalone fixed-pose roll/pitch/yaw proof is accepted. Dynamic
  roll/pitch and pure body-rate gyro proof remain blocked by missing
  attitude/body-rate setpoint support in the runner.
- Gate 5 yaw-target proof is accepted but not pure rotation-only; it includes
  position-hold lateral motion.
- Gate 6 delay sweep does not identify a safe delay. Route plots show a bigger
  control/route problem across all delay candidates. (Resolved by Gate 6b:
  the flow axis contract was rotated 90 deg from PX4's fusion convention;
  the sweep varied the wrong variable.)
- Gate 6b truth-side body rates were derived by differentiating the recorded
  Gazebo quaternion; their sign convention could not be independently
  validated against the ULog gyro (logged at 1 Hz). The translation
  conclusion is unaffected; the exact rotation-term signs rest on the
  physical-consistency argument plus the flight result.
- Gate 6b closed with one accepted run per config (no replicates yet).
  `EKF2_OF_N_MIN=0.5` de-weights flow; correct for GNSS-on flight, but its
  GNSS-loss holding performance is unproven. Residual `~9 deg` yaw offset,
  `1.87 m` along-track overshoot, and `~1.3x` rotation content over the
  100-deg FOV remain to be characterized (no longer dominant).
- The 2026-07-17 failsafe-isolation GNSS-loss rerun proves the delayed
  failsafe/cap change alone is insufficient for the Gate 6b LK config; it does
  not prove the Phase 8K bounded scenario contract or the stock PX4 flow
  sensor would fail under the same conditions.
- The effective GNSS-loss timer still landed at `10.0 s` after takeoff in the
  failsafe-isolation run despite a `20.0 s` request, so the takeoff/offboard
  timing reference remains a runner limitation.
- Texture coverage metrics are pragmatic image statistics, not a semantic
  classifier; the saved frames remain the final visual evidence.

## Files created or modified

- `scripts/analysis/sensor_contract_report.py`
- `scripts/analysis/diagnose_gate6_velocity_loop.py`
- `scripts/analysis/fit_flow_contract_from_truth.py`
- `scripts/worlds/prove_phase8l_attitude_pose.py`
- `scripts/runner/run_scenario_pxh_end_to_end.py`
- `scripts/runner/auto_takeoff_land_pxh_truth.py`
- `scripts/sim/flow_mavlink_bridge.py`
- `src/databoss_sim/flow/px4_adapter.py`
- `src/databoss_sim/models/x500_cam_lidar_down/model.config`
- `/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz/models/x500_cam_lidar_down/model.config`
- `experiments/configs/mvp/scenarios/phase8l_*`
- `experiments/configs/mvp/batches/phase8l_*`
- `experiments/configs/mvp/scenarios/phase8l_lk_gnssloss20_delayedcap_axisfix140_ofn05_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/batches/phase8l_lk_failsafe_isolation.yaml`
- `docs/phases/phase_08l_sensor_sanity_ladder.md`
- `docs/phases/README.md`
- `docs/PROJECT_LOG.md`

## Next phase

After all 8L gates pass: reopen Phase 8K/8M as a bounded LK GNSS-loss
replicate phase, with no additional tuning variables changed.
