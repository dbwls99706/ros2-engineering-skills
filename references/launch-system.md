# Launch System

## Table of contents

1. Python launch API fundamentals
2. Launch arguments and substitutions
3. Conditional logic
4. Event handlers
5. Composing launch files
6. GroupAction for namespacing
7. Launch for ros2_control
8. Large system organization
9. Launch testing integration
10. Common failures and fixes

---

## 1. Python launch API fundamentals

ROS 2 uses Python launch files (`*.launch.py`) as the primary launch mechanism.
XML and YAML launch formats exist but lack the full power of Python conditionals
and event handling. **Always use Python launch files for production systems.**

### Minimal launch file

```python
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='my_robot_driver',
            executable='driver_node',
            name='driver',
            namespace='robot',
            output='screen',
            parameters=[{'publish_rate': 100.0}],
            remappings=[
                ('joint_states', '/robot/joint_states'),
            ],
        ),
    ])
```

### Key launch entities

| Entity | Purpose | Example |
|---|---|---|
| `Node` | Launch a ROS 2 node | `Node(package='...', executable='...')` |
| `LifecycleNode` | Launch a lifecycle-managed node | Same as Node but exposes lifecycle events |
| `ComposableNodeContainer` | Launch a component container | Holds composable nodes |
| `ExecuteProcess` | Run any external process | `ExecuteProcess(cmd=['ros2', 'bag', 'record'])` |
| `IncludeLaunchDescription` | Include another launch file | Compose launch files hierarchically |
| `GroupAction` | Group actions with shared namespace/conditions | Namespace isolation |
| `DeclareLaunchArgument` | Declare a user-facing argument | CLI-configurable parameters |
| `SetEnvironmentVariable` | Set env var for child processes | DDS config, locale |

## 2. Launch arguments and substitutions

### Declaring and using arguments

```python
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    # Declare arguments (visible with `ros2 launch <pkg> <file> --show-args`)
    use_sim = DeclareLaunchArgument(
        'use_sim_time', default_value='false',
        description='Use simulation clock')

    robot_name = DeclareLaunchArgument(
        'robot_name', default_value='my_robot',
        description='Name prefix for all nodes and topics')

    config_file = DeclareLaunchArgument(
        'config', default_value=PathJoinSubstitution([
            FindPackageShare('my_robot_bringup'), 'config', 'params.yaml'
        ]),
        description='Path to parameter file')

    driver = Node(
        package='my_robot_driver',
        executable='driver_node',
        name=['driver_', LaunchConfiguration('robot_name')],
        namespace=LaunchConfiguration('robot_name'),
        parameters=[
            LaunchConfiguration('config'),
            {'use_sim_time': LaunchConfiguration('use_sim_time')},
        ],
        output='screen',
    )

    return LaunchDescription([use_sim, robot_name, config_file, driver])
```

```bash
# Launch with custom arguments
ros2 launch my_robot_bringup robot.launch.py use_sim_time:=true robot_name:=arm_1
```

### Common substitutions

| Substitution | Purpose | Example |
|---|---|---|
| `LaunchConfiguration('arg')` | Read a launch argument | Dynamic node config |
| `FindPackageShare('pkg')` | Get package share directory | Load config/URDF files |
| `PathJoinSubstitution([...])` | Join path segments | Build file paths |
| `Command(['cmd', 'args'])` | Run a command and use its output | `xacro` processing |
| `EnvironmentVariable('VAR')` | Read an environment variable | Robot-specific config |
| `PythonExpression(['expr'])` | Evaluate Python expression | Complex conditionals |
| `TextSubstitution(text='...')` | Static text | Fixed strings in expressions |

### URDF/xacro processing

```python
from launch.substitutions import Command, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

robot_description = Command([
    'xacro ',
    PathJoinSubstitution([
        FindPackageShare('my_robot_description'),
        'urdf', 'robot.urdf.xacro',
    ]),
    ' use_sim:=', LaunchConfiguration('use_sim_time'),
    ' robot_name:=', LaunchConfiguration('robot_name'),
])

robot_state_publisher = Node(
    package='robot_state_publisher',
    executable='robot_state_publisher',
    parameters=[{'robot_description': robot_description}],
)
```

