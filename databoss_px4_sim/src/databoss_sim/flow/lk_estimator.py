"""Sparse pyramidal Lucas-Kanade optical-flow estimator.

This estimator is the first Phase 8J bridge-side approximation of PX4's
stock Gazebo flow approach: maintain active image features, track them with
LK, reject inconsistent tracks, and report the mean scene displacement as an
integrated angular flow sample.
"""
from __future__ import annotations

import cv2
import numpy as np

from .estimator import FlowEstimator, FlowSample


class LucasKanadeFlowEstimator(FlowEstimator):
    def __init__(
        self,
        focal_px: float,
        max_corners: int = 160,
        quality_level: float = 0.01,
        min_distance: float = 6.0,
        block_size: int = 3,
        win_size: int = 21,
        max_level: int = 3,
        min_tracks: int = 8,
        fb_max_error_px: float = 1.5,
        confidence_multiplier: float = 1.5,
        mad_multiplier: float = 3.0,
        max_flow_rate_rad_s: float = 1.2,
    ):
        super().__init__(focal_px)
        self.max_corners = int(max_corners)
        self.quality_level = float(quality_level)
        self.min_distance = float(min_distance)
        self.block_size = int(block_size)
        self.win_size = int(win_size)
        self.max_level = int(max_level)
        self.min_tracks = int(min_tracks)
        self.fb_max_error_px = float(fb_max_error_px)
        self.confidence_multiplier = float(confidence_multiplier)
        self.mad_multiplier = float(mad_multiplier)
        self.max_flow_rate_rad_s = float(max_flow_rate_rad_s)
        self._prev_gray = None
        self._prev_t_s: float | None = None
        self._prev_pts = None

    def reset(self) -> None:
        self._prev_gray = None
        self._prev_t_s = None
        self._prev_pts = None

    def _detect(self, gray, existing=None):
        remaining = self.max_corners - (0 if existing is None else len(existing))
        if remaining <= 0:
            return existing

        mask = np.full(gray.shape, 255, dtype=np.uint8)
        if existing is not None and len(existing):
            pts = existing.reshape(-1, 2)
            radius = max(1, int(round(self.min_distance)))
            for x, y in pts:
                cv2.circle(mask, (int(round(x)), int(round(y))), radius, 0, -1)

        corners = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=remaining,
            qualityLevel=self.quality_level,
            minDistance=self.min_distance,
            mask=mask,
            blockSize=self.block_size,
        )
        if corners is None:
            return existing
        corners = corners.astype(np.float32)
        if existing is None or not len(existing):
            return corners
        return np.concatenate([existing.astype(np.float32), corners], axis=0)

    def _prime(self, gray, t_s: float) -> None:
        self._prev_gray = gray.copy()
        self._prev_t_s = float(t_s)
        self._prev_pts = self._detect(gray)

    def update(self, gray, t_s: float) -> FlowSample | None:
        if self._prev_gray is None or self._prev_t_s is None or self._prev_pts is None:
            self._prime(gray, t_s)
            return None

        dt = float(t_s) - self._prev_t_s
        if dt <= 0.0:
            self._prime(gray, t_s)
            return None

        prev_pts = self._prev_pts
        if prev_pts is None or len(prev_pts) < self.min_tracks:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, 0)

        lk_kwargs = dict(
            winSize=(self.win_size, self.win_size),
            maxLevel=self.max_level,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        next_pts, status, _err = cv2.calcOpticalFlowPyrLK(self._prev_gray, gray, prev_pts, None, **lk_kwargs)
        if next_pts is None or status is None:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, 0)

        back_pts, back_status, _back_err = cv2.calcOpticalFlowPyrLK(gray, self._prev_gray, next_pts, None, **lk_kwargs)
        if back_pts is None or back_status is None:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, 0)

        p0 = prev_pts.reshape(-1, 2)
        p1 = next_pts.reshape(-1, 2)
        pb = back_pts.reshape(-1, 2)
        good = (
            (status.reshape(-1) == 1)
            & (back_status.reshape(-1) == 1)
            & np.isfinite(p0).all(axis=1)
            & np.isfinite(p1).all(axis=1)
            & np.isfinite(pb).all(axis=1)
        )
        good &= np.linalg.norm(pb - p0, axis=1) <= self.fb_max_error_px

        if int(good.sum()) < self.min_tracks:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, int(good.sum()))

        disp_all = p1[good] - p0[good]
        median = np.median(disp_all, axis=0)
        residual = np.linalg.norm(disp_all - median, axis=1)
        mad = float(np.median(np.abs(residual - np.median(residual))))
        robust_sigma = 1.4826 * mad
        threshold_px = max(0.5, self.mad_multiplier * robust_sigma)
        inliers = residual <= threshold_px

        if int(inliers.sum()) < self.min_tracks:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, int(inliers.sum()))

        disp = disp_all[inliers]
        tracked = p1[good][inliers].reshape(-1, 1, 2).astype(np.float32)

        dx, dy = np.median(disp, axis=0)
        n_tracks = int(len(disp))
        flow_rate_rad_s = float(np.hypot(dx / self.focal_px, dy / self.focal_px) / dt)
        if self.max_flow_rate_rad_s > 0.0 and flow_rate_rad_s > self.max_flow_rate_rad_s:
            self._prime(gray, t_s)
            return FlowSample(t_s, 0.0, 0.0, dt, 0, n_tracks)

        consistency = max(0.0, min(1.0, 1.0 - (float(np.median(residual[inliers])) / max(1.0, threshold_px * 2.0))))
        coverage = min(1.0, n_tracks / max(1, self.max_corners))
        quality = int(min(255, round(255 * coverage * (0.5 + 0.5 * consistency))))

        refreshed = self._detect(gray, tracked)
        self._prev_gray = gray.copy()
        self._prev_t_s = float(t_s)
        self._prev_pts = refreshed

        return FlowSample(
            t_s=t_s,
            integrated_x_rad=float(dx) / self.focal_px,
            integrated_y_rad=float(dy) / self.focal_px,
            integration_dt_s=dt,
            quality=quality,
            n_matches=n_tracks,
        )
