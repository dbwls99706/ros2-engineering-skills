# SROS2 Security Configuration Challenge

## Scenario

You are setting up a production ROS 2 system (Jazzy) that controls a warehouse robot arm.
The system has these nodes:
- `/arm_controller` — sends joint commands
- `/camera_driver` — publishes RGB images
- `/safety_monitor` — subscribes to all topics and triggers e-stop

Requirements:
1. Generate SROS2 security artifacts (keystore, enclaves, policies)
2. Configure DDS security plugins (authentication, access control, cryptography)
3. Ensure `/safety_monitor` has subscribe-only access (no publish permissions except `/e_stop`)
4. Protect against unauthorized node injection

## Question

What is the correct setup? Show the SROS2 CLI commands, policy XML, and launch file integration.
