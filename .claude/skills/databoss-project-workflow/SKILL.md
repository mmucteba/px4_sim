---
name: databoss-project-workflow
description: >
  Project-specific engineering workflow for DATABOSS, a PX4/Gazebo GNSS-denied
  navigation simulation and evaluation system. Use this skill when planning,
  implementing, testing, documenting, or handing off DATABOSS work.
version: 1.0
---

# DATABOSS Project Workflow Skill

## Purpose

Use this skill to keep DATABOSS development:

- phase-based
- reproducible
- honest about what is physically simulated
- cleanly separated from the PX4 source tree
- documented after every meaningful change
- easy to hand off to another chat, engineer, or coding agent

The main research objective is to evaluate PX4 behavior and navigation-aiding
methods during GNSS loss using Gazebo ground truth as the reference.

---

## Project Paths

### DATABOSS workspace

```text
/opt/databoss_px4_sim
```

Store here:

- scenario and batch YAML files
- generated Gazebo worlds
- scripts
- run folders
- plots
- metrics
- reports
- phase documentation
- architecture documentation

### PX4 source

```text
/opt/sim_px4/PX4-Autopilot
```

Treat this as the simulation engine.

Do not store DATABOSS experiment outputs in the PX4 source tree.

---

## Operating Style

### Communication

- Address the user casually as “bro” when natural.
- Be direct and practical.
- Prefer concrete next actions over broad explanations.
- Use clean shell commands that can be copied and run.
- Explain why a command is being run when the reason is not obvious.
- Do not produce fake results or imply an unexecuted test passed.
- Clearly separate:
  - implemented
  - configured
  - physically simulated
  - tested
  - accepted
  - planned

### Engineering rhythm

For each phase:

1. State the phase goal.
2. Define what is in scope and out of scope.
3. Inspect the current repository before editing.
4. Make the smallest coherent implementation.
5. Run a focused smoke test.
6. Inspect logs and generated artifacts.
7. Decide accepted, rejected, or needs repair.
8. Update Markdown documentation.
9. Update `docs/PROJECT_LOG.md`.
10. Provide the exact next step.

Do not mix several major subsystems in one phase.

---

## Permanent Technical Rules

### Ground truth

Gazebo ground truth is the judge.

PX4 EKF output alone is not sufficient for physical-error evaluation.

### Frames

Gazebo world convention (ENU):

```text
X: East, Y: North, Z: upward
```

PX4 local convention (NED):

```text
X: North, Y: East, Z: downward
```

Current local conversion (corrected 2026-07-13 — the horizontal axes SWAP;
the old direct x↔x/y↔y rule was wrong and only looked right on hover runs
with zero displacement; see PROJECT_LOG 2026-07-13 and
`docs/architecture/frames_and_alignment.md`):

```text
px4_x = gazebo_y - gazebo_y_initial   # North
px4_y = gazebo_x - gazebo_x_initial   # East
px4_z = -(gazebo_z - gazebo_z_initial)
```

Every new sensor or estimator must explicitly define:

- source frame
- target frame
- timestamp source
- transform
- axis signs
- units
- covariance
- update rate
- latency behavior

### GNSS loss

Accepted simulated GNSS-loss command:

```text
param set SIM_GPS_USED 0
```

Restore:

```text
param set SIM_GPS_USED 10
```

Do not use this rejected method as the primary GNSS-loss mechanism:

```text
param set SYS_FAILURE_EN 1
failure gps off
```

It previously failed to make the simulated GPS unhealthy.

### QGroundControl

QGroundControl is a viewer and operator monitor.

It is not the experiment automation engine.

Known QGC/Tailscale target:

```text
100.109.200.5
```

Known PX4 MAVLink command:

```text
mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x
```

### Environment-condition honesty

Do not claim wind, lighting, terrain texture, fog, or other environment effects
changed the vehicle unless the values were physically applied to Gazebo.

A YAML label is not a physical simulation.

### External odometry honesty

Truth-fed external odometry is an ideal integration reference.

It proves the PX4 external-aiding path, not a real VIO, optical-flow, or LiDAR
algorithm.

---

## Runtime and Environment Rules

Run PX4/Gazebo as user:

```text
px4
```

Use the DATABOSS virtual environment for project Python and analysis:

```bash
cd /opt/databoss_px4_sim || exit 1
source venv/bin/activate
```

Before PX4 builds or launches, prevent the DATABOSS virtual environment from
leaking into PX4 tooling:

```bash
deactivate 2>/dev/null || true
unset VIRTUAL_ENV
unset PYTHONHOME
unset PYTHONPATH
export PATH="$(echo "$PATH" | tr ':' '\n' | grep -v '/opt/databoss_px4_sim/venv/bin' | paste -sd: -)"
hash -r
```

Before a run:

```bash
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
```

Do not recommend deleting build folders or run data unless the reason is clear
and the action is safe.

---

## Repository Layout

Expected high-level layout:

```text
/opt/databoss_px4_sim
├── docs
│   ├── architecture
│   ├── hardware
│   ├── phases
│   └── PROJECT_LOG.md
├── experiments
│   ├── configs
│   ├── runs
│   ├── batches
│   └── comparisons
├── generated_worlds
├── presets
├── scripts
│   ├── analysis
│   ├── runner
│   ├── sim
│   ├── utils
│   └── worlds
└── src
    └── databoss_sim
```

Every accepted run should aim to contain:

```text
experiments/runs/<run_id>/
├── README.md
├── config.yaml
├── commands.log
├── environment.txt
├── logs/
│   └── flight.ulg
├── truth/
├── sensor_logs/
├── extracted_csv/
├── plots/
├── summary.json
├── summary.md
└── validation.md
```

Do not silently change this contract. Document intentional changes.

---

## Phase Design Template

