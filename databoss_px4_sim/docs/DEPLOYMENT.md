# DATABOSS Deployment

This document is the human counterpart to `scripts/deploy/bootstrap.sh`. The
target host is Ubuntu 24.04.1 LTS on x86_64 with Python 3.12.3.

## Input Inventory

| Item | Required value | Source of truth |
|---|---:|---|
| OS | Ubuntu 24.04.1 LTS, x86_64 | deployment target |
| CPU/RAM | 2 cores, 3 GB RAM minimum observed; warn below 8 GB RAM | deployment target |
| Free disk | refuse below 20 GB free; full deployment costs about 8 GB | measured deployment size |
| Gazebo | `gz-harmonic`, `gz sim` 8.14.0 | OSRF noble apt source |
| OSRF apt source | `http://packages.osrfoundation.org/gazebo/ubuntu-stable noble main` | `deploy/px4/px4_pins.yaml` |
| OSRF keyring | `/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg` | deployment target |
| Top-level apt packages | `deploy/apt-packages.txt` | measured host package set |
| Main venv | `venv/` from `requirements.txt` | repo root |
| Bridge venv | `venv_bridge/` from `requirements-bridge.txt` with `--system-site-packages` | repo root |
| System Python pins | `deploy/requirements-system.txt` | system Python `/usr/local/lib/python3.12/dist-packages` |
| PX4 repo/pin | `deploy/px4/px4_pins.yaml` | repo deployment manifest |
| PX4 patches | `deploy/px4/*.patch` | repo deployment manifest |
| DATABOSS models | `src/databoss_sim/models/*` | copied into PX4, not symlinked |
| DATABOSS airframes | `src/databoss_sim/airframes/4022_*`, `4023_*` | copied into PX4 ROMFS |
| Terrain generator | `https://github.com/saiaravind19/gazebo_terrain_generator.git` at `4946f4c` | pristine upstream checkout |
| Terrain output | `generated_worlds/terrain/_generator_output/` | `GAZEBO_TERRAIN_OUTPUT_PATH` |
| Dashboard service | `scripts/dashboard/databoss-dashboard.service` | copied to systemd |

## Three Python Interpreters

DATABOSS deliberately uses three Python environments.

| Interpreter | Purpose | Important packages |
|---|---|---|
| `venv/` | Main dashboard, runners, analysis, plotting | frozen in `requirements.txt`; OpenCV is `opencv-python-headless==5.0.0.93` |
| `venv_bridge/` | Live flow/rangefinder MAVLink bridge | created with `/usr/bin/python3 -m venv --system-site-packages`; pip installs only `pymavlink`, `fastcrc`, `lxml` |
| System `python3` | apt gz bindings and system OpenCV inherited by `venv_bridge` | `deploy/requirements-system.txt`; OpenCV is `opencv-python-headless==4.13.0.92` |

The cv2 split is load-bearing. The flow bridge runs on system OpenCV 4.13.0.92,
while the main venv analysis stack runs on OpenCV 5.0.0.93. A deployment that
upgrades the bridge to match the main venv changes optical-flow results.
`scripts/deploy/check_deployment.py` checks this split.

The gz Python bindings are also not available from the main venv. They come
from apt packages such as `python3-gz-transport13` and `python3-gz-msgs10`, so
the bridge venv must inherit system site packages.

## PX4 Patch Story

The PX4 pin and patch inventory live in `deploy/px4/px4_pins.yaml`. Patch
application rules matter:

| Patch | Apply from | Why it matters |
|---|---|---|
| `0001-gz-bridge-sim-gps-used.patch` | PX4 root | Makes `SIM_GPS_USED=0` actually affect simulated GPS polling |
| `0002-server-config-wind-effects.patch` | PX4 root | Loads the Gazebo wind-effects system |
| `0003-x500-base-enable-wind.patch` | `Tools/simulation/gz` | Enables wind response on the x500 base model |
| `0004-airframes-cmakelists-register.patch` | PX4 root | Registers DATABOSS airframes into the PX4 ROMFS build |

Missing `0001`, `0002`, or `0003` fails SILENTLY. The sim still runs, the
runners can pass gates, and wind or GNSS-loss results become fiction. In
particular, a GNSS-loss scenario can claim loss while PX4 still receives
healthy simulated GPS, and a wind scenario can complete while the vehicle never
physically feels wind.

PX4 must be rebuilt after `0001` and `0004`. A patched source tree with a stale
`build/px4_sitl_default/bin/px4` is not a valid deployment.
An incomplete PX4 build is self-perpetuating because every runner launch invokes
`make px4_sitl` and then interrupts a long rebuild at startup timeout;
`scripts/deploy/check_deployment.py` now catches a dirty
`build/px4_sitl_default` with a read-only Ninja dry-run before flights are
burned.

