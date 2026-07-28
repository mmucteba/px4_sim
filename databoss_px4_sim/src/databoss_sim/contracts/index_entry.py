"""The experiments/index.json entry shapes - the composed, dashboard-facing
view built on top of the other contracts/ modules.

Unlike the other modules in this package, IndexEntry/ComparisonIndexEntry
are not validated directly against one on-disk file - they are assembled by
scripts/analysis/build_experiments_index.py (Phase 17A step 5) from several
per-run files at once (config.yaml, ekf_vs_ground_truth_metrics.json,
postprocess_summary.json, sensor_contract_report.json, end_to_end_status.json).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from databoss_sim.contracts.connections import RunConnections


class IndexEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    run_dir: str
    phase: str | None = None
    scenario_name: str | None = None
    algorithm: str | None = None
    gnss_state: str | None = None
    world_variant: str | None = None
    tag_source: str  # "comparison_manifest" | "folder_name_inferred" | "unknown"
    contract_status: str  # "conformant" | "legacy" | "in_progress" | "incomplete"
    accepted: bool | None = None
    created_utc: str | None = None
    last_modified_utc: str
    key_metrics: dict[str, float | None]
    artifacts: dict[str, str]
    connections: RunConnections
    comparisons: list[str]
    warnings: list[str]


class ComparisonIndexEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    comparison_id: str
    comparison_dir: str
    name: str
    title: str
    case_count: int
    run_ids: list[str]
    has_report_md: bool
    has_summary_csv: bool
    warnings: list[str]


class ExperimentsIndex(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int
    generated_utc: str
    runs: list[IndexEntry]
    comparisons: list[ComparisonIndexEntry]
