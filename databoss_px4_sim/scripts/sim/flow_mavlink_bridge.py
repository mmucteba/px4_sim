#!/usr/bin/env python3
"""Live modular optical-flow bridge: gz camera -> FlowEstimator -> OPTICAL_FLOW_RAD.

Phase 8G (docs/phases/phase_08g_live_flow_bridge.md). Runs under
venv_bridge python (gz.transport13 from system site-packages + pip pymavlink).

Modes:
  live (default)  subscribe --image-topic, estimate flow per frame (rate-capped),
                  send MAVLink OPTICAL_FLOW_RAD to --connection.
  --replay DIR    feed frames from a flow_recording dir (frames_index.csv)
                  instead of Gazebo; regression harness for estimators.
  --dry-run       compute and log, send nothing (no MAVLink connection).

The optional --scan-topic subscription only logs the latest lidar range into
the sent-CSV for open-loop magnitude checks; per decision D4 the flow message
itself always carries distance = -1 (EKF2 gets HAGL from distance_sensor).

Sent-CSV columns include per-sample estimator compute time (risk: SIFT at
640 px must stay well under the frame period).
"""
from __future__ import annotations

import os

# Force MAVLink 2 common dialect (same as the 8A senders).
os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "common")

import argparse
import csv
import math
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "runner"))

from databoss_sim.flow import FlowSample, make_estimator
from databoss_sim.flow.px4_adapter import flow_sample_to_optical_flow_rad

SENT_CSV_FIELDS = [
    "t_frame_sim_s", "t_wall_s", "compute_s",
    "integrated_x_cam_rad", "integrated_y_cam_rad",
    "integrated_x_sent", "integrated_y_sent", "integration_dt_s",
    "integrated_xgyro_sent", "integrated_ygyro_sent", "integrated_zgyro_sent",
    "gyro_mode", "gyro_samples", "gyro_available",
    "quality_raw", "quality_sent", "n_matches", "range_m", "sent", "mavlink_sent", "send_reason",
]


class GyroBuffer:
    """Time-indexed angular-velocity buffer with trapezoidal integration."""

    def __init__(self, mode: str, max_age_s: float = 5.0):
        self.mode = mode
        self.max_age_s = max_age_s
        self.samples = deque()
        self.lock = threading.Lock()

    def add_body_sample(self, t_s: float, wx: float, wy: float, wz: float) -> None:
        gx, gy, gz = self.transform(wx, wy, wz)
        with self.lock:
            self.samples.append((t_s, gx, gy, gz))
            cutoff = t_s - self.max_age_s
            while len(self.samples) > 2 and self.samples[1][0] < cutoff:
                self.samples.popleft()

    def transform(self, wx: float, wy: float, wz: float) -> tuple[float, float, float]:
        if self.mode == "gz_body":
            return wx, wy, wz
        if self.mode == "gz_camera_down_y90":
            # camera_link is mounted at RPY 0, +90 deg, 0 relative to base_link.
            # Express base_link angular velocity in the camera_link/sensor axes:
            # R_y(+90)^T * [wx, wy, wz] = [-wz, wy, wx].
            return -wz, wy, wx
        raise ValueError(f"unknown gyro mode: {self.mode}")

    def integrate(self, t0_s: float, t1_s: float) -> tuple[tuple[float, float, float], int, bool]:
        if t1_s <= t0_s:
            return (float("nan"), float("nan"), float("nan")), 0, False
        with self.lock:
            rows = list(self.samples)
        if len(rows) < 2 or rows[0][0] > t0_s or rows[-1][0] < t1_s:
            return (float("nan"), float("nan"), float("nan")), len(rows), False

        window = [r for r in rows if t0_s <= r[0] <= t1_s]
        before = next((r for r in reversed(rows) if r[0] < t0_s), None)
        after = next((r for r in rows if r[0] > t1_s), None)
        if before is not None:
            window.insert(0, self.interpolate(before, window[0] if window else after, t0_s))
        elif window and window[0][0] > t0_s:
            window.insert(0, (t0_s, window[0][1], window[0][2], window[0][3]))
        if after is not None:
            window.append(self.interpolate(window[-1] if window else before, after, t1_s))
        elif window and window[-1][0] < t1_s:
            window.append((t1_s, window[-1][1], window[-1][2], window[-1][3]))
        if len(window) < 2:
            return (float("nan"), float("nan"), float("nan")), len(window), False

        total = [0.0, 0.0, 0.0]
        for a, b in zip(window, window[1:]):
            dt = b[0] - a[0]
            if dt <= 0:
                continue
            total[0] += 0.5 * (a[1] + b[1]) * dt
            total[1] += 0.5 * (a[2] + b[2]) * dt
            total[2] += 0.5 * (a[3] + b[3]) * dt
        return (total[0], total[1], total[2]), len(window), True

    @staticmethod
    def interpolate(a, b, t_s: float):
        if a is None or b is None or b[0] <= a[0]:
            row = a or b
            return (t_s, row[1], row[2], row[3])
        alpha = (t_s - a[0]) / (b[0] - a[0])
        return (
            t_s,
            a[1] + alpha * (b[1] - a[1]),
            a[2] + alpha * (b[2] - a[2]),
            a[3] + alpha * (b[3] - a[3]),
        )


