"""Pydantic contract for a run's ekf_vs_ground_truth_metrics.json.

Field shape verified against all 150 real ekf_vs_ground_truth_metrics.json
files under experiments/runs/ (Phase 17A grounding pass, 2026-07-24): a
single, fully consistent 22-key top-level schema, three ErrorStats-shaped
sub-objects, and one nested station_keeping block reusing the same shape
twice more. land_command_t_rel_s / comparison_end_t_rel_s are the only
nullable fields (null in 122/150 files - full-window comparisons with no
explicit landing crop).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ErrorStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_m: float
    mean_m: float
    median_m: float
    p95_m: float
    end_m: float


class StationKeeping(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference: str
    px4_horizontal_displacement_from_start: ErrorStats
    gazebo_horizontal_displacement_from_start: ErrorStats


class EkfVsTruthMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    created_utc: str
    run_dir: str
    takeoff_threshold_m: float
    px4_takeoff_crossing_t_rel_s: float
    gazebo_takeoff_crossing_t_rel_s: float
    time_offset_gazebo_minus_px4_s: float
    comparison_window: str
    comparison_end_reason: str
    land_command_t_rel_s: float | None = None
    comparison_end_t_rel_s: float | None = None
    comparison_window_ok: bool
    uncropped_aligned_rows: int
    cropped_after_comparison_end_rows: int
    aligned_rows: int
    comparison_start_t_rel_s: float
    comparison_last_t_rel_s: float
    aligned_duration_s: float
    horizontal_error: ErrorStats
    height_abs_error: ErrorStats
    error_3d: ErrorStats
    station_keeping: StationKeeping
    accepted: bool
