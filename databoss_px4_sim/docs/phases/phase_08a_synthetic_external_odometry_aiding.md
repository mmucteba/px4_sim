# Phase 8A — Synthetic External Odometry Aiding

## Goal

Prove that PX4 can receive and use an external odometry / vision-aiding stream during GNSS loss.

This phase does **not** prove that real VIO, LiDAR SLAM, or optical flow works. It only proves the PX4 external-aiding pipe.

## Why this phase exists

Phase 0–7D already proved:

- PX4 SITL + Gazebo works.
- GNSS ON baseline works.
- GNSS loss using `SIM_GPS_USED=0` works.
- Default GNSS-loss failsafe causes protective/blind landing.
- Delayed failsafe allows drift observation.
- Gazebo ground truth can be recorded.
- PX4 ULog and Gazebo truth can be aligned.
- Automated scenario and batch runners work.
- Condition presets exist, but wind/sunset/texture are still metadata until Gazebo wiring is added.

Now we need to test the first GNSS-denied aiding source:

```text
Gazebo truth / synthetic local odometry
→ send to PX4 as external odometry or vision aiding
→ cut GNSS
→ check if PX4 EKF can reduce horizontal drift
→ compare against GNSS-loss no-aiding baseline
```

## Important honesty rule

Synthetic external odometry is **truth-fed aiding**.

It is useful for proving the PX4 fusion path, but it is not a real sensor solution.

Do not claim:

```text
VIO works
LiDAR works
optical flow works
real GNSS-denied navigation is solved
```

until real sensor pipelines are tested.

## Phase 8A questions

This phase must answer:

1. Which PX4 input path should we use first?
   - MAVLink `ODOMETRY`
   - MAVLink `VISION_POSITION_ESTIMATE`
   - MAVLink `ATT_POS_MOCAP`
   - other supported path

2. Which EKF2 parameters are required for external aiding?

3. Can PX4 logs prove that external odometry was received/fused?

4. Does EKF-vs-Gazebo-truth error improve after GNSS loss?

## Fixed first test conditions

Use the simplest already-proven setup:

```text
vehicle: gz_x500
world: default / databoss_mvp_yard_120m reference
route: hover_60s
altitude: 10m AGL
lighting: noon_clear
wind: none
texture: high_texture
GNSS loss: after takeoff
failsafe profile: delayed_observation
```

Environment condition files are allowed as labels, but this phase must not claim physical wind/light/texture effects.

## Planned cases

### Case A — GNSS ON baseline

Purpose:

```text
Confirm normal healthy baseline.
```

Expected:

```text
GNSS healthy
truth recorded
EKF-vs-truth horizontal error small
```

### Case B — GNSS LOSS no aiding

Purpose:

```text
Reproduce no-aiding GNSS-denied drift.
```

Expected:

```text
GNSS loss detected
no external odometry
truth recorded
horizontal error grows compared with GNSS ON
```

### Case C — GNSS LOSS + synthetic external odometry

Purpose:

```text
Test whether external odometry reduces GNSS-loss drift.
```

Expected:

```text
GNSS loss detected
synthetic odometry stream active
PX4 receives/fuses external aiding
truth recorded
horizontal error improves versus Case B
```

## Acceptance criteria

Phase 8A is accepted only if:

- A Phase 8A markdown file exists.
- A scenario config exists for synthetic external odometry.
- A batch config exists comparing GNSS ON, GNSS LOSS no aiding, and GNSS LOSS + synthetic external odometry.
- PX4 external odometry input path is inspected before coding.
- EKF2 external-aiding parameters are documented.
- ULog evidence shows whether external odometry was received/fused.
- Gazebo truth is recorded.
- EKF-vs-Gazebo-truth metrics are generated.
- The final report clearly says synthetic odometry is truth-fed aiding, not real VIO/LiDAR.

## Rejected shortcuts

Do not:

- Skip Gazebo truth comparison.
- Treat synthetic odometry as a real sensor.
- Claim wind/sunset/texture results before Gazebo world-condition wiring.
- Store experiment outputs inside PX4 source.
- Code the sender before inspecting PX4’s supported MAVLink/EKF path.

## Result

Inspection accepted. Sender implementation pending.

## Next phase

Phase 8B — Physical World Generation.

## Inspection result

Phase 8A external aiding path is confirmed as:

```text
Gazebo truth / synthetic local odometry
→ MAVLink ODOMETRY
→ PX4 mavlink_receiver
→ uORB vehicle_visual_odometry
→ EKF2 external vision fusion
```

## Receiver smoke result

Phase 8A-1 receiver smoke accepted.

Confirmed working MAVLink path:

