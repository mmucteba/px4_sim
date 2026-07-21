#!/usr/bin/env python3
"""Phase 8G.0 plumbing rehearsal: fly PX4's stock x500_flow, save a reference ULog.

Proves the EKF2 optical-flow fusion path (OpticalFlowSystem KLT plugin ->
SIM_GZ_EN_FLOW -> sensor_optical_flow -> EKF2) before our own bridge enters
the loop. Decision D7: GPS is re-enabled at pxh after boot (airframe 4021
boots GNSS-off; SIM_GPS_USED is runtime-effective, same mechanism as the
accepted GNSS-loss toggle, in reverse).

Launches `make px4_sitl gz_x500_flow` (default world, PX4-managed Gazebo)
under xvfb-run with PX4_GZ_SIM_RENDER_ENGINE=ogre so the 100x100 flow camera
renders on this headless VM (ogre2 + software EGL segfaults here). A
GZ_SIM_SERVER_CONFIG_PATH override does NOT work in PX4-managed mode:
px4-rc.gzsim sources gz_env.sh which unconditionally resets that variable;
the CLI --render-engine flag from PX4_GZ_SIM_RENDER_ENGINE wins instead.

Runs under system python3. Analysis is a separate venv step
(scripts/analysis/analyze_flow_fusion_ulog.py).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNNER_DIR))

from auto_takeoff_land_pxh_truth import (  # noqa: E402
    FLIGHT_READY_PATTERNS,
    STARTUP_PATTERNS,
    newest_ulog,
    query_pxh,
    send_pxh,
    wait_for_pattern,
)
from create_run_from_scenario import PROJECT_ROOT, PX4_ROOT, write_environment  # noqa: E402
from launch_px4_headless_smoke import clean_px4_env, stop_process_group  # noqa: E402

RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"
XVFB_SERVER_ARGS = "-screen 0 1280x1024x24"
# Wall-clock. Lockstep sim runs at RTF ~0.25 on this VM with the 50 Hz flow
# camera under software rendering, so 160 wall s ~= 40 sim s of hover
# (first flight: 40 wall s gave only ~10 sim s airborne, dominated by the
# hover-entry transient).
HOVER_S = 160.0
GPS_PARAMS = ["param set SYS_HAS_GPS 1", "param set SIM_GPS_USED 10", "param set EKF2_GPS_CTRL 7"]
# Same automation root as the runner's failsafe profiles: without RC input
# disabled, the RC-loss failsafe fires at arming and escalates to AUTO_LAND
# mid-hover (observed in run 20260713_062101 at t=33.9s sim).
AUTOMATION_PARAMS = ["param set COM_RC_IN_MODE 4"]


def main() -> int:
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_phase8g0_x500_flow_rehearsal"
    run_dir = RUNS_DIR / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "sensor_logs").mkdir()
    console_log = run_dir / "logs" / "px4_gazebo_console.log"
    notes: list[str] = []

    (run_dir / "README.md").write_text(
        "# Phase 8G.0 — stock x500_flow plumbing rehearsal\n\n"
        "Reference run for the EKF2 optical-flow fusion path using PX4's stock\n"
        "KLT flow plugin, BEFORE the DATABOSS bridge enters the loop. GPS\n"
        "re-enabled at pxh (D7). Default world, hover only, no truth recording\n"
        "(fusion-path proof, not physical-error evaluation).\n"
    )
    (run_dir / "commands.log").write_text(
        "# Commands\n\ncd {root}\npython3 scripts/runner/phase8g0_x500_flow_rehearsal.py\n\n"
        "PX4 launch: xvfb-run -a make px4_sitl gz_x500_flow (HEADLESS=1, ogre)\n\n"
        "PX4 shell sequence:\n\n{gps}\ncommander arm -f\ncommander takeoff\n"
        "# hover {hover}s\ncommander land\n".format(
            root=PROJECT_ROOT, gps="\n".join(GPS_PARAMS), hover=HOVER_S)
    )
    write_environment(run_dir / "environment.txt")

    env = clean_px4_env()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env["PX4_GZ_SIM_RENDER_ENGINE"] = "ogre"

    cmd = ["xvfb-run", "-a", "--server-args", XVFB_SERVER_ARGS,
           "make", "px4_sitl", "gz_x500_flow"]
    print(f"run_dir={run_dir}")
    print(f"launch: {' '.join(cmd)}")
    start_ts = time.time()
    log = console_log.open("w")
    px4 = subprocess.Popen(cmd, cwd=str(PX4_ROOT), env=env, stdin=subprocess.PIPE,
                           stdout=log, stderr=subprocess.STDOUT, text=True, bufsize=1,
                           preexec_fn=os.setsid)
    accepted = False
    try:
        startup = wait_for_pattern(console_log, px4, STARTUP_PATTERNS, 240.0)
        print(f"startup_pattern={startup}")
        if not startup:
            return finish(run_dir, notes, accepted, "PX4 startup pattern not seen")

        ready = wait_for_pattern(console_log, px4, FLIGHT_READY_PATTERNS, 30.0)
        print(f"flight_ready_pattern={ready}")

        # Evidence: is the stock flow stream alive before we touch anything?
        flow_probe = query_pxh(px4, console_log, "listener sensor_optical_flow 3", notes, wait_s=3.0)
        (run_dir / "sensor_logs" / "sensor_optical_flow_preflight.txt").write_text(flow_probe)
        flow_seen = "quality" in flow_probe
        print(f"preflight sensor_optical_flow seen={flow_seen}")

        for p in AUTOMATION_PARAMS + GPS_PARAMS:
            send_pxh(px4, p, notes)
        time.sleep(10)  # let GPS become healthy and EKF settle
        gps_probe = query_pxh(px4, console_log, "listener sensor_gps 1", notes, wait_s=2.0)
        (run_dir / "sensor_logs" / "sensor_gps_after_enable.txt").write_text(gps_probe)
        gps_sats = re.search(r"satellites_used:\s*(\d+)", gps_probe)
        print(f"gps satellites_used={gps_sats.group(1) if gps_sats else 'NOT SEEN'}")

        send_pxh(px4, "commander arm -f", notes)
        time.sleep(3)
        send_pxh(px4, "commander takeoff", notes)
        takeoff = wait_for_pattern(console_log, px4, ["Takeoff detected"], 60.0)
        print(f"takeoff_pattern={takeoff}")
        if not takeoff:
            return finish(run_dir, notes, accepted, "takeoff not detected")

        time.sleep(HOVER_S)
        inflight_probe = query_pxh(px4, console_log, "listener sensor_optical_flow 3", notes, wait_s=3.0)
        (run_dir / "sensor_logs" / "sensor_optical_flow_inflight.txt").write_text(inflight_probe)
        print(f"inflight sensor_optical_flow seen={'quality' in inflight_probe}")

        send_pxh(px4, "commander land", notes)
        landed = wait_for_pattern(console_log, px4, ["Landing detected", "Disarmed by landing"], 90.0)
        print(f"landing_pattern={landed}")
        time.sleep(8)  # let the logger close the file

        accepted = bool(landed and flow_seen)
        return finish(run_dir, notes, accepted, "flight complete", start_ts, px4)
    finally:
        stop_process_group(px4, notes)
        log.close()
        (run_dir / "logs" / "runner_notes.txt").write_text("\n".join(notes) + "\n")


def finish(run_dir: Path, notes: list[str], accepted: bool, reason: str,
           start_ts: float | None = None, px4=None) -> int:
    ulog_copied = False
    if start_ts is not None and px4 is not None:
        send_pxh(px4, "logger off", notes)
        time.sleep(3)
        src = newest_ulog(start_ts)
        if src is not None:
            shutil.copy2(src, run_dir / "logs" / "flight.ulg")
            ulog_copied = True
    summary = (f"accepted={accepted}\nreason={reason}\nulog_copied={ulog_copied}\n"
               f"run_dir={run_dir}\n")
    (run_dir / "summary.md").write_text(f"# 8G.0 rehearsal summary\n\n```\n{summary}```\n")
    print(f"== 8G.0 result ==\n{summary}", end="")
    return 0 if accepted and ulog_copied else 1


if __name__ == "__main__":
    raise SystemExit(main())
