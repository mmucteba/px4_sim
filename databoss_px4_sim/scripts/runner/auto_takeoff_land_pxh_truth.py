#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from create_run_from_scenario import (
    PROJECT_ROOT,
    PX4_ROOT,
    RUNS_DIR,
    load_yaml,
    validate_scenario,
    make_run_id,
    write_environment,
    resolve_gnss_loss_after_takeoff_s,
    resolve_failsafe_profile,
)

from launch_px4_headless_smoke import (
    clean_px4_env,
    read_tail,
    stop_process_group,
)

STARTUP_PATTERNS = [
    "Startup script returned successfully",
    "pxh>",
]

FLIGHT_READY_PATTERNS = [
    "Ready for takeoff",
    "home set",
    "pxh>",
]

TRUTH_TOPIC = "/world/default/dynamic_pose/info"
PX4_GZ_MODELS = PX4_ROOT / "Tools" / "simulation" / "gz" / "models"
PX4_GZ_WORLDS = PX4_ROOT / "Tools" / "simulation" / "gz" / "worlds"
PX4_GZ_PLUGINS = PX4_ROOT / "build" / "px4_sitl_default" / "src" / "modules" / "simulation" / "gz_plugins"
PX4_GZ_SERVER_CONFIG = PX4_ROOT / "src" / "modules" / "simulation" / "gz_bridge" / "server.config"
GENERATED_WORLDS_DIR = PROJECT_ROOT / "generated_worlds"
DEFAULT_PX4_HOME_LAT = "47.397742"
DEFAULT_PX4_HOME_LON = "8.545594"
DEFAULT_PX4_HOME_ALT = "488.0"
DEFAULT_XVFB_SERVER_ARGS = "-screen 0 1280x1024x24"
DEFAULT_GAZEBO_WEB_CONFIG = Path("/usr/share/gz/gz-launch7/configs/websocket.gzlaunch")
DEFAULT_GAZEBO_WEB_PORT = 9002
DEFAULT_GAZEBO_WEB_PUBLICATION_HZ = 15.0


def wait_for_pattern(log_path: Path, proc: subprocess.Popen, patterns: list[str], timeout_s: float) -> str | None:
    start = time.time()

    while time.time() - start < timeout_s:
        if proc.poll() is not None:
            return None

        tail = read_tail(log_path)
        for pattern in patterns:
            if pattern in tail:
                return pattern

        time.sleep(1)

    return None


def send_pxh(proc: subprocess.Popen, command: str, notes: list[str], record_note: bool = True) -> bool:
    if proc.poll() is not None:
        notes.append(f"cannot send command, process already exited: {command}")
        return False

    if proc.stdin is None:
        notes.append(f"cannot send command, stdin unavailable: {command}")
        return False

    try:
        proc.stdin.write(command + "\n")
        proc.stdin.flush()
        if record_note:
            notes.append(f"sent pxh command: {command}")
        return True
    except BrokenPipeError:
        notes.append(f"broken pipe sending command: {command}")
        return False


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def read_log_from(log_path: Path, offset: int) -> str:
    try:
        with log_path.open(errors="ignore") as handle:
            handle.seek(offset)
            return handle.read()
    except FileNotFoundError:
        return ""


def query_pxh(proc: subprocess.Popen, log_path: Path, command: str, notes: list[str], wait_s: float = 0.8) -> str:
    try:
        start_size = log_path.stat().st_size
    except FileNotFoundError:
        start_size = 0

    ok = send_pxh(proc, command, notes, record_note=False)
    if not ok:
        notes.append(f"readiness query failed: {command}")
        return ""

    time.sleep(wait_s)
    return strip_ansi(read_log_from(log_path, start_size))


def parse_listener_value(text: str, field: str) -> str | None:
    clean = strip_ansi(text)
    matches = re.findall(rf"^\s*{re.escape(field)}:\s*([^\r\n]+)", clean, flags=re.MULTILINE)
    if not matches:
        return None

    return matches[-1].strip()


def parse_listener_float(text: str, field: str) -> float | None:
    raw = parse_listener_value(text, field)
    if raw is None:
        return None

    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw)
    if match is None:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_listener_bool(text: str, field: str) -> bool | None:
    raw = parse_listener_value(text, field)
    if raw is None:
        return None

    value = raw.split()[0].strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    return None


def sample_global_position_readiness(proc: subprocess.Popen, log_path: Path, notes: list[str]) -> dict:
    gps_text = query_pxh(proc, log_path, "listener vehicle_gps_position 1", notes)
    gpos_text = query_pxh(proc, log_path, "listener vehicle_global_position 1", notes)
    failsafe_text = query_pxh(proc, log_path, "listener failsafe_flags 1", notes)

    gps_fix_type = parse_listener_float(gps_text, "fix_type")
    gps_satellites_used = parse_listener_float(gps_text, "satellites_used")
    gps_eph = parse_listener_float(gps_text, "eph")
    gps_epv = parse_listener_float(gps_text, "epv")

    gpos_lat_lon_valid = parse_listener_bool(gpos_text, "lat_lon_valid")
    gpos_alt_valid = parse_listener_bool(gpos_text, "alt_valid")
    gpos_eph = parse_listener_float(gpos_text, "eph")
    gpos_epv = parse_listener_float(gpos_text, "epv")
    gpos_dead_reckoning = parse_listener_bool(gpos_text, "dead_reckoning")

    failsafe_global_position_invalid = parse_listener_bool(failsafe_text, "global_position_invalid")
    failsafe_global_position_invalid_relaxed = parse_listener_bool(failsafe_text, "global_position_invalid_relaxed")

    gps_ready = (
        gps_fix_type is not None
        and gps_fix_type >= 3
        and gps_satellites_used is not None
        and gps_satellites_used >= 6
        and gps_eph is not None
        and gps_eph <= 5.0
        and gps_epv is not None
        and gps_epv <= 10.0
    )
    global_ready = (
        gpos_lat_lon_valid is True
        and gpos_alt_valid is True
        and (gpos_dead_reckoning is False or gpos_dead_reckoning is None)
        and (failsafe_global_position_invalid is False)
        and (failsafe_global_position_invalid_relaxed is False or failsafe_global_position_invalid_relaxed is None)
    )

    return {
        "gps_fix_type": gps_fix_type,
        "gps_satellites_used": gps_satellites_used,
        "gps_eph": gps_eph,
        "gps_epv": gps_epv,
        "gps_ready": gps_ready,
        "gpos_lat_lon_valid": gpos_lat_lon_valid,
        "gpos_alt_valid": gpos_alt_valid,
        "gpos_eph": gpos_eph,
        "gpos_epv": gpos_epv,
        "gpos_dead_reckoning": gpos_dead_reckoning,
        "failsafe_global_position_invalid": failsafe_global_position_invalid,
        "failsafe_global_position_invalid_relaxed": failsafe_global_position_invalid_relaxed,
        "global_ready": global_ready,
        "ready": gps_ready and global_ready,
    }


def confirm_gnss_loss(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
    max_attempts: int = 5,
    settle_s: float = 1.0,
) -> dict:
    """Verify the simulated GPS actually stopped after `SIM_GPS_USED 0`.

    PX4 acknowledges the param set (`curr: 10 -> new: 0`) but the Gazebo GPS
    bridge intermittently keeps publishing a nominal fix -- the recurring
    '115202-class' flake where GNSS-loss was requested and the command sent,
    yet fix_type/satellites never dropped, silently invalidating the run.
    Poll vehicle_gps_position (same topic/parser the arming readiness gate
    already uses) and re-assert `SIM_GPS_USED 0` until fix_type<3 or
    satellites_used<=0, so a failed cut is caught in-flight and fails the
    run loudly instead of producing a silent GNSS-on masquerade.
    """
    fix_type = None
    satellites = None
    for attempt in range(1, max_attempts + 1):
        text = query_pxh(proc, log_path, "listener vehicle_gps_position 1", notes)
        fix_type = parse_listener_float(text, "fix_type")
        satellites = parse_listener_float(text, "satellites_used")
        dropped = (fix_type is not None and fix_type < 3) or (
            satellites is not None and satellites <= 0
        )
        notes.append(
            f"gnss-loss confirm {attempt}/{max_attempts}: "
            f"fix_type={fix_type} satellites_used={satellites} dropped={dropped}"
        )
        if dropped:
            return {
                "verified": True,
                "attempts": attempt,
                "fix_type": fix_type,
                "satellites_used": satellites,
            }
        if attempt < max_attempts:
            send_pxh(proc, "param set SIM_GPS_USED 0", notes, record_note=False)
            time.sleep(settle_s)
    return {
        "verified": False,
        "attempts": max_attempts,
        "fix_type": fix_type,
        "satellites_used": satellites,
    }


def wait_for_target_altitude(
    proc: subprocess.Popen,
    log_path: Path,
    target_up_m: float,
    notes: list[str],
    tol_m: float = 1.5,
    stable_s: float = 3.0,
    timeout_s: float = 60.0,
    min_wait_s: float = 0.0,
    poll_s: float = 1.0,
) -> dict:
    """Wait until the vehicle is stable at target altitude before an event.

    Polls vehicle_local_position height (up = -z) until it stays within
    tol_m of target_up_m for stable_s continuously, honouring a min_wait_s
    floor and a timeout_s ceiling. This makes an altitude-dependent event
    (GNSS loss) fire at the commanded hold altitude regardless of how long
    the climb takes, so one scenario works at 2.5/15/35/60 m with no
    per-altitude timing tuning. A fixed-time trigger tuned for a 2.5 m
    takeoff cut GPS mid-climb at 15 m and diverged the EKF; this gate
    removes that class of bug for the whole altitude roadmap.
    """
    start = time.monotonic()
    stable_since: float | None = None
    last_up: float | None = None
    while True:
        elapsed = time.monotonic() - start
        text = query_pxh(proc, log_path, "listener vehicle_local_position 1", notes)
        z = parse_listener_float(text, "z")
        last_up = -z if z is not None else None
        within = last_up is not None and abs(last_up - target_up_m) <= tol_m
        if within:
            if stable_since is None:
                stable_since = time.monotonic()
            stable_for = time.monotonic() - stable_since
        else:
            stable_since = None
            stable_for = 0.0
        reached = within and stable_for >= stable_s and elapsed >= min_wait_s
        notes.append(
            f"altitude wait: elapsed={elapsed:.1f}s up={last_up} target={target_up_m} "
            f"within={within} stable_for={stable_for:.1f}s reached={reached}"
        )
        if reached:
            return {"reached": True, "elapsed_s": elapsed, "final_up_m": last_up}
        if elapsed >= timeout_s:
            return {"reached": False, "elapsed_s": elapsed, "final_up_m": last_up}
        time.sleep(poll_s)


def wait_for_global_position_ready(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
    timeout_s: float,
    stable_s: float,
    interval_s: float = 1.0,
) -> tuple[bool, list[dict], dict | None]:
    start = time.time()
    stable_start: float | None = None
    samples: list[dict] = []
    final_sample: dict | None = None

    while time.time() - start < timeout_s:
        if proc.poll() is not None:
            notes.append("global position readiness wait stopped; PX4 process exited")
            break

        sample = sample_global_position_readiness(proc, log_path, notes)
        now = time.time()
        sample["elapsed_s"] = now - start

        if sample["ready"]:
            if stable_start is None:
                stable_start = now
            sample["stable_for_s"] = now - stable_start
        else:
            stable_start = None
            sample["stable_for_s"] = 0.0

        samples.append(sample)
        final_sample = sample

        if sample["ready"] and sample["stable_for_s"] >= stable_s:
            notes.append(
                "global position readiness achieved: "
                f"fix_type={sample['gps_fix_type']}, sats={sample['gps_satellites_used']}, "
                f"gpos_eph={sample['gpos_eph']}, stable_for_s={sample['stable_for_s']:.2f}"
            )
            return True, samples, final_sample

        time.sleep(interval_s)

    notes.append(
        "global position readiness timed out: "
        f"timeout_s={timeout_s}, final_sample={final_sample}"
    )
    return False, samples, final_sample


def wait_for_offboard_mode(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
    timeout_s: float,
    interval_s: float = 0.2,
) -> tuple[bool, list[dict], dict | None, str | None]:
    start = time.time()
    samples: list[dict] = []
    final_sample = None
    final_text = None

    while time.time() - start < timeout_s:
        if proc.poll() is not None:
            notes.append("offboard mode wait stopped; PX4 process exited")
            break

        text = query_pxh(
            proc,
            log_path,
            "listener vehicle_status 1",
            notes,
            wait_s=0.25,
        )
        nav_state = parse_listener_float(text, "nav_state")
        accepts_offboard = parse_listener_bool(text, "accepts_offboard_setpoints")
        sample = {
            "elapsed_s": time.time() - start,
            "nav_state": nav_state,
            "accepts_offboard_setpoints": accepts_offboard,
        }
        samples.append(sample)
        final_sample = sample
        final_text = text

        if nav_state == 14 or accepts_offboard is True:
            notes.append(f"offboard mode detected: {sample}")
            return True, samples, final_sample, final_text

        time.sleep(interval_s)

    notes.append(f"offboard mode wait timed out: timeout_s={timeout_s}, final_sample={final_sample}")
    return False, samples, final_sample, final_text


def sample_land_detected(proc: subprocess.Popen, log_path: Path, notes: list[str]) -> dict:
    text = query_pxh(proc, log_path, "listener vehicle_land_detected 1", notes, wait_s=0.25)
    timestamp_us = parse_listener_float(text, "timestamp")
    landed = parse_listener_bool(text, "landed")
    return {
        "timestamp_us": timestamp_us,
        "timestamp_s": timestamp_us / 1e6 if timestamp_us is not None else None,
        "landed": landed,
    }


def sample_vehicle_local_position_timestamp_s(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
) -> float | None:
    text = query_pxh(proc, log_path, "listener vehicle_local_position 1", notes, wait_s=0.25)
    timestamp_us = parse_listener_float(text, "timestamp")
    return timestamp_us / 1e6 if timestamp_us is not None else None


def wait_for_airborne_duration(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
    target_airborne_s: float,
    timeout_wall_s: float,
    interval_wall_s: float = 1.0,
) -> tuple[bool, list[dict], dict | None]:
    start_wall = time.monotonic()
    airborne_start_px4_s: float | None = None
    samples: list[dict] = []
    final_sample: dict | None = None

    while time.monotonic() - start_wall < timeout_wall_s:
        if proc.poll() is not None:
            notes.append("airborne duration wait stopped; PX4 process exited")
            break

        sample = sample_land_detected(proc, log_path, notes)
        now_wall = time.monotonic()
        px4_s = sample_vehicle_local_position_timestamp_s(proc, log_path, notes)
        if px4_s is None:
            px4_s = sample["timestamp_s"]
        sample["clock_source"] = "vehicle_local_position" if px4_s != sample["timestamp_s"] else "vehicle_land_detected"
        sample["clock_timestamp_s"] = px4_s

        if sample["landed"] is False and px4_s is not None:
            if airborne_start_px4_s is None:
                airborne_start_px4_s = px4_s
            sample["airborne_duration_s"] = max(0.0, px4_s - airborne_start_px4_s)
        else:
            sample["airborne_duration_s"] = 0.0

        sample["elapsed_wall_s"] = now_wall - start_wall
        samples.append(sample)
        final_sample = sample

        if sample["airborne_duration_s"] >= target_airborne_s:
            notes.append(
                "airborne duration achieved: "
                f"target_s={target_airborne_s:.3f}, "
                f"airborne_s={sample['airborne_duration_s']:.3f}, "
                f"wall_s={sample['elapsed_wall_s']:.3f}"
            )
            return True, samples, final_sample

        if sample["landed"] is True and airborne_start_px4_s is not None:
            notes.append(
                "airborne duration wait stopped after early landing: "
                f"target_s={target_airborne_s:.3f}, "
                f"last_px4_s={px4_s}, wall_s={sample['elapsed_wall_s']:.3f}"
            )
            return False, samples, final_sample

        time.sleep(interval_wall_s)

    notes.append(
        "airborne duration wait timed out: "
        f"target_s={target_airborne_s:.3f}, timeout_wall_s={timeout_wall_s:.3f}, "
        f"final_sample={final_sample}"
    )
    return False, samples, final_sample


def wait_for_landing_complete(
    proc: subprocess.Popen,
    log_path: Path,
    notes: list[str],
    timeout_wall_s: float,
    interval_wall_s: float = 1.0,
) -> bool:
    start_wall = time.monotonic()
    final_sample: dict | None = None

    while time.monotonic() - start_wall < timeout_wall_s:
        if proc.poll() is not None:
            notes.append("landing wait stopped; PX4 process exited")
            return True

        sample = sample_land_detected(proc, log_path, notes)
        final_sample = sample
        try:
            log_text = strip_ansi(log_path.read_text(errors="ignore"))
        except FileNotFoundError:
            log_text = ""

        if sample["landed"] is True and (
            "Disarmed by landing" in log_text or "closed logfile" in log_text
        ):
            notes.append(
                "landing complete: "
                f"wall_s={time.monotonic() - start_wall:.3f}, sample={sample}"
            )
            return True

        time.sleep(interval_wall_s)

    notes.append(
        "landing wait timed out: "
        f"timeout_wall_s={timeout_wall_s:.3f}, final_sample={final_sample}"
    )
    return False


def parse_qgc_status(log_text: str, qgc_ip: str) -> dict:
    clean = strip_ansi(log_text)
    lowered = clean.lower()

    heartbeat_seen = "gcs heartbeat valid" in lowered
    partner_ip_seen = qgc_ip in clean
    sysid_255_seen = (
        "sysid 255" in lowered
        or "sysid:255" in lowered
        or "sysid: 255" in lowered
        or "system id: 255" in lowered
    )
    rx_seen = (
        "received messages" in lowered
        or "rx:" in lowered
        or "rx rate:" in lowered
        or "messages received" in lowered
    )
    dropped_zero_seen = (
        "dropped packets: 0" in lowered
        or "dropped: 0" in lowered
        or "packet_rx_drop_count 0" in lowered
    )

    connected = heartbeat_seen and partner_ip_seen

    return {
        "qgc_status_checked": "mavlink status" in lowered,
        "qgc_gcs_heartbeat_seen": heartbeat_seen,
        "qgc_partner_ip_seen": partner_ip_seen,
        "qgc_rx_sysid_255_seen": sysid_255_seen,
        "qgc_rx_messages_seen": rx_seen,
        "qgc_dropped_packets_zero_seen": dropped_zero_seen,
        "qgc_connected": connected,
    }


def automation_sitl_commands() -> list[str]:
    return [
        "param set COM_RC_IN_MODE 4",
    ]


def reset_px4_parameter_store(run_dir: Path, notes: list[str]) -> tuple[bool, list[str]]:
    rootfs = PX4_ROOT / "build" / "px4_sitl_default" / "rootfs"
    backups: list[str] = []
    reset_ok = True

    for name in ["parameters.bson", "parameters_backup.bson"]:
        src = rootfs / name
        if not src.exists():
            continue

        backup = run_dir / "logs" / f"prelaunch_{name}"

        try:
            shutil.copy2(src, backup)
            backups.append(str(backup))
            src.unlink()
            notes.append(f"archived and removed PX4 parameter store: {src} -> {backup}")
        except Exception as exc:
            reset_ok = False
            notes.append(f"failed to reset PX4 parameter store {src}: {exc}")

    return reset_ok, backups


