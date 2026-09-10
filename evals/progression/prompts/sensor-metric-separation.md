# Sensor Metric Separation Challenge

## Scenario

This is a synthetic calibration example with four observations:

- individual LiDAR range repeatability is within a few millimeters;
- a small marker-board ROI produces a 2.6 degree fitted plane-normal difference;
- an internal commissioning heuristic uses 1.8 degrees for that plane check;
- visual point-cloud/map alignment looks repeatable from two robot positions.

Someone concludes, "The robot yaw is wrong by 2.6 degrees, so navigation is unsafe."
Someone else concludes, "The LiDAR is millimeter-accurate, so the 2.6 degree result can
be ignored."

## Question

Are either of these conclusions justified? What would you do next?