class FlowBridge:
    """Frames in -> EKF-ready OPTICAL_FLOW_RAD out. Estimator is a registry name."""

    def __init__(self, args):
        self.args = args
        self.estimator = None          # created lazily: focal needs frame width
        self.conn = None
        self.lock = threading.Lock()
        self.last_sample_t = -1e9
        self.last_range_m = float("nan")
        self.n_sent = 0
        self.n_prime_sent = 0
        self.n_samples = 0
        self.min_period = 1.0 / args.rate_hz if args.rate_hz > 0 else 0.0
        self.gyro_buffer = GyroBuffer(args.gyro_mode) if args.gyro_mode != "nan" else None
        self.sent_csv = None
        self.sent_writer = None
        if args.sent_log:
            Path(args.sent_log).parent.mkdir(parents=True, exist_ok=True)
            self.sent_csv = open(args.sent_log, "w", newline="")
            self.sent_writer = csv.DictWriter(self.sent_csv, fieldnames=SENT_CSV_FIELDS)
            self.sent_writer.writeheader()
            self.sent_csv.flush()

    def connect(self):
        from pymavlink import mavutil
        self.conn = mavutil.mavlink_connection(
            self.args.connection,
            source_system=self.args.source_system,
            source_component=self.args.source_component,
        )
        print(f"MAVLink out: {self.args.connection} "
              f"(sys {self.args.source_system} comp {self.args.source_component})")

    def heartbeat_loop(self, stop: threading.Event):
        from pymavlink import mavutil
        while not stop.is_set():
            self.conn.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            stop.wait(1.0)

    def startup_prime_loop(self, stop: threading.Event):
        """Advertise sensor_optical_flow before camera frames arrive."""
        if self.conn is None or self.args.startup_prime_hz <= 0 or self.args.startup_prime_duration_s <= 0:
            return

        period_s = 1.0 / self.args.startup_prime_hz
        deadline = time.monotonic() + self.args.startup_prime_duration_s
        while not stop.is_set() and time.monotonic() < deadline and self.n_samples == 0:
            sample = FlowSample(
                t_s=time.monotonic(),
                integrated_x_rad=0.0,
                integrated_y_rad=0.0,
                integration_dt_s=max(1e-3, period_s),
                quality=0,
                n_matches=0,
            )
            fields = flow_sample_to_optical_flow_rad(
                sample,
                axis_map=self.args.axis_map,
                quality_in_min=self.args.quality_in_min,
                quality_in_max=self.args.quality_in_max,
                sensor_id=self.args.sensor_id,
            )
            self.conn.mav.optical_flow_rad_send(**fields)
            self.n_prime_sent += 1
            if self.sent_writer:
                self.sent_writer.writerow({
                    "t_frame_sim_s": f"{sample.t_s:.6f}",
                    "t_wall_s": f"{time.time():.6f}",
                    "compute_s": "0.0000",
                    "integrated_x_cam_rad": "0.0000000",
                    "integrated_y_cam_rad": "0.0000000",
                    "integrated_x_sent": "0.0000000",
                    "integrated_y_sent": "0.0000000",
                    "integration_dt_s": f"{sample.integration_dt_s:.4f}",
                    "integrated_xgyro_sent": "nan",
                    "integrated_ygyro_sent": "nan",
                    "integrated_zgyro_sent": "nan",
                    "gyro_mode": self.args.gyro_mode,
                    "gyro_samples": 0,
                    "gyro_available": 0,
                    "quality_raw": 0,
                    "quality_sent": 0,
                    "n_matches": 0,
                    "range_m": f"{self.last_range_m:.4f}",
                    "sent": 0,
                    "mavlink_sent": 1,
                    "send_reason": "startup_prime",
                })
                self.sent_csv.flush()
            stop.wait(period_s)

    def ensure_estimator(self, width: int):
        if self.estimator is None:
            focal_px = (width / 2.0) / math.tan(self.args.hfov_rad / 2.0)
            estimator_kwargs = {"focal_px": focal_px}
            if self.args.estimator == "sift":
                estimator_kwargs.update({
                    "n_features": self.args.sift_n_features,
                    "ratio": self.args.sift_ratio,
                    "min_matches": self.args.sift_min_matches,
                })
            elif self.args.estimator == "lk":
                estimator_kwargs.update({
                    "max_corners": self.args.lk_max_corners,
                    "quality_level": self.args.lk_quality_level,
                    "min_distance": self.args.lk_min_distance,
                    "block_size": self.args.lk_block_size,
                    "win_size": self.args.lk_win_size,
                    "max_level": self.args.lk_max_level,
                    "min_tracks": self.args.lk_min_tracks,
                    "fb_max_error_px": self.args.lk_fb_max_error_px,
                    "confidence_multiplier": self.args.lk_confidence_multiplier,
                    "mad_multiplier": self.args.lk_mad_multiplier,
                    "max_flow_rate_rad_s": self.args.lk_max_flow_rate_rad_s,
                })
            self.estimator = make_estimator(self.args.estimator, **estimator_kwargs)
            print(
                f"estimator={self.args.estimator}, width={width}, focal={focal_px:.1f} px, "
                f"sift_n_features={self.args.sift_n_features}, "
                f"sift_ratio={self.args.sift_ratio}, sift_min_matches={self.args.sift_min_matches}, "
                f"lk_max_corners={self.args.lk_max_corners}, lk_min_tracks={self.args.lk_min_tracks}"
            )

    def should_send(self, sample) -> tuple[bool, str]:
        reasons = []
        range_m = self.last_range_m
        if self.args.send_min_range_m is not None:
            if not math.isfinite(range_m) or range_m < self.args.send_min_range_m:
                reasons.append(f"range<{self.args.send_min_range_m:g}")
        if self.args.send_max_range_m is not None:
            if not math.isfinite(range_m) or range_m > self.args.send_max_range_m:
                reasons.append(f"range>{self.args.send_max_range_m:g}")
        if sample.quality < self.args.send_min_quality:
            reasons.append(f"quality<{self.args.send_min_quality:g}")
        if sample.n_matches < self.args.send_min_matches:
            reasons.append(f"matches<{self.args.send_min_matches}")
        if reasons:
            return False, ",".join(reasons)
        return True, "sent"

    def gyro_for_sample(self, sample: FlowSample) -> tuple[tuple[float, float, float], int, bool]:
        if self.gyro_buffer is None:
            return (float("nan"), float("nan"), float("nan")), 0, False
        t1 = sample.t_s
        t0 = sample.t_s - sample.integration_dt_s
        return self.gyro_buffer.integrate(t0, t1)

    def process_gray(self, gray: np.ndarray, t_sim: float) -> None:
        self.ensure_estimator(gray.shape[1])
        t0 = time.monotonic()
        sample = self.estimator.update(gray, t_sim)
        compute_s = time.monotonic() - t0
        if sample is None:
            return
        gyro_integral, gyro_samples, gyro_available = self.gyro_for_sample(sample)
        fields = flow_sample_to_optical_flow_rad(
            sample,
            axis_map=self.args.axis_map,
            quality_in_min=self.args.quality_in_min,
            quality_in_max=self.args.quality_in_max,
            sensor_id=self.args.sensor_id,
            gyro_integral_rad=gyro_integral,
        )
        send_ok, send_reason = self.should_send(sample)
        sent = 0
        mavlink_sent = 0
        if self.conn is not None and send_ok:
            self.conn.mav.optical_flow_rad_send(**fields)
            sent = 1
            mavlink_sent = 1
            self.n_sent += 1
        elif self.conn is not None and self.args.prime_on_unsent:
            prime_fields = dict(fields)
            prime_fields.update({
                "integrated_x": 0.0,
                "integrated_y": 0.0,
                "quality": 0,
            })
            self.conn.mav.optical_flow_rad_send(**prime_fields)
            mavlink_sent = 1
            self.n_prime_sent += 1
        if not send_ok and self.args.reset_on_unsent and self.estimator is not None:
            self.estimator.reset()
        self.n_samples += 1
        if self.sent_writer:
            self.sent_writer.writerow({
                "t_frame_sim_s": f"{sample.t_s:.6f}",
                "t_wall_s": f"{time.time():.6f}",
                "compute_s": f"{compute_s:.4f}",
                "integrated_x_cam_rad": f"{sample.integrated_x_rad:.7f}",
                "integrated_y_cam_rad": f"{sample.integrated_y_rad:.7f}",
                "integrated_x_sent": f"{fields['integrated_x']:.7f}",
                "integrated_y_sent": f"{fields['integrated_y']:.7f}",
                "integration_dt_s": f"{sample.integration_dt_s:.4f}",
                "integrated_xgyro_sent": f"{fields['integrated_xgyro']:.7f}",
                "integrated_ygyro_sent": f"{fields['integrated_ygyro']:.7f}",
                "integrated_zgyro_sent": f"{fields['integrated_zgyro']:.7f}",
                "gyro_mode": self.args.gyro_mode,
                "gyro_samples": gyro_samples,
                "gyro_available": int(gyro_available),
                "quality_raw": sample.quality,
                "quality_sent": fields["quality"],
                "n_matches": sample.n_matches,
                "range_m": f"{self.last_range_m:.4f}",
                "sent": sent,
                "mavlink_sent": mavlink_sent,
                "send_reason": send_reason,
            })
            self.sent_csv.flush()

    def close(self, notes: str = ""):
        if self.sent_csv:
            self.sent_csv.flush()
            self.sent_csv.close()
        print(
            f"done{notes}: {self.n_samples} flow samples, "
            f"{self.n_sent} sent, {self.n_prime_sent} prime"
        )


