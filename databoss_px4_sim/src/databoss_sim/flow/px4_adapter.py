"""FlowSample -> MAVLink OPTICAL_FLOW_RAD field mapping (pure, no I/O).

Decisions (docs/phases/phase_08g_live_flow_bridge.md):
- D3: integrated gyros default to NaN -> PX4 mavlink_receiver sets
  delta_angle_available=false. Phase 8L Gate 5 can pass an explicit
  same-window gyro integral for rotation sanity A/B tests.
- D4: distance is -1.0 (unknown) -> EKF2 takes HAGL from the separate
  TF03 distance_sensor stream; the flow message never carries range.
- D5: the camera->body axis mapping is a named config value, not code.
  Default "xy" (identity): PX4's own OpticalFlowSystem plugin feeds
  calcFlow's scene-displacement pixel flow into integrated_x/y with no
  swap or sign flip, from a camera mounted exactly like ours
  (pose `0 1.5707 0`, nadir, zero yaw). Phase 8N's ULog sign sentinel
  (`estimator_optical_flow_vel` vs GPS body velocity) supersedes the earlier
  Gate 6b `-x-y` workaround and confirms identity "xy" as the sign-correct
  bridge candidate.
- D6: raw estimator quality (algorithm metric, untouched upstream) is
  linearly rescaled here to use the 0..255 range EKF2 interpolates its
  measurement noise over. quality 0 stays 0 (invalid marker).
"""
from __future__ import annotations

import math

from .estimator import FlowSample

# integrated_x, integrated_y <- (sign, source) over the camera-frame sample.
AXIS_MAPS = {
    "xy": ((1, "x"), (1, "y")),
    "x-y": ((1, "x"), (-1, "y")),
    "-xy": ((-1, "x"), (1, "y")),
    "-x-y": ((-1, "x"), (-1, "y")),
    "yx": ((1, "y"), (1, "x")),
    "y-x": ((1, "y"), (-1, "x")),
    "-yx": ((-1, "y"), (1, "x")),
    "-y-x": ((-1, "y"), (-1, "x")),
}

DISTANCE_UNKNOWN = -1.0


def rescale_quality(quality: int, in_min: float, in_max: float) -> int:
    """Linear map [in_min, in_max] -> [1, 255], clamped; 0 stays 0 (invalid)."""
    if quality <= 0:
        return 0
    if in_max <= in_min:
        raise ValueError(f"quality map needs in_max > in_min, got [{in_min}, {in_max}]")
    scaled = (quality - in_min) / (in_max - in_min) * 254.0 + 1.0
    return int(round(min(255.0, max(1.0, scaled))))


def flow_sample_to_optical_flow_rad(
    sample: FlowSample,
    *,
    axis_map: str = "xy",
    quality_in_min: float | None = None,
    quality_in_max: float | None = None,
    sensor_id: int = 0,
    gyro_integral_rad: tuple[float, float, float] | None = None,
) -> dict:
    """Return kwargs for pymavlink optical_flow_rad_send (field names match)."""
    try:
        (sign_x, src_x), (sign_y, src_y) = AXIS_MAPS[axis_map]
    except KeyError as exc:
        raise ValueError(f"unknown axis map: {axis_map!r}; known: {sorted(AXIS_MAPS)}") from exc

    cam = {"x": sample.integrated_x_rad, "y": sample.integrated_y_rad}
    quality = sample.quality
    if quality_in_min is not None and quality_in_max is not None:
        quality = rescale_quality(quality, quality_in_min, quality_in_max)
    if gyro_integral_rad is None:
        gyro_integral_rad = (float("nan"), float("nan"), float("nan"))

    return {
        "time_usec": int(sample.t_s * 1e6),  # forensics only: PX4 arrival-stamps (D2)
        "sensor_id": sensor_id,
        "integration_time_us": int(sample.integration_dt_s * 1e6),
        "integrated_x": sign_x * cam[src_x],
        "integrated_y": sign_y * cam[src_y],
        "integrated_xgyro": gyro_integral_rad[0],
        "integrated_ygyro": gyro_integral_rad[1],
        "integrated_zgyro": gyro_integral_rad[2],
        "temperature": 0,
        "quality": quality,
        "time_delta_distance_us": 0,
        "distance": DISTANCE_UNKNOWN,
    }


def self_test() -> None:
    """Unit checks for every axis map and the quality rescale."""
    s = FlowSample(t_s=12.5, integrated_x_rad=0.02, integrated_y_rad=-0.01,
                   integration_dt_s=0.1, quality=48, n_matches=19)
    expected = {
        "xy": (0.02, -0.01), "x-y": (0.02, 0.01),
        "-xy": (-0.02, -0.01), "-x-y": (-0.02, 0.01),
        "yx": (-0.01, 0.02), "y-x": (-0.01, -0.02),
        "-yx": (0.01, 0.02), "-y-x": (0.01, -0.02),
    }
    for name, (ex, ey) in expected.items():
        m = flow_sample_to_optical_flow_rad(s, axis_map=name)
        assert math.isclose(m["integrated_x"], ex) and math.isclose(m["integrated_y"], ey), name
        assert math.isnan(m["integrated_xgyro"]) and m["distance"] == DISTANCE_UNKNOWN
        assert m["integration_time_us"] == 100000 and m["time_usec"] == 12500000
    assert rescale_quality(0, 20, 60) == 0
    assert rescale_quality(20, 20, 60) == 1
    assert rescale_quality(60, 20, 60) == 255
    assert rescale_quality(40, 20, 60) == 128
    assert rescale_quality(200, 20, 60) == 255  # clamp
    assert rescale_quality(5, 20, 60) == 1      # clamp
    m = flow_sample_to_optical_flow_rad(s, quality_in_min=20, quality_in_max=60)
    assert m["quality"] == 179, m["quality"]
    m = flow_sample_to_optical_flow_rad(s, gyro_integral_rad=(0.1, -0.2, 0.3))
    assert m["integrated_xgyro"] == 0.1
    assert m["integrated_ygyro"] == -0.2
    assert m["integrated_zgyro"] == 0.3
    print("px4_adapter self_test OK")


if __name__ == "__main__":
    self_test()
