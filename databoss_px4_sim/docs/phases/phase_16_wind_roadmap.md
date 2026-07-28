# Phase 16 — Wind Roadmap: Crosswind Across Altitude

Status: **In progress** (updated 2026-07-24). Umbrella planning doc for a
3-batch campaign; each batch gets its own lettered phase doc
(`phase_16a_*.md`, `phase_16b_*.md`, `phase_16c_*.md`) written when that
batch actually flies, mirroring the `phase_14a`…`phase_14h`
lettered-sub-phase convention.

## Goal

Answer: does the LK/SIFT GNSS-denied optical-flow stack (proven bounded
across altitude, lighting, and terrain in Phase 14) still hold when a
steady crosswind is added? Test a 3-case matrix (GNSS-on reference, SIFT
flow-aided with GNSS off, unaided baseline with GNSS off) at **2 m/s and
5 m/s** crosswind (revised down from the original 2/7 m/s plan -- see
"Wind speed selection" below) across the same three altitudes Phase 14
already established: **15 m, 35 m, 60 m**.

## Wind speed selection: 7 m/s → 6 m/s → 5 m/s (revised 2026-07-22/23)

The original plan (title above, kept for history) was 2 m/s and 7 m/s.
Debug probes at 15 m found that 7 m/s **reliably triggers an unresolved
PX4 `AUTO_TAKEOFF` setpoint-generation stall**: EKF and Gazebo-truth height
both correctly track ~0 m during the stall (this is not a truth/EKF
mismatch), but `trajectory_setpoint.position[2]` stays pinned near 0
instead of ramping toward the commanded negative-NED altitude, for
anywhere from ~90 s to 220+ s, before suddenly resolving on its own.

Extensive PX4 source-level investigation ruled out several candidate
mechanisms without finding the definitive line of code responsible:
`COM_WIND_MAX`/`wind_limit_exceeded` (disabled by default, not the cause),
`FlightTaskAuto::_checkEmergencyBraking()`, the EKF wind-state control
flag, and the mission-item global-position "reached" check. The stall
probability rises with wind speed but is **not a hard deterministic
cutoff** -- a delayed-wind mechanism (apply a wind force only after
takeoff, via Gazebo's `ApplyLinkWrench` system rather than the static
`WindEffects` world plugin) was built and validated
(`scripts/sim/apply_delayed_wind_force.py`) as a workaround, but was
rejected per project decision in favor of simply choosing wind speeds
that don't trigger the stall in practice. That script remains in the repo
but is not part of the active plan.

Bisection at 15 m: 2/4/5 m/s clean across debug probes; 6 m/s looked clean
on a single debug probe but then **failed 3 of 4 real batch attempts**
(the single clean probe was likely non-representative, not proof of
safety -- a lesson about trusting single debug runs over repeated batch
evidence); 7 m/s stalled consistently. Final decision: **2 m/s (low) and
5 m/s (high)** as the roadmap's two test speeds, replacing 6/7 m/s
everywhere (world YAMLs, scenario YAMLs, batch YAMLs).

Two unrelated runner bugs were found and fixed during this investigation,
both in `scripts/runner/auto_takeoff_land_pxh_truth.py` (shared by every
phase, not wind-specific):

1. **Takeoff-detection blind spot**: `wait_for_airborne_duration()` only
   gated on the land-detector flag, so a vehicle that never left the
   ground (stuck in the `AUTO_TAKEOFF` stall above) could still be
   counted as "airborne." Fixed by adding a `min_altitude_m` gate (0.5 m)
   sourced from `vehicle_local_position`, plus an `OFFBOARD`-mode
   confirmation retry loop (3 attempts) after the takeoff wait, so a run
   that never actually reaches `OFFBOARD` aborts loudly instead of
   silently streaming setpoints into a mode PX4 never entered.
2. **GCS-connection-loss failsafe** (`NAV_DLL_ACT`/`COM_DL_LOSS_T`):
   caused an unwanted mid-flight `AUTO_RTL` unrelated to the GNSS-loss
   experiment itself. Fixed by adding `NAV_DLL_ACT: 0` to every Phase 16
   scenario's `extra_px4_params`.

