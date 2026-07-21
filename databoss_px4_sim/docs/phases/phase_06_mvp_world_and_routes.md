# Phase 6 — MVP World and Routes

## Goal

Define the first DATABOSS MVP world and route configs before building automation.

This phase does not need to run PX4 yet. It freezes what the future runner must execute.

## MVP world

World name:

databoss_mvp_yard_120m

World requirements:

- 120 m x 120 m local test area.
- Flat ground.
- High-texture ground planned for optical flow later.
- One tall tower or building target.
- Several simple block obstacles.
- No trees, cars, rain, fog, glass, or moving objects in MVP.
- Lighting starts as noon_clear.
- Wind starts disabled.

## MVP route set

R1:

hover_60s

Purpose:

Baseline hover at fixed altitude.

R2:

straight_50m_out_and_back

Purpose:

Simple translation error test.

R3:

square_50m

Purpose:

Waypoint tracking and accumulated drift test.

R4:

tower_inspection_visual

Purpose:

Visual route around a tall object for future camera / LiDAR / optical-flow testing.

## MVP altitude set

- 3 m AGL
- 10 m AGL
- 30 m AGL
- 60 m AGL

## Acceptance criteria

Phase 6 is accepted when these files exist:

- experiments/configs/mvp/worlds/databoss_mvp_yard_120m.yaml
- experiments/configs/mvp/routes/hover_60s.yaml
- experiments/configs/mvp/routes/straight_50m_out_and_back.yaml
- experiments/configs/mvp/routes/square_50m.yaml
- experiments/configs/mvp/routes/tower_inspection_visual.yaml

## Result

Accepted.

## Next phase

Phase 7A — Automated Scenario Runner.
