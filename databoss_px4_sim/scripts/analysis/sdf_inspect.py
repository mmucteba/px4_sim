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
from pathlib import Path
from xml.etree import ElementTree as ET


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


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children_named(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _strip_namespace(child.tag) == name]


def _with_declared_namespace_prefixes(text: str) -> str:
    declared = set(re.findall(r"\bxmlns:([A-Za-z_][\w.-]*)\s*=", text))
    used = set(re.findall(r"(?:<\s*/?\s*|[\s<])([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*(?=[\s/>=])", text))
    missing = sorted(used - declared - {"xml", "xmlns"})
    if not missing:
        return text
    declarations = "".join(f' xmlns:{prefix}="urn:databoss:auto:{prefix}"' for prefix in missing)
    return re.sub(r"(<sdf\b[^>]*)(>)", rf"\1{declarations}\2", text, count=1)


def discover_model_link_names(model_sdf: Path) -> list[str]:
    """Return top-level link names from a submodel's model.sdf.

    Vehicle composition must use the link names defined inside included
    submodels for fixed-joint children. Guessing these names can create a
    vehicle that loads with a detached sensor.
    """
    root = ET.fromstring(_with_declared_namespace_prefixes(model_sdf.read_text()))
    model = next((child for child in _children_named(root, "model")), None)
    if model is None:
        return []
    return [link.attrib["name"] for link in _children_named(model, "link") if link.attrib.get("name")]


def discover_single_model_link_name(model_sdf: Path) -> str:
    """Return the only top-level link name from a submodel, or raise."""
    names = discover_model_link_names(model_sdf)
    if not names:
        raise ValueError(f"no discoverable top-level <link name=...> in {model_sdf}")
    if len(names) > 1:
        raise ValueError(f"ambiguous submodel links in {model_sdf}: {', '.join(names)}")
    return names[0]
