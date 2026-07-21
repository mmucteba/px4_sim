#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import re
import subprocess
import time
from collections import deque
from pathlib import Path

# MAVLink ODOMETRY requires MAVLink 2 common dialect.
os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "common")

from pymavlink import mavutil

from send_synthetic_external_odometry_mavlink import (
    send_heartbeat,
    send_odometry,
)


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def read_field(block: str, name: str) -> float:
    match = re.search(rf"\b{name}:\s*({NUMBER})", block)
    return float(match.group(1)) if match else 0.0


def parse_pose_message(text: str, model_name: str):
    sec_match = re.search(r"\bsec:\s*(\d+)", text)
    nsec_match = re.search(r"\bnsec:\s*(\d+)", text)

    pose_match = re.search(
        rf'pose\s*\{{\s*name:\s*"{re.escape(model_name)}".*?'
        rf'position\s*\{{(?P<position>.*?)\}}\s*orientation',
        text,
        re.DOTALL,
    )

    if not sec_match or not pose_match:
        return None

    sec = int(sec_match.group(1))
    nsec = int(nsec_match.group(1)) if nsec_match else 0
    position = pose_match.group("position")

    return {
        "sim_time_s": sec + nsec * 1e-9,
        "gx": read_field(position, "x"),
        "gy": read_field(position, "y"),
        "gz": read_field(position, "z"),
    }


def mav_frame_value(name: str) -> int:
    if name == "local_ned":
        return int(getattr(mavutil.mavlink, "MAV_FRAME_LOCAL_NED", 1))
    if name == "local_enu":
        return int(getattr(mavutil.mavlink, "MAV_FRAME_LOCAL_ENU", 4))
    raise ValueError(f"unsupported MAVLink frame: {name}")


