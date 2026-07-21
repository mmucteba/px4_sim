# DATABOSS PX4 Simulation Project

Goal:
Build a reproducible PX4 + Gazebo experiment system for comparing GNSS ON, GNSS OFF, and future aiding methods such as LiDAR, optical flow, VIO, and LiDAR-SLAM.

Final target:
A dashboard where the user can configure sensor, environment, drone, and EKF settings, run simulations, and compare drift/error results.

Current rule:
PX4 source stays in /opt/sim_px4/PX4-Autopilot.
All experiment code, configs, logs, summaries, and plots stay in this repository.

Current clean build path:
- Phase 0-7D: PX4/Gazebo automation, truth alignment, batches, reports, world/condition config structure
- Phase 8A: ideal truth-fed external-aiding upper-bound proof
- Phase 8B: physical Gazebo world generation from DATABOSS world YAML
- Phase 8C: downward monocular camera proof accepted in both generated worlds
- Phase 8D: TF03-style downward rangefinder proof
- Phase 8E: combined camera + TF03 vehicle
- Phase 8F: offline optical-flow validation
- Phase 8G: live optical-flow bridge into PX4
- Phase 8H: GNSS-on optical-flow fusion check
- Phase 8I: GNSS-denied camera + TF03 comparison
- Later: reports, dashboard contract, real logs, additional sensors



# DATABOSS — Complete Codex Handoff

**Handoff date:** 2026-07-08  
**Current phase:** Phase 8D — TF03-style downward rangefinder proof  
**Immediate workstream:** use the physically launchable, camera-proven generated rural worlds as the base for TF03 and optical-flow proof phases.

---

## 1. Executive summary

DATABOSS is a reproducible PX4 + Gazebo research/testbench for evaluating GNSS-denied multirotor navigation. The backend is intentionally YAML- and script-driven first; a dashboard will be added only after the experiment contract is stable.

The system currently automates PX4/Gazebo startup, QGroundControl streaming, takeoff, hover, timed GNSS loss, optional external odometry injection, landing, ULog collection, Gazebo ground-truth recording, PX4/truth alignment, metrics, and run validation.

The strongest short smoke result is a fully automated 2.5 m hover in which GNSS is removed 5 s after takeoff and truth-fed external horizontal/vertical position is fused using `EKF2_EV_CTRL=3`, with QGC streaming enabled. The accepted reference run is:

```text
/opt/databoss_px4_sim/experiments/runs/
20260707_121630_phase8a_hover_2p5m_gnss_loss_external_position_height_smoke_pxh_takeoff_land_truth
```

Accepted reference metrics:

```text
horizontal error mean = 0.042284 m
horizontal error max  = 0.176099 m
height abs error mean = 0.049825 m
3D error max          = 0.177967 m
```

This proves the PX4 external-aiding integration path. It does **not** prove real VIO, LiDAR odometry, LiDAR-SLAM, TF03, camera, or optical-flow performance because the aiding source is currently Gazebo truth.

Phase 8A is now frozen as the ideal upper-bound reference. Gazebo truth remains the evaluator for future phases, but it should not be used as the practical aiding source for camera/TF03/optical-flow experiments.

Phase 8B has moved from generated-and-valid to physically launchable and PX4-flight-compatible. The spawn proof run is:

```text
/opt/databoss_px4_sim/experiments/runs/
20260709_142806_phase8b_world_launch_proof
```

Both generated worlds launched in Gazebo headless mode as user `px4`, and the PX4 Gazebo `x500` model spawned successfully in each world.

The PX4 flight proof runs are:

```text
/opt/databoss_px4_sim/experiments/runs/
20260709_144318_phase8b_px4_flight_flat_rural_high_texture_noon_pxh_takeoff_land_truth

/opt/databoss_px4_sim/experiments/runs/
20260709_144526_phase8b_px4_flight_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Both generated worlds accepted a PX4 x500 takeoff, short hover, land, ULog copy, Gazebo truth postprocess, and EKF-vs-truth alignment.

Phase 8C has also accepted `gz_x500_mono_cam_down` in both generated worlds with delayed visual camera samples. The latest proof runs are:

```text
/opt/databoss_px4_sim/experiments/runs/
20260710_063125_phase8c_camera_flat_rural_high_texture_noon_pxh_takeoff_land_truth

