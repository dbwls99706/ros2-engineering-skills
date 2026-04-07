# Python Package Creation

## Scenario

Create a ROS 2 Jazzy Python package for a sensor data aggregator node called `sensor_aggregator`.

## Requirements

- The node must subscribe to `/imu/data` (sensor_msgs/Imu) and `/gps/fix` (sensor_msgs/NavSatFix)
- It should publish fused data on `/aggregated_pose` (geometry_msgs/PoseStamped)
- Use `qos_profile_sensor_data` for sensor subscriptions
- Declare parameters: `publish_rate` (double, default 10.0), `use_gps` (bool, default true)
- Include a launch file with parameter overrides
- Include pytest-based tests
- Target distribution: Jazzy

## Question

Generate the complete Python package structure with all necessary files.
Show the file tree and key file contents.
