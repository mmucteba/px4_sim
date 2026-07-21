# Phase 4A — Gazebo Ground Truth Topic Discovery

## Result

ACCEPTED.

## Goal

Find a Gazebo topic that provides true simulated vehicle pose for the PX4 Gazebo x500 model.

## Candidate topics found

Important candidates:

- /model/x500_0/odometry_with_covariance
- /world/default/dynamic_pose/info
- /world/default/pose/info

## Topic inspection

/model/x500_0/odometry_with_covariance:

- Message type: gz.msgs.OdometryWithCovariance
- Result: rejected because there were no publishers.

Observed:

No publishers on topic [/model/x500_0/odometry_with_covariance]

/world/default/dynamic_pose/info:

- Message type: gz.msgs.Pose_V
- Result: accepted as canonical ground-truth topic.

Observed sample contains:

- header stamp sec/nsec
- pose name: x500_0
- position x/y/z
- orientation x/y/z/w

/world/default/pose/info:

- Message type: gz.msgs.Pose_V
- Also usable, but includes more static/world entities such as ground_plane.
- dynamic_pose/info is cleaner for this project.

## Accepted canonical topic

/world/default/dynamic_pose/info

## Accepted model name

x500_0

## Important coordinate note

Gazebo world z is up-positive.

PX4 vehicle_local_position uses local NED convention, where z is down-positive / altitude appears negative during takeoff.

So later comparison likely needs:

PX4 estimated position:
x_px4, y_px4, z_px4

Gazebo truth converted to PX4-like local frame:
x_gt = gazebo_x
y_gt = gazebo_y
z_gt_ned = -gazebo_z

This must be validated in Phase 4C.
