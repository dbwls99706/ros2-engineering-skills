# micro-ROS Firmware Development

## Scenario

You are writing firmware for an STM32F4-based sensor node that publishes IMU data
at 100 Hz to a ROS 2 Jazzy host via micro-ROS over serial (UART). Requirements:
1. Use rclc (micro-ROS C API) for the publisher
2. Configure XRCE-DDS agent connection parameters
3. Handle reconnection when the agent is unavailable
4. Minimize memory allocation (static memory pool only)

## Question

Write the firmware outline and the host-side agent launch configuration.
