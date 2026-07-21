# Phase 7A — Automated Scenario Runner

## Goal

Create the first DATABOSS runner that can execute one scenario from YAML.

The runner should eventually:

1. Read scenario YAML.
2. Create a run folder.
3. Start PX4/Gazebo.
4. Start Gazebo ground-truth recording.
5. Arm automatically.
6. Take off automatically.
7. Fly the configured route.
8. Trigger GNSS loss if configured.
9. Land automatically.
10. Copy ULog.
11. Extract CSV.
12. Align PX4 EKF with Gazebo truth.
13. Generate plots and summaries.

## MVP scope for first runner

The first version does not need to fully fly yet.

It must safely create the run folder and validate scenario config fields.

## Inputs

Example scenario:

experiments/configs/mvp/scenarios/example_mvp_hover_10m_gnss_on.yaml

## Output run folder

experiments/runs/<run_id>/

Required first files:

- README.md
- config.yaml
- commands.log
- environment.txt
- validation.md

## Acceptance criteria

Phase 7A first step is accepted when:

- scripts/runner/create_run_from_scenario.py exists.
- It accepts a scenario YAML path.
- It creates a clean run folder.
- It copies the scenario YAML into config.yaml.
- It writes README.md.
- It writes commands.log.
- It writes environment.txt.
- It writes validation.md.
- No output is written into PX4 source.

## Result

Accepted for first-step prepared run creation. Full PX4/Gazebo flight automation is still pending.

## Phase 7A.2 Result

Accepted.

PX4/Gazebo headless launcher smoke test passed.

Evidence:

- PX4/Gazebo started from DATABOSS runner.
- Readiness detected using: Startup script returned successfully.
- Console log written into run folder.
- Launcher status JSON written into run folder.
- Process stopped cleanly with SIGINT.
- No output written into PX4 source.

## Phase 7A.3 Next Step

Automated PX4 shell control:

- Start PX4/Gazebo.
- Wait for startup.
- Send commander arm.
- Send commander takeoff.
- Hover briefly.
- Send commander land.
- Stop cleanly.
- Copy ULog into run folder.

## Phase 7A.3 Attempt 1 Result

Rejected as a flight automation test.

What worked:

- PX4/Gazebo started.
- PX4 shell commands were sent.
- ULog was copied into the run folder.

What failed:

- PX4 denied arming because there was no GCS connection.
- No takeoff was detected.
- No landing was detected.
- The script acceptance rule was too weak because it accepted startup + ULog copy only.

Fix:

- Treat "Arming denied" as rejection.
- For SITL-only PX4 shell testing, use forced arming:
  commander arm -f

## Phase 7A.3 Attempt 2 Result

Accepted.

Evidence:

- PX4/Gazebo started from DATABOSS runner.
- SIM_GPS_USED was restored to 10.
- PX4 shell sent commander arm -f.
- PX4 armed by internal command.
- Takeoff was detected.
- Landing was detected.
- Vehicle disarmed by landing.
- ULog was copied into the DATABOSS run folder.

Important limitation:

This is a PX4-shell SITL automation proof. It uses forced arming because no GCS is connected.
Final automation should use MAVSDK/MAVLink instead of PX4 shell commands.

## Phase 7A.4 Next Step

Automated PX4 shell takeoff/land with Gazebo ground-truth recording.

Acceptance requires:

- PX4/Gazebo starts.
- Gazebo truth recorder starts.
- Vehicle arms, takes off, lands, and disarms.
- ULog is copied.
- Gazebo truth raw log is written.
- No output is written into PX4 source.

## Phase 7A.4 Result

Accepted.

Evidence:

- PX4/Gazebo started.
- Gazebo truth recorder started on /world/default/dynamic_pose/info.
- Gazebo truth raw log was written.
- Vehicle armed with commander arm -f.
- Takeoff was detected.
- Landing was detected.
- Vehicle disarmed by landing.
- ULog was copied into the DATABOSS run folder.

Important limitation:

This still uses PX4 shell forced arming.
It is valid as SITL automation proof, but final runner should use MAVSDK/MAVLink.

## Phase 7A.5 Next Step

Automatic postprocessing for the latest automated run:

