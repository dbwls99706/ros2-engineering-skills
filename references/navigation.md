# Navigation (Nav2)

## Table of contents

1. Nav2 architecture overview
2. SLAM integration
3. Costmap configuration
4. Behavior tree navigator
5. Planner and controller plugins
6. Controller server internals and goal checking
7. Recovery behaviors
8. Waypoint following
9. Multi-robot navigation
10. Parameter tuning methodology
11. Common failures and fixes

---

## 1. Nav2 architecture overview

```text
                    ┌─────────────────┐
                    │   BT Navigator   │ ← Behavior tree orchestrates navigation
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌──────────────┐ ┌──────────┐ ┌──────────────┐
     │    Planner    │ │Controller│ │  Behavior     │
     │    Server     │ │  Server  │ │  Server       │
     │ (global path) │ │ (cmd_vel)│ │ (stuck help)  │
     └──────┬───────┘ └────┬─────┘ └──────────────┘
            │              │
            ▼              ▼
     ┌─────────────────────────────────┐
     │         Costmap 2D              │
     │  (global + local costmaps)      │
     └────────────┬────────────────────┘
                  │
     ┌────────────▼────────────────────┐
     │   Sensor Data (LiDAR, depth)    │
     │   + TF (map → odom → base)     │
     └─────────────────────────────────┘
```

**Key lifecycle nodes (all managed):**

- `bt_navigator` — orchestrates navigation tasks via behavior trees
- `planner_server` — computes global paths
- `controller_server` — generates velocity commands to follow paths
- `behavior_server` (Humble+; pre-Humble Nav2 used `recoveries_server` — renamed in the Galactic → Humble migration) -- handles stuck situations (wait, clear, opt-in motion behaviors)
- `smoother_server` — smooths planned paths (optional)
- `waypoint_follower` — executes multi-waypoint missions
- `velocity_smoother` — smooths `cmd_vel` output (optional)
- `docking_server` (Jazzy+) — automated docking/undocking with charging stations
- `route_server` (Kilted) — graph-based route planning for large-scale environments
- `loopback_simulator` (Kilted) — lightweight sim that feeds Nav2 outputs back as inputs (no Gazebo needed, useful for CI/testing)

### Nav2 feature: distro comparison

| Feature | Humble | Jazzy | Kilted |
|---|---|---|---|
| BehaviorTree.CPP version | v3 | v3 → v4 transition (XML port remapping syntax changed) | v4 |
| `cmd_vel` message type | `geometry_msgs/Twist` | `Twist` (default), `TwistStamped` opt-in via `enable_stamped_cmd_vel: true` | **`TwistStamped` default**; set `enable_stamped_cmd_vel: false` for backward compat |
| Behavior server | `behavior_server` + `behavior_plugins` (**renamed in Humble** — pre-Humble used `recoveries_server` + `recovery_plugins`) | `behavior_server` (same as Humble) | `behavior_server` (same as Humble) |
| Behavior plugin namespace | `nav2_behaviors/` (**renamed in Humble** — pre-Humble used `nav2_recoveries/`) | `nav2_behaviors::` | `nav2_behaviors::` |
| Plugin type separator | `/` for most types, e.g. `nav2_behaviors/Spin` on Humble/Iron (some, like `dwb_core::DWBLocalPlanner`, already used `::`) | **`::` everywhere on Jazzy+**, e.g. `nav2_behaviors::Spin` (Iron and older use `/`) | `::` |
| Docking server | Not available | **New**: `docking_server` | Enhanced: non-charging docks, RViz panel |
| Route server | Not available | Not available | **New**: graph-based route planning |
| Loopback simulator | Not available | Not available | **New**: `nav2_loopback_sim` (no Gazebo needed) |
| MPPI controller | Available | Available | **Rewritten** with Eigen (40–50% faster, ARM support), new trajectory validator plugin |
| Collision monitor | Available | Enhanced (velocity polygon, dynamic reconfig) | Available + toggle service |
| Error code propagation | `error_code_names` param | `error_code_names` param | **Replaced**: `error_code_name_prefixes` (old param causes runtime exception) |
| Nav2 node interface | `nav2_util::LifecycleNode` | `nav2_util::LifecycleNode` | **New**: `nav2::LifecycleNode` from `nav2_ros_common` (all plugins must migrate) |
| BT new nodes | — | — | `GetPoseFromPath`, `RemoveInCollisionGoals`, `IsStopped`, `NonblockingSequence`, `PersistentSequence` |

**Migration path:**

