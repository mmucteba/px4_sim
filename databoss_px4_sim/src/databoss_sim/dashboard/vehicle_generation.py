"""DATABOSS Phase 20A vehicle composition.

Pure helpers for turning a structured vehicle spec into model.sdf,
model.config, and an airframe body. Composition may read existing submodels
and PX4 airframe filenames, but it does not write anything; write_vehicle()
is the sole filesystem-writing entry point.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from databoss_sim.dashboard.config import PROJECT_ROOT

SCRIPTS_ANALYSIS_DIR = PROJECT_ROOT / "scripts" / "analysis"
if str(SCRIPTS_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ANALYSIS_DIR))

from sdf_inspect import discover_model_link_names, discover_single_model_link_name, extract_sensor_block, sdf_value  # noqa: E402

DEFAULT_DATABOSS_MODELS_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "models"
DEFAULT_DATABOSS_AIRFRAMES_DIR = PROJECT_ROOT / "src" / "databoss_sim" / "airframes"
PX4_ROOT = Path(os.environ.get("DATABOSS_PX4_ROOT", "/opt/sim_px4/PX4-Autopilot"))
DEFAULT_PX4_MODELS_DIR = PX4_ROOT / "Tools/simulation/gz/models"
DEFAULT_PX4_AIRFRAMES_DIR = PX4_ROOT / "ROMFS/px4fmu_common/init.d-posix/airframes"

NAME_RE = re.compile(r"^[a-z0-9_]+$")
DEGENERATE_LIDAR_REASON = (
    "a degenerate 1x1 gpu_lidar renders a 1-pixel depth image whose single ray goes "
    "to inf under vehicle roll (verified 2026-07-13, 82% inf on East legs)"
)
# PX4's GZBridge HARDCODES the lidar topic it subscribes to, in C++:
#   src/modules/simulation/gz_bridge/GZBridge.cpp:279
#     "/link/lidar_sensor_link/sensor/lidar/scan"
# and the runner's default_rangefinder_scan_topic() mirrors it
# (scripts/runner/auto_takeoff_land_pxh_truth.py:902). So for the NATIVE
# distance_sensor path these two names are not free choices.
NATIVE_LIDAR_LINK_NAME = "lidar_sensor_link"
NATIVE_LIDAR_SENSOR_NAME = "lidar"

# The two patterns this project actually supports:
#   A) native GZBridge path - names MUST be the canonical pair above, and
#      SIM_GZ_EN_LIDAR stays enabled. This is x500_cam_lidar_down.
#   B) MAVLink-injected path - any names allowed, but the airframe MUST set
#      SIM_GZ_EN_LIDAR 0 at BOOT (GZBridge::init() reads it once; it is
#      reboot_required, so a runtime `param set` is too late) and
#      scripts/sim/rangefinder_mavlink_bridge.py supplies the range instead.
#      This is x500_ark_flow.
# A vehicle matching NEITHER produces no range data at all: verified by a real
# flight on 2026-07-30 that armed, held 15.06 m for 63.6 s and landed cleanly,
# yet was failed by the rangefinder gate with ulog_distance_sensor_rows=0
# (run 20260730_091657_hover_test_test_15m_gnss_off_sift).
PATTERN_B_PARAM = "SIM_GZ_EN_LIDAR"

NON_CANONICAL_LIDAR_REASON = (
    f"PX4 hardcodes the native rangefinder topic as "
    f"/link/{NATIVE_LIDAR_LINK_NAME}/sensor/{NATIVE_LIDAR_SENSOR_NAME}/scan in "
    f"GZBridge.cpp:279, so custom lidar names produce ZERO distance_sensor rows on "
    f"the native path. Either use link_name={NATIVE_LIDAR_LINK_NAME!r} / "
    f"sensor_name={NATIVE_LIDAR_SENSOR_NAME!r} (pattern A, the default), or set "
    f"boot_params.{PATTERN_B_PARAM}: 0 to take the MAVLink-injected path "
    f"(pattern B, as x500_ark_flow does) which also requires "
    f"scripts/sim/rangefinder_mavlink_bridge.py to supply the range."
)

MULTI_NATIVE_LIDAR_REASON = (
    "GZBridge subscribes to exactly one lidar topic, so at most one gpu_lidar can "
    f"use the native path. Additional lidars need boot_params.{PATTERN_B_PARAM}: 0."
)

X500_ONLY_REASON = (
    "x500 pulls in x500_base, which is the exact file patched by "
    "deploy/px4/0003-x500-base-enable-wind.patch. A vehicle on another base silently "
    "stops responding to wind and every wind scenario built on it becomes fiction."
)


@dataclass(frozen=True)
class IncludeSensorSpec:
    kind: str
    model: str
    mount: tuple[float, float, float, float, float, float]
    mount_relative_to_base: bool = False
    joint_name: str | None = None
    joint_pose: tuple[float, float, float, float, float, float] | None = None
    include_name: str | None = None
    child_link: str | None = None
    joint_pose_relative_to_base: bool = True


@dataclass(frozen=True)
class GpuLidarSensorSpec:
    kind: str
    link_name: str
    mount: tuple[float, float, float, float, float, float]
    sensor_name: str
    sensor_pose: tuple[float, float, float, float, float, float]
    h_samples: int
    v_samples: int
    h_min_angle_rad: float
    h_max_angle_rad: float
    v_min_angle_rad: float
    v_max_angle_rad: float
    range_min_m: float
    range_max_m: float
    range_resolution_m: float
    rate_hz: float
    visualize: bool
    housing: str | None = None
    housing_mount: tuple[float, float, float, float, float, float] | None = None
    housing_joint_name: str | None = None
    sensor_joint_name: str | None = None
    mount_relative_to_base: bool = True
    housing_mount_relative_to_base: bool = True
    always_on: int = 1
    gz_frame_id: str | None = None
    mass: float = 0.001
    inertia_diagonal: tuple[float, float, float] = (0.00001, 0.00001, 0.00001)
    h_resolution: int = 1
    v_resolution: int = 1


@dataclass(frozen=True)
class CameraSensorSpec:
    kind: str
    link_name: str
    hfov_rad: float
    width: int
    height: int
    rate_hz: float
    mount: tuple[float, float, float, float, float, float]
    sensor_name: str = "camera"
    sensor_pose: tuple[float, float, float, float, float, float] = (0, 0, 0, 0, 0, 0)
    joint_name: str | None = None
    mount_relative_to_base: bool = True
    always_on: int = 1
    visualize: bool = True
    gz_frame_id: str | None = None
    mass: float = 0.001
    inertia_diagonal: tuple[float, float, float] = (0.00001, 0.00001, 0.00001)
    clip_near: float = 0.1
    clip_far: float = 3000.0


SensorSpec = IncludeSensorSpec | GpuLidarSensorSpec | CameraSensorSpec


@dataclass(frozen=True)
class VehicleSpec:
    name: str
    base: str
    base_airframe: str
    description: str
    sensors: tuple[SensorSpec, ...] = ()
    boot_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComposedVehicle:
    model_sdf: str
    model_config: str
    airframe: str
    airframe_filename: str
    autostart_id: int
    camera_hfov_rad: float | None
    pins_entries: dict[str, list[str]]
    warnings: list[str]


def _six(values: Any, field_name: str) -> tuple[float, float, float, float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 6:
        raise ValueError(f"{field_name} must be a six-number pose [x, y, z, roll, pitch, yaw]")
    try:
        return tuple(float(v) for v in values)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a six-number pose [x, y, z, roll, pitch, yaw]") from exc


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ValueError(f"{context}.{key} is required")
    return mapping[key]


def _bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be true or false")


def _parse_sensor(raw: dict[str, Any], index: int) -> SensorSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"sensors[{index}] must be a mapping")
    kind = _required(raw, "kind", f"sensors[{index}]")
    context = f"sensors[{index}]"
    if kind == "include":
        return IncludeSensorSpec(
            kind=kind,
            model=str(_required(raw, "model", context)),
            mount=_six(_required(raw, "mount", context), f"{context}.mount"),
            mount_relative_to_base=_bool(raw.get("mount_relative_to_base", False), f"{context}.mount_relative_to_base"),
            joint_name=raw.get("joint_name"),
            joint_pose=_six(raw["joint_pose"], f"{context}.joint_pose") if "joint_pose" in raw else None,
            include_name=raw.get("include_name"),
            child_link=str(raw["child_link"]) if "child_link" in raw else None,
            joint_pose_relative_to_base=_bool(
                raw.get("joint_pose_relative_to_base", True),
                f"{context}.joint_pose_relative_to_base",
            ),
        )
    if kind == "gpu_lidar":
        return GpuLidarSensorSpec(
            kind=kind,
            # Default to the names PX4 hardcodes (see NATIVE_LIDAR_* below). These
            # used to be _required, which is how a real vehicle got `lidar_link` /
            # `down_lidar` from an operator form and produced zero distance_sensor
            # rows in flight.
            link_name=str(raw.get("link_name", NATIVE_LIDAR_LINK_NAME)),
            housing=raw.get("housing"),
            housing_mount=_six(raw["housing_mount"], f"{context}.housing_mount") if "housing_mount" in raw else None,
            mount=_six(_required(raw, "mount", context), f"{context}.mount"),
            sensor_name=str(raw.get("sensor_name", NATIVE_LIDAR_SENSOR_NAME)),
            sensor_pose=_six(_required(raw, "sensor_pose", context), f"{context}.sensor_pose"),
            h_samples=int(_required(raw, "h_samples", context)),
            v_samples=int(_required(raw, "v_samples", context)),
            h_min_angle_rad=float(_required(raw, "h_min_angle_rad", context)),
            h_max_angle_rad=float(_required(raw, "h_max_angle_rad", context)),
            v_min_angle_rad=float(_required(raw, "v_min_angle_rad", context)),
            v_max_angle_rad=float(_required(raw, "v_max_angle_rad", context)),
            range_min_m=float(_required(raw, "range_min_m", context)),
            range_max_m=float(_required(raw, "range_max_m", context)),
            range_resolution_m=float(_required(raw, "range_resolution_m", context)),
            rate_hz=float(_required(raw, "rate_hz", context)),
            visualize=_bool(_required(raw, "visualize", context), f"{context}.visualize"),
            housing_joint_name=raw.get("housing_joint_name"),
            sensor_joint_name=raw.get("sensor_joint_name"),
            mount_relative_to_base=_bool(raw.get("mount_relative_to_base", True), f"{context}.mount_relative_to_base"),
            housing_mount_relative_to_base=_bool(
                raw.get("housing_mount_relative_to_base", True),
                f"{context}.housing_mount_relative_to_base",
            ),
            always_on=int(raw.get("always_on", 1)),
            gz_frame_id=raw.get("gz_frame_id"),
            mass=float(raw.get("mass", 0.001)),
            inertia_diagonal=(
                _six([*raw["inertia_diagonal"], 0, 0, 0], f"{context}.inertia_diagonal")[:3]
                if "inertia_diagonal" in raw else (0.00001, 0.00001, 0.00001)
            ),
            h_resolution=int(raw.get("h_resolution", 1)),
            v_resolution=int(raw.get("v_resolution", 1)),
        )
    if kind == "camera":
        return CameraSensorSpec(
            kind=kind,
            link_name=str(_required(raw, "link_name", context)),
            hfov_rad=float(_required(raw, "hfov_rad", context)),
            width=int(_required(raw, "width", context)),
            height=int(_required(raw, "height", context)),
            rate_hz=float(_required(raw, "rate_hz", context)),
            mount=_six(_required(raw, "mount", context), f"{context}.mount"),
            sensor_name=str(raw.get("sensor_name", "camera")),
            sensor_pose=_six(raw.get("sensor_pose", [0, 0, 0, 0, 0, 0]), f"{context}.sensor_pose"),
            joint_name=raw.get("joint_name"),
            mount_relative_to_base=_bool(raw.get("mount_relative_to_base", True), f"{context}.mount_relative_to_base"),
            always_on=int(raw.get("always_on", 1)),
            visualize=_bool(raw.get("visualize", True), f"{context}.visualize"),
            gz_frame_id=raw.get("gz_frame_id"),
            mass=float(raw.get("mass", 0.001)),
            inertia_diagonal=(
                _six([*raw["inertia_diagonal"], 0, 0, 0], f"{context}.inertia_diagonal")[:3]
                if "inertia_diagonal" in raw else (0.00001, 0.00001, 0.00001)
            ),
            clip_near=float(raw.get("clip_near", 0.1)),
            clip_far=float(raw.get("clip_far", 3000.0)),
        )
    raise ValueError(f"{context}.kind unsupported: {kind}")


def parse_vehicle_spec(spec: VehicleSpec | dict[str, Any]) -> VehicleSpec:
    if isinstance(spec, VehicleSpec):
        return spec
    if not isinstance(spec, dict):
        raise ValueError("spec must be a mapping")
    sensors = tuple(_parse_sensor(raw, i) for i, raw in enumerate(spec.get("sensors") or []))
    boot_params = spec.get("boot_params") or {}
    if not isinstance(boot_params, dict):
        raise ValueError("boot_params must be a mapping")
    return VehicleSpec(
        name=str(_required(spec, "name", "spec")),
        base=str(_required(spec, "base", "spec")),
        base_airframe=str(_required(spec, "base_airframe", "spec")),
        description=str(_required(spec, "description", "spec")),
        sensors=sensors,
        boot_params=dict(boot_params),
    )


def _fmt_num(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.12g}"


def _fmt_pose(pose: tuple[float, float, float, float, float, float]) -> str:
    return " ".join(_fmt_num(v) for v in pose)


def _fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def _pose_tag(pose: tuple[float, float, float, float, float, float], *, relative_to_base: bool, indent: str) -> str:
    attr = ' relative_to="base_link"' if relative_to_base else ""
    return f"{indent}<pose{attr}>{_fmt_pose(pose)}</pose>"


def _resolve_submodel_sdf(model: str, databoss_models_dir: Path, px4_models_dir: Path) -> Path:
    for root in (databoss_models_dir, px4_models_dir):
        candidate = root / model / "model.sdf"
        if candidate.is_file():
            return candidate
    raise ValueError(f"submodel {model!r} has no model.sdf under {databoss_models_dir} or {px4_models_dir}")


def _discover_child_link(
    model: str,
    databoss_models_dir: Path,
    px4_models_dir: Path,
    *,
    child_link: str | None = None,
) -> str:
    sdf = _resolve_submodel_sdf(model, databoss_models_dir, px4_models_dir)
    if child_link is None:
        return discover_single_model_link_name(sdf)
    names = discover_model_link_names(sdf)
    if child_link not in names:
        available = ", ".join(names) if names else "(none)"
        raise ValueError(f"child_link {child_link!r} is not a top-level link in {sdf}; discovered links: {available}")
    return child_link


def _camera_hfov_from_submodel(model: str, databoss_models_dir: Path, px4_models_dir: Path) -> float | None:
    sdf = _resolve_submodel_sdf(model, databoss_models_dir, px4_models_dir)
    block = extract_sensor_block(sdf.read_text(), "camera")
    if block is None:
        return None
    raw = sdf_value(block, "horizontal_fov")
    return float(raw) if raw is not None else None


def _next_free_autostart_id(px4_airframes_dir: Path) -> int:
    used: set[int] = set()
    if px4_airframes_dir.is_dir():
        for path in px4_airframes_dir.glob("*_gz_*"):
            prefix = path.name.split("_", 1)[0]
            if prefix.isdigit():
                used.add(int(prefix))
    candidate = 4024
    while candidate in used:
        candidate += 1
    return candidate


def _validate(
    spec: VehicleSpec,
    databoss_models_dir: Path,
    px4_models_dir: Path,
    *,
    allow_existing_name: bool,
) -> None:
    if not NAME_RE.fullmatch(spec.name):
        raise ValueError("name must match [a-z0-9_]+")
    if spec.base != "x500":
        raise ValueError(f"base must be x500: {X500_ONLY_REASON}")
    if not allow_existing_name:
        if (databoss_models_dir / spec.name).exists():
            raise ValueError(f"name collides with existing DATABOSS model: {databoss_models_dir / spec.name}")
        if (px4_models_dir / spec.name).exists():
            raise ValueError(f"name collides with existing PX4 model: {px4_models_dir / spec.name}")
    # Pattern B is opted into by disabling the native GZBridge lidar path at boot.
    # Accept 0 as int or str, since boot_params come straight from YAML/JSON.
    pattern_b = str(spec.boot_params.get(PATTERN_B_PARAM, "")).strip() == "0"
    native_lidars = 0
    for sensor in spec.sensors:
        if not isinstance(sensor, GpuLidarSensorSpec):
            continue
        if sensor.h_samples == 1 and sensor.v_samples == 1:
            raise ValueError(f"gpu_lidar {sensor.sensor_name!r} rejects h_samples == 1 and v_samples == 1: {DEGENERATE_LIDAR_REASON}")
        canonical = (
            sensor.link_name == NATIVE_LIDAR_LINK_NAME
            and sensor.sensor_name == NATIVE_LIDAR_SENSOR_NAME
        )
        if not canonical and not pattern_b:
            raise ValueError(
                f"gpu_lidar link_name={sensor.link_name!r} sensor_name={sensor.sensor_name!r} "
                f"is not the canonical pair and {PATTERN_B_PARAM} is not 0: "
                f"{NON_CANONICAL_LIDAR_REASON}"
            )
        if canonical and not pattern_b:
            native_lidars += 1
    if native_lidars > 1:
        raise ValueError(f"{native_lidars} gpu_lidars claim the native path: {MULTI_NATIVE_LIDAR_REASON}")


def _render_inertial(mass: float, inertia_diagonal: tuple[float, float, float], indent: str) -> list[str]:
    ixx, iyy, izz = inertia_diagonal
    return [
        f"{indent}<inertial>",
        f"{indent}  <mass>{_fmt_num(mass)}</mass>",
        f"{indent}  <inertia>",
        f"{indent}    <ixx>{_fmt_num(ixx)}</ixx>",
        f"{indent}    <iyy>{_fmt_num(iyy)}</iyy>",
        f"{indent}    <izz>{_fmt_num(izz)}</izz>",
        f"{indent}    <ixy>0.0</ixy>",
        f"{indent}    <ixz>0.0</ixz>",
        f"{indent}    <iyz>0.0</iyz>",
        f"{indent}  </inertia>",
        f"{indent}</inertial>",
    ]


def _render_include_sensor(sensor: IncludeSensorSpec, databoss_models_dir: Path, px4_models_dir: Path) -> list[str]:
    child = _discover_child_link(sensor.model, databoss_models_dir, px4_models_dir, child_link=sensor.child_link)
    joint_name = sensor.joint_name or f"{sensor.model}_joint"
    lines = [
        "    <include merge='true'>",
        f"      <uri>model://{sensor.model}</uri>",
        _pose_tag(sensor.mount, relative_to_base=sensor.mount_relative_to_base, indent="      "),
    ]
    if sensor.include_name:
        lines.append(f"      <name>{sensor.include_name}</name>")
    lines.extend([
        "    </include>",
        f"    <joint name=\"{joint_name}\" type=\"fixed\">",
        "      <parent>base_link</parent>",
        f"      <child>{child}</child>",
    ])
    if sensor.joint_pose is not None:
        lines.append(_pose_tag(sensor.joint_pose, relative_to_base=sensor.joint_pose_relative_to_base, indent="      "))
    lines.append("    </joint>")
    return lines


def _render_gpu_lidar(sensor: GpuLidarSensorSpec, databoss_models_dir: Path, px4_models_dir: Path) -> list[str]:
    lines: list[str] = []
    if sensor.housing:
        if sensor.housing_mount is None:
            raise ValueError(f"gpu_lidar {sensor.sensor_name!r} housing_mount is required when housing is set")
        housing_child = _discover_child_link(sensor.housing, databoss_models_dir, px4_models_dir)
        housing_joint_name = sensor.housing_joint_name or f"{sensor.sensor_name}_model_joint"
        lines.extend([
            "    <include merge='true'>",
            f"      <uri>model://{sensor.housing}</uri>",
            _pose_tag(sensor.housing_mount, relative_to_base=sensor.housing_mount_relative_to_base, indent="      "),
            "    </include>",
            f"    <joint name=\"{housing_joint_name}\" type=\"fixed\">",
            "      <parent>base_link</parent>",
            f"      <child>{housing_child}</child>",
            "      <pose relative_to=\"base_link\">0 0 0 0 0 0</pose>",
            "    </joint>",
        ])
    sensor_joint_name = sensor.sensor_joint_name or f"{sensor.sensor_name}_sensor_joint"
    gz_frame_id = sensor.gz_frame_id or sensor.link_name
    lines.extend([
        f"    <joint name=\"{sensor_joint_name}\" type=\"fixed\">",
        "      <parent>base_link</parent>",
        f"      <child>{sensor.link_name}</child>",
        "    </joint>",
        f"    <link name=\"{sensor.link_name}\">",
        _pose_tag(sensor.mount, relative_to_base=sensor.mount_relative_to_base, indent="      "),
    ])
    lines.extend(_render_inertial(sensor.mass, sensor.inertia_diagonal, "      "))
    lines.extend([
        f"      <sensor name='{sensor.sensor_name}' type='gpu_lidar'>",
        f"        <gz_frame_id>{gz_frame_id}</gz_frame_id>",
        f"        <pose>{_fmt_pose(sensor.sensor_pose)}</pose>",
        f"        <update_rate>{_fmt_num(sensor.rate_hz)}</update_rate>",
        "        <ray>",
        "          <scan>",
        "            <horizontal>",
        f"              <samples>{sensor.h_samples}</samples>",
        f"              <resolution>{sensor.h_resolution}</resolution>",
        f"              <min_angle>{_fmt_num(sensor.h_min_angle_rad)}</min_angle>",
        f"              <max_angle>{_fmt_num(sensor.h_max_angle_rad)}</max_angle>",
        "            </horizontal>",
        "            <vertical>",
        f"              <samples>{sensor.v_samples}</samples>",
        f"              <resolution>{sensor.v_resolution}</resolution>",
        f"              <min_angle>{_fmt_num(sensor.v_min_angle_rad)}</min_angle>",
        f"              <max_angle>{_fmt_num(sensor.v_max_angle_rad)}</max_angle>",
        "            </vertical>",
        "          </scan>",
        "          <range>",
        f"            <min>{_fmt_num(sensor.range_min_m)}</min>",
        f"            <max>{_fmt_num(sensor.range_max_m)}</max>",
        f"            <resolution>{_fmt_num(sensor.range_resolution_m)}</resolution>",
        "          </range>",
        "        </ray>",
        f"        <always_on>{sensor.always_on}</always_on>",
        f"        <visualize>{_fmt_bool(sensor.visualize)}</visualize>",
        "      </sensor>",
        "    </link>",
    ])
    return lines


def _render_camera(sensor: CameraSensorSpec) -> list[str]:
    joint_name = sensor.joint_name or f"{sensor.sensor_name}_joint"
    gz_frame_id = sensor.gz_frame_id or sensor.link_name
    lines = [
        f"    <joint name=\"{joint_name}\" type=\"fixed\">",
        "      <parent>base_link</parent>",
        f"      <child>{sensor.link_name}</child>",
        "    </joint>",
        f"    <link name=\"{sensor.link_name}\">",
        _pose_tag(sensor.mount, relative_to_base=sensor.mount_relative_to_base, indent="      "),
    ]
    lines.extend(_render_inertial(sensor.mass, sensor.inertia_diagonal, "      "))
    lines.extend([
        f"      <sensor name='{sensor.sensor_name}' type='camera'>",
        f"        <gz_frame_id>{gz_frame_id}</gz_frame_id>",
        f"        <pose>{_fmt_pose(sensor.sensor_pose)}</pose>",
        "        <camera>",
        f"          <horizontal_fov>{_fmt_num(sensor.hfov_rad)}</horizontal_fov>",
        "          <image>",
        f"            <width>{sensor.width}</width>",
        f"            <height>{sensor.height}</height>",
        "          </image>",
        "          <clip>",
        f"            <near>{_fmt_num(sensor.clip_near)}</near>",
        f"            <far>{_fmt_num(sensor.clip_far)}</far>",
        "          </clip>",
        "        </camera>",
        f"        <always_on>{sensor.always_on}</always_on>",
        f"        <update_rate>{_fmt_num(sensor.rate_hz)}</update_rate>",
        f"        <visualize>{_fmt_bool(sensor.visualize)}</visualize>",
        "      </sensor>",
        "    </link>",
    ])
    return lines


def _render_model_sdf(spec: VehicleSpec, databoss_models_dir: Path, px4_models_dir: Path) -> tuple[str, float | None]:
    camera_hfovs: list[float] = []
    lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        f"<!-- DATABOSS Phase 20A composed vehicle: {spec.name}.",
        f"     Description/spec: {spec.description}",
        f"     Source of truth: src/databoss_sim/models/{spec.name}/;",
        f"     deployed copy: PX4 Tools/simulation/gz/models/{spec.name}. -->",
        "<sdf version='1.9'>",
        f"  <model name='{spec.name}'>",
        "    <self_collide>false</self_collide>",
        "    <include merge='true'>",
        f"      <uri>{spec.base}</uri>",
        "    </include>",
    ]
    for sensor in spec.sensors:
        if isinstance(sensor, IncludeSensorSpec):
            lines.extend(_render_include_sensor(sensor, databoss_models_dir, px4_models_dir))
            hfov = _camera_hfov_from_submodel(sensor.model, databoss_models_dir, px4_models_dir)
            if hfov is not None:
                camera_hfovs.append(hfov)
        elif isinstance(sensor, GpuLidarSensorSpec):
            lines.extend(_render_gpu_lidar(sensor, databoss_models_dir, px4_models_dir))
        elif isinstance(sensor, CameraSensorSpec):
            lines.extend(_render_camera(sensor))
            camera_hfovs.append(sensor.hfov_rad)
    lines.extend([
        "  </model>",
        "</sdf>",
        "",
    ])
    return "\n".join(lines), camera_hfovs[0] if camera_hfovs else None


def _render_model_config(spec: VehicleSpec) -> str:
    return "\n".join([
        "<?xml version=\"1.0\"?>",
        "<model>",
        f"  <name>{spec.name}</name>",
        "  <version>1.0</version>",
        "  <sdf version=\"1.9\">model.sdf</sdf>",
        "  <author>",
        "    <name>DATABOSS</name>",
        "    <email>n/a</email>",
        "  </author>",
        f"  <description>{spec.description}</description>",
        "</model>",
        "",
    ])


def _render_airframe(spec: VehicleSpec) -> str:
    lines = [
        "#!/bin/sh",
        "#",
        f"# @name Gazebo {spec.name} (DATABOSS Phase 20A)",
        "#",
        "# @type Quadrotor",
        "#",
        "",
        f"PX4_SIM_MODEL=${{PX4_SIM_MODEL:={spec.name}}}",
        "",
        f". ${{R}}etc/init.d-posix/airframes/{spec.base_airframe}",
        "",
    ]
    for key, value in spec.boot_params.items():
        lines.append(f"param set-default {key} {value}")
    lines.append("")
    return "\n".join(lines)


def compose_vehicle(
    spec: VehicleSpec | dict[str, Any],
    *,
    databoss_models_dir: Path = DEFAULT_DATABOSS_MODELS_DIR,
    px4_models_dir: Path = DEFAULT_PX4_MODELS_DIR,
    px4_airframes_dir: Path = DEFAULT_PX4_AIRFRAMES_DIR,
    allow_existing_name: bool = False,
) -> ComposedVehicle:
    parsed = parse_vehicle_spec(spec)
    _validate(parsed, databoss_models_dir, px4_models_dir, allow_existing_name=allow_existing_name)
    autostart_id = _next_free_autostart_id(px4_airframes_dir)
    model_sdf, camera_hfov = _render_model_sdf(parsed, databoss_models_dir, px4_models_dir)
    airframe_filename = f"{autostart_id}_gz_{parsed.name}"
    return ComposedVehicle(
        model_sdf=model_sdf,
        model_config=_render_model_config(parsed),
        airframe=_render_airframe(parsed),
        airframe_filename=airframe_filename,
        autostart_id=autostart_id,
        camera_hfov_rad=camera_hfov,
        pins_entries={
            "models": [f"src/databoss_sim/models/{parsed.name}"],
            "airframes": [f"src/databoss_sim/airframes/{airframe_filename}"],
        },
        warnings=[],
    )


def write_vehicle(composed: ComposedVehicle, models_dir: Path, airframes_dir: Path, *, overwrite: bool = False) -> list[Path]:
    model_dir = models_dir / composed.airframe_filename.split("_gz_", 1)[1]
    paths = [
        model_dir / "model.sdf",
        model_dir / "model.config",
        airframes_dir / composed.airframe_filename,
    ]
    if not overwrite:
        existing = [path for path in paths if path.exists()]
        if existing:
            raise FileExistsError("refusing to overwrite existing vehicle files: " + ", ".join(str(p) for p in existing))
    model_dir.mkdir(parents=True, exist_ok=True)
    airframes_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.sdf").write_text(composed.model_sdf)
    (model_dir / "model.config").write_text(composed.model_config)
    airframe_path = airframes_dir / composed.airframe_filename
    airframe_path.write_text(composed.airframe)
    return paths
