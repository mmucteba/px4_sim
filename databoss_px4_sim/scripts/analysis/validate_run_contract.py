#!/usr/bin/env python3
"""Validate run folders against the Phase 17A data contract without writing
experiments/index.json.

    venv/bin/python scripts/analysis/validate_run_contract.py --all
    venv/bin/python scripts/analysis/validate_run_contract.py --run-id <run_id>

Exits non-zero only if a run's *present* contract files fail to validate
against their Pydantic model - a run simply missing files (legacy/
in-progress) is not a validation failure, matching build_experiments_index.py's
"never crash on a legacy folder" philosophy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from databoss_sim.contracts.ekf_metrics import EkfVsTruthMetrics  # noqa: E402
from databoss_sim.contracts.postprocess_summary import PostprocessSummary  # noqa: E402
from databoss_sim.contracts.sensor_contract_report import SensorContractReport  # noqa: E402
from scripts.analysis.build_experiments_index import (  # noqa: E402
    RUNS_DIR,
    build_reverse_comparison_index,
    build_run_entry,
    classify_contract_status,
)

CONTRACT_FILES = [
    ("ekf_vs_ground_truth_metrics.json", EkfVsTruthMetrics),
    ("postprocess_summary.json", PostprocessSummary),
    ("sensor_contract_report.json", SensorContractReport),
]


def validate_one_run(run_dir: Path) -> tuple[str, list[str]]:
    """Returns (contract_status, list of hard validation failures)."""
    from scripts.analysis.build_experiments_index import _dir_last_modified

    last_modified = _dir_last_modified(run_dir)
    status = classify_contract_status(run_dir, last_modified)

    failures: list[str] = []
    for filename, model in CONTRACT_FILES:
        p = run_dir / filename
        if not p.is_file():
            continue
        try:
            with p.open() as f:
                data = json.load(f)
            model.model_validate(data)
        except Exception as e:
            failures.append(f"{filename}: {e}")

    return status, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Validate every run under experiments/runs/")
    group.add_argument("--run-id", type=str, help="Validate a single run by folder name")
    args = parser.parse_args()

    if not RUNS_DIR.is_dir():
        print(f"no such directory: {RUNS_DIR}", file=sys.stderr)
        return 2

    if args.run_id:
        targets = [RUNS_DIR / args.run_id]
        if not targets[0].is_dir():
            print(f"no such run: {args.run_id}", file=sys.stderr)
            return 2
    else:
        targets = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())

    status_counts: dict[str, int] = {}
    total_failures = 0

    for run_dir in targets:
        status, failures = validate_one_run(run_dir)
        status_counts[status] = status_counts.get(status, 0) + 1
        if failures:
            total_failures += len(failures)
            print(f"FAIL {run_dir.name} [{status}]")
            for f in failures:
                print(f"  - {f}")
        elif args.run_id:
            print(f"OK {run_dir.name} [{status}]")

    print(f"\n{len(targets)} run(s) checked, {total_failures} hard validation failure(s)")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    return 1 if total_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
