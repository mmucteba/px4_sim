"""Read-only snapshot of what a run requested for QGC/gz-web/bridge wiring.

Grounded 2026-07-24 against real run folders, not the original plan sketch:
- QGC fields (qgc_enabled/qgc_ip/qgc_local_port/qgc_remote_port) live
  directly on end_to_end_status.json when that file exists (78/181 runs,
  a newer runner variant) - reused verbatim rather than reimplemented.
- When that file is absent, no run's commands.log has ever contained
  --no-qgc/--qgc-ip/--qgc-local-port (confirmed: 0/179 commands.log files
  match), so every run to date used the runner's own CLI defaults. Those
  defaults are used as a documented fallback, with `qgc_source` telling the
  caller whether the value is confirmed-from-status or assumed-from-default
  - never silently presented as equally certain.
- gz-web / flow_bridge / aiding fields come from the run's own copied
  config.yaml (present in 177/181 runs): `visualization.gazebo_web`
  (169/177), `flow_bridge` (97/177 - only optical-flow scenarios),
  `aiding` (177/177, universal).

This module never probes live processes or ports - it is purely
descriptive of what a run (finished or in-progress) requested, matching
Phase 17's read-only-display decision for this data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

# Confirmed via auto_takeoff_land_pxh_truth.py argparse defaults and,
# independently, via zero commands.log overrides found across 179 real runs.
DEFAULT_QGC_ENABLED = True
DEFAULT_QGC_IP = os.environ.get("DATABOSS_QGC_IP", "100.109.200.5")
DEFAULT_QGC_LOCAL_PORT = 14555
DEFAULT_QGC_REMOTE_PORT = 14550


class RunConnections(BaseModel):
    model_config = ConfigDict(extra="allow")

    qgc_enabled: bool
    qgc_ip: str
    qgc_local_port: int
    qgc_remote_port: int
    qgc_source: str  # "status_file" | "assumed_default"

    gazebo_web_enabled: bool | None = None
    gazebo_web_port: int | None = None
    gazebo_web_publication_hz: float | None = None
    gazebo_web_host: str | None = None

    flow_bridge_enabled: bool | None = None
    flow_bridge_estimator: str | None = None
    flow_bridge_rate_hz: float | None = None
    axis_map: str | None = None
    hfov_rad: float | None = None
    ekf2_of_ctrl: int | None = None
    ekf2_of_qmin: int | None = None
    ekf2_of_n_min: float | None = None
    ekf2_of_delay: float | None = None

    aiding_mode: str | None = None
    aiding_enabled: bool | None = None
    ekf2_ev_ctrl: int | None = None
    ekf2_ev_delay_ms: float | None = None
    aiding_rate_hz: float | None = None


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else None


def build_run_connections(run_dir: Path) -> RunConnections:
    """Assemble a RunConnections snapshot for one run folder.

    Never raises on missing/partial data - every field beyond the QGC
    core has a None fallback, matching every other contract in this
    package's tolerance for legacy/partial run folders.
    """
    # end_to_end_status.json is JSON, not YAML - load it explicitly.
    status_path = run_dir / "end_to_end_status.json"
    status = None
    if status_path.is_file():
        with status_path.open() as f:
            status = json.load(f)

    if status is not None and "qgc_enabled" in status:
        qgc_enabled = bool(status["qgc_enabled"])
        qgc_ip = str(status.get("qgc_ip", DEFAULT_QGC_IP))
        qgc_local_port = int(status.get("qgc_local_port", DEFAULT_QGC_LOCAL_PORT))
        qgc_remote_port = int(status.get("qgc_remote_port", DEFAULT_QGC_REMOTE_PORT))
        qgc_source = "status_file"
    else:
        qgc_enabled = DEFAULT_QGC_ENABLED
        qgc_ip = DEFAULT_QGC_IP
        qgc_local_port = DEFAULT_QGC_LOCAL_PORT
        qgc_remote_port = DEFAULT_QGC_REMOTE_PORT
        qgc_source = "assumed_default"

    config = _load_yaml(run_dir / "config.yaml") or {}

    gazebo_web = config.get("visualization", {}).get("gazebo_web", {}) if isinstance(
        config.get("visualization"), dict
    ) else {}
    flow_bridge = config.get("flow_bridge", {}) if isinstance(config.get("flow_bridge"), dict) else {}
    aiding = config.get("aiding", {}) if isinstance(config.get("aiding"), dict) else {}

    return RunConnections(
        qgc_enabled=qgc_enabled,
        qgc_ip=qgc_ip,
        qgc_local_port=qgc_local_port,
        qgc_remote_port=qgc_remote_port,
        qgc_source=qgc_source,
        gazebo_web_enabled=gazebo_web.get("enabled"),
        gazebo_web_port=gazebo_web.get("port"),
        gazebo_web_publication_hz=gazebo_web.get("publication_hz"),
        gazebo_web_host=gazebo_web.get("host"),
        flow_bridge_enabled=flow_bridge.get("enabled"),
        flow_bridge_estimator=flow_bridge.get("estimator"),
        flow_bridge_rate_hz=flow_bridge.get("rate_hz"),
        axis_map=flow_bridge.get("axis_map"),
        hfov_rad=flow_bridge.get("hfov_rad"),
        ekf2_of_ctrl=flow_bridge.get("ekf2_of_ctrl"),
        ekf2_of_qmin=flow_bridge.get("ekf2_of_qmin"),
        ekf2_of_n_min=flow_bridge.get("ekf2_of_n_min"),
        ekf2_of_delay=flow_bridge.get("ekf2_of_delay"),
        aiding_mode=aiding.get("mode"),
        aiding_enabled=aiding.get("enabled"),
        ekf2_ev_ctrl=aiding.get("ekf2_ev_ctrl"),
        ekf2_ev_delay_ms=aiding.get("ekf2_ev_delay_ms"),
        aiding_rate_hz=aiding.get("rate_hz"),
    )
