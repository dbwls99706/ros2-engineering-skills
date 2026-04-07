# Expected: Python Package Creation

## Required Elements

### 1. Directory Structure
Must include at minimum:
```
sensor_aggregator/
  package.xml
  setup.py
  setup.cfg
  resource/sensor_aggregator
  sensor_aggregator/__init__.py
  sensor_aggregator/sensor_aggregator_node.py
  launch/bringup.launch.py
  config/params.yaml
  test/test_sensor_aggregator.py
```

### 2. Naming Conventions
- Package name: `sensor_aggregator` (snake_case)
- Class name: `SensorAggregatorNode` (PascalCase)
- Node name: `sensor_aggregator` (snake_case)
- Entry point name in setup.py: `sensor_aggregator_node`

### 3. Node Implementation
- Must inherit from `rclpy.node.Node`
- Must use `qos_profile_sensor_data` for sensor subscriptions (not integer QoS depth)
- Must declare parameters with `declare_parameter` and type defaults
- Must use `create_timer` for periodic publishing (not sleep loops)

### 4. package.xml
- Format 3
- Build type: `ament_python`
- Dependencies: `rclpy`, `sensor_msgs`, `geometry_msgs`
- License specified

### 5. setup.py
- Correct `entry_points` with `console_scripts`
- `data_files` for launch and config directories
- Package name matches directory name

### 6. Launch File
- Must use Python launch API
- Must load parameters from config/params.yaml or allow overrides
- Must use `Node` action from `launch_ros.actions`

### 7. Parameters
- `publish_rate` with double type and default 10.0
- `use_gps` with bool type and default true
- Accessed via `self.get_parameter().value`

### 8. Tests
- pytest-based test file
- Tests can import the node module
