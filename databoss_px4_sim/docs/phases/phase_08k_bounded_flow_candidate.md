# Phase 8K — Bounded-Flow Candidate (LK bridge matches the stock flow contract)

**Superseded (2026-07-20).** This phase's LK contract (`axis_map: "-yx"`, no
`EKF2_OF_N_MIN` override) was later found loop-prone by Phase 8L Gate 6b
(`docs/phases/phase_08l_sensor_sanity_ladder.md`) and, further, wired with
the wrong optical-flow sign per Phase 8N
(`docs/phases/phase_08n_flow_sign_inversion_probe.md`), which found
`axis_map: "xy"` is sign-correct. See also the 2026-07-20 root-cause
inspection (`experiments/inspections/20260720_phase8m_route_root_cause_report.md`).
Results below reflect the pre-fix contract and should not be used as
current acceptance evidence.

Status: In progress

## Goal

Make the DATABOSS live LK optical-flow bridge keep PX4 EKF2 bounded through a
50 s GNSS outage on the 240x240 m `flat_rural_phototex_noon` world, at a level
comparable to PX4's stock simulated flow (`gz_x500_flow`: 3/3 bounded, worst
max horizontal error 8.368 m).

## Why this phase exists

Phase 8J's fusion repair made LK reach the EKF2 optical-flow path (run
`20260716_111242`: 150 fused / 0 rejected), but the follow-up flight run
`20260716_112613` still diverged. Forensics on that run's
`flow_bridge/flow_bridge_sent.csv` and Gazebo truth showed the divergence was
caused by the bridge's own protective gating, not by LK tracking quality:

- Real LK samples fused only in a ~5 s window (t≈22–27 s, 153 samples), while
  GNSS was still on.
- The middle third of processed frames had mean quality 0.0 with mean 79
  tracked matches — the `lk_max_flow_rate_rad_s: 1.2` sanity gate was the
  zeroing mechanism (it returns a quality-0 sample AND re-primes the tracker).
  The bridge sees uncompensated rotation+translation (gyros are NaN by design),
  so hover attitude corrections plus drift transiently exceed 1.2 rad/s. The
  stock gz flow sensor's limit is 7.4 rad/s.
- `reset_on_unsent: true` destroyed tracker state on every gated frame, so
  quality never recovered (a sibling run had 620 of 649 samples as primes).
- Once EKF drift exceeded the ±120 m texture patch, the downward rangefinder
  returned `inf`, which permanently latched the bridge's `send_min/max_range_m`
  gates shut (`inf` fails the finite check). EKF2 dead-reckoned; truth showed
  the vehicle 2.5 km off-map at −247 m while EKF height read +25 m at a true
  altitude of 2.5 m.

The stock gz flow sensor's contract is the opposite of our gating: it always
publishes at 50 Hz with honest quality and no sensor-side EKF-protection
gates. PX4 owns rejection via `SENS_FLOW_MAXR` (8 rad/s), `EKF2_OF_QMIN` (17
in our profile), and the EKF innovation gate (`EKF2_OF_GATE` 3 SD).

## In scope

- YAML-only bridge reconfiguration to match the stock sensor contract
  (one coherent change set, no code edits).
- 3x 50 s GNSS-loss replicates of the fixed LK bridge on the same world and
  profile as the accepted stock reference batch.
- Comparison report with per-run trajectory plots and cross-run metric plots
  against the existing stock 3/3 evidence.

## Out of scope

- SIFT rerun with the same fixes (later phase; user chose LK-only).
- LK quality-formula change (evidence shows genuine tracking gives quality
  well above QMIN; fallback only if 8K shows honest-quality dropouts).
- Camera rate/resolution changes (delivery is ~15–18 Hz vs stock 50 Hz;
  bounded SIFT rep2 at 14.3 Hz proves ~15 Hz can hold this profile).
- Stock rerun (its 3/3 bounded evidence on this exact profile is reused).
- 600 m world runs and their launch-failure triage.

## Inputs

- Template scenario: `experiments/configs/mvp/scenarios/phase8j_d_loss_flow_lk_flat_rural_phototex_noon.yaml`
- Template batch: `experiments/configs/mvp/batches/phase8j_lk_50s_replicates.yaml`
- Stock reference batch: `experiments/batches/20260716_081558_phase8j_stock_vs_sift_50s_replicates`
- Forensic evidence: runs `20260716_111242` (fusion proof) and `20260716_112613`
  (post-fix divergence), report `experiments/comparisons/phase8j_lk_fusion_repair/report.md`

## Implementation

New scenario `experiments/configs/mvp/scenarios/phase8k_d_loss_flow_lk_flat_rural_phototex_noon.yaml`,
identical to the 8J LK scenario except these `flow_bridge` keys:

```yaml
lk_max_flow_rate_rad_s: 7.4   # was 1.2 — stock gz sensor limit; PX4 guards via SENS_FLOW_MAXR 8
send_min_quality: 0           # was 20 — always send with honest quality
send_min_matches: 0           # was 8
reset_on_unsent: false        # was true — never destroy tracker state on gated frames
# send_min_range_m (was 0.8) and send_max_range_m (was 60.0) REMOVED:
# key omission fully disables range send-gating, including the inf-range block.
```

