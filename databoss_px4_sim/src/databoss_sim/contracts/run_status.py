"""Pydantic contract for a run's end_to_end_status.json.

Field shape verified against all 78 real end_to_end_status.json files found
under experiments/runs/ (Phase 17A grounding pass, 2026-07-24) - only 78 of
181 run folders have this file at all (a newer runner variant), so callers
must treat its absence as normal, not an error. Of the fields present, 12
are universal (78/78) and typed required below; the rest vary across 4
distinct real key-sets seen (16-23 keys) and are typed Optional.

Notably: qgc_enabled/qgc_ip/qgc_local_port/qgc_remote_port live directly on
this file - contracts/connections.py reads them from here in preference to
parsing commands.log, when this file exists.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RunStatusCore(BaseModel):
    model_config = ConfigDict(extra="allow")

    created_utc: str
    scenario: str
    run_dir: str
    accepted: bool
    qgc_enabled: bool
    qgc_ip: str
    qgc_local_port: int
    qgc_remote_port: int
    failsafe_profile: str
    global_position_gate_enabled: bool
    global_position_timeout_s: float
    global_position_stable_s: float
    gnss_start_used: int
    gnss_loss_after_takeoff_s: float | None = None
    post_loss_hover_s: float | None = None
    steps: list[dict[str, Any]]

    # Present in some but not all real files (varies by runner era/mode).
    comparison_window: str | None = None
    skip_landing_command: bool | None = None
    failsafe_profile_source: str | None = None
    gnss_loss_source: str | None = None
    flow_velocity_sign_enabled: bool | None = None
    flow_velocity_sign_required: bool | None = None
    flow_velocity_sign_source: str | None = None
