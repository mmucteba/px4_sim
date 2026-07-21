# Phase 14 — Difficulty Roadmap: Flat/Noon/Low-Alt → Dark Terrain @ 60m

Status: **In progress** (2026-07-21). Umbrella planning doc for an
8-batch campaign; each batch gets its own lettered phase doc
(`phase_14a_*.md` … `phase_14h_*.md`) written when that batch actually
flies, mirroring the `phase_08a`…`phase_08n` lettered-sub-phase convention.

## Goal

Prove the LK/SIFT/stock GNSS-denied optical-flow stack (validated in
Phase 12 on the easiest possible condition: flat photo-textured ground,
noon sun, no shadows, 2.5m altitude) survives progressively harder,
combined environmental conditions, ending at the user's real target:
**dark terrain world at 60m altitude**. Each batch changes one variable
(or combines two already-proven-individually variables), so a failure at
any rung is attributable to a specific cause, not a tangle of confounders.

## Why this phase exists

Phase 12's headline result (~20-50x error reduction from optical-flow
aiding vs. unaided dead-reckoning) only holds under the easiest world
condition tested so far. It says nothing about whether the system survives
altitude, dim lighting, or real terrain — let alone all three at once. The
user explicitly asked to go "step by step, adding one by one world
conditions, everything gets harder each time, and their combinations"
rather than jumping straight to the hardest case.

Three facts from this phase's planning research fixed the ladder's shape:

- **Altitude** (`route.altitude_agl_m`) is a proven, working knob — flown
  before up to 80m — but never combined with the full GNSS-loss +
  optical-flow-aiding stack above 10m. Lidar max range is 100m (nominal
  headroom at 60m, but zero margin data); DATABOSS's 3-sample lidar fan
  exists specifically because of a roll-induced `inf` bug at low altitude
  that could resurface differently at altitude. **Batch 1 (14a) removed
  most of the altitude risk** — see "What batch 1 changed" below.
- **Lighting** (dim/overcast: lower sun elevation, reduced ambient) needs
  no new code — `build_gazebo_world.py`'s scene builder already accepts
  arbitrary `sun_direction`/`ambient`/`shadows_enabled`, just no dim preset
  exists yet. True night/low-light rendering is a different, harder problem
  (no camera model has an exposure/gain block) and is explicitly out of
  scope here — "dark" means heavily overcast/dusk-style lighting, not
  literal darkness.
- **Terrain** (the one heightmap/satellite world that exists,
  `serefli_koschisar_web_terrain`) has been flown exactly once, camera-only,
  no GNSS-loss/aiding — and its generator
  (`scripts/worlds/heightmap_to_web_mesh_world.py`) has zero lighting
  support today. Combining "dark" with "terrain" is genuine new code.
- **Wind is not implemented** (`build_gazebo_world.py` raises on
  `wind.enabled=true`) — deferred beyond this roadmap per explicit user
  decision; a future phase, not one of these 8 batches.

This slots into the axes `phase_07d_scenario_world_condition_matrix.md`
already froze (world, route, altitude, lighting, wind, texture, aiding
mode) — this phase is that matrix's "stress matrix" finally being climbed,
not a new schema.

## What batch 1 (14a) changed for every batch after it

Batch 1 wasn't just the 15 m datapoint — getting a *valid* GNSS-loss run at
altitude produced four reusable runner primitives plus two world-level facts
that reshape the rest of the ladder. Read these before planning any later
batch; several later batches are now "drop-in" because of them.

**Runner primitives (apply to every batch, no per-batch work):**
- Scenario YAML is authoritative for GNSS-loss / failsafe (shared resolver
  in `create_run_from_scenario.py`); CLI is an override, not the only source.
- `confirm_gnss_loss()` verifies the sim GPS actually dropped, re-asserts,
  and fails the run loudly if not (kills the silent "115202-class" flake).
  Caught 2 flakes in batch 1 that would otherwise have been silent bad data.
- `wait_for_target_altitude()` cuts GPS only at stable hold altitude, so
  **15/35/60 m need zero timing changes** — this is what makes batches 2 and
  3 pure config edits.
- Universal `extra_px4_params` application in the runner.

