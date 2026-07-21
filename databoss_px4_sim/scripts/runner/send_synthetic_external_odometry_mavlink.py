#!/usr/bin/env python3
"""
Phase 8A synthetic external odometry sender.

Purpose:
- Send MAVLink ODOMETRY messages to PX4.
- PX4 mavlink_receiver should publish them to vehicle_visual_odometry.
- EKF2 can then fuse them when EKF2_EV_CTRL is enabled.

This first version is a receiver/fusion-path smoke tool.
It sends ideal local-NED hover odometry.
It does not yet read live Gazebo truth.
"""

from __future__ import annotations

import os

# Force MAVLink 2 common dialect so MAVLink ODOMETRY is available.
os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "common")

import argparse
import csv
import math
import time
from pathlib import Path

from pymavlink import mavutil


def yaw_to_quaternion_wxyz(yaw_rad: float) -> list[float]:
    """Return Hamilton quaternion [w, x, y, z] for yaw-only attitude."""
    half = yaw_rad * 0.5
    return [math.cos(half), 0.0, 0.0, math.sin(half)]


def covariance_6x6_upper_triangular(
    position_var: float,
    orientation_var: float,
) -> list[float]:
    """
    MAVLink ODOMETRY covariance is 6x6 upper triangular, length 21.

    Diagonal indices:
      0  = x / roll
      6  = y / pitch
      11 = z / yaw
      15 = roll rate / unused orientation axis
      18 = pitch rate / unused orientation axis
      20 = yaw rate / unused orientation axis
    """
    cov = [float("nan")] * 21
    cov[0] = position_var
    cov[6] = position_var
    cov[11] = position_var
    cov[15] = orientation_var
    cov[18] = orientation_var
    cov[20] = orientation_var
    return cov


def send_heartbeat(conn) -> None:
    conn.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        0,
    )


def send_odometry(
    conn,
    *,
    x: float,
    y: float,
    z: float,
    q: list[float],
    vx: float,
    vy: float,
    vz: float,
    position_std_m: float,
    velocity_std_m_s: float,
    yaw_std_deg: float,
    quality: int,
    reset_counter: int,
    frame_id: int | None = None,
    child_frame_id: int | None = None,
    time_usec: int | None = None,
) -> None:
    if frame_id is None:
        frame_id = getattr(mavutil.mavlink, "MAV_FRAME_LOCAL_NED", 1)

    if child_frame_id is None:
        # Use LOCAL_NED for velocity too. PX4 maps this into
        # vehicle_visual_odometry.velocity_frame = VELOCITY_FRAME_NED = 1.
        # Earlier smoke used BODY_FRD, which worked for zero velocity but is wrong
        # for future Gazebo-truth velocity replay.
        child_frame_id = getattr(mavutil.mavlink, "MAV_FRAME_LOCAL_NED", 1)

    estimator_type = getattr(mavutil.mavlink, "MAV_ESTIMATOR_TYPE_VISION", 3)

    position_var = position_std_m ** 2
    velocity_var = velocity_std_m_s ** 2
    yaw_var = math.radians(yaw_std_deg) ** 2

    pose_covariance = covariance_6x6_upper_triangular(position_var, yaw_var)
    velocity_covariance = covariance_6x6_upper_triangular(velocity_var, yaw_var)

    if time_usec is None:
        time_usec = int(time.time() * 1_000_000)

    conn.mav.odometry_send(
        time_usec,
        frame_id,
        child_frame_id,
        x,
        y,
        z,
        q,
        vx,
        vy,
        vz,
        0.0,
        0.0,
        0.0,
        pose_covariance,
        velocity_covariance,
        reset_counter,
        estimator_type,
        quality,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--connection",
        default="udpout:127.0.0.1:14540",
        help="pymavlink connection string, for example udpout:127.0.0.1:14540",
    )
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--duration-s", type=float, default=60.0)

    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=0.0)
    parser.add_argument(
        "--z",
        type=float,
        default=-10.0,
        help="Local NED z. For 10m above takeoff, use -10.",
    )
    parser.add_argument("--vx", type=float, default=0.0)
    parser.add_argument("--vy", type=float, default=0.0)
    parser.add_argument("--vz", type=float, default=0.0)
    parser.add_argument("--yaw-deg", type=float, default=0.0)

    parser.add_argument("--position-std-m", type=float, default=0.02)
    parser.add_argument("--velocity-std-m-s", type=float, default=0.02)
    parser.add_argument("--yaw-std-deg", type=float, default=1.0)
    parser.add_argument("--quality", type=int, default=100)

    parser.add_argument(
        "--sent-log",
        default="experiments/inspections/phase8a_external_odom/synthetic_external_odometry_sent.csv",
    )

    parser.add_argument("--source-system", type=int, default=42)
    parser.add_argument("--source-component", type=int, default=197)

    args = parser.parse_args()

    if args.rate_hz <= 0:
        raise SystemExit("--rate-hz must be positive")

    sent_log = Path(args.sent_log)
    sent_log.parent.mkdir(parents=True, exist_ok=True)

    print(f"Connecting MAVLink: {args.connection}")
    conn = mavutil.mavlink_connection(
        args.connection,
        source_system=args.source_system,
        source_component=args.source_component,
    )

    q = yaw_to_quaternion_wxyz(math.radians(args.yaw_deg))
    period_s = 1.0 / args.rate_hz
    start = time.monotonic()
    next_send = start
    sent = 0
    reset_counter = 0

    with sent_log.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "wall_time_s",
                "time_since_start_s",
                "x",
                "y",
                "z",
                "vx",
                "vy",
                "vz",
                "qw",
                "qx",
                "qy",
                "qz",
                "quality",
            ],
        )
        writer.writeheader()

        while True:
            now = time.monotonic()
            elapsed = now - start

            if elapsed >= args.duration_s:
                break

            if now < next_send:
                time.sleep(min(0.01, next_send - now))
                continue

            if sent % max(1, int(args.rate_hz)) == 0:
                send_heartbeat(conn)

            send_odometry(
                conn,
                x=args.x,
                y=args.y,
                z=args.z,
                q=q,
                vx=args.vx,
                vy=args.vy,
                vz=args.vz,
                position_std_m=args.position_std_m,
                velocity_std_m_s=args.velocity_std_m_s,
                yaw_std_deg=args.yaw_std_deg,
                quality=args.quality,
                reset_counter=reset_counter,
            )

            writer.writerow(
                {
                    "wall_time_s": time.time(),
                    "time_since_start_s": elapsed,
                    "x": args.x,
                    "y": args.y,
                    "z": args.z,
                    "vx": args.vx,
                    "vy": args.vy,
                    "vz": args.vz,
                    "qw": q[0],
                    "qx": q[1],
                    "qy": q[2],
                    "qz": q[3],
                    "quality": args.quality,
                }
            )

            sent += 1
            next_send += period_s

    print(f"Sent {sent} ODOMETRY messages")
    print(f"Sent log: {sent_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