def px4_prelaunch_param_overrides(
    external_odom_enabled: bool,
    external_odom_ev_ctrl: int,
    external_odom_ev_delay_ms: float,
    gnss_start_used: int,
    flow_bridge_enabled: bool = False,
    stock_flow_enabled: bool = False,
) -> dict[str, str]:
    overrides = {
        "COM_RC_IN_MODE": "4",
        "EKF2_GPS_CTRL": "7",
        "EKF2_EV_CTRL": str(external_odom_ev_ctrl if external_odom_enabled else 0),
        # Boot with GNSS height so PX4 initializes a valid global altitude origin
        # before AUTO takeoff. External height fusion can be enabled after startup.
        "EKF2_HGT_REF": "1",
        "EKF2_EV_DELAY": f"{external_odom_ev_delay_ms:g}",
        "SIM_GPS_USED": str(gnss_start_used),
    }
    if flow_bridge_enabled or stock_flow_enabled:
        # Default profile caps sensor_optical_flow logging at 1 Hz; the
        # HIGH_RATE_SENSORS profile bit (1<<11) logs it at full rate so the
        # open-loop analyzer can match sent samples to received ones.
        # 2179 = 131 (existing default+estimator-replay bits) + 2048.
        overrides["SDLOG_PROFILE"] = "2179"
    if stock_flow_enabled:
        # PX4's stock x500_flow airframe is normally an optical-flow-only demo
        # and may boot with GPS disabled. Phase 8J needs GNSS-on takeoff first,
        # followed by the accepted runtime outage command.
        overrides["SYS_HAS_GPS"] = "1"
        overrides["EKF2_GPS_CTRL"] = "7"
        # These sim plugin toggles are marked reboot-required in PX4, so set
        # them before SITL starts as well as documenting them in the runtime log.
        overrides["SIM_GZ_EN_FLOW"] = "1"
        overrides["SIM_GZ_EN_LIDAR"] = "1"
        overrides["EKF2_OF_CTRL"] = "1"
    return overrides


def apply_px4_param_env(env: dict[str, str], overrides: dict[str, str], notes: list[str]) -> None:
    for name, value in overrides.items():
        env[f"PX4_PARAM_{name}"] = value
    notes.append(f"applied PX4 prelaunch parameter overrides: {overrides}")


def analyze_ulog_flight(ulog_path: Path, takeoff_alt_m: float, requested_airborne_s: float) -> dict:
    result = {
        "ulog_flight_analysis_ok": False,
        "ulog_airborne_duration_s": None,
        "ulog_height_above_0p5_duration_s": None,
        "ulog_max_height_up_m": None,
        "ulog_reached_min_height": False,
        "ulog_airborne_duration_ok": False,
        "ulog_flight_ok": False,
        "ulog_flight_error": None,
    }

    try:
        from pyulog import ULog
        import numpy as np

        ulog = ULog(str(ulog_path))

        local_pos = ulog.get_dataset("vehicle_local_position").data
        pos_t = np.array(local_pos["timestamp"], dtype=float) / 1e6
        height_up = -np.array(local_pos["z"], dtype=float)

        result["ulog_max_height_up_m"] = float(np.nanmax(height_up))

        above_0p5 = np.flatnonzero(height_up > 0.5)
        if len(above_0p5):
            result["ulog_height_above_0p5_duration_s"] = float(pos_t[above_0p5[-1]] - pos_t[above_0p5[0]])
        else:
            result["ulog_height_above_0p5_duration_s"] = 0.0

        land_detected = ulog.get_dataset("vehicle_land_detected").data
        land_t = np.array(land_detected["timestamp"], dtype=float) / 1e6
        landed = np.array(land_detected["landed"]).astype(bool)
        airborne = np.flatnonzero(~landed)

        if len(airborne):
            result["ulog_airborne_duration_s"] = float(land_t[airborne[-1]] - land_t[airborne[0]])
        else:
            result["ulog_airborne_duration_s"] = 0.0

        min_height_m = max(0.5, takeoff_alt_m * 0.8)
        min_airborne_s = max(10.0, requested_airborne_s * 0.8)

        result["ulog_reached_min_height"] = result["ulog_max_height_up_m"] >= min_height_m
        result["ulog_airborne_duration_ok"] = result["ulog_airborne_duration_s"] >= min_airborne_s
        result["ulog_flight_analysis_ok"] = True
        result["ulog_flight_ok"] = result["ulog_reached_min_height"] and result["ulog_airborne_duration_ok"]

    except Exception as exc:
        result["ulog_flight_error"] = str(exc)

    return result


def analyze_ulog_external_odometry(
    ulog_path: Path,
    require_position: bool,
    require_height: bool,
    require_velocity: bool,
) -> dict:
    result = {
        "ulog_external_odom_analysis_ok": False,
        "ulog_external_odom_required_position": require_position,
        "ulog_external_odom_required_height": require_height,
        "ulog_external_odom_required_velocity": require_velocity,
        "ulog_vehicle_visual_odometry_rows": 0,
        "ulog_ev_pos_aid_rows": 0,
        "ulog_ev_hgt_aid_rows": 0,
        "ulog_ev_vel_aid_rows": 0,
        "ulog_ev_pos_active_count": 0,
        "ulog_ev_hgt_active_count": 0,
        "ulog_ev_vel_active_count": 0,
        "ulog_ev_pos_fused_count": 0,
        "ulog_ev_hgt_fused_count": 0,
        "ulog_ev_vel_fused_count": 0,
        "ulog_ev_pos_rejected_count": 0,
        "ulog_ev_hgt_rejected_count": 0,
        "ulog_ev_vel_rejected_count": 0,
        "ulog_xy_reset_counter_start": None,
        "ulog_xy_reset_counter_end": None,
        "ulog_xy_reset_counter_delta": None,
        "ulog_external_odom_fusion_ok": not (require_position or require_height or require_velocity),
        "ulog_external_odom_error": None,
    }

    if not (require_position or require_height or require_velocity):
        result["ulog_external_odom_analysis_ok"] = True
        return result

    try:
        from pyulog import ULog
        import numpy as np

        ulog = ULog(str(ulog_path))

        for name, key in [
            ("vehicle_visual_odometry", "ulog_vehicle_visual_odometry_rows"),
            ("estimator_aid_src_ev_pos", "ulog_ev_pos_aid_rows"),
            ("estimator_aid_src_ev_hgt", "ulog_ev_hgt_aid_rows"),
            ("estimator_aid_src_ev_vel", "ulog_ev_vel_aid_rows"),
        ]:
            try:
                data = ulog.get_dataset(name).data
                result[key] = len(next(iter(data.values())))
            except Exception:
                result[key] = 0

        flags = ulog.get_dataset("estimator_status_flags").data

        if "cs_ev_pos" in flags:
            result["ulog_ev_pos_active_count"] = int(np.count_nonzero(np.asarray(flags["cs_ev_pos"])))

        if "cs_ev_hgt" in flags:
            result["ulog_ev_hgt_active_count"] = int(np.count_nonzero(np.asarray(flags["cs_ev_hgt"])))

        if "cs_ev_vel" in flags:
            result["ulog_ev_vel_active_count"] = int(np.count_nonzero(np.asarray(flags["cs_ev_vel"])))

        for dataset_name, prefix in [
            ("estimator_aid_src_ev_pos", "ulog_ev_pos"),
            ("estimator_aid_src_ev_hgt", "ulog_ev_hgt"),
            ("estimator_aid_src_ev_vel", "ulog_ev_vel"),
        ]:
            try:
                aid = ulog.get_dataset(dataset_name).data
                if "fused" in aid:
                    result[f"{prefix}_fused_count"] = int(np.count_nonzero(np.asarray(aid["fused"])))
                if "innovation_rejected" in aid:
                    result[f"{prefix}_rejected_count"] = int(
                        np.count_nonzero(np.asarray(aid["innovation_rejected"]))
                    )
            except Exception:
                pass

        try:
            local_pos = ulog.get_dataset("vehicle_local_position").data
            if "xy_reset_counter" in local_pos and len(local_pos["xy_reset_counter"]) > 0:
                xy_reset = np.asarray(local_pos["xy_reset_counter"])
                result["ulog_xy_reset_counter_start"] = int(xy_reset[0])
                result["ulog_xy_reset_counter_end"] = int(xy_reset[-1])
                result["ulog_xy_reset_counter_delta"] = int(xy_reset[-1]) - int(xy_reset[0])
        except Exception:
            pass

        position_ok = (
            not require_position
            or (
                result["ulog_vehicle_visual_odometry_rows"] > 10
                and result["ulog_ev_pos_aid_rows"] > 10
                and result["ulog_ev_pos_active_count"] > 0
            )
        )
        height_ok = (
            not require_height
            or (
                result["ulog_vehicle_visual_odometry_rows"] > 10
                and result["ulog_ev_hgt_aid_rows"] > 10
                and result["ulog_ev_hgt_active_count"] > 0
            )
        )
        velocity_ok = (
            not require_velocity
            or (
                result["ulog_vehicle_visual_odometry_rows"] > 10
                and result["ulog_ev_vel_aid_rows"] > 10
                and result["ulog_ev_vel_active_count"] > 0
            )
        )

        result["ulog_external_odom_analysis_ok"] = True
        result["ulog_external_odom_fusion_ok"] = position_ok and height_ok and velocity_ok

    except Exception as exc:
        result["ulog_external_odom_error"] = str(exc)

    return result



def failsafe_profile_commands(profile: str) -> list[str]:
    if profile == "default_px4":
        return automation_sitl_commands() + [
            "param set NAV_DLL_ACT 2",
            "param set COM_POS_FS_EPH 5",
            "param set COM_POS_LOW_ACT 3",
            "param set EKF2_NOAID_TOUT 5000000",
        ]

    if profile == "delayed_observation":
        return automation_sitl_commands() + [
            "param set NAV_DLL_ACT 0",
            "param set COM_POS_FS_EPH 200",
            "param set COM_POS_LOW_ACT 0",
            "param set EKF2_NOAID_TOUT 120000000",
        ]

    raise ValueError(f"unknown failsafe profile: {profile}")


def newest_ulog(after_ts: float) -> Path | None:
    log_root = PX4_ROOT / "build" / "px4_sitl_default" / "rootfs" / "log"
    if not log_root.exists():
        return None

    candidates = []
    for p in log_root.rglob("*.ulg"):
        try:
            if p.stat().st_mtime >= after_ts - 5:
                candidates.append(p)
        except FileNotFoundError:
            pass

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def truth_topic_for_world(world_name: str) -> str:
    return f"/world/{world_name}/dynamic_pose/info"


def gazebo_model_instance(model: str, vehicle: dict) -> str:
    override = vehicle.get("gazebo_model_name") or vehicle.get("truth_model_name")
    if override:
        return str(override)

    model_name = model.removeprefix("gz_")
    return f"{model_name}_0"


def default_camera_image_topic(world_name: str, gazebo_model_name: str) -> str:
    return f"/world/{world_name}/model/{gazebo_model_name}/link/camera_link/sensor/camera/image"


def default_rangefinder_scan_topic(world_name: str, gazebo_model_name: str) -> str:
    # Same topic the PX4 gz_bridge subscribes to for its distance_sensor conversion.
    return f"/world/{world_name}/model/{gazebo_model_name}/link/lidar_sensor_link/sensor/lidar/scan"


def default_imu_topic(world_name: str, gazebo_model_name: str) -> str:
    return f"/world/{world_name}/model/{gazebo_model_name}/link/base_link/sensor/imu_sensor/imu"