Use this structure for a new phase document:

```markdown
# Phase X — Title

## Goal

## Why this phase exists

## In scope

## Out of scope

## Inputs

## Implementation

## Commands

## Expected outputs

## Acceptance criteria

## Results

## Interpretation

## Known limitations

## Files created or modified

## Next phase
```

A phase is accepted only when its acceptance criteria have evidence.

Use one of these statuses:

```text
Planned
In progress
Blocked
Rejected
Accepted
Accepted with limitations
```

---

## Experiment Design Rules

A valid comparison should control all non-tested variables whenever practical.

Freeze and record:

- PX4 commit or version
- world
- vehicle
- spawn pose
- route
- altitude
- flight duration
- GNSS-loss timing
- failsafe profile
- EKF parameters
- sensor parameters
- wind and lighting
- random seed
- script version
- QGC connection settings
- ground-truth source

For an A/B/C comparison:

```text
A: stable reference
B: failure or no-aiding case
C: proposed aiding case
```

Only change the intended experimental variable.

---

## Acceptance Evidence

Prefer evidence in this order:

1. Saved configuration
2. Console log
3. PX4 ULog
4. Gazebo truth log
5. Sensor log
6. Parsed metrics
7. Plot
8. Human-readable validation report

A screenshot alone is not sufficient when machine-readable evidence can be
saved.

Acceptance reports should answer:

- Did the requested condition occur?
- Was the intended sensor or aiding stream received?
- Was it fused or rejected?
- Did the vehicle complete the mission?
- What was the physical error against truth?
- Were there estimator faults?
- What limitations remain?

---

## Sensor Integration Checklist

For every simulated sensor, document:

### Physical model

- pose on vehicle
- orientation
- field of view or beam geometry
- range
- update rate
- noise
- bias
- latency
- dropout
- saturation
- environmental dependencies

### Software path

```text
Gazebo sensor
→ simulator topic or bridge
→ PX4 input/uORB/MAVLink
→ EKF or controller
→ ULog evidence
```

### Validation

Compare the sensor output against Gazebo truth before using it for navigation.

Never skip the standalone sensor-validation phase.

---

## Current Practical Sensor Direction

Primary practical sensing stack:

```text
downward monocular camera
+
TF03-style downward single-point LiDAR
+
PX4 onboard IMU
```

Primary environments:

- rural fields
- hills
- ridges
- valleys
- mountain terrain
- open land

Not part of the immediate implementation unless explicitly reopened:

- 3D LiDAR
- LiDAR SLAM
- stereo VIO
- dense urban worlds
- dashboard-first development

---

## World-Building Workflow

Use this sequence:

1. Create or update world YAML.
2. Generate an SDF world.
3. Validate that the SDF parses.
4. Launch the world without PX4 if needed.
5. Verify visual and collision geometry.
6. Spawn the vehicle.
7. Record the exact generated SDF with the run.
8. Only then add cameras, LiDAR, wind, or missions.

Do not test optical flow before proving that the camera sees the intended
physical world.

---

## Documentation Update Rules

After meaningful work, update:

```text
docs/phases/<current_phase>.md
docs/phases/README.md
docs/PROJECT_LOG.md
```

Update architecture docs when interfaces or data contracts change.

Record:

- date
- decision
- commands
- result
- run folder
- limitations
- next action

Do not rewrite previous accepted results to make newer results look cleaner.
Preserve history.

---

## Command-Writing Rules

Commands must:

- start from a known directory
- include `|| exit 1` for critical directory changes
- avoid hidden assumptions
- avoid unnecessary destructive operations
- use quoted variables
- print generated paths
- leave evidence in the run folder
- restore temporary PX4 parameter changes when appropriate

For long procedures, provide:

1. inspection command
2. edit/create command
3. run command
4. verification command
5. documentation update command

Do not give a large blind script before checking the current file structure when
the repository may have changed.

---

## Decision Rules

### When a test fails

Do not immediately add more code.

First classify the failure:

```text
launch
configuration
transport
topic publication
frame conversion
timestamp
covariance
EKF rejection
controller/failsafe
truth alignment
analysis
```

Then inspect the narrowest relevant evidence.

### When results look unexpectedly good

Check for:

- truth leakage
- GNSS still active
- aiding enabled earlier than intended
- wrong ground-truth alignment
- comparing EKF to itself
- stale logs
- wrong run folder
- wrong vehicle model
- ignored sensor noise or latency

### When results look unexpectedly bad

Check for:

- frame mismatch
- NED/ENU sign error
- timestamp discontinuity
- covariance too small or too large
- low message rate
- stale messages
- EKF innovation rejection
- failsafe intervention
- physical collision
- controller saturation

---

## Standard Response Format

For implementation requests, respond in this order:

### Current situation

One short paragraph.

### What we are doing now

State the exact phase and goal.

### Commands

Provide copyable commands.

### Expected result

State what files, messages, or metrics should appear.

### Acceptance check

Provide exact verification commands and pass/fail criteria.

### What this proves

State the narrow technical conclusion.

### What this does not prove

State the remaining limitation.

### Next step

Give one next phase only.

---

## Handoff Workflow

When the chat becomes long, create a compact handoff containing:

- project goal
- current paths
- permanent technical rules
- accepted phases
- current phase
- exact files modified
- accepted and rejected methods
- latest run folders
- latest metrics
- unresolved issue
- next command to run

Avoid pasting irrelevant historical terminal output.

The handoff should let another engineer continue without guessing.

---

## Definition of Done for a Phase

A phase is done when:

- implementation exists
- config is saved
- smoke test ran
- evidence is saved
- acceptance criteria are evaluated
- documentation is updated
- limitations are explicit
- next phase is defined

Code existing without a test is not completion.

A test passing without saved evidence is not reproducible completion.
