#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo scripts/deploy/bootstrap.sh [--dry-run] [--px4-root PATH] [--skip-px4-build]
                                   [--skip-terrain-generator] [--yes]
EOF
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PX4_ROOT="/opt/sim_px4/PX4-Autopilot"
TERRAIN_ROOT=""
TERRAIN_REPO=""
TERRAIN_COMMIT=""
TERRAIN_OUTPUT_PATH=""
TERRAIN_UV_VERSION=""
DRY_RUN=0
SKIP_PX4_BUILD=0
SKIP_TERRAIN_GENERATOR=0
YES=0
CURRENT_STEP="startup"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --px4-root)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      PX4_ROOT="$2"
      shift 2
      ;;
    --skip-px4-build)
      SKIP_PX4_BUILD=1
      shift
      ;;
    --skip-terrain-generator)
      SKIP_TERRAIN_GENERATOR=1
      shift
      ;;
    --yes)
      YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

PX4_PINS_PATH="${DATABOSS_PX4_PINS_PATH:-$PROJECT_ROOT/deploy/px4/px4_pins.yaml}"
PX4_REPO=""
PX4_COMMIT=""
PX4_BUILD_TARGET=""
GZ_SUBMODULE_PATH=""
OSRF_KEYRING="/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg"
OSRF_SOURCE="/etc/apt/sources.list.d/gazebo-stable.list"
OSRF_SOURCE_LINE="deb [arch=amd64 signed-by=$OSRF_KEYRING] http://packages.osrfoundation.org/gazebo/ubuntu-stable noble main"
APT_YES=()
if [[ "$YES" -eq 1 ]]; then
  APT_YES=(-y)
fi

step() {
  CURRENT_STEP="$1"
  printf '\n==> %s\n' "$CURRENT_STEP"
}

# The ONLY executor. In --dry-run it prints and returns 0 without running
# anything, so a dry run walks the exact same step sequence a real run does.
# There is deliberately no second "plan" function: a hand-maintained preview
# drifts from the code it claims to describe, and this one already had --
# it printed an rsync --delete against the whole PX4 models directory while
# the real path synced per-model, which would have deleted every stock model.
run_cmd() {
  printf '+ %s\n' "$*"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  bash -c "$*"
}

run_as_px4() {
  local cmd="$1"
  run_cmd "runuser -u px4 -- bash -c $(printf '%q' "$cmd")"
}

# Read-only git probe as the px4 user. safe.directory is set explicitly because
# provisioning legitimately passes through states where the tree is not yet
# owned by px4 (the chown happens mid-step), and git otherwise refuses with
# "detected dubious ownership" and aborts the whole run.
git_as_px4() {
  local dir="$1"; shift
  runuser -u px4 -- git -c "safe.directory=$dir" -C "$dir" "$@"
}

# A read-only probe could not run because an earlier step was skipped in
# --dry-run. Report what would happen rather than aborting the preview.
dry_skip() {
  printf '+ (would run) %s   [%s]\n' "$1" "$2"
}

