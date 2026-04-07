# Expected: ros2_control Hardware Interface

## Required Elements

### 1. SystemInterface Plugin
- Must inherit from `hardware_interface::SystemInterface`
- Must implement: `on_init`, `export_state_interfaces`, `export_command_interfaces`, `read`, `write`
- `on_init` must parse URDF parameters (serial port, baud rate)
- `export_state_interfaces` must export position and velocity for all 6 joints
- `export_command_interfaces` must export position command for all 6 joints
- `read` and `write` must return `return_type::OK` or `return_type::ERROR`

### 2. Lifecycle Callbacks
- Must implement `on_configure` (open serial port)
- Must implement `on_activate` (enable motor drivers, start communication)
- Must implement `on_deactivate` (send zero velocity/hold position, disable motors)
- Must implement `on_cleanup` (close serial port)

### 3. Serial Communication
- Must open serial port with 115200 baud rate
- Must handle serial failures (return ERROR, not crash)
- Must not block indefinitely on serial read (use timeout)

### 4. URDF ros2_control Tag
- Must use `<ros2_control name="..." type="system">`
- Must reference the hardware plugin class
- Must declare 6 joints with `<command_interface>` and `<state_interface>` tags
- Must include serial port parameter in `<param>` tags

### 5. Controller Configuration YAML
- Must configure `joint_trajectory_controller` from `joint_trajectory_controller/JointTrajectoryController`
- Must list all 6 joint names
- Must specify `command_interfaces: [position]` and `state_interfaces: [position, velocity]`
- Must configure `joint_state_broadcaster`

### 6. Launch File
- Must load robot description (URDF/xacro)
- Must launch `controller_manager` with the robot description
- Must spawn controllers using `spawner` nodes
- Must sequence controller spawning (joint_state_broadcaster first, then trajectory controller)

### 7. CMake Plugin Export
- Must include `pluginlib_export_plugin_description_file` in CMakeLists.txt
- Must have a plugin XML file registering the hardware interface class
