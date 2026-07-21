# Phase 1 - GNSS ON Baseline

Goal:
Run a clean controlled hover with GNSS enabled.

Experiment:
- Default X500
- PX4 SITL + Gazebo headless
- GNSS enabled
- Takeoff to 2.5 m
- Hover about 60 seconds
- Land
- Save ULog into a clean run folder

Acceptance criteria:
- Vehicle takes off and lands
- ULog saved
- EKF local position valid
- GNSS available
