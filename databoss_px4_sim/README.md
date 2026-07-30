# DATABOSS PX4 Simulation Project

DATABOSS is a reproducible PX4 + Gazebo Harmonic experiment system for
GNSS-denied multirotor navigation. It provides YAML scenarios, automated
PX4/Gazebo runners, truth-aligned analysis, deployment checks, and a FastAPI
dashboard for browsing runs, comparisons, scenarios, jobs, system health, and
vehicle composition.

The repository now contains the DATABOSS-side source of truth. PX4 itself is
still an external checkout at `/opt/sim_px4/PX4-Autopilot`; DATABOSS deploys
pinned patches, models, airframes, and generated vehicle assets into that tree.

## Quick start

Clone the repo and bootstrap a target host:

```bash
git clone git@github.com:mmucteba/px4_sim.git databoss_px4_sim
cd databoss_px4_sim
sudo scripts/deploy/bootstrap.sh --yes
```

The bootstrap script provisions system packages, PX4, the two DATABOSS virtual
environments, terrain-generator support, dashboard service files, and the PX4
model/airframe inventory. It finishes by running:

```bash
venv/bin/python scripts/deploy/check_deployment.py
```

Expected current result:

```text
33 OK, 0 FAIL, 0 WARN, 0 SKIP
```

The dashboard is served by the installed systemd unit. On a local deployment,
open:

```text
http://127.0.0.1:8600
```

Use `DATABOSS_DASHBOARD_HOST` when the dashboard must bind somewhere other than
loopback, and `DATABOSS_QGC_IP` when QGroundControl is not on the same host.

Important: a fresh clone does **not** include `generated_worlds/` because that
tree is gitignored. Current scenarios reference
`generated_worlds/.../world.sdf_path`, so terrain/flat worlds must be copied
from a bundle or regenerated before those scenarios can fly.

## Host requirements

- Ubuntu 24.04 noble. `scripts/deploy/bootstrap.sh` pins the OSRF apt source to
  `noble`, so other Ubuntu releases are not the documented target.
- Gazebo Harmonic, currently `gz sim` 8.14.0 from OSRF noble packages.
- At least 2 CPU cores and at least 4 GB RAM; more is strongly advised.
- At least 15 GB free disk before deployment.
- x86_64 system Python 3.12 from Ubuntu 24.04.

This repo was developed on a tight reference host with 2 cores and 3 GB RAM.
That is the documented lower-bound evidence host, not a recommendation. The
bootstrap script's own output warns that `make px4_sitl_default` "takes a long
time on a 2-core host".

---

## 1. Executive summary

DATABOSS automates PX4/Gazebo startup, QGroundControl streaming, takeoff,
route/hover execution, timed GNSS loss, optional aiding bridges, landing, ULog
collection, Gazebo ground-truth recording, PX4/truth alignment, per-run plots,
statistics, validation, and comparison reporting.

Current checked deployment facts:

```text
vehicles: afbr_s50, test_test, x500_ark_flow, x500_cam_lidar_down, x500_e2e_widecam
airframes: 4022_gz_x500_cam_lidar_down, 4023_gz_x500_ark_flow,
           4024_gz_test_test, 4025_gz_x500_e2e_widecam
scenarios: 13
archived runs: 7
plots per run: 14
deployment checks: 33 OK / 0 FAIL / 0 WARN / 0 SKIP
static-asset checks: 81 OK / 0 FAIL
```

The dashboard currently exposes these pages:

```text
comparison_detail, comparisons, create, health, job_detail, jobs,
launch, overview, run_detail, runs, scenarios, vehicles
```

Backend routers:

```text
checks, comparisons, jobs, runs, scenarios, vehicles, worlds
```

The core scientific status is no longer "Phase 8A handoff". The system has
proven the runner, analysis, world, optical-flow, wind, dashboard, deployment,
and vehicle-install pipelines through Phase 20. The main unresolved scientific
item is that optical-flow sign/performance cannot be validated in the current
low-angular-rate flight regime; this needs a different flight profile, not
another code tweak.

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
- real optical-flow performance in a discriminating flight profile
- fresh-clone flight readiness until `generated_worlds/` is restored
- full bootstrap provisioning on a truly fresh production host