/opt/databoss_px4_sim/experiments/runs/
20260710_063752_phase8c_camera_flat_rural_low_texture_noon_pxh_takeoff_land_truth
```

Both camera runs published the downward image topic, captured one image message after the airborne hover gate, rendered visible generated ground from that saved message, flew, landed, postprocessed truth, and passed EKF-vs-truth alignment. On this headless VM the accepted camera path uses `render_engine=ogre` with `xvfb-run`; the default `ogre2`/EGL path crashed during camera rendering.

The latest executed long-hover A/B/C comparison uses 2.5 m AGL, 120 s total hover, GNSS loss 10 s after takeoff, and 110 s post-loss observation:

```text
A: GNSS ON, no external aiding
B: GNSS loss, no external aiding
C: GNSS loss, external position + height aiding, EKF2_EV_CTRL=3
```

Accepted batch:

```text
/opt/databoss_px4_sim/experiments/batches/
20260708_075652_phase8a_position_height_three_case_120s
```

Key finding from QGC and ULog:

```text
Case C did receive and fuse external odometry, but the aircraft still ran away.
vehicle_visual_odometry rows: 5417
estimator_aid_src_ev_pos rows: 5425
estimator_aid_src_ev_hgt rows: 5425
EV height fusion: stable for the run
EV horizontal position: many samples rejected during motion
EV velocity fusion: disabled, cs_ev_vel stayed false
xy_reset_counter: 4 -> 167
QGC / Gazebo truth displacement by run end: about 9.0 km
```

Do not treat the current Case C as a solved external-aiding behavior. Treat it as the accepted failure evidence that revealed the next repair work.

---

## 2. Repository and runtime boundaries

### Main paths

```text
DATABOSS workspace: /opt/databoss_px4_sim
PX4 source:         /opt/sim_px4/PX4-Autopilot
```

Hard rule:

```text
PX4 source is the simulation/autopilot engine.
All DATABOSS configs, scripts, documentation, runs, comparisons, and reports stay under /opt/databoss_px4_sim.
Do not place experiment outputs in the PX4 source tree.
```

### Runtime user

Run PX4/Gazebo and DATABOSS commands as user `px4`, not root.

### Python environments

DATABOSS analysis and MAVLink sender scripts use:

```bash
cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
```

PX4 build/run must use clean system Python. Before invoking PX4 build tooling directly:

```bash
deactivate 2>/dev/null || true
unset VIRTUAL_ENV
unset PYTHONHOME
unset PYTHONPATH
export PATH="$(echo "$PATH" | tr ':' '\n' | grep -v '/opt/databoss_px4_sim/venv/bin' | paste -sd: -)"
hash -r
```

Expected PX4 Python:

```text
/usr/bin/python3
```

Previously encountered failure:

```text
Found Python3: /opt/databoss_px4_sim/venv/bin/python3
ModuleNotFoundError: No module named 'menuconfig'
```

### Permissions and stale processes

Ownership was previously repaired with:

```bash
sudo chown -R px4:px4 /opt/databoss_px4_sim
sudo chown -R px4:px4 /opt/sim_px4/PX4-Autopilot
sudo chmod -R u+rwX /opt/databoss_px4_sim
sudo chmod -R u+rwX /opt/sim_px4/PX4-Autopilot
```

Stale PX4/Gazebo cleanup when required:

```bash
sudo pkill -f "/opt/sim_px4/PX4-Autopilot/build/px4_sitl_default/bin/px4" 2>/dev/null || true
sudo pkill -f "px4_sitl_default" 2>/dev/null || true
sudo pkill -f "gz sim" 2>/dev/null || true
sudo rm -f /tmp/px4-sock-*
```

Do not run these destructively unless a stale process/socket is actually blocking a run.

---

## 3. Product goal and scope

### Research question

```text
For a CUAV V5+ / PX4-based multirotor, what happens when GNSS is lost,
and which aiding source best limits truth-referenced position error?
```

### Final system concept

A dashboard/testbench should allow configuration of:

- world and environment
- exact Earth origin and local frame
- vehicle/drone model
- sensor suite
- route and waypoints
- altitude AGL
- GNSS state and failure timing
- aiding source
- sensor rate, noise, bias, latency, dropout, blackout, and quality
- PX4/EKF/failsafe profile
- logging and analysis outputs

It then performs:

```text
YAML configuration
→ PX4/Gazebo startup
→ truth recorder startup
→ optional sensor/aiding bridge startup
→ automatic arm/takeoff/mission
→ timed GNSS loss
→ automatic landing/disarm
→ ULog and truth collection
→ time/frame alignment
→ EKF-vs-truth analysis
→ summary, validation, plots, and comparison report
```

### Explicitly not yet proven

- real CUAV V5+ flight behavior
- real VIO accuracy
- real LiDAR odometry or LiDAR-SLAM accuracy
- real optical-flow performance
- physical effects from the Phase 7D wind/lighting/texture labels
- QGC application-level connectivity, beyond starting the outbound MAVLink stream

---

## 4. Architecture

### 4.1 Configuration layer

The dashboard contract is represented by YAML files under:

```text
experiments/configs/mvp/
├── worlds/
├── routes/
├── scenarios/
├── batches/
└── conditions/
```

A scenario should describe at least:

```yaml
run:
  name: string
  description: string

vehicle:
  model: gz_x500
  px4_airframe: x500
  start_pose: {x_m: 0, y_m: 0, z_m: 0, yaw_deg: 0}

world:
  name: string
  lighting: string
  wind: {...}

route:
  name: string
  type: string
  altitude_agl_m: number
  duration_s: number
  waypoints: []

gnss:
  start_enabled: bool
  loss_enabled: bool
  loss_time_s: number|null
  restore_after_run: bool

aiding:
  mode: string
  enabled: bool
  external_odom_enabled: bool
  input_path: string
  external_odometry: {...}

logging:
  record_ulog: true
  record_gazebo_truth: true

analysis:
  align_with_gazebo_truth: true
  generate_plots: true
  generate_summary: true
