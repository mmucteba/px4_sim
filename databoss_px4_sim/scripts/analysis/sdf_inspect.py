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


def resolve_camera_hfov(
    vehicle_sdf: Path,
    px4_models_dir: Path,
    databoss_models_dir: Path | None = None,
) -> tuple[float | None, str | None]:
    """Resolve a vehicle's downward-camera horizontal FOV, whichever shape it uses.

    Returns (hfov_rad, source) where source is "inline" when the camera sensor is
    declared directly in the vehicle's own model.sdf, or the submodel name when it
    comes from an <include>d model:// submodel. (None, None) when the vehicle has
    no resolvable camera - a legitimate state for camera-less vehicles.

    Two shapes exist and BOTH must resolve (Phase 20, 2026-07-30):

    - hand-authored vehicles <include> a stock submodel that owns the camera
      (x500_cam_lidar_down -> mono_cam 1.74; x500_ark_flow -> optical_flow 0.733038),
    - composer-generated vehicles emit the camera INLINE, in a <link> of their own
      (vehicle_generation.py's `kind: camera` primitive).

    Resolving only the submodel shape returned None for every composed vehicle,
    which silently left flow_bridge.hfov_rad at the scenario template's 1.74 while
    the real camera was something else - a direct scale error on every optical-flow
    sample, since the bridge converts pixel flow to angular rate using this FOV.
    Caught by a composed 2.2 rad vehicle whose scenario still said 1.74.
    """
    if not vehicle_sdf.is_file():
        return None, None
    text = vehicle_sdf.read_text()

    # Inline first: a camera in the vehicle's own SDF is unambiguously its camera.
    block = extract_sensor_block(text, "camera")
    if block is not None:
        raw = sdf_value(block, "horizontal_fov")
        if raw is not None:
            try:
                return float(raw), "inline"
            except ValueError:
                pass

    # Otherwise the camera belongs to an <include>d submodel. Stock camera
    # submodels live in the PX4 tree; a DATABOSS-authored one may live in either.
    search_dirs = [px4_models_dir]
    if databoss_models_dir is not None:
        search_dirs.append(databoss_models_dir)
    for submodel in re.findall(r"model://([A-Za-z0-9_-]+)", text):
        for base in search_dirs:
            submodel_sdf = base / submodel / "model.sdf"
            if not submodel_sdf.is_file():
                continue
            sub_block = extract_sensor_block(submodel_sdf.read_text(), "camera")
            if sub_block is None:
                continue
            raw = sdf_value(sub_block, "horizontal_fov")
            if raw is None:
                continue
            try:
                return float(raw), submodel
            except ValueError:
                continue
    return None, None
