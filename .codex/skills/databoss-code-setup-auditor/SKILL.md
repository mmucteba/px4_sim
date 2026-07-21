---
name: databoss-code-setup-auditor
description: >-
  Analyze the DATABOSS PX4/Gazebo GNSS-denied navigation project before changing code.
  Use this skill for repository audits, environment/setup diagnosis, runner and YAML tracing,
  PX4/Gazebo integration checks, optical-flow and external-odometry pipeline audits, experiment
  validity reviews, and evidence-backed next-step planning. It is specialized for
  /opt/databoss_px4_sim and /opt/sim_px4/PX4-Autopilot.
---

# DATABOSS Code & Setup Auditor

## Mission

Act as the project’s evidence-first systems engineer.

Your job is not merely to read files or suggest patches. Your job is to reconstruct what the system **actually does**, separate that from what configs and documentation claim it does, identify the smallest defensible next change, and preserve experimental validity.

The mandatory reasoning chain is:

```text
requested behavior
→ configuration value
→ config loader
→ runner/CLI precedence
→ generated PX4/Gazebo commands
→ effective runtime state
→ recorded evidence
→ conclusion
```

Never skip the middle of this chain.

---

## Project roots and permanent boundaries

```text
DATABOSS workspace:
/opt/databoss_px4_sim

PX4 source/build engine:
/opt/sim_px4/PX4-Autopilot
```

Rules:

1. Store scenarios, scripts, generated worlds, runs, plots, reports, and comparisons in the DATABOSS workspace.
2. Treat the PX4 repository as the simulator/autopilot engine. Do not store DATABOSS experiment outputs there.
3. Run PX4/Gazebo as the `px4` user unless the task explicitly proves another user is required.
4. Do not let the DATABOSS Python virtual environment contaminate PX4 builds.
5. QGroundControl is a viewer/operator monitor. DATABOSS runners are the automation engine.
6. Gazebo truth is the scoring reference, not an estimator input, except in explicitly labeled ideal truth-aiding experiments.

---

## Core scientific rules

### 1. Requested is not consumed

A YAML key existing in a scenario does not prove the runner reads it.

For every important setting, report four states:

| State | Meaning |
|---|---|
| Requested | Value present in YAML, docs, or command request |
| Consumed | Code path that reads the value |
| Effective | Runtime value actually applied to PX4/Gazebo/bridge |
| Observed | Log evidence showing resulting behavior |

If one state is missing, mark it `unproven`.

### 2. Acceptance flags are not enough

Do not accept a run solely because a batch summary says `accepted: true`.

Inspect at least:

- `commands.log`
- scenario snapshot/config copied into the run
- bridge/sensor logs
- PX4 ULog topics
- Gazebo ground truth
- final `summary.json`, `summary.md`, and `validation.md`
- actual failsafe, fusion, GNSS, and estimator states

### 3. Gazebo truth is the judge

PX4 local position may remain smooth while the physical vehicle drifts.

Use:

```text
PX4 height_up = -vehicle_local_position.z
Gazebo height = gazebo_z - initial_gazebo_z
```

Frames must be declared. Never compare two values only because both are called NED, ENU, local, or odometry.

### 4. One variable at a time

A valid diagnostic experiment changes one primary variable while keeping constant:

- world and physical environment
- vehicle/model/airframe
- mission and timing
- altitude
- sensor pipeline
- update rate
- estimator parameters
- failsafe profile
- logging and analysis path

When this is impossible, label the comparison `confounded`.

### 5. Transport proof is not navigation proof

Classify evidence precisely:

- **transport proof**: message reaches PX4
- **publication proof**: expected uORB topic updates
- **fusion proof**: EKF aid source is active and fused
- **control proof**: controller uses a valid estimate without divergence
- **navigation proof**: physical error remains bounded against Gazebo truth
- **environment proof**: a configured world condition is visibly/physically applied

Never upgrade one level into another without evidence.

---

## Default audit workflow

Perform these phases in order. Remain read-only until the user explicitly asks for edits.

### Phase A — Establish task and current phase

State:

- the user’s concrete question
- the project phase or subsystem involved
- what would count as proof
- whether the task is code audit, setup audit, run audit, experiment design, or implementation

Do not assume phase status from an old handoff. Verify the repository and latest run evidence.

### Phase B — Snapshot the workspace

Run or inspect the equivalent of:

```bash
whoami
pwd
date -Is
uname -a

cd /opt/databoss_px4_sim
git status --short --branch 2>/dev/null || true
git rev-parse --show-toplevel 2>/dev/null || true
find . -maxdepth 3 -type f | sort | sed -n '1,300p'

cd /opt/sim_px4/PX4-Autopilot
git status --short --branch
git rev-parse HEAD
git describe --always --dirty --tags 2>/dev/null || true
```

Then identify:

- modified/untracked files
- duplicate or superseded scripts
- latest scenario, batch, run, and comparison folders
- latest phase documentation
- mismatch between docs and code

Use `scripts/collect_databoss_context.sh` from this skill when available.