- **Galactic → Humble**: Rename `recoveries_server`→`behavior_server`, `recovery_plugins`→`behavior_plugins`, and plugin namespaces `nav2_recoveries/`→`nav2_behaviors/`. Configs copied from pre-Humble examples silently break on Humble: `nav2_recoveries` does not exist there.
- **Humble → Jazzy**: Migrate custom BT XMLs if they use BT.CPP v3 syntax (v3 → v4 transition)
- **Jazzy → Kilted**: Set `enable_stamped_cmd_vel: true` on robot subscriber (or it won't receive cmd_vel), replace `error_code_names` with `error_code_name_prefixes`, migrate plugins to `nav2::LifecycleNode` with factory methods

### Minimal Nav2 launch

```python
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare('nav2_bringup'),
                'launch', 'navigation_launch.py',
            ])
        ),
        launch_arguments={
            'use_sim_time': 'true',
            'params_file': PathJoinSubstitution([
                FindPackageShare('my_robot_navigation'),
                'config', 'nav2_params.yaml',
            ]),
        }.items(),
    )
    return LaunchDescription([nav2_launch])
```

## 2. SLAM integration

Start any LiDAR-inertial (LIO) mapper with the robot stationary and level for
its initialization window: the first IMU samples define the gravity direction
for the whole map. A map started while driving was tilted 3.6° in one field
run; the same route started from rest was within 0.22°. Check the mapper's
startup log for its gravity/initialization message before the first motion.

### slam_toolbox (recommended)

```yaml
# slam_toolbox_params.yaml
slam_toolbox:
  ros__parameters:
    solver_plugin: solver_plugins::CeresSolver
    ceres_linear_solver: SPARSE_NORMAL_CHOLESKY
    ceres_preconditioner: SCHUR_JACOBI

    # Map parameters
    resolution: 0.05               # meters per pixel
    max_laser_range: 20.0           # meters

    # Update thresholds
    minimum_travel_distance: 0.5    # meters before updating map
    minimum_travel_heading: 0.5     # radians before updating map

    # Mode: mapping (online) or localization (use existing map)
    mode: mapping                   # or "localization"

    # Scan matching
    scan_topic: /scan
    use_scan_matching: true
    use_scan_barycenter: true

    # Map save
    map_file_prefix: my_map
    map_start_at_dock: true
```

### Launch with SLAM

```python
Node(
    package='slam_toolbox',
    executable='async_slam_toolbox_node',
    name='slam_toolbox',
    parameters=[slam_params_file, {'use_sim_time': True}],
),
```

### Saving and loading maps

```bash
# Save map during SLAM
ros2 run nav2_map_server map_saver_cli -f ~/maps/my_map

# Serve a saved map for localization
ros2 run nav2_map_server map_server --ros-args \
  -p yaml_filename:=~/maps/my_map.yaml \
  -p use_sim_time:=true
```

### AMCL localization (known maps)

AMCL (Adaptive Monte Carlo Localization) is the standard particle filter localization for navigating in pre-built maps:

```yaml
amcl:
  ros__parameters:
    use_sim_time: true
    alpha1: 0.2   # rotation noise from rotation
    alpha2: 0.2   # rotation noise from translation
    alpha3: 0.2   # translation noise from translation
    alpha4: 0.2   # translation noise from rotation
    base_frame_id: "base_link"
    global_frame_id: "map"
    odom_frame_id: "odom"
    max_particles: 2000
    min_particles: 500
    robot_model_type: "nav2_amcl::DifferentialMotionModel"
    scan_topic: scan
    tf_broadcast: true
    set_initial_pose: true
    initial_pose:
      x: 0.0
      y: 0.0
      yaw: 0.0
```

When to use AMCL vs SLAM:

| Scenario | Use |
|---|---|
| Known, static environment | AMCL with pre-built map |
| Unknown environment | slam_toolbox (online SLAM) |
| Semi-dynamic environment | slam_toolbox in localization mode |
| Outdoor with GPS | robot_localization EKF + NavSat |

## 3. Costmap configuration

### Global vs local costmap

| Property | Global costmap | Local costmap |
|---|---|---|
| Size | Entire map | Rolling window around robot |
| Purpose | Path planning (A* / Dijkstra) | Local obstacle avoidance |
| Update rate | Slow (1–5 Hz) | Fast (5–20 Hz) |
| Typical width | Map size | 3–6 meters |
| Resolution | 0.05 m | 0.05 m |

### Costmap layers configuration

```yaml
global_costmap:
  global_costmap:
    ros__parameters:
      update_frequency: 1.0
      publish_frequency: 1.0
      global_frame: map
      robot_base_frame: base_link
      use_sim_time: true
      robot_radius: 0.22
      resolution: 0.05
      plugins: ["static_layer", "obstacle_layer", "inflation_layer"]

      static_layer:
        plugin: "nav2_costmap_2d::StaticLayer"
        map_subscribe_transient_local: true

      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        enabled: true
        observation_sources: scan
        scan:
          topic: /scan
          max_obstacle_height: 2.0
          clearing: true
          marking: true
          data_type: "LaserScan"
          raytrace_max_range: 5.0
          raytrace_min_range: 0.0
          obstacle_max_range: 4.5
          obstacle_min_range: 0.0

      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 3.0     # Higher = cost drops faster (exponential decay)
        inflation_radius: 0.55       # Must be > robot_radius

# Inflation cost formula (exponential decay):
#   cost(d) = INSCRIBED_INFLATED_OBSTACLE * e^(-cost_scaling_factor * (d - inscribed_radius))
#
# where d = distance from obstacle, inscribed_radius = robot_radius
#
# Key insight: doubling cost_scaling_factor does NOT double the safe zone.
# It makes the cost DROP FASTER, meaning the robot navigates CLOSER to obstacles.
#   cost_scaling_factor=1.0 → gradual falloff, robot stays far from walls
#   cost_scaling_factor=5.0 → steep falloff, robot cuts close to obstacles
#   cost_scaling_factor=10.0 → almost binary (lethal vs free), robot hugs walls
#
# Tune inflation_radius first (sets the maximum inflation range), then
# adjust cost_scaling_factor to control how aggressively cost decays within
# that range.

local_costmap:
  local_costmap:
    ros__parameters:
      update_frequency: 5.0
      publish_frequency: 2.0
      global_frame: odom
      robot_base_frame: base_link
      rolling_window: true
      width: 3
      height: 3
      resolution: 0.05
      plugins: ["voxel_layer", "inflation_layer"]

      voxel_layer:
        plugin: "nav2_costmap_2d::VoxelLayer"
        enabled: true
        observation_sources: scan
        scan:
          topic: /scan
          max_obstacle_height: 2.0
          clearing: true
          marking: true
          data_type: "LaserScan"

      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 3.0
        inflation_radius: 0.55
```

## 4. Behavior tree navigator

Nav2 uses BehaviorTree.CPP to orchestrate navigation. The default BT handles
planning, following, and recovery.

**Kilted migration note:** Nav2 moved from BT.CPP v3 to v4 starting in Jazzy. BT.CPP v4 changes the XML format (e.g., port remapping syntax). A conversion script is provided in the Nav2 repo (`tools/bt_converter.py`) to migrate custom BT XMLs.

### Default navigation BT flow

```text
NavigateRecovery
├── NavigateWithReplanning
│   ├── ComputePathToPose → Planner Server
│   ├── FollowPath → Controller Server
│   └── (replans on failure or rate timer)
└── RecoveryFallback
    ├── ClearCostmapExceptLastResort
    ├── Spin
    ├── Wait
    └── BackUp
```

Note: the stock BT includes **motion recoveries** (Spin, BackUp). Before
deploying it on a real robot, read the recovery escalation ladder in
section 7 — motion recoveries should stay disabled until validated on the
actual platform.

### Custom behavior tree

```xml
<!-- my_nav_bt.xml -->
<root main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <RecoveryNode number_of_retries="3" name="NavigateRecovery">
      <PipelineSequence name="NavigateWithReplanning">
        <RateController hz="1.0">
          <ComputePathToPose goal="{goal}" path="{path}" planner_id="GridBased"/>
        </RateController>
        <FollowPath path="{path}" controller_id="FollowPath"/>
      </PipelineSequence>
      <ReactiveFallback name="RecoveryFallback">
        <GoalUpdated/>
        <SequenceStar>
          <Wait wait_duration="5"/>
          <ClearEntireCostmap name="ClearLocalCostmap"
            service_name="local_costmap/clear_entirely_local_costmap"/>
          <ClearEntireCostmap name="ClearGlobalCostmap"
            service_name="global_costmap/clear_entirely_global_costmap"/>
          <!-- Motion recoveries are opt-in. Enable ONLY after validating
               robot geometry, locomotion response, and clearance at the
               stuck locations (see the recovery escalation ladder, sec. 7):
          <Spin spin_dist="1.57"/>
          <BackUp backup_dist="0.30" backup_speed="0.05"/>
          -->
        </SequenceStar>
      </ReactiveFallback>
    </RecoveryNode>
  </BehaviorTree>
</root>
```

### Using custom BT in config

```yaml
bt_navigator:
  ros__parameters:
    # Galactic+ (including Humble) parameter names. Leave them unset to load
    # the package default BT that ships with nav2_bt_navigator.
    # default_nav_to_pose_bt_xml: /path/to/my_nav_bt.xml
    # default_nav_through_poses_bt_xml: /path/to/my_nav_through_poses_bt.xml
    plugin_lib_names:
      - nav2_compute_path_to_pose_action_bt_node
      - nav2_follow_path_action_bt_node
      - nav2_spin_action_bt_node
      - nav2_wait_action_bt_node
      - nav2_back_up_action_bt_node
      - nav2_clear_costmap_service_bt_node
      - nav2_rate_controller_bt_node
      - nav2_pipeline_sequence_bt_node
      - nav2_recovery_node_bt_node
      - nav2_goal_updated_bt_node
```

**Parameter-name trap:** `default_bt_xml_filename` is the **pre-Galactic**
(Foxy and earlier) parameter name. On Humble and newer it is not declared —
a config that sets it is silently ignored and the robot keeps running the
package default BT while you believe your custom tree is active. Use the
Galactic+ names above.

**Which default BT actually runs?** When the parameter is unset, the default
tree ships with the `nav2_bt_navigator` package — on a Humble install this is
typically `navigate_to_pose_w_replanning_and_recovery.xml`. Do not trust the
name from memory; list the installed trees and read the one your version uses:

```bash
ls "$(ros2 pkg prefix nav2_bt_navigator)/share/nav2_bt_navigator/behavior_trees/"
```

## 5. Planner and controller plugins

**Plugin type separator:** Nav2 parameter files should use `::` in plugin
type strings on Jazzy and newer. Iron and older configurations may use `/`;
some plugin types, such as `dwb_core::DWBLocalPlanner`, already used `::` on
older distributions. Copying a `/`-style string into a Jazzy+ config (or the
reverse) fails at plugin load — verify against the installed
`nav2_params.yaml` (source-first, section 6).

### Planner plugins

| Plugin | Algorithm | Best for |
|---|---|---|
| `NavfnPlanner` | Dijkstra / A* | Simple environments, guaranteed optimal |
| `SmacPlannerHybrid` | Hybrid-A* | Non-holonomic robots (cars, Ackermann) |
| `SmacPlanner2D` | A* on 2D grid | Holonomic robots, fast planning |
| `SmacPlannerLattice` | State lattice | Complex kinematic constraints |
| `ThetaStarPlanner` | Theta* | Any-angle planning, smoother paths |

```yaml
planner_server:
  ros__parameters:
    planner_plugins: ["GridBased"]
    GridBased:
      # Humble/Iron syntax; Jazzy+ uses "nav2_navfn_planner::NavfnPlanner"
      plugin: "nav2_navfn_planner/NavfnPlanner"
      tolerance: 0.5
      use_astar: true
      allow_unknown: true
```

### Controller plugins

| Plugin | Method | Best for |
|---|---|---|
| `DWBLocalPlanner` | Dynamic Window | General purpose, differential drive |
| `RegulatedPurePursuitController` | Pure pursuit | Smooth, regulated tracking |
| `MPPIController` | Model Predictive | Complex dynamics, obstacle avoidance (Kilted: reimplemented with Eigen, 40-50% faster, adds ARM support) |
| `RotationShimController` | Wraps other controllers | Rotate in place before following path |

```yaml
controller_server:
  ros__parameters:
    controller_frequency: 20.0
    controller_plugins: ["FollowPath"]
    FollowPath:
      plugin: "dwb_core::DWBLocalPlanner"
      min_vel_x: 0.0
      min_vel_y: 0.0
      max_vel_x: 0.5
      max_vel_y: 0.0          # 0 for diff-drive
      max_vel_theta: 1.0
      min_speed_xy: 0.0
      max_speed_xy: 0.5
      # Kilted: TwistStamped is now the default cmd_vel type.
      # Set enable_stamped_cmd_vel: true in your controller config.
      # Humble/Jazzy used geometry_msgs/Twist by default.
      acc_lim_x: 2.5
      acc_lim_y: 0.0
      acc_lim_theta: 3.2
      decel_lim_x: -2.5
      decel_lim_y: 0.0
      decel_lim_theta: -3.2
```

## 6. Controller server internals and goal checking

When a navigation failure only reproduces on the real robot ("it suddenly
spins the wrong way", "it never finishes the final rotation"), the cause is
often controller-server behavior that the parameter reference does not spell
out. The behaviors below were checked against Nav2 Humble sources — but patch
releases change details, so re-verify against **your installed version**
(see "Source-first verification" at the end of this section) before relying
on them for a diagnosis.

### DWB minimum-speed validity is OR, not AND

DWB's velocity iterator (`XYThetaIterator`, via
`KinematicParameters::isValidSpeed`) accepts a candidate velocity when
translational speed ≥ `min_speed_xy` **OR** |angular speed| ≥
`min_speed_theta` — not AND. A candidate is rejected only when it fails
*both* minimums. Consequences:

- Raising `min_speed_theta` does NOT reduce the yaw candidates to a single
  legal value: mixed candidates with enough translational speed still pass
  through the OR branch. A diagnosis like "the only valid yaw candidate is
  0.48 rad/s" is wrong if it assumed AND semantics.
- When diagnosing "why did DWB pick this velocity", enumerate the sampled
  window (`vx_samples`, `vtheta_samples`) against the OR rule instead of
  assuming which candidates were pruned.

### Goal checker reset and the stateful XY latch

- The controller server owns the goal-checker plugins. In the Humble sources
  verified for this guide, the selected goal checker is **reset when a new
  `FollowPath` action goal starts**. If your BT replaces the FollowPath goal
  while replanning, that reset repeats with every replacement.
- `SimpleGoalChecker` is **stateful** by default (`stateful: true`): once the
  XY tolerance is met it latches and only checks yaw afterwards, so the robot
  may drift outside `xy_goal_tolerance` while finishing the final rotation.
  Every goal-checker reset clears that latch.
- The combination — a replanning BT that replaces FollowPath goals plus a
  latch that each replacement clears — shows up on hardware as a robot that
  repeatedly re-approaches the XY goal and never settles the final yaw.
  Confirm on your installed version whether goal replacement occurs and
  whether the latch survives it before tuning tolerances around the symptom.

### RotationShim hands off the final yaw

`RotationShimController`'s primary role is aligning the **initial** heading of
a newly received path: it rotates until the heading error drops below
`angular_dist_threshold`, then hands control to the primary controller. Do
not assume it also completes the final goal yaw — that responsibility sits
with the primary controller and the goal checker. Options differ between
releases (some add goal-heading behavior), so confirm on your installed
version which component owns the last rotation before tuning it.

### Verify what actually reaches the hardware

The command a controller computes is not the command the robot executes.
Trace the full output pipeline:

```text
controller plugin
  → controller_server output topic
  → velocity_smoother        (accel/decel limits, deadband)
  → collision monitor / twist mux
  → vendor bridge / SDK driver
  → hardware command
```

When motion doesn't match expectation, echo the input and output topic of
each stage and compare both values and timestamps:

```bash
ros2 topic echo /cmd_vel_nav --once   # controller output (topic names vary per bringup)
ros2 topic echo /cmd_vel --once       # after smoother / collision monitor
ros2 topic info /cmd_vel -v           # who actually publishes and subscribes
```

This separates "Nav2 commanded this rotation" from "a downstream stage
transformed the rotation" — two failures with identical symptoms and
different fixes.

Two follow-ups this trace does not cover on its own:

- **Command topics with more than one publisher.** `ros2 topic info -v` reports
  the count. Without an arbiter the subscriber can process commands from every
  compatible publisher, and there is no defined command priority on the topic
  itself. The end-to-end verification of a command actually reaching (and
  stopping) the hardware is in `references/safety-estop.md` §3.
- **Which config each stage actually loaded.** A smoother or collision monitor
  reading an older installed YAML explains a "wrong" transformation that no
  amount of parameter tuning fixes — audit with
  `references/runtime-provenance.md`.

### Source-first verification

Distro labels are not enough when exact behavior matters. Before asserting
how *your* Nav2 behaves, identify the installed version and read the
artifacts that ship with it:

```bash
ros2 pkg prefix nav2_bringup           # install prefix
ros2 pkg xml nav2_bringup              # read the <version> element
dpkg-query -W 'ros-humble-nav2-*'      # package versions on apt-based installs
```

Then check the installed files, not the docs you remember:

- Reference params: `/opt/ros/<distro>/share/nav2_bringup/params/nav2_params.yaml`
- Default behavior trees: `$(ros2 pkg prefix nav2_bt_navigator)/share/nav2_bt_navigator/behavior_trees/`
- Installed headers under `/opt/ros/<distro>/include/`, and for exact logic
  the GitHub source at the tag matching the installed version.

## 7. Recovery behaviors

```yaml
# Humble and newer: behavior_server + behavior_plugins.
# (Pre-Humble Nav2 examples use legacy naming that does not exist on Humble.)
behavior_server:
  ros__parameters:
    behavior_plugins: ["wait", "spin", "backup"]
    wait:
      plugin: "nav2_behaviors/Wait"    # Humble/Iron; Jazzy+: "nav2_behaviors::Wait"
    # Motion behaviors: declare them, but keep them out of your BT's
    # recovery sequence until validated on the actual robot (see below).
    spin:
      plugin: "nav2_behaviors/Spin"    # Humble/Iron; Jazzy+: "nav2_behaviors::Spin"
    backup:
      plugin: "nav2_behaviors/BackUp"  # Humble/Iron; Jazzy+: "nav2_behaviors::BackUp"
```

### Recovery escalation ladder — actuation-free first

Choose recoveries in escalating order of risk. Motion recoveries are a
last resort, not a default.

1. **Wait / re-sense** — no actuation; lets dynamic obstacles pass and
   sensors re-observe the scene.
2. **Targeted costmap clearing** (`ClearCostmapExceptRegion`, or resetting a
   specific layer) — no actuation, but **not automatically safe**: clearing
   can erase *real* obstacles, and the next plan may drive through where
   they were. Only clear what the robot can re-observe before traversing.
3. **Full costmap clear** — same caveat with a larger blast radius.
4. **Motion recoveries** (`Spin`, `BackUp`, `DriveOnHeading`) — commanded
   movement at exactly the moment the planner is already confused. Keep
   disabled until validated with the checklist below.

**Before enabling Spin/BackUp on a real robot, verify:**

- Robot geometry — does the platform actually rotate in place within its
  footprint? Legged robots sweep more than their static footprint.
- Locomotion response — how does the gait react to a pure rotation command
  at the commanded rate?
- Clearance at the locations where the robot actually gets stuck.
- `spin_dist` / `backup_dist` / `backup_speed` against measured clearance.

A stock `+1.57 rad` Spin executed on an unvalidated quadruped is a known
field failure: the operator sees the robot "suddenly rotate in a random
direction" — the recovery behavior, not path following, was the fault.

### Dynamic obstacle handling

Nav2's costmap handles static and semi-static obstacles well, but fast-moving
obstacles (humans walking at 1.5 m/s, forklifts) require additional strategies:

| Strategy | When to use | Implementation |
|---|---|---|
| **Higher costmap update rate** | Moderate dynamics (warehouse) | `local_costmap.update_frequency: 10-20` Hz |
| **Collision monitor** | Last-line safety (see below) | Separate node, geometry-based |
| **MPPI controller** | Predictive avoidance | `MPPIController` with obstacle cost critic |
| **Costmap filters** | Known dynamic zones | `KeepoutFilter` for exclusion zones |
| **People tracking** | Dense crowds | External tracker → costmap layer via plugin |

**Limitation:** The standard `ObstacleLayer` uses a clearing/marking model that
assumes obstacles are static between scans. Fast-moving objects can leave "ghost
trails" in the costmap. For populated environments, consider:

1. Reducing `obstacle_max_range` to limit stale markings
2. Using the `VoxelLayer` with 3D clearing
3. Adding a people-tracking costmap layer

### Nav2 Collision Monitor

The collision monitor (Humble+, significantly enhanced in Jazzy) is an independent safety node that monitors sensor
data and **directly overrides `cmd_vel`** — it is NOT just a warning system. When
an obstacle enters the stop polygon, the monitor publishes zero velocity regardless
of what the controller commands. This provides a last-line-of-defense safety layer
independent of the costmap and controller.

```yaml
collision_monitor:
  ros__parameters:
    base_frame_id: "base_link"
    odom_frame_id: "odom"
    transform_tolerance: 0.5
    source_timeout: 2.0
    stop_pub_timeout: 2.0
    polygons: ["PolygonStop", "PolygonSlow"]
    PolygonStop:
      type: "polygon"
      points: [0.4, 0.3, 0.4, -0.3, -0.1, -0.3, -0.1, 0.3]
      action_type: "stop"
      max_points: 3
      visualize: true
    PolygonSlow:
      type: "circle"
      radius: 0.7
      action_type: "slowdown"
      max_points: 3
      slowdown_ratio: 0.5
    observation_sources: ["scan"]
    scan:
      source_timeout: 2.0
      type: "scan"
      topic: "/scan"
```

## 8. Waypoint following

### Sending waypoints programmatically (Python)

```python
from math import sin, cos
from nav2_simple_commander.robot_navigator import BasicNavigator
from geometry_msgs.msg import PoseStamped
import rclpy

def main():
    rclpy.init()
    navigator = BasicNavigator()

    # Wait for Nav2 to be active
    navigator.waitUntilNav2Active()

    # Define waypoints
    waypoints = []
    for (x, y, yaw) in [(1.0, 0.0, 0.0), (2.0, 1.0, 1.57), (0.0, 0.0, 3.14)]:
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = navigator.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = sin(yaw / 2)
        pose.pose.orientation.w = cos(yaw / 2)
        waypoints.append(pose)

    navigator.followWaypoints(waypoints)

    while not navigator.isTaskComplete():
        feedback = navigator.getFeedback()
        if feedback:
            print(f'Waypoint {feedback.current_waypoint}/{len(waypoints)}')

    result = navigator.getResult()
    print(f'Navigation result: {result}')
    rclpy.shutdown()
```

### Waypoint follower configuration

```yaml
waypoint_follower:
  ros__parameters:
    loop_rate: 20
    stop_on_failure: false
    waypoint_task_executor_plugin: "wait_at_waypoint"
    wait_at_waypoint:
      plugin: "nav2_waypoint_follower::WaitAtWaypoint"
      enabled: true
      waypoint_pause_duration: 200  # ms to pause at each waypoint
```

## 9. Multi-robot navigation

### Namespace isolation

```python
# Each robot gets its own Nav2 stack in a namespace
for robot_id in ['robot_1', 'robot_2']:
    GroupAction([
        PushRosNamespace(robot_id),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(nav2_launch_file),
            launch_arguments={
                'namespace': robot_id,
                'use_namespace': 'true',
                'params_file': nav2_params,
            }.items(),
        ),
    ])
```

### Multi-robot considerations

- Each robot needs its own costmap (sees other robots as obstacles)
- Use `frame_prefix` in robot_state_publisher for unique TF frames
- Localization must publish `map → robot_N/odom` (not shared `odom`)
- Consider a central task allocator for waypoint assignment

### Outdoor navigation with GPS

For outdoor robots, combine GPS with Nav2 using `robot_localization` EKF:

```bash
sudo apt install ros-jazzy-robot-localization ros-jazzy-nav2-waypoint-follower
```

The key is fusing GPS (lat/lon) into the Nav2 coordinate system via `robot_localization`'s `navsat_transform_node`, which converts GPS fixes to odometry in the map frame.

For multi-robot navigation patterns including fleet management and Open-RMF integration, see `references/multi-robot.md`.

## 10. Parameter tuning methodology

### Step-by-step tuning workflow

1. **Costmap first:** Verify sensor data appears correctly in costmaps
   - `ros2 run rviz2 rviz2` — visualize global and local costmaps
   - Adjust `inflation_radius` to match robot footprint + safety margin

2. **Global planner:** Get reasonable paths
   - Start with `NavfnPlanner` (simple, reliable)
   - Tune `tolerance` based on position accuracy needs

3. **Local controller:** Tune velocity tracking
   - Start with conservative velocity limits (50% of max)
   - Increase gradually while monitoring oscillation
   - Watch for overshooting at waypoints

4. **Recovery:** Handle edge cases
   - Test in narrow passages, dead ends, dynamic obstacles
   - Start with actuation-free recoveries only (section 7); validate robot
     geometry and clearance before enabling Spin/BackUp, then tune
     `spin_dist`/`backup_dist` from measured clearance

### Goal tolerance and oscillation

A common production issue: the robot oscillates near the goal. This happens when
`xy_goal_tolerance` is too tight relative to the controller frequency and robot
inertia. The robot overshoots, replans, overshoots in the opposite direction, and
loops.

**Rules:**

- Sanity floor: `max_vel_x / controller_frequency` is the distance traveled
  in **one control tick** (0.5 m/s at 20 Hz → 0.025 m). `xy_goal_tolerance`
  below that cannot work — but this is NOT a stopping distance.
- Baseline stopping-distance estimate (lower-bound model assuming ideal
  constant deceleration): `d ≈ v² / (2·|decel_limit|) + v·t_latency`. Real
  robots stop in a *longer* distance — add command transport latency, the
  velocity smoother's output period, gait-phase delay before deceleration
  actually starts (legged robots), state-estimation latency, command
  deadband/quantization, floor friction and slope, vendor-controller rate
  limits, and collision-monitor processing latency. Calibrate with a
  measured stopping test at operational speed, then size `xy_goal_tolerance`
  and collision-monitor polygons from the *measured* value.
- If using `RegulatedPurePursuitController`, its `regulated_linear_scaling_min_speed`
  reduces speed near goals, allowing tighter tolerances.
- Set `yaw_goal_tolerance` generously (0.1–0.3 rad) unless orientation matters.
- If the robot stops, rotates, overshoots, repeats — increase tolerances or reduce
  `max_vel_theta`.

### Hardware limits vs operational limits

The velocity range the SDK *accepts* is not the velocity Nav2 should
*command*. Derive `max_vel_*` from a chain of distinct numbers — measure
each one instead of copying the spec sheet:

| # | Quantity | Source |
|---|---|---|
| 1 | SDK / API absolute input range | Vendor API docs — a hard clamp, not a target |
| 2 | Actuation onset threshold | Measured — smallest command that produces real motion (gait start on legged robots) |
| 3 | Safe operational ceiling | Site risk assessment — environment, payload, clearance |
| 4 | Smoother accel/decel limits | `velocity_smoother` config — bounds how fast commands change |
| 5 | Speed after collision slowdown | Collision monitor `slowdown_ratio` applied to (3) |
| 6 | Measured braking distance & inertia | Stopping test at operational speed |

Set Nav2's `max_vel_x` / `max_vel_theta` from (3), never from (1). A ±4 rad/s
API range does not mean the robot is controllable — or safe — at 4 rad/s.

**Row 2 is also a diagnosis, not just a limit.** "Nav2 outputs a small velocity
and the robot does not move" is usually read as a configuration bug, and
retuning follows. But a command below the actuation-onset threshold is a
*correct* command the hardware ignores — no Nav2 parameter can fix it. Measure
the threshold before tuning: with the robot restrained and an operator present
(L5, `references/testing.md` §11), publish increasing commands and record the
smallest one that produces measured motion in encoder or IMU feedback. If the
controller's normal output near the goal sits below that value, the fix is a
minimum-command floor or a different approach strategy, not smaller tolerances.

**Deadbands belong on the output, never in the feedback path.** A smoother that
zeroes small commands and then feeds the *zeroed* value back as its own state
erases every tick's ramp increment: each cycle starts from zero, the ramp never
accumulates past the deadband, and the robot never accelerates. Upstream
`nav2_velocity_smoother` gets this right — it assigns `last_cmd_` from the
rate-limited value *before* applying `deadband_velocities_` — so if you write
your own smoother or wrap one in open-loop mode, keep that ordering.

### Key parameters to tune first

| Parameter | Effect | Start value |
|---|---|---|
| `robot_radius` | Collision boundary | Actual radius + 0.05 m |
| `inflation_radius` | Safe distance | `robot_radius` + 0.15 m |
| `max_vel_x` | Maximum speed | Safe operational ceiling (well below SDK max — see above) |
| `controller_frequency` | Path tracking update rate | 20 Hz |
| `planner_frequency` | Replanning rate | 1 Hz |

## 11. Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Robot doesn't move after sending goal | Nav2 nodes not in Active state | Check lifecycle states with `ros2 lifecycle list`; use `nav2_lifecycle_manager` |
| "No valid pose received" | TF chain broken (map → odom → base_link) | Verify transforms with `ros2 run tf2_tools view_frames` |
| Robot oscillates near goal | Controller gains too aggressive | Reduce `max_vel_theta`, increase `xy_goal_tolerance` |
| Robot gets stuck at narrow passage | Inflation radius too large for gap | Reduce `inflation_radius` or use `cost_scaling_factor` to soften falloff |
| "Planning failed" | Start or goal pose inside an obstacle in costmap | Clear costmaps, check sensor data, adjust obstacle layer params |
| Costmap shows phantom obstacles | Stale sensor data or wrong TF | Check sensor topic rate, verify TF timestamps |
| Robot takes very long paths | Costmap inflation too high | Reduce `cost_scaling_factor` (higher value = cost drops faster) |
| Recovery spin doesn't complete | Insufficient space to rotate | Re-measure clearance and reduce `spin_dist` — or disable motion recoveries per the escalation ladder (section 7) |
| Robot suddenly rotates in place mid-mission | Recovery `Spin` triggered (stock BT enables motion recoveries) | Check behavior server logs for recovery activation; keep motion recoveries opt-in until validated (section 7) |
| Robot re-approaches goal, final yaw never settles | Stateful goal-checker XY latch cleared when the BT replaces the FollowPath goal during replanning | See section 6 — verify goal-checker reset behavior on your installed version; widen `xy_goal_tolerance` or adjust BT replanning |
| Robot oscillates at goal | Goal tolerance too tight for speed/inertia | Increase `xy_goal_tolerance`, reduce `max_vel_theta` near goal |
| Ghost obstacles in costmap | Fast-moving people/objects leave stale marks | Reduce `obstacle_max_range`, increase `update_frequency`, use VoxelLayer |
| Collision monitor stops robot unexpectedly | Stop polygon too large for robot | Shrink `PolygonStop` points to match actual footprint + small margin |