---

## 4. Architecture

### 4.1 Configuration layer

The experiment contract is represented by YAML files under:

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

The dashboard reads and writes against these contracts rather than inventing a
separate state model.

### 4.2 Runner/orchestration layer

Important scripts:

```text
scripts/runner/run_scenario_pxh_end_to_end.py
scripts/runner/auto_takeoff_land_pxh_truth.py
scripts/runner/run_batch_matrix_pxh.py
scripts/runner/send_synthetic_external_odometry_mavlink.py
scripts/runner/send_live_gazebo_odometry_mavlink.py
scripts/sim/add_vehicle.py
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
scripts/analysis/generate_run_plots.py
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
run_stats.json
run_stats.md
```

Gazebo truth is the acceptance judge. EKF movement alone is not physical drift/error.

### 4.5 Dashboard layer

The dashboard is a FastAPI/HTML application backed by the same repo artifacts
used by the CLI. Current pages:

```text
comparison_detail, comparisons, create, health, job_detail, jobs,
launch, overview, run_detail, runs, scenarios, vehicles
```

Current routers:

```text
checks, comparisons, jobs, runs, scenarios, vehicles, worlds
```

The vehicles page can compose and install PX4/Gazebo vehicle variants through
the same pipeline as `scripts/sim/add_vehicle.py`. The runs page and run detail
view expose generated `run_stats.*` data and plots when present.

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

QGroundControl is configured through `DATABOSS_QGC_IP`. For a local-only
deployment, set it to:

```text
DATABOSS_QGC_IP=127.0.0.1
```

PX4/QGC UDP convention:

```text
PX4 local UDP port:  14555
QGC remote UDP port: 14550
```