# In --dry-run a failed precondition is a warning so the operator still sees
# the whole plan; in a real run it stays a hard error.
fail_or_warn() {
  local message="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "WARN (dry-run, would be fatal): $message"
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

read_pin_scalar_sequence() {
  local key="$1"
  awk -v key="$key" '
    /^[^[:space:]#][^:]*:[[:space:]]*($|#)/ {
      current = $0
      sub(/:.*/, "", current)
      in_key = (current == key)
      next
    }
    in_key && /^[[:space:]]*-[[:space:]]*/ {
      item = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", item)
      sub(/[[:space:]]*#.*/, "", item)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
      if (item != "" && item !~ /:/) {
        print item
      }
    }
  ' "$PX4_PINS_PATH"
}

read_required_pin_scalar_sequence() {
  local key="$1"
  mapfile -t PIN_SEQUENCE_RESULT < <(read_pin_scalar_sequence "$key")
  if [[ "${#PIN_SEQUENCE_RESULT[@]}" -eq 0 ]]; then
    fail_or_warn "$PX4_PINS_PATH key '$key' resolved to an empty scalar list."
  fi
}

read_pin_mapping_scalar() {
  local section="$1"
  local key="$2"
  awk -v section="$section" -v key="$key" '
    /^[^[:space:]#][^:]*:[[:space:]]*($|#|[^#]*)/ {
      current = $0
      sub(/:.*/, "", current)
      in_section = (current == section)
      next
    }
    in_section {
      pattern = "^[[:space:]]+" key ":[[:space:]]*"
      if ($0 ~ pattern) {
        value = $0
        sub(pattern, "", value)
        sub(/[[:space:]]*#.*/, "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (value == "\"\"" || value == "\047\047") {
          value = ""
        }
        print value
        exit
      }
    }
  ' "$PX4_PINS_PATH"
}

read_required_pin_mapping_scalar() {
  local section="$1"
  local key="$2"
  local value
  value="$(read_pin_mapping_scalar "$section" "$key")"
  if [[ -z "$value" ]]; then
    fail_or_warn "$PX4_PINS_PATH key '$section.$key' resolved to an empty scalar." >&2
  fi
  printf '%s\n' "$value"
}

resolve_project_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$PROJECT_ROOT" "$path"
  fi
}

load_pinned_config() {
  PX4_REPO="$(awk '/^[[:space:]]*repo: / && !seen {print $2; seen=1}' "$PX4_PINS_PATH")"
  PX4_COMMIT="$(awk '/^[[:space:]]*commit: / && !seen {print $2; seen=1}' "$PX4_PINS_PATH")"
  PX4_BUILD_TARGET="$(awk '/^[[:space:]]*build_target: / {print $2; exit}' "$PX4_PINS_PATH")"
  GZ_SUBMODULE_PATH="$(awk '/^[[:space:]]*path: Tools\/simulation\/gz/ {print $2; exit}' "$PX4_PINS_PATH")"

  TERRAIN_ROOT="$(read_required_pin_mapping_scalar terrain_generator root)"
  TERRAIN_REPO="$(read_required_pin_mapping_scalar terrain_generator repo)"
  TERRAIN_COMMIT="$(read_required_pin_mapping_scalar terrain_generator commit)"
  TERRAIN_OUTPUT_PATH="$(read_required_pin_mapping_scalar terrain_generator output_path)"
  TERRAIN_UV_VERSION="$(read_required_pin_mapping_scalar terrain_generator uv_version)"
}

on_error() {
  local rc=$?
  echo "ERROR: step ${CURRENT_STEP} failed (exit ${rc}). Fix that step and rerun this bootstrap command; completed idempotent steps will be skipped."
  exit "$rc"
}

trap on_error ERR

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    fail_or_warn "run this provisioning script with sudo/root."
  fi
}

nearest_existing_parent() {
  local path="$1"
  while [[ ! -e "$path" ]]; do
    path="$(dirname "$path")"
  done
  printf '%s\n' "$path"
}

assert_disk_free() {
  local label="$1"
  local target="$2"
  local parent avail_text avail_gb
  parent="$(nearest_existing_parent "$target")"
  avail_text="$(df -BG --output=avail "$parent" | tail -n 1 | tr -dc '0-9')"
  avail_gb="${avail_text:-0}"
  if (( avail_gb < 20 )); then
    fail_or_warn "$label filesystem has ${avail_gb} GB free at $parent; need at least 20 GB."
    return 0
  fi
  echo "OK: $label filesystem has ${avail_gb} GB free at $parent."
}

requirements_satisfied() {
  local python_bin="$1"
  local requirements_file="$2"
  [[ -x "$python_bin" ]] || return 1
  "$python_bin" - "$requirements_file" <<'PY'
import importlib.metadata as md
import re
import sys
from pathlib import Path

req = Path(sys.argv[1])
missing = []
for raw in req.read_text().splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^; ]+)", line)
    if not match:
        continue
    name, expected = match.groups()
    try:
        actual = md.version(name)
    except md.PackageNotFoundError:
        missing.append(f"{name}: missing, expected {expected}")
    else:
        if actual != expected:
            missing.append(f"{name}: {actual}, expected {expected}")
if missing:
    print("\n".join(missing))
    sys.exit(1)
PY
}

bridge_imports_ok() {
  [[ -x "$PROJECT_ROOT/venv_bridge/bin/python" ]] || return 1
  "$PROJECT_ROOT/venv_bridge/bin/python" - <<'PY'
import importlib
for name in ("gz.transport13", "gz.msgs10", "cv2", "pymavlink"):
    importlib.import_module(name)
PY
}

