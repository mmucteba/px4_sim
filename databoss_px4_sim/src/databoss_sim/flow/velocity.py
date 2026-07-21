"""Flow -> ground velocity conversion (analysis-side).

v1: no gyro compensation. Valid for hover / near-level flight where vehicle
rotation between frames is small; documented as a Phase 8F limitation.

Frames: camera x points right in the image; with the DATABOSS downward camera
(yaw 0, camera pitched straight down per x500_mono_cam_down mounting) image x
maps to body/world axes up to a fixed rotation. For hover validation we
compare speed magnitudes and axis-aligned components after empirically fixing
the sign/axis mapping against truth (recorded in the phase doc).
"""
from __future__ import annotations

from .estimator import FlowSample


def flow_to_ground_velocity(sample: FlowSample, distance_m: float) -> tuple[float, float] | None:
    """Integrated flow angle + range -> ground-relative velocity components
    in the camera image frame (m/s). None if the sample is unusable."""
    if sample.quality <= 0 or sample.integration_dt_s <= 0.0 or distance_m <= 0.0:
        return None

    # Scene shift in image = -vehicle translation over ground.
    vx = -(sample.integrated_x_rad / sample.integration_dt_s) * distance_m
    vy = -(sample.integrated_y_rad / sample.integration_dt_s) * distance_m
    return vx, vy