```text
DATABOSS synthetic odometry sender
→ udpout:127.0.0.1:14600
→ PX4 MAVLink onboard instance on UDP 14600
→ MAVLink ODOMETRY msgid 331
→ vehicle_visual_odometry
```

## Phase 8A-3 — Live Gazebo-truth synthetic odometry

### Goal

Feed live Gazebo vehicle pose into PX4 as MAVLink ODOMETRY and prove that external position, velocity, and height aiding remain fused after GNSS loss.

### Data path

```text
Gazebo /world/default/dynamic_pose/info
→ DATABOSS live bridge
→ MAVLink ODOMETRY
→ vehicle_visual_odometry
→ EKF2 external vision fusion
```

### First implementation scope

- Local NED position.
- Local NED velocity.
- Valid position and velocity covariance.
- Continuous quality value.
- Approximately 30 Hz output.
- Initial Gazebo vehicle pose defines the local origin.

Initial conversion:

```text
ned_x = gazebo_x - origin_x
ned_y = gazebo_y - origin_y
ned_z = -(gazebo_z - origin_z)
```

Gazebo uses z-up while PX4 local NED uses z-down.

### Initial fusion configuration

```text
EKF2_EV_CTRL = 7
```

This enables horizontal position, vertical position, and 3D velocity.

External yaw fusion is disabled until the Gazebo-to-PX4 attitude conversion is validated.

### Acceptance criteria

1. Live Gazebo pose is sent continuously.
2. vehicle_visual_odometry changes when the vehicle moves.
3. pose_frame and velocity_frame are local NED.
4. EV position, velocity, and height report fused: True.
5. GNSS loss is detected.
6. External odometry continues after GNSS loss.
7. ULog and Gazebo truth are recorded.
8. EKF-versus-truth metrics are generated.
9. Results are compared with the no-aiding GNSS-loss case.

### Limitation

Gazebo ground truth is an ideal synthetic aiding source. It proves the PX4 integration path, not real VIO, LiDAR SLAM, or optical-flow performance.

Status: superseded by the 120 s A/B/C long-hover result below.

## Phase 8A-2 — EKF fusion smoke result

Status: Accepted.

PX4 received MAVLink ODOMETRY through vehicle_visual_odometry and EKF2 fused the external-vision aiding stream.

Confirmed receiver state:

```text
pose_frame: 1
velocity_frame: 1
quality: 100
```

Confirmed fusion evidence:

```text
estimator_aid_src_ev_pos: fused: True
estimator_aid_src_ev_vel: fused: True
estimator_aid_src_ev_hgt: fused: True
estimator_aid_src_ev_yaw: fused: True
innovation_rejected: False
```

## Phase 8A-4 — 120 s A/B/C long-hover result and QGC runaway

Date: 2026-07-08

Status: Accepted as failure evidence. Not accepted as solved station-keeping.

Batch:

```text
/opt/databoss_px4_sim/experiments/batches/
20260708_075652_phase8a_position_height_three_case_120s
```

Runs:

```text
A GNSS ON:
/opt/databoss_px4_sim/experiments/runs/
20260708_075655_phase8a_compare_2p5m_gnss_on_no_aiding_pxh_takeoff_land_truth

B GNSS loss, no aiding:
/opt/databoss_px4_sim/experiments/runs/
20260708_080114_phase8a_compare_2p5m_gnss_loss_no_aiding_pxh_takeoff_land_truth

C GNSS loss, external position + height:
/opt/databoss_px4_sim/experiments/runs/
20260708_080532_phase8a_compare_2p5m_gnss_loss_external_position_height_pxh_takeoff_land_truth
```

Run settings:

```text
altitude_agl_m: 2.5
hover_s: 120
gnss_loss_after_takeoff_s: 10
post_loss_hover_s: 110
comparison_window: until-land-command
Case C EKF2_EV_CTRL: 3
Case C EKF2_HGT_REF: 3
```

The comparison window stops at the `commander land` command. This keeps the long hover/observation while excluding the post-land-command tail from EKF-vs-truth metrics.

### Cropped EKF-vs-truth metrics

| Case | Horizontal mean m | Horizontal max m | Horizontal end m | Interpretation |
|---|---:|---:|---:|---|
| A GNSS ON | 0.033 | 0.112 | 0.022 | Healthy baseline |
| B GNSS loss, no aiding | 88.299 | 332.347 | 164.779 | Large GNSS-denied EKF/truth error |
| C GNSS loss, EV pos+hgt | 12.607 | 70.889 | 16.519 | Better EKF/truth agreement, but not stable station-keeping |

### QGC and ULog runaway evidence