```

Unknown YAML fields should not be relied on unless runner code explicitly reads them. Verify the actual parser before assuming a field has behavior.

### 4.2 Runner/orchestration layer

Important scripts:

```text
scripts/runner/run_scenario_pxh_end_to_end.py
scripts/runner/auto_takeoff_land_pxh_truth.py
scripts/runner/run_batch_matrix_pxh.py
scripts/runner/send_synthetic_external_odometry_mavlink.py
scripts/runner/send_live_gazebo_odometry_mavlink.py
```

Current automated runner responsibilities:

- read scenario YAML
- read route altitude and aiding settings
- set `MIS_TAKEOFF_ALT`
- start PX4/Gazebo
- start Gazebo truth recorder
- start MAVLink receiver on UDP 14600 for external odometry
- set EKF2 external-vision parameters
- start live Gazebo-to-MAVLink odometry bridge when enabled
- verify the bridge process remains alive
- arm and take off automatically
- cut GNSS at configured time
- hover for configured observation period
- land and disarm automatically
- stop bridge/recorders
- copy ULog
- save external odometry CSV and console logs
- postprocess and align truth
- write status/validation fields
- require more than 10 odometry rows for external-odom acceptance

### 4.3 External odometry bridge

Current data flow:

```text
Gazebo perfect pose
→ subtract initial Gazebo pose to define local origin
→ convert Gazebo Z-up into PX4 NED Z-down
→ derive velocity from position differences
→ MAVLink ODOMETRY (msgid 331)
→ PX4 mavlink_receiver
→ uORB vehicle_visual_odometry
→ EKF2 external-vision fusion
```

Current MAVLink properties:

```text
frame_id       = MAV_FRAME_LOCAL_NED
child_frame_id = MAV_FRAME_LOCAL_NED
estimator_type = MAV_ESTIMATOR_TYPE_VISION
quality        = 100
sysid          = 42
compid         = 197
requested rate = 30 Hz
observed rate  ≈ 25 Hz
```

Current covariance assumptions:

```text
position std      = 0.02 m
position variance = 0.0004
velocity std      = 0.05 m/s
velocity variance = 0.0025
orientation std   ≈ 30 deg
```

Current orientation payload:

```text
q = [1, 0, 0, 0]
angular velocity = [0, 0, 0]
```

Orientation is fake and must not be fused yet.

### 4.4 Analysis layer

Important scripts:

```text
scripts/analysis/extract_ulog.py
scripts/analysis/plot_run_basic.py
scripts/analysis/compare_runs_basic.py
scripts/analysis/parse_gz_pose_text.py
scripts/analysis/report_phase7d_from_metrics_md.py
```

ULog extraction has supported:

```text
vehicle_local_position
vehicle_gps_position
vehicle_attitude
sensor_accel
sensor_gyro
vehicle_imu
sensor_baro
vehicle_magnetometer
estimator_status
estimator_innovations
```

Run analysis should prefer generated truth-aligned products:

```text
ekf_vs_ground_truth_aligned.csv
ekf_vs_ground_truth_metrics.json
ekf_vs_ground_truth_metrics.md
```

Gazebo truth is the acceptance judge. EKF movement alone is not physical drift/error.

---

## 5. Frame and time contract

### 5.1 Current truth source

```text
Gazebo topic: /world/default/dynamic_pose/info
Vehicle model: x500_0
```

### 5.2 Frame conversion

Gazebo uses Z-up. PX4 local position uses NED with Z-down.

Current conversion:

```text
PX4 x = Gazebo x - initial Gazebo x
PX4 y = Gazebo y - initial Gazebo y
PX4 z = -(Gazebo z - initial Gazebo z)
```

Height comparison:

```text
PX4 height_up = -vehicle_local_position.z
Gazebo height = Gazebo z relative to initial pose
```

### 5.3 Architectural requirement

A future Frame + Time + Transform Manager is mandatory. Every aiding measurement must include:

1. source frame
2. target/navigation frame
3. timestamp and clock domain
4. transform provenance
5. covariance/quality

MVP navigation frame:

```text
px4_local_ned
```

Do not assume two values called “NED” share the same origin or yaw. This is the “Turkey vs Mars” failure mode: identical axis convention, incompatible origin/heading.

For the MVP, GNSS is initially valid, the local external-aiding frame is aligned to PX4 local NED, and GNSS is then removed.

---

## 6. Ground station and GNSS controls

### QGroundControl/Tailscale

Mac/QGC Tailscale IP:

```text
100.109.200.5
```

Working PX4 command:

```text
mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x
```

Current automation proves the outbound stream was started via:

```text
qgc_mavlink_started=True
```

It does not yet prove the QGC application replied. Add `mavlink status` parsing for RX traffic/GCS heartbeat and record a separate `qgc_connected` field.

### GNSS failure injection

Accepted simulated GNSS cut:

```text
param set SIM_GPS_USED 0
```

Restore:

```text
param set SIM_GPS_USED 10
```

Rejected method:

```text
param set SYS_FAILURE_EN 1
failure gps off
```

Reason: GPS remained healthy in this PX4/Gazebo setup.

---

## 7. Run-folder and evidence contract

Expected run folder shape:

```text
experiments/runs/<run_id>/
├── README.md
├── config.yaml
├── commands.log
├── environment.txt
├── logs/flight.ulg
├── extracted_csv/
├── plots/
├── summary.json
├── summary.md
├── validation.json or validation.md
├── ekf_vs_ground_truth_aligned.csv
├── ekf_vs_ground_truth_metrics.json
└── ekf_vs_ground_truth_metrics.md
```

A run is not accepted merely because the process exits successfully. Acceptance should include:

- automatic arm/takeoff succeeded
- configured GNSS transition occurred when requested
- optional aiding stream started and had sufficient samples
- external aiding was received and, where required, fused
- Gazebo truth was recorded
- ULog was copied
- alignment produced valid rows and sensible duration
- landing and disarm completed
- estimator/fusion flags meet the scenario gate
- metrics are finite and physically plausible

Protect accepted reference run directories. New comparisons must create new run folders and never overwrite accepted evidence.

---

## 8. Major decisions frozen so far

### Vehicle and world

```text
Current simulation vehicle: gz_x500
MVP world concept: databoss_mvp_yard_120m
```

MVP world concept:

- 120 m × 120 m
- flat high-texture ground
- one ~80 m tower/building
- several 10–30 m block buildings
- open flat area
- noon-clear baseline
- wind off baseline

Phase 7D condition presets exist for:

```text
lighting: noon_clear, sunset_low_angle
wind: none, crosswind_5ms
texture: high_texture, low_texture
```

Critical honesty rule: these are currently metadata/reference labels, not confirmed physical Gazebo changes.

### Routes and heights

Routes:

```text
hover_60s
straight_50m_out_and_back
square_50m
tower_inspection_visual
```

Planned height envelope:

```text
3 m, 10 m, 30 m, 60 m AGL
```

### Aiding roadmap

```text
A0 GNSS ON baseline
A1 GNSS OFF, no aiding
A2 ideal truth-fed external odometry upper bound
A3 downward camera + TF03 optical flow
A4 GNSS-denied camera + TF03 comparison
Later: VIO, LiDAR odometry/SLAM, UWB, landmarks, dashboard
```

### EKF policy

Current accepted ideal external-aiding configuration:

```text
EKF2_EV_CTRL = 7
EKF2_HGT_REF = 3
```

Meaning:

```text
fuse external horizontal position
fuse external vertical position/height
fuse external velocity
do not fuse external yaw
```

Accepted Phase 8A repair details:

```text
mav_frame = local_enu
velocity_source = finite_difference
control_mode = offboard_local_position_hold
local_hold_setpoint_mode = velocity_xy_position_z
```

Do not enable EV yaw until real Gazebo attitude has been correctly transformed into PX4 NED/body conventions and validated.

For Phase 8G and later optical-flow work, truth-fed EV must be disabled:

```text
EKF2_EV_CTRL = 0
EKF2_OF_CTRL = 1
```

---

## 9. Completed phases and confirmed progress

### Phase 0 — PX4/Gazebo environment proof

Confirmed PX4 SITL + Gazebo headless operation, X500 arm/takeoff/land/disarm, ULog creation, and valid EKF local position.

### Phase 1 — GNSS ON baseline

Accepted baseline run:

```text
/opt/databoss_px4_sim/experiments/runs/
20260702_133535_phase01_gnss_on_x500_hover60_alt2p5
```

Key metrics:

```text
duration ≈ 95.696 s
GPS fix_type = 3
satellites used = 10
max horizontal EKF movement ≈ 0.056 m
mean horizontal EKF movement ≈ 0.024 m
max altitude ≈ 2.515 m
```

### Phase 2 — extraction and plots

ULog extraction and basic plots accepted.

### Phase 3 — GNSS loss and failsafe behavior

Accepted GNSS-loss mechanism: `SIM_GPS_USED=0`.

Default failsafe test produced protective/blind landing. Delayed-observation failsafe allowed long drift observation. Earlier EKF-only drift values became very large, reinforcing the requirement that truth alignment—not EKF motion alone—must judge physical error.

### Phase 3C/3D — QGC over Tailscale

QGC stream path accepted as viewer/monitor. QGC is not the automation engine.

### Phase 4 — Gazebo truth and alignment

Truth recording and PX4/Gazebo alignment accepted.

### Phase 5 — MVP structure

Repository structure, phase docs, backend contract, and scenario placeholders accepted.

### Phase 6 — MVP world and route configs

World and route YAML structure accepted.

### Phase 7A — automated end-to-end runner

One-command automated takeoff/hover/GNSS-loss/landing/logging pipeline accepted.

### Phase 7B — batch matrix

Rerunnable GNSS scenario batch accepted.

### Phase 7C — comparisons

Comparison metrics and plots accepted.

### Phase 7D — environment condition matrix

A 16-case reference matrix executed and was accepted for orchestration/coverage. It does not prove wind/lighting/texture physics because those conditions are not yet physically wired.

A previous report issue occurred because scripts read the wrong metric source or used the wrong filename. Correct report script:

```text
scripts/analysis/report_phase7d_from_metrics_md.py
```

Correct metric source per run:

```text
<run_dir>/ekf_vs_ground_truth_metrics.md
```

### Phase 8A — external odometry receive/fusion path

Confirmed:

```text
MAVLink ODOMETRY msgid 331
→ PX4 vehicle_visual_odometry
→ EKF2 external-vision position/height fusion
```

Receiver details:

```text
PX4 UDP local port 14600
remote port 14601
mode Onboard
pose_frame = 1 (NED)
velocity_frame = 1 (NED)
quality = 100
```

Static fusion evidence:

```text
estimator_aid_src_ev_pos: fused=True, innovation_rejected=False
estimator_aid_src_ev_hgt: fused=True, innovation_rejected=False
estimator_aid_src_ev_vel: fused=True in stationary proof
filter_fault_flags=0
health_flags=0
timeout_flags=0
```

Dynamic velocity fusion is not accepted.

Accepted automated GNSS-on external-position/height smoke:

```text
run: 20260707_112655_phase8a_hover_2p5m_gnss_on_external_position_height_smoke_pxh_takeoff_land_truth
horizontal mean = 0.046552 m
horizontal max  = 0.146009 m
height abs mean = 0.010701 m
3D max          = 0.146032 m
external odom rows = 2056
```

Accepted automated GNSS-loss headless smoke:

```text
run: 20260707_120958_phase8a_hover_2p5m_gnss_loss_external_position_height_smoke_pxh_takeoff_land_truth
horizontal mean = 0.118901 m
horizontal max  = 0.756133 m
height abs mean = 0.028080 m
3D max          = 0.756137 m
```

Accepted QGC-enabled GNSS-loss reference:

```text
run: 20260707_121630_phase8a_hover_2p5m_gnss_loss_external_position_height_smoke_pxh_takeoff_land_truth
GNSS loss after takeoff = 5 s
post-loss hover = 12 s
EKF2_EV_CTRL = 3
horizontal mean = 0.042284 m
horizontal max  = 0.176099 m
height abs mean = 0.049825 m
3D max          = 0.177967 m
```

---

## 10. Latest Phase 8A long-hover status

Latest robustness result:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s
```