preflight() {
  step "1. Preflight"
  require_root
  # shellcheck source=/dev/null
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
    fail_or_warn "expected Ubuntu 24.04, got ${PRETTY_NAME:-unknown}."
  fi
  if [[ "$(uname -m)" != "x86_64" ]]; then
    fail_or_warn "expected x86_64, got $(uname -m)."
  fi
  assert_disk_free "project" "$PROJECT_ROOT"
  assert_disk_free "PX4 target" "$(dirname "$PX4_ROOT")"
  if [[ "$SKIP_TERRAIN_GENERATOR" -eq 0 ]]; then
    assert_disk_free "terrain generator target" "$(dirname "$TERRAIN_ROOT")"
  fi
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  mem_gb=$(( mem_kb / 1024 / 1024 ))
  if (( mem_gb < 8 )); then
    echo "WARN: detected ${mem_gb} GB RAM; PX4 build is slow and memory-constrained below 8 GB."
  else
    echo "OK: detected ${mem_gb} GB RAM."
  fi
}

install_apt_packages() {
  step "2. OSRF apt source and apt packages"
  if [[ -f "$OSRF_KEYRING" ]]; then
    echo "SKIP: OSRF keyring already exists at $OSRF_KEYRING."
  else
    run_cmd "install -m 0755 -d /usr/share/keyrings"
    run_cmd "curl -fsSL https://packages.osrfoundation.org/gazebo.gpg | gpg --dearmor -o $OSRF_KEYRING"
  fi

  if [[ -f "$OSRF_SOURCE" ]] && grep -Fq "$OSRF_SOURCE_LINE" "$OSRF_SOURCE"; then
    echo "SKIP: OSRF apt source already present in $OSRF_SOURCE."
  else
    run_cmd "printf '%s\n' '$OSRF_SOURCE_LINE' > $OSRF_SOURCE"
  fi

  run_cmd "apt-get update"
  mapfile -t apt_packages < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$PROJECT_ROOT/deploy/apt-packages.txt")
  if dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' "${apt_packages[@]}" 2>/dev/null | awk '$1 != "ii" {exit 1}'; then
    echo "SKIP: DATABOSS top-level apt packages are already installed."
  else
    run_cmd "apt-get install ${APT_YES[*]} ${apt_packages[*]}"
  fi
}

install_system_python_packages() {
  step "3. System Python packages"
  if requirements_satisfied /usr/bin/python3 "$PROJECT_ROOT/deploy/requirements-system.txt"; then
    echo "SKIP: system Python packages already match deploy/requirements-system.txt."
  else
    run_cmd "/usr/bin/python3 -m pip install --break-system-packages -r $PROJECT_ROOT/deploy/requirements-system.txt"
  fi
}

ensure_px4_user() {
  step "4. px4 system user"
  if id px4 >/dev/null 2>&1; then
    echo "SKIP: px4 user already exists."
  else
    run_cmd "useradd --system --create-home --home-dir /home/px4 --shell /bin/bash px4"
  fi
}

sync_px4_tree() {
  step "5. PX4 clone and pin"
  run_cmd "install -d -o px4 -g px4 $(dirname "$PX4_ROOT")"
  if [[ ! -d "$PX4_ROOT/.git" ]]; then
    run_as_px4 "git clone --recursive $PX4_REPO $PX4_ROOT"
  fi

  if [[ ! -d "$PX4_ROOT/.git" ]]; then
    # Only reachable in --dry-run: a real run cloned it just above.
    dry_skip "git -C $PX4_ROOT checkout $PX4_COMMIT" "PX4 not cloned yet"
  else
    current_head="$(git_as_px4 "$PX4_ROOT" rev-parse HEAD)"
    if [[ "$current_head" == "$PX4_COMMIT" ]]; then
      echo "SKIP: PX4 already at pinned commit $PX4_COMMIT."
    else
      if [[ -n "$(git_as_px4 "$PX4_ROOT" status --porcelain)" ]]; then
        fail_or_warn "$PX4_ROOT has local changes; refusing to change PX4 checkout."
      else
        run_as_px4 "cd $PX4_ROOT && git fetch --tags origin && git checkout $PX4_COMMIT"
      fi
    fi
  fi
  run_as_px4 "cd $PX4_ROOT && git submodule update --init --recursive"

  step "5b. PX4 prerequisite script"
  marker="/var/lib/databoss/bootstrap/px4-ubuntu-sh.$PX4_COMMIT.done"
  if [[ -f "$marker" ]]; then
    echo "SKIP: PX4 Tools/setup/ubuntu.sh already completed for $PX4_COMMIT."
  else
    run_cmd "install -d /var/lib/databoss/bootstrap"
    run_cmd "cd $PX4_ROOT && bash Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools"
    run_cmd "touch $marker"
  fi
}