The runner starts the outbound MAVLink stream with PX4 `mavlink start -m
config`. QGC is a monitor/viewer, not the automation engine.

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
├── run_stats.json
├── run_stats.md
├── validation.json or validation.md
├── ekf_vs_ground_truth_aligned.csv
├── ekf_vs_ground_truth_metrics.json
└── ekf_vs_ground_truth_metrics.md
```

Every completed run now receives a non-fatal analytics pack from
`scripts/analysis/generate_run_plots.py`: 14 plots plus `run_stats.json` and
`run_stats.md`. Plot/stat generation failure should not erase the run; the
dashboard run detail page shows the outputs on the Stats and Plots tabs when
they exist.

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
Current simulation vehicles:
afbr_s50
test_test
x500_ark_flow
x500_cam_lidar_down
x500_e2e_widecam
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

Condition presets include:

```text
lighting: noon_clear, sunset_low_angle
wind: none, crosswind_5ms
texture: high_texture, low_texture
```

Critical honesty rule: do not claim a condition is physically active unless the
scenario/world SDF and runtime evidence prove it. Wind now has deployment
patches and checks; historical runs before those patches remain evidence of
whatever that host actually flew.

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

### Phase 8B through Phase 16 — worlds, camera/rangefinder/flow, reports, terrain, wind

These phases are recorded in `docs/phases/` and `docs/PROJECT_LOG.md`. In
short, DATABOSS added generated Gazebo worlds, downward camera and rangefinder
vehicles, optical-flow bridge work, GNSS-denied comparison reports, dashboard
data contracts, terrain/difficulty matrices, and the Phase 16 wind roadmap.

The important current interpretation is preserved in the phase docs:
truth-fed EV is an upper bound, real optical flow has not passed the flow-sign
validation gate, and pre-2026-07-29 runs must be interpreted with the exact
host/PX4 patch state they recorded.

### Phase 17 — dashboard control panel

Added the dashboard control-panel workflow for browsing runs/comparisons,
creating scenarios, launching jobs, viewing health/checks, selecting worlds,
and operating through the stable YAML/runner contract.

### Phase 18 — reproducible deployment

Moved PX4 behavior patches and deployment inventory into repo-controlled
artifacts: `deploy/px4/px4_pins.yaml`, `scripts/deploy/bootstrap.sh`, and
`scripts/deploy/check_deployment.py`. Current deployment validation checks 4
airframes and 5 models and reports `33 OK / 0 FAIL / 0 WARN / 0 SKIP`.

### Phase 19 — dashboard redesign, partly landed

Phase 19 was planned as a dashboard redesign. Some dashboard/UI improvements
landed, but this README does not claim Phase 19 complete.

### Phase 20 — vehicle composition and install pipeline

Accepted vehicle-generation path from a structured spec through repo-side model
and airframe generation, optional dashboard generation, PX4 install, airframe
registration, pins refresh, patch regeneration, and deployment checks. Full
details: `docs/phases/phase_20_vehicle_pipeline.md`.

---

## 10. Current system state

### Deployment inventory

Current repo-side vehicles under `src/databoss_sim/models/`:

```text
afbr_s50
test_test
x500_ark_flow
x500_cam_lidar_down
x500_e2e_widecam
```

Current DATABOSS airframes:

```text
4022_gz_x500_cam_lidar_down
4023_gz_x500_ark_flow
4024_gz_test_test
4025_gz_x500_e2e_widecam
```

Current measured counts:

```text
scenarios: 13
archived runs: 7
plots per run: 14
check_deployment.py: 33 OK, 0 FAIL, 0 WARN, 0 SKIP
check_static_assets.py: 81 OK, 0 FAIL
```

Current check scripts:

```text
check_static_assets
check_deployment
check_model_sync_and_fov
check_flow_velocity_sign
check_vehicle_composer
```

### Dashboard

Pages:

```text
comparison_detail, comparisons, create, health, job_detail, jobs,
launch, overview, run_detail, runs, scenarios, vehicles
```

Routers:

```text
checks, comparisons, jobs, runs, scenarios, vehicles, worlds
```

### Vehicle pipeline

Vehicle specs are documented in
`docs/phases/phase_20_vehicle_pipeline.md`. The dashboard vehicles page and CLI
share the same composition/install path:

```text
POST /api/vehicles/generate
POST /api/vehicles/install
scripts/sim/add_vehicle.py
```

The generated model and airframe can be written repo-side first, then installed
into PX4. Install syncs the model into `Tools/simulation/gz/models/`, installs
the generated airframe into `ROMFS/px4fmu_common/init.d-posix/airframes/`,
updates the PX4 airframe `CMakeLists.txt`, refreshes
`deploy/px4/px4_pins.yaml`, and regenerates the airframe-registration patch.

Two hard rules are load-bearing:

- `base` must be `x500`; the wind patch targets PX4's `x500_base`, so other
  bases silently invalidate wind scenarios.
- A `gpu_lidar` on the native PX4/Gazebo path must be named
  `lidar_sensor_link/lidar`. PX4 hardcodes that topic in `GZBridge.cpp:279`.

### Per-run analytics

Every run now receives a non-fatal analytics pack generated by:

```text
scripts/analysis/generate_run_plots.py
```

Expected outputs:

```text
14 plots
run_stats.json
run_stats.md
```

The dashboard run detail page shows those products on the Stats and Plots tabs.
Plot/stat generation is intentionally non-fatal so an analysis issue does not
destroy otherwise useful flight evidence.

### Current remote

```text
remote: git@github.com:mmucteba/px4_sim.git
branch: main
```

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
- Optical-flow claims must be backed by a flight profile with enough angular flow rate for the EKF gate to discriminate signal from noise.

### QGC

- QGC remains a monitor/viewer.
- The runner must not depend on manual QGC actions.
- Local-only deployments should set `DATABOSS_QGC_IP=127.0.0.1`.

### Vehicle pipeline

- Vehicle composition must use the Phase 20 spec schema.
- Generated vehicles must pass composer and deployment checks before being treated as runnable.
- Vehicle install must not run concurrently with a flight/job.
- Native-path lidar vehicles must preserve PX4's hardcoded `lidar_sensor_link/lidar` naming.

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
- Environment condition labels must not be described as physical effects unless SDF/runtime evidence proves the wiring.
- Fresh clones are not flight-ready until `generated_worlds/` has been restored or regenerated.
- PX4 tree ownership is separate from repo ownership; dashboard vehicle install can fail if the PX4 ROMFS airframe registration file is not writable.
- Prefer small, testable patches with syntax checks and explicit acceptance evidence.

---

## 13. Unresolved technical issues

### 13.1 Optical-flow validation regime

Optical flow cannot be validated in the current flight regime. Angular flow
rate is velocity divided by height. The current scenarios fly `0.2 m/s` at
`15 m`, which produces only `0.0133 rad/s`; two scenarios fly `0 m/s`.

Measured assigned observation noise from `EKF2_OF_N_MIN=0.3` implies
`sigma=0.303 rad/s`, which is 4-7x the measured airborne signal. The EKF
innovation gate therefore cannot discriminate signal from noise: runs fuse
about 99% of samples while contributing no meaningful constraint, and
`run_stats` reports `gate_discriminating: false`.

No run has ever passed `check_flow_velocity_sign`. Fixing this needs a flight
profile with real translation, not a code change. For example, `2 m/s` at `5 m`
gives about `1.33x` the noise floor.

### 13.2 Fresh clone cannot fly until worlds are restored

`generated_worlds/` is gitignored, but every current scenario references:

```text
world.sdf_path: generated_worlds/...
```

The needed generated worlds are about 27 MB in the current deployment context.
A clone-based deployment must transfer or regenerate them before running
scenarios.

### 13.3 Scenario naming mismatch

`gnss_off_lk_15m_refika_hover.yaml` is named `gnss_off` but has:

```yaml
gnss:
  loss_enabled: false
