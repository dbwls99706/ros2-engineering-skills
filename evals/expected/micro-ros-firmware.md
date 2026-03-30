# Expected: micro-ROS Firmware

## Key Elements

### Firmware Structure (rclc)
- `rclc_support_init()` with transport configuration
- `rclc_publisher_init_best_effort()` for IMU at 100 Hz (BEST_EFFORT for sensor data)
- `rclc_executor_add_timer()` for periodic publish
- Static memory allocator via `rcl_allocator_t` with pre-allocated pools

### Memory Management
- Use `micro_ros_utilities_create_static_message_memory()` or equivalent
- Pre-allocate message buffers at init (no malloc in publish loop)
- Configure `MICRO_ROS_MAX_NODES`, `MICRO_ROS_MAX_PUBLISHERS` at build time

### XRCE-DDS Agent
- Launch command: `ros2 run micro_ros_agent micro_ros_agent serial --dev /dev/ttyUSB0 -b 115200`
- Or UDP: `micro_ros_agent udp4 --port 8888`
- Agent bridges XRCE-DDS to full DDS domain

### Reconnection Handling
- Check agent availability with `rmw_uros_ping_agent()`
- On disconnect: destroy entities, re-init support, recreate publisher
- Implement exponential backoff for reconnection attempts

### Host-Side Launch
- Include micro_ros_agent node in launch file
- Set `use_sim_time:=false` explicitly for hardware
- Bridge IMU topic to standard `sensor_msgs/msg/Imu`
