# Phase 14a — Altitude step 1: GNSS-denied optical flow at 15 m

Status: **Accepted** (2026-07-21). Batch 1 of the Phase 14 difficulty
roadmap (`docs/phases/phase_14_difficulty_roadmap.md`).

## Goal

First altitude step above the 2.5 m Phase 12 baseline: rerun the
LK/SIFT/stock GNSS-denied optical-flow comparison, plus one GNSS-on
reference and an unaided baseline, at **15 m** altitude on
`flat_rural_phototex_noon`. Answer: does the aided stack still hold when the
vehicle is 6x higher (and the optical flow correspondingly weaker)?

## Result headline

At 15 m, GNSS lost at stable hover, the aided cases stay bounded and the
unaided baseline diverges — the same story as 2.5 m, now proven at altitude:

| Case | GNSS | Horizontal err max (m) | Height err max (m) | OF fused frac |
|---|---|---:|---:|---:|
| LK loss | loss | 1.53 | 1.01 | 0.994 |
| SIFT loss | loss | 1.73 | 1.04 | 0.957 |
| Stock loss r1 | loss | 2.20 | 0.24 | 0.802 |
| Stock loss r2 | loss | 1.60 | 0.90 | 0.965 |
| LK on | on | 0.13 | 0.85 | 0.996 |
| **No aiding** | loss | **60.24** | 1.62 | n/a |

Optical-flow aiding (LK/SIFT/stock) holds horizontal error to 1.5–2.2 m vs
**60.24 m unaided** — a ~30–40x reduction, consistent with the 2.5 m Phase 12
result (which was ~20–50x). Height stays bounded (~1 m) for every case,
including unaided, because the rangefinder now anchors absolute height (see
the EKF2_RNG_A_HMAX finding below). Full detail:
`experiments/comparisons/20260721_phase14a_altitude_15m/report.md`.

## Why this batch mattered beyond the number: four robust runner fixes

Getting a *valid* 15 m GNSS-loss run surfaced four separate problems, each
fixed with a reusable primitive rather than a per-run patch. These de-risk
the entire altitude roadmap (35 m, 60 m) and every future scenario.

1. **Scenario YAML was not authoritative for GNSS loss / failsafe.** Only
   the `--gnss-loss-after-takeoff-s` / `--failsafe-profile` CLI flags were
   read; a `gnssloss` scenario launched without them silently ran GNSS-on
   (documented trap, phase_10). Fix: `resolve_gnss_loss_after_takeoff_s` /
   `resolve_failsafe_profile` in `scripts/runner/create_run_from_scenario.py`,
   used by both `auto_takeoff_land_pxh_truth.py` (direct path) and
   `run_scenario_pxh_end_to_end.py` (wrapper) so they never disagree. YAML
   is authoritative; CLI stays an explicit override. Status JSON now records
   `gnss_loss_source` / `failsafe_profile_source`.

2. **The `SIM_GPS_USED 0` flake was silent.** PX4 acknowledges the param
   (`curr: 10 -> new: 0`) but the Gazebo GPS bridge intermittently keeps
   publishing a nominal fix — the recurring "115202-class" flake. The old
   `gnss_loss_detected` only grepped the console for the command text, so a
   flaked run became a silent GNSS-on masquerade. Fix: `confirm_gnss_loss()`
   polls `vehicle_gps_position` after the cut, re-asserts `SIM_GPS_USED 0`
   up to 5x, and if the sensor never drops (`fix_type<3` / `satellites<=0`)
   fails the run loudly (`gnss_loss_ok=False -> accepted=False`). Two flakes
   in this batch (stock r1, noaid) were caught this way and cleared on
   retry. Status JSON records `gnss_loss_verified` + observed fix_type.

3. **Loss timing tuned for a 2.5 m takeoff cut GPS mid-climb at 15 m.** The
   fixed `gnss_loss_after_offboard_s` fired while the vehicle was still
   ascending (~3 m/s); the EKF lost its velocity reference mid-climb and the
   vertical-velocity estimate blew up to −48 m/s, driving a runaway to
   **279 m**. Fix: `wait_for_target_altitude()` cuts GPS only once the
   vehicle is stable at the commanded hold altitude, with the old fixed time
   as a floor — so one scenario works at 2.5/15/35/60 m with **no
   per-altitude timing tuning**. Status JSON records
   `pre_loss_altitude_reached`.