**World-level facts that shape later batches:**
- **`EKF2_RNG_A_HMAX=80` anchors absolute height — but only on flat
  ground.** On flat terrain the downward rangefinder *is* the true altitude,
  so raising HMAX let it correct baro drift after GNSS loss (height error
  7.6 m → 0.28 m). This assumption **breaks on the terrain batches (6–8)**,
  where ground elevation varies under the vehicle. Height reference on
  terrain is the single biggest carried-forward open risk (see "Carried
  open risks").
- **Camera footprint vs. field size scales with altitude.** At 60 m the
  downward footprint half-width is ~70 m (hfov 1.74 rad); the 240 m
  `flat_rural_phototex_noon` field (±120 m textured) is fine for a
  near-origin hover but a driftier/unaided run can push the footprint edge
  toward bare background. The existing `flat_rural_phototex_600m_noon` world
  exists for exactly this — **the 60 m batches (3, 5) should fly the 600 m
  field**, not the 240 m one, so texture and finite rangefinder stay under
  the camera regardless of drift.

## The 8-batch ladder (summary)

One new variable isolated per batch where possible; the two riskiest
combinations (altitude reaching 60m, terrain gaining lighting) are each
proven alone before being combined with anything else. Altitude values are
proposed defaults, adjustable by hand at execution time. Detailed
per-batch specs follow the table.

| # | Phase doc | World / field | Lighting | Altitude | New engineering | Isolates |
|---|---|---|---|---|---|---|
| 1 | `phase_14a_altitude_15m` | flat_rural_phototex (240m) | noon (baseline) | 15m | none (produced the 4 primitives) | first altitude step above 2.5m, full stack |
| 2 | `phase_14b_altitude_35m` | flat_rural_phototex (240m) | noon | 35m | **none — config-only drop-in** | altitude trend continues, still flat/bright |
| 3 | `phase_14c_altitude_60m` | flat_rural_phototex **600m** | noon | 60m | none (switch to 600m field) | 60m proven in isolation; lidar headroom check |
| 4 | `phase_14d_dim_lighting` | flat_rural_phototex (240m) | dim/overcast (new preset) | 15m | new world YAML preset (no code) | lighting-only difficulty, safe altitude |
| 5 | `phase_14e_dim_lighting_60m` | flat_rural_phototex **600m** | dim/overcast | 60m | none (reuses #3 + #4) | lighting + altitude combined, still flat |
| 6 | `phase_14f_terrain_baseline` | serefli_koschisar terrain | noon/default | 15–35m | first full-stack terrain run **+ height-reference decision** | terrain-only difficulty |
| 7 | `phase_14g_terrain_dim` | serefli_koschisar terrain | dim/overcast | 15–35m | **port lighting into `heightmap_to_web_mesh_world.py`** (currently none) | terrain + lighting code risk, safe altitude |
| 8 | `phase_14h_dark_terrain_60m` | serefli_koschisar terrain | dim/overcast | 60m | none (reuses #3, #6, #7) | **endgame**: everything combined |

Every batch reruns the LK/SIFT/stock × gnss-loss matrix, a stock replicate
pair, the noaid-gnssloss baseline, and **one** gnss-on reference (LK only —
revised 2026-07-21 from the original two). **Six cases per batch.**

## Per-batch detailed specs

Each spec below is enough to execute the batch: what to copy, the exact knob
changes, what could break at that rung, and the acceptance gate. A batch's
own `phase_14x_*.md` is written only after it flies.

### Batch 2 — `phase_14b_altitude_35m` (config-only drop-in)

- **Prep:** copy the 6 `phase14a_*_alt15m_*.yaml` scenarios to
  `phase14b_*_alt35m_*`; change **only** `route.altitude_agl_m: 15.0 → 35.0`
  and `control.z_m: -15.0 → -35.0`. World, GNSS timing, axis_map, HMAX=80,
  battery params all unchanged. New manifest
  `experiments/comparisons/<date>_phase14b_altitude_35m/`.
- **New engineering:** none. The `wait_for_target_altitude()` gate and
  HMAX=80 already cover 35 m with no tuning — this batch exists to confirm
  the trend, not to solve anything.
- **What could break:** optical flow angular rate scales as v/h, so at 35 m
  the same ground speed produces weaker flow; texture-per-pixel on the 240 m
  field is still adequate at 35 m (footprint half-width ~40 m, inside
  ±120 m). Rangefinder at 35 m is well under the 100 m max.
- **Acceptance:** aided horizontal bounded (trend prediction ~2–2.5 m,
  see below), unaided diverges, GPS guard clean, HMAX height error < ~1 m.
- **Prediction from data:** LK 1.09 m @2.5 m → 1.53 m @15 m; a roughly
  linear-in-altitude read puts 35 m near ~2 m horizontal. Flag it loudly if
  flow degradation makes it grow *faster* than linear — that would be the
  first sign altitude scaling isn't benign and matters for 60 m.

### Batch 3 — `phase_14c_altitude_60m` (altitude endgame, isolated)

- **Prep:** copy batch-2 scenarios to `phase14c_*_alt60m_*`;
  `altitude_agl_m: 60.0`, `control.z_m: -60.0`, **and switch the world to
  `flat_rural_phototex_600m_noon`** (`world.name` + `world.sdf_path`) so the
  ~70 m footprint half-width stays over texture on the larger field.
- **New engineering:** none code-wise, but this batch owes two explicit
  validations: (a) lidar headroom — 60 m vs the 100 m max, first time near
  that regime with the 3-sample fan; check distance-finite fraction doesn't
  collapse. (b) confirm the 600 m field keeps rangefinder finite even for
  the driftier unaided baseline.
- **What could break:** rangefinder approaching range limit / `inf` returns;
  weakest flow of the flat batches; a driftier run leaving texture (mitigated
  by the 600 m field).
- **Acceptance:** aided bounded, unaided diverges, GPS guard clean, lidar
  finite-fraction acceptable. **This is a prerequisite gate for batches 5
  and 8** — the endgame cannot be attempted until 60 m is proven alone.

### Batch 4 — `phase_14d_dim_lighting` (new world preset, no code)

- **Prep:** new world YAML `flat_rural_phototex_dim.yaml` cloned from
  `flat_rural_phototex_noon.yaml`, changing only the `lighting` block:
  reduce `ambient` (`0.55 → ~0.25`), lower/darker `background`, and lower the
  sun elevation (`sun_direction` z-component less steep, e.g.
  `[-0.6, 0.2, -0.55]`). Regenerate the SDF via `build_gazebo_world.py`
  (no code change — the scene builder already accepts these). 6 scenarios
  `phase14d_*_dim_alt15m_*`, altitude back to a **safe 15 m** so lighting is
  the only changed axis vs. batch 1.
- **Shadows decision (bake in):** keep `shadows_enabled: false`. Real
  overcast light is *diffuse* → soft/no shadows, so "overcast" is physically
  shadows-off + low ambient + low sun. This also dodges the documented SIFT
  self-shadow poisoning trap (the noon world turns shadows off for exactly
  this reason). Hard shadows / dusk-with-long-shadows is a *separate* future
  knob, not this batch.
- **What could break:** lower contrast → fewer good corners/features,
  especially for SIFT; watch the OF-fused fraction. If SIFT degrades more
  than LK under dim light, that is a real, expected, publishable finding —
  do not paper over it.
- **Acceptance:** aided bounded under reduced contrast at safe altitude;
  characterize any per-estimator (LK vs SIFT) degradation delta vs. batch 1.

### Batch 5 — `phase_14e_dim_lighting_60m` (dim + altitude, flat ceiling)

- **Prep:** dim world at 60 m on the **600 m** field — i.e. the batch-4
  lighting block applied to a 600 m dim world, batch-3 altitude/field. Build
  `flat_rural_phototex_600m_dim.yaml`, 6 scenarios `phase14e_*_dim_alt60m_*`.
- **New engineering:** none — pure reuse of #3 (60 m + 600 m field) and #4
  (dim preset). **Prerequisite: batches 3 and 4 both Accepted.**
- **What could break:** the two hardest flat-world stressors compounded —
  weakest flow (60 m) *and* lowest contrast (dim). This is the flat-world
  stress ceiling; it de-risks whether the *terrain* endgame's difficulty is
  dominated by lighting+altitude (proven here) or by terrain itself.
- **Acceptance:** aided bounded, or a documented, understood degradation
  attributable to the known altitude+lighting factors (not a new mystery).

### Batch 6 — `phase_14f_terrain_baseline` (first full-stack on terrain)

- **Prep:** `serefli_koschisar_web_terrain` world; 6 scenarios
  `phase14f_*_terrain_alt{15|35}m_*`. First GNSS-loss + optical-flow-aiding
  run ever on real heightmap terrain (previously camera-only, once). Keep
  altitude a safe **15–35 m** so terrain is the only new axis.
- **New engineering — the height-reference decision (central risk):**
  HMAX=80's flat-ground assumption is invalid here — the rangefinder reads
  height *above varying terrain*, not absolute altitude, so blindly anchoring
  absolute height to it will inject the terrain's elevation profile as false
  altitude error. Options to evaluate, in order of preference:
  1. Revert HMAX toward stock (5) and accept baro-driven absolute-height
     drift after GNSS loss — measure how bad it actually is on this terrain's
     relief.
  2. Use PX4 terrain estimation (`EKF2_TERR_*` / terrain-relative height)
     so rangefinder constrains height-above-terrain while a separate state
     carries absolute height.
  3. Keep GPS as height reference longer / hybrid — least preferred, muddies
     the GNSS-denied claim.
  This decision is the batch's real deliverable; the horizontal-aiding number
  is secondary here.
- **What could break:** non-flat ground under the camera changes flow
  parallax; texture quality of the satellite/heightmap imagery is unproven
  for flow; rangefinder relief-induced jumps.
- **Acceptance:** likely **"Accepted with limitations"** — characterize
  height behavior honestly (expect worse than flat), confirm horizontal
  aiding still bounds if terrain texture is adequate, and record the chosen
  height-reference strategy for batches 7 and 8.

### Batch 7 — `phase_14g_terrain_dim` (terrain-lighting CODE)

- **New engineering — biggest code item in the roadmap:**
  `scripts/worlds/heightmap_to_web_mesh_world.py` has **zero lighting
  support**. Port the same `sun_direction` / `ambient` / `background` /
  `shadows_enabled` knobs the flat `build_gazebo_world.py` scene builder
  already exposes into the terrain generator's SDF emission, so the batch-4
  dim preset can be applied to terrain. Validate the generated terrain SDF
  renders with usable downward texture under dim light before flying.
- **Prep:** dim terrain world + 6 scenarios `phase14g_*_terrain_dim_alt{15|35}m_*`,
  reusing the height-reference strategy chosen in batch 6. Safe 15–35 m.
- **Prerequisite:** batches 4 (dim preset) and 6 (terrain full-stack +
  height reference) both Accepted.
- **What could break:** the new lighting code itself (SDF that won't load,
  black renders, texture washed out); dim + terrain-relief flow combined.
- **Acceptance:** dim terrain renders with usable texture; aiding bounded or
  a documented, understood degradation.

### Batch 8 — `phase_14h_dark_terrain_60m` (ENDGAME — the user's target)

- **Prep:** everything combined — `serefli_koschisar` terrain, dim/overcast
  lighting (batch-7 code), **60 m** altitude. 6 scenarios
  `phase14h_*_dark_terrain_alt60m_*`. No new engineering: reuses #3 (60 m
  proven + field/lidar handling), #6 (terrain height reference), #7 (terrain
  lighting code).
- **Hard prerequisite:** batches 3, 4, 6, and 7 each independently
  `Accepted`. This gate is non-negotiable — the whole ladder exists so that
  if this batch fails, the cause is a *combination* effect, every ingredient
  having been proven alone.
- **Acceptance:** aided GNSS-denied flight bounded (or characterized) on dark
  terrain at 60 m — the campaign's headline result.

## Dependency graph

```
14a (done) ──► 14b ──► 14c ─────────────┐
                        │                │
14a ────────────────► 14d ──► 14e        │
                        │                ▼
14c + 14d ──────────► 14e            (60m + dim, flat ceiling)
14a ────────────────► 14f (terrain + height-ref decision)
14d + 14f ──────────► 14g (terrain-lighting CODE)
14c + 14d + 14f + 14g ─► 14h  (ENDGAME: dark terrain @ 60m)
```

Batches 2→3 and 4 can proceed as soon as the previous flat rung is accepted;
6→7→8 are gated on the terrain height-reference decision and the
terrain-lighting code, so the second half is sequential and slower.

## Carried open risks (watch across batches)

1. **Height reference on terrain (batches 6–8).** The #1 risk. HMAX=80 is a
   flat-ground trick; terrain needs a real decision (batch 6). If none of the
   three options in batch 6 gives acceptable absolute height, the endgame's
   "60 m" claim weakens to "60 m above takeoff, drifting with terrain relief"
   — acceptable to state, but must be stated.
2. **Lidar range headroom at 60 m (batches 3, 5, 8).** 60 m vs 100 m max with
   the 3-sample fan; unproven margin. First real check is batch 3.
3. **Per-estimator lighting sensitivity (batches 4, 5, 7, 8).** SIFT is the
   likely first casualty of low contrast; the roadmap keeps LK, SIFT, and
   stock in every batch specifically to catch a ranking change, not to
   assume LK≈SIFT holds as it did at noon.
4. **Terrain texture quality for flow (batches 6–8).** Satellite/heightmap
   imagery has never been used as an optical-flow source here; adequacy is
   an open empirical question first answered in batch 6.

## Status table

| # | Phase doc | Status |
|---|---|---|
| 1 | `phase_14a_altitude_15m` | **Accepted (2026-07-21)** — aided 1.5–2.2 m vs 60 m unaided |
| 2 | `phase_14b_altitude_35m` | **Planned — next; config-only drop-in** |
| 3 | `phase_14c_altitude_60m` | Planned (switch to 600m field; lidar headroom check) |
| 4 | `phase_14d_dim_lighting` | Planned (new dim world preset, no code) |
| 5 | `phase_14e_dim_lighting_60m` | Planned (needs 3 + 4) |
| 6 | `phase_14f_terrain_baseline` | Planned (height-reference decision) |
| 7 | `phase_14g_terrain_dim` | Planned (terrain-lighting code) |
| 8 | `phase_14h_dark_terrain_60m` | Planned (endgame; needs 3, 4, 6, 7) |

## Reused infrastructure

Every batch reuses, unchanged: `scripts/analysis/comparison_manifest.py`
(incl. `open_ulog()` for transparent gzipped-ULog reads),
`build_unified_comparison_report.py`, `plot_unified_comparison.py`, and the
manifest-per-comparison-folder pattern
(`experiments/comparisons/<date>_phase14x_.../manifest.yaml`). The flat
altitude batches (2, 3) are new scenario/manifest YAML only — zero script
edits. Only batches 4 (world preset), 6 (height-reference param), and 7
(terrain-lighting code) touch anything beyond config, and only 7 touches
Python. Same GPS-guard verification and per-run dashboards throughout.

## Disk and time budget

~48 runs across the remaining 7 batches (6 runs/batch), each producing a
`flow_recording` at the Phase-12-fixed `rate_hz: 2` (~10MB/run). Multi-session
effort; batches 6–8 (terrain) are gated on the terrain height-reference
decision and the terrain-lighting code, so the second half is sequential.

**ULog lifecycle** (the biggest disk lever): handled via **gzip-in-place,
not deletion** — `open_ulog()` transparently decompresses `flight.ulg.gz`
on demand so report/plot scripts need no change, and ULogs stay
regenerable (this project has re-run the Phase 12 report 4–5 times).
Policy: gzip a batch's 6 ULogs only after that batch's report is generated
and verified; `df -h /opt` checkpoint before/after every batch; abort
further launches below ~150MB free. **Current free space is tight (~360MB,
100% used) — clear headroom before batch 2 launches.**

## Acceptance criteria (for this umbrella doc)

- All 8 rows exist in the status table above with a named phase doc and a
  detailed per-batch spec (§ "Per-batch detailed specs").
- Each batch, once flown, gets its own `phase_14x_*.md` with real Results/
  Interpretation sections — this doc only holds the plan and rationale.
- No batch skips the independent GPS-guard verification Phase 12 built.
- The endgame batch (8) is not attempted until 3, 4, 6, and 7 are each
  independently `Accepted`.

## Next phase

Batch 2 (`phase_14b_altitude_35m`) — a config-only drop-in per its spec
above. Clear disk headroom first, then it's copy-6-YAMLs + new manifest +
run + report, no code.
