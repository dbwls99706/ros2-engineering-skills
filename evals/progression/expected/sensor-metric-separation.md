# Expected: Sensor Metric Separation

## Required elements

### Distinguish the physical quantities
- Recognize the prompt as a synthetic fixture, not a measured field result.
- Separate individual LiDAR range precision, local plane-normal fit, marker/PnP pose
  uncertainty, camera-LiDAR extrinsic error, cloud-map registration residual, and
  global robot pose/yaw error.
- State that these quantities can constrain one another only through an explicit
  error/uncertainty model; they are not interchangeable acceptance metrics.

### Reject both unsupported conclusions
- Do not relabel a 2.6 degree plane-normal difference as 2.6 degrees of robot yaw.
- Do not use millimeter range repeatability to dismiss orientation uncertainty from a
  small/narrow ROI.
- Do not make a navigation safe/unsafe conclusion from that single metric alone.

### Preserve operator observations at their real strength
- Treat repeatable visual alignment from different positions as useful qualitative or
  repeatability evidence when timestamp, pose/session, and scene are recorded.
- Do not promote it to independent survey-grade accuracy without a calibrated reference.

### Design discriminating measurements
- Vary range, target orientation/viewing angle, robot pose, or geometry to test plane-fit
  observability and extrinsic sensitivity.
- Use independent reprojection/registration/pose-reference evidence as appropriate.
- Define the pass/fail criterion and identify which downstream claim it supports.
