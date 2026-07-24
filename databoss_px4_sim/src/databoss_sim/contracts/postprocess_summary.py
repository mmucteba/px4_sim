"""Pydantic contract for a run's postprocess_summary.json.

Field shape verified against all 152 real postprocess_summary.json files
under experiments/runs/ (Phase 17A grounding pass, 2026-07-24): a single,
consistent 5-key top-level schema and a fully consistent `ulog` sub-object.
`truth.truth_model_name` is the only field found to vary - present in
150/152 files, absent in 2 older runs that predate it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TruthSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    truth_csv: str
    truth_rows: int
    truth_duration_s: float
    truth_first_time_s: float
    truth_last_time_s: float
    truth_model_name: str | None = None


class UlogSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    ulog_exists: bool
    extract_ok: bool
    extract_method: str
    csv_count: int
    csv_files: list[str]
    available_dataset_count: int
    available_datasets_sample: list[str]


class PostprocessSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    created_utc: str
    run_dir: str
    accepted: bool
    truth: TruthSummary
    ulog: UlogSummary