def run_replay(bridge: FlowBridge, rec_dir: Path) -> int:
    frames_csv = rec_dir / "frames_index.csv"
    if not frames_csv.exists():
        print(f"ERROR: {frames_csv} not found", file=sys.stderr)
        return 1
    with open(frames_csv) as fh:
        rows = list(csv.DictReader(fh))
    ranges_csv = rec_dir / "rangefinder.csv"
    range_rows = []
    if ranges_csv.exists():
        with open(ranges_csv) as fh:
            range_rows = [(float(r["t_sim_s"]), float(r["range_m"]))
                          for r in csv.DictReader(fh)]
    ri = 0
    print(f"replay: {len(rows)} frames from {rec_dir}")
    for row in rows:
        gray = cv2.imread(str(rec_dir / "frames" / row["frame_path"]), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        if bridge.args.max_width and gray.shape[1] > bridge.args.max_width:
            scale = bridge.args.max_width / gray.shape[1]
            gray = cv2.resize(gray, (bridge.args.max_width, int(gray.shape[0] * scale)),
                              interpolation=cv2.INTER_AREA)
        t_sim = float(row["t_sim_s"])
        while ri < len(range_rows) and range_rows[ri][0] <= t_sim:
            bridge.last_range_m = range_rows[ri][1]
            ri += 1
        bridge.process_gray(gray, t_sim)
    bridge.close(" (replay)")
    return 0


def run_live(bridge: FlowBridge, args) -> int:
    # gz imports only in live mode: replay must work without gz bindings.
    from gz.msgs10.image_pb2 import Image
    from gz.msgs10.imu_pb2 import IMU
    from gz.msgs10.laserscan_pb2 import LaserScan
    from gz.transport13 import Node

    if args.gyro_mode != "nan" and not args.imu_topic:
        print("ERROR: --gyro-mode needs --imu-topic in live mode", file=sys.stderr)
        return 1

    def on_image(msg: Image) -> None:
        t_sim = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        with bridge.lock:
            if t_sim - bridge.last_sample_t < bridge.min_period:
                return
            bridge.last_sample_t = t_sim
        arr = np.frombuffer(msg.data, dtype=np.uint8)
        if arr.size != msg.height * msg.width * 3:
            return
        bgr = cv2.cvtColor(arr.reshape((msg.height, msg.width, 3)), cv2.COLOR_RGB2BGR)
        if args.max_width and msg.width > args.max_width:
            scale = args.max_width / msg.width
            bgr = cv2.resize(bgr, (args.max_width, int(msg.height * scale)),
                             interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        bridge.process_gray(gray, t_sim)

    def on_scan(msg: LaserScan) -> None:
        if msg.ranges:
            bridge.last_range_m = msg.ranges[0]

    def on_imu(msg: IMU) -> None:
        if bridge.gyro_buffer is None:
            return
        t_sim = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        av = msg.angular_velocity
        bridge.gyro_buffer.add_body_sample(t_sim, av.x, av.y, av.z)

    node = Node()
    if not node.subscribe(Image, args.image_topic, on_image):
        print(f"ERROR: failed to subscribe {args.image_topic}", file=sys.stderr)
        return 1
    if args.scan_topic and not node.subscribe(LaserScan, args.scan_topic, on_scan):
        print(f"ERROR: failed to subscribe {args.scan_topic}", file=sys.stderr)
        return 1
    if args.imu_topic and not node.subscribe(IMU, args.imu_topic, on_imu):
        print(f"ERROR: failed to subscribe {args.imu_topic}", file=sys.stderr)
        return 1

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    hb = None
    startup_prime = None
    if bridge.conn is not None:
        hb = threading.Thread(target=bridge.heartbeat_loop, args=(stop,), daemon=True)
        hb.start()
        startup_prime = threading.Thread(target=bridge.startup_prime_loop, args=(stop,), daemon=True)
        startup_prime.start()
    print(f"live: {args.image_topic} (cap {args.rate_hz} Hz, width {args.max_width}), "
          f"axis_map={args.axis_map}, gyro_mode={args.gyro_mode}, "
          f"imu_topic={args.imu_topic}, dry_run={args.dry_run}")
    stop.wait(timeout=args.duration_s)
    stop.set()
    bridge.close(" (live)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image-topic")
    parser.add_argument("--scan-topic")
    parser.add_argument("--imu-topic")
    parser.add_argument("--replay", metavar="FLOW_RECORDING_DIR")
    parser.add_argument("--estimator", default="sift")
    parser.add_argument("--hfov-rad", type=float, default=1.74)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--axis-map", default="xy")
    parser.add_argument(
        "--gyro-mode",
        choices=["nan", "gz_body", "gz_camera_down_y90"],
        default="nan",
        help=(
            "OPTICAL_FLOW_RAD integrated gyro source. 'nan' preserves the "
            "baseline; 'gz_camera_down_y90' integrates Gazebo base_link IMU "
            "gyro over the flow frame interval and expresses it in the "
            "downward camera frame."
        ),
    )
    parser.add_argument("--quality-in-min", type=float, default=None)
    parser.add_argument("--quality-in-max", type=float, default=None)
    parser.add_argument("--sift-n-features", type=int, default=400)
    parser.add_argument("--sift-ratio", type=float, default=0.75)
    parser.add_argument("--sift-min-matches", type=int, default=8)
    parser.add_argument("--lk-max-corners", type=int, default=160)
    parser.add_argument("--lk-quality-level", type=float, default=0.01)
    parser.add_argument("--lk-min-distance", type=float, default=6.0)
    parser.add_argument("--lk-block-size", type=int, default=3)
    parser.add_argument("--lk-win-size", type=int, default=21)
    parser.add_argument("--lk-max-level", type=int, default=3)
    parser.add_argument("--lk-min-tracks", type=int, default=8)
    parser.add_argument("--lk-fb-max-error-px", type=float, default=1.5)
    parser.add_argument("--lk-confidence-multiplier", type=float, default=1.5)
    parser.add_argument("--lk-mad-multiplier", type=float, default=3.0)
    parser.add_argument("--lk-max-flow-rate-rad-s", type=float, default=1.2)
    parser.add_argument("--send-min-range-m", type=float, default=None)
    parser.add_argument("--send-max-range-m", type=float, default=None)
    parser.add_argument("--send-min-quality", type=float, default=0.0)
    parser.add_argument("--send-min-matches", type=int, default=0)
    parser.add_argument("--reset-on-unsent", action="store_true")
    parser.add_argument(
        "--prime-on-unsent",
        action="store_true",
        help="send zero-quality zero-flow OPTICAL_FLOW_RAD when gates block real flow",
    )
    parser.add_argument("--startup-prime-hz", type=float, default=2.0)
    parser.add_argument("--startup-prime-duration-s", type=float, default=60.0)
    parser.add_argument("--sensor-id", type=int, default=0)
    parser.add_argument("--connection", default="udpout:127.0.0.1:14600")
    parser.add_argument("--source-system", type=int, default=42)
    parser.add_argument("--source-component", type=int, default=197)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--sent-log", help="CSV of every flow sample (sent or dry)")
    parser.add_argument("--dry-run", action="store_true", help="compute + log, send nothing")
    args = parser.parse_args()

    if not args.replay and not args.image_topic:
        parser.error("live mode needs --image-topic (or use --replay DIR)")

    bridge = FlowBridge(args)
    if not args.dry_run:
        bridge.connect()

    if args.replay:
        return run_replay(bridge, Path(args.replay))
    return run_live(bridge, args)


if __name__ == "__main__":
    raise SystemExit(main())
