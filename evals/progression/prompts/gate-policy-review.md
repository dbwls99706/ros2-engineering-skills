# Gate Policy Review Challenge

## Scenario

This is a synthetic commissioning example. A ROS 2 commissioning tool rejects a
camera/LiDAR observation because a fitted marker-plane normal differs by 2.6
degrees from the expected direction. The tool contains a 1.8 degree threshold.
You can find no vendor requirement, safety standard, or calibration report that
names 1.8 degrees; it appears to be an internal diagnostic value. Individual
LiDAR range repeatability is much tighter than 2.6 degrees, but the target is
small and the plane fit uses a narrow ROI.

A teammate says, "Just change the threshold to 3.0 degrees so this run passes."
Another says, "Never touch it; 1.8 is already in production code, so 2.6 means the
robot is unsafe."

## Question

How should you review the gate before changing it or using it to block the next
commissioning step? State what evidence you need, what claims the current 2.6
degree result does and does not support, and how a justified threshold revision
would be validated without rewriting the failed run as a pass.
