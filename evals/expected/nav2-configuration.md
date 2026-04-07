# Expected: Nav2 Stack Configuration

## Required Elements

### 1. AMCL Configuration
- Must configure `amcl` node with appropriate particle filter parameters
- Must specify `robot_model_type: differential`
- Must set `laser_model_type` (likelihood_field or beam)
- Must reference the map topic and TF frames (`base_frame`, `odom_frame`, `global_frame`)

### 2. Controller Server (DWB)
- Must use `dwb_core::DWBLocalPlanner` as the controller plugin
- Must set `max_vel_x: 0.5` and `max_vel_theta: 1.0` matching robot specs
- Must include `min_vel_x` (negative for backup capability or 0.0)
- Must configure critics (GoalDist, PathDist, ObstacleFootprint or similar)

### 3. Costmap Configuration
- Global costmap: `static_layer` + `obstacle_layer` + `inflation_layer`
- Local costmap: `obstacle_layer` + `inflation_layer` (rolling window)
- Must set `robot_radius` or `footprint` matching 0.5m x 0.4m
- `inflation_radius` must be reasonable (> robot radius)
- Must reference the LiDAR topic in obstacle layer (`observation_sources`)

### 4. Planner Server
- Must configure a planner plugin (NavfnPlanner, SmacPlanner2D, or ThetaStarPlanner)
- Must set `tolerance` for goal reaching

### 5. Behavior Tree Navigator
- Must reference a BT XML file or use default `navigate_w_replanning_and_recovery.xml`
- Must configure recovery behaviors: spin, backup, wait
- Must set appropriate recovery timeout values

### 6. Launch File
- Must launch `nav2_bringup` or individual Nav2 nodes
- Must load parameters from the YAML configuration
- Must accept `map` argument for the map file path
- Must use `use_sim_time` parameter

### 7. TF Frame Conventions
- Must use standard frames: `map`, `odom`, `base_link`, `base_scan`/`laser`
- Frame names must be consistent across all configurations