Everything else is unchanged (rate_hz 40, max_width 320, hfov 1.74,
axis_map "-yx", quality_in 20/100, NaN gyros, distance −1, startup primes,
pre-arm gate, min_sent_samples 100, EKF2_OF_CTRL 1 / QMIN 17 / DELAY 111).

Plumbing verified before running: the runner
(`scripts/runner/auto_takeoff_land_pxh_truth.py:1440-1453,2224-2243`) only
passes gate args when the YAML keys are present, and the bridge
(`scripts/sim/flow_mavlink_bridge.py:175-190`) skips range checks when bounds
are None. LK quality-0 samples always carry zero flow
(`src/databoss_sim/flow/lk_estimator.py:98-151`), and EKF2 does not fuse
sub-QMIN samples while active, so always-sending is EKF-safe.

New batch `experiments/configs/mvp/batches/phase8k_lk_bounded_50s_replicates.yaml`:
same defaults as the 8J batch (including the QGC 100.109.200.5 link and the
gz-web 9003 visualization, both required live per standing rule), cases
`lk_bounded_rep1/2/3` (names start with `lk` for the report tool's system
mapping).

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1

# smoke: single case first
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8k_lk_bounded_50s_replicates.yaml \
  --only lk_bounded_rep1 --continue-on-fail

# full 3x batch
sudo rm -f /tmp/px4-sock-* 2>/dev/null || true
sudo -H -u px4 /opt/databoss_px4_sim/venv/bin/python scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8k_lk_bounded_50s_replicates.yaml --continue-on-fail

# comparison report vs existing stock evidence
source venv/bin/activate
python scripts/analysis/report_phase8j_stock_vs_sift_replicates.py \
  --batch-dir experiments/batches/20260716_081558_phase8j_stock_vs_sift_50s_replicates \
  --batch-dir experiments/batches/<TS>_phase8k_lk_bounded_50s_replicates \
  --out-dir experiments/comparisons/phase8k_lk_bounded_50s_replicates --repair-missing
```

## Expected outputs

- 3 run folders `experiments/runs/<ts>_phase8k_d_loss_flow_lk_flat_rural_phototex_noon_pxh_takeoff_land_truth`
  with ULog, Gazebo truth, `flow_bridge/flow_bridge_sent.csv`, validation.md.
- Batch folder `experiments/batches/<ts>_phase8k_lk_bounded_50s_replicates` with `batch_summary.json`.
- Comparison folder `experiments/comparisons/phase8k_lk_bounded_50s_replicates`
  with `report.md`, `replicate_metrics.csv/json`, `bridge_rate_diagnostics.csv`,
  per-run trajectory plots, and cross-run metric plots.

## Acceptance criteria

For all 3 replicates:

1. `divergence_verdict = bounded` (report thresholds: horizontal_max ≤ 25 m,
   height_abs_max ≤ 10 m, rejected/fused ≤ 1.0).
2. Stock parity: worst `horizontal_max_m` ≤ 10 m (10–25 m ⇒ Accepted with
   limitations).
3. `flow_fusion_ok = True` per `analyze_flow_fusion_ulog.py`
   (aid rows > 100, cs_opt_flow active, fused > 100, rejected/fused < 0.10).
4. `cs_opt_flow_active_fraction ≥ 0.5` (recorded either way; delivery is
   ~15–18 Hz vs stock 50 Hz, brief dropouts cost ~3 s restart and are
   tolerated).
5. Runner gates: `gnss_loss_detected`, `flow_bridge_ok` (sent rows ≥ 100),
   `distance_sensor_ok`.
6. Direct proof the zeroing mechanism is gone: in `flow_bridge_sent.csv`,
   mid/last-third mean raw quality > 20 with zero-fraction < 0.5.

## Results

Pending.

## Interpretation

Pending.

## Known limitations

- Delivered flow rate remains ~15–18 Hz (30 Hz camera, render-limited) vs
  stock 50 Hz — a residual, documented variable, not controlled in this phase.
- The 1.2-gate trip cause (uncompensated body rates) is inferred from send
  reasons and truth kinematics, not from per-sample flow-rate logging.
- Truth-independent: this phase proves the bridge/EKF integration contract,
  not LK's estimator quality limits in harder scenes (texture, lighting,
  altitude are all fixed).

## Files created or modified

- `experiments/configs/mvp/scenarios/phase8k_d_loss_flow_lk_flat_rural_phototex_noon.yaml` (new)
- `experiments/configs/mvp/batches/phase8k_lk_bounded_50s_replicates.yaml` (new)
- `docs/phases/phase_08k_bounded_flow_candidate.md` (this file)
- `docs/PROJECT_LOG.md`, `docs/phases/README.md` (entries)

## Next phase

If accepted: apply the same contract fixes to a SIFT scenario and rerun the
full stock/SIFT/LK matrix; then move to harder worlds (terrain, altitude).
If rejected with healthy quality and low rejects: single-variable camera-rate
phase (raise camera Hz / delivery rate toward stock 50 Hz).
