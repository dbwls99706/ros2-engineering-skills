# ros2_control Hardware Interface

## Scenario

Create a ros2_control hardware interface plugin for a 6-DOF robotic arm that communicates over serial (UART). The robot uses ROS 2 Jazzy.

## Requirements

- Implement a `SystemInterface` plugin for the 6-joint arm
- Communicate with the arm over serial at 115200 baud
- Read joint positions and velocities from the hardware
- Write position commands to the hardware
- Handle serial connection failures gracefully (return ERROR from read/write)
- Include the URDF `<ros2_control>` tag configuration
- Include a controller configuration YAML for `joint_trajectory_controller`
- Target distribution: Jazzy

## Question

Provide the hardware interface implementation (header + source), URDF ros2_control snippet, controller config YAML, and a launch file. Explain the lifecycle integration.
