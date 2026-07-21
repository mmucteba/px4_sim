# Phase 8N — Flow Sign Inversion Probe

Status: Accepted as a short GNSS-on sign/route probe on 2026-07-20.

## Scope

Phase 8N checked the reviewed optical-flow sign inversion claim with a short
GNSS-on route pair:

- `axis_map: "xy"`, default `EKF2_OF_N_MIN=0.15`
- old Gate 6b `axis_map: "-x-y"`, `EKF2_OF_N_MIN=0.5`

This phase did not test GNSS-denied performance.

## Evidence

Accepted `xy` run:

```text
experiments/runs/20260720_070148_phase8n_lk_xy_gnsson_short_flat_rural_phototex_noon_pxh_takeoff_land_truth
```

Accepted old-baseline rerun:

```text
experiments/runs/20260720_071532_phase8l_lk_gnsson_axisfix140_ofn05_flat_rural_phototex_noon_pxh_takeoff_land_truth
```

Inspection report:

```text
experiments/inspections/20260720_phase8n_xy_vs_old_short_gnsson_compare.md
```

## Decision

`xy` is accepted as the sign-correct bridge candidate for the next repair
flight. The old `-x-y + EKF2_OF_N_MIN=0.5` config remains useful only as a
GNSS-on historical workaround: it flies straight under GNSS, but the
flow-velocity sign sentinel shows its active-axis flow velocity is inverted
against GPS body velocity.

Next work must update the old convention analyzers before using their sign
gates as acceptance evidence, then run a GNSS-on proof with the fixed
sentinel, followed by GNSS-loss only if GNSS-on remains clean.

Update on 2026-07-20: `scripts/analysis/check_flow_velocity_sign.py` is now
implemented and is the final sign authority for flow runs. The legacy
open-loop and sensor-contract axis checks are explicitly wire-side transport
checks only.

The end-to-end runner now executes the GPS-backed sentinel automatically for
GNSS-on flow-bridge scenarios and writes `flow_velocity_sign.json`; scenarios
can make it a hard gate with `analysis.flow_velocity_sign_required: true`.
GNSS-loss sign acceptance still needs a separate truth-backed sentinel
validation before it should be used as final evidence.
