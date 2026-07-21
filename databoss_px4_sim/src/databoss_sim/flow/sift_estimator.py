"""SIFT-based two-frame flow estimator (v1).

Method: SIFT keypoints on consecutive frames, brute-force matching with a
Lowe ratio test, median pixel displacement, converted to integrated flow
angle via the pinhole small-angle approximation (rad = px / focal_px).

Deliberately simple: no gyro compensation (valid for hover/near-level
flight), no outlier RANSAC beyond the ratio test + median. Replaceable via
the FlowEstimator registry.
"""
from __future__ import annotations

import cv2
import numpy as np

from .estimator import FlowEstimator, FlowSample


class SiftFlowEstimator(FlowEstimator):
    def __init__(
        self,
        focal_px: float,
        n_features: int = 400,
        ratio: float = 0.75,
        min_matches: int = 8,
    ):
        super().__init__(focal_px)
        self._sift = cv2.SIFT_create(nfeatures=n_features)
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)
        self._ratio = float(ratio)
        self._min_matches = int(min_matches)
        self._prev = None  # (t_s, keypoints, descriptors)

    def reset(self) -> None:
        self._prev = None

    def update(self, gray, t_s: float) -> FlowSample | None:
        kp, desc = self._sift.detectAndCompute(gray, None)
        prev = self._prev
        self._prev = (t_s, kp, desc)

        if prev is None:
            return None

        prev_t, prev_kp, prev_desc = prev
        dt = t_s - prev_t
        if dt <= 0.0:
            return None

        if desc is None or prev_desc is None or len(kp) < self._min_matches or len(prev_kp) < self._min_matches:
            return FlowSample(t_s, 0.0, 0.0, dt, 0, 0)

        pairs = self._matcher.knnMatch(prev_desc, desc, k=2)
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < self._ratio * n.distance]

        if len(good) < self._min_matches:
            return FlowSample(t_s, 0.0, 0.0, dt, 0, len(good))

        dx = np.median([kp[m.trainIdx].pt[0] - prev_kp[m.queryIdx].pt[0] for m in good])
        dy = np.median([kp[m.trainIdx].pt[1] - prev_kp[m.queryIdx].pt[1] for m in good])

        # Quality: match count saturated at 100 matches -> 255 scale.
        quality = int(min(255, 255 * len(good) / 100))

        return FlowSample(
            t_s=t_s,
            integrated_x_rad=float(dx) / self.focal_px,
            integrated_y_rad=float(dy) / self.focal_px,
            integration_dt_s=dt,
            quality=quality,
            n_matches=len(good),
        )
