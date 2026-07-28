"""RangefinderModel interface.

Frames: single downward-beam range along the sensor's local -Z (nadir) axis,
inherited unchanged from the existing accepted TF03-style gpu_lidar geometry
(Phase 8D/8E) -- this module only changes how the scalar range is derived
from a raw multi-sample Gazebo LaserScan, not the sensor's mount frame.

update() consumes one LaserScan's flattened ranges[] array (grid layout:
row = vertical ring, column = horizontal sample within that ring, matching
gz-sensors' GpuLidarSensor fill order -- confirm empirically against a real
scan during the Phase 15 smoke test, see phase doc) and always returns a
RangeSample (valid=False rather than None, since every scan tick has SOME
result even if it's "no target in range").
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class RangeSample:
    t_s: float          # timestamp of the scan (sim time, s)
    range_m: float       # corrected/selected range, meters (meaningless if not valid)
    valid: bool          # False if no in-range return was found
    n_pixels_used: int   # how many grid samples contributed to the selection (diagnostic)


class RangefinderModel(abc.ABC):
    """Multi-sample LaserScan -> single corrected range, swappable implementation."""

    @abc.abstractmethod
    def update(
        self,
        ranges: list[float],
        *,
        horizontal_count: int,
        vertical_count: int,
        angle_min: float,
        angle_step: float,
        vertical_angle_min: float,
        vertical_angle_step: float,
        range_min: float,
        range_max: float,
        t_s: float,
    ) -> RangeSample:
        """Reduce one raw grid scan to a single corrected range reading."""
