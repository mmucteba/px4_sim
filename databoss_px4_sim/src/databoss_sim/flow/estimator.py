"""FlowEstimator interface.

Frames: grayscale uint8 arrays in the camera frame (x right, y down).
Angles: integrated flow in radians about the camera axes over the integration
interval, following the PX4 sensor_optical_flow convention (pixel_flow =
integrated flow angle in rad; positive x flow = scene moving toward +x in the
image = vehicle moving toward -x).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class FlowSample:
    t_s: float                  # timestamp of the newest frame (sim time, s)
    integrated_x_rad: float     # integrated flow about camera x over dt
    integrated_y_rad: float
    integration_dt_s: float     # time between the two frames used
    quality: int                # 0..255, 0 = invalid
    n_matches: int              # feature matches used (diagnostic)


class FlowEstimator(abc.ABC):
    """Two-frame optical-flow estimator with swappable implementation."""

    def __init__(self, focal_px: float):
        # focal length in pixels for the frames actually fed to update()
        # (scale with any downsampling: focal_px = (width/2)/tan(hfov/2)).
        self.focal_px = float(focal_px)

    @abc.abstractmethod
    def reset(self) -> None:
        """Drop internal state (previous frame)."""

    @abc.abstractmethod
    def update(self, gray, t_s: float) -> FlowSample | None:
        """Feed the next grayscale frame; return a FlowSample once two frames
        are available, else None."""
