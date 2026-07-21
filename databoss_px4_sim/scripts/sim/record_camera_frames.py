#!/usr/bin/env python3
"""Record downward camera frames + rangefinder ranges from a running Gazebo sim.

Runs under SYSTEM python3 (needs gz.transport13 bindings + cv2). The caller
sets GZ_PARTITION/GZ_IP to match the sim. Output layout:

  <out_dir>/
    frames/frame_000123.jpg
    frames_index.csv     # t_sim_s,frame_path
    rangefinder.csv      # t_sim_s,range_m

Usage:
  record_camera_frames.py --image-topic <t> --scan-topic <t> --out-dir <d>
                          [--rate-hz 10] [--max-width 640] [--duration-s 120]
"""
from __future__ import annotations

import argparse
import csv
import signal
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from gz.msgs10.image_pb2 import Image
from gz.msgs10.laserscan_pb2 import LaserScan
from gz.transport13 import Node


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image-topic", required=True)
    parser.add_argument("--scan-topic", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frames_csv = (out_dir / "frames_index.csv").open("w", newline="")
    frames_writer = csv.writer(frames_csv)
    frames_writer.writerow(["t_sim_s", "frame_path"])
    range_csv = (out_dir / "rangefinder.csv").open("w", newline="")
    range_writer = csv.writer(range_csv)
    range_writer.writerow(["t_sim_s", "range_m"])

    lock = threading.Lock()
    state = {"n_frames": 0, "n_ranges": 0, "last_saved_t": -1e9}
    min_period = 1.0 / args.rate_hz if args.rate_hz > 0 else 0.0

    def on_image(msg: Image) -> None:
        t_sim = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        with lock:
            if t_sim - state["last_saved_t"] < min_period:
                return
            state["last_saved_t"] = t_sim
            idx = state["n_frames"]
            state["n_frames"] += 1

        arr = np.frombuffer(msg.data, dtype=np.uint8)
        if arr.size != msg.height * msg.width * 3:
            return  # unexpected format; skip
        rgb = arr.reshape((msg.height, msg.width, 3))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if args.max_width and msg.width > args.max_width:
            scale = args.max_width / msg.width
            bgr = cv2.resize(bgr, (args.max_width, int(msg.height * scale)), interpolation=cv2.INTER_AREA)
        path = frames_dir / f"frame_{idx:06d}.jpg"
        cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
        with lock:
            frames_writer.writerow([f"{t_sim:.6f}", path.name])

    def on_scan(msg: LaserScan) -> None:
        t_sim = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        if not msg.ranges:
            return
        with lock:
            range_writer.writerow([f"{t_sim:.6f}", f"{msg.ranges[0]:.4f}"])
            state["n_ranges"] += 1

    node = Node()
    if not node.subscribe(Image, args.image_topic, on_image):
        print(f"ERROR: failed to subscribe {args.image_topic}", file=sys.stderr)
        return 1
    if not node.subscribe(LaserScan, args.scan_topic, on_scan):
        print(f"ERROR: failed to subscribe {args.scan_topic}", file=sys.stderr)
        return 1

    print(f"recording: {args.image_topic} (cap {args.rate_hz} Hz, width {args.max_width}) + {args.scan_topic}")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait(timeout=args.duration_s)

    with lock:
        frames_csv.flush()
        range_csv.flush()
        print(f"done: {state['n_frames']} frames, {state['n_ranges']} range samples -> {out_dir}")
    frames_csv.close()
    range_csv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