## What `bootstrap.sh` Does

Run from the repo root on a fresh target:

```bash
sudo scripts/deploy/bootstrap.sh --yes
```

Dry-run mode is safe on constrained hosts:

```bash
scripts/deploy/bootstrap.sh --dry-run
```

The bootstrap script:

1. Checks Ubuntu 24.04, x86_64, disk, and RAM.
2. Adds the OSRF apt source/keyring and installs `deploy/apt-packages.txt`.
3. Installs `deploy/requirements-system.txt` into system Python with
   `pip install --break-system-packages`.
4. Creates the `px4` system user if absent.
5. Clones PX4 at the pinned commit and updates submodules.
6. Calls PX4's own `Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools` for PX4
   prerequisites instead of duplicating PX4's package list.
7. Applies PX4 patches with `git apply --check`, treating reverse-apply success
   as already applied.
8. Copies DATABOSS models and airframes into PX4.
9. Builds `make px4_sitl_default` as the `px4` user unless skipped.
10. Builds `venv/` and `venv_bridge/`.
11. Clones and syncs the terrain generator unless skipped.
12. Generates `.dashboard_token` if absent. It is only needed when
   `DATABOSS_DASHBOARD_REQUIRE_TOKEN=1`.
13. Ensures `experiments/` and `generated_worlds/` are owned by `px4`.
14. Installs and enables the dashboard systemd unit.
15. Runs `scripts/deploy/check_deployment.py` last and exits non-zero if it
   fails.

Do not treat a skipped PX4 build as proof of readiness. The final deployment
check must pass.

## Out-of-Band Transfer

Transfer `generated_worlds/` out-of-band. It is gitignored and currently costs
about 202 MB. Regenerating it costs Mapbox requests and will not necessarily
produce byte-identical terrain assets.

Build a transport bundle on the source host:

```bash
scripts/deploy/make_world_bundle.sh --output-dir /tmp/databoss-transfer
```

The script writes `databoss-worlds-<UTC-date>.tar.zst` when `zstd` is
available, otherwise `databoss-worlds-<UTC-date>.tar.gz`, plus a sibling
`databoss-worlds-<UTC-date>.sha256`. The archive also contains
`generated_worlds/DATABOSS_WORLD_BUNDLE_MANIFEST.sha256`, which records the
sha256 of each regular file and the preserved symlink targets.

On the receiver, copy the two bundle files to the project root, verify the
transport checksum, extract, verify the per-file manifest, and restore
ownership:

```bash
cd /opt/databoss_px4_sim
sha256sum -c databoss-worlds-<UTC-date>.sha256
tar -I zstd -xf databoss-worlds-<UTC-date>.tar.zst
grep -E '^[0-9a-f]{64}  ' generated_worlds/DATABOSS_WORLD_BUNDLE_MANIFEST.sha256 | sha256sum -c -
sudo chown -R px4:px4 generated_worlds
```

For `.tar.gz` fallback bundles, use `tar -xzf` instead of `tar -I zstd -xf`.

The terrain generator itself is a pristine upstream checkout at `/opt/gazebo_terrain_generator`.
There is no server-side Mapbox secret to deploy. The Mapbox key is posted
per-request from the browser and is not stored by the server.

## Ownership

Runs execute as the `px4` user. Keep these paths writable by `px4`:

```text
experiments/
generated_worlds/
generated_worlds/terrain/_generator_output/
```

Root-owned files in `experiments/` or `generated_worlds/` previously broke
`/api/runs` with HTTP 500 responses.

## QGC and gz-web Wiring

QGroundControl is a viewer/operator monitor, not the experiment engine.

PX4/QGC UDP convention:

```text
PX4 local UDP port:  14555
QGC remote UDP port: 14550
```

Known PX4 MAVLink command:

```bash
mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x
```

Gazebo web convention:

```text
9003 = raw runner-managed gz-launch WebsocketServer bridge
9002 = browser-facing enum-patch proxy
```

Browsers should connect through `ws://localhost:9002`, usually via an SSH
tunnel, while the runner owns the raw bridge on 9003 during a run.

## Not Yet Verified

`scripts/deploy/bootstrap.sh` has never been run end-to-end on a fresh machine.
It was written on a host that cannot spare the disk, RAM, apt install, PX4
clone, PX4 build, or terrain-generator sync. Syntax and dry-run behavior can be
checked here, but actual provisioning remains unverified until tested on a
fresh Ubuntu 24.04 x86_64 target with adequate disk.
