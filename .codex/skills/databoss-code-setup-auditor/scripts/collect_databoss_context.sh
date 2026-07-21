#!/usr/bin/env bash
set -u

DATABOSS_ROOT="${DATABOSS_ROOT:-/opt/databoss_px4_sim}"
PX4_ROOT="${PX4_ROOT:-/opt/sim_px4/PX4-Autopilot}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${1:-/tmp/databoss_audit_${STAMP}}"
mkdir -p "$OUT_DIR"

run_capture() {
  local name="$1"
  shift
  {
    echo "# command: $*"
    echo "# time: $(date -Is)"
    "$@"
  } >"$OUT_DIR/$name.txt" 2>&1 || true
}

{
  echo "timestamp=$(date -Is)"
  echo "user=$(id -un 2>/dev/null || true)"
  echo "uid=$(id -u 2>/dev/null || true)"
  echo "host=$(hostname 2>/dev/null || true)"
  echo "databoss_root=$DATABOSS_ROOT"
  echo "px4_root=$PX4_ROOT"
  echo "pwd=$PWD"
  echo "virtual_env=${VIRTUAL_ENV:-}"
  echo "python3=$(command -v python3 2>/dev/null || true)"
  echo "python_version=$(python3 --version 2>&1 || true)"
} > "$OUT_DIR/00_identity.txt"

run_capture 01_system uname -a
run_capture 02_id id
run_capture 03_processes bash -lc "ps -ef | grep -E 'px4|gz sim|gzserver|ruby|mavlink|optical|odometry' | grep -v grep"
run_capture 04_udp_ports bash -lc "ss -lunp 2>/dev/null | grep -E '14540|14550|14555|14600|14601'"
run_capture 05_px4_sockets bash -lc "ls -l /tmp/px4-sock-* 2>/dev/null"
run_capture 06_disk df -h
run_capture 07_memory free -h

if [[ -d "$DATABOSS_ROOT" ]]; then
  run_capture 10_databoss_git bash -lc "cd '$DATABOSS_ROOT' && git status --short --branch 2>/dev/null; git rev-parse HEAD 2>/dev/null"
  run_capture 11_databoss_tree bash -lc "cd '$DATABOSS_ROOT' && find docs experiments/configs scripts src -maxdepth 4 -type f 2>/dev/null | sort | sed -n '1,1000p'"
  run_capture 12_latest_runs bash -lc "cd '$DATABOSS_ROOT' && find experiments/runs -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -20"
  run_capture 13_latest_batches bash -lc "cd '$DATABOSS_ROOT' && find experiments/batches -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -20"
  run_capture 14_latest_comparisons bash -lc "cd '$DATABOSS_ROOT' && find experiments/comparisons -mindepth 1 -maxdepth 2 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -30"
  run_capture 15_scenario_keys bash -lc "cd '$DATABOSS_ROOT' && grep -RIn --include='*.yaml' --include='*.yml' -E 'failsafe|gnss|aiding|optical|flow|EKF2_|SIM_GPS|world|vehicle|rate|quality|latency' experiments/configs 2>/dev/null | sed -n '1,2000p'"
  run_capture 16_runner_consumers bash -lc "cd '$DATABOSS_ROOT' && grep -RIn --include='*.py' -E 'failsafe|gnss|aiding|optical|flow|EKF2_|SIM_GPS|argparse|subprocess|Popen|MAVLink|ODOMETRY' scripts src 2>/dev/null | sed -n '1,2500p'"
fi

if [[ -d "$PX4_ROOT/.git" ]]; then
  run_capture 20_px4_git bash -lc "cd '$PX4_ROOT' && git status --short --branch; git rev-parse HEAD; git describe --always --dirty --tags 2>/dev/null"
  run_capture 21_px4_builds bash -lc "cd '$PX4_ROOT' && find build -maxdepth 2 -type d 2>/dev/null | sort | sed -n '1,300p'"
fi

cat > "$OUT_DIR/README.txt" <<EOT
DATABOSS read-only context snapshot

Created: $(date -Is)

This script does not launch PX4/Gazebo and does not modify project files.
Review the files in this folder, then trace requested → consumed → effective → observed.
EOT

printf '%s\n' "$OUT_DIR"