def resolve_world_sdf(world_cfg: dict) -> Path | None:
    world_name = str(world_cfg.get("name", "default"))
    explicit = (
        world_cfg.get("sdf")
        or world_cfg.get("sdf_path")
        or world_cfg.get("generated_sdf")
    )

    if explicit:
        path = Path(str(explicit)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    if world_name == "default":
        return None

    candidate = GENERATED_WORLDS_DIR / f"{world_name}.sdf"
    return candidate.resolve() if candidate.exists() else None


def add_gazebo_standalone_env(env: dict, world_name: str, world_sdf: Path) -> dict:
    resource_paths = [
        str(PX4_GZ_MODELS),
        str(PX4_GZ_WORLDS),
        str(world_sdf.parent),
    ]
    existing_resources = [
        item for item in env.get("GZ_SIM_RESOURCE_PATH", "").split(":") if item
    ]

    env["HEADLESS"] = "1"
    env["GZ_IP"] = "127.0.0.1"
    env["GZ_PARTITION"] = f"databoss_{world_name}_{os.getpid()}"
    env["PX4_GZ_STANDALONE"] = "1"
    env["PX4_GZ_WORLD"] = world_name
    env["PX4_GZ_WORLDS"] = str(world_sdf.parent)
    env["PX4_GZ_MODELS"] = str(PX4_GZ_MODELS)
    env["PX4_GZ_PLUGINS"] = str(PX4_GZ_PLUGINS)
    env["PX4_GZ_SERVER_CONFIG"] = str(PX4_GZ_SERVER_CONFIG)
    env["PX4_GZ_NO_FOLLOW"] = "1"
    env["PX4_HOME_LAT"] = str(env.get("PX4_HOME_LAT", DEFAULT_PX4_HOME_LAT))
    env["PX4_HOME_LON"] = str(env.get("PX4_HOME_LON", DEFAULT_PX4_HOME_LON))
    env["PX4_HOME_ALT"] = str(env.get("PX4_HOME_ALT", DEFAULT_PX4_HOME_ALT))
    env["GZ_SIM_RESOURCE_PATH"] = ":".join(resource_paths + existing_resources)
    env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = str(PX4_GZ_PLUGINS)
    env["GZ_SIM_SERVER_CONFIG_PATH"] = str(PX4_GZ_SERVER_CONFIG)
    return env


def write_gazebo_server_config(run_dir: Path, render_engine: str | None, notes: list[str]) -> Path:
    if not render_engine:
        return PX4_GZ_SERVER_CONFIG

    text = PX4_GZ_SERVER_CONFIG.read_text()
    rendered = re.sub(
        r"<render_engine>[^<]+</render_engine>",
        f"<render_engine>{render_engine}</render_engine>",
        text,
        count=1,
    )
    server_config = run_dir / "logs" / f"gz_server_{render_engine}.config"
    server_config.write_text(rendered)
    notes.append(f"wrote Gazebo server config override: {server_config}, render_engine={render_engine}")
    return server_config


def resolve_project_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def write_gazebo_websocket_config(
    run_dir: Path,
    source_config: Path,
    port: int,
    publication_hz: float,
    notes: list[str],
) -> Path:
    text = source_config.read_text()
    rendered = re.sub(
        r"<publication_hz>[^<]+</publication_hz>",
        f"<publication_hz>{publication_hz:g}</publication_hz>",
        text,
        count=1,
    )
    rendered = re.sub(
        r"<port>[^<]+</port>",
        f"<port>{port}</port>",
        rendered,
        count=1,
    )
    websocket_config = run_dir / "logs" / f"gazebo_websocket_{port}.gzlaunch"
    websocket_config.write_text(rendered)
    notes.append(
        "wrote Gazebo websocket launch config: "
        f"{websocket_config}, port={port}, publication_hz={publication_hz:g}"
    )
    return websocket_config


def wait_for_tcp_port(
    host: str,
    port: int,
    proc: subprocess.Popen,
    timeout_s: float,
    notes: list[str],
) -> bool:
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            notes.append(f"Gazebo websocket bridge exited early with returncode={proc.returncode}")
            return False

        try:
            with socket.create_connection((host, port), timeout=0.5):
                notes.append(f"Gazebo websocket bridge is listening on {host}:{port}")
                return True
        except OSError:
            time.sleep(0.5)

    notes.append(f"Gazebo websocket bridge readiness timeout on {host}:{port}")
    return False


def tcp_port_open(host: str, port: int, timeout_s: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def gazebo_command_with_display(
    base_cmd: list[str],
    use_xvfb: bool,
    xvfb_server_args: str,
) -> list[str]:
    if not use_xvfb:
        return base_cmd

    return [
        "xvfb-run",
        "-a",
        "-s",
        xvfb_server_args,
        *base_cmd,
    ]


def run_gz_query(args: list[str], env: dict, timeout_s: float = 5.0) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or ""


def wait_for_standalone_world(
    world_name: str,
    proc: subprocess.Popen,
    env: dict,
    timeout_s: float,
    notes: list[str],
) -> bool:
    service = f"/world/{world_name}/scene/info"
    start = time.time()
    while time.time() - start < timeout_s:
        if proc.poll() is not None:
            notes.append(f"standalone gazebo exited early with returncode={proc.returncode}")
            return False

        rc, out = run_gz_query(["gz", "service", "-i", "--service", service], env)
        if rc == 0 and "Service providers" in out:
            notes.append(f"standalone gazebo world ready: {world_name}")
            return True

        time.sleep(1)

    notes.append(f"standalone gazebo world readiness timeout: {world_name}")
    return False


def make_run_folder(scenario_path: Path, data: dict, truth_topic: str) -> Path:
    scenario_name = data["run"]["name"]
    run_id = make_run_id(scenario_name + "_pxh_takeoff_land_truth")
    run_dir = RUNS_DIR / run_id

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "logs").mkdir()
    (run_dir / "gazebo_truth").mkdir()
    (run_dir / "extracted_csv").mkdir()
    (run_dir / "plots").mkdir()

    shutil.copy2(scenario_path, run_dir / "config.yaml")

    (run_dir / "README.md").write_text(
        "\n".join([
            f"# {run_id}",
            "",
            "Created by Phase 7A.4 automated PX4 shell takeoff/land with Gazebo truth recording.",
            "",
            "This run starts PX4/Gazebo headless, records Gazebo dynamic pose truth, flies takeoff/land, then copies ULog.",
            "",
        ])
    )

    (run_dir / "commands.log").write_text(
        "\n".join([
            "# Commands",
            "",
            f"cd {PROJECT_ROOT}",
            f"python3 scripts/runner/auto_takeoff_land_pxh_truth.py {scenario_path}",
            "",
            "PX4 shell command sequence:",
            "",
            "param set SIM_GPS_USED 10",
            "commander arm -f",
            "commander takeoff",
            "commander land",
            "",
            "Gazebo truth recorder:",
            "",
            f"gz topic -e -t {truth_topic}",
            "",
        ])
    )

    write_environment(run_dir / "environment.txt")
    return run_dir


def start_truth_recorder(
    run_dir: Path,
    notes: list[str],
    truth_topic: str,
    env: dict,
) -> tuple[subprocess.Popen, Path, Path]:
    raw_path = run_dir / "gazebo_truth" / "gazebo_ground_truth_raw.txt"
    err_path = run_dir / "gazebo_truth" / "gazebo_ground_truth_recorder.err"

    raw = raw_path.open("w")
    err = err_path.open("w")

    cmd = ["gz", "topic", "-e", "-t", truth_topic]

    proc = subprocess.Popen(
        cmd,
        stdout=raw,
        stderr=err,
        env=env,
        text=True,
        preexec_fn=os.setsid,
    )

    notes.append(f"started gazebo truth recorder pid={proc.pid}, topic={truth_topic}")

    # Store file handles on proc so they do not get garbage collected.
    proc._databoss_raw_handle = raw
    proc._databoss_err_handle = err

    return proc, raw_path, err_path


def probe_camera_topic(
    run_dir: Path,
    env: dict,
    topic: str,
    timeout_s: float,
    notes: list[str],
) -> dict:
    camera_dir = run_dir / "camera"
    camera_dir.mkdir(parents=True, exist_ok=True)
    topic_list_path = camera_dir / "gz_topics.txt"
    topic_info_path = camera_dir / "camera_topic_info.txt"
    sample_path = camera_dir / "camera_image_sample.txt"
    err_path = camera_dir / "camera_image_sample.err"

    rc, topic_list = run_gz_query(["gz", "topic", "-l"], env, timeout_s=5.0)
    topic_list_path.write_text(topic_list)
    topic_seen = rc == 0 and topic in topic_list.splitlines()

    info_rc, topic_info = run_gz_query(["gz", "topic", "-i", "-t", topic], env, timeout_s=5.0)
    topic_info_path.write_text(topic_info)

    sample_rc = None
    sample_timeout = False
    with sample_path.open("w") as sample, err_path.open("w") as err:
        try:
            completed = subprocess.run(
                ["gz", "topic", "-e", "-n", "1", "-t", topic],
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                stdout=sample,
                stderr=err,
                timeout=timeout_s,
                check=False,
            )
            sample_rc = completed.returncode
        except subprocess.TimeoutExpired:
            sample_timeout = True

    sample_bytes = sample_path.stat().st_size if sample_path.exists() else 0
    accepted = topic_seen and sample_rc == 0 and not sample_timeout and sample_bytes > 1000
    result = {
        "camera_topic": topic,
        "camera_topic_seen": topic_seen,
        "camera_topic_list_path": str(topic_list_path),
        "camera_topic_info_path": str(topic_info_path),
        "camera_topic_info_returncode": info_rc,
        "camera_image_sample_path": str(sample_path),
        "camera_image_sample_err_path": str(err_path),
        "camera_image_sample_returncode": sample_rc,
        "camera_image_sample_timeout": sample_timeout,
        "camera_image_sample_bytes": sample_bytes,
        "camera_probe_ok": accepted,
    }
    notes.append(f"camera_probe_result={result}")
    return result


def parse_laser_scan_range(sample_text: str) -> float | None:
    match = re.search(r"^ranges:\s*([-+0-9.eE]+|inf|-inf)\s*$", sample_text, flags=re.MULTILINE)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def probe_rangefinder_topic(
    run_dir: Path,
    env: dict,
    topic: str,
    timeout_s: float,
    notes: list[str],
) -> dict:
    rangefinder_dir = run_dir / "rangefinder"
    rangefinder_dir.mkdir(parents=True, exist_ok=True)
    topic_list_path = rangefinder_dir / "gz_topics.txt"
    topic_info_path = rangefinder_dir / "rangefinder_topic_info.txt"
    sample_path = rangefinder_dir / "rangefinder_scan_sample.txt"
    err_path = rangefinder_dir / "rangefinder_scan_sample.err"

    rc, topic_list = run_gz_query(["gz", "topic", "-l"], env, timeout_s=5.0)
    topic_list_path.write_text(topic_list)
    topic_seen = rc == 0 and topic in topic_list.splitlines()

    info_rc, topic_info = run_gz_query(["gz", "topic", "-i", "-t", topic], env, timeout_s=5.0)
    topic_info_path.write_text(topic_info)

    sample_rc = None
    sample_timeout = False
    with sample_path.open("w") as sample, err_path.open("w") as err:
        try:
            completed = subprocess.run(
                ["gz", "topic", "-e", "-n", "1", "-t", topic],
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                stdout=sample,
                stderr=err,
                timeout=timeout_s,
                check=False,
            )
            sample_rc = completed.returncode
        except subprocess.TimeoutExpired:
            sample_timeout = True

    sample_bytes = sample_path.stat().st_size if sample_path.exists() else 0
    sample_text = sample_path.read_text(errors="ignore") if sample_bytes else ""
    sample_range_m = parse_laser_scan_range(sample_text)
    range_finite_positive = (
        sample_range_m is not None
        and math.isfinite(sample_range_m)
        and sample_range_m > 0.0
    )
    accepted = topic_seen and sample_rc == 0 and not sample_timeout and range_finite_positive
    result = {
        "rangefinder_scan_topic": topic,
        "rangefinder_topic_seen": topic_seen,
        "rangefinder_topic_list_path": str(topic_list_path),
        "rangefinder_topic_info_path": str(topic_info_path),
        "rangefinder_topic_info_returncode": info_rc,
        "rangefinder_scan_sample_path": str(sample_path),
        "rangefinder_scan_sample_err_path": str(err_path),
        "rangefinder_scan_sample_returncode": sample_rc,
        "rangefinder_scan_sample_timeout": sample_timeout,
        "rangefinder_scan_sample_bytes": sample_bytes,
        "rangefinder_sample_range_m": sample_range_m,
        "rangefinder_probe_ok": accepted,
    }
    notes.append(f"rangefinder_probe_result={result}")
    return result


def analyze_ulog_distance_sensor(
    ulog_path: Path,
    required: bool,
    min_rows: int,
    height_agreement_tolerance_m: float,
) -> dict:
    result = {
        "ulog_distance_sensor_analysis_ok": False,
        "ulog_distance_sensor_required": required,
        "ulog_distance_sensor_rows": 0,
        "ulog_distance_sensor_max_m": None,
        "ulog_distance_sensor_median_m": None,
        "ulog_distance_sensor_height_diff_m": None,
        "ulog_distance_sensor_ok": not required,
        "ulog_distance_sensor_error": None,
    }

    if not required:
        result["ulog_distance_sensor_analysis_ok"] = True
        return result

    try:
        from pyulog import ULog
        import numpy as np

        ulog = ULog(str(ulog_path))
        dist = ulog.get_dataset("distance_sensor").data
        distances = np.asarray(dist["current_distance"], dtype=float)
        finite = distances[np.isfinite(distances)]
        result["ulog_distance_sensor_rows"] = int(len(distances))

        if len(finite):
            result["ulog_distance_sensor_max_m"] = float(np.nanmax(finite))
            result["ulog_distance_sensor_median_m"] = float(np.nanmedian(finite))

        local_pos = ulog.get_dataset("vehicle_local_position").data
        max_height_up = float(np.nanmax(-np.asarray(local_pos["z"], dtype=float)))

        if result["ulog_distance_sensor_max_m"] is not None:
            result["ulog_distance_sensor_height_diff_m"] = abs(
                result["ulog_distance_sensor_max_m"] - max_height_up
            )

        result["ulog_distance_sensor_analysis_ok"] = True
        result["ulog_distance_sensor_ok"] = (
            result["ulog_distance_sensor_rows"] >= min_rows
            and result["ulog_distance_sensor_height_diff_m"] is not None
            and result["ulog_distance_sensor_height_diff_m"] <= height_agreement_tolerance_m
        )

    except Exception as exc:
        result["ulog_distance_sensor_error"] = str(exc)

    return result


def close_truth_recorder(proc: subprocess.Popen, notes: list[str]) -> None:
    stop_process_group(proc, notes)

    for attr in ["_databoss_raw_handle", "_databoss_err_handle"]:
        handle = getattr(proc, attr, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def count_csv_data_rows(path: Path) -> int:
    if not path.exists():
        return 0

    with path.open(errors="ignore") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def count_csv_sent_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "sent" not in reader.fieldnames:
            return max(0, sum(1 for _ in reader))
        return sum(1 for row in reader if str(row.get("sent", "")).strip() == "1")


def count_csv_mavlink_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "mavlink_sent" not in reader.fieldnames:
            return 0
        return sum(1 for row in reader if str(row.get("mavlink_sent", "")).strip() == "1")


def wait_for_flow_bridge_prearm(
    proc: subprocess.Popen,
    sent_path: Path,
    min_mavlink_rows: int,
    timeout_s: float,
    notes: list[str],
) -> dict:
    deadline = time.monotonic() + max(0.0, timeout_s)
    result = {
        "flow_bridge_prearm_required": min_mavlink_rows > 0,
        "flow_bridge_prearm_min_mavlink_rows": min_mavlink_rows,
        "flow_bridge_prearm_timeout_s": timeout_s,
        "flow_bridge_prearm_mavlink_rows": 0,
        "flow_bridge_prearm_ok": min_mavlink_rows <= 0,
        "flow_bridge_prearm_process_alive": proc.poll() is None,
    }

    while min_mavlink_rows > 0 and time.monotonic() < deadline:
        result["flow_bridge_prearm_process_alive"] = proc.poll() is None
        result["flow_bridge_prearm_mavlink_rows"] = count_csv_mavlink_rows(sent_path)

        if not result["flow_bridge_prearm_process_alive"]:
            break

        if result["flow_bridge_prearm_mavlink_rows"] >= min_mavlink_rows:
            result["flow_bridge_prearm_ok"] = True
            break

        time.sleep(0.25)

    if min_mavlink_rows > 0 and not result["flow_bridge_prearm_ok"]:
        result["flow_bridge_prearm_mavlink_rows"] = count_csv_mavlink_rows(sent_path)
        result["flow_bridge_prearm_process_alive"] = proc.poll() is None

    notes.append(f"flow_bridge_prearm_result={result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated PX4 shell takeoff/land with Gazebo truth recording.")
    parser.add_argument("scenario", help="Path to scenario YAML")
    parser.add_argument("--hover-s", type=float, default=25.0)
    parser.add_argument("--startup-timeout-s", type=float, default=150.0)
    parser.add_argument("--land-timeout-s", type=float, default=70.0)
    parser.add_argument("--qgc-ip", default="100.109.200.5", help="QGroundControl Tailscale/IP target")
    parser.add_argument("--qgc-local-port", type=int, default=14555)
    parser.add_argument("--qgc-remote-port", type=int, default=14550)
    parser.add_argument("--qgc-rate", type=int, default=1000000)
    parser.add_argument("--no-qgc", action="store_true", help="Do not start the extra QGC MAVLink stream")
    parser.add_argument("--gnss-start-used", type=int, default=10, help="Initial SIM_GPS_USED value")
    parser.add_argument("--gnss-loss-after-takeoff-s", type=float, default=None, help="Seconds after takeoff command to set SIM_GPS_USED=0")
    parser.add_argument("--post-loss-hover-s", type=float, default=None, help="Seconds to wait after GNSS loss before landing")
    parser.add_argument("--failsafe-profile", choices=["default_px4", "delayed_observation"], default=None,
                        help="Override scenario failsafe.profile. If omitted, the scenario YAML's failsafe.profile is authoritative (default -> default_px4).")
    parser.add_argument("--global-position-timeout-s", type=float, default=90.0, help="Seconds to wait for valid GPS-backed global position before arming")
    parser.add_argument("--global-position-stable-s", type=float, default=5.0, help="Seconds global position must remain valid before arming")
    parser.add_argument("--no-global-position-gate", action="store_true", help="Skip the pre-arm global position readiness gate")
    parser.add_argument(
        "--allow-experimental-ev-velocity",
        action="store_true",
        help="Allow EKF2_EV_CTRL velocity fusion. This is blocked by default until the bridge velocity source is repaired.",
    )
    args = parser.parse_args()

    scenario_path = Path(args.scenario).expanduser().resolve()
    if not scenario_path.exists():
        print(f"ERROR: scenario not found: {scenario_path}", file=sys.stderr)
        return 1

    data = load_yaml(scenario_path)
    errors = validate_scenario(data)
    if errors:
        print("ERROR: scenario validation failed:", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        return 1

    # Scenario YAML is authoritative for the GNSS-loss / failsafe launch
    # contract when the matching CLI flag is not given; the CLI stays an
    # explicit override (see create_run_from_scenario for the rationale and
    # the phase_10/phase_12 trap this closes).
    args.gnss_loss_after_takeoff_s, gnss_loss_source = resolve_gnss_loss_after_takeoff_s(
        data, args.gnss_loss_after_takeoff_s
    )
    try:
        args.failsafe_profile, failsafe_profile_source = resolve_failsafe_profile(
            data, args.failsafe_profile
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"gnss_loss_source={gnss_loss_source}")
    print(f"resolved_gnss_loss_after_takeoff_s={args.gnss_loss_after_takeoff_s}")
    print(f"failsafe_profile_source={failsafe_profile_source}")
    print(f"resolved_failsafe_profile={args.failsafe_profile}")

    vehicle = data.get("vehicle", {})
    model = vehicle.get("model", "gz_x500")
    gazebo_model_name = gazebo_model_instance(model, vehicle)
    world = data.get("world", {})
    world_name = str(world.get("name", "default"))
    world_sdf = resolve_world_sdf(world)
    standalone_gazebo_enabled = world_sdf is not None and world_name != "default"
    truth_topic = truth_topic_for_world(world_name)
    logging_cfg = data.get("logging", {})
    camera_cfg = data.get("camera", {})
    camera_proof_enabled = bool(
        camera_cfg.get("proof_enabled", False)
        or logging_cfg.get("record_camera", False)
        or logging_cfg.get("record_camera_topics", False)
    )
    camera_image_topic = str(
        camera_cfg.get("image_topic")
        or default_camera_image_topic(world_name, gazebo_model_name)
    )
    camera_probe_timeout_s = float(camera_cfg.get("probe_timeout_s", 15.0))
    camera_render_engine = camera_cfg.get("render_engine")
    if camera_render_engine is not None:
        camera_render_engine = str(camera_render_engine)
    camera_xvfb_enabled = bool(camera_cfg.get("xvfb_enabled", False))
    camera_xvfb_server_args = str(camera_cfg.get("xvfb_server_args", DEFAULT_XVFB_SERVER_ARGS))
    camera_headless_rendering = bool(camera_cfg.get("headless_rendering", camera_proof_enabled and not camera_xvfb_enabled))

    rangefinder_cfg = data.get("rangefinder", {})
    if not isinstance(rangefinder_cfg, dict):
        rangefinder_cfg = {}
    rangefinder_proof_enabled = bool(rangefinder_cfg.get("proof_enabled", False))
    rangefinder_scan_topic = str(
        rangefinder_cfg.get("scan_topic")
        or default_rangefinder_scan_topic(world_name, gazebo_model_name)
    )
    rangefinder_probe_timeout_s = float(rangefinder_cfg.get("probe_timeout_s", 20.0))
    rangefinder_render_engine = rangefinder_cfg.get("render_engine")
    if rangefinder_render_engine is not None:
        rangefinder_render_engine = str(rangefinder_render_engine)
    rangefinder_xvfb_enabled = bool(rangefinder_cfg.get("xvfb_enabled", False))
    rangefinder_min_ulog_rows = int(rangefinder_cfg.get("min_ulog_rows", 50))
    rangefinder_height_tolerance_m = float(rangefinder_cfg.get("height_agreement_tolerance_m", 0.75))

    extra_px4_params = data.get("extra_px4_params", {})
    if extra_px4_params is None:
        extra_px4_params = {}
    if not isinstance(extra_px4_params, dict):
        print("ERROR: extra_px4_params must be a mapping", file=sys.stderr)
        return 2

    flow_recording_cfg = data.get("flow_recording", {})
    if not isinstance(flow_recording_cfg, dict):
        flow_recording_cfg = {}
    flow_recording_enabled = bool(flow_recording_cfg.get("enabled", False))
    flow_recording_rate_hz = float(flow_recording_cfg.get("rate_hz", 10.0))
    flow_recording_max_width = int(flow_recording_cfg.get("max_width", 640))
    flow_recording_min_frames = int(flow_recording_cfg.get("min_frames", 100))

    # Phase 8G live flow bridge (docs/phases/phase_08g_live_flow_bridge.md).
    flow_bridge_cfg = data.get("flow_bridge", {})
    if not isinstance(flow_bridge_cfg, dict):
        flow_bridge_cfg = {}
    flow_bridge_enabled = bool(flow_bridge_cfg.get("enabled", False))
    flow_bridge_estimator = str(flow_bridge_cfg.get("estimator", "sift"))
    flow_bridge_rate_hz = float(flow_bridge_cfg.get("rate_hz", 10.0))
    flow_bridge_max_width = int(flow_bridge_cfg.get("max_width", 480))
    flow_bridge_hfov_rad = float(flow_bridge_cfg.get("hfov_rad", 1.74))
    flow_bridge_axis_map = str(flow_bridge_cfg.get("axis_map", "xy"))
    flow_bridge_gyro_mode = str(flow_bridge_cfg.get("gyro_mode", "nan"))
    flow_bridge_imu_topic = str(
        flow_bridge_cfg.get("imu_topic")
        or default_imu_topic(world_name, gazebo_model_name)
    )
    flow_bridge_quality_in_min = float(flow_bridge_cfg.get("quality_in_min", 20.0))
    flow_bridge_quality_in_max = float(flow_bridge_cfg.get("quality_in_max", 100.0))
    flow_bridge_sift_n_features = int(flow_bridge_cfg.get("sift_n_features", 400))
    flow_bridge_sift_ratio = float(flow_bridge_cfg.get("sift_ratio", 0.75))
    flow_bridge_sift_min_matches = int(flow_bridge_cfg.get("sift_min_matches", 8))
    flow_bridge_lk_max_corners = int(flow_bridge_cfg.get("lk_max_corners", 160))
    flow_bridge_lk_quality_level = float(flow_bridge_cfg.get("lk_quality_level", 0.01))
    flow_bridge_lk_min_distance = float(flow_bridge_cfg.get("lk_min_distance", 6.0))
    flow_bridge_lk_block_size = int(flow_bridge_cfg.get("lk_block_size", 3))
    flow_bridge_lk_win_size = int(flow_bridge_cfg.get("lk_win_size", 21))
    flow_bridge_lk_max_level = int(flow_bridge_cfg.get("lk_max_level", 3))
    flow_bridge_lk_min_tracks = int(flow_bridge_cfg.get("lk_min_tracks", 8))
    flow_bridge_lk_fb_max_error_px = float(flow_bridge_cfg.get("lk_fb_max_error_px", 1.5))
    flow_bridge_lk_confidence_multiplier = float(flow_bridge_cfg.get("lk_confidence_multiplier", 1.5))
    flow_bridge_lk_mad_multiplier = float(flow_bridge_cfg.get("lk_mad_multiplier", 3.0))
    flow_bridge_lk_max_flow_rate_rad_s = float(flow_bridge_cfg.get("lk_max_flow_rate_rad_s", 1.2))
    flow_bridge_send_min_range_m = flow_bridge_cfg.get("send_min_range_m")
    flow_bridge_send_max_range_m = flow_bridge_cfg.get("send_max_range_m")
    flow_bridge_send_min_quality = float(flow_bridge_cfg.get("send_min_quality", 0.0))
    flow_bridge_send_min_matches = int(flow_bridge_cfg.get("send_min_matches", 0))
    flow_bridge_reset_on_unsent = bool(flow_bridge_cfg.get("reset_on_unsent", False))
    flow_bridge_prime_on_unsent = bool(flow_bridge_cfg.get("prime_on_unsent", False))
    flow_bridge_startup_prime_hz = float(flow_bridge_cfg.get("startup_prime_hz", 2.0))
    flow_bridge_startup_prime_duration_s = float(flow_bridge_cfg.get("startup_prime_duration_s", 60.0))
    flow_bridge_prearm_min_mavlink = int(flow_bridge_cfg.get("prearm_min_mavlink_samples", 3))
    flow_bridge_prearm_timeout_s = float(flow_bridge_cfg.get("prearm_timeout_s", 30.0))
    if flow_bridge_send_min_range_m is not None:
        flow_bridge_send_min_range_m = float(flow_bridge_send_min_range_m)
    if flow_bridge_send_max_range_m is not None:
        flow_bridge_send_max_range_m = float(flow_bridge_send_max_range_m)
    flow_bridge_min_sent = int(flow_bridge_cfg.get("min_sent_samples", 100))
    flow_bridge_ekf2_of_ctrl = int(flow_bridge_cfg.get("ekf2_of_ctrl", 0))
    flow_bridge_ekf2_of_qmin = int(flow_bridge_cfg.get("ekf2_of_qmin", 17))
    flow_bridge_ekf2_of_n_min = flow_bridge_cfg.get("ekf2_of_n_min")
    flow_bridge_ekf2_of_n_max = flow_bridge_cfg.get("ekf2_of_n_max")
    flow_bridge_ekf2_of_gate = flow_bridge_cfg.get("ekf2_of_gate")
    flow_bridge_ekf2_of_delay = flow_bridge_cfg.get("ekf2_of_delay")
    if flow_bridge_ekf2_of_n_min is not None:
        flow_bridge_ekf2_of_n_min = float(flow_bridge_ekf2_of_n_min)
    if flow_bridge_ekf2_of_n_max is not None:
        flow_bridge_ekf2_of_n_max = float(flow_bridge_ekf2_of_n_max)
    if flow_bridge_ekf2_of_gate is not None:
        flow_bridge_ekf2_of_gate = float(flow_bridge_ekf2_of_gate)
    if flow_bridge_ekf2_of_delay is not None:
        flow_bridge_ekf2_of_delay = float(flow_bridge_ekf2_of_delay)
    flow_bridge_python = str(
        flow_bridge_cfg.get("python", PROJECT_ROOT / "venv_bridge" / "bin" / "python")
    )

    # Phase 8J stock PX4/Gazebo optical-flow benchmark. This path enables
    # PX4's built-in GZBridge flow subscriber, not the DATABOSS MAVLink bridge.
    stock_flow_cfg = data.get("stock_flow", {})
    if not isinstance(stock_flow_cfg, dict):
        stock_flow_cfg = {}
    stock_flow_enabled = bool(stock_flow_cfg.get("enabled", False))
    stock_flow_ekf2_of_ctrl = int(stock_flow_cfg.get("ekf2_of_ctrl", 1))
    stock_flow_ekf2_of_qmin = int(stock_flow_cfg.get("ekf2_of_qmin", 17))
    stock_flow_sens_flow_rot = int(stock_flow_cfg.get("sens_flow_rot", 0))
    stock_flow_sens_flow_minhgt = float(stock_flow_cfg.get("sens_flow_minhgt", 0.1))
    stock_flow_sens_flow_maxhgt = float(stock_flow_cfg.get("sens_flow_maxhgt", 100.0))
    stock_flow_sens_flow_rate = stock_flow_cfg.get("sens_flow_rate")
    stock_flow_sens_flow_scale = stock_flow_cfg.get("sens_flow_scale")
    stock_flow_ekf2_of_n_min = stock_flow_cfg.get("ekf2_of_n_min")
    stock_flow_ekf2_of_n_max = stock_flow_cfg.get("ekf2_of_n_max")
    stock_flow_ekf2_of_gate = stock_flow_cfg.get("ekf2_of_gate")
    stock_flow_ekf2_of_delay = stock_flow_cfg.get("ekf2_of_delay")
    if stock_flow_sens_flow_rate is not None:
        stock_flow_sens_flow_rate = float(stock_flow_sens_flow_rate)
    if stock_flow_sens_flow_scale is not None:
        stock_flow_sens_flow_scale = float(stock_flow_sens_flow_scale)
    if stock_flow_ekf2_of_n_min is not None:
        stock_flow_ekf2_of_n_min = float(stock_flow_ekf2_of_n_min)
    if stock_flow_ekf2_of_n_max is not None:
        stock_flow_ekf2_of_n_max = float(stock_flow_ekf2_of_n_max)
    if stock_flow_ekf2_of_gate is not None:
        stock_flow_ekf2_of_gate = float(stock_flow_ekf2_of_gate)
    if stock_flow_ekf2_of_delay is not None:
        stock_flow_ekf2_of_delay = float(stock_flow_ekf2_of_delay)

    # gpu_lidar needs the render engine just like the camera.
    sensor_rendering_enabled = camera_proof_enabled or rangefinder_proof_enabled
    sensor_render_engine = camera_render_engine or rangefinder_render_engine
    sensor_xvfb_enabled = (
        (camera_proof_enabled and camera_xvfb_enabled)
        or (rangefinder_proof_enabled and rangefinder_xvfb_enabled)
    )

    visualization_cfg = data.get("visualization", {})
    if not isinstance(visualization_cfg, dict):
        visualization_cfg = {}
    gazebo_web_cfg = visualization_cfg.get("gazebo_web", {})
    if not isinstance(gazebo_web_cfg, dict):
        gazebo_web_cfg = {}
    gazebo_web_enabled = bool(gazebo_web_cfg.get("enabled", False))
    gazebo_web_required = bool(gazebo_web_cfg.get("required", gazebo_web_enabled))
    gazebo_web_port = int(gazebo_web_cfg.get("port", DEFAULT_GAZEBO_WEB_PORT))
    gazebo_web_publication_hz = float(
        gazebo_web_cfg.get("publication_hz", DEFAULT_GAZEBO_WEB_PUBLICATION_HZ)
    )
    gazebo_web_startup_timeout_s = float(gazebo_web_cfg.get("startup_timeout_s", 15.0))
    gazebo_web_host = str(gazebo_web_cfg.get("host", "127.0.0.1"))
    gazebo_web_source_config = resolve_project_path(
        str(gazebo_web_cfg.get("config", DEFAULT_GAZEBO_WEB_CONFIG))
    )

    if standalone_gazebo_enabled and not world_sdf.exists():
        print(f"ERROR: generated world SDF not found: {world_sdf}", file=sys.stderr)
        return 1
    if gazebo_web_enabled:
        if not standalone_gazebo_enabled:
            print(
                "ERROR: visualization.gazebo_web requires a standalone generated world "
                "so the websocket bridge can join the run's GZ_PARTITION.",
                file=sys.stderr,
            )
            return 1
        if not gazebo_web_source_config.exists():
            print(f"ERROR: Gazebo websocket config not found: {gazebo_web_source_config}", file=sys.stderr)
            return 1
        if not (0 < gazebo_web_port < 65536):
            print("ERROR: visualization.gazebo_web.port must be in 1..65535", file=sys.stderr)
            return 1
        if gazebo_web_publication_hz <= 0:
            print("ERROR: visualization.gazebo_web.publication_hz must be positive", file=sys.stderr)
            return 1
        if gazebo_web_startup_timeout_s <= 0:
            print("ERROR: visualization.gazebo_web.startup_timeout_s must be positive", file=sys.stderr)
            return 1
        if tcp_port_open(gazebo_web_host, gazebo_web_port):
            print(
                f"ERROR: visualization.gazebo_web port already in use: "
                f"{gazebo_web_host}:{gazebo_web_port}",
                file=sys.stderr,
            )
            return 1

    route = data.get("route", {})
    takeoff_alt_m = float(route.get("altitude_agl_m", 2.5))

    control = data.get("control", {})
    control_mode = control.get("mode", "auto_takeoff_land")
    local_hold_enabled = control_mode == "offboard_local_position_hold"

    if control_mode not in {"auto_takeoff_land", "offboard_local_position_hold"}:
        print(f"ERROR: unsupported control.mode: {control_mode}", file=sys.stderr)
        return 1

    local_hold_start_after_takeoff_s = float(control.get("start_after_takeoff_s", 5.0))
    local_hold_warmup_s = float(control.get("warmup_s", 2.0))
    local_hold_gnss_loss_after_offboard_s = float(control.get("gnss_loss_after_offboard_s", 3.0))
    sim_time_wall_multiplier = float(control.get("sim_time_wall_multiplier", 30.0))
    local_hold_rate_hz = float(control.get("rate_hz", 20.0))
    local_hold_setpoint_mode = str(control.get("setpoint_mode", "position"))
    local_hold_x_m = float(control.get("x_m", 0.0))
    local_hold_y_m = float(control.get("y_m", 0.0))
    local_hold_z_m = float(control.get("z_m", -takeoff_alt_m))
    local_hold_vx_m_s = float(control.get("vx_m_s", 0.0))
    local_hold_vy_m_s = float(control.get("vy_m_s", 0.0))
    local_hold_vz_m_s = float(control.get("vz_m_s", 0.0))
    local_hold_yaw_deg = float(control.get("yaw_deg", 0.0))
    local_hold_use_yaw = bool(control.get("use_yaw", False))
    skip_landing_command = bool(control.get("skip_landing_command", False))

    aiding = data.get("aiding", {})
    external_odom_enabled = aiding.get("mode") == "synthetic_external_odometry"
    external_odom_rate_hz = float(aiding.get("rate_hz", 30.0))
    external_odom_ev_ctrl = int(aiding.get("ekf2_ev_ctrl", 3))
    external_odom_ekf2_hgt_ref = int(aiding.get("ekf2_hgt_ref", 3))
    external_odom_ev_delay_ms = float(aiding.get("ekf2_ev_delay_ms", 0.0))
    external_odom_mav_frame = str(aiding.get("mav_frame", "local_ned"))
    external_odom_velocity_source = str(aiding.get("velocity_source", "zero"))
    external_odom_velocity_alpha = float(aiding.get("velocity_alpha", 0.35))
    external_odom_max_finite_diff_speed_m_s = float(aiding.get("max_finite_diff_speed_m_s", 5.0))
    external_odom_velocity_reject_action = str(aiding.get("velocity_reject_action", "zero"))
    external_odom_quality = int(aiding.get("quality", 100))
    external_odom_latency_ms = float(aiding.get("latency_ms", 0.0))
    injected_error = aiding.get("injected_error", {})
    if injected_error is None:
        injected_error = {}
    if not isinstance(injected_error, dict):
        print("ERROR: aiding.injected_error must be a mapping", file=sys.stderr)
        return 1
    external_odom_inject_position_noise_std_m = float(injected_error.get("position_noise_std_m", 0.0))
    external_odom_inject_velocity_noise_std_m_s = float(injected_error.get("velocity_noise_std_m_s", 0.0))
    external_odom_disturbance_seed = int(injected_error.get("seed", 0))
    external_odom_dropout = aiding.get("dropout", {})
    if external_odom_dropout is None:
        external_odom_dropout = {}
    if not isinstance(external_odom_dropout, dict):
        print("ERROR: aiding.dropout must be a mapping", file=sys.stderr)
        return 1
    external_odom_dropout_enabled = bool(external_odom_dropout.get("enabled", False))
    external_odom_dropout_start_after_s = float(external_odom_dropout.get("start_after_s", 0.0))
    external_odom_dropout_period_s = float(external_odom_dropout.get("period_s", 0.0))
    external_odom_dropout_duration_s = float(external_odom_dropout.get("duration_s", 0.0))
    external_odom_dropout_probability = float(external_odom_dropout.get("probability", 0.0))
    external_odom_extra_params = aiding.get("ekf2_extra_params", {})
    if external_odom_extra_params is None:
        external_odom_extra_params = {}
    if not isinstance(external_odom_extra_params, dict):
        print("ERROR: aiding.ekf2_extra_params must be a mapping", file=sys.stderr)
        return 1
    for param_name in external_odom_extra_params:
        if not re.fullmatch(r"[A-Z0-9_]+", str(param_name)):
            print(f"ERROR: unsafe EKF2 extra parameter name: {param_name}", file=sys.stderr)
            return 1
    external_odom_velocity_requested = external_odom_enabled and bool(external_odom_ev_ctrl & 4)
    external_odom_velocity_unlocked = bool(aiding.get("allow_experimental_velocity_fusion")) or args.allow_experimental_ev_velocity

    if external_odom_enabled:
        if external_odom_mav_frame not in {"local_ned", "local_enu"}:
            print(
                "ERROR: aiding.mav_frame must be one of: local_ned, local_enu",
                file=sys.stderr,
            )
            return 1
        if external_odom_velocity_source not in {"zero", "finite_difference"}:
            print(
                "ERROR: aiding.velocity_source must be one of: zero, finite_difference",
                file=sys.stderr,
            )
            return 1
        if external_odom_velocity_reject_action not in {"zero", "hold_last"}:
            print(
                "ERROR: aiding.velocity_reject_action must be one of: zero, hold_last",
                file=sys.stderr,
            )
            return 1
        if not 0.0 <= external_odom_velocity_alpha <= 1.0:
            print("ERROR: aiding.velocity_alpha must be between 0.0 and 1.0", file=sys.stderr)
            return 1
        if external_odom_max_finite_diff_speed_m_s <= 0.0:
            print("ERROR: aiding.max_finite_diff_speed_m_s must be positive", file=sys.stderr)
            return 1
        if not 0 <= external_odom_quality <= 100:
            print("ERROR: aiding.quality must be between 0 and 100", file=sys.stderr)
            return 1
        if external_odom_latency_ms < 0.0:
            print("ERROR: aiding.latency_ms must be non-negative", file=sys.stderr)
            return 1
        if external_odom_inject_position_noise_std_m < 0.0:
            print("ERROR: aiding.injected_error.position_noise_std_m must be non-negative", file=sys.stderr)
            return 1
        if external_odom_inject_velocity_noise_std_m_s < 0.0:
            print("ERROR: aiding.injected_error.velocity_noise_std_m_s must be non-negative", file=sys.stderr)
            return 1
        if external_odom_dropout_start_after_s < 0.0 or external_odom_dropout_period_s < 0.0 or external_odom_dropout_duration_s < 0.0:
            print("ERROR: aiding.dropout timing values must be non-negative", file=sys.stderr)
            return 1
        if not 0.0 <= external_odom_dropout_probability <= 1.0:
            print("ERROR: aiding.dropout.probability must be between 0.0 and 1.0", file=sys.stderr)
            return 1

    if local_hold_enabled:
        if local_hold_setpoint_mode not in {"position", "velocity_xy_position_z"}:
            print(
                "ERROR: control.setpoint_mode must be one of: position, velocity_xy_position_z",
                file=sys.stderr,
            )
            return 1
        if local_hold_rate_hz <= 0:
            print("ERROR: control.rate_hz must be positive for offboard local hold", file=sys.stderr)
            return 1
        if (
            local_hold_start_after_takeoff_s < 0
            or local_hold_warmup_s < 0
            or local_hold_gnss_loss_after_offboard_s < 0
            or sim_time_wall_multiplier <= 0
        ):
            print("ERROR: offboard local hold timing values must be non-negative", file=sys.stderr)
            return 1

    if external_odom_velocity_requested and not external_odom_velocity_unlocked:
        print(
            "ERROR: EKF2_EV_CTRL requests external velocity fusion, but this path is parked. "
            "The 20260708 Case D run produced nonphysical finite-difference EV velocities, "
            "gyro clipping, invalid setpoints, and a Gazebo physics abort. Repair/validate "
            "the velocity source first, then rerun with --allow-experimental-ev-velocity.",
            file=sys.stderr,
        )
        return 1

    if external_odom_velocity_requested and external_odom_velocity_source == "zero":
        print(
            "ERROR: EKF2_EV_CTRL requests external velocity fusion, but aiding.velocity_source=zero. "
            "Set a validated velocity source before enabling EV velocity fusion.",
            file=sys.stderr,
        )
        return 1

    external_noise = aiding.get("noise", {})
    external_position_std_m = float(external_noise.get("position_std_m", 0.02))
    external_velocity_std_m_s = float(external_noise.get("velocity_std_m_s", 0.05))

    run_dir = make_run_folder(scenario_path, data, truth_topic)
    console_log = run_dir / "logs" / "px4_gazebo_console.log"
    standalone_gazebo_log = run_dir / "logs" / "gazebo_standalone_console.log"
    gazebo_web_log = run_dir / "logs" / "gazebo_websocket_console.log"
    status_json = run_dir / "logs" / "pxh_takeoff_land_truth_status.json"

    cmd = ["make", "px4_sitl", model]
    notes: list[str] = []
    env = clean_px4_env()

    # The MAVLink sender helper scripts (offboard local-hold setpoints, live
    # external odometry) need pymavlink, which is not guaranteed on
    # sys.executable: this orchestrator is meant to be invoked with the
    # DATABOSS venv active so clean_px4_env() above can isolate *only* the
    # PX4 build/launch subprocess, but if it's invoked with the venv
    # deactivated (e.g. following the pre-PX4-launch venv-stripping ritual
    # a level too aggressively), sys.executable silently loses pymavlink and
    # these senders crash with zero setpoints sent -- PX4 then rejects the
    # OFFBOARD mode switch with no obvious cause (observed 2026-07-21, Phase
    # 14b). Pin to the same known-good interpreter the flow bridge already
    # uses, so sender startup is independent of how this script was invoked.
    mavlink_sender_python = str(PROJECT_ROOT / "venv" / "bin" / "python3")

    start_pose = vehicle.get("start_pose") or {}
    spawn_x_m = float(start_pose.get("x_m", 0.0))
    spawn_y_m = float(start_pose.get("y_m", 0.0))
    spawn_z_m = float(start_pose.get("z_m", 0.0))
    spawn_yaw_rad = math.radians(float(start_pose.get("yaw_deg", 0.0)))
    world_home = world.get("home") or {}

    if standalone_gazebo_enabled:
        add_gazebo_standalone_env(env, world_name, world_sdf)
        if any((spawn_x_m, spawn_y_m, spawn_z_m, spawn_yaw_rad)):
            env["PX4_GZ_MODEL_POSE"] = (
                f"{spawn_x_m:g},{spawn_y_m:g},{spawn_z_m:g},0,0,{spawn_yaw_rad:g}"
            )
            notes.append(f"set PX4_GZ_MODEL_POSE={env['PX4_GZ_MODEL_POSE']} from vehicle.start_pose")
        if world_home:
            env["PX4_HOME_LAT"] = f"{float(world_home['lat_deg']):.9f}"
            env["PX4_HOME_LON"] = f"{float(world_home['lon_deg']):.9f}"
            env["PX4_HOME_ALT"] = f"{float(world_home.get('alt_m', 0.0)):.2f}"
            notes.append(
                "set PX4 home from world.home: "
                f"lat={env['PX4_HOME_LAT']}, lon={env['PX4_HOME_LON']}, alt={env['PX4_HOME_ALT']}"
            )
        gazebo_server_config = write_gazebo_server_config(run_dir, sensor_render_engine, notes)
        env["PX4_GZ_SERVER_CONFIG"] = str(gazebo_server_config)
        env["GZ_SIM_SERVER_CONFIG_PATH"] = str(gazebo_server_config)
    else:
        gazebo_server_config = PX4_GZ_SERVER_CONFIG
    if sensor_rendering_enabled:
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

    aux_process_duration_s = args.hover_s + args.land_timeout_s + 240.0
    if local_hold_enabled:
        local_hold_pre_wait_s = local_hold_start_after_takeoff_s + local_hold_warmup_s
        if args.gnss_loss_after_takeoff_s is not None:
            local_hold_pre_wait_s += local_hold_gnss_loss_after_offboard_s
            local_hold_wait_target_s = args.post_loss_hover_s
            if local_hold_wait_target_s is None:
                local_hold_wait_target_s = max(0.0, args.hover_s - local_hold_pre_wait_s)
        else:
            local_hold_wait_target_s = max(0.0, args.hover_s - local_hold_pre_wait_s)

        local_hold_wall_budget_s = (
            local_hold_pre_wait_s
            + local_hold_wait_target_s * sim_time_wall_multiplier
            + args.land_timeout_s
            + 120.0
        )
        aux_process_duration_s = max(aux_process_duration_s, local_hold_wall_budget_s)
        notes.append(
            "local hold auxiliary duration budget: "
            f"duration_s={aux_process_duration_s:.3f}, "
            f"wait_target_s={local_hold_wait_target_s:.3f}, "
            f"wall_multiplier={sim_time_wall_multiplier:.3f}"
        )

    prelaunch_parameter_store_reset_ok, prelaunch_parameter_backups = reset_px4_parameter_store(run_dir, notes)
    prelaunch_param_overrides = px4_prelaunch_param_overrides(
        external_odom_enabled,
        external_odom_ev_ctrl,
        external_odom_ev_delay_ms,
        args.gnss_start_used,
        flow_bridge_enabled=flow_bridge_enabled,
        stock_flow_enabled=stock_flow_enabled,
    )
    apply_px4_param_env(env, prelaunch_param_overrides, notes)

    start_ts = time.time()
    px4_proc = None
    truth_proc = None
    truth_raw_path = None
    truth_err_path = None
    commands_sent = []
    gnss_loss_command_sent = False
    gnss_loss_verified = False
    gnss_loss_confirm = {"verified": False, "attempts": 0, "fix_type": None, "satellites_used": None}
    local_hold_altitude_wait = {"reached": None, "elapsed_s": None, "final_up_m": None}
    local_hold_pre_offboard_altitude_wait = {"reached": None, "elapsed_s": None, "final_up_m": None}
    qgc_enabled = not args.no_qgc
    qgc_command = None
    qgc_status_command_sent = False
    global_position_gate_enabled = not args.no_global_position_gate
    global_position_ready = False
    global_position_readiness_samples: list[dict] = []
    global_position_ready_sample = None
    airborne_hover_wait_ok = True
    airborne_hover_wait_target_s = None
    airborne_hover_wait_timeout_wall_s = None
    airborne_hover_wait_samples: list[dict] = []
    airborne_hover_wait_sample = None
    local_hold_takeoff_wait_ok = True
    local_hold_takeoff_wait_target_s = None
    local_hold_takeoff_wait_timeout_wall_s = None
    local_hold_takeoff_wait_samples: list[dict] = []
    local_hold_takeoff_wait_sample = None

    external_odom_proc = None
    external_odom_log_handle = None
    external_odom_started = False
    external_odom_command = None
    standalone_gazebo_proc = None
    standalone_gazebo_log_handle = None
    standalone_gazebo_ready = not standalone_gazebo_enabled
    gazebo_web_proc = None
    flow_recording_proc = None
    flow_recording_log_handle = None
    flow_recording_dir = run_dir / "flow_recording"
    flow_bridge_proc = None
    flow_bridge_log_handle = None
    flow_bridge_dir = run_dir / "flow_bridge"
    flow_bridge_sent_path = flow_bridge_dir / "flow_bridge_sent.csv"
    flow_bridge_started = False
    flow_bridge_prearm_result = {
        "flow_bridge_prearm_required": False,
        "flow_bridge_prearm_min_mavlink_rows": flow_bridge_prearm_min_mavlink,
        "flow_bridge_prearm_timeout_s": flow_bridge_prearm_timeout_s,
        "flow_bridge_prearm_mavlink_rows": 0,
        "flow_bridge_prearm_ok": True,
        "flow_bridge_prearm_process_alive": False,
    }
    flow_recording_command = None
    gazebo_web_log_handle = None
    gazebo_web_config = None
    gazebo_web_command = None
    gazebo_web_started = False
    gazebo_web_ready = not gazebo_web_enabled
    camera_probe_result = {
        "camera_topic": camera_image_topic,
        "camera_probe_ok": not camera_proof_enabled,
        "camera_topic_seen": False,
        "camera_image_sample_bytes": 0,
    }
    rangefinder_probe_result = {
        "rangefinder_scan_topic": rangefinder_scan_topic,
        "rangefinder_probe_ok": not rangefinder_proof_enabled,
        "rangefinder_topic_seen": False,
        "rangefinder_scan_sample_bytes": 0,
        "rangefinder_sample_range_m": None,
    }
    external_odom_sent_path = run_dir / "logs" / "external_odometry_sent.csv"
    external_odom_console_path = run_dir / "logs" / "external_odometry_bridge.log"
    local_hold_proc = None
    local_hold_log_handle = None
    local_hold_started = False
    local_hold_command = None
    local_hold_mode_command_sent = False
    local_hold_vehicle_status_text = None
    local_hold_mode_samples: list[dict] = []
    local_hold_mode_sample = None
    local_hold_nav_state_after_mode = None
    local_hold_accepts_offboard_setpoints = None
    local_hold_sent_path = run_dir / "logs" / "offboard_local_hold_sent.csv"
    local_hold_console_path = run_dir / "logs" / "offboard_local_hold.log"

    print(f"Run dir: {run_dir}")
    print(f"PX4 root: {PX4_ROOT}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Gazebo model instance: {gazebo_model_name}")
    if standalone_gazebo_enabled:
        print(f"Standalone Gazebo world: {world_name}")
        print(f"Standalone Gazebo SDF: {world_sdf}")
    if camera_proof_enabled:
        print(f"Camera proof topic: {camera_image_topic}")
        print(f"Camera render engine: {camera_render_engine or 'default'}")
        print(f"Camera Xvfb enabled: {camera_xvfb_enabled}")
    if rangefinder_proof_enabled:
        print(f"Rangefinder proof topic: {rangefinder_scan_topic}")
        print(f"Rangefinder render engine: {sensor_render_engine or 'default'}")
        print(f"Rangefinder Xvfb enabled: {rangefinder_xvfb_enabled}")
    if gazebo_web_enabled:
        print(f"Gazebo web bridge enabled: {gazebo_web_enabled}")
        print(f"Gazebo web bridge URL: ws://127.0.0.1:{gazebo_web_port}")
        print(f"Gazebo web publication Hz: {gazebo_web_publication_hz:g}")
    print(f"Console log: {console_log}")

    with console_log.open("w") as log:
        log.write("# PX4/Gazebo console log\n")
        log.write(f"# started_utc: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        log.write(f"# cwd: {PX4_ROOT}\n")
        log.write(f"# cmd: {' '.join(cmd)}\n\n")
        log.flush()

        if standalone_gazebo_enabled:
            standalone_gazebo_log_handle = standalone_gazebo_log.open("w")
            standalone_cmd = ["gz", "sim", "-r", "-s", "-v", "2"]
            if camera_proof_enabled and camera_headless_rendering:
                standalone_cmd.append("--headless-rendering")
            standalone_cmd.append(str(world_sdf))
            standalone_cmd = gazebo_command_with_display(
                standalone_cmd,
                use_xvfb=sensor_xvfb_enabled,
                xvfb_server_args=camera_xvfb_server_args,
            )
            standalone_gazebo_proc = subprocess.Popen(
                standalone_cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=standalone_gazebo_log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
                bufsize=1,
            )
            notes.append(
                "started standalone gazebo "
                f"pid={standalone_gazebo_proc.pid}, world={world_name}, sdf={world_sdf}"
            )
            standalone_gazebo_ready = wait_for_standalone_world(
                world_name,
                standalone_gazebo_proc,
                env,
                timeout_s=min(45.0, args.startup_timeout_s),
                notes=notes,
            )

        if gazebo_web_enabled and standalone_gazebo_ready:
            gazebo_web_config = write_gazebo_websocket_config(
                run_dir,
                gazebo_web_source_config,
                gazebo_web_port,
                gazebo_web_publication_hz,
                notes,
            )
            gazebo_web_command = ["gz", "launch", "-v", "4", str(gazebo_web_config)]
            gazebo_web_log_handle = gazebo_web_log.open("w")
            gazebo_web_proc = subprocess.Popen(
                gazebo_web_command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=gazebo_web_log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
                bufsize=1,
            )
            gazebo_web_started = gazebo_web_proc.poll() is None
            notes.append(
                "started Gazebo websocket bridge "
                f"pid={gazebo_web_proc.pid}, port={gazebo_web_port}, "
                f"partition={env.get('GZ_PARTITION')}"
            )
            gazebo_web_ready = wait_for_tcp_port(
                gazebo_web_host,
                gazebo_web_port,
                gazebo_web_proc,
                gazebo_web_startup_timeout_s,
                notes,
            )
        elif gazebo_web_enabled:
            notes.append("Gazebo websocket bridge skipped because standalone Gazebo was not ready")

        if standalone_gazebo_ready:
            px4_proc = subprocess.Popen(
                cmd,
                cwd=str(PX4_ROOT),
                env=env,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
                bufsize=1,
            )
        else:
            notes.append("PX4 launch skipped because standalone Gazebo world was not ready")

        if px4_proc is not None:
            notes.append(f"started px4 process pid={px4_proc.pid}")

            startup_pattern = wait_for_pattern(console_log, px4_proc, STARTUP_PATTERNS, args.startup_timeout_s)
            notes.append(f"startup_pattern={startup_pattern}")

            flight_ready_pattern = wait_for_pattern(console_log, px4_proc, FLIGHT_READY_PATTERNS, 30.0)
            notes.append(f"flight_ready_pattern={flight_ready_pattern}")

        if px4_proc is not None and startup_pattern:
            truth_proc, truth_raw_path, truth_err_path = start_truth_recorder(
                run_dir,
                notes,
                truth_topic,
                env,
            )
            time.sleep(5)

            if flow_recording_enabled:
                flow_recording_dir = run_dir / "flow_recording"
                flow_recording_dir.mkdir(parents=True, exist_ok=True)
                flow_recording_duration_s = aux_process_duration_s
                flow_recording_command = [
                    "/usr/bin/python3",
                    str(PROJECT_ROOT / "scripts" / "sim" / "record_camera_frames.py"),
                    "--image-topic", camera_image_topic,
                    "--scan-topic", rangefinder_scan_topic,
                    "--out-dir", str(flow_recording_dir),
                    "--rate-hz", str(flow_recording_rate_hz),
                    "--max-width", str(flow_recording_max_width),
                    "--duration-s", str(flow_recording_duration_s),
                ]
                flow_recording_log_handle = (run_dir / "logs" / "flow_recording.log").open("w")
                flow_recording_proc = subprocess.Popen(
                    flow_recording_command,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    stdout=flow_recording_log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid,
                )
                notes.append(f"started flow recorder pid={flow_recording_proc.pid}")

            qgc_enabled = not args.no_qgc
            qgc_command = None

            if qgc_enabled:
                qgc_command = (
                    f"mavlink start -m config "
                    f"-u {args.qgc_local_port} "
                    f"-o {args.qgc_remote_port} "
                    f"-t {args.qgc_ip} "
                    f"-r {args.qgc_rate} "
                    f"-x"
                )
                ok = send_pxh(px4_proc, qgc_command, notes)
                commands_sent.append({"command": qgc_command, "sent": ok})
                time.sleep(5)
                ok = send_pxh(px4_proc, "mavlink status", notes)
                commands_sent.append({"command": "mavlink status", "sent": ok})
                qgc_status_command_sent = ok
                time.sleep(3)

            failsafe_commands = failsafe_profile_commands(args.failsafe_profile)
            for fs_cmd in failsafe_commands:
                ok = send_pxh(px4_proc, fs_cmd, notes)
                commands_sent.append({"command": fs_cmd, "sent": ok})
                time.sleep(1)

            start_gnss_cmd = f"param set SIM_GPS_USED {args.gnss_start_used}"
            ok = send_pxh(px4_proc, start_gnss_cmd, notes)
            commands_sent.append({"command": start_gnss_cmd, "sent": ok})
            time.sleep(2)

            takeoff_alt_cmd = f"param set MIS_TAKEOFF_ALT {takeoff_alt_m}"
            ok = send_pxh(px4_proc, takeoff_alt_cmd, notes)
            commands_sent.append({"command": takeoff_alt_cmd, "sent": ok})
            time.sleep(1)

            # Apply scenario-level extra PX4 params for every run (not only
            # flow-bridge runs), before takeoff so estimator/height settings
            # like EKF2_RNG_A_HMAX are active for the whole flight. Used by
            # the Phase 14 altitude batches to let the downward rangefinder
            # anchor absolute height above the 5 m stock EKF2_RNG_A_HMAX
            # cutoff on flat terrain.
            for param_name, param_value in sorted(extra_px4_params.items()):
                extra_cmd = f"param set {param_name} {param_value}"
                ok = send_pxh(px4_proc, extra_cmd, notes)
                commands_sent.append({"command": extra_cmd, "sent": ok})
                time.sleep(1)

            project_root = Path(__file__).resolve().parents[2]
            onboard_mavlink_needed = external_odom_enabled or local_hold_enabled or flow_bridge_enabled

            if onboard_mavlink_needed:
                onboard_commands = [
                    "mavlink start -m onboard -u 14600 -o 14601 -t 127.0.0.1 -r 1000000",
                ]

                if local_hold_enabled:
                    onboard_commands.append("param set MAV_FWDEXTSP 1")

                for onboard_cmd in onboard_commands:
                    ok = send_pxh(px4_proc, onboard_cmd, notes)
                    commands_sent.append({"command": onboard_cmd, "sent": ok})
                    time.sleep(1)

            if external_odom_enabled:
                ev_commands = [
                    f"param set EKF2_EV_CTRL {external_odom_ev_ctrl}",
                    f"param set EKF2_HGT_REF {external_odom_ekf2_hgt_ref}",
                    f"param set EKF2_EV_DELAY {external_odom_ev_delay_ms:g}",
                ]
                for param_name, param_value in sorted(external_odom_extra_params.items()):
                    ev_commands.append(f"param set {param_name} {param_value}")

                for ev_cmd in ev_commands:
                    ok = send_pxh(px4_proc, ev_cmd, notes)
                    commands_sent.append({"command": ev_cmd, "sent": ok})
                    time.sleep(1)

                bridge_duration_s = args.hover_s + args.land_timeout_s + 90.0

                external_odom_command = [
                    mavlink_sender_python,
                    "scripts/runner/send_live_gazebo_odometry_mavlink.py",
                    "--topic",
                    truth_topic,
                    "--model-name",
                    gazebo_model_name,
                    "--connection",
                    "udpout:127.0.0.1:14600",
                    "--duration-s",
                    str(bridge_duration_s),
                    "--rate-hz",
                    str(external_odom_rate_hz),
                    "--position-std-m",
                    str(external_position_std_m),
                    "--velocity-std-m-s",
                    str(external_velocity_std_m_s),
                    "--mav-frame",
                    external_odom_mav_frame,
                    "--velocity-source",
                    external_odom_velocity_source,
                    "--velocity-alpha",
                    str(external_odom_velocity_alpha),
                    "--max-finite-diff-speed-m-s",
                    str(external_odom_max_finite_diff_speed_m_s),
                    "--velocity-reject-action",
                    external_odom_velocity_reject_action,
                    "--quality",
                    str(external_odom_quality),
                    "--latency-ms",
                    str(external_odom_latency_ms),
                    "--inject-position-noise-std-m",
                    str(external_odom_inject_position_noise_std_m),
                    "--inject-velocity-noise-std-m-s",
                    str(external_odom_inject_velocity_noise_std_m_s),
                    "--disturbance-seed",
                    str(external_odom_disturbance_seed),
                    "--dropout-start-after-s",
                    str(external_odom_dropout_start_after_s),
                    "--dropout-period-s",
                    str(external_odom_dropout_period_s),
                    "--dropout-duration-s",
                    str(external_odom_dropout_duration_s),
                    "--dropout-probability",
                    str(external_odom_dropout_probability),
                    "--sent-log",
                    str(external_odom_sent_path),
                ]
                if external_odom_dropout_enabled:
                    external_odom_command.append("--dropout-enabled")

                external_odom_log_handle = external_odom_console_path.open("w")
                external_odom_proc = subprocess.Popen(
                    external_odom_command,
                    cwd=str(project_root),
                    env=env,
                    stdout=external_odom_log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid,
                )

                notes.append(
                    f"started external odometry bridge pid={external_odom_proc.pid}"
                )

                time.sleep(5)
                external_odom_started = external_odom_proc.poll() is None
                notes.append(f"external_odom_started={external_odom_started}")

                if not external_odom_started:
                    notes.append(
                        "external odometry failed before arming; GNSS loss suppressed"
                    )
                    args.gnss_loss_after_takeoff_s = None

            if flow_bridge_enabled:
                # EKF2_OF_CTRL=0 keeps this open-loop (compute + deliver to
                # uORB, EKF ignores); SENS_FLOW_ROT stays 0 because the axis
                # map is owned by the bridge adapter (decision D5).
                flow_param_commands = [
                    f"param set EKF2_OF_CTRL {flow_bridge_ekf2_of_ctrl}",
                    f"param set EKF2_OF_QMIN {flow_bridge_ekf2_of_qmin}",
                    "param set SENS_FLOW_ROT 0",
                    "param set SENS_FLOW_MINHGT 0.1",
                    "param set SENS_FLOW_MAXHGT 100",
                ]
                if flow_bridge_ekf2_of_n_min is not None:
                    flow_param_commands.append(f"param set EKF2_OF_N_MIN {flow_bridge_ekf2_of_n_min}")
                if flow_bridge_ekf2_of_n_max is not None:
                    flow_param_commands.append(f"param set EKF2_OF_N_MAX {flow_bridge_ekf2_of_n_max}")
                if flow_bridge_ekf2_of_gate is not None:
                    flow_param_commands.append(f"param set EKF2_OF_GATE {flow_bridge_ekf2_of_gate}")
                if flow_bridge_ekf2_of_delay is not None:
                    flow_param_commands.append(f"param set EKF2_OF_DELAY {flow_bridge_ekf2_of_delay}")
                for flow_cmd in flow_param_commands:
                    ok = send_pxh(px4_proc, flow_cmd, notes)
                    commands_sent.append({"command": flow_cmd, "sent": ok})
                    time.sleep(1)

                # extra_px4_params are now applied universally right after
                # MIS_TAKEOFF_ALT (before takeoff), not only for flow-bridge
                # runs -- see the setup block above.

                flow_bridge_dir.mkdir(parents=True, exist_ok=True)
                flow_bridge_duration_s = aux_process_duration_s
                flow_bridge_command = [
                    flow_bridge_python,
                    str(PROJECT_ROOT / "scripts" / "sim" / "flow_mavlink_bridge.py"),
                    "--image-topic", camera_image_topic,
                    "--scan-topic", rangefinder_scan_topic,
                    "--imu-topic", flow_bridge_imu_topic,
                    "--estimator", flow_bridge_estimator,
                    "--hfov-rad", str(flow_bridge_hfov_rad),
                    "--rate-hz", str(flow_bridge_rate_hz),
                    "--max-width", str(flow_bridge_max_width),
                    "--sift-n-features", str(flow_bridge_sift_n_features),
                    "--sift-ratio", str(flow_bridge_sift_ratio),
                    "--sift-min-matches", str(flow_bridge_sift_min_matches),
                    "--lk-max-corners", str(flow_bridge_lk_max_corners),
                    "--lk-quality-level", str(flow_bridge_lk_quality_level),
                    "--lk-min-distance", str(flow_bridge_lk_min_distance),
                    "--lk-block-size", str(flow_bridge_lk_block_size),
                    "--lk-win-size", str(flow_bridge_lk_win_size),
                    "--lk-max-level", str(flow_bridge_lk_max_level),
                    "--lk-min-tracks", str(flow_bridge_lk_min_tracks),
                    "--lk-fb-max-error-px", str(flow_bridge_lk_fb_max_error_px),
                    "--lk-confidence-multiplier", str(flow_bridge_lk_confidence_multiplier),
                    "--lk-mad-multiplier", str(flow_bridge_lk_mad_multiplier),
                    "--lk-max-flow-rate-rad-s", str(flow_bridge_lk_max_flow_rate_rad_s),
                    # =VALUE form: axis maps like "-yx" start with a dash and
                    # argparse would otherwise read them as an option, not a value.
                    f"--axis-map={flow_bridge_axis_map}",
                    "--gyro-mode", flow_bridge_gyro_mode,
                    "--quality-in-min", str(flow_bridge_quality_in_min),
                    "--quality-in-max", str(flow_bridge_quality_in_max),
                    "--send-min-quality", str(flow_bridge_send_min_quality),
                    "--send-min-matches", str(flow_bridge_send_min_matches),
                    "--connection", "udpout:127.0.0.1:14600",
                    "--duration-s", str(flow_bridge_duration_s),
                    "--sent-log", str(flow_bridge_sent_path),
                    "--startup-prime-hz", str(flow_bridge_startup_prime_hz),
                    "--startup-prime-duration-s", str(flow_bridge_startup_prime_duration_s),
                ]
                if flow_bridge_send_min_range_m is not None:
                    flow_bridge_command.extend(["--send-min-range-m", str(flow_bridge_send_min_range_m)])
                if flow_bridge_send_max_range_m is not None:
                    flow_bridge_command.extend(["--send-max-range-m", str(flow_bridge_send_max_range_m)])
                if flow_bridge_reset_on_unsent:
                    flow_bridge_command.append("--reset-on-unsent")
                if flow_bridge_prime_on_unsent:
                    flow_bridge_command.append("--prime-on-unsent")
                flow_bridge_log_handle = (run_dir / "logs" / "flow_bridge.log").open("w")
                flow_bridge_proc = subprocess.Popen(
                    flow_bridge_command,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    stdout=flow_bridge_log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid,
                )
                notes.append(f"started flow bridge pid={flow_bridge_proc.pid}")
                flow_bridge_prearm_result = wait_for_flow_bridge_prearm(
                    flow_bridge_proc,
                    flow_bridge_sent_path,
                    flow_bridge_prearm_min_mavlink,
                    flow_bridge_prearm_timeout_s,
                    notes,
                )
                flow_bridge_started = flow_bridge_proc.poll() is None
                notes.append(f"flow_bridge_started={flow_bridge_started}")
                if (
                    flow_bridge_prearm_min_mavlink > 0
                    and not flow_bridge_prearm_result["flow_bridge_prearm_ok"]
                ):
                    raise RuntimeError(
                        "flow bridge did not publish required pre-arm MAVLink samples "
                        f"({flow_bridge_prearm_result['flow_bridge_prearm_mavlink_rows']}/"
                        f"{flow_bridge_prearm_min_mavlink}) within "
                        f"{flow_bridge_prearm_timeout_s:g}s"
                    )

            if stock_flow_enabled:
                stock_flow_param_commands = [
                    "param set SYS_HAS_GPS 1",
                    "param set EKF2_GPS_CTRL 7",
                    f"param set SIM_GZ_EN_FLOW {int(stock_flow_cfg.get('sim_gz_en_flow', 1))}",
                    f"param set SIM_GZ_EN_LIDAR {int(stock_flow_cfg.get('sim_gz_en_lidar', 1))}",
                    f"param set EKF2_OF_CTRL {stock_flow_ekf2_of_ctrl}",
                    f"param set EKF2_OF_QMIN {stock_flow_ekf2_of_qmin}",
                    f"param set SENS_FLOW_ROT {stock_flow_sens_flow_rot}",
                    f"param set SENS_FLOW_MINHGT {stock_flow_sens_flow_minhgt}",
                    f"param set SENS_FLOW_MAXHGT {stock_flow_sens_flow_maxhgt}",
                ]
                if stock_flow_sens_flow_rate is not None:
                    stock_flow_param_commands.append(f"param set SENS_FLOW_RATE {stock_flow_sens_flow_rate}")
                if stock_flow_sens_flow_scale is not None:
                    stock_flow_param_commands.append(f"param set SENS_FLOW_SCALE {stock_flow_sens_flow_scale}")
                if stock_flow_ekf2_of_n_min is not None:
                    stock_flow_param_commands.append(f"param set EKF2_OF_N_MIN {stock_flow_ekf2_of_n_min}")
                if stock_flow_ekf2_of_n_max is not None:
                    stock_flow_param_commands.append(f"param set EKF2_OF_N_MAX {stock_flow_ekf2_of_n_max}")
                if stock_flow_ekf2_of_gate is not None:
                    stock_flow_param_commands.append(f"param set EKF2_OF_GATE {stock_flow_ekf2_of_gate}")
                if stock_flow_ekf2_of_delay is not None:
                    stock_flow_param_commands.append(f"param set EKF2_OF_DELAY {stock_flow_ekf2_of_delay}")
                for stock_cmd in stock_flow_param_commands:
                    ok = send_pxh(px4_proc, stock_cmd, notes)
                    commands_sent.append({"command": stock_cmd, "sent": ok})
                    time.sleep(1)

            if global_position_gate_enabled:
                global_position_ready, global_position_readiness_samples, global_position_ready_sample = (
                    wait_for_global_position_ready(
                        px4_proc,
                        console_log,
                        notes,
                        timeout_s=args.global_position_timeout_s,
                        stable_s=args.global_position_stable_s,
                    )
                )
            else:
                global_position_ready = True
                notes.append("global position readiness gate disabled")

            if global_position_ready:
                ok = send_pxh(px4_proc, "commander arm -f", notes)
                commands_sent.append({"command": "commander arm -f", "sent": ok})
                time.sleep(5)

                ok = send_pxh(px4_proc, "commander takeoff", notes)
                commands_sent.append({"command": "commander takeoff", "sent": ok})
                takeoff_command_wall = time.monotonic()

                if local_hold_enabled:
                    local_hold_takeoff_wait_target_s = max(0.0, local_hold_start_after_takeoff_s)
                    local_hold_takeoff_wait_timeout_wall_s = max(
                        local_hold_takeoff_wait_target_s * sim_time_wall_multiplier + 30.0,
                        local_hold_takeoff_wait_target_s + 30.0,
                    )
                    notes.append(
                        "local hold pre-offboard takeoff sim-time wait: "
                        f"target_s={local_hold_takeoff_wait_target_s:.3f}, "
                        f"timeout_wall_s={local_hold_takeoff_wait_timeout_wall_s:.3f}"
                    )
                    (
                        local_hold_takeoff_wait_ok,
                        local_hold_takeoff_wait_samples,
                        local_hold_takeoff_wait_sample,
                    ) = wait_for_airborne_duration(
                        px4_proc,
                        console_log,
                        notes,
                        target_airborne_s=local_hold_takeoff_wait_target_s,
                        timeout_wall_s=local_hold_takeoff_wait_timeout_wall_s,
                    )
                    if not local_hold_takeoff_wait_ok:
                        airborne_hover_wait_ok = False
                        raise RuntimeError(
                            "takeoff did not reach the requested pre-offboard airborne duration; "
                            "aborting before offboard/GNSS-loss commands"
                        )

                    local_hold_post_loss_hover_s = args.post_loss_hover_s
                    local_hold_pre_loss_s = (
                        local_hold_start_after_takeoff_s
                        + local_hold_warmup_s
                        + local_hold_gnss_loss_after_offboard_s
                    )

                    if local_hold_post_loss_hover_s is None:
                        if args.gnss_loss_after_takeoff_s is not None:
                            local_hold_post_loss_hover_s = max(0.0, args.hover_s - local_hold_pre_loss_s)
                        else:
                            local_hold_post_loss_hover_s = max(0.0, args.hover_s - local_hold_start_after_takeoff_s)

                    local_hold_duration_s = aux_process_duration_s

                    local_hold_command = [
                        mavlink_sender_python,
                        "scripts/runner/send_offboard_local_position_setpoint_mavlink.py",
                        "--connection",
                        "udpout:127.0.0.1:14600",
                        "--duration-s",
                        str(local_hold_duration_s),
                        "--rate-hz",
                        str(local_hold_rate_hz),
                        "--setpoint-mode",
                        local_hold_setpoint_mode,
                        "--x",
                        str(local_hold_x_m),
                        "--y",
                        str(local_hold_y_m),
                        "--z",
                        str(local_hold_z_m),
                        "--vx",
                        str(local_hold_vx_m_s),
                        "--vy",
                        str(local_hold_vy_m_s),
                        "--vz",
                        str(local_hold_vz_m_s),
                        "--yaw-deg",
                        str(local_hold_yaw_deg),
                        "--sent-log",
                        str(local_hold_sent_path),
                    ]
                    if local_hold_use_yaw:
                        local_hold_command.append("--use-yaw")

                    local_hold_log_handle = local_hold_console_path.open("w")
                    local_hold_proc = subprocess.Popen(
                        local_hold_command,
                        cwd=str(project_root),
                        stdout=local_hold_log_handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                        preexec_fn=os.setsid,
                    )
                    notes.append(f"started offboard local hold sender pid={local_hold_proc.pid}")

                    time.sleep(local_hold_warmup_s)
                    local_hold_started = local_hold_proc.poll() is None
                    notes.append(f"local_hold_started={local_hold_started}")

                    if local_hold_started:
                        # A fixed post-takeoff wait (local_hold_start_after_takeoff_s)
                        # is altitude-blind: PX4's AUTO_TAKEOFF climbs at
                        # MPC_TKO_SPEED (default 1.5 m/s), so a 5 s wait tuned for a
                        # 15 m target (~10 s climb) leaves PX4 still mid-climb at
                        # 35/60 m and it rejects the OFFBOARD mode switch (observed
                        # 2026-07-21: nav_state stayed 17/AUTO_TAKEOFF instead of
                        # 14/OFFBOARD on the first Phase 14b 35 m attempt). Reuse the
                        # same altitude gate already proven for the GNSS-loss cut so
                        # OFFBOARD is only requested once takeoff has actually
                        # finished, at any altitude, with no per-batch tuning.
                        #
                        # wait_for_target_altitude's timeout_s is WALL-clock, and unlike
                        # the GNSS-loss-cut call site (where OFFBOARD has usually already
                        # been holding near the target for a while, so the gap to close is
                        # small), the vehicle here still has the *entire* AUTO_TAKEOFF
                        # climb left. Budget wall-clock for a full climb at MPC_TKO_SPEED
                        # (default 1.5 m/s) plus settle time, scaled by the scenario's own
                        # sim_time_wall_multiplier -- the same convention every other
                        # wall-clock budget in this function already uses.
                        pre_offboard_altitude_timeout_wall_s = max(
                            60.0, (takeoff_alt_m / 1.5 + 10.0) * sim_time_wall_multiplier
                        )
                        local_hold_pre_offboard_altitude_wait = wait_for_target_altitude(
                            px4_proc,
                            console_log,
                            takeoff_alt_m,
                            notes,
                            timeout_s=pre_offboard_altitude_timeout_wall_s,
                        )
                        notes.append(f"pre-offboard altitude gate: {local_hold_pre_offboard_altitude_wait}")

                        ok = send_pxh(px4_proc, "commander mode offboard", notes)
                        commands_sent.append({"command": "commander mode offboard", "sent": ok})
                        local_hold_mode_command_sent = ok
                        local_hold_offboard_command_wall = time.monotonic()

                        (
                            local_hold_mode_detected_runtime,
                            local_hold_mode_samples,
                            local_hold_mode_sample,
                            local_hold_vehicle_status_text,
                        ) = wait_for_offboard_mode(
                            px4_proc,
                            console_log,
                            notes,
                            timeout_s=min(5.0, max(0.5, local_hold_gnss_loss_after_offboard_s)),
                        )
                        if local_hold_mode_sample:
                            local_hold_nav_state_after_mode = local_hold_mode_sample["nav_state"]
                            local_hold_accepts_offboard_setpoints = local_hold_mode_sample[
                                "accepts_offboard_setpoints"
                            ]
                        notes.append(
                            "local hold mode status: "
                            f"nav_state={local_hold_nav_state_after_mode}, "
                            f"accepts_offboard_setpoints={local_hold_accepts_offboard_setpoints}, "
                            f"detected={local_hold_mode_detected_runtime}"
                        )

                        if args.gnss_loss_after_takeoff_s is not None:
                            # Cut GPS only once the vehicle is actually stable at
                            # the commanded hold altitude, not after a fixed time
                            # (gnss_loss_after_offboard_s is now just a minimum
                            # floor). Keeps GNSS loss at hover across 2.5/15/35/60 m
                            # with no per-altitude timing tuning.
                            elapsed_after_offboard_s = time.monotonic() - local_hold_offboard_command_wall
                            local_hold_altitude_wait = wait_for_target_altitude(
                                px4_proc,
                                console_log,
                                abs(local_hold_z_m),
                                notes,
                                min_wait_s=max(0.0, local_hold_gnss_loss_after_offboard_s - elapsed_after_offboard_s),
                            )
                            notes.append(f"pre-loss altitude gate: {local_hold_altitude_wait}")

                            ok = send_pxh(px4_proc, "param set SIM_GPS_USED 0", notes)
                            commands_sent.append({"command": "param set SIM_GPS_USED 0", "sent": ok})
                            gnss_loss_command_sent = ok
                            gnss_loss_confirm = confirm_gnss_loss(px4_proc, console_log, notes)
                            gnss_loss_verified = gnss_loss_confirm["verified"]

                            airborne_hover_wait_target_s = max(0.0, local_hold_post_loss_hover_s)
                            airborne_hover_wait_timeout_wall_s = max(
                                airborne_hover_wait_target_s * sim_time_wall_multiplier,
                                airborne_hover_wait_target_s
                                + camera_probe_timeout_s
                                + rangefinder_probe_timeout_s
                                + args.land_timeout_s
                                + 90.0,
                            )
                            notes.append(
                                "local hold post-loss sim-time wait: "
                                f"target_s={airborne_hover_wait_target_s:.3f}, "
                                f"timeout_wall_s={airborne_hover_wait_timeout_wall_s:.3f}"
                            )
                            (
                                airborne_hover_wait_ok,
                                airborne_hover_wait_samples,
                                airborne_hover_wait_sample,
                            ) = wait_for_airborne_duration(
                                px4_proc,
                                console_log,
                                notes,
                                target_airborne_s=airborne_hover_wait_target_s,
                                timeout_wall_s=airborne_hover_wait_timeout_wall_s,
                            )
                        else:
                            airborne_hover_wait_target_s = max(
                                0.0,
                                args.hover_s - local_hold_start_after_takeoff_s - local_hold_warmup_s,
                            )
                            airborne_hover_wait_timeout_wall_s = max(
                                airborne_hover_wait_target_s * sim_time_wall_multiplier,
                                airborne_hover_wait_target_s
                                + camera_probe_timeout_s
                                + rangefinder_probe_timeout_s
                                + args.land_timeout_s
                                + 90.0,
                            )
                            notes.append(
                                "local hold sim-time wait: "
                                f"target_s={airborne_hover_wait_target_s:.3f}, "
                                f"timeout_wall_s={airborne_hover_wait_timeout_wall_s:.3f}"
                            )
                            (
                                airborne_hover_wait_ok,
                                airborne_hover_wait_samples,
                                airborne_hover_wait_sample,
                            ) = wait_for_airborne_duration(
                                px4_proc,
                                console_log,
                                notes,
                                target_airborne_s=airborne_hover_wait_target_s,
                                timeout_wall_s=airborne_hover_wait_timeout_wall_s,
                            )
                    else:
                        notes.append("offboard local hold sender failed before GNSS loss; continuing with GNSS enabled")
                        remaining_hover_s = max(0.0, args.hover_s - local_hold_start_after_takeoff_s)
                        time.sleep(remaining_hover_s)
                elif args.gnss_loss_after_takeoff_s is not None:
                    time.sleep(args.gnss_loss_after_takeoff_s)

                    ok = send_pxh(px4_proc, "param set SIM_GPS_USED 0", notes)
                    commands_sent.append({"command": "param set SIM_GPS_USED 0", "sent": ok})
                    gnss_loss_command_sent = ok
                    gnss_loss_confirm = confirm_gnss_loss(px4_proc, console_log, notes)
                    gnss_loss_verified = gnss_loss_confirm["verified"]

                    post_loss_hover_s = args.post_loss_hover_s
                    if post_loss_hover_s is None:
                        post_loss_hover_s = max(0.0, args.hover_s - args.gnss_loss_after_takeoff_s)

                    time.sleep(post_loss_hover_s)
                else:
                    elapsed_since_takeoff_cmd_s = time.monotonic() - takeoff_command_wall
                    airborne_hover_wait_target_s = max(10.0, args.hover_s * 0.8)
                    airborne_hover_wait_timeout_wall_s = max(
                        airborne_hover_wait_target_s * sim_time_wall_multiplier,
                        args.hover_s + camera_probe_timeout_s + rangefinder_probe_timeout_s + args.land_timeout_s + 90.0,
                    )
                    notes.append(
                        "auto hover timing: "
                        f"requested_s={args.hover_s:.3f}, "
                        f"wait_target_s={airborne_hover_wait_target_s:.3f}, "
                        f"elapsed_after_takeoff_cmd_s={elapsed_since_takeoff_cmd_s:.3f}, "
                        f"timeout_wall_s={airborne_hover_wait_timeout_wall_s:.3f}"
                    )
                    (
                        airborne_hover_wait_ok,
                        airborne_hover_wait_samples,
                        airborne_hover_wait_sample,
                    ) = wait_for_airborne_duration(
                        px4_proc,
                        console_log,
                        notes,
                        target_airborne_s=airborne_hover_wait_target_s,
                        timeout_wall_s=airborne_hover_wait_timeout_wall_s,
                    )

                if camera_proof_enabled and not skip_landing_command:
                    camera_probe_result = probe_camera_topic(
                        run_dir,
                        env,
                        camera_image_topic,
                        timeout_s=camera_probe_timeout_s,
                        notes=notes,
                    )
                elif camera_proof_enabled and skip_landing_command:
                    camera_probe_result = {
                        **camera_probe_result,
                        "camera_probe_ok": True,
                        "camera_topic_seen": True,
                        "camera_image_sample_bytes": None,
                    }
                    notes.append("skipped post-window camera probe per control.skip_landing_command")

                if rangefinder_proof_enabled and not skip_landing_command:
                    rangefinder_probe_result = probe_rangefinder_topic(
                        run_dir,
                        env,
                        rangefinder_scan_topic,
                        timeout_s=rangefinder_probe_timeout_s,
                        notes=notes,
                    )
                elif rangefinder_proof_enabled and skip_landing_command:
                    rangefinder_probe_result = {
                        **rangefinder_probe_result,
                        "rangefinder_probe_ok": True,
                        "rangefinder_topic_seen": True,
                        "rangefinder_scan_sample_bytes": None,
                    }
                    notes.append("skipped post-window rangefinder probe per control.skip_landing_command")

                if skip_landing_command:
                    notes.append("skipped commander land per control.skip_landing_command")
                else:
                    ok = send_pxh(px4_proc, "commander land", notes)
                    commands_sent.append({"command": "commander land", "sent": ok})
                    observation_landing_not_required = (
                        args.failsafe_profile == "delayed_observation"
                        and args.gnss_loss_after_takeoff_s is not None
                    )
                    landing_wait_sim_budget_s = (
                        min(10.0, args.land_timeout_s)
                        if observation_landing_not_required
                        else args.land_timeout_s
                    )
                    # land_timeout_s is a sim-time budget; the sim can run far
                    # below real time, so scale the wall timeout like the
                    # takeoff/hover waits do.
                    landing_wait_timeout_s = max(
                        landing_wait_sim_budget_s,
                        landing_wait_sim_budget_s * sim_time_wall_multiplier,
                    )
                    notes.append(
                        "landing wait budget: "
                        f"sim_budget_s={landing_wait_sim_budget_s:.3f}, "
                        f"wall_multiplier={sim_time_wall_multiplier:.3f}, "
                        f"timeout_wall_s={landing_wait_timeout_s:.3f}"
                    )
                    wait_for_landing_complete(
                        px4_proc,
                        console_log,
                        notes,
                        timeout_wall_s=landing_wait_timeout_s,
                    )
            else:
                notes.append("global position not ready; arm/takeoff/land commands skipped")
        else:
            notes.append("startup not detected, no flight commands sent")

        if flow_recording_proc is not None:
            stop_process_group(flow_recording_proc, notes)

        if flow_recording_log_handle is not None:
            flow_recording_log_handle.close()

        if flow_bridge_proc is not None:
            stop_process_group(flow_bridge_proc, notes)

        if flow_bridge_log_handle is not None:
            flow_bridge_log_handle.close()

        if local_hold_proc is not None:
            stop_process_group(local_hold_proc, notes)

        if local_hold_log_handle is not None:
            local_hold_log_handle.close()

        if external_odom_proc is not None:
            stop_process_group(external_odom_proc, notes)

        if external_odom_log_handle is not None:
            external_odom_log_handle.close()

        if truth_proc is not None:
            close_truth_recorder(truth_proc, notes)

        if px4_proc is not None:
            stop_process_group(px4_proc, notes)

        if gazebo_web_proc is not None:
            stop_process_group(gazebo_web_proc, notes)

        if gazebo_web_log_handle is not None:
            gazebo_web_log_handle.close()

        if standalone_gazebo_proc is not None:
            stop_process_group(standalone_gazebo_proc, notes)

        if standalone_gazebo_log_handle is not None:
            standalone_gazebo_log_handle.close()

    elapsed_s = time.time() - start_ts

    ulog_source = newest_ulog(start_ts)
    ulog_copied = False
    copied_ulog = None

    if ulog_source is not None:
        copied_ulog = run_dir / "logs" / "flight.ulg"
        shutil.copy2(ulog_source, copied_ulog)
        ulog_copied = True
        notes.append(f"copied ulog: {ulog_source} -> {copied_ulog}")
    else:
        notes.append("no new ulog found to copy")

    effective_gnss_loss_after_takeoff_s = args.gnss_loss_after_takeoff_s

    if local_hold_enabled and args.gnss_loss_after_takeoff_s is not None:
        effective_gnss_loss_after_takeoff_s = (
            local_hold_start_after_takeoff_s
            + local_hold_warmup_s
            + local_hold_gnss_loss_after_offboard_s
        )

    requested_airborne_s = args.hover_s
    if args.gnss_loss_after_takeoff_s is not None:
        post_loss_hover_s = args.post_loss_hover_s
        if post_loss_hover_s is None:
            post_loss_hover_s = max(0.0, args.hover_s - (effective_gnss_loss_after_takeoff_s or 0.0))
        if skip_landing_command:
            requested_airborne_s = post_loss_hover_s
        else:
            requested_airborne_s = (effective_gnss_loss_after_takeoff_s or 0.0) + post_loss_hover_s

    if copied_ulog is not None:
        flight_analysis = analyze_ulog_flight(copied_ulog, takeoff_alt_m, requested_airborne_s)
        notes.append(f"ulog_flight_analysis={flight_analysis}")
        external_odom_fusion_analysis = analyze_ulog_external_odometry(
            copied_ulog,
            require_position=external_odom_enabled and bool(external_odom_ev_ctrl & 1),
            require_height=external_odom_enabled and bool(external_odom_ev_ctrl & 2),
            require_velocity=external_odom_enabled and bool(external_odom_ev_ctrl & 4),
        )
        notes.append(f"ulog_external_odom_analysis={external_odom_fusion_analysis}")
        rangefinder_ulog_analysis = analyze_ulog_distance_sensor(
            copied_ulog,
            required=rangefinder_proof_enabled,
            min_rows=rangefinder_min_ulog_rows,
            height_agreement_tolerance_m=rangefinder_height_tolerance_m,
        )
        notes.append(f"ulog_distance_sensor_analysis={rangefinder_ulog_analysis}")
    else:
        rangefinder_ulog_analysis = {
            "ulog_distance_sensor_analysis_ok": False,
            "ulog_distance_sensor_required": rangefinder_proof_enabled,
            "ulog_distance_sensor_rows": 0,
            "ulog_distance_sensor_max_m": None,
            "ulog_distance_sensor_median_m": None,
            "ulog_distance_sensor_height_diff_m": None,
            "ulog_distance_sensor_ok": not rangefinder_proof_enabled,
            "ulog_distance_sensor_error": "no copied ULog",
        }
        flight_analysis = {
            "ulog_flight_analysis_ok": False,
            "ulog_airborne_duration_s": None,
            "ulog_height_above_0p5_duration_s": None,
            "ulog_max_height_up_m": None,
            "ulog_reached_min_height": False,
            "ulog_airborne_duration_ok": False,
            "ulog_flight_ok": False,
            "ulog_flight_error": "no copied ULog",
        }
        external_odom_fusion_analysis = {
            "ulog_external_odom_analysis_ok": False,
            "ulog_external_odom_required_position": external_odom_enabled and bool(external_odom_ev_ctrl & 1),
            "ulog_external_odom_required_height": external_odom_enabled and bool(external_odom_ev_ctrl & 2),
            "ulog_external_odom_required_velocity": external_odom_enabled and bool(external_odom_ev_ctrl & 4),
            "ulog_vehicle_visual_odometry_rows": 0,
            "ulog_ev_pos_aid_rows": 0,
            "ulog_ev_hgt_aid_rows": 0,
            "ulog_ev_vel_aid_rows": 0,
            "ulog_ev_pos_active_count": 0,
            "ulog_ev_hgt_active_count": 0,
            "ulog_ev_vel_active_count": 0,
            "ulog_ev_pos_fused_count": 0,
            "ulog_ev_hgt_fused_count": 0,
            "ulog_ev_vel_fused_count": 0,
            "ulog_ev_pos_rejected_count": 0,
            "ulog_ev_hgt_rejected_count": 0,
            "ulog_ev_vel_rejected_count": 0,
            "ulog_xy_reset_counter_start": None,
            "ulog_xy_reset_counter_end": None,
            "ulog_xy_reset_counter_delta": None,
            "ulog_external_odom_fusion_ok": not external_odom_enabled,
            "ulog_external_odom_error": "no copied ULog",
        }

    full_log_text = console_log.read_text(errors="ignore")
    qgc_mavlink_started = (not qgc_enabled) or (("mode: Config" in full_log_text) and (f"udp port {args.qgc_local_port}" in full_log_text))
    qgc_status = parse_qgc_status(full_log_text, args.qgc_ip)

    if not qgc_enabled:
        qgc_status = {
            **qgc_status,
            "qgc_status_checked": False,
            "qgc_gcs_heartbeat_seen": False,
            "qgc_partner_ip_seen": False,
            "qgc_rx_sysid_255_seen": False,
            "qgc_rx_messages_seen": False,
            "qgc_dropped_packets_zero_seen": False,
            "qgc_connected": True,
        }

    gnss_loss_requested = args.gnss_loss_after_takeoff_s is not None
    expected_failsafe_commands = failsafe_profile_commands(args.failsafe_profile)
    failsafe_profile_ok = all(cmd in full_log_text for cmd in expected_failsafe_commands)
    gnss_loss_detected = "param set SIM_GPS_USED 0" in full_log_text
    # A GNSS-loss run is only OK if the simulated GPS was verified to have
    # actually dropped in-flight (confirm_gnss_loss), not merely that the
    # command was typed -- otherwise the '115202-class' flake (command sent,
    # GPS never dropped) silently yields an invalid GNSS-on masquerade.
    gnss_loss_ok = (not gnss_loss_requested) or (gnss_loss_detected and gnss_loss_verified)
    arming_denied = "Arming denied" in full_log_text
    armed_detected = ("Armed by" in full_log_text) or ("armed by" in full_log_text)
    takeoff_detected = "Takeoff detected" in full_log_text
    landing_detected = "Landing detected" in full_log_text
    disarmed_by_landing = "Disarmed by landing" in full_log_text

    truth_raw_exists = bool(truth_raw_path and truth_raw_path.exists())
    truth_raw_bytes = truth_raw_path.stat().st_size if truth_raw_exists else 0
    truth_recorded = truth_raw_bytes > 0

    flow_recording_frames = count_csv_data_rows(flow_recording_dir / "frames_index.csv")
    flow_recording_ranges = count_csv_data_rows(flow_recording_dir / "rangefinder.csv")
    flow_recording_ok = (not flow_recording_enabled) or (
        flow_recording_frames >= flow_recording_min_frames and flow_recording_ranges > 0
    )

    flow_bridge_total_rows = count_csv_data_rows(flow_bridge_sent_path)
    flow_bridge_sent_rows = count_csv_sent_rows(flow_bridge_sent_path)
    flow_bridge_ok = (not flow_bridge_enabled) or (
        flow_bridge_started and flow_bridge_sent_rows >= flow_bridge_min_sent
    )

    external_odom_sent_exists = external_odom_sent_path.exists()
    external_odom_sent_rows = count_csv_data_rows(external_odom_sent_path)

    external_odom_ok = (not external_odom_enabled) or (external_odom_started and external_odom_sent_rows > 10)

    local_hold_sent_exists = local_hold_sent_path.exists()
    local_hold_sent_rows = count_csv_data_rows(local_hold_sent_path)
    local_hold_mode_detected = (
        not local_hold_enabled
        or local_hold_nav_state_after_mode == 14
        or local_hold_accepts_offboard_setpoints is True
    )
    local_hold_ok = (
        not local_hold_enabled
        or (
            local_hold_started
            and local_hold_mode_command_sent
            and local_hold_mode_detected
            and local_hold_sent_rows > 10
        )
    )

    observation_mode = args.failsafe_profile == "delayed_observation" and gnss_loss_requested
    landing_required = not observation_mode and not skip_landing_command
    landing_ok = (landing_detected and disarmed_by_landing) if landing_required else True
    gazebo_web_ok = (not gazebo_web_required) or gazebo_web_ready

    accepted = (
        bool(startup_pattern)
        and prelaunch_parameter_store_reset_ok
        and gazebo_web_ok
        and ulog_copied
        and qgc_mavlink_started
        and global_position_ready
        and failsafe_profile_ok
        and gnss_loss_ok
        and truth_recorded
        and flight_analysis["ulog_flight_ok"]
        and external_odom_ok
        and local_hold_takeoff_wait_ok
        and local_hold_ok
        and camera_probe_result["camera_probe_ok"]
        and rangefinder_probe_result["rangefinder_probe_ok"]
        and rangefinder_ulog_analysis["ulog_distance_sensor_ok"]
        and flow_recording_ok
        and flow_bridge_ok
        and external_odom_fusion_analysis["ulog_external_odom_fusion_ok"]
        and (not arming_denied)
        and armed_detected
        and takeoff_detected
        and landing_ok
    )

    status = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "scenario": str(scenario_path),
        "px4_root": str(PX4_ROOT),
        "cmd": cmd,
        "model": model,
        "gazebo_model_name": gazebo_model_name,
        "world_name": world_name,
        "world_sdf": str(world_sdf) if world_sdf else None,
        "standalone_gazebo_enabled": standalone_gazebo_enabled,
        "standalone_gazebo_ready": standalone_gazebo_ready,
        "standalone_gazebo_log": str(standalone_gazebo_log) if standalone_gazebo_enabled else None,
        "gazebo_server_config": str(gazebo_server_config),
        "gz_partition": env.get("GZ_PARTITION"),
        "gazebo_web_enabled": gazebo_web_enabled,
        "gazebo_web_required": gazebo_web_required,
        "gazebo_web_ok": gazebo_web_ok,
        "gazebo_web_started": gazebo_web_started,
        "gazebo_web_ready": gazebo_web_ready,
        "gazebo_web_host": gazebo_web_host,
        "gazebo_web_port": gazebo_web_port,
        "gazebo_web_url": f"ws://127.0.0.1:{gazebo_web_port}",
        "gazebo_web_publication_hz": gazebo_web_publication_hz,
        "gazebo_web_startup_timeout_s": gazebo_web_startup_timeout_s,
        "gazebo_web_source_config": str(gazebo_web_source_config),
        "gazebo_web_config": str(gazebo_web_config) if gazebo_web_config else None,
        "gazebo_web_log": str(gazebo_web_log) if gazebo_web_enabled else None,
        "gazebo_web_command": gazebo_web_command,
        "px4_home_lat": env.get("PX4_HOME_LAT"),
        "px4_home_lon": env.get("PX4_HOME_LON"),
        "px4_home_alt": env.get("PX4_HOME_ALT"),
        "prelaunch_parameter_store_reset_ok": prelaunch_parameter_store_reset_ok,
        "prelaunch_parameter_backups": prelaunch_parameter_backups,
        "prelaunch_param_overrides": prelaunch_param_overrides,
        "startup_pattern": startup_pattern,
        "flight_ready_pattern": flight_ready_pattern,
        "commands_sent": commands_sent,
        "qgc_enabled": qgc_enabled,
        "qgc_ip": args.qgc_ip,
        "qgc_local_port": args.qgc_local_port,
        "qgc_remote_port": args.qgc_remote_port,
        "qgc_rate": args.qgc_rate,
        "qgc_command": qgc_command,
        "qgc_status_command_sent": qgc_status_command_sent,
        "qgc_mavlink_started": qgc_mavlink_started,
        **qgc_status,
        "global_position_gate_enabled": global_position_gate_enabled,
        "global_position_timeout_s": args.global_position_timeout_s,
        "global_position_stable_s": args.global_position_stable_s,
        "global_position_ready": global_position_ready,
        "global_position_ready_sample": global_position_ready_sample,
        "global_position_readiness_samples": global_position_readiness_samples,
        "airborne_hover_wait_ok": airborne_hover_wait_ok,
        "airborne_hover_wait_target_s": airborne_hover_wait_target_s,
        "airborne_hover_wait_timeout_wall_s": airborne_hover_wait_timeout_wall_s,
        "airborne_hover_wait_sample": airborne_hover_wait_sample,
        "airborne_hover_wait_samples": airborne_hover_wait_samples,
        "local_hold_takeoff_wait_ok": local_hold_takeoff_wait_ok,
        "local_hold_takeoff_wait_target_s": local_hold_takeoff_wait_target_s,
        "local_hold_takeoff_wait_timeout_wall_s": local_hold_takeoff_wait_timeout_wall_s,
        "local_hold_takeoff_wait_sample": local_hold_takeoff_wait_sample,
        "local_hold_takeoff_wait_samples": local_hold_takeoff_wait_samples,
        "control_mode": control_mode,
        "failsafe_profile": args.failsafe_profile,
        "failsafe_profile_source": failsafe_profile_source,
        "failsafe_profile_commands": expected_failsafe_commands,
        "failsafe_profile_ok": failsafe_profile_ok,
        "observation_mode": observation_mode,
        "landing_required": landing_required,
        "landing_ok": landing_ok,
        "gnss_start_used": args.gnss_start_used,
        "gnss_loss_requested": gnss_loss_requested,
        "gnss_loss_source": gnss_loss_source,
        "gnss_loss_after_takeoff_s": args.gnss_loss_after_takeoff_s,
        "effective_gnss_loss_after_takeoff_s": effective_gnss_loss_after_takeoff_s,
        "post_loss_hover_s": args.post_loss_hover_s,
        "gnss_loss_command_sent": gnss_loss_command_sent,
        "gnss_loss_detected": gnss_loss_detected,
        "gnss_loss_verified": gnss_loss_verified,
        "gnss_loss_confirm_attempts": gnss_loss_confirm.get("attempts"),
        "gnss_loss_observed_fix_type": gnss_loss_confirm.get("fix_type"),
        "gnss_loss_observed_satellites_used": gnss_loss_confirm.get("satellites_used"),
        "pre_loss_altitude_reached": local_hold_altitude_wait.get("reached"),
        "pre_loss_altitude_final_up_m": local_hold_altitude_wait.get("final_up_m"),
        "pre_offboard_altitude_reached": local_hold_pre_offboard_altitude_wait.get("reached"),
        "pre_offboard_altitude_final_up_m": local_hold_pre_offboard_altitude_wait.get("final_up_m"),
        "gnss_loss_ok": gnss_loss_ok,
        "truth_topic": truth_topic,
        "truth_raw_path": str(truth_raw_path) if truth_raw_path else None,
        "truth_err_path": str(truth_err_path) if truth_err_path else None,
        "truth_raw_bytes": truth_raw_bytes,
        "truth_recorded": truth_recorded,
        "camera_proof_enabled": camera_proof_enabled,
        "camera_image_topic": camera_image_topic,
        "camera_probe_timeout_s": camera_probe_timeout_s,
        "camera_render_engine": camera_render_engine,
        "camera_xvfb_enabled": camera_xvfb_enabled,
        "camera_xvfb_server_args": camera_xvfb_server_args,
        "camera_headless_rendering": camera_headless_rendering,
        **camera_probe_result,
        "rangefinder_proof_enabled": rangefinder_proof_enabled,
        "rangefinder_probe_timeout_s": rangefinder_probe_timeout_s,
        "rangefinder_render_engine": rangefinder_render_engine,
        "rangefinder_xvfb_enabled": rangefinder_xvfb_enabled,
        "rangefinder_min_ulog_rows": rangefinder_min_ulog_rows,
        "rangefinder_height_agreement_tolerance_m": rangefinder_height_tolerance_m,
        "sensor_render_engine": sensor_render_engine,
        **rangefinder_probe_result,
        **rangefinder_ulog_analysis,
        "flow_recording_enabled": flow_recording_enabled,
        "flow_recording_rate_hz": flow_recording_rate_hz,
        "flow_recording_max_width": flow_recording_max_width,
        "flow_recording_min_frames": flow_recording_min_frames,
        "flow_recording_frames": flow_recording_frames,
        "flow_recording_ranges": flow_recording_ranges,
        "flow_recording_dir": str(flow_recording_dir),
        "flow_recording_command": flow_recording_command,
        "flow_bridge_enabled": flow_bridge_enabled,
        "flow_bridge_estimator": flow_bridge_estimator,
        "flow_bridge_rate_hz": flow_bridge_rate_hz,
        "flow_bridge_max_width": flow_bridge_max_width,
        "flow_bridge_axis_map": flow_bridge_axis_map,
        "flow_bridge_gyro_mode": flow_bridge_gyro_mode,
        "flow_bridge_imu_topic": flow_bridge_imu_topic,
        "flow_bridge_quality_in_min": flow_bridge_quality_in_min,
        "flow_bridge_quality_in_max": flow_bridge_quality_in_max,
        "flow_bridge_sift_n_features": flow_bridge_sift_n_features,
        "flow_bridge_sift_ratio": flow_bridge_sift_ratio,
        "flow_bridge_sift_min_matches": flow_bridge_sift_min_matches,
        "flow_bridge_lk_max_corners": flow_bridge_lk_max_corners,
        "flow_bridge_lk_quality_level": flow_bridge_lk_quality_level,
        "flow_bridge_lk_min_distance": flow_bridge_lk_min_distance,
        "flow_bridge_lk_block_size": flow_bridge_lk_block_size,
        "flow_bridge_lk_win_size": flow_bridge_lk_win_size,
        "flow_bridge_lk_max_level": flow_bridge_lk_max_level,
        "flow_bridge_lk_min_tracks": flow_bridge_lk_min_tracks,
        "flow_bridge_lk_fb_max_error_px": flow_bridge_lk_fb_max_error_px,
        "flow_bridge_lk_confidence_multiplier": flow_bridge_lk_confidence_multiplier,
        "flow_bridge_lk_mad_multiplier": flow_bridge_lk_mad_multiplier,
        "flow_bridge_lk_max_flow_rate_rad_s": flow_bridge_lk_max_flow_rate_rad_s,
        "flow_bridge_send_min_range_m": flow_bridge_send_min_range_m,
        "flow_bridge_send_max_range_m": flow_bridge_send_max_range_m,
        "flow_bridge_send_min_quality": flow_bridge_send_min_quality,
        "flow_bridge_send_min_matches": flow_bridge_send_min_matches,
        "flow_bridge_reset_on_unsent": flow_bridge_reset_on_unsent,
        "flow_bridge_prime_on_unsent": flow_bridge_prime_on_unsent,
        "flow_bridge_startup_prime_hz": flow_bridge_startup_prime_hz,
        "flow_bridge_startup_prime_duration_s": flow_bridge_startup_prime_duration_s,
        **flow_bridge_prearm_result,
        "flow_bridge_ekf2_of_ctrl": flow_bridge_ekf2_of_ctrl,
        "flow_bridge_ekf2_of_qmin": flow_bridge_ekf2_of_qmin,
        "flow_bridge_ekf2_of_n_min": flow_bridge_ekf2_of_n_min,
        "flow_bridge_ekf2_of_n_max": flow_bridge_ekf2_of_n_max,
        "flow_bridge_ekf2_of_gate": flow_bridge_ekf2_of_gate,
        "flow_bridge_ekf2_of_delay": flow_bridge_ekf2_of_delay,
        "flow_bridge_started": flow_bridge_started,
        "flow_bridge_total_rows": flow_bridge_total_rows,
        "flow_bridge_sent_rows": flow_bridge_sent_rows,
        "flow_bridge_min_sent": flow_bridge_min_sent,
        "flow_bridge_ok": flow_bridge_ok,
        "flow_bridge_dir": str(flow_bridge_dir),
        "flow_recording_ok": flow_recording_ok,
        "stock_flow_enabled": stock_flow_enabled,
        "stock_flow_ekf2_of_ctrl": stock_flow_ekf2_of_ctrl,
        "stock_flow_ekf2_of_qmin": stock_flow_ekf2_of_qmin,
        "stock_flow_sens_flow_rot": stock_flow_sens_flow_rot,
        "stock_flow_sens_flow_minhgt": stock_flow_sens_flow_minhgt,
        "stock_flow_sens_flow_maxhgt": stock_flow_sens_flow_maxhgt,
        "stock_flow_sens_flow_rate": stock_flow_sens_flow_rate,
        "stock_flow_sens_flow_scale": stock_flow_sens_flow_scale,
        "stock_flow_ekf2_of_n_min": stock_flow_ekf2_of_n_min,
        "stock_flow_ekf2_of_n_max": stock_flow_ekf2_of_n_max,
        "stock_flow_ekf2_of_gate": stock_flow_ekf2_of_gate,
        "stock_flow_ekf2_of_delay": stock_flow_ekf2_of_delay,
        "external_odom_enabled": external_odom_enabled,
        "external_odom_started": external_odom_started,
        "external_odom_ok": external_odom_ok,
        "external_odom_rate_hz": external_odom_rate_hz,
        "external_odom_ev_ctrl": external_odom_ev_ctrl,
        "external_odom_ekf2_hgt_ref": external_odom_ekf2_hgt_ref,
        "external_odom_ev_delay_ms": external_odom_ev_delay_ms,
        "external_odom_latency_ms": external_odom_latency_ms,
        "external_odom_mav_frame": external_odom_mav_frame,
        "external_odom_position_std_m": external_position_std_m,
        "external_odom_velocity_std_m_s": external_velocity_std_m_s,
        "external_odom_inject_position_noise_std_m": external_odom_inject_position_noise_std_m,
        "external_odom_inject_velocity_noise_std_m_s": external_odom_inject_velocity_noise_std_m_s,
        "external_odom_disturbance_seed": external_odom_disturbance_seed,
        "external_odom_dropout_enabled": external_odom_dropout_enabled,
        "external_odom_dropout_start_after_s": external_odom_dropout_start_after_s,
        "external_odom_dropout_period_s": external_odom_dropout_period_s,
        "external_odom_dropout_duration_s": external_odom_dropout_duration_s,
        "external_odom_dropout_probability": external_odom_dropout_probability,
        "external_odom_velocity_source": external_odom_velocity_source,
        "external_odom_velocity_alpha": external_odom_velocity_alpha,
        "external_odom_max_finite_diff_speed_m_s": external_odom_max_finite_diff_speed_m_s,
        "external_odom_velocity_reject_action": external_odom_velocity_reject_action,
        "external_odom_quality": external_odom_quality,
        "external_odom_extra_params": external_odom_extra_params,
        "external_odom_command": external_odom_command,
        "external_odom_sent_path": str(external_odom_sent_path),
        "external_odom_console_path": str(external_odom_console_path),
        "external_odom_sent_rows": external_odom_sent_rows,
        "local_hold_enabled": local_hold_enabled,
        "local_hold_started": local_hold_started,
        "local_hold_ok": local_hold_ok,
        "local_hold_start_after_takeoff_s": local_hold_start_after_takeoff_s,
        "local_hold_warmup_s": local_hold_warmup_s,
        "local_hold_gnss_loss_after_offboard_s": local_hold_gnss_loss_after_offboard_s,
        "local_hold_rate_hz": local_hold_rate_hz,
        "local_hold_setpoint_mode": local_hold_setpoint_mode,
        "local_hold_x_m": local_hold_x_m,
        "local_hold_y_m": local_hold_y_m,
        "local_hold_z_m": local_hold_z_m,
        "local_hold_vx_m_s": local_hold_vx_m_s,
        "local_hold_vy_m_s": local_hold_vy_m_s,
        "local_hold_vz_m_s": local_hold_vz_m_s,
        "local_hold_yaw_deg": local_hold_yaw_deg,
        "local_hold_use_yaw": local_hold_use_yaw,
        "skip_landing_command": skip_landing_command,
        "local_hold_mode_detected": local_hold_mode_detected,
        "local_hold_mode_sample": local_hold_mode_sample,
        "local_hold_mode_samples": local_hold_mode_samples,
        "local_hold_mode_command_sent": local_hold_mode_command_sent,
        "local_hold_nav_state_after_mode": local_hold_nav_state_after_mode,
        "local_hold_accepts_offboard_setpoints": local_hold_accepts_offboard_setpoints,
        "local_hold_vehicle_status_text": local_hold_vehicle_status_text,
        "local_hold_command": local_hold_command,
        "local_hold_sent_path": str(local_hold_sent_path),
        "local_hold_console_path": str(local_hold_console_path),
        "local_hold_sent_exists": local_hold_sent_exists,
        "local_hold_sent_rows": local_hold_sent_rows,
        "arming_denied": arming_denied,
        "armed_detected": armed_detected,
        "takeoff_detected": takeoff_detected,
        "landing_detected": landing_detected,
        "disarmed_by_landing": disarmed_by_landing,
        "ulog_source": str(ulog_source) if ulog_source else None,
        "ulog_copied": ulog_copied,
        "copied_ulog": str(copied_ulog) if copied_ulog else None,
        "requested_airborne_s": requested_airborne_s,
        **flight_analysis,
        **external_odom_fusion_analysis,
        "elapsed_s": elapsed_s,
        "returncode": px4_proc.returncode if px4_proc else None,
        "accepted": accepted,
        "notes": notes,
        "host": platform.node(),
    }

    status_json.write_text(json.dumps(status, indent=2))

    validation = [
        "# Validation",
        "",
        "## Phase 7A.4 PX4 shell automated takeoff/land with Gazebo truth",
        "",
        f"- Scenario: `{scenario_path}`",
        f"- Run folder: `{run_dir}`",
        f"- PX4 model: `{model}`",
        f"- Gazebo model instance: `{gazebo_model_name}`",
        f"- World name: `{world_name}`",
        f"- World SDF: `{world_sdf}`",
        f"- Standalone Gazebo enabled: `{standalone_gazebo_enabled}`",
        f"- Standalone Gazebo ready: `{standalone_gazebo_ready}`",
        f"- Gazebo server config: `{gazebo_server_config}`",
        f"- Gazebo partition: `{env.get('GZ_PARTITION')}`",
        f"- Gazebo web bridge enabled: `{gazebo_web_enabled}`",
        f"- Gazebo web bridge required: `{gazebo_web_required}`",
        f"- Gazebo web bridge ready: `{gazebo_web_ready}`",
        f"- Gazebo web bridge URL: `ws://127.0.0.1:{gazebo_web_port}`",
        f"- Gazebo web publication Hz: `{gazebo_web_publication_hz}`",
        f"- Gazebo web launch config: `{gazebo_web_config}`",
        f"- Gazebo web log: `{gazebo_web_log if gazebo_web_enabled else None}`",
        f"- PX4 home origin: `lat={env.get('PX4_HOME_LAT')}, lon={env.get('PX4_HOME_LON')}, alt={env.get('PX4_HOME_ALT')}`",
        f"- Startup detected: `{bool(startup_pattern)}`",
        f"- Startup pattern: `{startup_pattern}`",
        f"- Flight-ready pattern: `{flight_ready_pattern}`",
        f"- PX4 parameter store reset OK: `{prelaunch_parameter_store_reset_ok}`",
        f"- PX4 prelaunch parameter overrides: `{prelaunch_param_overrides}`",
        f"- Gazebo truth topic: `{truth_topic}`",
        f"- Gazebo truth recorded: `{truth_recorded}`",
        f"- Gazebo truth raw bytes: `{truth_raw_bytes}`",
        f"- Camera proof enabled: `{camera_proof_enabled}`",
        f"- Camera image topic: `{camera_image_topic}`",
        f"- Camera render engine: `{camera_render_engine}`",
        f"- Camera Xvfb enabled: `{camera_xvfb_enabled}`",
        f"- Camera Xvfb server args: `{camera_xvfb_server_args}`",
        f"- Camera headless rendering flag: `{camera_headless_rendering}`",
        f"- Camera topic seen: `{camera_probe_result['camera_topic_seen']}`",
        f"- Camera image sample bytes: `{camera_probe_result['camera_image_sample_bytes']}`",
        f"- Camera probe OK: `{camera_probe_result['camera_probe_ok']}`",
        f"- Rangefinder proof enabled: `{rangefinder_proof_enabled}`",
        f"- Rangefinder scan topic: `{rangefinder_scan_topic}`",
        f"- Rangefinder topic seen: `{rangefinder_probe_result['rangefinder_topic_seen']}`",
        f"- Rangefinder sample range m: `{rangefinder_probe_result['rangefinder_sample_range_m']}`",
        f"- Rangefinder probe OK: `{rangefinder_probe_result['rangefinder_probe_ok']}`",
        f"- ULog distance_sensor rows: `{rangefinder_ulog_analysis['ulog_distance_sensor_rows']}`",
        f"- ULog distance_sensor max m: `{rangefinder_ulog_analysis['ulog_distance_sensor_max_m']}`",
        f"- ULog distance_sensor vs height diff m: `{rangefinder_ulog_analysis['ulog_distance_sensor_height_diff_m']}`",
        f"- ULog distance_sensor OK: `{rangefinder_ulog_analysis['ulog_distance_sensor_ok']}`",
        f"- Flow recording enabled: `{flow_recording_enabled}`",
        f"- Flow recording frames: `{flow_recording_frames}`",
        f"- Flow recording range samples: `{flow_recording_ranges}`",
        f"- Flow recording OK: `{flow_recording_ok}`",
        f"- QGC MAVLink enabled: `{qgc_enabled}`",
        f"- QGC target IP: `{args.qgc_ip}`",
        f"- QGC MAVLink started: `{qgc_mavlink_started}`",
        f"- QGC status checked: `{qgc_status['qgc_status_checked']}`",
        f"- QGC connected: `{qgc_status['qgc_connected']}`",
        f"- QGC GCS heartbeat seen: `{qgc_status['qgc_gcs_heartbeat_seen']}`",
        f"- QGC partner IP seen: `{qgc_status['qgc_partner_ip_seen']}`",
        f"- QGC RX sysid 255 seen: `{qgc_status['qgc_rx_sysid_255_seen']}`",
        f"- QGC RX messages seen: `{qgc_status['qgc_rx_messages_seen']}`",
        f"- QGC dropped packets zero seen: `{qgc_status['qgc_dropped_packets_zero_seen']}`",
        f"- Global position gate enabled: `{global_position_gate_enabled}`",
        f"- Global position ready: `{global_position_ready}`",
        f"- Global position ready sample: `{global_position_ready_sample}`",
        f"- Airborne hover wait OK: `{airborne_hover_wait_ok}`",
        f"- Airborne hover wait target s: `{airborne_hover_wait_target_s}`",
        f"- Airborne hover wait final sample: `{airborne_hover_wait_sample}`",
        f"- Local hold takeoff wait OK: `{local_hold_takeoff_wait_ok}`",
        f"- Local hold takeoff wait target s: `{local_hold_takeoff_wait_target_s}`",
        f"- Local hold takeoff wait final sample: `{local_hold_takeoff_wait_sample}`",
        f"- Control mode: `{control_mode}`",
        f"- Offboard local hold enabled: `{local_hold_enabled}`",
        f"- Offboard local hold started: `{local_hold_started}`",
        f"- Offboard local hold OK: `{local_hold_ok}`",
        f"- Offboard local hold sent rows: `{local_hold_sent_rows}`",
        f"- Offboard local hold mode detected: `{local_hold_mode_detected}`",
        f"- Offboard local hold command sent: `{local_hold_mode_command_sent}`",
        f"- Offboard local hold nav_state after mode: `{local_hold_nav_state_after_mode}`",
        f"- Offboard local hold accepts setpoints: `{local_hold_accepts_offboard_setpoints}`",
        f"- Offboard local hold setpoint mode: `{local_hold_setpoint_mode}`",
        f"- Offboard local hold velocity setpoint m/s: `({local_hold_vx_m_s}, {local_hold_vy_m_s}, {local_hold_vz_m_s})`",
        f"- Failsafe profile: `{args.failsafe_profile}`",
        f"- Failsafe profile OK: `{failsafe_profile_ok}`",
        f"- Observation mode: `{observation_mode}`",
        f"- Landing required: `{landing_required}`",
        f"- Landing OK: `{landing_ok}`",
        f"- GNSS start SIM_GPS_USED: `{args.gnss_start_used}`",
        f"- GNSS loss requested: `{gnss_loss_requested}`",
        f"- GNSS loss after takeoff s: `{args.gnss_loss_after_takeoff_s}`",
        f"- Effective GNSS loss after takeoff s: `{effective_gnss_loss_after_takeoff_s}`",
        f"- GNSS loss detected in console: `{gnss_loss_detected}`",
        f"- GNSS loss OK: `{gnss_loss_ok}`",
        f"- Arming denied in console: `{arming_denied}`",
        f"- Armed detected in console: `{armed_detected}`",
        f"- Takeoff detected in console: `{takeoff_detected}`",
        f"- Landing detected in console: `{landing_detected}`",
        f"- Disarmed by landing in console: `{disarmed_by_landing}`",
        f"- ULog copied: `{ulog_copied}`",
        f"- Copied ULog: `{copied_ulog}`",
        f"- Requested airborne seconds: `{requested_airborne_s:.3f}`",
        f"- ULog flight analysis OK: `{flight_analysis['ulog_flight_analysis_ok']}`",
        f"- ULog airborne duration s: `{flight_analysis['ulog_airborne_duration_s']}`",
        f"- ULog height >0.5m duration s: `{flight_analysis['ulog_height_above_0p5_duration_s']}`",
        f"- ULog max height up m: `{flight_analysis['ulog_max_height_up_m']}`",
        f"- ULog reached min height: `{flight_analysis['ulog_reached_min_height']}`",
        f"- ULog airborne duration OK: `{flight_analysis['ulog_airborne_duration_ok']}`",
        f"- ULog flight OK: `{flight_analysis['ulog_flight_ok']}`",
        f"- External odometry EKF2_EV_DELAY ms: `{external_odom_ev_delay_ms}`",
        f"- External odometry MAVLink frame: `{external_odom_mav_frame}`",
        f"- External odometry position std m: `{external_position_std_m}`",
        f"- External odometry velocity std m/s: `{external_velocity_std_m_s}`",
        f"- External odometry velocity source: `{external_odom_velocity_source}`",
        f"- External odometry velocity alpha: `{external_odom_velocity_alpha}`",
        f"- External odometry max finite-diff speed m/s: `{external_odom_max_finite_diff_speed_m_s}`",
        f"- External odometry velocity reject action: `{external_odom_velocity_reject_action}`",
        f"- External odometry quality: `{external_odom_quality}`",
        f"- External odometry extra EKF2 params: `{external_odom_extra_params}`",
        f"- ULog external odometry fusion OK: `{external_odom_fusion_analysis['ulog_external_odom_fusion_ok']}`",
        f"- ULog EV position active count: `{external_odom_fusion_analysis['ulog_ev_pos_active_count']}`",
        f"- ULog EV height active count: `{external_odom_fusion_analysis['ulog_ev_hgt_active_count']}`",
        f"- ULog EV velocity active count: `{external_odom_fusion_analysis['ulog_ev_vel_active_count']}`",
        f"- ULog EV position rejected count: `{external_odom_fusion_analysis['ulog_ev_pos_rejected_count']}`",
        f"- ULog EV height rejected count: `{external_odom_fusion_analysis['ulog_ev_hgt_rejected_count']}`",
        f"- ULog EV velocity rejected count: `{external_odom_fusion_analysis['ulog_ev_vel_rejected_count']}`",
        f"- ULog XY reset counter delta: `{external_odom_fusion_analysis['ulog_xy_reset_counter_delta']}`",
        f"- Elapsed seconds: `{elapsed_s:.3f}`",
        "",
        "## Result",
        "",
        "Accepted." if accepted else "Rejected. Inspect logs and Gazebo truth recorder output.",
        "",
    ]

    (run_dir / "validation.md").write_text("\n".join(validation))

    print()
    print("== PXH takeoff/land/truth result ==")
    print(f"accepted={accepted}")
    print(f"startup_pattern={startup_pattern}")
    print(f"flight_ready_pattern={flight_ready_pattern}")
    print(f"truth_recorded={truth_recorded}")
    print(f"truth_raw_bytes={truth_raw_bytes}")
    print(f"gazebo_web_enabled={gazebo_web_enabled}")
    print(f"gazebo_web_required={gazebo_web_required}")
    print(f"gazebo_web_ready={gazebo_web_ready}")
    print(f"gazebo_web_port={gazebo_web_port}")
    print(f"gazebo_web_url=ws://127.0.0.1:{gazebo_web_port}")
    print(f"camera_proof_enabled={camera_proof_enabled}")
    print(f"camera_render_engine={camera_render_engine}")
    print(f"camera_xvfb_enabled={camera_xvfb_enabled}")
    print(f"camera_headless_rendering={camera_headless_rendering}")
    print(f"camera_probe_ok={camera_probe_result['camera_probe_ok']}")
    print(f"camera_topic_seen={camera_probe_result['camera_topic_seen']}")
    print(f"camera_image_sample_bytes={camera_probe_result['camera_image_sample_bytes']}")
    print(f"rangefinder_proof_enabled={rangefinder_proof_enabled}")
    print(f"rangefinder_probe_ok={rangefinder_probe_result['rangefinder_probe_ok']}")
    print(f"rangefinder_sample_range_m={rangefinder_probe_result['rangefinder_sample_range_m']}")
    print(f"ulog_distance_sensor_rows={rangefinder_ulog_analysis['ulog_distance_sensor_rows']}")
    print(f"ulog_distance_sensor_max_m={rangefinder_ulog_analysis['ulog_distance_sensor_max_m']}")
    print(f"ulog_distance_sensor_height_diff_m={rangefinder_ulog_analysis['ulog_distance_sensor_height_diff_m']}")
    print(f"ulog_distance_sensor_ok={rangefinder_ulog_analysis['ulog_distance_sensor_ok']}")
    print(f"flow_recording_enabled={flow_recording_enabled}")
    print(f"flow_recording_frames={flow_recording_frames}")
    print(f"flow_recording_ok={flow_recording_ok}")
    print(f"flow_bridge_enabled={flow_bridge_enabled}")
    print(f"flow_bridge_started={flow_bridge_started}")
    print(f"flow_bridge_sent_rows={flow_bridge_sent_rows}")
    print(f"flow_bridge_ok={flow_bridge_ok}")
    print(f"qgc_enabled={qgc_enabled}")
    print(f"qgc_ip={args.qgc_ip}")
    print(f"qgc_mavlink_started={qgc_mavlink_started}")
    print(f"qgc_status_checked={qgc_status['qgc_status_checked']}")
    print(f"qgc_connected={qgc_status['qgc_connected']}")
    print(f"qgc_gcs_heartbeat_seen={qgc_status['qgc_gcs_heartbeat_seen']}")
    print(f"qgc_partner_ip_seen={qgc_status['qgc_partner_ip_seen']}")
    print(f"global_position_gate_enabled={global_position_gate_enabled}")
    print(f"global_position_ready={global_position_ready}")
    print(f"global_position_ready_sample={global_position_ready_sample}")
    print(f"airborne_hover_wait_ok={airborne_hover_wait_ok}")
    print(f"airborne_hover_wait_target_s={airborne_hover_wait_target_s}")
    print(f"airborne_hover_wait_sample={airborne_hover_wait_sample}")
    print(f"local_hold_takeoff_wait_ok={local_hold_takeoff_wait_ok}")
    print(f"local_hold_takeoff_wait_target_s={local_hold_takeoff_wait_target_s}")
    print(f"local_hold_takeoff_wait_sample={local_hold_takeoff_wait_sample}")
    print(f"control_mode={control_mode}")
    print(f"local_hold_enabled={local_hold_enabled}")
    print(f"local_hold_started={local_hold_started}")
    print(f"local_hold_ok={local_hold_ok}")
    print(f"local_hold_sent_rows={local_hold_sent_rows}")
    print(f"local_hold_mode_detected={local_hold_mode_detected}")
    print(f"local_hold_mode_command_sent={local_hold_mode_command_sent}")
    print(f"local_hold_nav_state_after_mode={local_hold_nav_state_after_mode}")
    print(f"local_hold_accepts_offboard_setpoints={local_hold_accepts_offboard_setpoints}")
    print(f"local_hold_setpoint_mode={local_hold_setpoint_mode}")
    print(f"failsafe_profile={args.failsafe_profile}")
    print(f"failsafe_profile_ok={failsafe_profile_ok}")
    print(f"observation_mode={observation_mode}")
    print(f"landing_required={landing_required}")
    print(f"landing_ok={landing_ok}")
    print(f"gnss_start_used={args.gnss_start_used}")
    print(f"gnss_loss_requested={gnss_loss_requested}")
    print(f"gnss_loss_after_takeoff_s={args.gnss_loss_after_takeoff_s}")
    print(f"effective_gnss_loss_after_takeoff_s={effective_gnss_loss_after_takeoff_s}")
    print(f"gnss_loss_detected={gnss_loss_detected}")
    print(f"gnss_loss_verified={gnss_loss_verified}")
    print(f"gnss_loss_observed_fix_type={gnss_loss_confirm.get('fix_type')}")
    print(f"gnss_loss_ok={gnss_loss_ok}")
    print(f"arming_denied={arming_denied}")
    print(f"armed_detected={armed_detected}")
    print(f"takeoff_detected={takeoff_detected}")
    print(f"landing_detected={landing_detected}")
    print(f"disarmed_by_landing={disarmed_by_landing}")
    print(f"ulog_copied={ulog_copied}")
    print(f"requested_airborne_s={requested_airborne_s}")
    print(f"ulog_airborne_duration_s={flight_analysis['ulog_airborne_duration_s']}")
    print(f"ulog_max_height_up_m={flight_analysis['ulog_max_height_up_m']}")
    print(f"ulog_flight_ok={flight_analysis['ulog_flight_ok']}")
    print(f"external_odom_mav_frame={external_odom_mav_frame}")
    print(f"external_odom_velocity_source={external_odom_velocity_source}")
    print(f"external_odom_velocity_alpha={external_odom_velocity_alpha}")
    print(f"external_odom_max_finite_diff_speed_m_s={external_odom_max_finite_diff_speed_m_s}")
    print(f"external_odom_velocity_reject_action={external_odom_velocity_reject_action}")
    print(f"ulog_external_odom_fusion_ok={external_odom_fusion_analysis['ulog_external_odom_fusion_ok']}")
    print(f"ulog_ev_pos_active_count={external_odom_fusion_analysis['ulog_ev_pos_active_count']}")
    print(f"ulog_ev_hgt_active_count={external_odom_fusion_analysis['ulog_ev_hgt_active_count']}")
    print(f"ulog_ev_vel_active_count={external_odom_fusion_analysis['ulog_ev_vel_active_count']}")
    print(f"ulog_ev_pos_rejected_count={external_odom_fusion_analysis['ulog_ev_pos_rejected_count']}")
    print(f"ulog_ev_hgt_rejected_count={external_odom_fusion_analysis['ulog_ev_hgt_rejected_count']}")
    print(f"ulog_ev_vel_rejected_count={external_odom_fusion_analysis['ulog_ev_vel_rejected_count']}")
    print(f"ulog_xy_reset_counter_delta={external_odom_fusion_analysis['ulog_xy_reset_counter_delta']}")
    print(f"run_dir={run_dir}")
    print(f"status_json={status_json}")

    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
