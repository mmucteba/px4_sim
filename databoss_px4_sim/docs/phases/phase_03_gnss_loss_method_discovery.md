# Phase 3 - GNSS Loss Method Discovery

Goal:
Find a GNSS-loss method that visibly changes PX4 logged GPS data.

Rejected method:
failure gps off

Reason:
PX4 accepted the command, but vehicle_gps_position stayed healthy:
- fix_type stayed 3
- satellites_used stayed 10
- eph stayed about 0.9
- epv stayed about 1.78

Accepted method:
param set SIM_GPS_USED 0

Evidence:
- fix_type changed from 3 to 0
- satellites_used changed from 10 to 0
- eph changed from about 0.9 to 100
- epv changed from about 1.78 to 100
- PX4 warned GPS fix was too low

Restore method:
param set SIM_GPS_USED 10

Conclusion:
For this PX4/Gazebo setup, GNSS loss will be simulated using SIM_GPS_USED=0.