Known residual limitation: neither runner bug's fix has a `try`/`finally`
around the abort path, so a run that raises `RuntimeError` (takeoff never
confirmed, or `OFFBOARD` never confirmed) can leave orphaned PX4/Gazebo
processes that block the next case's ports (observed once, worked around
operationally by killing stale processes before the next launch -- not
yet fixed at the source).

## Why this phase exists

`docs/phases/phase_14_difficulty_roadmap.md:49-51` explicitly deferred
wind out of the Phase 14 roadmap: `build_gazebo_world.py` raised on
`wind.enabled=true` and no vehicle model had `<enable_wind>` set — wind was
never physically wired into Gazebo at all, "a future phase, not one of
those 8 batches." This phase is that follow-up: it both implements real
wind physics (previously out of scope) and runs the resulting test matrix.

## Wind mechanism (new engineering, not just config)

- **World builder** (`scripts/worlds/build_gazebo_world.py`): removed the
  `wind.enabled=true` guard, added `wind_config()`/`wind_block()` which
  emit a world-level `<wind><linear_velocity>...</linear_velocity></wind>`
  element plus a `gz-sim-wind-effects-system` (`gz::sim::systems::WindEffects`)
  plugin block, only when `world.wind.enabled: true`. New YAML schema
  fields: `mean_mps`, `direction_vector_enu: [east, north]` (unit vector),
  `gusts_enabled` (hard-stops with `ValueError` if true — steady wind only
  is this phase's scope). gz-sim version confirmed 8.14.0
  (`libgz-sim8-wind-effects-system.so` present); `WindEffects` is not in
  the shared `server.config`, so it is emitted per-world-SDF instead.
- **Vehicle**: `Tools/simulation/gz/models/x500_base/model.sdf` `base_link`
  now has `<enable_wind>true</enable_wind>` (submodule file, untracked by
  the main repo — documented here as the durable record of the edit).
  `x500_cam_lidar_down` (the model actually used by every Phase 14/16
  scenario) and `x500_flow` both merge-include `x500` → `x500_base`, so
  this single edit reaches every vehicle variant.
- **Direction**: confirmed via `docs/architecture/frames_and_alignment.md`
  (NED_x/North = ENU_y, NED_y/East = ENU_x) that all three Phase 14
  altitude scenarios fly `vx_m_s: 0.0, vy_m_s: 0.2` (PX4 NED → due East =
  ENU +X). Crosswind is defined as `direction_vector_enu: [0, 1]` (ENU
  north), perpendicular to that flight vector, at both speeds.

## The 3-batch ladder

| # | Phase doc | World / field | Altitude | Cases | Status |
|---|---|---|---|---|---|
| A | `phase_16a_wind_15m` | flat_rural_phototex_noon (240m) | 15m | 6 (2 speeds x 3 cases) | accepted with limitations (2026-07-23) |
| B | `phase_16b_wind_35m` | flat_rural_phototex_noon (240m) | 35m | 6 | accepted with limitations (2026-07-24) |
| C | `phase_16c_wind_60m` | flat_rural_phototex_600m_noon (600m) | 60m | 6 | accepted with limitations (2026-07-24) |

All 3 batches complete. See "Roadmap conclusion" below.

"Accepted with limitations" here means: every case's flight itself is
sound (`ULog flight OK: True`, no `AUTO_TAKEOFF` stall, GNSS on/off state
independently verified against ULog) and the truth-vs-EKF route/drift
comparison is clean evidence -- but most cases in both completed batches
fail one or both of two pre-existing, already-characterized, non-blocking
pipeline gates (rangefinder-vs-height tolerance at higher altitude/longer
climbs, and the flow-velocity-sign correlation sentinel on `body_y`) that
reflect known instrumentation-tolerance quirks, not flight-quality
failures. See each batch's own phase doc for the per-case breakdown.

Each batch is a **config-only drop-in** from the previous once the wind
mechanism is proven at 15 m — same pattern as Phase 14's altitude batches
(2, 3), since world/route/GNSS-timing/flow-tuning are all unchanged from
the matching wind-off Phase 14a/14b/14c scenario, with only the world
reference (now pointing at a wind-enabled world) as the changed variable.

### The 3 cases (per batch, x2 wind speeds = 6 runs)

- **GNSS-on reference** — LK estimator active, GNSS never lost (positive
  control: does wind alone, with full GNSS, perturb the hold?).
- **SIFT flow-aided, GNSS off** — GNSS lost 10s after takeoff, SIFT
  optical-flow is the only aiding (the feature under test).
- **Unaided baseline, GNSS off** — no optical flow, no GNSS after loss
  (negative control: raw dead-reckoning drift under wind, for scale).

Per explicit project decision this phase: **drift is expected and
reported as evidence, not used to suppress reporting or gate a run as
failed** — a driftier unaided run under 7 m/s crosswind is not a bug. What
**is** checked on every single run, no exceptions, is the GNSS on/off
state itself: `build_unified_comparison_report.py`'s `gps_guard` check
independently verifies the manifest's declared `gnss_state` against the
observed ULog GPS fix status and flags a loud `**MISMATCH**` warning (and
`gps_guard_mismatch` in `summary.csv`) if they disagree. Any mismatched
case must be rerun before its batch is accepted.

## Verification before the full 18-run batch

A cheap smoke test was run first (Case A, 15 m, 7 m/s — the strongest
wind, GNSS-on so any deviation is attributable to wind/controller
interaction, not open-loop drift), before committing to all 18 runs.
Command and result: see `phase_16a_wind_15m.md`.

## Reused infrastructure

Every batch reuses, unchanged: `scripts/runner/run_batch_matrix_pxh.py`
(run with `--continue-on-fail` so one case's rejection on a non-blocking
gate doesn't stop the remaining 5), `scripts/analysis/comparison_manifest.py`,
`build_unified_comparison_report.py`, `plot_unified_comparison.py`, and
the manifest-per-comparison-folder pattern
(`experiments/comparisons/<date>_phase16x_.../manifest.yaml`). The
wind-off Phase 14a/14b/14c comparison folders at the same three altitudes
serve as the no-wind control -- not re-run here.

New this phase: `scripts/analysis/plot_route_single_run.py`, a per-run
truth-vs-EKF route plot + automated anomaly checker, run after every
single flight (not just at batch-comparison time). It flags: truth/EKF
NaN gaps, truth position teleports, ground strikes during the airborne
window, GNSS-on course deviation, `nav_state` departures from `OFFBOARD`
without a requested landing, and (added 2026-07-24 during Batch 16B) a
climb-phase EKF-vs-truth height-error transient check -- see Batch 16B's
phase doc for the finding that motivated it. GNSS-loss drift and
failsafe-triggered mode changes are reported as `info:` (expected
evidence), not flagged as `unusual`.

## Roadmap conclusion (2026-07-24)

All 3 batches (15 m, 35 m, 60 m; 2 m/s and 5 m/s each; 18 runs total)
complete, each accepted with limitations. Consistent result across every
altitude: GNSS-on reference stays tight (H mean well under 1 m at every
altitude/speed), SIFT-aided GNSS-loss bounds drift to single/low-double
digits with wind-dependent growth (roughly 2-5x from 2 to 5 m/s), and the
unaided GNSS-loss baseline dead-reckons badly (H max 78-139 m across all
6 unaided runs) -- the LK/SIFT stack proven across altitude/lighting/
terrain in Phase 14 continues to hold under a steady crosswind. See
`phase_16a_wind_15m.md` / `phase_16b_wind_35m.md` / `phase_16c_wind_60m.md`
for full per-batch numbers, and each batch's `experiments/comparisons/`
report for the complete evidence.

Two genuinely new things were found and characterized along the way,
neither of which changes the headline conclusion above: the `AUTO_TAKEOFF`
wind-speed stall (worked around via 2/5 m/s speed selection, not fixed at
the source) and a climb-phase/persistent EKF-vs-truth height-estimation
transient (found in Phase 16b, self-corrects during climbs, does not
self-correct when it coincides with a post-failsafe descent in 16c) --
both documented above and in the per-batch docs, neither affecting the
horizontal-drift conclusions this roadmap set out to test.

## Next phase

None planned yet. A natural follow-up (not in scope here) would be a
second wind direction (headwind/tailwind) or gusty/turbulent wind, both
explicitly deferred by `wind_config()`'s `gusts_enabled` hard-stop; or
characterizing the `AUTO_TAKEOFF` stall / height-transient mechanisms at
the PX4 source level.
