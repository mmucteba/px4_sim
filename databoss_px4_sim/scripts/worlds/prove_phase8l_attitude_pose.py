#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import statistics as stats
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - reported in the output
    cv2 = None
    np = None


PROJECT_ROOT = Path("/opt/databoss_px4_sim")
PX4_GZ_ROOT = Path("/opt/sim_px4/PX4-Autopilot/Tools/simulation/gz")
PX4_MODELS = PX4_GZ_ROOT / "models"
PX4_WORLDS = PX4_GZ_ROOT / "worlds"
PX4_ROOT = Path("/opt/sim_px4/PX4-Autopilot")
PX4_PLUGINS = PX4_ROOT / "build/px4_sitl_default/src/modules/simulation/gz_plugins"
PX4_SERVER_CONFIG = PX4_ROOT / "src/modules/simulation/gz_bridge/server.config"
DEFAULT_WORLD = PROJECT_ROOT / "generated_worlds/flat_rural_phototex_noon.sdf"
MODEL_NAME = "x500_cam_lidar_down_0"
VEHICLE_URI = "model://x500_cam_lidar_down"


POSES = [
    ("level", 0.0, 0.0, 0.0),
    ("roll_p5", 5.0, 0.0, 0.0),
    ("roll_n5", -5.0, 0.0, 0.0),
    ("roll_p10", 10.0, 0.0, 0.0),
    ("roll_n10", -10.0, 0.0, 0.0),
    ("pitch_p5", 0.0, 5.0, 0.0),
    ("pitch_n5", 0.0, -5.0, 0.0),
    ("pitch_p10", 0.0, 10.0, 0.0),
    ("pitch_n10", 0.0, -10.0, 0.0),
    ("yaw_p45", 0.0, 0.0, 45.0),
    ("yaw_n45", 0.0, 0.0, -45.0),
    ("yaw_p90", 0.0, 0.0, 90.0),
    ("yaw_n90", 0.0, 0.0, -90.0),
]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def finite_float(value) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def stop_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=5)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            continue


def xvfb_pids() -> set[int]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,user=,cmd="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        pid_text, user, cmd = parts
        if user == "px4" and cmd.startswith("Xvfb "):
            try:
                pids.add(int(pid_text))
            except ValueError:
                pass
    return pids


def stop_new_xvfb(before: set[int]) -> None:
    for pid in sorted(xvfb_pids() - before):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
                time.sleep(0.2)
            except ProcessLookupError:
                break
            if pid not in xvfb_pids():
                break


def run_command(args: list[str], env: dict[str, str], timeout_s: float) -> dict:
    started = time.time()
    try:
        completed = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "timeout": False,
            "elapsed_s": time.time() - started,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "returncode": None,
            "stdout": exc.stdout or "",
            "timeout": True,
            "elapsed_s": time.time() - started,
        }


def load_world_name(sdf_path: Path) -> str:
    manifest = sdf_path.with_suffix(".manifest.json")
    if manifest.exists():
        data = json.loads(manifest.read_text())
        if isinstance(data.get("world_name"), str):
            return data["world_name"]
    return sdf_path.stem


def make_env(world_name: str, world_sdf: Path) -> dict[str, str]:
    env = os.environ.copy()
    resource_paths = [
        str(PX4_MODELS),
        str(PX4_WORLDS),
        str(world_sdf.parent),
    ]
    existing_resources = [item for item in env.get("GZ_SIM_RESOURCE_PATH", "").split(":") if item]
    env["HEADLESS"] = "1"
    env["GZ_IP"] = "127.0.0.1"
    env["GZ_PARTITION"] = f"databoss_phase8l_pose_{world_name}_{os.getpid()}"
    env["GZ_SIM_RESOURCE_PATH"] = ":".join(resource_paths + existing_resources)
    env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = str(PX4_PLUGINS)
    env["GZ_SIM_SERVER_CONFIG_PATH"] = str(PX4_SERVER_CONFIG)
    return env


def wait_for_topics(env: dict[str, str], topics: list[str], timeout_s: float) -> tuple[bool, list[str]]:
    deadline = time.time() + timeout_s
    seen: list[str] = []
    while time.time() < deadline:
        result = run_command(["gz", "topic", "-l"], env, timeout_s=5)
        if result["returncode"] == 0:
            lines = result["stdout"].splitlines()
            seen = lines
            if all(topic in lines for topic in topics):
                return True, lines
        time.sleep(0.5)
    return False, seen