This 120 s real-error matrix accepted `11 / 11` cases. The GNSS-loss/no-aiding
reference drifted `320.197 m`, while all real-error Case C variants stayed
between `0.068 m` and `0.215 m` station drift. The matrix injected actual
measurement noise, real `100 ms` odometry latency, recurring odometry dropouts,
and one combined noise+latency+dropout case.

Report:

```text
experiments/batches/20260709_104321_phase8a_case_c_real_error_matrix_120s/phase8a_case_c_real_error_report.md
```

The long-hover three-case comparison is now implemented and executed. The batch YAML path is still:

```text
experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml
```

but its internal batch name and settings are now:

```text
phase8a_position_height_three_case_120s
hover_s: 120
gnss_loss_after_takeoff_s: 10
post_loss_hover_s: 110
```

Latest accepted batch:

```text
/opt/databoss_px4_sim/experiments/batches/
20260708_075652_phase8a_position_height_three_case_120s
```

Latest case runs:

```text
A: 20260708_075655_phase8a_compare_2p5m_gnss_on_no_aiding_pxh_takeoff_land_truth
B: 20260708_080114_phase8a_compare_2p5m_gnss_loss_no_aiding_pxh_takeoff_land_truth
C: 20260708_080532_phase8a_compare_2p5m_gnss_loss_external_position_height_pxh_takeoff_land_truth
```

