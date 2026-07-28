#!/usr/bin/env python3
"""Export JSON Schema files for every Phase 17A contract model.

Generated-only, like experiments/index.json - never hand-edited. The
Pydantic models under src/databoss_sim/contracts/ are the single source of
truth; this script is the export step, run after any contract change:

    venv/bin/python scripts/analysis/export_json_schemas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from databoss_sim.contracts.comparison import ComparisonManifestModel, ComparisonSummaryCase  # noqa: E402
from databoss_sim.contracts.connections import RunConnections  # noqa: E402
from databoss_sim.contracts.ekf_metrics import EkfVsTruthMetrics  # noqa: E402
from databoss_sim.contracts.index_entry import ComparisonIndexEntry, ExperimentsIndex, IndexEntry  # noqa: E402
from databoss_sim.contracts.postprocess_summary import PostprocessSummary  # noqa: E402
from databoss_sim.contracts.run_status import RunStatusCore  # noqa: E402
from databoss_sim.contracts.sensor_contract_report import SensorContractReport  # noqa: E402

OUT_DIR = PROJECT_ROOT / "docs" / "architecture" / "contracts"

MODELS = {
    "ekf_vs_ground_truth_metrics": EkfVsTruthMetrics,
    "postprocess_summary": PostprocessSummary,
    "sensor_contract_report": SensorContractReport,
    "run_status": RunStatusCore,
    "run_connections": RunConnections,
    "comparison_manifest": ComparisonManifestModel,
    "comparison_summary_case": ComparisonSummaryCase,
    "index_entry": IndexEntry,
    "comparison_index_entry": ComparisonIndexEntry,
    "experiments_index": ExperimentsIndex,
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        out_path = OUT_DIR / f"{name}.schema.json"
        out_path.write_text(json.dumps(schema, indent=2) + "\n")
        written.append(out_path)

    print(f"Wrote {len(written)} schema files to {OUT_DIR}")
    for p in written:
        print(f"  {p.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