def in_dropout_window(
    elapsed_s: float,
    *,
    enabled: bool,
    start_after_s: float,
    period_s: float,
    duration_s: float,
) -> bool:
    if not enabled or duration_s <= 0.0:
        return False
    if elapsed_s < start_after_s:
        return False

    t = elapsed_s - start_after_s
    if period_s <= 0.0:
        return t < duration_s

    return (t % period_s) < duration_s


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--topic",
        default="/world/default/dynamic_pose/info",
    )
    parser.add_argument("--model-name", default="x500_0")
    parser.add_argument(
        "--connection",
        default="udpout:127.0.0.1:14600",
    )
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--duration-s", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--position-std-m", type=float, default=0.02)
    parser.add_argument("--velocity-std-m-s", type=float, default=0.05)
    parser.add_argument(
        "--mav-frame",
        choices=["local_ned", "local_enu"],
        default="local_ned",
        help="MAVLink ODOMETRY pose/velocity frame used for Gazebo samples.",
    )
    parser.add_argument(
        "--velocity-source",
        choices=["zero", "finite_difference"],
        default="zero",
        help="Velocity reported in MAVLink ODOMETRY. ABC Case C uses zero because EKF2_EV_CTRL=3 does not fuse EV velocity.",
    )
    parser.add_argument("--velocity-alpha", type=float, default=0.35)
    parser.add_argument("--max-finite-diff-speed-m-s", type=float, default=5.0)
    parser.add_argument(
        "--velocity-reject-action",
        choices=["zero", "hold_last"],
        default="zero",
        help="Velocity behavior when a finite-difference sample exceeds the speed cap.",
    )
    parser.add_argument("--quality", type=int, default=100)
    parser.add_argument(
        "--latency-ms",
        type=float,
        default=0.0,
        help="Delay outgoing odometry samples by this many milliseconds.",
    )
    parser.add_argument(
        "--inject-position-noise-std-m",
        type=float,
        default=0.0,
        help="Gaussian noise added to outgoing x/y/z measurements.",
    )
    parser.add_argument(
        "--inject-velocity-noise-std-m-s",
        type=float,
        default=0.0,
        help="Gaussian noise added to outgoing vx/vy/vz measurements.",
    )
    parser.add_argument("--disturbance-seed", type=int, default=0)
    parser.add_argument("--dropout-enabled", action="store_true")
    parser.add_argument("--dropout-start-after-s", type=float, default=0.0)
    parser.add_argument("--dropout-period-s", type=float, default=0.0)
    parser.add_argument("--dropout-duration-s", type=float, default=0.0)
    parser.add_argument(
        "--dropout-probability",
        type=float,
        default=0.0,
        help="Independent random probability of dropping each scheduled odometry send.",
    )
    parser.add_argument(
        "--sent-log",
        default=(
            "experiments/inspections/phase8a_live_gazebo_odometry/"
            "live_bridge_sent.csv"
        ),
    )

    args = parser.parse_args()

    if args.rate_hz <= 0:
        raise SystemExit("--rate-hz must be positive")
    if args.latency_ms < 0:
        raise SystemExit("--latency-ms must be non-negative")
    if args.inject_position_noise_std_m < 0:
        raise SystemExit("--inject-position-noise-std-m must be non-negative")
    if args.inject_velocity_noise_std_m_s < 0:
        raise SystemExit("--inject-velocity-noise-std-m-s must be non-negative")
    if args.dropout_start_after_s < 0 or args.dropout_period_s < 0 or args.dropout_duration_s < 0:
        raise SystemExit("dropout timing values must be non-negative")
    if not 0.0 <= args.dropout_probability <= 1.0:
        raise SystemExit("--dropout-probability must be between 0.0 and 1.0")

    log_path = Path(args.sent_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    connection = None

    if not args.dry_run:
        print(f"Connecting MAVLink: {args.connection}")
        connection = mavutil.mavlink_connection(
            args.connection,
            source_system=42,
            source_component=197,
        )

    command = [
        "stdbuf",
        "-oL",
        "gz",
        "topic",
        "-e",
        "-t",
        args.topic,
    ]

    print("Starting Gazebo stream:", " ".join(command))

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    if process.stdout is None:
        raise RuntimeError("Gazebo stdout is unavailable")

    origin = None
    previous = None
    velocity = [0.0, 0.0, 0.0]
    mav_frame_id = mav_frame_value(args.mav_frame)
    rng = random.Random(args.disturbance_seed)
    latency_s = args.latency_ms / 1000.0
    pending_measurements = deque()

    message_lines: list[str] = []

    start_wall = time.monotonic()
    last_send_wall = 0.0
    last_heartbeat_wall = 0.0
    period_s = 1.0 / args.rate_hz

    parsed_count = 0
    output_count = 0
    finite_diff_velocity_rejected_count = 0
    dropout_skipped_count = 0

    fields = [
        "wall_time_s",
        "capture_wall_time_s",
        "sample_age_s",
        "sim_time_s",
        "raw_x",
        "raw_y",
        "raw_z",
        "x",
        "y",
        "z",
        "raw_vx",
        "raw_vy",
        "raw_vz",
        "vx",
        "vy",
        "vz",
        "mav_frame",
        "velocity_source",
        "velocity_reject_action",
        "finite_diff_velocity_rejected_count",
        "latency_ms",
        "inject_position_noise_std_m",
        "inject_velocity_noise_std_m_s",
        "dropout_enabled",
        "dropout_start_after_s",
        "dropout_period_s",
        "dropout_duration_s",
        "dropout_probability",
        "dropout_skipped_count",
        "quality",
    ]

    try:
        with log_path.open("w", newline="") as log_file:
            writer = csv.DictWriter(log_file, fieldnames=fields)
            writer.writeheader()

            for line in process.stdout:
                if line.startswith("header {") and message_lines:
                    sample = parse_pose_message(
                        "".join(message_lines),
                        args.model_name,
                    )
                    message_lines = [line]
                else:
                    message_lines.append(line)
                    continue

                if sample is None:
                    continue

                parsed_count += 1

                if origin is None:
                    origin = (
                        sample["gx"],
                        sample["gy"],
                        sample["gz"],
                    )
                    print(
                        "Origin locked:",
                        f"x={origin[0]:.6f}",
                        f"y={origin[1]:.6f}",
                        f"z={origin[2]:.6f}",
                    )

                if args.mav_frame == "local_ned":
                    x = sample["gx"] - origin[0]
                    y = sample["gy"] - origin[1]
                    z = -(sample["gz"] - origin[2])
                else:
                    x = sample["gx"] - origin[0]
                    y = sample["gy"] - origin[1]
                    z = sample["gz"] - origin[2]

                if args.velocity_source == "zero":
                    velocity = [0.0, 0.0, 0.0]
                elif previous is not None:
                    dt = sample["sim_time_s"] - previous["sim_time_s"]

                    if 0.0001 < dt < 1.0:
                        raw_velocity = [
                            (x - previous["x"]) / dt,
                            (y - previous["y"]) / dt,
                            (z - previous["z"]) / dt,
                        ]

                        raw_speed = math.sqrt(sum(v * v for v in raw_velocity))

                        if raw_speed <= args.max_finite_diff_speed_m_s:
                            alpha = args.velocity_alpha

                            velocity = [
                                alpha * raw_velocity[i]
                                + (1.0 - alpha) * velocity[i]
                                for i in range(3)
                            ]
                        else:
                            finite_diff_velocity_rejected_count += 1
                            if args.velocity_reject_action == "zero":
                                velocity = [0.0, 0.0, 0.0]

                previous = {
                    "sim_time_s": sample["sim_time_s"],
                    "x": x,
                    "y": y,
                    "z": z,
                }

                now = time.monotonic()

                if now - start_wall >= args.duration_s:
                    break

                vx, vy, vz = velocity
                capture_wall_time_s = time.time()
                measurement = {
                    "available_monotonic_s": now + latency_s,
                    "capture_wall_time_s": capture_wall_time_s,
                    "time_usec": int(capture_wall_time_s * 1_000_000),
                    "sim_time_s": sample["sim_time_s"],
                    "raw_x": x,
                    "raw_y": y,
                    "raw_z": z,
                    "x": x + rng.gauss(0.0, args.inject_position_noise_std_m),
                    "y": y + rng.gauss(0.0, args.inject_position_noise_std_m),
                    "z": z + rng.gauss(0.0, args.inject_position_noise_std_m),
                    "raw_vx": vx,
                    "raw_vy": vy,
                    "raw_vz": vz,
                    "vx": vx + rng.gauss(0.0, args.inject_velocity_noise_std_m_s),
                    "vy": vy + rng.gauss(0.0, args.inject_velocity_noise_std_m_s),
                    "vz": vz + rng.gauss(0.0, args.inject_velocity_noise_std_m_s),
                }
                pending_measurements.append(measurement)

                if now - last_send_wall < period_s:
                    continue

                send_sample = None
                while pending_measurements and pending_measurements[0]["available_monotonic_s"] <= now:
                    send_sample = pending_measurements.popleft()

                if send_sample is None:
                    continue

                if connection is not None:
                    if now - last_heartbeat_wall >= 1.0:
                        send_heartbeat(connection)
                        last_heartbeat_wall = now

                elapsed_s = now - start_wall
                drop_deterministic = in_dropout_window(
                    elapsed_s,
                    enabled=args.dropout_enabled,
                    start_after_s=args.dropout_start_after_s,
                    period_s=args.dropout_period_s,
                    duration_s=args.dropout_duration_s,
                )
                drop_random = (
                    args.dropout_probability > 0.0
                    and rng.random() < args.dropout_probability
                )

                if drop_deterministic or drop_random:
                    dropout_skipped_count += 1
                    last_send_wall = now
                    continue

                send_x = send_sample["x"]
                send_y = send_sample["y"]
                send_z = send_sample["z"]
                send_vx = send_sample["vx"]
                send_vy = send_sample["vy"]
                send_vz = send_sample["vz"]

                if connection is not None:
                    send_odometry(
                        connection,
                        x=send_x,
                        y=send_y,
                        z=send_z,
                        q=[1.0, 0.0, 0.0, 0.0],
                        vx=send_vx,
                        vy=send_vy,
                        vz=send_vz,
                        position_std_m=args.position_std_m,
                        velocity_std_m_s=args.velocity_std_m_s,
                        yaw_std_deg=30.0,
                        quality=args.quality,
                        reset_counter=0,
                        frame_id=mav_frame_id,
                        child_frame_id=mav_frame_id,
                        time_usec=send_sample["time_usec"],
                    )

                writer.writerow(
                    {
                        "wall_time_s": time.time(),
                        "capture_wall_time_s": send_sample["capture_wall_time_s"],
                        "sample_age_s": time.time() - send_sample["capture_wall_time_s"],
                        "sim_time_s": send_sample["sim_time_s"],
                        "raw_x": send_sample["raw_x"],
                        "raw_y": send_sample["raw_y"],
                        "raw_z": send_sample["raw_z"],
                        "x": send_x,
                        "y": send_y,
                        "z": send_z,
                        "raw_vx": send_sample["raw_vx"],
                        "raw_vy": send_sample["raw_vy"],
                        "raw_vz": send_sample["raw_vz"],
                        "vx": send_vx,
                        "vy": send_vy,
                        "vz": send_vz,
                        "mav_frame": args.mav_frame,
                        "velocity_source": args.velocity_source,
                        "velocity_reject_action": args.velocity_reject_action,
                        "finite_diff_velocity_rejected_count": finite_diff_velocity_rejected_count,
                        "latency_ms": args.latency_ms,
                        "inject_position_noise_std_m": args.inject_position_noise_std_m,
                        "inject_velocity_noise_std_m_s": args.inject_velocity_noise_std_m_s,
                        "dropout_enabled": args.dropout_enabled,
                        "dropout_start_after_s": args.dropout_start_after_s,
                        "dropout_period_s": args.dropout_period_s,
                        "dropout_duration_s": args.dropout_duration_s,
                        "dropout_probability": args.dropout_probability,
                        "dropout_skipped_count": dropout_skipped_count,
                        "quality": args.quality,
                    }
                )
                log_file.flush()

                output_count += 1
                last_send_wall = now

                if output_count == 1 or output_count % int(args.rate_hz) == 0:
                    print(
                        f"sample={output_count}",
                        f"odom=({send_x:.3f}, {send_y:.3f}, {send_z:.3f})",
                        f"vel=({send_vx:.3f}, {send_vy:.3f}, {send_vz:.3f})",
                        f"velocity_source={args.velocity_source}",
                        f"latency_ms={args.latency_ms:g}",
                        f"dropouts={dropout_skipped_count}",
                    )

    except KeyboardInterrupt:
        print("Stopped by user")

    finally:
        process.terminate()

        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    print(f"Parsed Gazebo samples: {parsed_count}")
    print(f"Output samples: {output_count}")
    print(f"MAVLink odometry frame: {args.mav_frame}")
    print(f"Velocity source: {args.velocity_source}")
    print(f"Velocity reject action: {args.velocity_reject_action}")
    print(f"Finite-difference velocity rejects: {finite_diff_velocity_rejected_count}")
    print(f"Latency ms: {args.latency_ms:g}")
    print(f"Injected position noise std m: {args.inject_position_noise_std_m:g}")
    print(f"Injected velocity noise std m/s: {args.inject_velocity_noise_std_m_s:g}")
    print(f"Dropout enabled: {args.dropout_enabled}")
    print(f"Dropout skipped sends: {dropout_skipped_count}")
    print(f"Log: {log_path}")

    if output_count == 0:
        raise SystemExit(
            f'No "{args.model_name}" poses were parsed from {args.topic}'
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