## 3. Conditional logic

### IfCondition / UnlessCondition

```python
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

use_rviz_arg = DeclareLaunchArgument('rviz', default_value='true')

rviz_node = Node(
    package='rviz2',
    executable='rviz2',
    arguments=['-d', rviz_config_path],
    condition=IfCondition(LaunchConfiguration('rviz')),
)

headless_logger = Node(
    package='my_robot_monitor',
    executable='logger_node',
    condition=UnlessCondition(LaunchConfiguration('rviz')),
)
```

### PythonExpression for complex conditions

```python
from launch.conditions import IfCondition
from launch.substitutions import PythonExpression

# Only launch when robot_type is 'arm' and simulation is enabled
condition=IfCondition(PythonExpression([
    "'", LaunchConfiguration('robot_type'), "' == 'arm' and '",
    LaunchConfiguration('use_sim_time'), "' == 'true'"
]))
```

## 4. Event handlers

### OnProcessExit — sequenced startup

```python
from launch.actions import RegisterEventHandler, ExecuteProcess
from launch.event_handlers import OnProcessExit
from launch_ros.actions import Node

# Spawn controller after joint_state_broadcaster is loaded
spawn_jsb = Node(
    package='controller_manager',
    executable='spawner',
    arguments=['joint_state_broadcaster'],
)

spawn_arm_controller = Node(
    package='controller_manager',
    executable='spawner',
    arguments=['arm_controller'],
)

delayed_spawn = RegisterEventHandler(
    OnProcessExit(
        target_action=spawn_jsb,
        on_exit=[spawn_arm_controller],
    )
)
```

### OnProcessIO — log monitoring

```python
from launch.event_handlers import OnProcessIO

log_monitor = RegisterEventHandler(
    OnProcessIO(
        target_action=driver_node,
        on_stdout=lambda event: print(f'[DRIVER] {event.text.decode()}'),
    )
)
```

### OnShutdown — cleanup actions

```python
from launch.actions import RegisterEventHandler, LogInfo
from launch.event_handlers import OnShutdown

shutdown_handler = RegisterEventHandler(
    OnShutdown(
        on_shutdown=[LogInfo(msg='System shutting down — saving state...')],
    )
)
```

## 5. Composing launch files

### IncludeLaunchDescription

```python
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    driver_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare('my_robot_driver'),
                'launch', 'driver.launch.py',
            ])
        ),
        launch_arguments={
            'serial_port': '/dev/ttyUSB0',
            'baud_rate': '115200',
        }.items(),
    )

    navigation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare('my_robot_navigation'),
                'launch', 'navigation.launch.py',
            ])
        ),
        launch_arguments={
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'map': LaunchConfiguration('map'),
        }.items(),
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('map', default_value=''),
        driver_launch,
        navigation_launch,
    ])
```

### Recommended hierarchy

```text
my_robot_bringup/
├── launch/
│   ├── robot.launch.py          # Top-level: includes all subsystem launches
│   ├── simulation.launch.py     # Sim-specific (Gazebo + bridges)
│   └── rviz.launch.py           # Visualization
my_robot_driver/
├── launch/
│   └── driver.launch.py         # Self-contained driver launch
my_robot_navigation/
├── launch/
│   └── navigation.launch.py     # Self-contained Nav2 launch
my_robot_perception/
├── launch/
│   └── perception.launch.py     # Self-contained perception pipeline
```

## 6. GroupAction for namespacing

```python
from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import PushRosNamespace, Node

def generate_launch_description():
    robot_1 = GroupAction([
        PushRosNamespace('robot_1'),
        Node(package='my_robot_driver', executable='driver_node', name='driver'),
        Node(package='my_robot_perception', executable='lidar_node', name='lidar'),
    ])

    robot_2 = GroupAction([
        PushRosNamespace('robot_2'),
        Node(package='my_robot_driver', executable='driver_node', name='driver'),
        Node(package='my_robot_perception', executable='lidar_node', name='lidar'),
    ])

    return LaunchDescription([robot_1, robot_2])
```

This creates:

