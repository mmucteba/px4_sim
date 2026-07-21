# Phase 3 - GNSS OFF Hover

Goal:
Simulate GNSS loss during hover and compare drift against Phase 1.

Experiment:
- Stabilize with GNSS
- Disable/loss GNSS during hover
- Keep PX4 running
- Save ULog
- Compare against GNSS ON baseline

Acceptance criteria:
- GNSS loss moment visible in data
- ULog saved
- GNSS ON vs GNSS OFF comparison possible
