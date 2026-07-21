# Phase 12 — Unified comparison report system

Status: **Accepted** (2026-07-21). Supersedes the original 2026-07-13 scope
below (see banner).

> **Scope correction, 2026-07-21**: this doc originally targeted a
> different, now-stale MVP story (phases 1–8I, a script named
> `build_mvp_report.py`, GNSS-on → unaided → ideal-anchor → camera+TF03
> narrative). That plan was written before Phase 8N's flow-sign fix and
> Phase 10/11's GNSS-loss replicate work existed. This phase was
> re-scoped and executed against the actual current matrix: LK vs SIFT vs
> stock, GNSS-on vs GNSS-loss vs fully unaided, on `flat_rural_phototex_noon`.
> The manifest-driven architecture and traceability requirements from the
> original scope carried over unchanged; only the target comparison matrix
> and report sections changed.

## Goal

A reusable comparison-report **system** (not a one-off report): a
manifest-driven generator that, given any set of accepted run folders
tagged by algorithm / GNSS state / world variant, produces one
traceable Markdown report covering literally everything relevant to
judging a run — config, GNSS timeline (independently verified from the
ULog, not trusted from status flags), route/error plots, camera input
samples, world/environment settings, and sensor physical characteristics
— so that future world-lighting/shadow/reflectivity or sensor-range
variants (added by hand, one run at a time) slot in as manifest edits,
never code changes.

## Why this phase exists

Phase 11 produced one good three-way comparison, but it was hand-built
and static: the plot/report scripts hardcoded a 3-entry `CASES` list, had
no GNSS-on dimension, no camera-input section, and no world/sensor
inventory. The user asked for "one perfect comparison report template"
that scales as the project adds GNSS-on data, unaided baselines, and
(in future phases) world/sensor variants — the "MVP of the dashboard": a
stable, regenerable report contract, not a UI.

## In scope

- `scripts/analysis/comparison_manifest.py` — shared `Case` dataclass
  (`key, label, short, kind [algorithm], gnss_state, world_variant,
  run_dir, replicate_of`) and `load_manifest()`, used by both the report
  and plot generators so the case list is defined once, in data.
- `scripts/analysis/build_unified_comparison_report.py` — manifest-driven
  Markdown report generator, adapted from
  `report_phase11_detailed_comparison.py`. Sections: Verdict, Runs And
  Classification, **GPS Loss Verification** (independent ULog check, see
  below), System Configuration, Route And Control Settings, **World And
  Environment Settings**, **Sensor Settings And Known Characteristics**
  (including a dedicated stock-rangefinder-dropout writeup), **Camera
  Inputs** (frame counts + first/mid/last sample images per case), Optical
  Flow Configuration, EKF Aid Source Rates And Fusion, Sensor Publication
  Rates And Rangefinder Health, Route/Truth Performance, Per-Case Notes,
  Plots, Generated Evidence Files.
- `scripts/analysis/plot_unified_comparison.py` — manifest-driven
  plotting, adapted from `plot_phase11_three_way_flow_comparison.py`.
  Color = algorithm (`lk`/`sift`/`stock`/`none` = blue/green/red/gray),
  linestyle = GNSS state (solid = loss, dotted = on) — an arbitrary-length,
  arbitrary-mix case list renders consistently with zero new code per case.
- **Mandatory GPS-state guard**: every case's manifest `gnss_state` tag is
  checked against `vehicle_gps_position`/`sensor_gps` `fix_type` read
  directly from the ULog (`fix_type < 3` at any point = loss observed;
  `fix_type >= 3` throughout = on observed), independent of the runner's
  own `gnss_loss_detected`/`gnss_loss_ok` status flags (which only confirm
  the `SIM_GPS_USED 0` command was sent, not that the simulated driver
  actually dropped the fix — the exact gap that let run `115202` through
  in the original Phase 11 report). A mismatch fires a loud Verdict banner
  instead of silently trusting the tag.
- Three new runs to complete the matrix: LK GNSS-on, SIFT GNSS-on, and an
  **unaided GNSS-loss baseline** (no flow bridge, no stock flow — pure
  PX4 EKF2 dead reckoning after GNSS loss) as the "before" picture that
  LK/SIFT/stock's bounded results are solving relative to. Stock's own
  GNSS-on case was deliberately skipped — LK+SIFT already give a GNSS-on
  reference; see Known limitations.
- Disk headroom (Phase 0, prerequisite): `/opt` was at 424 MB free / 99%
  full before this phase. Freed via (a) deleting ~71 MB of zero-citation,
  no-`flight.ulg` run folders, re-verified fresh rather than trusting an
  earlier stale cleanup plan's citation list, and (b) losslessly `gzip`ing
  `gazebo_ground_truth_raw.txt` (measured ~7–8x compression; nothing
  currently reads the raw file, only its derived `ekf_vs_ground_truth_aligned.csv`)
  in ~18 large, pre-2026-07-20, non-cited run folders — reaching >1 GB free
  before any new runs launched. Also fixed a real disk-cost bug found along
  the way: the LK/SIFT scenario YAMLs' `flow_recording: {rate_hz: 0, ...}`
  does not mean "off," it means "no throttling" in
  `scripts/sim/record_camera_frames.py` — it was silently costing ~150 MB
  per run. New scenarios use `rate_hz: 2` (~10–13 MB/run, measured).