The EKF-vs-truth comparison window now stops at the `commander land` vehicle command. This keeps the hover/observation long while excluding the post-land-command tail from EKF/truth metrics.

Latest cropped EKF-vs-truth metrics:

| Case | H mean m | H max m | H end m | Notes |
|---|---:|---:|---:|---|
| A GNSS ON | 0.033 | 0.112 | 0.022 | Baseline stable |
| B GNSS loss, no aiding | 88.299 | 332.347 | 164.779 | Large GNSS-denied error |
| C GNSS loss, EV pos+hgt | 12.607 | 70.889 | 16.519 | Better EKF/truth agreement, but not station-keeping |

Important distinction:

```text
EKF-vs-truth error is not the same as station-keeping drift.
Case C tracked Gazebo truth better than Case B, but Gazebo truth and QGC both show the aircraft physically walking away.
```

Case C QGC/ULog runaway evidence:

```text
vehicle_visual_odometry rows: 5417
estimator_aid_src_ev_pos rows: 5425
estimator_aid_src_ev_hgt rows: 5425
EV height fusion: fused for essentially the whole run
EV position aid: many samples rejected during motion
EV velocity fusion: disabled by EKF2_EV_CTRL=3
xy_reset_counter: 4 -> 167
Gazebo truth / QGC displacement by end: about 9.0 km
```

Immediate repair note:

```text
Measured aid delay is about 0.158 s.
The runner previously forced EKF2_EV_DELAY=0.
Case C now has aiding.ekf2_ev_delay_ms: 160.
```

Next test should rerun the same 120 s batch and compare EV position rejection count, XY reset count, QGC displacement, and station-keeping drift before changing velocity or yaw fusion.

---

## 11. Functional requirements

### Experiment execution