QGC showed Case C walking kilometers away from the takeoff area. The logs agree with QGC; this was not only a display artifact.

Case C evidence:

```text
vehicle_visual_odometry rows: 5417
estimator_aid_src_ev_pos rows: 5425
estimator_aid_src_ev_hgt rows: 5425
EV height fused for essentially the whole run
EV horizontal position had many rejected samples during motion
EV velocity fusion was disabled; cs_ev_vel stayed false
xy_reset_counter increased from 4 to 167
Gazebo truth / QGC displacement by run end: about 9.0 km
```

Bridge log endpoint:

```text
x: -5156.99 m
y:  7425.20 m
horizontal distance: about 9040 m
```

Interpretation:

```text
The external-odometry bridge sent Gazebo truth and PX4 received it.
Height aiding behaved well.
Horizontal position aiding improved EKF-vs-truth consistency but did not prevent physical runaway.
The station-keeping metric, not EKF-vs-truth alone, is the main hover-success signal.
```

Latest accepted 120 s long-hover batch:

```text
experiments/batches/20260708_091923_phase8a_position_height_three_case_120s
```

Key results until `commander land`:

| Case | EKF/truth H mean m | EKF/truth H max m | Gazebo station drift end m | EV pos rejects | XY resets |
|---|---:|---:|---:|---:|---:|
| A GNSS ON | 0.031 | 0.097 | 0.033 | 0 | |
| B GNSS loss, no aiding | 101.926 | 414.744 | 412.705 | 0 | |
| C GNSS loss, EV pos+hgt | 9.242 | 57.602 | 3729.436 | 4223 | 161 |

### Current repair hypotheses

1. External-vision delay was wrong for this live bridge.

Evidence:

```text
ULog aid timestamp delay: about 0.158 s
previous EKF2_EV_DELAY: 0 ms
```

Applied next-test change:

```text
aiding.ekf2_ev_delay_ms: 160
```

2. EV velocity is still not fused.

Current accepted comparison uses:

```text
EKF2_EV_CTRL = 3
```

Meaning:

```text
fuse EV horizontal position
fuse EV vertical position/height
do not fuse EV velocity
do not fuse EV yaw
```

Case D attempted `EKF2_EV_CTRL=7` and hard-failed on 2026-07-08. It is now parked.

Case D evidence:

```text
Run: experiments/runs/20260708_094705_phase8a_compare_2p5m_gnss_loss_external_position_height_velocity_pxh_takeoff_land_truth
EV velocity fused: 820
EV velocity rejected: 414
first EV velocity rejection: t_rel 56.168 s
max bridge speed: 320.654 m/s
failure symptoms: gyro clipping, invalid setpoints, Gazebo ODE abort
```

3. Station-keeping needs its own metric.

EKF-vs-truth error answers:

```text
Does PX4 estimate match Gazebo truth?
```

Station-keeping drift answers:

```text
Did the aircraft remain near the commanded hover point?
```

Both must be reported.

4. AUTO_LOITER/global-setpoint behavior after GNSS loss may be inappropriate for local-only aiding.

The next ABC run tests the local-frame hold method before treating external odometry as failed:

```text
commander takeoff
then local/offboard hover hold
then GNSS loss
then compare station-keeping drift and EKF/truth error
```

### Repaired ABC Result

The repaired 60 s A/B/C comparison has passed:

```text
experiments/batches/20260708_165632_phase8a_abc_repaired_velocity_60s
```

Result, compared until `commander land`:

- Case A: GNSS on, no aiding, station drift end `0.064 m`.
- Case B: GNSS loss, no aiding, station drift end `100.817 m`.
- Case C: GNSS loss, external position+height+velocity aiding, station drift end `0.039 m`.
- Case C EV position/height/velocity rejected counts: `0 / 0 / 0`.
- Case C `xy_reset_counter_delta`: `2`.

The repair was not wider EV position gates. The fix was:

- Label live Gazebo odometry as MAVLink `LOCAL_ENU`, not `LOCAL_NED`, so PX4 converts ENU truth to NED internally.
- Use `EKF2_EV_CTRL=7` with validated finite-difference velocity.
- Use zero XY velocity plus Z position Offboard setpoints instead of fixed absolute XY position hold.
- Use realistic covariance and delay for this bridge: `position_std_m=0.10`, `velocity_std_m_s=1.00`, `EKF2_EV_DELAY=0`.

The repaired 120 s A/B/C comparison also passed:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s
```

Result, compared until `commander land`:

- Case A: GNSS on, no aiding, station drift end `0.016 m`.
- Case B: GNSS loss, no aiding, station drift end `319.281 m`.
- Case C: GNSS loss, external position+height+velocity aiding, station drift end `0.089 m`.
- Case C EV position/height/velocity rejected counts: `0 / 0 / 0`.
- Case C `xy_reset_counter_delta`: `1`.

Plot report:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s/phase8a_abc_repaired_plot_report.md
```

