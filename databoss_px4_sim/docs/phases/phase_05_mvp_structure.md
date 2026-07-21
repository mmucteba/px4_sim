# Phase 5 — MVP Structure Freeze

## Goal

Freeze the clean DATABOSS MVP project structure before adding worlds, routes, automation, aiding sources, optical flow, or dashboard logic.

## Proven before this phase

- PX4 SITL + Gazebo works.
- GNSS ON baseline works.
- GNSS loss with SIM_GPS_USED=0 works.
- Default GNSS-loss failsafe causes blind land.
- Delayed failsafe allows drift observation.
- QGroundControl over Tailscale works as viewer.
- Gazebo ground truth can be recorded.
- PX4 ULog and Gazebo truth can be aligned.
- EKF-only drift is not enough; Gazebo truth must be the judge.

## Workspace rule

PX4 source:

/opt/sim_px4/PX4-Autopilot

DATABOSS workspace:

/opt/databoss_px4_sim

Do not store DATABOSS experiment outputs inside PX4 source.

## Accepted structure

- docs/phases/
- docs/architecture/
- experiments/configs/mvp/worlds/
- experiments/configs/mvp/routes/
- experiments/configs/mvp/scenarios/
- experiments/configs/mvp/batches/
- experiments/runs/
- experiments/comparisons/
- scripts/analysis/
- scripts/runner/
- scripts/worlds/
- src/databoss_sim/

## Acceptance criteria

Phase 5 is accepted when:

- The MVP folder structure exists.
- The phase roadmap exists.
- The MVP backend contract exists.
- A first placeholder scenario config exists.
- Future phases know where to write files.
- No new experiment output is written into PX4 source.

## Result

Accepted.

## Next phase

Phase 6 — MVP World and Routes.
