# Phase 7B — Rerunnable Case Matrix

## Goal

Make DATABOSS experiments rerunnable from config.

Instead of manually changing commands, define cases in a YAML batch file and run them through the same end-to-end pipeline.

## Each case must produce

- PX4/Gazebo automated flight
- Optional QGroundControl telemetry link
- Gazebo truth recording
- ULog copy
- ULog CSV extraction
- Gazebo truth CSV extraction
- EKF vs Gazebo truth alignment
- Metrics report
- Final summary

## Case types

Initial Phase 7B cases:

1. GNSS ON baseline.
2. GNSS loss shortly after takeoff.
3. GNSS loss later after takeoff.
4. GNSS loss with longer post-loss observation.

## Acceptance

Accepted when:

- A batch YAML exists.
- A batch runner exists.
- Each case can be rerun from config.
- Batch summary JSON/Markdown is produced.
- Each successful case points to its generated run folder.

## Status

In progress.

## Single GNSS-loss Case Result

Accepted.

Evidence:

- Batch runner launched the GNSS-loss case from YAML.
- End-to-end runner forwarded GNSS-loss options correctly.
- PX4 console confirmed SIM_GPS_USED changed from 10 to 0.
- QGroundControl MAVLink link remained enabled.
- Gazebo truth recording passed.
- ULog extraction passed.
- EKF vs Gazebo truth alignment passed.

Accepted single case:

- Case: gnss_loss_after_takeoff_5s_post_25s
- GNSS loss after takeoff: 5.0 s
- Post-loss hover: 25.0 s
- Horizontal error mean: 0.067720 m
- Horizontal error max: 0.177870 m
- 3D error max: 0.341648 m

## Full Matrix Result — Default PX4 Failsafe Active

Accepted as a default-failsafe behavior batch.

Important interpretation:

This batch was run before failsafe behavior was made explicit in the case YAML.
Therefore the GNSS-loss cases should be interpreted as:

- GNSS loss with PX4 default/current failsafe behavior active.
- Not pure EKF drift-only behavior.
- Not delayed-failsafe behavior unless the console/status proves the relevant failsafe parameters were set.

Accepted batch:

- Batch folder: experiments/batches/20260703_144337_phase7b_gnss_cases
- Cases run: 4
- Accepted: 4
- Failed: 0

Metrics:

- Baseline GNSS ON horizontal mean: 0.040612 m
- GNSS loss 5s/post25s horizontal mean: 0.066033 m
- GNSS loss 15s/post25s horizontal mean: 0.066497 m
- GNSS loss 5s/post45s horizontal mean: 0.073760 m

Conclusion:

The matrix runner is valid and rerunnable.
The next fix is to make failsafe behavior an explicit case parameter.

## Failsafe Audit Result

The first full 4-case matrix is accepted only as default/current PX4 failsafe behavior.

Audit result:

- Baseline GNSS ON: failsafe-related warnings seen, but no "Failsafe activated".
- GNSS-loss 5s/post25s: "Failsafe activated" seen.
- GNSS-loss 15s/post25s: "Failsafe activated" seen.
- GNSS-loss 5s/post45s: "Failsafe activated" seen.

Interpretation:

This batch is not pure drift-only.
Future batches must declare failsafe_profile explicitly.

Required profiles:

- default_px4
- delayed_observation

## Delayed Observation Single Case Result

Accepted.

Evidence:

- Batch command included --failsafe-profile delayed_observation.
- Runner applied delayed-observation failsafe parameters.
- Failsafe profile OK was true.
- GNSS loss was requested and detected.
- QGroundControl MAVLink remained enabled.
- Gazebo truth was recorded.
- ULog was copied.
- Postprocess passed.
- EKF vs Gazebo truth alignment passed.
- Landing was not required because this was an observation-mode GNSS-loss case.
- No "Failsafe activated" console evidence was seen.

Accepted delayed-observation case:

- Case: gnss_loss_after_takeoff_5s_post_25s
- Run folder: 20260703_150946_example_mvp_hover_10m_gnss_on_pxh_takeoff_land_truth
- Horizontal error mean: 34.853598 m
- Horizontal error max: 115.579242 m
- 3D error max: 115.579300 m

Interpretation:

This is the first valid delayed-observation GNSS-loss drift result.
It is separate from the earlier default-failsafe matrix.

## Full Delayed-Observation Matrix Result

Accepted.

Batch:

- experiments/batches/20260703_152125_phase7b_gnss_cases_delayed_failsafe

Evidence:

- Cases run: 4
- Accepted: 4
- Failed: 0
- QGroundControl link enabled during runs.
- Gazebo truth recorded.
- ULog copied.
- ULog extraction passed.
- EKF vs Gazebo truth alignment passed.
- Failsafe profile: delayed_observation.
- Failsafe activated count: 0 for all cases.

Delayed-observation metrics:

- Baseline GNSS ON:
  - Horizontal mean: 0.040922 m
  - Horizontal max: 0.132272 m
  - 3D max: 0.152463 m

- GNSS loss after takeoff 5s, post-loss 25s:
  - Horizontal mean: 0.037803 m
  - Horizontal max: 0.113541 m
  - 3D max: 0.131277 m

- GNSS loss after takeoff 15s, post-loss 25s:
  - Horizontal mean: 29.719157 m
  - Horizontal max: 111.177823 m
  - 3D max: 111.177838 m

- GNSS loss after takeoff 5s, post-loss 45s:
  - Horizontal mean: 93.202379 m
  - Horizontal max: 305.039270 m
  - 3D max: 305.039293 m

Conclusion:

Phase 7B is accepted.

DATABOSS can now rerun GNSS ON, GNSS-loss default-failsafe, and GNSS-loss delayed-observation cases from YAML and produce truth-based metrics.