- A scenario must be runnable from one command.
- Arm, takeoff, GNSS cut, hover/route execution, landing, and disarm should be automatic.
- Batch execution must isolate runs and preserve logs even when one case fails.
- Accepted references must never be overwritten.

### Ground truth

- Gazebo truth must be recorded for every accepted scenario.
- PX4 and truth must be time-aligned.
- Metrics must be finite, physically plausible, and based on truth-referenced error.

### Aiding

- External odometry must use explicit frames, timestamps, covariance, estimator type, and quality.
- Clean truth and synthetic sensor output must remain separate.
- The system must record both what was sent and what PX4 fused/rejected.

### QGC

- QGC remains a monitor/viewer.
- The runner must not depend on manual QGC actions.
- Add a distinction between outbound stream start and verified GCS connection.

### Documentation

Every accepted phase/change should update:

```text
docs/phases/<phase>.md
docs/phases/README.md
docs/PROJECT_LOG.md
```

Planned status must be replaced with actual results after execution.

---

## 12. Non-functional requirements and guardrails

- Reproducible and rerunnable.
- No manual flight steps when automation can perform them.
- No experiment outputs in PX4 source.
- No root-owned run artifacts.
- No claims beyond evidence.
- No real-sensor conclusions from truth-fed synthetic aiding.
- No external velocity fusion until dynamic acceptance gates pass.
- No yaw fusion until frame conversion is validated.
- Environment condition labels must not be described as physical effects until wired into Gazebo.
- Prefer small, testable patches with syntax checks and explicit acceptance evidence.

---

## 13. Unresolved technical issues

### 13.1 Three-case comparison confirmed; station-keeping not solved

The 120 s A/B/C comparison is accepted as an executed batch, but Case C is not yet a solved aiding behavior.

Latest accepted evidence:

```text
experiments/batches/20260708_091923_phase8a_position_height_three_case_120s
```

- Case A stayed stable: Gazebo station drift end `0.033 m`.
- Case B physically drifted after GNSS loss: Gazebo station drift end `412.705 m`.
- Case C improved EKF-vs-truth agreement versus Case B: horizontal mean `9.242 m`, max `57.602 m`.
- Case C did not solve station-keeping: Gazebo station drift end `3729.436 m`.
- Case C horizontal aiding remained unhealthy: EV position rejects `4223`, XY reset counter delta `161`.
- Case C height aiding remained healthy: EV height rejects `0`.
- Case C did not fuse EV velocity: `cs_ev_vel` stayed false.

Repair/reporting need:

- Keep comparing only until `commander land`.
- Keep the long hover because the failure appears only after longer post-loss observation.
- Treat station-keeping drift as the primary pass/fail signal, separate from EKF-vs-truth agreement.
- Park EV velocity fusion until the bridge velocity source is repaired and validated.

Implemented diagnostic support:

- Alignment metrics now include station-keeping displacement.
- Run status records EV aid rejection counts and XY reset counter delta.
- Run status now records EV velocity active/fused/rejected counts when `EKF2_EV_CTRL` requests velocity.
- Batch summaries include EV delay/control, EV velocity activity, EV position/velocity rejection counts, XY reset delta, and truth drift end distance.
- Case D, `EKF2_EV_CTRL=7`, was attempted and is now parked after a hard failure.
- ABC Case C now uses `control.mode: offboard_local_position_hold`, streams `SET_POSITION_TARGET_LOCAL_NED` before switching to Offboard, then disables GNSS for the long hover.
- The live Gazebo odometry bridge now reports zero velocity by default. Finite-difference velocity must be explicitly requested and is sanity-capped.
- The 20260708_113455 ABC run showed Case C did enter Offboard, but horizontal EV position rejected after GNSS loss. The follow-up `EKF2_EVP_GATE=10` / `position_std_m=1.0` run failed fast with attitude/accelerometer failure and Gazebo ODE abort, so that tuning was rolled back.

### 13.2 External velocity rejection

Current ABC velocity policy is conservative: Case C sends zero velocity in ODOMETRY and does not fuse EV velocity because `EKF2_EV_CTRL=3`. The old finite-difference velocity path is no longer the default.

Investigate:

- timestamp delay and clock domain; latest measured EV aid delay is about 0.158 s
- Gazebo/world-to-PX4-local frame convention; GNSS-on baseline suggests current raw x/y alignment matches PX4 local for this SITL setup, but this still needs a deliberate transform test
- differentiation noise
- smoothing/filter lag
- covariance realism
- frame_id vs child_frame_id semantics
- body vs local velocity convention
- whether AUTO_LOITER/global-setpoint behavior is appropriate for local-only aiding after GNSS loss

Preferred implementation:

```text
Use Gazebo native linear velocity rather than differentiating position.
```

### 13.3 Repaired ABC proof

The repaired 60 s A/B/C comparison passed:

```text
experiments/batches/20260708_165632_phase8a_abc_repaired_velocity_60s
```

Result, compared until `commander land`:

- Case A, GNSS on/no aiding: station drift end `0.064 m`.
- Case B, GNSS loss/no aiding: station drift end `100.817 m`.
- Case C repaired, GNSS loss/external position+height+velocity: station drift end `0.039 m`.
- Repaired Case C used `mav_frame: local_enu`, `EKF2_EV_CTRL=7`, finite-difference velocity, `velocity_reject_action: hold_last`, `position_std_m=0.10`, `velocity_std_m_s=1.00`, and `EKF2_EV_DELAY=0`.
- Repaired Case C had EV position/height/velocity rejected counts all `0`, with `xy_reset_counter_delta=2`.

Root cause:

- Gazebo live odometry was ENU-like, but the bridge had labeled it as MAVLink `LOCAL_NED`.
- Hover smoke tests hid the frame bug because there was little horizontal motion.
- After GNSS loss, the wrong frame label caused horizontal EV innovations to reject and the vehicle drifted.
- Fixed absolute Offboard XY position setpoints also made EKF reset behavior brittle; repaired Case C uses zero XY velocity plus Z position hold.

Next proof step:

- Keep this repaired C as the comparison candidate.
- Run a longer 120 s repaired ABC if we need the same long hover duration as the older failed batch.
- Keep station drift, EV rejection counts, and XY reset delta as the primary pass/fail signals.

The repaired 120 s A/B/C comparison also passed:

```text
experiments/batches/20260708_190902_phase8a_abc_repaired_velocity_120s
```

Result, compared until `commander land`:

- Case A, GNSS on/no aiding: station drift end `0.016 m`.
- Case B, GNSS loss/no aiding: station drift end `319.281 m`.
- Case C repaired, GNSS loss/external position+height+velocity: station drift end `0.089 m`.
- Repaired Case C EV position/height/velocity rejected counts: `0 / 0 / 0`.
- Repaired Case C `xy_reset_counter_delta=1`.

Acceptance gate:

```text
estimator_aid_src_ev_vel.fused = True
estimator_aid_src_ev_vel.innovation_rejected = False
test ratio < 1 during actual motion
```

Case D hard-failed on 2026-07-08 and is parked. It fused EV velocity for a while, then produced large velocity innovations, nonphysical finite-difference velocities, gyro clipping, invalid setpoints, and a Gazebo physics abort.

Latest Case D evidence:

```text
Run: experiments/runs/20260708_094705_phase8a_compare_2p5m_gnss_loss_external_position_height_velocity_pxh_takeoff_land_truth
EV velocity fused: 820
EV velocity rejected: 414
first EV velocity rejection: t_rel 56.168 s
max bridge velocity: 320.654 m/s
```

Next velocity work is implementation repair, not another long-hover rerun:

```text
Use native Gazebo velocity or another validated velocity source.
Add velocity sanity caps before feeding EKF2.
Validate in a short controlled run before GNSS-loss hover testing.
```

### 13.3 Attitude/yaw missing

Identity quaternion is a placeholder. Implement and test correct Gazebo quaternion → PX4 NED/body conversion. Validate at known yaw/roll/pitch poses. Only then consider `EKF2_EV_CTRL=15`.

### 13.4 QGC connection verification

Add parsing of `mavlink status` after startup and record:

```text
qgc_mavlink_started
qgc_rx_bytes_or_rate
qgc_gcs_heartbeat_seen
qgc_connected
```

### 13.5 Synthetic sensor realism

The bridge is an ideal upper bound. Add YAML-controlled:

```text
rate
white noise
bias
bias random walk
drift
latency
jitter
dropout
blackout
quality degradation
frame reset/relocalization jumps
covariance policy
```

Clean Gazebo truth remains untouched for judging error.

### 13.6 Physical environment wiring

Wind, lighting, and texture presets currently do not reliably modify Gazebo. Implement and prove each with world/SDF inspection and runtime evidence before using them for performance conclusions.

### 13.7 Frame/time manager

The current bridge uses a simple initial-origin conversion. General routes, real-data replay, multiple sensors, map frames, and Earth coordinates need a formal transform/time subsystem.

---

## 14. Prioritized next tasks for Codex

### Task 0 — inspect before modifying

From `/opt/databoss_px4_sim`:

```bash
pwd
git status --short 2>/dev/null || true
find experiments/configs/mvp/scenarios -maxdepth 1 -type f -name 'phase8a_compare_2p5m_*' -print
sed -n '1,80p' experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml
sed -n '35,55p' experiments/configs/mvp/scenarios/phase8a_compare_2p5m_gnss_loss_external_position_height.yaml
```

Also inspect the accepted reference folder and do not alter it.

### Task 1 — validate the current 120 s comparison files

- Validate all three YAML files with `yaml.safe_load`.
- Confirm Case A has no external aiding and no GNSS loss.
- Confirm Case B has GNSS loss at 10 s, no aiding, delayed-observation failsafe, and 110 s post-loss observation.
- Confirm Case C has GNSS loss at 10 s, external position+height only, `ekf2_ev_delay_ms: 160`, and `control.mode: offboard_local_position_hold`.
- Ensure runner flags and YAML timing do not conflict.
- Keep the comparison window at `until-land-command`.
- Run:

```bash
env PYTHONPYCACHEPREFIX=/tmp/databoss_pycache python3 -m py_compile \
  scripts/runner/run_scenario_pxh_end_to_end.py \
  scripts/runner/auto_takeoff_land_pxh_truth.py \
  scripts/runner/send_live_gazebo_odometry_mavlink.py \
  scripts/runner/send_offboard_local_position_setpoint_mavlink.py \
  scripts/runner/run_batch_matrix_pxh.py \
  scripts/runner/summarize_batch_metrics.py
```