- `/robot_1/driver`, `/robot_1/lidar` with topics under `/robot_1/...`
- `/robot_2/driver`, `/robot_2/lidar` with topics under `/robot_2/...`

## 7. Launch for ros2_control

### Standard ros2_control launch pattern

```python
from launch import LaunchDescription
from launch.actions import RegisterEventHandler, DeclareLaunchArgument
from launch.event_handlers import OnProcessExit
from launch.substitutions import Command, PathJoinSubstitution, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    robot_description = Command([
        'xacro ',
        PathJoinSubstitution([
            FindPackageShare('my_robot_description'), 'urdf', 'robot.urdf.xacro',
        ]),
    ])

    controller_config = PathJoinSubstitution([
        FindPackageShare('my_robot_control'), 'config', 'controllers.yaml',
    ])

    # Robot State Publisher (publishes URDF to /robot_description)
    rsp = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}],
    )

    # Controller Manager
    # Humble: pass robot_description as parameter
    # Jazzy+: CM subscribes to /robot_description topic (published by RSP above)
    #         — do NOT pass robot_description as parameter
    cm_params = [{'robot_description': robot_description}, controller_config]  # Humble
    # cm_params = [controller_config]  # Jazzy / Kilted / Rolling
    control_node = Node(
        package='controller_manager',
        executable='ros2_control_node',
        parameters=cm_params,
        output='screen',
    )

    # Spawn broadcasters and controllers in order
    spawn_jsb = Node(
        package='controller_manager',
        executable='spawner',
        arguments=['joint_state_broadcaster',
                   '--controller-manager', '/controller_manager'],
    )

    spawn_controller = Node(
        package='controller_manager',
        executable='spawner',
        arguments=['arm_controller',
                   '--controller-manager', '/controller_manager',
                   '--param-file', controller_config],
    )

    # Ensure joint_state_broadcaster is up before arm_controller
    delayed_controller = RegisterEventHandler(
        OnProcessExit(
            target_action=spawn_jsb,
            on_exit=[spawn_controller],
        )
    )

    return LaunchDescription([
        rsp,
        control_node,
        spawn_jsb,
        delayed_controller,
    ])
```

## 8. Large system organization

### For systems with >50 nodes

**Problem:** A single monolithic launch file becomes unmanageable.

**Solution:** Layered launch architecture:

```text
Layer 1 (Subsystem launches):
  driver.launch.py       → hardware driver nodes
  perception.launch.py   → cameras, LiDAR, detection
  navigation.launch.py   → Nav2, localization, mapping
  manipulation.launch.py → MoveIt 2, gripper control

Layer 2 (Robot launch):
  robot.launch.py        → includes all Layer 1 files
                           → passes arguments down

Layer 3 (Deployment launch):
  fleet.launch.py        → includes robot.launch.py N times with namespaces
  simulation.launch.py   → includes robot.launch.py + Gazebo
```

### Launch argument forwarding pattern

```python
# In robot.launch.py — forward all common args to subsystem launches
COMMON_ARGS = ['use_sim_time', 'robot_name', 'namespace']

def forward_args(args_dict):
    """Forward common launch arguments to included launch files."""
    return {k: LaunchConfiguration(k) for k in COMMON_ARGS if k in args_dict}
```

### TimerAction for staggered startup

```python
from launch.actions import TimerAction

# Stagger perception by 3 seconds; this does not establish driver readiness.
delayed_perception = TimerAction(
    period=3.0,
    actions=[perception_launch],
)
```

`TimerAction` measures elapsed time, not initialization success. When perception
depends on a driver, use a bounded service/state readiness check and handle its
failure before starting the dependent work. Process start alone is also not a
readiness signal.

