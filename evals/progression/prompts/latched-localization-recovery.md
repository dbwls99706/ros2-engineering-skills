# Latched Localization Recovery Challenge

## Scenario

A ROS 2 navigation gateway lost its vendor localization feed while the robot was
moving. The state machine entered `LOST` and invalidated its continuity proof. A few
seconds later the vendor pose is fresh and stable again, but the gateway remains
latched in `LOST`. The previous navigation goal still exists in memory.

The operator asks why the system is still blocked and whether the old goal can just
resume now that the pose is fresh.

## Question

Explain the current state without describing the old failure as if it were still a
fresh observation. Design a bounded recovery path, including what must be verified
before clearing or transitioning the latch and what should happen to the pre-loss
navigation command.