### Phase C — Audit the execution environment

Inspect:

```bash
id
which python3
python3 --version
printf '%s\n' "${VIRTUAL_ENV:-<none>}"
printf '%s\n' "$PATH"

ps -ef | grep -E 'px4|gz sim|ruby|mavlink|QGroundControl|optical|odometry' | grep -v grep || true
ss -lunp 2>/dev/null | grep -E '14540|14550|14555|14600|14601' || true
ls -l /tmp/px4-sock-* 2>/dev/null || true
```

Confirm:

- PX4 is not accidentally using `/opt/databoss_px4_sim/venv/bin/python3`
- stale PX4/Gazebo processes are not affecting a test
- required UDP ports are not occupied by old processes
- files are owned by the correct user
- the intended PX4 build/model exists

If environment contamination exists, stop code diagnosis until setup is clean.

### Phase D — Trace configuration to runtime

For each relevant scenario key:

1. Open the scenario YAML.
2. Identify batch overrides.
3. Identify CLI overrides.
4. Locate the exact Python function that reads the key.
5. Locate defaults used when the key is absent.
6. Locate generated PX4 commands or environment variables.
7. Find the same value in `commands.log` or runtime logs.
8. Confirm the corresponding PX4 parameter or Gazebo state.

Audit precedence explicitly:

```text
hard-coded default
< shared config
< scenario YAML
< batch case override
< CLI argument
< runtime repair/override
```

Do not assume this precedence; derive it from code.

High-risk patterns:

- YAML keys that are never read
- a nested key read from the wrong location
- CLI default overriding YAML unintentionally
- multiple names for the same setting
- truthy string values such as `"false"`
- seconds vs microseconds vs milliseconds
- radians vs degrees
- standard deviation vs variance
- ENU vs NED sign swaps
- image resize without focal-length scaling
- sender rate greater than source measurement rate
- stale samples resent as new samples
- generated world conditions recorded only as metadata

### Phase E — Audit code structure and lifecycle

For entrypoints, runners, bridges, and analyzers, inspect:

- argument parsing
- config loading and schema assumptions
- hard-coded paths and ports
- process launch working directory
- environment sanitization
- timeout handling
- subprocess cleanup
- signal handling
- log capture
- return-code checks
- partial-run behavior
- restoration of PX4 parameters after a run
- deterministic run naming
- copying the exact config and commands used
- exception paths that can incorrectly mark a run accepted

For sensor bridges, also inspect:

- source topic and source rate
- timestamps and time base
- frame definitions and transforms
- axis signs
- integration interval
- calibration/intrinsics
- covariance/noise units
- quality scaling
- dropout/stale-data detection
- message fields left as NaN or zero
- output rate vs independent source rate

### Phase F — Verify the complete data path

#### GNSS loss

Required evidence:

```text
SIM_GPS_USED changes to 0
vehicle_gps_position fix_type falls
satellites_used falls
position accuracy degrades
PX4 reports GPS unhealthy
requested timing matches effective timing
```

The known reliable simulation injection is:

```text
param set SIM_GPS_USED 0
```

Restore with:

```text
param set SIM_GPS_USED 10
```

Do not use `failure gps off` as proof unless the current build is independently verified to support it.

#### External odometry

Trace:

```text
source estimate
→ MAVLink ODOMETRY
→ PX4 receiver
→ vehicle_visual_odometry
→ estimator aid-source topics
→ fused state
→ physical error against Gazebo truth
```

Truth-fed odometry is an ideal upper bound, not proof of a real sensor algorithm.

#### Optical flow

Trace each stage independently:

```text
Gazebo image/source
→ image timestamps and frame pairs
→ LK/flow calculation
→ angular flow and integration time
→ quality
→ MAVLink OPTICAL_FLOW_RAD
→ sensor_optical_flow
→ vehicle_optical_flow
→ estimator_aid_src_optical_flow
→ cs_opt_flow / fusion state
→ physical drift against truth
```

Before GNSS removal, require continuously active evidence equivalent to:

```text
vehicle_optical_flow updates
estimator_aid_src_optical_flow exists
fused = true
innovation_rejected = false
cs_opt_flow = true
```

A GNSS-on run is only a transport/integration smoke test unless flow-only observability is proven.

#### Rangefinder / TF03-style sensor

Trace:

```text
Gazebo ray sensor
→ range message
→ PX4 distance_sensor
→ validity and orientation
→ comparison with true slant range
```

Do not claim horizontal aiding from a single-point downward rangefinder.

#### Physical world conditions

For wind, lighting, texture, terrain, or other conditions:

- identify the SDF/plugin/material field changed
- inspect the generated SDF
- verify Gazebo launched that generated world
- capture visual or runtime evidence
- distinguish physical application from metadata labels

### Phase G — Audit run validity and comparability

Classify every run as one of:

| Classification | Definition |
|---|---|
| Accepted | All declared acceptance gates proven |
| Rejected | A required gate failed |
| Confounded | Multiple uncontrolled differences prevent attribution |
| Transport-only | Data path works, navigation claim unproven |
| Incomplete | Missing logs or interrupted execution |
| Invalid setup | Environment/process/config state made result unreliable |

Create an apples-to-apples table including:

- scenario path
- batch and CLI command
- vehicle/model/airframe
- world and physically applied conditions
- route, altitude, duration, and GNSS timing
- failsafe profile and effective parameters
- sensor implementation and rates
- EKF parameters
- quality/noise/covariance
- ground-truth availability
- accepted/rejected reason

### Phase H — Rank findings by evidence strength

Use exactly these labels:

- **Confirmed** — directly proven by code or logs
- **Highly likely** — strong evidence, one missing link
- **Possible** — plausible but not isolated
- **Unresolved** — requires a targeted test
- **Not causal** — evidence rules it out

Do not present possibilities as root causes.

### Phase I — Design the minimum next experiment

The next experiment must:

1. isolate one hypothesis;
2. reuse an accepted scenario where possible;
3. change the smallest number of fields;
4. include safety containment that is not misrepresented as a fix;
5. define preconditions before takeoff/GNSS cut;
6. define explicit pass/fail gates;
7. save all requested, effective, and observed values.

For unstable GNSS-denied tests, distinguish:

```text
measurement/fusion correction
versus
safety containment
```

For example, lowering `MPC_XY_VEL_MAX` may contain a runaway but does not repair optical-flow estimation.

---

## DATABOSS-specific known traps

Treat these as audit prompts, not eternal assumptions. Reverify them in current code.

1. A scenario may contain a failsafe profile key that the runner does not consume.
2. CLI defaults may silently select `default_px4` instead of a requested delayed-observation profile.
3. Environment condition presets may exist as labels without physical Gazebo wiring.
4. A 30 Hz camera cannot provide 40 independent flow measurements per second.
5. Resizing an image from 1280 to 320 pixels requires corresponding focal-length scaling.
6. Optical-flow quality must be interpreted on PX4’s expected scale; a cap of 100 cannot exercise a 0–255 model fully.
7. `EKF2_OF_N_MIN = EKF2_OF_N_MAX = 0.5` removes quality-dependent noise improvement.
8. A GPS-controlled GNSS-on run does not prove flow-only navigation.
9. A normal failsafe alone may not explain extreme estimator velocity, altitude excursion, or estimator fault flags.
10. One good run is not repeatability. Use multiple repeats for timing/state-sensitive GNSS-denied cases.

---

## Change protocol

Only modify code after the audit identifies:

- the exact defect or missing capability
- the file and function responsible
- evidence that the proposed edit affects the effective runtime path
- a rollback method
- a test that fails before and passes after

Before editing:

```bash
git status --short
```

Prefer small commits or patches grouped by one purpose.

After editing:

1. run syntax/static checks;
2. run a dry-run/config rendering check;
3. run the smallest smoke test;
4. inspect effective commands;
5. run the targeted experiment;
6. compare against the preserved reference;
7. update the phase document and `docs/PROJECT_LOG.md`;
8. never overwrite accepted reference runs.

---

## Required final response format

Use this structure for every substantial audit:

```markdown
# DATABOSS Audit — <scope>

## Verdict
One clear paragraph. State whether the setup is valid, invalid, confounded, or incomplete.

## System path actually used
requested → consumed → effective → observed

## Evidence table
| Item | Requested | Consumed by | Effective value | Evidence | Status |

## Confirmed findings
Numbered, with file/function/log evidence.

## Unresolved risks
Only items not yet proven.

## Experiment validity
Accepted / Rejected / Confounded / Transport-only / Incomplete / Invalid setup.

## Root-cause ranking
Confirmed, highly likely, possible, not causal.

## Minimum next experiment
Exactly one primary variable, exact config changes, preconditions, and safety containment.

## Acceptance gates
Observable pass/fail conditions.

## Files to change
Only after audit proof.

## Commands
Copy-paste commands, with working directory and user assumptions.

## Documentation updates
Phase file, project log, scenario notes, comparison notes.
```

Keep facts, inferences, and proposals visibly separate.

---

## Quick invocation prompts

### Full repository and setup audit

```text
Use the DATABOSS Code & Setup Auditor skill. Perform a read-only audit of the current repository, PX4 environment, scenario-to-runner configuration flow, latest run evidence, and documentation. Do not change code. Produce the requested/consumed/effective/observed table and one minimum next experiment.
```

### Audit one failed run

```text
Use the DATABOSS Code & Setup Auditor skill. Audit this run against its scenario, runner command, effective PX4 parameters, sensor bridge logs, ULog fusion state, and Gazebo truth. Classify it as accepted, rejected, confounded, transport-only, incomplete, or invalid setup.
```

### Audit a proposed patch

```text
Use the DATABOSS Code & Setup Auditor skill. Trace whether this patch affects the actual runtime path. Identify hidden defaults, CLI overrides, frame/time/unit risks, rollback steps, and a before/after test that isolates the change.
```
