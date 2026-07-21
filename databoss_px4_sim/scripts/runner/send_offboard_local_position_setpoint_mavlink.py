#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import time
from pathlib import Path

# SET_POSITION_TARGET_LOCAL_NED is in the MAVLink common dialect.
os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "common")

from pymavlink import mavutil


def mavlink_value(name: str, fallback: int) -> int:
    return int(getattr(mavutil.mavlink, name, fallback))


def local_setpoint_type_mask(setpoint_mode: str, use_yaw: bool) -> int:
    ignored_by_mode = {
        "position": [
            ("POSITION_TARGET_TYPEMASK_VX_IGNORE", 8),
            ("POSITION_TARGET_TYPEMASK_VY_IGNORE", 16),
            ("POSITION_TARGET_TYPEMASK_VZ_IGNORE", 32),
        ],
        "velocity_xy_position_z": [
            ("POSITION_TARGET_TYPEMASK_X_IGNORE", 1),
            ("POSITION_TARGET_TYPEMASK_Y_IGNORE", 2),
            ("POSITION_TARGET_TYPEMASK_VZ_IGNORE", 32),
        ],
    }

    mask = 0
    for name, fallback in ignored_by_mode[setpoint_mode] + [
        ("POSITION_TARGET_TYPEMASK_AX_IGNORE", 64),
        ("POSITION_TARGET_TYPEMASK_AY_IGNORE", 128),
        ("POSITION_TARGET_TYPEMASK_AZ_IGNORE", 256),
        ("POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE", 2048),
    ]:
        mask |= mavlink_value(name, fallback)

    if not use_yaw:
        mask |= mavlink_value("POSITION_TARGET_TYPEMASK_YAW_IGNORE", 1024)

    return mask


def send_heartbeat(conn) -> None:
    conn.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        0,
    )


def send_local_setpoint(
    conn,
    *,
    time_boot_ms: int,
    target_system: int,
    target_component: int,
    frame: int,
    type_mask: int,
    x: float,
    y: float,
    z: float,
    vx: float,
    vy: float,
    vz: float,
    yaw_rad: float,
) -> None:
    conn.mav.set_position_target_local_ned_send(
        time_boot_ms,
        target_system,
        target_component,
        frame,
        type_mask,
        x,
        y,
        z,
        vx,
        vy,
        vz,
        0.0,
        0.0,
        0.0,
        yaw_rad,
        0.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream MAVLink local-NED position hold setpoints for PX4 Offboard mode.")
    parser.add_argument("--connection", default="udpout:127.0.0.1:14600")
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument(
        "--setpoint-mode",
        choices=["position", "velocity_xy_position_z"],
        default="position",
    )
    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=0.0)
    parser.add_argument("--z", type=float, default=-2.5, help="Local NED z. For 2.5 m above origin, use -2.5.")
    parser.add_argument("--vx", type=float, default=0.0)
    parser.add_argument("--vy", type=float, default=0.0)
    parser.add_argument("--vz", type=float, default=0.0)
    parser.add_argument("--yaw-deg", type=float, default=0.0)
    parser.add_argument("--use-yaw", action="store_true", help="Command yaw instead of leaving yaw unconstrained.")
    parser.add_argument("--target-system", type=int, default=0, help="0 broadcasts to the PX4 instance.")
    parser.add_argument("--target-component", type=int, default=0, help="0 broadcasts to the PX4 instance.")
    parser.add_argument("--source-system", type=int, default=43)
    parser.add_argument("--source-component", type=int, default=196)
    parser.add_argument("--sent-log", default="experiments/inspections/offboard_local_hold/offboard_local_hold_sent.csv")
    args = parser.parse_args()

    if args.rate_hz <= 0:
        raise SystemExit("--rate-hz must be positive")

    if args.duration_s <= 0:
        raise SystemExit("--duration-s must be positive")

    sent_log = Path(args.sent_log)
    sent_log.parent.mkdir(parents=True, exist_ok=True)

    print(f"Connecting MAVLink: {args.connection}")
    conn = mavutil.mavlink_connection(
        args.connection,
        source_system=args.source_system,
        source_component=args.source_component,
    )

    frame = mavlink_value("MAV_FRAME_LOCAL_NED", 1)
    type_mask = local_setpoint_type_mask(args.setpoint_mode, args.use_yaw)
    yaw_rad = math.radians(args.yaw_deg)

    period_s = 1.0 / args.rate_hz
    start = time.monotonic()
    next_send = start
    last_heartbeat = 0.0
    sent = 0

    with sent_log.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "wall_time_s",
                "time_since_start_s",
                "time_boot_ms",
                "setpoint_mode",
                "x",
                "y",
                "z",
                "vx",
                "vy",
                "vz",
                "yaw_deg",
                "use_yaw",
                "frame",
                "type_mask",
                "target_system",
                "target_component",
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

            if now - last_heartbeat >= 1.0:
                send_heartbeat(conn)
                last_heartbeat = now

            time_boot_ms = int(elapsed * 1000.0) & 0xFFFFFFFF
            send_local_setpoint(
                conn,
                time_boot_ms=time_boot_ms,
                target_system=args.target_system,
                target_component=args.target_component,
                frame=frame,
                type_mask=type_mask,
                x=args.x,
                y=args.y,
                z=args.z,
                vx=args.vx,
                vy=args.vy,
                vz=args.vz,
                yaw_rad=yaw_rad,
            )

            writer.writerow(
                {
                    "wall_time_s": time.time(),
                    "time_since_start_s": elapsed,
                    "time_boot_ms": time_boot_ms,
                    "setpoint_mode": args.setpoint_mode,
                    "x": args.x,
                    "y": args.y,
                    "z": args.z,
                    "vx": args.vx,
                    "vy": args.vy,
                    "vz": args.vz,
                    "yaw_deg": args.yaw_deg,
                    "use_yaw": args.use_yaw,
                    "frame": frame,
                    "type_mask": type_mask,
                    "target_system": args.target_system,
                    "target_component": args.target_component,
                }
            )
            f.flush()

            sent += 1
            next_send += period_s

    print(f"Sent {sent} SET_POSITION_TARGET_LOCAL_NED messages")
    print(f"Sent log: {sent_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
