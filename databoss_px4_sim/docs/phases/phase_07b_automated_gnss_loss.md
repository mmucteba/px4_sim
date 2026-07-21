# Phase 7B — Automated GNSS-Loss Scenario

## Goal

Run the same end-to-end automated DATABOSS pipeline, but cut simulated GNSS during flight.

The runner must still produce:

- PX4/Gazebo automated flight
- QGroundControl telemetry link
- Gazebo ground truth
- ULog copy
- ULog CSV extraction
- EKF vs Gazebo truth alignment
- final_summary.md

## GNSS-loss command

param set SIM_GPS_USED 0

## Acceptance criteria

- End-to-end runner starts successfully.
- QGroundControl MAVLink stream starts.
- Vehicle arms.
- Vehicle takes off.
- GNSS-loss command is sent after takeoff.
- Vehicle lands or failsafe-lands.
- Vehicle disarms.
- ULog is copied.
- Gazebo truth is recorded.
- Postprocess succeeds.
- EKF vs truth metrics are written.

## Result

Pending.
