# Phase 13 — Dashboard data contract

Status: **Planned** (2026-07-13). Was blocked on Phase 12; Phase 12
shipped 2026-07-21 (`docs/phases/phase_12_mvp_comparison_report.md`) with
a real manifest/report schema (`scripts/analysis/comparison_manifest.py`,
`experiments/comparisons/*/manifest.yaml`) — this phase's Inputs/schema
work should derive from that actual format, not the original 2026-07-13
sketch referenced below.

## Goal

Define and validate the machine-readable contract a future DATABOSS dashboard
consumes — schemas and an index over the existing run/comparison artifacts —
WITHOUT building dashboard UI (dashboard-first development is explicitly not
part of the MVP; see the workflow rules).

## Why this phase exists

The run-folder contract grew organically (summary.md/json names vary by
runner era; analysis JSONs were added per phase). A dashboard, a CI check, or
any external consumer needs one documented, versioned schema instead of
reverse-engineering folders.

## In scope

- JSON Schema (or equivalent) for: run summary, EKF-vs-truth metrics, flow
  bridge open-loop/fusion analysis, comparison manifest/report.
- A generated top-level index (`experiments/index.json`): run ID → phase,
  scenario, world, status, key metrics, artifact paths.
- A validator script that walks `experiments/runs/` and reports
  contract-conformant vs legacy folders (legacy folders are documented, not
  rewritten — history is preserved).
- Architecture doc for the contract under `docs/architecture/`.

## Out of scope

- Any web UI, server, or visualization framework.
- Backfilling/rewriting legacy run folders.

## Inputs

Standard run-folder contract (workflow skill), Phase 12 manifest/report
formats, existing analysis JSONs.

## Implementation

Schema files under `docs/architecture/contracts/`; generator + validator
under `scripts/analysis/`; index regenerated on demand, never hand-edited.

## Commands

```bash
cd /opt/databoss_px4_sim || exit 1
venv/bin/python scripts/analysis/build_experiments_index.py
venv/bin/python scripts/analysis/validate_run_contract.py --all
```

## Expected outputs

`experiments/index.json`, schema files, validator report, architecture doc.

## Acceptance criteria

- All Phase 8G/8I/12 artifacts validate against the schemas.
- Index correctly lists every accepted run with working artifact paths.
- A reader can locate any reported number from index → run folder without
  tribal knowledge.

## Results

(pending)

## Interpretation

(pending)

## Known limitations

(pending)

## Files created or modified

(populated during execution)

## Next phase

MVP complete; further work (hardware latency modeling, gyro compensation,
low-texture robustness) is post-MVP backlog.
