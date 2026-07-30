#!/usr/bin/env python3
"""Phase 20A vehicle composer golden check.

Standalone Markdown/JSON gate. It composes the real DATABOSS vehicles from
structured specs and compares the results semantically against the real,
flight-proven DATABOSS models and airframes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from databoss_sim.dashboard.vehicle_generation import (  # noqa: E402
    DEGENERATE_LIDAR_REASON,
    X500_ONLY_REASON,
    compose_vehicle,
)

PX4_ROOT = Path("/opt/sim_px4/PX4-Autopilot")
PX4_AIRFRAMES_DIR = PX4_ROOT / "ROMFS/px4fmu_common/init.d-posix/airframes"


CAM_LIDAR_SPEC: dict[str, Any] = {
    "name": "x500_cam_lidar_down",
    "base": "x500",
    "base_airframe": "4001_gz_x500",
    "description": "DATABOSS x500 with downward mono camera and TF03-style downward LiDAR.",
    "sensors": [
        {
            "kind": "include",
            "model": "mono_cam",
            "mount": [0, 0, 0.10, 0, 1.5707, 0],
            "mount_relative_to_base": False,
            "joint_name": "CameraJoint",
            "joint_pose": [0, 0, 0, 0, 1.5707, 0],
            "include_name": "mono_cam",
        },
        {
            "kind": "gpu_lidar",
            "link_name": "lidar_sensor_link",
            "housing": "LW20",
            "housing_mount": [0.08, 0, -0.079, 0, 1.57, 0],
            "housing_joint_name": "lidar_model_joint",
            "mount": [0.08, 0, -0.05, 0, 1.57, 0],
            "sensor_joint_name": "lidar_sensor_joint",
            "sensor_name": "lidar",
            "sensor_pose": [0, 0, 0, 3.14, 0, 0],
            "h_samples": 3,
            "v_samples": 1,
            "h_min_angle_rad": -0.02,
            "h_max_angle_rad": 0.02,
            "v_min_angle_rad": 0,
            "v_max_angle_rad": 0,
            "range_min_m": 0.1,
            "range_max_m": 100.0,
            "range_resolution_m": 0.01,
            "rate_hz": 50,
            "visualize": True,
        },
    ],
    "boot_params": {
        "EKF2_OF_DELAY": 111,
    },
}


ARK_FLOW_SPEC: dict[str, Any] = {
    "name": "x500_ark_flow",
    "base": "x500",
    "base_airframe": "4001_gz_x500",
    "description": "DATABOSS x500 with PX4 stock optical flow and AFBR-S50-style downward ToF.",
    "sensors": [
        {
            "kind": "include",
            "model": "optical_flow",
            "mount": [0.03, 0, -0.1, 0, 0, 0],
            "mount_relative_to_base": True,
            "joint_name": "optical_flow_joint",
            "joint_pose": [0, 0, -0.1, 0, 1.5707, 0],
        },
        {
            "kind": "gpu_lidar",
            "link_name": "afbr_sensor_link",
            "housing": "afbr_s50",
            "housing_mount": [0, 0, -0.079, 0, 1.57, 0],
            "housing_joint_name": "afbr_body_joint",
            "mount": [0, 0, -0.05, 0, 1.57, 0],
            "sensor_joint_name": "afbr_sensor_joint",
            "sensor_name": "afbr_lidar",
            "sensor_pose": [0, 0, 0, 3.14, 0, 0],
            "h_samples": 8,
            "v_samples": 4,
            "h_min_angle_rad": -0.108210,
            "h_max_angle_rad": 0.108210,
            "v_min_angle_rad": -0.054105,
            "v_max_angle_rad": 0.054105,
            "range_min_m": 0.02,
            "range_max_m": 30.0,
            "range_resolution_m": 0.01,
            "rate_hz": 50,
            "visualize": True,
        },
    ],
    "boot_params": {
        "SIM_GZ_EN_LIDAR": 0,
    },
}


FIRST_DATABOSS_AUTOSTART_ID = 4024


def independent_next_free_autostart_id(
    airframes_dir: Path = PX4_AIRFRAMES_DIR,
    first: int = FIRST_DATABOSS_AUTOSTART_ID,
) -> int:
    """Lowest free autostart ID >= `first`, computed by this check's OWN scan.

    Deliberately NOT vehicle_generation._next_free_autostart_id: comparing the
    composer against its own helper would be tautological and would still pass if
    the allocator were broken. Two independent computations must agree.

    This replaces a hardcoded `expected_autostart_id=4024`, which broke the moment
    a real vehicle was installed and took 4024 - the composer correctly returned
    4025 and the test failed. Every future install would have broken it again.
    """
    used: set[int] = set()
    if airframes_dir.is_dir():
        for path in airframes_dir.glob("*_gz_*"):
            prefix = path.name.split("_", 1)[0]
            if prefix.isdigit():
                used.add(int(prefix))
    candidate = first
    while candidate in used:
        candidate += 1
    return candidate


@dataclass(frozen=True)
class GoldenCase:
    label: str
    spec: dict[str, Any]
    target_model: Path
    target_airframe: Path
    expected_camera_hfov_rad: float | None

    @property
    def expected_autostart_id(self) -> int:
        # Resolved at access time, so the expectation tracks whatever is really
        # installed rather than a literal frozen at authoring time.
        return independent_next_free_autostart_id()


GOLDEN_CASES = (
    GoldenCase(
        label="x500_cam_lidar_down",
        spec=CAM_LIDAR_SPEC,
        target_model=PROJECT_ROOT / "src/databoss_sim/models/x500_cam_lidar_down/model.sdf",
        target_airframe=PROJECT_ROOT / "src/databoss_sim/airframes/4022_gz_x500_cam_lidar_down",
        expected_camera_hfov_rad=1.74,
    ),
    GoldenCase(
        label="x500_ark_flow",
        spec=ARK_FLOW_SPEC,
        target_model=PROJECT_ROOT / "src/databoss_sim/models/x500_ark_flow/model.sdf",
        target_airframe=PROJECT_ROOT / "src/databoss_sim/airframes/4023_gz_x500_ark_flow",
        expected_camera_hfov_rad=0.733038,
    ),
)


@dataclass
class Check:
    section: str
    status: str
    expected: Any = None
    actual: Any = None
    detail: str = ""


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children_named(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _strip_namespace(child.tag) == name]


def _first_child(element: ET.Element, name: str) -> ET.Element | None:
    return next(iter(_children_named(element, name)), None)


def _child_text(element: ET.Element, name: str) -> str | None:
    child = _first_child(element, name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return 0.0 if parsed == 0.0 else parsed


def _pose_value(element: ET.Element | None) -> dict[str, Any] | None:
    if element is None or element.text is None:
        return None
    values = tuple(_num(part) for part in element.text.split())
    return {"values": values, "relative_to": element.attrib.get("relative_to")}


def _scalar(element: ET.Element, path: list[str]) -> float | str | None:
    node: ET.Element | None = element
    for name in path:
        if node is None:
            return None
        node = _first_child(node, name)
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    try:
        return _num(text)
    except ValueError:
        return text


def _int_scalar(element: ET.Element, path: list[str]) -> int | None:
    value = _scalar(element, path)
    return int(value) if value is not None else None


def _strip_xml_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _model_from_xml(text: str) -> ET.Element:
    root = ET.fromstring(_strip_xml_comments(text))
    model = _first_child(root, "model")
    if model is None:
        raise ValueError("SDF has no top-level model")
    return model


def _canon_include(include: ET.Element) -> dict[str, Any]:
    pose = _first_child(include, "pose")
    return {
        "merge": include.attrib.get("merge"),
        "uri": _child_text(include, "uri"),
        "name": _child_text(include, "name"),
        "pose": _pose_value(pose),
    }


def _canon_joint(joint: ET.Element) -> dict[str, Any]:
    return {
        "name": joint.attrib.get("name"),
        "type": joint.attrib.get("type"),
        "parent": _child_text(joint, "parent"),
        "child": _child_text(joint, "child"),
        "pose": _pose_value(_first_child(joint, "pose")),
    }


def _canon_link(link: ET.Element) -> dict[str, Any]:
    inertial = _first_child(link, "inertial")
    inertia = _first_child(inertial, "inertia") if inertial is not None else None
    return {
        "name": link.attrib.get("name"),
        "pose": _pose_value(_first_child(link, "pose")),
        "mass": _scalar(inertial, ["mass"]) if inertial is not None else None,
        "inertia_diagonal": {
            "ixx": _scalar(inertia, ["ixx"]) if inertia is not None else None,
            "iyy": _scalar(inertia, ["iyy"]) if inertia is not None else None,
            "izz": _scalar(inertia, ["izz"]) if inertia is not None else None,
        },
    }


def _canon_sensor(sensor: ET.Element, link_name: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "link": link_name,
        "name": sensor.attrib.get("name"),
        "type": sensor.attrib.get("type"),
        "gz_frame_id": _child_text(sensor, "gz_frame_id"),
        "pose": _pose_value(_first_child(sensor, "pose")),
        "update_rate": _scalar(sensor, ["update_rate"]),
        "always_on": _scalar(sensor, ["always_on"]),
        "visualize": _scalar(sensor, ["visualize"]),
    }
    if data["type"] == "gpu_lidar":
        data["ray"] = {
            "horizontal": {
                "samples": _int_scalar(sensor, ["ray", "scan", "horizontal", "samples"]),
                "resolution": _int_scalar(sensor, ["ray", "scan", "horizontal", "resolution"]),
                "min_angle": _scalar(sensor, ["ray", "scan", "horizontal", "min_angle"]),
                "max_angle": _scalar(sensor, ["ray", "scan", "horizontal", "max_angle"]),
            },
            "vertical": {
                "samples": _int_scalar(sensor, ["ray", "scan", "vertical", "samples"]),
                "resolution": _int_scalar(sensor, ["ray", "scan", "vertical", "resolution"]),
                "min_angle": _scalar(sensor, ["ray", "scan", "vertical", "min_angle"]),
                "max_angle": _scalar(sensor, ["ray", "scan", "vertical", "max_angle"]),
            },
            "range": {
                "min": _scalar(sensor, ["ray", "range", "min"]),
                "max": _scalar(sensor, ["ray", "range", "max"]),
                "resolution": _scalar(sensor, ["ray", "range", "resolution"]),
            },
        }
    if data["type"] == "camera":
        data["camera"] = {
            "horizontal_fov": _scalar(sensor, ["camera", "horizontal_fov"]),
            "width": _int_scalar(sensor, ["camera", "image", "width"]),
            "height": _int_scalar(sensor, ["camera", "image", "height"]),
        }
    return data


def _sorted(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))


def semantic_summary(text: str) -> dict[str, Any]:
    model = _model_from_xml(text)
    sensors: list[dict[str, Any]] = []
    for link in _children_named(model, "link"):
        link_name = link.attrib.get("name", "")
        for sensor in _children_named(link, "sensor"):
            sensors.append(_canon_sensor(sensor, link_name))
    return {
        "self_collide": _child_text(model, "self_collide"),
        "includes": _sorted([_canon_include(include) for include in _children_named(model, "include")]),
        "joints": _sorted([_canon_joint(joint) for joint in _children_named(model, "joint")]),
        "links": _sorted([_canon_link(link) for link in _children_named(model, "link")]),
        "sensors": _sorted(sensors),
    }


def _equivalent(expected: Any, actual: Any, tolerance: float = 1e-9) -> bool:
    if isinstance(expected, float) and isinstance(actual, float):
        return abs(expected - actual) <= tolerance
    if isinstance(expected, tuple) and isinstance(actual, tuple):
        return len(expected) == len(actual) and all(_equivalent(e, a, tolerance) for e, a in zip(expected, actual))
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_equivalent(e, a, tolerance) for e, a in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_equivalent(expected[k], actual[k], tolerance) for k in expected)
    return expected == actual


def compare_sdf(expected_text: str, actual_text: str) -> list[Check]:
    expected = semantic_summary(expected_text)
    actual = semantic_summary(actual_text)
    checks: list[Check] = []
    for section in ("self_collide", "includes", "joints", "links", "sensors"):
        ok = _equivalent(expected[section], actual[section])
        checks.append(Check(f"SDF {section}", "PASS" if ok else "FAIL", expected[section], actual[section]))
    return checks


def _airframe_summary(text: str) -> dict[str, Any]:
    body: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            body.append(line)
    px4_model = next((line for line in body if line.startswith("PX4_SIM_MODEL=")), None)
    source = next((line for line in body if line.startswith(". ")), None)
    params: dict[str, str] = {}
    for line in body:
        parts = line.split()
        if len(parts) == 4 and parts[:2] == ["param", "set-default"]:
            params[parts[2]] = parts[3]
    return {"PX4_SIM_MODEL": px4_model, "source": source, "params": params}


def compare_airframe(expected_text: str, actual_text: str) -> list[Check]:
    expected = _airframe_summary(expected_text)
    actual = _airframe_summary(actual_text)
    checks: list[Check] = []
    for section in ("PX4_SIM_MODEL", "source", "params"):
        ok = expected[section] == actual[section]
        checks.append(Check(f"Airframe {section}", "PASS" if ok else "FAIL", expected[section], actual[section]))
    return checks


def expect_raises(name: str, spec: dict[str, Any], expected_fragment: str, **kwargs: Any) -> Check:
    try:
        compose_vehicle(spec, **kwargs)
    except Exception as exc:  # noqa: BLE001 - checker reports exact validation text.
        message = str(exc)
        ok = expected_fragment in message
        return Check(name, "PASS" if ok else "FAIL", expected_fragment, message)
    return Check(name, "FAIL", expected_fragment, "compose_vehicle accepted the invalid spec")


def expect_child_link(name: str, spec: dict[str, Any], expected_child: str, **kwargs: Any) -> Check:
    try:
        composed = compose_vehicle(spec, **kwargs)
    except Exception as exc:  # noqa: BLE001 - checker reports exact validation text.
        return Check(name, "FAIL", expected_child, str(exc))
    model = semantic_summary(composed.model_sdf)
    actual_children = sorted(joint["child"] for joint in model["joints"])
    ok = expected_child in actual_children
    return Check(name, "PASS" if ok else "FAIL", expected_child, actual_children)


def validation_checks() -> list[Check]:
    duplicate_spec = dict(CAM_LIDAR_SPEC)
    non_x500_spec = {**CAM_LIDAR_SPEC, "name": "phase20_non_x500_validation", "base": "iris"}
    degenerate_spec = {
        **CAM_LIDAR_SPEC,
        "name": "phase20_degenerate_validation",
        "sensors": [
            {
                "kind": "gpu_lidar",
                "link_name": "lidar_sensor_link",
                "mount": [0, 0, -0.05, 0, 1.57, 0],
                "sensor_name": "lidar",
                "sensor_pose": [0, 0, 0, 3.14, 0, 0],
                "h_samples": 1,
                "v_samples": 1,
                "h_min_angle_rad": 0,
                "h_max_angle_rad": 0,
                "v_min_angle_rad": 0,
                "v_max_angle_rad": 0,
                "range_min_m": 0.1,
                "range_max_m": 100.0,
                "range_resolution_m": 0.01,
                "rate_hz": 50,
                "visualize": True,
            },
        ],
    }
    checks = [
        expect_raises("Validation non-x500 base", non_x500_spec, X500_ONLY_REASON),
        expect_raises("Validation 1x1 gpu_lidar", degenerate_spec, DEGENERATE_LIDAR_REASON),
        expect_raises("Validation duplicate name", duplicate_spec, "name collides with existing DATABOSS model"),
    ]
    with tempfile.TemporaryDirectory(prefix="vehicle-composer-") as tmp:
        tmp_path = Path(tmp)
        bad_models = tmp_path / "px4_models"
        (bad_models / "nolink_sensor").mkdir(parents=True)
        (bad_models / "nolink_sensor" / "model.sdf").write_text(
            "<?xml version=\"1.0\"?><sdf version=\"1.9\"><model name=\"nolink_sensor\"></model></sdf>\n"
        )
        (bad_models / "ambiguous_sensor").mkdir(parents=True)
        (bad_models / "ambiguous_sensor" / "model.sdf").write_text(
            "<?xml version=\"1.0\"?><sdf version=\"1.9\"><model name=\"ambiguous_sensor\">"
            "<link name=\"first_link\"/><link name=\"second_link\"/></model></sdf>\n"
        )
        no_link_spec = {
            **CAM_LIDAR_SPEC,
            "name": "phase20_no_link_validation",
            "sensors": [
                {
                    "kind": "include",
                    "model": "nolink_sensor",
                    "mount": [0, 0, 0, 0, 0, 0],
                    "mount_relative_to_base": False,
                    "joint_name": "NoLinkJoint",
                }
            ],
        }
        checks.append(
            expect_raises(
                "Validation include link discovery",
                no_link_spec,
                "no discoverable top-level <link name=...>",
                px4_models_dir=bad_models,
                px4_airframes_dir=PX4_AIRFRAMES_DIR,
            )
        )
        ambiguous_link_spec = {
            **CAM_LIDAR_SPEC,
            "name": "phase20_ambiguous_link_validation",
            "sensors": [
                {
                    "kind": "include",
                    "model": "ambiguous_sensor",
                    "mount": [0, 0, 0, 0, 0, 0],
                    "mount_relative_to_base": False,
                    "joint_name": "AmbiguousLinkJoint",
                }
            ],
        }
        checks.append(
            expect_raises(
                "Validation ambiguous include link discovery",
                ambiguous_link_spec,
                "ambiguous submodel links",
                px4_models_dir=bad_models,
                px4_airframes_dir=PX4_AIRFRAMES_DIR,
            )
        )
    gimbal_child_link_spec = {
        **CAM_LIDAR_SPEC,
        "name": "phase20_gimbal_child_link_validation",
        "sensors": [
            {
                "kind": "include",
                "model": "gimbal",
                "mount": [0, 0, 0, 0, 0, 0],
                "mount_relative_to_base": False,
                "joint_name": "GimbalCameraJoint",
                "child_link": "camera_link",
            }
        ],
    }
    checks.append(
        expect_child_link(
            "Validation explicit valid child_link",
            gimbal_child_link_spec,
            "camera_link",
            px4_airframes_dir=PX4_AIRFRAMES_DIR,
        )
    )
    invalid_gimbal_child_link_spec = {
        **gimbal_child_link_spec,
        "name": "phase20_invalid_child_link_validation",
        "sensors": [
            {
                **gimbal_child_link_spec["sensors"][0],
                "child_link": "not_a_real_link",
            }
        ],
    }
    checks.append(
        expect_raises(
            "Validation explicit invalid child_link",
            invalid_gimbal_child_link_spec,
            "child_link 'not_a_real_link' is not a top-level link",
            px4_airframes_dir=PX4_AIRFRAMES_DIR,
        )
    )
    return checks


def golden_case_checks(case: GoldenCase) -> tuple[list[Check], dict[str, Any]]:
    composed = compose_vehicle(case.spec, allow_existing_name=True)
    checks = compare_sdf(case.target_model.read_text(), composed.model_sdf)
    checks.extend(compare_airframe(case.target_airframe.read_text(), composed.airframe))
    checks.append(
        Check(
            "Autostart ID",
            "PASS" if composed.autostart_id == case.expected_autostart_id else "FAIL",
            case.expected_autostart_id,
            composed.autostart_id,
        )
    )
    checks.append(
        Check(
            "Camera hfov_rad",
            "PASS" if composed.camera_hfov_rad == case.expected_camera_hfov_rad else "FAIL",
            case.expected_camera_hfov_rad,
            composed.camera_hfov_rad,
        )
    )
    return checks, {
        "autostart_id": composed.autostart_id,
        "airframe_filename": composed.airframe_filename,
        "camera_hfov_rad": composed.camera_hfov_rad,
    }


def run_checks() -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []
    golden_meta: dict[str, Any] = {}
    for case in GOLDEN_CASES:
        case_checks, case_meta = golden_case_checks(case)
        checks.extend(Check(f"{case.label} {check.section}", check.status, check.expected, check.actual) for check in case_checks)
        golden_meta[case.label] = case_meta
    checks.extend(validation_checks())
    meta = {
        "golden_cases": golden_meta,
        "comparison_ignores": ["comments", "attribute ordering", "whitespace"],
    }
    return checks, meta


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def print_markdown(checks: list[Check], meta: dict[str, Any]) -> None:
    print("# Vehicle composer check\n")
    for label, case_meta in meta["golden_cases"].items():
        print(f"- {label}.composed_autostart_id: {case_meta['autostart_id']}")
        print(f"- {label}.airframe_filename: `{case_meta['airframe_filename']}`")
        print(f"- {label}.camera_hfov_rad: {case_meta['camera_hfov_rad']}")
    print("- Semantic SDF comparison ignores comment text, attribute ordering, and whitespace.")
    print("- Numeric comparison tolerance: 1e-9 (`.10` == `0.10`, `-0` == `0`).")
    print("\n| check | status |")
    print("| --- | --- |")
    for check in checks:
        print(f"| {check.section} | {check.status} |")
    failures = [check for check in checks if check.status != "PASS"]
    for check in failures:
        print(f"\n## {check.section}\n")
        print("| expected | actual |")
        print("| --- | --- |")
        print(f"| `{json.dumps(_jsonable(check.expected), sort_keys=True)}` | `{json.dumps(_jsonable(check.actual), sort_keys=True)}` |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args()
    checks, meta = run_checks()
    ok = all(check.status == "PASS" for check in checks)
    if args.json:
        print(json.dumps({
            "ok": ok,
            "meta": meta,
            "checks": [
                {
                    "section": check.section,
                    "status": check.status,
                    "expected": _jsonable(check.expected),
                    "actual": _jsonable(check.actual),
                    "detail": check.detail,
                }
                for check in checks
            ],
        }, indent=2))
    else:
        print_markdown(checks, meta)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
