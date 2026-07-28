"""Pydantic contract for a run's sensor_contract_report.json.

Field shape verified against all 40 real sensor_contract_report.json files
under experiments/runs/ (Phase 17A grounding pass, 2026-07-24). Only 12 of
14 observed top-level keys are universal (40/40); `axis_contract` (4/40)
and `rotation_contract` (2/40) are genuinely gate-specific extras, present
only for `gate: "axis"`/`gate: "rotation"` runs respectively.

The internals of `status`/`scene_window`/`camera_frames*`/`rangefinder*`/
`flow_bridge`/`flow_fusion`/`gate_result` vary by `gate` (observed values:
scene, axis, rotation, timing, fusion, loss) - typed loosely as
dict[str, Any] rather than one rigid shape per the project's own
documented approach for this file. `truth_metrics`, however, was confirmed
(40/40) to be byte-for-byte the same shape as ekf_vs_ground_truth_metrics.json,
so it reuses EkfVsTruthMetrics directly rather than re-modeling it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from databoss_sim.contracts.ekf_metrics import EkfVsTruthMetrics


class SensorContractReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    created_utc: str
    run_dir: str
    gate: str
    config: dict[str, Any]
    status: dict[str, Any]
    scene_window: dict[str, Any]
    camera_frames_all: dict[str, Any]
    camera_frames: dict[str, Any]
    rangefinder_all: dict[str, Any]
    rangefinder: dict[str, Any]
    flow_bridge: dict[str, Any]
    flow_fusion: dict[str, Any]
    gate_result: dict[str, Any]
    truth_metrics: EkfVsTruthMetrics

    # Gate-specific extras - only present for gate="axis" / gate="rotation".
    axis_contract: dict[str, Any] | None = None
    rotation_contract: dict[str, Any] | None = None
