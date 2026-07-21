#!/usr/bin/env python3
"""Shared case list for the unified comparison report/plot generators.

A manifest YAML defines the set of runs to compare, tagged by algorithm,
GNSS state, and world variant, instead of hardcoding a Python CASES list.
Adding a case (a new GNSS-on run, a future world-lighting or sensor
variant) is a manifest edit, not a code change.
"""

from __future__ import annotations

import gzip
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from pyulog import ULog

ROOT = Path("/opt/databoss_px4_sim")


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    short: str
    kind: str  # algorithm: lk, sift, stock
    gnss_state: str  # "on" or "loss"
    world_variant: str
    run_dir: Path
    replicate_of: str | None = None


@dataclass(frozen=True)
class Manifest:
    name: str
    title: str
    cases: list[Case]


def load_manifest(path: Path) -> Manifest:
    with Path(path).open() as handle:
        raw = yaml.safe_load(handle) or {}

    report_cfg = raw.get("report", {})
    cases: list[Case] = []
    for entry in raw.get("cases", []):
        run_dir = Path(entry["run_dir"])
        if not run_dir.is_absolute():
            run_dir = ROOT / run_dir
        cases.append(
            Case(
                key=entry["key"],
                label=entry.get("label", entry["key"]),
                short=entry.get("short", entry["key"]),
                kind=entry["algorithm"],
                gnss_state=entry["gnss_state"],
                world_variant=entry.get("world_variant", "unknown"),
                run_dir=run_dir,
                replicate_of=entry.get("replicate_of"),
            )
        )

    return Manifest(
        name=report_cfg.get("name", Path(path).parent.name),
        title=report_cfg.get("title", report_cfg.get("name", "Comparison report")),
        cases=cases,
    )


def open_ulog(run_dir: Path) -> ULog:
    """Open a run's flight.ulg, transparently decompressing flight.ulg.gz if needed.

    ULogs get gzipped in place after their comparison report is generated
    (see Phase 14 disk policy) since raw ULogs are the single biggest disk
    cost in the project (~15-20MB each, ~2.7-3x compressible). Both the
    report and plot generators go through this helper so regenerating a
    report never requires a manual gunzip step first.
    """
    ulog_path = run_dir / "logs/flight.ulg"
    if ulog_path.exists():
        return ULog(str(ulog_path))

    gz_path = run_dir / "logs/flight.ulg.gz"
    if not gz_path.exists():
        raise FileNotFoundError(f"Missing both {ulog_path} and {gz_path}")

    with tempfile.NamedTemporaryFile(suffix=".ulg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with gzip.open(gz_path, "rb") as src:
            shutil.copyfileobj(src, tmp)

    try:
        return ULog(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)
