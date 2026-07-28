"""Broadcom AFBR-S50LV85D-shaped range reduction.

Real sensor: 32-pixel receiver array across a 12.4 x 6.2 deg FoV, but the
850nm transmitter's own beam is much narrower (2 x 2 deg) and only lights
1-3 of the 32 pixels at a time -- the sensor firmware reports the nearest
valid return among whichever few pixels the target actually reflects the
laser back to, not a scene-wide min/median over the full receiver FoV.

This model approximates that by picking the K angularly-nearest grid
samples to boresight (K default 2, datasheet says 1-3) and returning the
minimum valid range among them -- "closest target wins", matching real
multi-zone ToF firmware behavior. A strict +/-1deg (half of the 2deg beam)
angle threshold was tried first and rejected: with the SDF's 8x4 grid
(~1.77deg horizontal / ~2.07deg vertical spacing, chosen to give exactly
32 samples across the real FoV), no vertical sample lands within 1deg of
boresight (nearest is 1.03deg) -- a fixed-angle filter would deterministically
select zero pixels on the vertical axis. K-nearest-neighbor selection is
robust to that, at the cost of being a deliberate approximation rather than
a literal beam-footprint simulation. Revisit if the grid resolution changes.

Accuracy/precision noise below is a PLACEHOLDER (see phase doc) -- the real
AFBR-S50LV85D accuracy/precision spec was not available when this was
written (only FoV/range/pixel-count/ambient-light-tolerance were sourced).
Do not treat sigma_m as validated; it exists so the bridge's structural and
geometric behavior can be exercised before the real noise envelope lands.
"""
from __future__ import annotations

import math
import random

from .model import RangeSample, RangefinderModel


class AFBRModel(RangefinderModel):
    def __init__(
        self,
        *,
        transmitter_pixels: int = 2,
        quantization_m: float = 0.01,   # matches the SDF's placeholder <resolution>
        noise_sigma_m: float = 0.02,    # PLACEHOLDER, not datasheet-sourced
        seed: int | None = None,
    ):
        if not (1 <= transmitter_pixels <= 3):
            raise ValueError(f"transmitter_pixels must be 1..3 per datasheet, got {transmitter_pixels}")
        self.transmitter_pixels = int(transmitter_pixels)
        self.quantization_m = float(quantization_m)
        self.noise_sigma_m = float(noise_sigma_m)
        self._rng = random.Random(seed)

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
        expected = horizontal_count * vertical_count
        if len(ranges) != expected:
            raise ValueError(f"expected {expected} samples ({horizontal_count}x{vertical_count}), got {len(ranges)}")

        # Row-major, row = vertical ring (see module docstring on fill order).
        candidates = []
        for v in range(vertical_count):
            v_angle = vertical_angle_min + v * vertical_angle_step
            for h in range(horizontal_count):
                h_angle = angle_min + h * angle_step
                angular_dist = math.hypot(h_angle, v_angle)
                idx = v * horizontal_count + h
                candidates.append((angular_dist, idx))

        candidates.sort(key=lambda c: c[0])
        selected_idx = [idx for _, idx in candidates[: self.transmitter_pixels]]

        valid_ranges = []
        for idx in selected_idx:
            r = ranges[idx]
            if math.isfinite(r) and range_min <= r <= range_max:
                valid_ranges.append(r)

        if not valid_ranges:
            return RangeSample(t_s=t_s, range_m=float("nan"), valid=False, n_pixels_used=0)

        raw_m = min(valid_ranges)  # closest target wins, matches real ToF firmware
        noisy_m = raw_m + self._rng.gauss(0.0, self.noise_sigma_m)
        quantized_m = round(noisy_m / self.quantization_m) * self.quantization_m
        clamped_m = min(max(quantized_m, range_min), range_max)

        return RangeSample(
            t_s=t_s,
            range_m=clamped_m,
            valid=True,
            n_pixels_used=len(valid_ranges),
        )