apply_px4_patches() {
  step "6. PX4 patches"
  local patch base
  for patch in "$PROJECT_ROOT"/deploy/px4/*.patch; do
    base="$PX4_ROOT"
    if [[ "$(basename "$patch")" == 0003-* ]]; then
      base="$PX4_ROOT/$GZ_SUBMODULE_PATH"
    fi
    if [[ ! -d "$base/.git" && ! -f "$base/.git" ]]; then
      # Only reachable in --dry-run: a real run has the tree by now.
      dry_skip "cd $base && git apply $patch" "PX4 tree not present yet"
    elif git_as_px4 "$base" apply --check "$patch"; then
      run_as_px4 "cd $base && git apply $patch"
    elif git_as_px4 "$base" apply --check -R "$patch"; then
      echo "SKIP: $(basename "$patch") is already applied in $base."
    else
      fail_or_warn "$(basename "$patch") neither applies cleanly nor reverses cleanly in $base."
    fi
  done
}

install_models_and_airframes() {
  step "7. DATABOSS models and airframes"
  local model airframe
  local -a models=()
  local -a airframes=()
  local -a PIN_SEQUENCE_RESULT=()
  read_required_pin_scalar_sequence models
  models=("${PIN_SEQUENCE_RESULT[@]}")
  read_required_pin_scalar_sequence airframes
  airframes=("${PIN_SEQUENCE_RESULT[@]}")
  run_cmd "install -d $PX4_ROOT/$GZ_SUBMODULE_PATH/models"
  for model in "${models[@]}"; do
    run_cmd "rsync -a --delete $PROJECT_ROOT/src/databoss_sim/models/$model/ $PX4_ROOT/$GZ_SUBMODULE_PATH/models/$model/"
  done
  run_cmd "install -d $PX4_ROOT/ROMFS/px4fmu_common/init.d-posix/airframes"
  for airframe in "${airframes[@]}"; do
    run_cmd "install -m 0644 $PROJECT_ROOT/src/databoss_sim/airframes/$airframe $PX4_ROOT/ROMFS/px4fmu_common/init.d-posix/airframes/$airframe"
  done
}

build_px4() {
  step "8. PX4 build"
  if [[ "$SKIP_PX4_BUILD" -eq 1 ]]; then
    echo "SKIP: --skip-px4-build requested. Final deployment check may fail if the build is missing or stale."
    return
  fi
  echo "INFO: make $PX4_BUILD_TARGET takes a long time on a 2-core host."
  run_as_px4 "cd $PX4_ROOT && unset VIRTUAL_ENV PYTHONHOME PYTHONPATH && make $PX4_BUILD_TARGET"
}

build_venvs() {
  step "9. Python virtual environments"
  if [[ ! -x "$PROJECT_ROOT/venv/bin/python" ]]; then
    run_cmd "cd $PROJECT_ROOT && python3 -m venv venv"
  fi
  if requirements_satisfied "$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/requirements.txt"; then
    echo "SKIP: main venv already matches requirements.txt."
  else
    run_cmd "$PROJECT_ROOT/venv/bin/python -m pip install -r $PROJECT_ROOT/requirements.txt"
  fi

  if [[ ! -x "$PROJECT_ROOT/venv_bridge/bin/python" ]]; then
    run_cmd "cd $PROJECT_ROOT && /usr/bin/python3 -m venv --system-site-packages venv_bridge"
  fi
  if requirements_satisfied "$PROJECT_ROOT/venv_bridge/bin/python" "$PROJECT_ROOT/requirements-bridge.txt" && bridge_imports_ok; then
    echo "SKIP: venv_bridge already has bridge pins and apt/system imports."
  else
    run_cmd "$PROJECT_ROOT/venv_bridge/bin/python -m pip install -r $PROJECT_ROOT/requirements-bridge.txt"
  fi
}

install_uv() {
  local uv_path
  uv_path="$(command -v uv || true)"
  if [[ -n "$uv_path" ]]; then
    echo "SKIP: uv already on PATH at $uv_path ($("$uv_path" --version))."
    return
  fi

  run_cmd "curl -LsSf https://astral.sh/uv/$TERRAIN_UV_VERSION/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh"
}

sync_terrain_generator() {
  step "10. Terrain generator"
  if [[ "$SKIP_TERRAIN_GENERATOR" -eq 1 ]]; then
    echo "SKIP: --skip-terrain-generator requested."
    return
  fi
  local terrain_output_abs
  terrain_output_abs="$(resolve_project_path "$TERRAIN_OUTPUT_PATH")"
  echo "INFO: terrain generator pins repo=$TERRAIN_REPO commit=$TERRAIN_COMMIT root=$TERRAIN_ROOT output=$terrain_output_abs uv=$TERRAIN_UV_VERSION."
  install_uv
  if [[ ! -d "$TERRAIN_ROOT/.git" ]]; then
    run_cmd "git clone $TERRAIN_REPO $TERRAIN_ROOT"
  fi
  run_cmd "chown -R px4:px4 $TERRAIN_ROOT"
  if [[ ! -d "$TERRAIN_ROOT/.git" ]]; then
    # Only reachable in --dry-run: a real run cloned it just above.
    dry_skip "cd $TERRAIN_ROOT && git checkout $TERRAIN_COMMIT" "terrain generator not cloned yet"
  else
    if ! current_head="$(git_as_px4 "$TERRAIN_ROOT" rev-parse --short=7 HEAD 2>/dev/null)"; then
      if [[ "$DRY_RUN" -eq 1 ]]; then
        dry_skip "cd $TERRAIN_ROOT && git rev-parse --short=7 HEAD" "could not read terrain generator HEAD as px4"
        current_head=""
      else
        fail_or_warn "could not read $TERRAIN_ROOT HEAD as px4."
      fi
    fi
    if [[ "$current_head" == "$TERRAIN_COMMIT" ]]; then
      echo "SKIP: terrain generator already at $TERRAIN_COMMIT."
    else
      if ! terrain_status="$(git_as_px4 "$TERRAIN_ROOT" status --porcelain 2>/dev/null)"; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
          dry_skip "cd $TERRAIN_ROOT && git status --porcelain" "could not read terrain generator status as px4"
          terrain_status=""
        else
          fail_or_warn "could not read $TERRAIN_ROOT status as px4."
        fi
      fi
      if [[ -n "$terrain_status" ]]; then
        fail_or_warn "$TERRAIN_ROOT has local changes; refusing to change terrain generator checkout."
      else
        run_as_px4 "cd $TERRAIN_ROOT && git fetch origin && git checkout $TERRAIN_COMMIT"
      fi
    fi
  fi
  run_as_px4 "cd $TERRAIN_ROOT && PATH=/usr/local/bin:\$PATH GAZEBO_TERRAIN_OUTPUT_PATH=$terrain_output_abs uv sync"
}

ensure_dashboard_token() {
  step "11. Dashboard token"
  if [[ -f "$PROJECT_ROOT/.dashboard_token" ]]; then
    echo "SKIP: .dashboard_token already exists. It is only needed when DATABOSS_DASHBOARD_REQUIRE_TOKEN=1."
  else
    echo "INFO: generating .dashboard_token; it is only needed when DATABOSS_DASHBOARD_REQUIRE_TOKEN=1."
    run_cmd "$PROJECT_ROOT/venv/bin/python $PROJECT_ROOT/scripts/dashboard/generate_token.py"
  fi
}

fix_ownership() {
  step "12. Ownership"
  run_cmd "mkdir -p $PROJECT_ROOT/experiments $PROJECT_ROOT/generated_worlds $PROJECT_ROOT/generated_worlds/terrain/_generator_output"
  run_cmd "chown -R px4:px4 $PROJECT_ROOT"
  run_cmd "chown -R px4:px4 $PROJECT_ROOT/experiments $PROJECT_ROOT/generated_worlds"
}

install_systemd_unit() {
  step "13. systemd dashboard service"
  if [[ -f /etc/systemd/system/databoss-dashboard.service ]] && cmp -s "$PROJECT_ROOT/scripts/dashboard/databoss-dashboard.service" /etc/systemd/system/databoss-dashboard.service; then
    echo "SKIP: systemd unit already installed and identical."
  else
    run_cmd "install -m 0644 $PROJECT_ROOT/scripts/dashboard/databoss-dashboard.service /etc/systemd/system/databoss-dashboard.service"
    run_cmd "systemctl daemon-reload"
  fi
  run_cmd "systemctl enable --now databoss-dashboard"
}

run_deployment_check() {
  step "14. Deployment check"
  run_cmd "$PROJECT_ROOT/venv/bin/python $PROJECT_ROOT/scripts/deploy/check_deployment.py --px4-root $PX4_ROOT"
}

load_pinned_config
preflight
install_apt_packages
install_system_python_packages
ensure_px4_user
sync_px4_tree
apply_px4_patches
install_models_and_airframes
build_px4
build_venvs
sync_terrain_generator
ensure_dashboard_token
fix_ownership
install_systemd_unit
run_deployment_check
