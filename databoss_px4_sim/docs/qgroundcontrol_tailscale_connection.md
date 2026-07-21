# QGroundControl Connection Over Tailscale

Status:
Working.

Mac QGroundControl Tailscale IP:
100.109.200.5

PX4 server command:
deactivate 2>/dev/null || true
cd /opt/sim_px4/PX4-Autopilot
HEADLESS=1 make px4_sitl gz_x500

PX4 shell command:
mavlink start -m config -u 14555 -o 14550 -t 100.109.200.5 -r 1000000 -x

Validation:
mavlink status showed:
- GCS heartbeat valid
- partner IP: 100.109.200.5
- rx messages from sysid 255
- dropped packets: 0

Notes:
This PX4 build does not support `mavlink start -m normal`.
Use `-m config` for QGroundControl.
