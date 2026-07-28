# DATABOSS MVP Conditions

**Deprecated (Phase 17B, 2026-07-24).** These presets were never wired
into the actual world generator (`scripts/worlds/build_gazebo_world.py`) -
a Phase 7D scenario that references them says so directly
("Condition references are tested. Physical Gazebo condition application
is not wired yet."), and their schemas are structurally incompatible with
what the generator actually reads (e.g. `wind/*.yaml` here uses
`wind.mean_velocity_m_s: {x,y,z}`, while the real, working schema is
`wind.mean_mps` scalar + `wind.direction_vector_enu: [east, north]`).
"Wiring it up" would be a rewrite, not an adapter.

Real physical condition control instead happens via the world YAML schema
`build_gazebo_world.py` actually consumes (`experiments/configs/mvp/worlds/
*.yaml`: `texture`, `lighting`, `wind`, `objects`) and a scenario's
`world.sdf_path` pointing at the resulting generated SDF, with
`condition_is_physical: true` marking that it's real. See
`docs/architecture/mvp_backend_contract.md` and Phase 17B's audit notes in
`docs/phases/` for the full finding.

Files below are kept for history, not deleted, but should not be read by
any new scenario-editor or dashboard code path.

---

This folder stores reusable environment condition presets.

Conditions are not full scenarios.

A full scenario combines:

world + route + vehicle + GNSS profile + failsafe profile + aiding mode + conditions

Current categories:

- lighting
- wind
- texture
- disturbance

Phase 7D defines the condition structure.

Phase 8A should use only easy capability conditions first.
