# System Bringup

> **Distro stability:** udev, systemd, and launch patterns in this guide are identical on
> Humble, Jazzy, Kilted, and Rolling. The one distro-sensitive area is discovery control:
> Humble uses `ROS_LOCALHOST_ONLY=1`, Jazzy+ replaces it with
> `ROS_AUTOMATIC_DISCOVERY_RANGE` (see `references/deployment.md` §6). Differences are
> tagged inline.

This guide covers taking a robot from power-on to a verified, operational ROS 2 system
with no human in the loop: bringup package layering, persistent device naming with udev,
systemd boot sequencing, watchdog design, and boot-time health verification.
The base systemd unit template, `WatchdogSec`/`sd_notify` integration, and graceful
shutdown live in `references/deployment.md` §5 and §9 — this file goes deeper on
*ordering*, *device identity*, and *application-level liveness*.

## Table of contents

1. [Bringup package architecture](#1-bringup-package-architecture)
2. [udev rules and persistent device naming](#2-udev-rules-and-persistent-device-naming)
3. [Boot sequencing with systemd](#3-boot-sequencing-with-systemd)
4. [Watchdog patterns](#4-watchdog-patterns)
5. [Boot-time health verification](#5-boot-time-health-verification)
6. [Common failures and fixes](#6-common-failures-and-fixes)

---

## 1. Bringup package architecture

### The `robot_bringup` package

Every deployable robot needs exactly one package that owns "how this robot starts."
Keep it free of business logic — it contains only launch files, configuration, and
system integration assets:

```text
my_robot_bringup/
├── package.xml              # exec_depend on every package it launches
├── CMakeLists.txt           # ament_cmake, installs launch/ config/ systemd/ udev/
├── launch/
│   ├── robot.launch.py      # top level — includes the layers below
│   ├── base.launch.py       # drivers: motor controller, ros2_control, robot_state_publisher
│   ├── sensors.launch.py    # camera, LiDAR, IMU drivers
│   └── autonomy.launch.py   # Nav2 / MoveIt / application nodes
├── config/
│   ├── robot_params.yaml
│   └── diagnostics.yaml     # diagnostic_aggregator analyzers (Section 5)
├── udev/
│   └── 99-my-robot.rules    # installed to /etc/udev/rules.d/ (Section 2)
└── systemd/
    ├── ros2-base.service    # unit files installed to /etc/systemd/system/ (Section 3)
    └── ros2-autonomy.service
```

### Layered launch — one layer per failure domain

Split launch files by *what can fail independently*, not by package. Hardware drivers
crash for different reasons than planners; separating them lets systemd restart one
layer without touching the other.

```python
# launch/robot.launch.py — top level, composition only
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    bringup_dir = FindPackageShare('my_robot_bringup')
    use_autonomy = LaunchConfiguration('autonomy')

    return LaunchDescription([
        DeclareLaunchArgument('autonomy', default_value='true',
                              description='Start the autonomy layer'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(
            PathJoinSubstitution([bringup_dir, 'launch', 'base.launch.py']))),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(
            PathJoinSubstitution([bringup_dir, 'launch', 'sensors.launch.py']))),
        # Autonomy is optional so field techs can bring up hardware alone
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution([bringup_dir, 'launch', 'autonomy.launch.py'])),
            condition=IfCondition(use_autonomy)),
    ])
```

### Deterministic startup order with lifecycle nodes

DDS discovery is asynchronous — node A starting "before" node B in a launch file does
NOT mean A is ready when B activates. For hardware-owning nodes, use lifecycle nodes
(see `references/lifecycle-components.md`) and drive transitions explicitly so the
sensor driver is `active` before the consumer configures:

```python
# launch/sensors.launch.py — event-driven ordered activation
from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch_ros.actions import LifecycleNode
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from lifecycle_msgs.msg import Transition


def generate_launch_description():
    lidar = LifecycleNode(package='sllidar_ros2', executable='sllidar_node',
                          name='lidar_driver', namespace='',
                          parameters=[{'serial_port': '/dev/lidar'}])  # udev symlink, Section 2

    configure_lidar = EmitEvent(event=ChangeState(
        lifecycle_node_matcher=lambda node: node == lidar,
        transition_id=Transition.TRANSITION_CONFIGURE))

    # Activate only after on_configure succeeded — never blind-fire both transitions
    activate_on_configured = RegisterEventHandler(OnStateTransition(
        target_lifecycle_node=lidar, goal_state='inactive',
        entities=[EmitEvent(event=ChangeState(
            lifecycle_node_matcher=lambda node: node == lidar,
            transition_id=Transition.TRANSITION_ACTIVATE))]))

    return LaunchDescription([lidar, activate_on_configured, configure_lidar])
```

For many managed nodes, delegate sequencing to `nav2_lifecycle_manager` instead of
hand-writing event handlers — it configures/activates a declared node list in order,
respawns on failure, and exposes a bond-based liveness check:

```yaml
lifecycle_manager:
  ros__parameters:
    autostart: true
    node_names: [lidar_driver, camera_driver, imu_driver]   # activation order
    bond_timeout: 4.0          # seconds without a bond heartbeat → node considered dead
    attempt_respawn_reconnection: true
```

## 2. udev rules and persistent device naming

### The problem

Kernel device names are assigned by enumeration order. `/dev/ttyUSB0` is the LiDAR
today and the motor controller after the next reboot or a loose cable. Any config that
hardcodes `ttyUSB0`/`ttyACM0`/`video0` will eventually open the wrong device — on a
robot this means commanding the wrong hardware.

```yaml
# BAD — enumeration-order name, silently swaps between devices
lidar_driver:
  ros__parameters:
    serial_port: /dev/ttyUSB0

# GOOD — udev symlink that always points at the LiDAR
lidar_driver:
  ros__parameters:
    serial_port: /dev/lidar
```

### Zero-config option: `/dev/serial/by-id`

Before writing rules, check the stable paths the kernel already provides:

```bash
ls -l /dev/serial/by-id/
# usb-FTDI_USB-RS485_Cable_FT89ABCD-if00-port0 -> ../../ttyUSB0
# usb-Silicon_Labs_CP2102_USB_to_UART_0001-if00 -> ../../ttyUSB1
```

These are stable as long as the device reports a unique serial number. They are fine
for a single robot, but the path embeds the *specific unit's* serial — replacing a
failed sensor changes the path on that robot only. For fleets, write udev rules so
every robot uses the same logical name (`/dev/lidar`) regardless of which physical
unit is installed.

### Finding match attributes

```bash
# Everything udev knows about the device, walking up the USB parent chain
udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial|KERNELS'
#   ATTRS{idVendor}=="10c4"
#   ATTRS{idProduct}=="ea60"
#   ATTRS{serial}=="0001"
#   KERNELS=="1-2.3"        <- physical USB port path
```

### Writing the rules

```bash
# /etc/udev/rules.d/99-my-robot.rules
# Rules are processed lexically; 99- runs after the distro defaults.

# LiDAR — CP2102 bridge, matched by vendor/product + serial
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
  ATTRS{serial}=="0001", SYMLINK+="lidar", MODE="0660", GROUP="dialout"

# Motor controller — FTDI bridge
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", \
  ATTRS{serial}=="FT89ABCD", SYMLINK+="motor_controller", MODE="0660", GROUP="dialout"

# Camera — match the video4linux *capture* node, not the metadata node.
# UVC cameras expose two /dev/video* nodes; index 0 of the pair is the capture node.
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="085e", \
  ATTR{index}=="0", SYMLINK+="camera_front", MODE="0660", GROUP="video"
```

Rules for identical devices with **no unique serial** (common with cheap CP2102 clones
that all report `serial=="0001"`): match the physical USB port instead. This pins the
role to the port — label the ports on the robot chassis.

```bash
# Two identical IMUs distinguished by which port they are plugged into
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
  KERNELS=="1-2.3", SYMLINK+="imu_base", MODE="0660", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
  KERNELS=="1-2.4", SYMLINK+="imu_arm", MODE="0660", GROUP="dialout"
```

### Permissions — never chmod in a startup script

```bash
# BAD — race-prone, lost on replug, requires root at runtime
sudo chmod 666 /dev/ttyUSB0    # in some startup script

# GOOD — udev sets GROUP+MODE at creation time; put the ROS user in the group once
sudo usermod -aG dialout robot_user   # serial devices
sudo usermod -aG video robot_user     # cameras
# Log out/in (or reboot) for group membership to take effect.
```

`MODE="0666"` (world-writable) works but grants every local process hardware access —
use group-based `0660` on anything that drives motion.

### Applying and testing rules

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --subsystem-match=video4linux

ls -l /dev/lidar /dev/motor_controller     # symlinks exist?
udevadm test $(udevadm info -q path -n /dev/ttyUSB0) 2>&1 | grep -E 'SYMLINK|GROUP|MODE'
```

Install the rules file from the bringup package so it is versioned with the robot:

```cmake
# CMakeLists.txt — stage rules into the install space; a deploy script copies them
install(FILES udev/99-my-robot.rules DESTINATION share/${PROJECT_NAME}/udev)
```

```bash
# Deploy step (Ansible/provisioning script — requires root, so not done by ROS itself)
sudo cp "$(ros2 pkg prefix my_robot_bringup)/share/my_robot_bringup/udev/99-my-robot.rules" \
  /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
```

## 3. Boot sequencing with systemd

The single-unit template (`ExecStart`, environment sourcing, restart policy, logging)
is in `references/deployment.md` §5. This section covers what that template does not:
making *multiple* units start in the right order, only when their dependencies are
actually ready.

### Ordering vs dependency — the distinction that breaks robots

systemd separates "start B after A" from "B needs A":

| Directive | Meaning | Use for |
|---|---|---|
| `After=A` | Ordering only — wait for A to start before starting this. Does NOT pull A in. | Everything that must be sequenced |
| `Wants=A` | Pull A in; continue even if A fails | Soft deps (monitoring, upload agents) |
| `Requires=A` | Pull A in; if A fails to start, this unit is not started | Hard deps at start time |
| `BindsTo=A` | Like `Requires`, plus: stop this unit when A stops or vanishes | Units bound to a device or a base layer |
| `PartOf=A` | Stop/restart propagates from A to this unit (not the reverse) | Grouping layers under one restart |

`Wants=`/`Requires=` without `After=` starts both units *concurrently* — always pair
them. The classic robot bringup failure is a driver starting before the network or
the device node exists; encode those as real dependencies:

```ini
# /etc/systemd/system/ros2-base.service — drivers layer
[Unit]
Description=ROS 2 base drivers
# Network must be UP (not just "network.target reached") — DDS binds real interfaces
Wants=network-online.target
After=network-online.target
# System clock must be synchronized before stamping sensor data
Wants=time-sync.target
After=time-sync.target
# Device units: systemd creates dev-*.device for udev devices tagged "systemd".
# BindsTo stops the driver layer if the LiDAR is unplugged.
BindsTo=dev-lidar.device
After=dev-lidar.device

[Service]
Type=notify
User=robot_user
ExecStart=/opt/robot/scripts/start_layer.sh base
Restart=on-failure
RestartSec=2
WatchdogSec=30

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ros2-autonomy.service — autonomy layer, starts after base
[Unit]
Description=ROS 2 autonomy stack
Requires=ros2-base.service
After=ros2-base.service
# Restarting base (new drivers) restarts autonomy too, never the reverse
PartOf=ros2-base.service

[Service]
Type=simple
User=robot_user
ExecStart=/opt/robot/scripts/start_layer.sh autonomy
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

For `BindsTo=dev-lidar.device` to work, tag the device in the udev rule:

```bash
# Append to the LiDAR rule from Section 2
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
  ATTRS{serial}=="0001", SYMLINK+="lidar", MODE="0660", GROUP="dialout", \
  TAG+="systemd", ENV{SYSTEMD_ALIAS}="/dev/lidar"
```

### network-online and time-sync are not free

Both targets are passive — they only mean something when a waiting service is enabled:

```bash
# Make network-online.target actually wait for an IP address
sudo systemctl enable systemd-networkd-wait-online.service   # networkd systems
sudo systemctl enable NetworkManager-wait-online.service     # NetworkManager (desktop Ubuntu)

# Make time-sync.target wait for a *synchronized* clock, not just a started daemon.
sudo systemctl enable systemd-time-wait-sync.service         # with systemd-timesyncd
# With chrony, use chrony-waitsync (Ubuntu 24.04 / Jazzy hosts) instead.
```

Why time matters at bringup: if sensor drivers start stamping data before NTP/chrony
steps the clock, the jump (often years, from RTC-less SBCs) breaks tf2 — consumers see
extrapolation errors, and rosbag recordings straddle the step. On offline robots run a
local chrony serving its own RTC, or gate bringup on a PPS/GPS source (see
`references/sensor-integration.md` §3 for sensor-level clock sync).

### Site-specific overrides with drop-ins

Never hand-edit deployed unit files — package the base unit, override per robot:

```bash
sudo systemctl edit ros2-base.service
# Creates /etc/systemd/system/ros2-base.service.d/override.conf
```

```ini
# override.conf — this robot has no autonomy compute, lower the watchdog
[Service]
WatchdogSec=60
Environment=ROS_DOMAIN_ID=17
```

### User vs system services

Run bringup as a **system service with `User=robot_user`** (as above). User-session
services (`systemctl --user`) die at logout unless lingering is enabled
(`loginctl enable-linger robot_user`) and start *late* — after the user manager —
which adds seconds to bringup and cannot express `BindsTo=` on system device units.
Reserve user services for developer conveniences, never for the robot's own stack.

### Debugging boot order

```bash
systemd-analyze critical-chain ros2-autonomy.service   # what delayed startup
systemd-analyze plot > boot.svg                        # full boot timeline
systemctl list-dependencies ros2-autonomy.service
journalctl -u ros2-base.service -b                     # this boot's logs, one unit
```

## 4. Watchdog patterns

A production robot needs three watchdog layers. Each catches what the layer below
cannot:

| Layer | Detects | Mechanism | Recovery |
|---|---|---|---|
| systemd `WatchdogSec` | Hung/dead *process* | `sd_notify(0, "WATCHDOG=1")` (see `deployment.md` §5) | Process restart |
| Topic-level heartbeat | Node alive but *not publishing* (deadlocked executor, stalled driver thread) | QoS DEADLINE / LIVELINESS events | Safe-stop, then targeted restart |
| Hardware watchdog | Kernel panic, total freeze | `/dev/watchdog` via systemd `RuntimeWatchdogSec` | Machine reboot |

A process can pet the systemd watchdog from a healthy main thread while its DDS
publishing thread is deadlocked — only a topic-level watchdog sees that.

Before relying on a watchdog reboot as a recovery path, confirm that the logs
of the boot being recovered from survive it: `journalctl --list-boots` must show
earlier boots, which requires persistent journald storage
(`Storage=persistent` in `/etc/systemd/journald.conf` or an existing
`/var/log/journal/`). With volatile storage the reboot erases the evidence of
what the watchdog fired on (one field unit lost the logs of every boot before
the last).

### Topic-level heartbeat watchdog (DEADLINE events)

The publisher declares a DEADLINE contract; the subscriber gets an event callback the
moment the contract is violated. No polling, no timer bookkeeping:

```cpp
// heartbeat_watchdog.cpp — monitors critical topics, commands safe-stop on silence
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/empty.hpp>
#include <geometry_msgs/msg/twist.hpp>

using namespace std::chrono_literals;

class HeartbeatWatchdog : public rclcpp::Node
{
public:
  HeartbeatWatchdog() : Node("heartbeat_watchdog")
  {
    // Contract: a heartbeat at least every 500 ms (publisher sends at 5 Hz —
    // always set the deadline to ~2.5x the period to tolerate scheduling jitter).
    rclcpp::QoS qos(rclcpp::KeepLast(1));
    qos.reliable().deadline(500ms);

    rclcpp::SubscriptionOptions options;
    options.event_callbacks.deadline_callback =
      [this](rclcpp::QOSDeadlineRequestedInfo & info) {
        // total_count_change tells how many deadlines were missed since last callback
        RCLCPP_ERROR(get_logger(),
                     "Heartbeat deadline missed (%d new misses) — commanding stop",
                     info.total_count_change);
        trigger_safe_stop();
      };

    heartbeat_sub_ = create_subscription<std_msgs::msg::Empty>(
      "/base_driver/heartbeat", qos,
      [this](std_msgs::msg::Empty::ConstSharedPtr) { healthy_ = true; },
      options);

    cmd_pub_ = create_publisher<geometry_msgs::msg::Twist>("/cmd_vel_watchdog", 10);
  }

private:
  void trigger_safe_stop()
  {
    healthy_ = false;
    // Publish through a priority mux input, not raw /cmd_vel —
    // see references/safety-estop.md for command arbitration.
    cmd_pub_->publish(geometry_msgs::msg::Twist{});  // all-zero = stop
  }

  bool healthy_{false};
  rclcpp::Subscription<std_msgs::msg::Empty>::SharedPtr heartbeat_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_pub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<HeartbeatWatchdog>());
  rclcpp::shutdown();
  return 0;
}
```

The monitored node publishes the heartbeat from the same loop that does the real work,
so a stalled loop stops the heartbeat:

```cpp
// Inside the driver's control loop — NOT a separate timer, or it lies about health
rclcpp::QoS hb_qos(rclcpp::KeepLast(1));
hb_qos.reliable().deadline(500ms);   // offered deadline must be <= requested (RxO)
heartbeat_pub_ = create_publisher<std_msgs::msg::Empty>("~/heartbeat", hb_qos);

void control_cycle()   // called at 5 Hz+
{
  read_hardware();
  update_commands();
  heartbeat_pub_->publish(std_msgs::msg::Empty{});   // proves the LOOP ran
}
```

```python
# BAD — heartbeat on its own timer keeps beating while the control loop is dead
self.create_timer(0.2, lambda: self.hb_pub.publish(Empty()))
```

### LIVELINESS for process-death detection

DEADLINE detects a silent-but-alive publisher. LIVELINESS `AUTOMATIC` detects the
publisher *disappearing* (crash, network split) faster than waiting for a deadline
miss, because the RMW withdraws liveliness on participant loss:

```cpp
qos.liveliness(RMW_QOS_POLICY_LIVELINESS_AUTOMATIC)
   .liveliness_lease_duration(1s);
options.event_callbacks.liveliness_callback =
  [this](rclcpp::QOSLivelinessChangedInfo & info) {
    if (info.alive_count == 0) { trigger_safe_stop(); }
  };
```

Use `MANUAL_BY_TOPIC` only when the process itself must assert health explicitly
(`publisher->assert_liveliness()`); `AUTOMATIC` + a DEADLINE heartbeat covers most
robots with less code.

### Escalation policy

Restart loops are a failure mode of their own — a driver crash-looping every 2 s
slams hardware with open/close cycles. Escalate instead of retrying forever:

```ini
# ros2-base.service — stop retrying after 3 failures in 60 s, run a fallback
[Unit]
StartLimitIntervalSec=60
StartLimitBurst=3
OnFailure=robot-safe-mode.service   # oneshot: park hardware, alert operator

[Service]
Restart=on-failure
RestartSec=2
```

### Hardware watchdog

```ini
# /etc/systemd/system.conf.d/watchdog.conf — systemd pets /dev/watchdog;
# if PID 1 itself hangs (kernel or init failure), the SoC watchdog reboots the board.
[Manager]
RuntimeWatchdogSec=30
RebootWatchdogSec=2min
```

Enable this only after the safe-stop path works: a hardware reboot mid-motion is
itself a hazard. Motors must be commanded to a safe state by e-stop hardware or
controller firmware, never by the OS that just froze (`references/safety-estop.md`).

## 5. Boot-time health verification

`systemctl status` says the *process* runs. It says nothing about nodes discovering
each other, topics flowing, or tf being complete. Verify at the ROS graph level before
declaring the robot in service.

### Bringup smoke check

```bash
#!/bin/bash
# /opt/robot/scripts/bringup_check.sh — run by a oneshot unit after the stack starts
set -euo pipefail
source /opt/ros/${ROS_DISTRO}/setup.bash
source /opt/robot/ws/install/setup.bash

REQUIRED_NODES=(/lidar_driver /base_driver /robot_state_publisher)
REQUIRED_TOPICS=(/scan /odom /tf)
DEADLINE=$((SECONDS + 60))

# The CLI daemon caches the graph — restart it so we see the fresh boot state
ros2 daemon stop >/dev/null 2>&1 || true
ros2 daemon start >/dev/null

for node in "${REQUIRED_NODES[@]}"; do
  until ros2 node list 2>/dev/null | grep -qx "${node}"; do
    (( SECONDS < DEADLINE )) || { echo "FAIL: node ${node} never appeared"; exit 1; }
    sleep 2
  done
done

for topic in "${REQUIRED_TOPICS[@]}"; do
  # Data actually flowing, not just an advertised topic
  timeout 10 ros2 topic echo --once "${topic}" >/dev/null 2>&1 \
    || { echo "FAIL: no data on ${topic}"; exit 1; }
done

echo "OK: bringup verified"
```

```ini
# /etc/systemd/system/ros2-bringup-check.service
[Unit]
Description=Verify ROS 2 graph after bringup
Requires=ros2-base.service
After=ros2-base.service
OnFailure=robot-safe-mode.service

[Service]
Type=oneshot
User=robot_user
Environment=ROS_DISTRO=jazzy
ExecStart=/opt/robot/scripts/bringup_check.sh
# Downstream units can order After=ros2-bringup-check.service to gate on health
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

> **Humble note:** on Humble hosts also export `ROS_LOCALHOST_ONLY` consistently with
> the stack under test; on Jazzy+ use `ROS_AUTOMATIC_DISCOVERY_RANGE`. A mismatch
> between the check script's environment and the services' environment makes the
> check see an empty graph while the robot is actually fine.

### Continuous health: diagnostic_aggregator

Drivers should publish to `/diagnostics` via `diagnostic_updater` (sensor-side setup
in `references/sensor-integration.md` §6). Aggregate them into one operator-facing
tree:

```yaml
# config/diagnostics.yaml
analyzers:
  ros__parameters:
    path: robot
    base:
      type: diagnostic_aggregator/GenericAnalyzer
      path: base
      contains: ['base_driver', 'motor']
    sensors:
      type: diagnostic_aggregator/GenericAnalyzer
      path: sensors
      contains: ['lidar', 'camera', 'imu']
```

```bash
sudo apt install ros-${ROS_DISTRO}-diagnostic-aggregator
ros2 run diagnostic_aggregator aggregator_node --ros-args --params-file config/diagnostics.yaml
ros2 topic echo /diagnostics_agg --once   # one STALE/ERROR entry fails the fleet health poll
```

The fleet health endpoint pattern (HTTP liveness for load balancers/monitoring) is in
`references/deployment.md` §8 — feed it from `/diagnostics_agg` rather than a bare
"process is up" check.

## 6. Common failures and fixes

| Symptom | Why it happens | Fix |
|---|---|---|
| Driver opens the wrong serial device after reboot | `/dev/ttyUSB*` order depends on enumeration | udev `SYMLINK+=` rule matched on vendor/product/serial; configure drivers with the symlink (Section 2) |
| udev rule matches nothing | Attributes taken from different parent devices in one rule (`ATTRS` must all match the same parent) | Use `udevadm info -a` and copy attributes from a single parent block; test with `udevadm test` |
| `Permission denied` opening `/dev/lidar` | ROS user not in `dialout`/`video` group, or rule missing `GROUP=` | `usermod -aG dialout robot_user` + `GROUP="dialout", MODE="0660"` in the rule; re-login |
| Two identical adapters swap roles | Devices report identical serial numbers | Match `KERNELS==` (physical port path) instead of `ATTRS{serial}` |
| DDS binds only loopback at boot | Service started before the NIC had an address; `network.target` does not mean "online" | `Wants=network-online.target` + `After=network-online.target` + enable the matching `*-wait-online.service` |
| tf extrapolation errors only on cold boot | Clock stepped by NTP after nodes started stamping data | Order bringup `After=time-sync.target` and enable `systemd-time-wait-sync`/`chrony-waitsync` (Section 3) |
| Units with `Wants=` still race | `Wants=`/`Requires=` do not imply ordering | Always pair with `After=` |
| Autonomy keeps running against dead drivers | Layers not coupled | `PartOf=ros2-base.service` on the autonomy unit so base restarts cascade |
| Watchdog says healthy while robot is frozen | Heartbeat published from a dedicated timer, not the work loop | Publish the heartbeat inside the control cycle; monitor with DEADLINE events (Section 4) |
| Driver crash-loops and wears hardware | Unbounded `Restart=on-failure` | `StartLimitBurst` + `OnFailure=` escalation to a safe-mode unit |
| `ros2 node list` empty in check script but robot works | CLI daemon cached a stale graph, or discovery env mismatch | `ros2 daemon stop && ros2 daemon start` in the script; align `ROS_DOMAIN_ID` and discovery range env |
| Health check passes but no sensor data | Check only tested node presence | Assert data flow with `ros2 topic echo --once` / `ros2 topic hz`, not just `ros2 node list` (Section 5) |
