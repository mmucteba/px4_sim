# Phase 3B - GNSS Loss With SITL-Only Failsafe Delay

Goal:
Allow the drone to continue flying for a short controlled period after simulated GNSS loss so EKF drift can be measured.

Problem found in Phase 3A:
SIM_GPS_USED=0 correctly invalidates GPS, but PX4 immediately enters failsafe blind land.

Reason:
When GNSS becomes invalid:
- GPS fix_type changes to 0
- satellites_used changes to 0
- eph changes to 100
- epv changes to 100

Relevant current parameters:
- COM_POS_FS_EPH = 5.0
- COM_POS_LOW_ACT = 3
- EKF2_NOAID_TOUT = 5000000 us
- COM_ARM_WO_GPS = 1
- EKF2_GPS_CTRL = 7

SITL-only Phase 3B parameter changes:
- COM_POS_FS_EPH: 5 -> 200
- COM_POS_LOW_ACT: 3 -> 0
- EKF2_NOAID_TOUT: 5000000 -> 120000000

Purpose of changes:
- Do not immediately blind-land when GPS EPH jumps to 100
- Allow EKF no-aiding behavior to continue longer
- Keep this only for simulation research, not real flight

GNSS loss method:
param set SIM_GPS_USED 0

Restore method:
param set SIM_GPS_USED 10