def make_pose_world(
    source_world: Path,
    out_path: Path,
    pose_name: str,
    roll_deg: float,
    pitch_deg: float,
    yaw_deg: float,
    altitude_m: float,
) -> None:
    text = source_world.read_text()
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)
    include = f"""
    <!-- Phase 8L attitude pose proof: {pose_name}.
         Static include keeps the vehicle at the requested pose so this test
         proves sensor geometry without PX4 controller dynamics. -->
    <include>
      <uri>{VEHICLE_URI}</uri>
      <name>{MODEL_NAME}</name>
      <pose>0 0 {altitude_m:.6f} {roll:.9f} {pitch:.9f} {yaw:.9f}</pose>
      <static>true</static>
    </include>
"""
    marker = "</world>"
    if marker not in text:
        raise ValueError(f"world has no {marker}: {source_world}")
    out_path.write_text(text.replace(marker, include + marker, 1))


def image_stats(frames_dir: Path, max_frames: int = 12) -> dict:
    rows = read_csv_rows(frames_dir / "frames_index.csv")
    out = {"available": bool(rows), "rows": len(rows)}
    if not rows:
        return out
    if cv2 is None or np is None:
        out["image_analysis_available"] = False
        out["image_analysis_error"] = "cv2/numpy unavailable"
        return out

    sample_rows = rows
    if len(rows) > max_frames:
        idxs = np.linspace(0, len(rows) - 1, max_frames).round().astype(int)
        sample_rows = [rows[int(i)] for i in idxs]

    means: list[float] = []
    stds: list[float] = []
    feature_counts: list[int] = []
    textured_cell_fracs: list[float] = []
    sky_like_fracs: list[float] = []

    for row in sample_rows:
        path = frames_dir / "frames" / str(row.get("frame_path", ""))
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        means.append(float(img.mean()))
        stds.append(float(img.std()))
        corners = cv2.goodFeaturesToTrack(img, maxCorners=600, qualityLevel=0.01, minDistance=6)
        feature_counts.append(0 if corners is None else int(len(corners)))

        h, w = img.shape[:2]
        grid = []
        for y0 in np.linspace(0, h, 9, dtype=int)[:-1]:
            y1 = min(h, y0 + max(1, h // 8))
            for x0 in np.linspace(0, w, 9, dtype=int)[:-1]:
                x1 = min(w, x0 + max(1, w // 8))
                grid.append(float(img[y0:y1, x0:x1].std()))
        textured_cell_fracs.append(sum(v > 5.0 for v in grid) / max(len(grid), 1))
        sky_like_fracs.append(float(((img > 145) & (img < 235)).mean()))

    if not means:
        out["image_analysis_available"] = False
        out["image_analysis_error"] = "no readable sampled frames"
        return out
    times = [finite_float(row.get("t_sim_s")) for row in rows]
    times = [t for t in times if t is not None]
    out.update({
        "image_analysis_available": True,
        "sampled_frames": len(means),
        "duration_s": max(times) - min(times) if len(times) >= 2 else None,
        "rate_hz": len(times) / (max(times) - min(times)) if len(times) >= 2 and max(times) > min(times) else None,
        "brightness_mean": stats.mean(means),
        "contrast_std_mean": stats.mean(stds),
        "feature_count_median": stats.median(feature_counts),
        "feature_count_min": min(feature_counts),
        "textured_cell_fraction_mean": stats.mean(textured_cell_fracs),
        "sky_like_fraction_mean": stats.mean(sky_like_fracs),
    })
    return out


def range_stats(frames_dir: Path, altitude_m: float, roll_deg: float, pitch_deg: float) -> dict:
    rows = read_csv_rows(frames_dir / "rangefinder.csv")
    values = []
    for row in rows:
        value = finite_float(row.get("range_m"))
        if value is not None and value > 0:
            values.append(value)

    out = {
        "available": bool(rows),
        "rows": len(rows),
        "finite_positive_rows": len(values),
        "finite_positive_fraction": len(values) / len(rows) if rows else None,
    }
    if not values:
        return out

    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    cos_tilt = max(1e-6, abs(math.cos(roll) * math.cos(pitch)))
    expected = altitude_m / cos_tilt
    median = stats.median(values)
    out.update({
        "median_m": median,
        "mean_m": stats.mean(values),
        "min_m": min(values),
        "max_m": max(values),
        "expected_m": expected,
        "median_minus_expected_m": median - expected,
    })
    return out


def evaluate_pose(camera: dict, rng: dict) -> dict:
    finite_fraction = rng.get("finite_positive_fraction")
    checks = {
        "camera_frames_present": camera.get("rows", 0) >= 5,
        "camera_ground_textured": (
            camera.get("image_analysis_available") is True
            and camera.get("textured_cell_fraction_mean", 0.0) >= 0.90
            and camera.get("feature_count_median", 0) >= 80
        ),
        "camera_not_sky": camera.get("sky_like_fraction_mean", 1.0) < 0.20,
        "range_finite": finite_fraction is not None and finite_fraction >= 0.95,
        "range_attitude_corrected": (
            rng.get("median_minus_expected_m") is not None
            and abs(float(rng["median_minus_expected_m"])) <= 0.30
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {"checks": checks, "accepted": not failed, "failed_checks": failed}


def prove_pose(
    pose: tuple[str, float, float, float],
    source_world: Path,
    world_name: str,
    out_dir: Path,
    altitude_m: float,
    record_s: float,
    startup_timeout_s: float,
) -> dict:
    pose_name, roll_deg, pitch_deg, yaw_deg = pose
    pose_dir = out_dir / pose_name
    pose_dir.mkdir(parents=True, exist_ok=True)
    pose_world = pose_dir / f"{world_name}_{pose_name}.sdf"
    make_pose_world(source_world, pose_world, pose_name, roll_deg, pitch_deg, yaw_deg, altitude_m)

    env = make_env(world_name, pose_world)
    image_topic = f"/world/{world_name}/model/{MODEL_NAME}/link/camera_link/sensor/camera/image"
    scan_topic = f"/world/{world_name}/model/{MODEL_NAME}/link/lidar_sensor_link/sensor/lidar/scan"
    console_log = pose_dir / "gz_console.log"
    recording_dir = pose_dir / "flow_recording"
    recording_log = pose_dir / "record_camera_frames.log"
    xvfb_before = xvfb_pids()

    result = {
        "pose": {
            "name": pose_name,
            "roll_deg": roll_deg,
            "pitch_deg": pitch_deg,
            "yaw_deg": yaw_deg,
            "altitude_m": altitude_m,
        },
        "world_sdf": str(pose_world),
        "gz_partition": env["GZ_PARTITION"],
        "image_topic": image_topic,
        "scan_topic": scan_topic,
        "launch_ok": False,
        "topics_ok": False,
        "record_ok": False,
    }

    with console_log.open("w") as log_file:
        process = subprocess.Popen(
            [
                "xvfb-run",
                "-a",
                "-s",
                "-screen 0 1280x1024x24",
                "gz",
                "sim",
                "-r",
                "-s",
                "-v",
                "2",
                str(pose_world),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        result["gz_pid"] = process.pid
        try:
            topics_ok, topics = wait_for_topics(env, [image_topic, scan_topic], startup_timeout_s)
            result["launch_ok"] = process.poll() is None
            result["topics_ok"] = topics_ok
            result["topics_seen_count"] = len(topics)
            if not topics_ok:
                result["accepted"] = False
                result["reason"] = "camera/range topics not seen"
                result["gz_exit_code"] = process.poll()
                return result

            cmd = [
                "/usr/bin/python3",
                str(PROJECT_ROOT / "scripts/sim/record_camera_frames.py"),
                "--image-topic",
                image_topic,
                "--scan-topic",
                scan_topic,
                "--out-dir",
                str(recording_dir),
                "--rate-hz",
                "0",
                "--max-width",
                "640",
                "--duration-s",
                str(record_s),
            ]
            with recording_log.open("w") as rec_log:
                rec = subprocess.run(
                    cmd,
                    cwd=PROJECT_ROOT,
                    env=env,
                    text=True,
                    stdout=rec_log,
                    stderr=subprocess.STDOUT,
                    timeout=record_s + 15,
                    check=False,
                )
            result["record_returncode"] = rec.returncode
            result["record_process_ok"] = rec.returncode == 0
            result["camera"] = image_stats(recording_dir)
            result["rangefinder"] = range_stats(recording_dir, altitude_m, roll_deg, pitch_deg)
            result["gate_result"] = evaluate_pose(result["camera"], result["rangefinder"])
            result["record_ok"] = bool(result["camera"].get("available") and result["rangefinder"].get("available"))
            result["accepted"] = bool(result["launch_ok"] and result["topics_ok"] and result["record_ok"] and result["gate_result"]["accepted"])
            return result
        finally:
            stop_process_group(process)
            stop_new_xvfb(xvfb_before)
            result["gz_stopped"] = process.poll() is not None
            result["gz_final_exit_code"] = process.poll()


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# Phase 8L Gate 3 Attitude Pose Proof",
        "",
        f"- Started UTC: `{report['started_utc']}`",
        f"- World: `{report['world_sdf']}`",
        f"- Output dir: `{report['out_dir']}`",
        f"- Accepted: `{report['accepted']}`",
        "",
        "## Summary",
        "",
        "| Pose | Roll | Pitch | Yaw | Camera texture | Sky-like | Range finite | Range median | Expected | Accepted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["poses"]:
        pose = item["pose"]
        camera = item.get("camera", {})
        rng = item.get("rangefinder", {})
        lines.append(
            "| {name} | {roll:.1f} | {pitch:.1f} | {yaw:.1f} | {tex} | {sky} | {finite} | {median} | {expected} | {accepted} |".format(
                name=pose["name"],
                roll=pose["roll_deg"],
                pitch=pose["pitch_deg"],
                yaw=pose["yaw_deg"],
                tex=_fmt(camera.get("textured_cell_fraction_mean"), 3),
                sky=_fmt(camera.get("sky_like_fraction_mean"), 3),
                finite=_fmt(rng.get("finite_positive_fraction"), 3),
                median=_fmt(rng.get("median_m"), 3),
                expected=_fmt(rng.get("expected_m"), 3),
                accepted="yes" if item.get("accepted") else "no",
            )
        )
    lines.extend([
        "",
        "## Acceptance",
        "",
        "- Camera frames present: at least 5 frames.",
        "- Camera ground texture: textured cell fraction >= 0.90 and feature median >= 80.",
        "- Camera not sky: sky-like fraction < 0.20.",
        "- LiDAR finite: finite positive fraction >= 0.95.",
        "- LiDAR range: median within 0.30 m of attitude-corrected AGL.",
        "",
    ])
    path.write_text("\n".join(lines))


def _fmt(value, digits: int) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 8L Gate 3 standalone camera/LiDAR attitude pose proof.")
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--altitude-m", type=float, default=2.5)
    parser.add_argument("--record-s", type=float, default=8.0)
    parser.add_argument("--startup-timeout-s", type=float, default=25.0)
    parser.add_argument("--pose", action="append", help="Optional pose name:roll:pitch:yaw in degrees. Can be repeated.")
    return parser.parse_args()


def parse_poses(values: list[str] | None) -> list[tuple[str, float, float, float]]:
    if not values:
        return POSES
    poses = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 4:
            raise SystemExit(f"invalid --pose {value!r}; expected name:roll:pitch:yaw")
        poses.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    return poses


def main() -> int:
    args = parse_args()
    world = args.world.resolve()
    if not world.exists():
        raise SystemExit(f"world not found: {world}")
    world_name = load_world_name(world)
    out_dir = args.out_dir or PROJECT_ROOT / "experiments/inspections" / f"{utc_stamp()}_phase8l_attitude_pose_proof"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    poses = []
    for pose in parse_poses(args.pose):
        print(f"== pose {pose[0]} roll={pose[1]} pitch={pose[2]} yaw={pose[3]} ==")
        result = prove_pose(
            pose=pose,
            source_world=world,
            world_name=world_name,
            out_dir=out_dir,
            altitude_m=args.altitude_m,
            record_s=args.record_s,
            startup_timeout_s=args.startup_timeout_s,
        )
        (out_dir / pose[0] / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"accepted={result.get('accepted')}")
        poses.append(result)

    report = {
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "world_sdf": str(world),
        "world_name": world_name,
        "out_dir": str(out_dir),
        "vehicle_uri": VEHICLE_URI,
        "model_name": MODEL_NAME,
        "altitude_m": args.altitude_m,
        "poses": poses,
    }
    report["accepted"] = all(item.get("accepted") for item in poses)
    json_path = out_dir / "attitude_pose_report.json"
    md_path = out_dir / "attitude_pose_report.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    write_markdown(report, md_path)
    print(f"accepted={report['accepted']}")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