4. **Absolute height was unobservable above 5 m after GNSS loss.** With
   `EKF2_RNG_A_HMAX = 5` (stock), above 5 m the rangefinder only constrains
   height *above terrain*, not absolute height; after GNSS loss the
   absolute-height and terrain estimates drifted up together on baro (EKF
   believed 15 m, ground truth 7 m, rangefinder correctly read 7 m —
   ~7.6 m median height error). Fix: raise `EKF2_RNG_A_HMAX` to 80 (valid on
   flat terrain, below the 100 m lidar max) via a **universal**
   `extra_px4_params` application — moved out of the flow-bridge-only block
   in the runner so it applies to every scenario. Height error dropped from
   7.6 m median to **0.28 m**.

Also added, addressing an observed confound: **battery-health params**
(`SIM_BAT_DRAIN: 3000, SIM_BAT_MIN_PCT: 95`). The SITL battery drains full→
empty in 60 s (`SIM_BAT_DRAIN`), and flaked runs correlated with
"battery unhealthy" failsafe warnings. Root cause of the flake is most
likely a stale/degraded Gazebo instance (which publishes stale battery data
*and* ignores `SIM_GPS_USED 0`), not battery charge per se — the stock r1
retry recovered on a plain fresh run with no battery param. The battery
params remove the confound going forward; the fail-loud GPS gate + retry is
what actually recovers the flakes. A blanket `pkill gz` was deliberately
avoided (would kill concurrent parallel-session runs).

## Matrix

Six cases (one GNSS-on reference only — LK — per project decision this
batch; every roadmap batch keeps a single GNSS-on case, not two). Two stock
replicates kept per the Phase 11/12 convention. Manifest:
`experiments/comparisons/20260721_phase14a_altitude_15m/manifest.yaml`.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
sudo rm -f /tmp/px4-sock-0
# No --gnss-loss-after-takeoff-s / --failsafe-profile needed: the scenario
# YAML is now authoritative. --post-loss-hover-s sets the post-loss window.
sudo -u px4 bash -c "source venv/bin/activate && MPLCONFIGDIR=/tmp/databoss-matplotlib-px4 venv/bin/python scripts/runner/run_scenario_pxh_end_to_end.py experiments/configs/mvp/scenarios/phase14a_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_alt15m.yaml --hover-s 90 --post-loss-hover-s 50"
# Report (plots first, then report):
sudo -u px4 bash -c "source venv/bin/activate && MPLCONFIGDIR=/tmp/databoss-matplotlib-px4 PYTHONPATH=scripts/analysis venv/bin/python scripts/analysis/plot_unified_comparison.py experiments/comparisons/20260721_phase14a_altitude_15m/manifest.yaml && venv/bin/python scripts/analysis/build_unified_comparison_report.py experiments/comparisons/20260721_phase14a_altitude_15m/manifest.yaml"
```

## Interpretation

The GNSS-denied optical-flow stack survives a 6x altitude increase with
only a modest error growth (LK 1.09 m @2.5 m → 1.53 m @15 m horizontal). The
decisive enabler at altitude is treating the downward rangefinder as the
absolute height reference (`EKF2_RNG_A_HMAX`), which is valid on flat
terrain but will need revisiting on the terrain batches (6–8) where the flat
assumption breaks — itself an expected finding.

## Known limitations

- `EKF2_RNG_A_HMAX = 80` assumes flat terrain (true for `flat_rural_*`).
  The terrain batches must revisit height reference; this is flagged in the
  roadmap.
- Stock rangefinder dropout persists (r1 distance-finite fraction 0.886) —
  the accepted PX4 `x500_flow` 1×1 lidar characteristic, unchanged.
- GPS-drop flake root cause (stale Gazebo instance) is mitigated (fail-loud
  gate + retry, battery params) but not eliminated at the PX4/Gazebo source;
  the engine tree stays off-limits.
- The unaided baseline (60.24 m) is `accepted:false` by the divergence gate
  (correctly) and was postprocessed/aligned manually, per the established
  pattern for scientifically-valid divergent runs.

## Files created or modified

**Created**: 6 `phase14a_*_alt15m_*.yaml` scenarios;
`experiments/comparisons/20260721_phase14a_altitude_15m/` (manifest, report,
plots, camera_samples, summary.*); this doc.
**Modified (reusable runner primitives)**:
`scripts/runner/create_run_from_scenario.py` (resolvers),
`scripts/runner/auto_takeoff_land_pxh_truth.py` (`confirm_gnss_loss`,
`wait_for_target_altitude`, universal `extra_px4_params`, fail-loud gate,
new status fields), `scripts/runner/run_scenario_pxh_end_to_end.py`
(resolve + forward). `scripts/analysis/comparison_manifest.py` (`open_ulog`
for transparent gzipped-ULog reads).

## Next batch

Phase 14b — 35 m. Same six-case matrix, `route.altitude_agl_m: 35.0`,
`control.z_m: -35.0`; the altitude gate and HMAX=80 already cover it with no
new tuning.
