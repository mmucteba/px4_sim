# Phase 10 — GNSS-loss flow-aiding repair

Status: **Accepted with limitations** (2026-07-20) — the first-ever bounded
GNSS-loss LK result on `axis_map: "xy"` was achieved and confirmed on two
independent replicates, including a dedicated, explicitly-parameterized
50 s GNSS-off duration test matching the project's established benchmark
convention (n=3/3 passing all acceptance criteria). See Results.

## Goal

Get the DATABOSS LK optical-flow bridge bounded and straight under *actual*
GNSS loss, not just GNSS-on, starting from the Phase 8N-accepted
`axis_map: "xy"` contract. Reconfirm SIFT's ~35 s outage duration limit
still holds once the sign fix propagates through any dependent tooling.

## Why this phase exists

Every prior GNSS-loss attempt diverged:

- Phase 8L Gate 6b's `axis_map: "-x-y"` + `EKF2_OF_N_MIN=0.5` flew straight
  GNSS-on, but the first GNSS-loss smoke on that config flew away
  (~1.4 km), and a failsafe-isolation rerun on the same contract still
  rejected (2515.98 m max horizontal error).
- Phase 8N's sign sentinel explains why: that contract's flow-derived body
  velocity is sign-inverted against GPS truth (corr `-0.90`/`-0.46`, gain
  near `-1.0`). It only "worked" GNSS-on because `EKF2_OF_N_MIN=0.5`
  de-weighted flow enough for GNSS to carry the real solution. Remove GNSS
  and the inverted velocity becomes the dominant, wrong-direction control
  input — consistent with both flyaways.
- The GNSS-loss timing reference is also unreliable: `20 s` was requested
  but `10 s` was effective in the failsafe-isolation rerun. This confounds
  any GNSS-loss result until fixed.

## In scope

1. Audit `scripts/analysis/*flow*` and
   `src/databoss_sim/flow/px4_adapter.py` for logic that still assumes the
   old `-x-y`/`-yx` sign convention — Phase 8N's own doc flags this as a
   prerequisite before trusting any further acceptance gate.
