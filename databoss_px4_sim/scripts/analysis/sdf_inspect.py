#!/usr/bin/env python3
"""Shared regex-based SDF text helpers.

Extracted from build_unified_comparison_report.py (Phase 17B, 2026-07-24)
so the comparison report generator and the new model-sync/FOV consistency
checker (check_model_sync_and_fov.py) share one implementation instead of
two copies drifting apart. Deliberately regex-based, not a full XML parse:
these helpers only ever need one sensor block or one tag's text value out
of a much larger SDF file, and the existing report generator already
proved this approach works across every real vehicle model SDF in the repo.
"""

from __future__ import annotations

import re


def extract_sensor_block(text: str, sensor_type: str) -> str | None:
    """First <sensor ... type='sensor_type'> ... </sensor> block, whole text."""
    pattern = re.compile(r"<sensor\s[^>]*type=['\"]" + re.escape(sensor_type) + r"['\"][^>]*>.*?</sensor>", re.DOTALL)
    m = pattern.search(text)
    return m.group(0) if m else None


def sdf_tag_block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[ >].*?</{tag}>", text, re.DOTALL)
    return m.group(0) if m else ""


def sdf_value(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1).strip() if m else None
