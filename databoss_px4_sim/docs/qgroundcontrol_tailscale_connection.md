# QGroundControl Tailscale Connection

QGroundControl is a viewer/operator monitor for DATABOSS runs. The runner does
not depend on manual QGC actions.

Configure the QGC destination with:

```bash
export DATABOSS_QGC_IP=<qgc-host-ip>
```

For a local-only deployment, use:

```bash
export DATABOSS_QGC_IP=127.0.0.1
```

PX4/QGC UDP convention:

```text
PX4 local UDP port:  14555
QGC remote UDP port: 14550
```

The runner starts the PX4 MAVLink stream in this shape:

```bash
mavlink start -m config -u 14555 -o 14550 -t "$DATABOSS_QGC_IP" -r 1000000 -x
```

For dashboard binding on the same host, set:

```bash
export DATABOSS_DASHBOARD_HOST=127.0.0.1
```

For LAN or tailnet access, set `DATABOSS_DASHBOARD_HOST` to the address the
dashboard should bind and `DATABOSS_QGC_IP` to the machine running QGC.

See `docs/DEPLOYMENT.md` for the complete deployment wiring, including
`DATABOSS_PX4_ROOT` and `DATABOSS_PX4_PINS_PATH`.