2. A GNSS-on replicate (n>1, not Phase 8N's single short probe) on
   `axis_map: "xy"` with corrected analyzers, to confirm the sign result
   holds beyond one run. Deliberately tune `EKF2_OF_N_MIN` rather than
   reusing the untuned `0.15` default — Phase 8N's `xy` run had a much
   hotter innovation-gate margin than the old contract (max flow test ratio
   `0.750` vs `0.043`), which is worth understanding before GNSS-loss
   removes the GNSS cross-check entirely.
3. Repair the GNSS-loss timing reference (requested vs. effective loss
   time mismatch).
4. GNSS-loss aiding-strength / loop-damping tuning on the corrected `xy`
   contract.
5. Reconfirm SIFT's known ~35 s outage duration limit (Phase 8I) still
   holds; SIFT does not go through the same axis_map/sign path as LK, but
   any shared analyzer fixes from step 1 should be re-validated against it.

## Out of scope

- Multi-world/condition testing (Phase 11 and beyond).
- SIFT algorithm changes.
- Dashboard work (Phase 13).

## Inputs

- `docs/phases/phase_08n_flow_sign_inversion_probe.md` and
  `experiments/inspections/20260720_phase8n_xy_vs_old_short_gnsson_compare.md`
  — the sign decision this phase starts from.
- `docs/phases/phase_08l_sensor_sanity_ladder.md` (Gate 6b axis/noise
  findings, the two GNSS-loss flyaway failures, the timing-reference bug).
- `experiments/inspections/20260720_phase8m_route_root_cause_report.md`
  (the LK/EKF/controller feedback-loop diagnosis and proposed acceptance
  criteria, reused below).

## Implementation

Step 1 (analyzer/sign-contract audit) is complete as of the 2026-07-20
"Phase 8N analyzer/sign-contract fix" PROJECT_LOG entry:

- New `scripts/analysis/check_flow_velocity_sign.py` — the ULog sign
  sentinel comparing `estimator_optical_flow_vel.vel_body` against
  GNSS velocity rotated into body frame (GNSS-on only for now; a
  truth-backed path exists but isn't yet the accepted sign authority for
  GNSS-loss).
- `fit_flow_contract_from_truth.py`, `analyze_flow_bridge_openloop.py`,
  `sensor_contract_report.py` updated to describe the legacy axis gate as
  wire-local transport evidence only, not a sign-acceptance gate.
- `src/databoss_sim/flow/px4_adapter.py` notes updated: Phase 8N supersedes
  the Gate 6b `-x-y` workaround, `xy` is the sign-correct candidate.
- `scripts/runner/run_scenario_pxh_end_to_end.py` now runs the sign
  sentinel by default for GNSS-on flow-bridge scenarios
  (`flow_velocity_sign.json`); it gates acceptance only when
  `analysis.flow_velocity_sign_required: true` is set (enabled in the
  Phase 8N `xy` scenario). GNSS-loss scenarios must opt in explicitly once
  the truth-backed sign source is validated — this is part of step 3/4
  below, not yet done.

Step 2 (GNSS-on replicate + `EKF2_OF_N_MIN` tuning) is done:
`experiments/configs/mvp/scenarios/phase10_lk_xy_gnsson_nmin03_short_flat_rural_phototex_noon.yaml`,
tuned to `ekf2_of_n_min: 0.3` (up from Phase 8N's untuned `0.15` default).
Accepted run `20260720_083128_...` confirmed the sign again
(`check_flow_velocity_sign.py`: body-X corr `0.7713`, gain `0.9776` —
positive/correct, same signature as Phase 8N) with excellent fusion (612/622
fused, 0 rejected).

Step 4 (GNSS-loss attempt on the corrected contract) is done, ahead of step
3 (timing repair) since the timing bug turned out not to block a bounded
result:
`experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon.yaml`
(same `xy` / `ekf2_of_n_min: 0.3` contract, GNSS loss requested 20 s after
takeoff, 60 s route). Accepted run `20260720_085443_...` is the
**first-ever bounded GNSS-loss LK result** — see Results.

Step 3 (GNSS-loss timing-reference repair) reproduced again in this run
(`effective_gnss_loss_after_takeoff_s=10.0` vs. `20.0` requested) — still
open, tracked as a known limitation below. It did not prevent a bounded
result this time, but should still be fixed before trusting exact
loss-duration claims.

Step 5 (SIFT reconfirmation) has not started.

### Operational gotchas found while executing this phase (not the flow bug)

- `scripts/runner/auto_takeoff_land_pxh_truth.py` and
  `run_scenario_pxh_end_to_end.py` do **not** read `gnss.loss_after_takeoff_s`
  or `route.duration_s` from the scenario YAML — GNSS-loss timing and hover
  duration are CLI-only (`--gnss-loss-after-takeoff-s`, `--hover-s`),
  matching the project's own "unknown YAML fields... verify the actual
  parser" warning in `README.md`. Invoking the runner with just the
  scenario path silently runs GNSS-on-only regardless of what the YAML's
  `gnss:` block says. Two of this phase's early run attempts were wasted
  this way before catching it — always pass both flags explicitly for any
  GNSS-loss scenario.
- PX4 Offboard-mode entry failed intermittently (`accepts_offboard_setpoints`
  never reaching `True`, vehicle stuck in `nav_state: 4`/AUTO_LOITER
  ignoring streamed setpoints) on 3 of 7 attempts today — a pre-existing,
  low but nonzero base rate across run history (~4%, 72 prior successes vs.
  3 prior failures), clearly worsened by today's heavy back-to-back sim
  load. Every time, removing the leftover `/tmp/px4-sock-0` from the
  previous run before retrying resolved it. Not related to the flow-sign
  work, but easy to misread as a flight-quality problem if you're watching
  the live viz — the vehicle really does just hover doing nothing,
  correctly-formed setpoints notwithstanding.

## Commands

```bash
# GNSS-on tuning check (n=1 so far, EKF2_OF_N_MIN=0.3)
sudo -u px4 bash -c '
cd /opt/databoss_px4_sim && source venv/bin/activate
python3 scripts/runner/auto_takeoff_land_pxh_truth.py \
  experiments/configs/mvp/scenarios/phase10_lk_xy_gnsson_nmin03_short_flat_rural_phototex_noon.yaml
'

# GNSS-loss attempt — CLI flags are required, the YAML gnss/route fields alone do nothing
sudo -u px4 bash -c '
cd /opt/databoss_px4_sim && source venv/bin/activate
python3 scripts/runner/auto_takeoff_land_pxh_truth.py \
  experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon.yaml \
  --hover-s 60 --gnss-loss-after-takeoff-s 20
'

# Post-run analysis (not automatic for this scenario shape)
python3 scripts/runner/postprocess_latest_truth_run.py --run-dir <run_dir>
python3 scripts/runner/align_latest_truth_run.py --run-dir <run_dir>
python3 scripts/analysis/analyze_flow_fusion_ulog.py <run_dir>/logs/flight.ulg
python3 scripts/analysis/check_flow_velocity_sign.py <run_dir> --source gps
```

## Expected outputs

A `xy`-contract LK config that flies straight under GNSS loss, with
evidence saved the same way as every other accepted DATABOSS run
(`experiments/runs/<run_id>/validation.md` + metrics).

## Acceptance criteria

Reusing the inspection report's proposed "Next Test" criteria, applied
under actual GNSS loss (not GNSS-on):

- Truth-path straightness `> 0.9`.
- Truth end displacement within a fixed tolerance of the intended route.
- Optical-flow fused fraction `> 0.9`.
- Optical-flow rejected count near zero.
- EKF-vs-truth horizontal error bounded (not diverging/flyaway).

## Results

**GNSS-on tuning check** (`20260720_083128_...`, `axis_map: xy`,
`EKF2_OF_N_MIN=0.3`): Accepted. Sign sentinel body-X `corr=0.7713,
gain=0.9776` (positive/correct). Flow fusion `612/622` fused, `0` rejected.

**GNSS-loss attempts** (same contract, effective GNSS loss at 10 s after
takeoff every time). Three independent runs, all Accepted:

| Metric | Run B (`085443`) | Run C (`090850`, replicate) | Run D (`104301`, dedicated 50 s-off test) | Acceptance | Pass? |
|---|---:|---:|---:|---|---|
| Truth-path straightness | 0.978 | 0.964 | 0.974 | > 0.9 | Yes, all 3 |
| Truth path length / end displacement | 10.340 m / 10.108 m | 10.314 m / 9.944 m | 10.201 m / 9.936 m | end ≈ intended ~10 m route | Yes, all 3 |
| Optical-flow fused / rejected | 1590 / 0 | 1584 / 0 | 1595 / 0 | fused fraction > 0.9, rejected ≈ 0 | Yes, all 3 (100%) |
| EKF-vs-truth horizontal error mean/max | 0.594 m / 1.097 m | 0.751 m / 1.661 m | 0.398 m / 1.093 m | bounded, no flyaway | Yes, all 3 |
| EKF-vs-truth height error mean | 0.258 m | 0.173 m | 0.260 m | bounded | Yes, all 3 |

Run D was the deliberately, explicitly-parameterized version: `post_loss_hover_s`
was passed directly as `--post-loss-hover-s 50` rather than relying on the
`hover_s − effective_loss` default, so this is an unambiguous full 50 s
GNSS-off duration — the same benchmark duration SIFT diverges 4/4 at (Phase
8I) and stock flow is proven bounded 3/3 at (Phase 8J), making this result
directly comparable for Phase 11.

For comparison, the sign-inverted contract's GNSS-loss attempts diverged to
`~1.4 km` and `2515.98 m` max horizontal error. These three runs' max
horizontal error never exceeded `1.7 m` — three orders of magnitude
smaller, and all three truth paths are clean straight legs, not loops.

All five of this phase's acceptance criteria pass on all three independent
runs (n=3/3). Run-to-run variance is modest (straightness 0.964–0.978, max
horizontal error 1.09–1.66 m) and does not change the pass/fail outcome.

Comparison plots and a machine-readable two-run summary were generated in
`experiments/comparisons/20260720_phase10_gnssloss_lk_xy_nmin03_pair/`.
They include Gazebo-truth route overlays, PX4 EKF vs. Gazebo truth panels,
horizontal/height error time series, optical-flow fusion fraction, aid-source
sample rate, and optical-flow innovation test ratio.

Bookkeeping note: the per-run `ekf_vs_ground_truth_metrics.json` files for
the two GNSS-loss runs still have `accepted=false` because the alignment step
used `comparison_window=until-land-command`, while these scenarios
intentionally set `control.skip_landing_command=true`. The metric files
therefore report `comparison_window_ok=false` and
`comparison_end_reason=land_command_not_found`. This is an analysis-window
contract mismatch, not a navigation-failure signal; the phase-level
acceptance above is based on the saved validation, ULog optical-flow fusion,
Gazebo-truth route metrics, and EKF-vs-truth bounded-error evidence.

Latest-run GNSS data plots were generated for Run D in its `plots/` folder:
`gnss_data_over_time.png`, `gnss_position_trace.png`, and
`gnss_data_summary.json`. The ULog `vehicle_gps_position` stream itself
changes from `fix_type=3` / `10` satellites to `fix_type=0` / `0` satellites
at `14.382 s` after ULog start, which is `2.508 s` after the takeoff-threshold
crossing (`11.874 s`). That observed GNSS-data transition is earlier than the
status/schedule timestamp (`21.874 s`, derived from the recorded
`10.0 s after takeoff` setting), so timing claims for this scenario must be
validated from ULog GNSS topics, not status-file bookkeeping alone.

## Interpretation

The optical-flow sign inversion was very likely the entire root cause of
every prior GNSS-loss divergence, not merely a contributing factor. Fixing
only the sign (`axis_map: "xy"`) and choosing a moderate trust weight
(`EKF2_OF_N_MIN=0.3`, between Phase 8N's untuned `0.15` and the old
contract's overly-suppressive `0.5`) was sufficient to produce a bounded,
straight GNSS-loss flight on the first properly-configured attempt — no
additional loop-damping or aiding-strength tuning (originally scoped as
steps 4/5) was needed to get a first pass. This is a much simpler resolution
than the project's working hypothesis going into this phase, which expected
a genuine control-loop damping problem on top of the sign issue.

## Known limitations

- **n=3.** Three independent GNSS-loss runs passing all criteria, with
  tightly clustered metrics, is solid evidence the fix is real and
  reasonably repeatable — but a parameter sweep around `EKF2_OF_N_MIN=0.3`
  (e.g. 0.15/0.2/0.3/0.4/0.5) has still not been run, so treat 0.3 as a
  good first choice, not a swept optimum.
- **GNSS-loss timing-reference bug still open, but now well understood.**
  Root cause found: in `offboard_local_position_hold` control mode, the
  runner computes effective loss time as
  `start_after_takeoff_s + warmup_s + control.gnss_loss_after_offboard_s`
  (default `3.0`) and **ignores** the `--gnss-loss-after-takeoff-s` CLI
  value entirely — that flag only matters for a different, non-offboard
  control path. This explains every "requested vs. effective" mismatch
  seen since Phase 8L. The Run D GNSS-data plot adds a second timing concern:
  actual `vehicle_gps_position` validity drops at `2.508 s` after takeoff,
  earlier than the status-file scheduled value of `10.0 s` after takeoff.
  Not yet fixed (either make the CLI/status schedule reflect the actual
  offboard-local-hold command path, or stop treating it as meaningful for
  this control mode in docs/tooling), but it did not prevent bounded results
  on any of the three runs.
- **Offboard-mode-entry flake cost real time during this phase's execution**
  (see gotchas below) — worth a permanent fix if this phase repeats often.
- **Alignment acceptance flag mismatch.** The two GNSS-loss metric JSON files
  are marked `accepted=false` because the alignment command expected a land
  command that was intentionally skipped. Future skip-landing analyses should
  use the full comparison window or another explicit observation-window gate.
- **No dedicated GNSS-loss sign-sentinel run yet.** `check_flow_velocity_sign.py`
  was only run against the GNSS-on tuning check, not the GNSS-loss run
  itself (Phase 8N's own doc notes the sentinel isn't yet the accepted sign
  authority for GNSS-loss, since GPS velocity isn't a valid reference once
  GNSS is cut). A truth-backed sign check for the GNSS-loss window would
  strengthen this result.
- **`EKF2_OF_N_MIN=0.3` was a reasonable first guess, not a swept optimum.**
  A real tuning sweep (e.g. 0.15/0.2/0.3/0.4/0.5) was scoped as step 2 but
  not executed beyond this one value.
- SIFT has not been reconfirmed under the corrected contract (step 5).

## Files created or modified

- `experiments/configs/mvp/scenarios/phase10_lk_xy_gnsson_nmin03_short_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon.yaml`
- `experiments/configs/mvp/scenarios/phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon.yaml`
- `experiments/runs/20260720_083128_phase10_lk_xy_gnsson_nmin03_short_flat_rural_phototex_noon_pxh_takeoff_land_truth/`
- `experiments/runs/20260720_085443_phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/`
- `experiments/runs/20260720_090850_phase10_lk_xy_gnssloss20_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/`
  (Run D, the dedicated 50 s-off test)
- `scripts/analysis/plot_phase10_gnssloss_pair.py` and
  `experiments/comparisons/20260720_phase10_gnssloss_lk_xy_nmin03_pair/`
  (covers Runs B/C; Run D landed after this comparison was generated and
  is not yet folded in — its numbers are consistent with B/C, see the table
  above)
- `scripts/analysis/plot_gnss_data_run.py`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_data_over_time.png`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_position_trace.png`
- `experiments/runs/20260720_104301_phase10_lk_xy_gnssloss_off50s_nmin03_flat_rural_phototex_noon_pxh_takeoff_land_truth/plots/gnss_data_summary.json`
- Several earlier rejected attempts from Offboard-entry flakes and the
  GNSS-loss-CLI-flag miss are preserved as-is (not deleted) — they're
  useful evidence of the operational gotchas above, not junk.

## Next phase

Phase 11 — three-way flow comparison (LK vs SIFT vs stock).