Dry-run command:

```bash
venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml \
  --dry-run --continue-on-fail
```

### Task 2 — repair velocity observability before another long Case C

The latest A/B references are already available in:

```text
experiments/batches/20260708_113455_phase8a_position_height_three_case_120s
```

Do not keep widening EV position gates for the long Case C run. Offboard is now proven; the missing piece is stable horizontal velocity observability after GNSS loss.

Do not treat zero exit code alone as acceptance. Collect each run folder and inspect:

```text
batch_summary.json/md
logs/pxh_takeoff_land_truth_status.json
validation.md
ekf_vs_ground_truth_metrics.json/md
external odometry logs for Case C
offboard local hold logs for Case C
ULog fusion evidence
QGC/Gazebo drift from the hover point
xy_reset_counter
```

### Task 3 — create a Phase 8A comparison report

Output under:

```text
experiments/comparisons/phase8a_position_height_three_case_extended/
```

Recommended outputs:

```text
comparison_metrics.csv
comparison_metrics.json
comparison_report.md
plots/horizontal_error_timeseries.png
plots/height_error_timeseries.png
plots/max_horizontal_error.png
plots/mean_horizontal_error.png
plots/end_horizontal_error.png
plots/distance_travelled.png
```

Compare at minimum:

- mean/max/p95/end horizontal truth error
- mean/max height error
- 3D error
- post-loss error growth
- estimated and truth distance travelled
- EKF local-position validity
- GNSS-loss detection
- failsafe/landing behavior
- EV position/height fused/rejected status
- QGC stream/connection evidence

Use the same post-loss window for Cases B and C. Case A should be reported as a baseline, not forced into a fake post-loss metric.

### Task 4 — update documentation with actual results

Replace “planned/pending” sections with run IDs, acceptance status, metrics, anomalies, and scientific interpretation. Preserve the old accepted reference in the history.

### Task 5 — strengthen QGC verification

Implement application-level connection evidence separately from stream startup.

### Task 6 — repair velocity fusion

Use native Gazebo velocity, explicitly convert frames, validate timestamps/covariance, and run a controlled motion scenario before enabling velocity in the main comparison.

### Task 7 — add real attitude conversion

Implement quaternion conversion tests and known-pose validation before any yaw fusion.

### Task 8 — synthetic sensor model

Introduce a clean, configurable impairment layer between truth and MAVLink output.

### Task 9 — later phases

After the external odometry interface is robust:

1. physically wire wind, lighting, and texture into Gazebo
2. synthetic optical flow + rangefinder
3. image-based optical flow + rangefinder
4. VIO
5. LiDAR odometry/SLAM
6. dashboard UI backed by the stable YAML/runner contract

---

## 15. Suggested immediate acceptance criteria

The extended three-case comparison is accepted only if:

```text
All three runs create unique run folders.
Case A: GNSS remains healthy; no external odometry process runs.
Case B: GNSS loss is detected; no external odometry process runs.
Case C: GNSS loss is detected; external odometry is sent, received, and position/height are fused.
All cases record Gazebo truth and copy a ULog.
All cases align successfully with finite, plausible metrics.
Automatic takeoff and landing succeed.
No accepted reference is overwritten.
Case C materially outperforms Case B in truth-referenced post-loss horizontal/height error.
Station-keeping drift from the hover point is reported separately from EKF-vs-truth error.
QGC/Gazebo runaway is explicitly called out even if EKF-vs-truth error is improved.
Documentation records actual results and limitations.
```

No fixed numeric “better” threshold has been frozen for Case C vs Case B. The report should show the effect and propose a threshold only after reviewing the distributions, alignment quality, EV rejection/reset counts, and station-keeping drift.

---

## 16. Hardware context for later real-system mapping

The documentation set describes a CUAV V5+ architecture with onboard IMU, magnetometer, barometer, and NEO 3 GNSS components. The current simulator is `gz_x500`, not a validated physical CUAV V5+ digital twin.

Long-term system modes:

```text
1. Theoretical CUAV model derived from datasheets/manuals
2. Calibrated CUAV model derived from real flight logs
```

Simulation sensor settings must eventually be traceable to documented sensor characteristics and then calibrated against real ULogs. Until then, label results as PX4/Gazebo capability tests.

---

## 17. Codex working style

- Begin by reading existing files and current `git diff`; do not recreate blindly.
- Keep patches narrow and reversible.
- Preserve accepted evidence.
- Run syntax/compile checks after every patch.
- For long simulations, print the exact command and expected outputs.
- On failure, preserve logs and diagnose before rerunning.
- Update Markdown phase files and project log as part of the same change.
- Clearly separate confirmed facts, inferred behavior, and planned work.

---

## 18. One-sentence continuation point

**Continue by repairing velocity observability before another long Case C: A/B from `20260708_113455` are valid references, Offboard is proven, position+height-only still diverges, and Case D / `EKF2_EV_CTRL=7` stays parked until velocity is validated.**
