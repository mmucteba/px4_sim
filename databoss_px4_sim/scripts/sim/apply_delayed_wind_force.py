#!/usr/bin/env python3
"""Apply a wind-equivalent aerodynamic drag force to a link, starting only
after this process is launched (i.e. after the caller's own height/time
gate has passed) -- used to give PX4's AUTO_TAKEOFF a calm-air climb while
still exposing the vehicle to real wind once airborne.

Runs under SYSTEM/venv_bridge python3 (needs gz.transport13 + gz.msgs10).
The caller sets GZ_PARTITION/GZ_IP to match the sim, same convention as
record_camera_frames.py / flow_mavlink_bridge.py.

Physics model (a documented approximation, distinct from gz-sim's native
WindEffects system, chosen specifically because WindEffects has no runtime
on/off control -- see docs/phases/phase_16_wind_roadmap.md for why this
exists):

    v_rel = wind_velocity_enu - vehicle_velocity_enu   (apparent wind)
    F     = 0.5 * air_density * drag_coefficient * frontal_area_m2
            * |v_rel| * v_rel                            (quadratic drag)

Vehicle velocity is estimated by finite-differencing consecutive truth
poses on /world/<world>/dynamic_pose/info for --entity-name (the tracked
model), matching the project's existing ENU truth convention. The force is
published as an instantaneous world-frame EntityWrench on
/world/<world>/wrench against --apply-to-link every control step, so it
continuously tracks relative velocity rather than being a constant push.

Usage:
  apply_delayed_wind_force.py --world <w> --entity-name <model> \\
      --apply-to-link base_link --wind-mean-mps 7.0 \\
      --wind-direction-enu 0.0 1.0 --rate-hz 20 --duration-s 120 \\
      --sent-log <path>
"""
from __future__ import annotations

import argparse
import csv
import math
import signal
import sys
import threading
import time
from pathlib import Path

from gz.msgs10.entity_pb2 import Entity
from gz.msgs10.entity_wrench_pb2 import EntityWrench
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node

AIR_DENSITY_KG_M3 = 1.225
# X500-class quadrotor with downward camera/lidar payload: no published
# drag-area spec for this exact build, so this is a documented estimate
# (roughly the frame+prop frontal silhouette), not a measured value.
DEFAULT_DRAG_COEFFICIENT = 1.0
DEFAULT_FRONTAL_AREA_M2 = 0.16


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--world", required=True)
    parser.add_argument("--entity-name", required=True, help="Model name to track for velocity (truth pose)")
    parser.add_argument("--apply-to-link", default="base_link", help="Link name to apply the wrench force to")
    parser.add_argument("--wind-mean-mps", type=float, required=True)
    parser.add_argument("--wind-direction-enu", type=float, nargs=2, required=True, metavar=("EAST", "NORTH"))
    parser.add_argument("--air-density", type=float, default=AIR_DENSITY_KG_M3)
    parser.add_argument("--drag-coefficient", type=float, default=DEFAULT_DRAG_COEFFICIENT)
    parser.add_argument("--frontal-area-m2", type=float, default=DEFAULT_FRONTAL_AREA_M2)
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--sent-log", required=True)
    args = parser.parse_args()

    east, north = args.wind_direction_enu
    norm = math.hypot(east, north)
    if norm <= 0.0:
        print("ERROR: wind-direction-enu must not be zero-length", file=sys.stderr)
        return 1
    wind_vx = args.wind_mean_mps * east / norm
    wind_vy = args.wind_mean_mps * north / norm

    pose_topic = f"/world/{args.world}/dynamic_pose/info"
    wrench_topic = f"/world/{args.world}/wrench"

    sent_log_path = Path(args.sent_log)
    sent_log_path.parent.mkdir(parents=True, exist_ok=True)
    sent_csv = sent_log_path.open("w", newline="")
    sent_writer = csv.writer(sent_csv)
    sent_writer.writerow(["t_sim_s", "vx_est", "vy_est", "vz_est", "fx", "fy", "fz"])

    lock = threading.Lock()
    # gz's per-pose header.stamp on dynamic_pose/info is not populated in
    # this build (always reads 0), so velocity is estimated by
    # finite-differencing on wall-clock arrival time between callbacks
    # instead of the message timestamp.
    state = {"t": None, "x": None, "y": None, "z": None, "vx": 0.0, "vy": 0.0, "vz": 0.0, "sim_t": 0.0}

    def on_pose(msg: Pose_V) -> None:
        t = time.monotonic()
        sim_t = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        for pose in msg.pose:
            if pose.name != args.entity_name:
                continue
            x, y, z = pose.position.x, pose.position.y, pose.position.z
            with lock:
                if state["t"] is not None and t > state["t"]:
                    dt = t - state["t"]
                    state["vx"] = (x - state["x"]) / dt
                    state["vy"] = (y - state["y"]) / dt
                    state["vz"] = (z - state["z"]) / dt
                state["t"], state["x"], state["y"], state["z"] = t, x, y, z
                state["sim_t"] = sim_t
            break

    node = Node()
    if not node.subscribe(Pose_V, pose_topic, on_pose):
        print(f"ERROR: failed to subscribe {pose_topic}", file=sys.stderr)
        return 1

    pub = node.advertise(wrench_topic, EntityWrench)
    if not pub.valid():
        print(f"ERROR: failed to advertise {wrench_topic}", file=sys.stderr)
        return 1

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    print(
        f"delayed wind force: world={args.world} track={args.entity_name} "
        f"apply_to={args.apply_to_link} wind=({wind_vx:.2f},{wind_vy:.2f}) m/s "
        f"rate={args.rate_hz}Hz duration={args.duration_s}s"
    )

    period = 1.0 / args.rate_hz if args.rate_hz > 0 else 0.05
    coeff = 0.5 * args.air_density * args.drag_coefficient * args.frontal_area_m2
    start = time.monotonic()

    while not stop.is_set() and (time.monotonic() - start) < args.duration_s:
        with lock:
            t_sim, vx, vy, vz = state["t"], state["vx"], state["vy"], state["vz"]

        if t_sim is not None:
            rel_x = wind_vx - vx
            rel_y = wind_vy - vy
            rel_z = 0.0 - vz
            rel_mag = math.sqrt(rel_x * rel_x + rel_y * rel_y + rel_z * rel_z)
            fx = coeff * rel_mag * rel_x
            fy = coeff * rel_mag * rel_y
            fz = coeff * rel_mag * rel_z

            msg = EntityWrench()
            msg.entity.name = args.apply_to_link
            msg.entity.type = Entity.LINK
            msg.wrench.force.x = fx
            msg.wrench.force.y = fy
            msg.wrench.force.z = fz
            pub.publish(msg)
            sent_writer.writerow([f"{t_sim:.6f}", f"{vx:.4f}", f"{vy:.4f}", f"{vz:.4f}", f"{fx:.4f}", f"{fy:.4f}", f"{fz:.4f}"])

        time.sleep(period)

    sent_csv.close()
    print("delayed wind force: stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