Sources: [TimerAction](https://github.com/ros2/launch/blob/3.4.11/launch/launch/actions/timer_action.py),
[controller manager spawner options](https://github.com/ros-controls/ros2_control/blob/jazzy/controller_manager/doc/userdoc.rst).

## 9. Launch testing integration

### Basic launch test

```python
import unittest
import launch
import launch.actions
import launch_testing
import launch_testing.actions
from launch_ros.actions import Node

def generate_test_description():
    node_under_test = Node(
        package='my_robot_driver',
        executable='driver_node',
        name='driver',
        parameters=[{'use_sim_time': True}],
    )

    return (
        launch.LaunchDescription([
            node_under_test,
            launch_testing.actions.ReadyToTest(),
        ]),
        {'driver': node_under_test},
    )

class TestDriverStartup(unittest.TestCase):
    def test_node_starts(self, proc_info, driver):
        """Verify the driver node starts without crashing."""
        proc_info.assertWaitForStartup(process=driver, timeout=10)

@launch_testing.post_shutdown_test()
class TestDriverShutdown(unittest.TestCase):
    def test_clean_exit(self, proc_info):
        """Verify the node exits cleanly."""
        launch_testing.asserts.assertExitCodes(proc_info)
```

### Running launch tests

```bash
# Via colcon
colcon test --packages-select my_robot_driver

# Directly with launch_test
launch_test test/test_driver.launch.py
```

## 10. Common failures and fixes

### Supervisor shutdown stalls

Distinguish a supervisor-only signal from a signal to the whole process group.
Require the supervisor and every started child to exit within the original
deadline. Children exiting during later forced cleanup do not make that attempt
successful. Preserve the first timeout and stack trace instead of retrying until
the gate passes.

In Python, the default SIGINT handler can raise `KeyboardInterrupt` between
asyncio operations. Python documents that this can leave the event loop unable
to shut down. ROS launch 3.4.11 catches that exception and resumes the loop while
its asynchronous signal manager also receives SIGINT. If shutdown is logged but
no child receives a signal, record the installed launch/Python versions, active
Python signal handler, thread stacks, and pending event/task state. An idle
selector alone does not prove which callback was interrupted.

A signal guard belongs to the supervisor that owns the event loop for its whole
lifetime. Do not install a global handler from an included launch description,
use `SIG_IGN`, or replace the native CLI only in a test and claim its shutdown
problem is fixed. Verify a proposed upstream repair against the installed version
with a deterministic signal-injection regression and unchanged child-exit gates.
Process shutdown remains separate from a downstream hardware stop mechanism.

Sources: [Python asyncio SIGINT behavior](https://docs.python.org/3.12/library/asyncio-runner.html#handling-keyboard-interruption),
[ROS launch 3.4.11 signal management](https://github.com/ros2/launch/blob/3.4.11/launch/launch/utilities/signal_management.py),
[LaunchService.run](https://github.com/ros2/launch/blob/3.4.11/launch/launch/launch_service.py).

### Other launch failures

| Symptom | Cause | Fix |
|---|---|---|
| "Package not found" in launch | Package not installed or not sourced | `colcon build`, `source install/setup.bash` |
| Node starts but parameters are default | Parameter file path is wrong (Substitution not resolved) | Use `FindPackageShare` + `PathJoinSubstitution`, verify file exists |
| `xacro` fails during launch | Missing xacro dependency or syntax error | Test with `xacro robot.urdf.xacro` manually first |
| Launch argument not passed to included file | Argument not forwarded in `launch_arguments` | Explicitly pass `launch_arguments={...}.items()` |
| Nodes in wrong namespace | `PushRosNamespace` not wrapping correctly | Use `GroupAction` with `PushRosNamespace` as first element |
| Event handler never fires | Target action reference mismatch | Ensure the `target_action` variable is the same object, not a copy |
| Controller spawner exits with error | Manager unavailable, or controller load/configure/activate failed | Inspect the exit reason. Use the installed spawner's bounded service-wait options (such as `--controller-manager-timeout`) for manager readiness; inspect controller errors after discovery succeeds. `OnProcessExit` means termination, not startup readiness. |
| Launch file not found during `ros2 launch` | Missing `data_files` entry in setup.py (Python pkg) or `install(DIRECTORY launch ...)` in CMake | Add install directive in build config |

---

**See also:** `references/lifecycle-components.md` for lifecycle node orchestration from launch files, `references/tf2-urdf.md` for xacro processing and robot_state_publisher launch patterns, `references/workspace-build.md` for package discovery and sourcing.