```

That scenario does not cut GNSS despite its filename.

---

## 14. Prioritized next tasks for Codex

### Task 1 — restore or regenerate `generated_worlds/` for clone deployments

Make fresh-clone flight readiness explicit in deployment automation. Either
ship a documented world bundle with checksum evidence or add a deterministic
regeneration route for the current scenario set.

### Task 2 — create a discriminating optical-flow flight profile

Add scenarios with real translation and lower altitude so optical-flow angular
rate rises above the current observation-noise floor. Re-run
`check_flow_velocity_sign` only after the profile can produce a discriminating
gate.

### Task 3 — fix the GNSS scenario naming/config mismatch

Either rename `gnss_off_lk_15m_refika_hover.yaml` or change its
`gnss.loss_enabled` value after deciding what the scenario is supposed to test.

### Task 4 — prove clone-based deployment end to end

Run `scripts/deploy/bootstrap.sh --yes` on a fresh Ubuntu 24.04 x86_64 host
with adequate RAM/disk, restore worlds, then verify
`scripts/deploy/check_deployment.py` reports `33 OK / 0 FAIL / 0 WARN / 0 SKIP`.

### Task 5 — keep the vehicle pipeline guarded

Before adding another vehicle, run:

```bash
venv/bin/python scripts/sim/check_vehicle_composer.py
venv/bin/python scripts/deploy/check_deployment.py
```

Install vehicles only while no dashboard job/flight is active, then rebuild PX4
so the generated airframe target exists.

---

## 15. Suggested immediate acceptance criteria

For the current deployment/control-panel state, immediate acceptance should be:

```text
check_deployment.py: 33 OK, 0 FAIL, 0 WARN, 0 SKIP
check_static_assets.py: 81 OK, 0 FAIL
Vehicle composer check passes before adding/installing vehicles.
Fresh clone has generated_worlds restored or regenerated before flights.
Every run preserves ULog, Gazebo truth, aligned metrics, run_stats, and 14 plots when analysis succeeds.
Optical-flow validation uses a flight profile whose angular flow rate can exceed the observation-noise floor.
Documentation records actual results and limitations without rewriting historical evidence.
```

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

**Continue by making a fresh clone flight-ready with `generated_worlds/`, then create a higher-translation/lower-altitude optical-flow validation profile before treating flow-sign results as meaningful.**