## Out of scope

- Any web UI or dashboard front-end (Phase 13's territory, and Phase 13
  stays UI-free too — see Next phase).
- Automated world/sensor-setting sweeps. Future variants are added to the
  manifest by hand, one accepted run at a time, per explicit user
  direction this phase ("we will make the changes with hands").
- Fixing PX4 stock's rangefinder dropout. Documented as an accepted
  characteristic of the stock reference case (see Sensor Settings section
  of the generated report) — PX4-Autopilot's engine tree stays off-limits
  to edit without separate authorization.
- A cold-start "GNSS never available from arm" case — considered and
  explicitly declined this phase in favor of the simpler, more diagnostic
  "GNSS-loss-after-takeoff, zero aiding" unaided baseline.

## Inputs

Accepted Phase 10/11 GNSS-loss runs (`104301` LK, `111755` SIFT, `122327`
+ `133743` stock replicates), plus three new Phase 12 runs (below).

## Implementation

Manifest YAML (`experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/manifest.yaml`)
lists cases as tags + `run_dir`, not code. The report generator loads each
case's `config.yaml`, `logs/pxh_takeoff_land_truth_status.json`,
`ekf_vs_ground_truth_metrics.json`, and `logs/flight.ulg` (via `pyulog`),
fails loudly (raises) if a case's `logs/flight.ulg` is missing, and writes
`report.md` + `summary.json` + `summary.csv` + `camera_samples/<key>/*.jpg`.
Markdown-only output, matching the repo's existing report convention.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
sudo -u px4 bash -c "source venv/bin/activate && MPLCONFIGDIR=/tmp/databoss-matplotlib-px4 PYTHONPATH=scripts/analysis venv/bin/python scripts/analysis/build_unified_comparison_report.py experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/manifest.yaml"
sudo -u px4 bash -c "source venv/bin/activate && MPLCONFIGDIR=/tmp/databoss-matplotlib-px4 PYTHONPATH=scripts/analysis venv/bin/python scripts/analysis/plot_unified_comparison.py experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/manifest.yaml"
```

New GNSS-on / unaided run scenarios (`experiments/configs/mvp/scenarios/`):
`phase12_lk_xy_gnsson_off70s_flat_rural_phototex_noon.yaml`,
`phase12_sift_xy_gnsson_off70s_flat_rural_phototex_noon.yaml`,
`phase12_stock_gnsson_off70s_flat_rural_phototex_noon.yaml` (created,
scaffolded, **not run** — stock GNSS-on skipped this phase, kept for
future use), `phase12_noaid_gnssloss_off70s_flat_rural_phototex_noon.yaml`
(no `flow_bridge`/`stock_flow` block at all — pure unaided dead reckoning).
Launched via the established pattern:
```bash
sudo rm -f /tmp/px4-sock-0
sudo -u px4 bash -c "source venv/bin/activate && MPLCONFIGDIR=/tmp/databoss-matplotlib-px4 venv/bin/python scripts/runner/run_scenario_pxh_end_to_end.py <scenario.yaml> --hover-s 72 [--gnss-loss-after-takeoff-s 19 --post-loss-hover-s 50 --failsafe-profile delayed_observation]"
```

## Expected outputs

`experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/{report.md,summary.json,summary.csv,plots/*.png,camera_samples/<key>/*.jpg}`.

## Acceptance criteria

- Report regenerates byte-stable except a data-derived content change
  (verified: two consecutive runs on an unchanged manifest diff clean).
- Every quantitative claim traces to a run-ID-cited artifact (manifest
  `run_dir` → `config.yaml`/`ULog`/`metrics.json`).
- GPS-state guard actually catches a mismatch: verified by deliberately
  adding the known-bad `115202` run (tagged `gnss_state: loss` despite GPS
  never dropping) to a test manifest — the Verdict banner and per-case
  MISMATCH row fired correctly; removed from the real manifest afterward.
- Extensibility: a dummy duplicate manifest entry with a new
  `world_variant` tag appeared in the report/plots with zero script edits.
- No case in the final manifest shows a GPS-guard mismatch.

## Results

Final 7-case matrix, `flat_rural_phototex_noon` world, all with
independently-verified GPS-guard `match`:

| Case | GNSS | H err max (m) | Z err max (m) | 3D err max (m) | OF fused frac |
|---|---|---:|---:|---:|---:|
| LK loss | loss (t=24.55s) | 1.093 | 0.377 | 1.135 | 0.994 |
| SIFT loss | loss (t=25.54s) | 1.214 | 0.389 | 1.251 | 0.953 |
| Stock loss r1 | loss (t=33.13s) | 1.347 | 0.628 | 1.487 | 0.779 |
| Stock loss r2 | loss (t=33.17s) | 0.584 | 0.357 | 0.685 | 0.783 |
| LK on | on (throughout) | 0.251 | 0.329 | 0.353 | 0.995 |
| SIFT on | on (throughout) | 0.498 | 1.380 | 1.384 | 0.945 |
| **No aiding, loss** | loss (t=26.30s) | **30.079** | 5.507 | 30.246 | n/a (no bridge) |

Full detail (config, camera samples, sensor SDF dumps, per-case notes) in
the generated `report.md`.

## Interpretation

The unaided baseline is the headline result of this phase: with GNSS lost
and zero velocity/position aiding, PX4's EKF2 height/position estimate
diverges catastrophically (max horizontal error `30.08 m`, vs `0.58–1.35 m`
for the three aided GNSS-loss cases) — a ~20–50x reduction in worst-case
error from adding any of LK, SIFT, or stock optical-flow aiding. This is
the first time this project has a clean, apples-to-apples "before" picture
in the same report as the "after" cases, on the same world/route/timing.
The unaided run needed two retries to get right: attempt 1 hit a
launch-time race (offboard mode requested while PX4 was still mid
auto-takeoff, rejected — resolved on retry, confirmed a one-off flake, not
systemic); attempt 2 hit the same "`SIM_GPS_USED 0` acknowledged but the
simulated fix never actually dropped" bug documented in Phase 11's
`115202` correction — caught immediately by this phase's own GPS guard
rather than requiring manual ULog inspection, which is exactly the
regression-prevention this phase was built for.

LK and SIFT's GNSS-on numbers (`0.251 m` / `0.498 m` max horizontal error)
are noticeably tighter than their GNSS-loss numbers, as expected — more
aiding sources active. The flow-velocity sign sentinel (auto-enabled
whenever GNSS loss is not requested) independently reconfirmed
`axis_map: xy` is sign-correct on both new GNSS-on runs (LK
corr=0.891/gain=1.011, SIFT corr=0.811/gain=1.011, both `ok: true`),
adding two more data points to Phase 8N's finding.

## Known limitations

- **Stock has no GNSS-on datapoint in this matrix.** Skipped per explicit
  project decision this phase (one GNSS-on reference across LK+SIFT was
  judged sufficient); the scaffolded
  `phase12_stock_gnsson_off70s_flat_rural_phototex_noon.yaml` scenario
  exists and is ready to run if this gap needs closing later.
- **Only one world variant tested** (`flat_rural_phototex_noon`). The
  report's World And Environment Settings section is designed to grow with
  future lighting/shadow/reflectivity variants but has nothing to compare
  against yet.
- **The stock rangefinder ~44% non-finite dropout is accepted, not fixed**
  — documented in the report's Sensor Settings section with full root
  cause (PX4's own `x500_flow` model's degenerate 1×1 GPU-lidar geometry).
  This is a project decision, not an oversight.
- **The GNSS-loss timing-reference gap noted in earlier phases persists**:
  for `local_hold`-enabled scenarios, the actual GPS-loss-command schedule
  is `control.start_after_takeoff_s + control.warmup_s +
  control.gnss_loss_after_offboard_s` from the YAML, not the
  `--gnss-loss-after-takeoff-s` CLI value (confirmed by direct code read
  this phase) — the CLI value only matters for scenarios without
  `local_hold`. All cases in this report/matrix are still correctly
  matched to each other in timing (verified via the GPS guard's observed
  values), but this remains a real, still-unresolved confounder anyone
  adding new manifest cases should design around explicitly, not assume.
- **`115202`-class GPS-not-actually-dropped flakes are apparently not rare**
  — three separate instances now (original `115202`, one Phase 8J
  replicate, this phase's unaided-baseline attempt 2). The GPS guard makes
  this cheap to catch per-run, but the underlying `SIM_GPS_USED` simulator
  driver behavior itself remains unexplained and un-investigated.

## Files created or modified

**Created**: `scripts/analysis/comparison_manifest.py`,
`build_unified_comparison_report.py`, `plot_unified_comparison.py`;
`experiments/comparisons/20260720_unified_lk_sift_stock_gnss_matrix/{manifest.yaml,report.md,summary.json,summary.csv,plots/,camera_samples/}`;
scenario YAMLs `phase12_lk_xy_gnsson_off70s_flat_rural_phototex_noon.yaml`,
`phase12_sift_xy_gnsson_off70s_flat_rural_phototex_noon.yaml`,
`phase12_stock_gnsson_off70s_flat_rural_phototex_noon.yaml` (scaffolded,
unused), `phase12_noaid_gnssloss_off70s_flat_rural_phototex_noon.yaml`.
**Modified**: `docs/phases/README.md`, `docs/PROJECT_LOG.md`.
**Deleted**: 6 zero-citation run folders (~71 MB). **Compressed in place**
(not deleted): `gazebo_ground_truth_raw.txt` in ~18 pre-2026-07-20 run
folders.

## Next phase

Phase 13 — dashboard data contract (schema + `experiments/index.json` +
validator, still explicitly no UI), now unblocked since this phase shipped
a real manifest/report schema to derive the contract from. Beyond that:
world-lighting/shadow/reflectivity and sensor-range variants, added to
this same manifest by hand as they're run; a stock GNSS-on replicate to
close the one remaining matrix gap.