Plot artifacts:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s/plots/
```

Plot interpretation:

- Full-scale station-drift and XY plots show the no-aiding Case B running away.
- Zoomed plots show the healthy GNSS Case A and repaired aided Case C staying in the hover region.
- The Case C EV fusion plot shows active position, height, and velocity fusion with zero rejections.

Next required work:

- Keep this repaired C as the Phase 8A comparison candidate.
- Use the 120 s repaired ABC as the main proof run.
- Keep comparing station drift, EV rejection counts, and `xy_reset_counter_delta`.
- Do not enable EV yaw fusion until yaw/frame behavior is separately validated.
- Continue checking QGC/Gazebo drift from hover.

### Prepared Case C Stress Matrix

The next robustness batch is:

```text
experiments/configs/mvp/batches/phase8a_case_c_stress_matrix_120s.yaml
```

It includes:

- One GNSS-on/no-aiding reference.
- One GNSS-loss/no-aiding reference.
- One nominal repaired Case C reference.
- Case C odometry-rate stresses at `15 Hz`, `10 Hz`, and `5 Hz`.
- Case C reported-covariance stresses at `position_std_m=0.25, velocity_std_m_s=1.50` and `position_std_m=0.50, velocity_std_m_s=2.00`.
- Case C `EKF2_EV_DELAY` stresses at `100 ms` and `160 ms`.
- One combo stress: `10 Hz`, `position_std_m=0.25`, `velocity_std_m_s=1.50`, `EKF2_EV_DELAY=100 ms`.

This batch intentionally uses only currently wired stress knobs. It does not yet inject actual random measurement noise, transport latency, or odometry dropout.

Run command:

```bash
cd /opt/databoss_px4_sim
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py experiments/configs/mvp/batches/phase8a_case_c_stress_matrix_120s.yaml --continue-on-fail
```

After the batch completes, summarize:

```bash
cd /opt/databoss_px4_sim
venv/bin/python scripts/runner/summarize_batch_metrics.py --batch-dir <batch_dir>
```

Estimator health during the smoke:

```text
filter_fault_flags: 0
health_flags: 0
timeout_flags: 0
```

Conclusion:

```text
MAVLink ODOMETRY
→ vehicle_visual_odometry
→ EKF2 external vision fusion
```

This proves the PX4 external-aiding fusion pipe. It does not prove live Gazebo-truth aiding or real VIO, LiDAR SLAM, or optical-flow performance.

### Real-Error Case C Matrix Result

The real-error 120 s Case C matrix has passed:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s
```

Result, compared until `commander land`:

- Accepted cases: `11 / 11`.
- GNSS-on/no-aiding reference station drift end: `0.007 m`.
- GNSS-loss/no-aiding reference station drift end: `320.197 m`.
- Nominal repaired Case C station drift end: `0.113 m`.
- Case C real-error station drift range: `0.068 m` to `0.215 m`.
- Worst real-error Case C: `case_c_realerr_noise_strong_pos025_vel050`.
- Combo noise+latency+dropout Case C station drift end: `0.075 m`.

This matrix injects actual disturbances into the MAVLink odometry stream:

- Gaussian EV measurement noise up to `0.25 m` position and `0.50 m/s` velocity.
- Real delayed odometry replay with `100 ms` latency.
- Recurring odometry dropouts of `1 s / 10 s` and `2 s / 10 s`.
- One combined case with medium noise, `100 ms` latency, and `1 s / 10 s` dropout.

Important estimator notes:

- Strong noise remained accepted and held station, but produced EV position/height rejections: `18 / 128 / 0`.
- Dropout cases had zero EV rejections but produced XY reset deltas of `11` and `12`.
- The compensated `100 ms` latency case had lower station drift than the uncompensated latency case in this run: `0.068 m` versus `0.159 m`.

Report and plots:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/phase8a_case_c_real_error_report.md
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/real_error_summary.md
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/plots/
```

### Phase 8A Freeze Decision

Phase 8A is now frozen as the ideal external-aiding upper-bound reference.

Use it for:

- regression checks when the external-aiding pipeline changes
- the ideal upper-bound Case E in later GNSS-denied comparisons
- proving PX4 can fuse external position, height, and velocity correctly

Do not use it to claim performance for:

- camera optical flow
- TF03 rangefinder aiding
- VIO
- LiDAR odometry or LiDAR-SLAM

Next active phase:

```text
Phase 8B - Physical World Generation
```