- Parse Gazebo truth raw text into CSV.
- Extract PX4 ULog datasets into extracted_csv/.
- Write postprocess_summary.json.
- Write postprocess_summary.md.

## Phase 7A.5 Result

Accepted.

Evidence:

- Gazebo truth raw text was parsed into gazebo_ground_truth_x500_0.csv.
- PX4 ULog was extracted directly with pyulog.
- 10 ULog CSV datasets were written.
- postprocess_summary.md and postprocess_summary.json were written.
- Postprocess result was Accepted.

## Phase 7A.6 Next Step

Automatic EKF vs Gazebo truth alignment and metrics.

Acceptance requires:

- vehicle_local_position.csv exists.
- gazebo_ground_truth_x500_0.csv exists.
- Takeoff crossing alignment is computed.
- EKF local position is compared against Gazebo truth.
- ekf_vs_ground_truth_aligned.csv is written.
- ekf_vs_ground_truth_metrics.json is written.
- ekf_vs_ground_truth_metrics.md is written.

## Phase 7A.6 Result

Accepted.

Evidence:

- vehicle_local_position.csv existed.
- gazebo_ground_truth_x500_0.csv existed.
- Takeoff crossing alignment was computed.
- ekf_vs_ground_truth_aligned.csv was written.
- ekf_vs_ground_truth_metrics.json was written.
- ekf_vs_ground_truth_metrics.md was written.

Accepted metrics from automated GNSS-on takeoff/land truth run:

- Aligned rows: 5644
- Aligned duration: 45.148 s
- Horizontal error max: 0.128013 m
- Horizontal error mean: 0.046915 m
- Height absolute error max: 0.101816 m
- Height absolute error mean: 0.025485 m
- 3D error max: 0.132001 m
- 3D error mean: 0.056450 m

Conclusion:

The DATABOSS runner can now produce a truth-judged automated PX4/Gazebo evaluation run.

## Phase 7A.7 Next Step

Create a single end-to-end scenario runner that performs:

1. PX4/Gazebo launch.
2. Gazebo truth recording.
3. Automated PX4 shell takeoff/land.
4. ULog copy.
5. Gazebo truth parsing.
6. ULog extraction.
7. EKF-vs-truth alignment.
8. Final run summary.

## Phase 7A.7 Result

Accepted.

Evidence:

- Single end-to-end scenario runner completed.
- Automated PX4/Gazebo truth flight passed.
- Postprocess passed.
- EKF vs Gazebo truth alignment passed.
- final_summary.md was written.
- end_to_end_status.json was written.

## Phase 7A.8 Next Step

Keep QGroundControl connected during automated runs.

Goal:

- Automated runner still controls the flight.
- QGroundControl remains available as a viewer/operator monitor.
- PX4 starts an additional MAVLink config stream to the Mac Tailscale IP.
- The QGC link command is logged in the run status.

QGC command:

mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x

## Phase 7A.8 Result

Accepted.

Evidence:

- End-to-end scenario runner passed with QGroundControl link enabled.
- PX4 started the additional MAVLink config stream.
- QGC target IP: 100.109.200.5
- Local UDP port: 14555
- Remote UDP port: 14550
- PX4 console confirmed mode: Config on UDP port 14555 remote port 14550.
- Automated flight still passed.
- Gazebo truth recording passed.
- ULog extraction passed.
- EKF vs Gazebo truth alignment passed.
- final_summary.md was written.

Accepted QGC-enabled run:

- Run folder: 20260703_141502_example_mvp_hover_10m_gnss_on_pxh_takeoff_land_truth
- Truth rows: 5656
- Aligned rows: 6272
- Aligned duration: 50.172 s
- Horizontal error mean: 0.031921 m
- Horizontal error max: 0.092054 m
- Height absolute error mean: 0.016154 m
- 3D error max: 0.093527 m

Conclusion:

Phase 7A is complete for PX4-shell SITL automation.
The runner can now produce a QGC-visible, truth-recorded, postprocessed, metrics-scored automated evaluation run.

## Next Phase

Phase 7B: Automated GNSS-loss scenario.

Goal:

Run the same end-to-end pipeline, but cut simulated GNSS during flight and score the EKF behavior against Gazebo ground truth.
