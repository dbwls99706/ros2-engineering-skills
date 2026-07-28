---
name: ros2-engineering-skills
description: >
  TRIGGER when the user: writes or reviews ROS 2 nodes (rclcpp/rclpy), creates packages
  (colcon/ament), edits launch files (.launch.py), configures QoS or DDS, writes URDF/xacro,
  implements ros2_control hardware interfaces or controllers, sets up Nav2/MoveIt 2 pipelines,
  processes sensor data (camera/LiDAR/PCL), works with Gazebo/Isaac Sim, configures SROS2
  security, develops micro-ROS firmware, manages multi-robot fleets (Open-RMF), debugs with
  ros2 doctor/rosbag2, deploys via Docker/cross-compilation, or migrates from ROS 1.
  DO NOT TRIGGER for general C++/Python questions unrelated to ROS 2, non-robotics middleware,
  or web/mobile development tasks.
context: fork
classification: capability
category: api-reference
version: 1.2.0
deprecation-risk: medium
# The hooks block below is Claude Code-specific: the hook schema, the
# ${CLAUDE_PLUGIN_ROOT} path variable, and the tool-name matcher are not
# part of the Agent Skills standard. Other platforms (Codex, Cursor,
# Gemini CLI) ignore this block — see "Platform support" in the body for
# the manual fallback.
hooks:
  PreToolUse:
    # Matcher = Claude Code's file-mutation and shell tools.
    - matcher: "Edit|Write|MultiEdit|Bash"
      hooks:
        - type: command
          # timeout is in SECONDS (not milliseconds). The command-hook
          # default is 600 s; these validators are local file scans that
          # finish in well under a second.
          command: "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/skill_validate_hook.py"
          timeout: 10
  Stop:
    - hooks:
        - type: command
          command: "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/skill_stop_hook.py"
          timeout: 15
# Eval definitions live in evals/eval.yaml (single source of truth).
---

# ROS 2 Engineering Skills

> **Single responsibility:** This skill is an **API reference & code template guide**
> for ROS 2 development. It tells you *how to use ROS 2 APIs correctly* and
> *what mistakes to avoid*. It does NOT do CI/CD orchestration, incident response,
> data analysis, or deployment automation — those are separate skill categories.

A progressive-disclosure skill for ROS 2 development — from first workspace to
production fleet deployment. Detailed patterns and code templates live in
`references/`; read the relevant file before writing code.

## How to use this skill

This always-loaded file carries routing, core principles, pitfalls, and
anti-patterns — enough for quick questions and architectural decisions.
For implementation work, use the Decision Router below to load the
reference file(s) matching the task; the AI pitfalls table lists mistakes
worth re-checking before generating code. `scripts/` are tools to run
(scaffolding, QoS checking, launch validation), not reading material.
When domains intersect (e.g. Nav2 + ros2_control) and recommendations
conflict, favor safety > determinism > simplicity.

**Execution log (opt-in):** When the Stop hook runs (Claude Code only) *and*
the `SKILL_RUNS_LOG` environment variable is set, a session summary is
appended to `.skill-runs.log`. If that file exists in the workspace, read the
last few lines to avoid repeating past mistakes. Without the opt-in — and on
platforms without hooks — the file is never created, so a read-only session
leaves the working tree untouched.

**Platform support:** `SKILL.md` and `references/` are platform-neutral
knowledge documents. `scripts/` can be run manually on any platform whose
environment has Python and the repository dependencies. The hook wiring
(automatic execution) and `.skill-runs.log` are Claude Code-specific; on
other platforms run the validators manually from the skill root:
`SKILL_WORKSPACE=<dir> python3 scripts/skill_stop_hook.py` and
`python3 scripts/skill_validate_hook.py --file <src> / --command '<cmd>'`
(the command string is inspected only, never executed; without those flags
the validate hook expects a Claude Code PreToolUse payload and checks
nothing on its own).

## Decision router

| User is doing...                                  | Read                              |
|---------------------------------------------------|-----------------------------------|
| Creating a workspace, package, or build config    | `references/workspace-build.md`   |
| Writing nodes, executors, callback groups         | `references/nodes-executors.md`   |
| Topics, services, actions, custom interfaces, QoS | `references/communication.md`     |
| Lifecycle nodes, component loading, composition   | `references/lifecycle-components.md` |
| Launch files, conditional logic, event handlers   | `references/launch-system.md`     |
| tf2, URDF, xacro, robot_state_publisher           | `references/tf2-urdf.md`         |
| ros2_control, hardware interfaces, controllers    | `references/hardware-interface.md` |
| Real-time constraints, PREEMPT_RT, memory, jitter | `references/realtime.md`         |
| Nav2, SLAM, costmaps, behavior trees              | `references/navigation.md`       |
| MoveIt 2, planning scene, grasp pipelines         | `references/manipulation.md`     |
| Camera, LiDAR, PCL, cv_bridge, depth processing   | `references/perception.md`       |
| Sensor drivers, clock sync, LiDAR-camera extrinsics | `references/sensor-integration.md` |
| Unit tests, integration tests, launch_testing, CI | `references/testing.md`          |
| ros2 doctor, tracing, profiling, rosbag2, CLI cheat sheet | `references/debugging.md` |
| Docker, cross-compile, fleet deployment, OTA      | `references/deployment.md`       |
| System bringup, udev rules, boot sequence, watchdogs | `references/system-bringup.md` |
| Gazebo, Isaac Sim, sim-to-real, use_sim_time      | `references/simulation.md`       |
| SROS2, DDS security, certificates, supply chain   | `references/security.md`         |
| E-stop, safety chains, command arbitration        | `references/safety-estop.md`     |
| micro-ROS, MCU/RTOS, XRCE-DDS, rclc              | `references/micro-ros.md`        |
| Multi-robot fleet, Open-RMF, DDS discovery scale  | `references/multi-robot.md`      |
| Message types, units, covariance, frame conventions | `references/message-types.md`    |
| ROS 1 migration, ros1_bridge, hybrid operation    | `references/migration-ros1.md`   |

**Cross-cutting concerns:** Security, error handling, and QoS are not isolated to single
reference files — use your judgment and apply them whenever the data path crosses a
trust boundary, a node owns hardware, or communication reliability matters.

## Core engineering principles

These apply to every ROS 2 artifact you produce, regardless of domain.

### 1. Distro awareness

<!-- LAST_UPDATED: 2026-07-15 — Review this table every 6 months or when a new distro is released. -->
<!-- NEXT_REVIEW: 2027-01-15 -->
> **Staleness warning:** The table below was last verified on **2026-07-15**.
> If the current date is more than 6 months past that, re-verify EOL dates and
> feature support against https://docs.ros.org/en/rolling/Releases.html before
> relying on this table. When you update it, change both `LAST_UPDATED` and
> `NEXT_REVIEW` comments above.

Identify which ROS 2 distribution the user targets — from the workspace
(Dockerfile, CI config, `/opt/ros/<distro>`) when possible, or by asking
when it is not inferable. Key differences:

| Feature                   | Humble (LTS)       | Jazzy (LTS)        | Kilted (non-LTS)   | Lyrical (LTS)      | Rolling            |
|---------------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
| EOL                       | May 2027           | May 2029           | Dec 2026           | May 2031           | Rolling            |
| Ubuntu                    | 22.04              | 24.04              | 24.04              | 26.04              | Latest             |
| Default DDS               | Fast DDS           | Fast DDS           | Fast DDS           | Fast DDS           | Fast DDS           |
| Zenoh support             | —                  | —                  | Tier 1             | Tier 1             | Tier 1             |
| Type description support  | No                 | Yes                | Yes                | Yes                | Yes                |
| Service introspection     | No                 | Yes                | Yes                | Yes                | Yes                |
| EventsExecutor            | No                 | Experimental       | Experimental (+ rclpy port) | EventsCBGExecutor (non-experimental, rclcpp) | Verify installed rclcpp |
| Default bag format        | sqlite3            | MCAP               | MCAP               | MCAP               | MCAP               |
| ros2_control interface    | 2.x                | 4.x                | 5.x                | 6.x (verify installed) | Latest         |
| CMake recommendation      | ament_target_deps  | either             | target_link_libs   | target_link_libs   | target_link_libs   |

Foxy (EOL June 2023, Ubuntu 20.04, ros2_control not bundled) is a migration
reference only — see the migration notes below. The pre-Lyrical
`EventsExecutor` lives in the `rclcpp::experimental` namespace on every
release that ships it; Lyrical adds the separate, non-experimental
`rclcpp::executors::EventsCBGExecutor`.

When the user does not specify, default to the latest LTS — **Lyrical Luth**
(Ubuntu 26.04); use **Jazzy** when the target platform is Ubuntu 24.04.
Pin the exact distro in Dockerfile, CI, and documentation so builds are reproducible.

### 2. C++ vs Python decision

Choose the language based on the node's role, not personal preference.
**rclcpp (C++)**: control loops ≥100 Hz, deterministic memory allocation
(real-time path), hardware drivers and controller plugins, intra-process
zero-copy. **rclpy (Python)**: orchestration, monitoring, parameter
management, rapid prototyping, Python-native ML frameworks — anything off
the latency-critical path.

**Mixed stacks are normal.** A typical robot has C++ drivers/controllers and Python
orchestration/monitoring. Note: `component_container` (composition) only loads
C++ components via pluginlib. Python nodes run as separate processes and
communicate over intra-host DDS — **not zero-overhead by default**: the
standard inter-process transport pays serialization, copies, and transport
bandwidth, and splitting work into another process does not by itself remove
encoding costs. Copy avoidance has three distinct mechanisms with different
preconditions: (1) the rclcpp **intra-process** path
(`use_intra_process_comms(true)`, same process) avoids copies only depending
on publish ownership (`unique_ptr`), callback type, subscriber count, and
QoS; (2) **loaned messages / vendor shared memory (SHM/PSMX)** are RMW- and
vendor-dependent and can avoid some or all copies when their preconditions
hold; (3) separate processes on the standard DDS transport get no copy
avoidance — crossing processes without copies requires the vendor
mechanisms in (2). Details: `references/nodes-executors.md`.

### 3. Package structure conventions

Follow the standard layout — `package.xml` (format 3, explicit `<depend>`
tags), `config/params.yaml`, `launch/*.launch.py`, `src/` +
`include/<pkg>/` for C++ or `<pkg>/` for Python, and `test/`. Keep custom
msg/srv/action definitions in a dedicated `*_interfaces` package so
downstream packages can depend on interfaces without the implementation.
Full annotated layout: `references/workspace-build.md`.

### 4. Parameter discipline

- Declare every parameter with a type, description, range, and default
  in the node constructor — never use undeclared parameters.
- Use `ParameterDescriptor` with `FloatingPointRange` or `IntegerRange`
  for numeric bounds. The parameter server rejects out-of-range values at set time.
- Group related parameters under a namespace prefix:
  `controller.kp`, `controller.ki`, `controller.kd`.
- Load defaults from a `config/params.yaml`; allow launch-time overrides.
- For dynamic reconfiguration, register a `set_parameters_callback` and
  validate new values atomically before accepting.

### 5. Error handling philosophy

- Nodes must not silently swallow errors. Log at the appropriate severity,
  then take a safe action (stop motion, request help, transition to error state).
- Prefer lifecycle node error transitions over ad-hoc boolean flags.
- When calling a service, always handle the "service not available" and
  "future timed out" cases explicitly.
- For hardware drivers, distinguish transient errors (retry with backoff)
  from fatal errors (transition to `FINALIZED` and alert the operator).

### 6. Quality of Service defaults

Start from these profiles and adjust per use case:

| Use case              | Reliability   | Durability       | History | Depth | Deadline    | Lifespan    |
|-----------------------|---------------|------------------|---------|-------|-------------|-------------|
| Sensor stream         | BEST_EFFORT   | VOLATILE         | KEEP_LAST | 5   | —           | —           |
| Command velocity      | RELIABLE      | VOLATILE         | KEEP_LAST | 1   | 100 ms      | 200 ms      |
| Map (latched)         | RELIABLE      | TRANSIENT_LOCAL  | KEEP_LAST | 1   | —           | —           |
| Diagnostics           | RELIABLE      | VOLATILE         | KEEP_LAST | 10  | —           | —           |
| Parameter events      | RELIABLE      | VOLATILE         | KEEP_LAST | 1000| —           | —           |
| Action feedback       | RELIABLE      | VOLATILE         | KEEP_LAST | 1   | —           | —           |
| Safety heartbeat      | RELIABLE      | VOLATILE         | KEEP_LAST | 1   | 500 ms      | 1 s         |

QoS mismatches are the #1 cause of "I published but nobody receives."
Always check compatibility with `ros2 topic info -v` when debugging.

**DEADLINE and LIFESPAN** are critical for safety-critical systems. DEADLINE fires an
event when no message arrives within the specified period (detect stale data). LIFESPAN
discards messages older than the specified duration before delivery (prevent acting on
stale data). See `references/communication.md` section 9 for full API and examples.

### 7. Naming conventions

| Entity      | Convention                  | Example                        |
|-------------|-----------------------------|--------------------------------|
| Package     | `snake_case`                | `arm_controller`               |
| Node        | `snake_case`                | `joint_state_broadcaster`      |
| Topic       | `/snake_case` with ns       | `/arm/joint_states`            |
| Service     | `/snake_case`               | `/arm/set_mode`                |
| Action      | `/snake_case`               | `/arm/follow_joint_trajectory` |
| Parameter   | `snake_case` with dot ns    | `controller.publish_rate`      |
| Frame       | `snake_case`                | `base_link`, `camera_optical`  |
| Interface   | `PascalCase.msg/srv/action` | `JointState.msg`               |

### 8. Thread safety and callbacks

- A `MutuallyExclusiveCallbackGroup` serializes its callbacks — safe for
  shared state without locks, but limits throughput.
- A `ReentrantCallbackGroup` allows parallel execution — you must protect
  shared state with `std::mutex` (C++) or `threading.Lock` (Python).
- **Calling a service from a callback:** If the callback registers the
  request asynchronously — rclcpp: `async_send_request(request, response_callback)`;
  rclpy: `future = client.call_async(request)` then
  `future.add_done_callback(...)` — and **returns without waiting for the
  result**, the same `MutuallyExclusiveCallbackGroup` does not deadlock.
  Deadlock comes from **waiting synchronously inside the callback** —
  rclcpp: calling `get()`/`wait()`/`wait_for()` on a **not-yet-complete**
  future from the initiating callback, or `spin_until_future_complete`
  (inside the response callback the future is already complete, so `get()`
  there is safe — the examples use exactly that); rclpy: synchronous
  `Client.call()`, `spin_until_future_complete`, or a loop that blocks
  until `future.done()`. (rclpy's `future.result()` by itself does not
  block — it immediately returns whatever result is currently stored,
  which may be unset.) A synchronous wait needs the
  client in a different callback group or a `ReentrantCallbackGroup`, plus
  a matching executor configuration (e.g. `MultiThreadedExecutor`). Do not assume plain-executor
  `async def` callback patterns are safe until tested with your executor;
  Lyrical's `rclpy.experimental.AsyncNode` is a separate execution model
  that officially supports `await client.call(...)` inside callbacks.
- Never do blocking work (file I/O, long computation, `sleep`) inside a
  timer or subscription callback on the default executor. Offload to a
  dedicated thread or use a `MultiThreadedExecutor` with a reentrant group.
- In rclcpp, prefer `std::shared_ptr<const MessageT>` in subscription
  callbacks to avoid unnecessary copies; whether intra-process delivery is
  actually copy-free additionally depends on publish ownership, subscriber
  count, and QoS (Principle 2).

### 9. Lifecycle-first design

Default to lifecycle (managed) nodes for anything that owns resources:
hardware drivers, sensor pipelines, planners, controllers. The managed
state machine (`unconfigured → inactive → active`, with `cleanup`,
`shutdown`, and error transitions) gives the system manager explicit
control over when resources are allocated, when processing starts, and how
shutdown proceeds — and makes error recovery predictable. Configure-only
transitions also enable hardware-safe config validation
(`references/testing.md` section 4). Full state diagram and callbacks:
`references/lifecycle-components.md`.

### 10. Build and CI hygiene

- Use `colcon build --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo` for
  development; `Release` for deployment.
- Enable `-Wall -Wextra -Wpedantic` and treat warnings as errors in CI.
- Run `colcon test` with `--event-handlers console_cohesion+` so test
  output groups by package.
- Pin rosdep keys in `rosdep.yaml` for reproducible dependency resolution.
- Cache `/opt/ros/`, `.ccache/`, and `build/`/`install/` in CI to cut build
  times by 60–80%.

### 11. Source-first behavior verification

Distro labels are not enough when exact behavior matters — patch releases
change parameter names, plugin behavior, and defaults. Before asserting how
an installed stack behaves, identify the installed version (`ros2 pkg xml`,
`dpkg-query -W`) and read what ships with it: reference configs, headers,
and the source tag matching that version. Worked Nav2 procedure:
`references/navigation.md` section 6.

### 12. Motion-safety defaults

Never generate configs that can move an unvalidated robot. Motion
recoveries (Spin/BackUp) stay opt-in until robot geometry, locomotion
response, and clearance are validated — actuation-free recovery comes
first. Velocity limits come from the safe operational ceiling, never the
SDK/API maximum. For hardware checks, prefer configure-only lifecycle
validation with hardware isolation (`references/testing.md` section 4).
Details: `references/navigation.md` sections 7 and 10.

## Common anti-patterns

| Anti-pattern | Why it hurts | Fix |
|---|---|---|
| Global variables for node state | Breaks composition, untestable | Store state as class members |
| `spin()` in `main()` for multi-node processes | Starves other nodes | Use `MultiThreadedExecutor` or component composition |
| Hardcoded topic names | Breaks reuse across robots | Use relative names + namespace remapping |
| `KEEP_ALL` history with no bound | Memory grows unbounded on slow subscribers | Use `KEEP_LAST` with explicit depth |
| Using `time.sleep()` / `std::this_thread::sleep_for` | Blocks the executor thread | Use `create_wall_timer` or a dedicated thread |
| Monolithic launch file for everything | Unmanageable past 10 nodes | Compose launch files with `IncludeLaunchDescription` |
| Skipping `package.xml` dependencies | Builds locally, breaks CI and Docker | Declare every dependency explicitly |
| Publishing in constructor | Subscribers may not be ready, messages lost | Publish in `on_activate` or after a short timer |
| Ignoring QoS compatibility | Silent communication failure | Match publisher/subscriber QoS or check with `ros2 topic info -v` |
| Creating timers/subs in callbacks | Resource leak, unpredictable behavior | Create all entities in constructor or `on_configure` |
| Synchronous service call in callback | Deadlocks the executor thread | Use `async_send_request` with a callback or dedicated thread |
| Waiting on a service future inside a callback | Synchronous waiting deadlocks a `MutuallyExclusiveCallbackGroup`; registering a response callback and returning is safe even in the same group | Return without waiting; if a synchronous wait is unavoidable, put the client in a different group (or reentrant) with a `MultiThreadedExecutor` |
| No safe command on shutdown | Motors hold last velocity after node exits | Send zero-velocity in `on_deactivate` and the destructor as best-effort hygiene; crash safety needs a downstream command timeout/watchdog (`references/safety-estop.md`) |
| Dynamic subscriptions with `StaticSingleThreadedExecutor` | New subs are never picked up after `spin()` | Use `SingleThreadedExecutor` or `MultiThreadedExecutor` for dynamic entities |
| CPU frequency governor left on `powersave`/`ondemand` | 10-100 ms latency spikes in RT path | Set `performance` governor, disable turbo boost (see `references/realtime.md`) |

## AI pitfalls — traps this skill has learned from

These are mistakes AI agents repeatedly make when generating ROS 2 code.
**Add a new line here every time a failure is discovered in practice.**

| # | Pitfall | What goes wrong | Correct approach |
|---|---------|----------------|-----------------|
| 1 | Using `spin_until_future_complete` inside a callback | Deadlocks the executor — the callback blocks waiting for a response that can never be delivered | Register a response callback and return without waiting; a separate callback group (or reentrant + `MultiThreadedExecutor`) is needed only when a synchronous wait is unavoidable |
| 2 | Generating Foxy-era API for Jazzy/Kilted | `node_executable` is deprecated, `export_state_interfaces()` signature changed in ros2_control 4.x | Always check the distro feature matrix above before generating code |
| 3 | Omitting QoS in publisher/subscriber creation | Defaults silently mismatch — publisher sends but subscriber receives nothing | Always specify QoS explicitly; use the QoS defaults table in Principle 6 |
| 4 | Creating a `msg/` directory inside a non-interfaces package | Builds locally but fails in CI — interface packages need `rosidl_generate_interfaces` | Put messages in a dedicated `*_interfaces` package |
| 5 | Hardcoding `/opt/ros/humble/` paths in launch files | Breaks on any other distro or install prefix | Use `FindPackageShare`, `PathJoinSubstitution`, or environment substitutions |
| 6 | Forgetting `<depend>` tags in `package.xml` | `colcon build` works in overlay but `rosdep install` and Docker builds fail | Declare every `find_package()` / `import` as `<depend>` in package.xml |
| 7 | Using `time.sleep()` for rate control in rclpy | Blocks the executor thread; timers and subscriptions stop firing | Use `create_timer()` or `Rate` with a `MultiThreadedExecutor` |
| 8 | Treating process-side cleanup as crash safety | Destructors never run on SIGKILL/power loss and are not guaranteed on segfaults — the robot keeps its last command | Zero-command in `on_deactivate` + destructor is best-effort hygiene only; require a downstream command timeout, heartbeat/watchdog, and hardware e-stop (`references/safety-estop.md`) |
| 9 | Mixing `ament_target_dependencies()` and `target_link_libraries()` | Kilted deprecated `ament_target_dependencies` — mixing causes link errors | Use `target_link_libraries()` with modern CMake targets for Kilted+; `ament_target_dependencies()` for Humble/Jazzy |
| 10 | Generating `rospy` / `roscpp` code instead of `rclpy` / `rclcpp` | ROS 1 patterns in a ROS 2 context — nothing compiles | This skill is ROS 2 only — always use `rclpy`/`rclcpp` APIs |
| 11 | Ignoring `use_sim_time` parameter in simulation | Real clock diverges from Gazebo clock — tf lookups fail, controllers drift | Set `use_sim_time:=true` in launch and pass `--clock` to `ros2 bag play` |
| 12 | Publishing before subscribers connect (no TRANSIENT_LOCAL) | First N messages lost — map, URDF, or initial config never received | Use `TRANSIENT_LOCAL` durability for latched-style data, or publish in `on_activate` with a startup delay |
| 13 | Writing Nav2 names from memory (`recoveries_server`/`nav2_recoveries/` on Humble, pre-Galactic `default_bt_xml_filename`) | Parameters silently ignored or plugin loading fails at configure | Humble+ uses `behavior_server`/`nav2_behaviors/` and `default_nav_to_pose_bt_xml`; verify against the installed version (Principle 11) |
| 14 | Enabling Spin/BackUp recoveries by default on an unvalidated robot | Robot suddenly rotates or reverses in the field — the recovery, not path following, is at fault | Motion recoveries are opt-in after validation; actuation-free recovery first (Principle 12) |

> **Maintenance rule:** When you encounter a new AI failure pattern while using this
> skill, append it to this table with the next sequential number. The pitfall list
> is the single most valuable section for preventing repeated mistakes.

## Distro-specific migration notes

<!-- LAST_UPDATED: 2026-03-30 — Keep in sync with the distro table in Principle 1. -->
When upgrading between distributions, check these breaking changes first:

- **Foxy → Humble:** complete API overhaul (lifecycle, actions stabilized in Humble);
  `ros2_control` was not bundled in Foxy; Nav2 renamed `recoveries_server` →
  `behavior_server` and `nav2_recoveries/` → `nav2_behaviors/` (Galactic → Humble
  migration — pre-Humble recovery naming does not exist on Humble). Plan a rework,
  not a port.
- **Humble → Jazzy:** `ros2_control` 2.x → 4.x — interface exports auto-generated,
  `get_value()` → `get_optional<T>()`, spawner uses `--param-file`, all `<ros2_control>`
  joints must exist in the URDF (details: `references/hardware-interface.md`); default
  bag format sqlite3 → **MCAP** (`storage_id='mcap'`); `ROS_AUTOMATIC_DISCOVERY_RANGE`
  replaces `ROS_LOCALHOST_ONLY`; `launch_ros` parameter handling changed — retest
  launch files.
- **Jazzy → Kilted (non-LTS):** Zenoh Tier 1 (`RMW_IMPLEMENTATION=rmw_zenoh_cpp`);
  experimental EventsExecutor gains an rclpy port (still `rclcpp::experimental`);
  `ament_target_dependencies()` deprecated — use `target_link_libraries()` with modern
  CMake targets; Gazebo pairing is **Ionic** (Harmonic was Jazzy); multi-bag replay in
  `ros2 bag play`.
- **Kilted → Lyrical (LTS):** primary platform moves to Ubuntu 26.04; default RMW
  stays `rmw_fastrtps_cpp`; new non-experimental `rclcpp::executors::EventsCBGExecutor`
  (distinct from the experimental EventsExecutor); ros2_control moves to the 6.x
  series — verify per-package changes against the installed versions (Principle 11).
- **ROS 1 → ROS 2:** see `references/migration-ros1.md` for a step-by-step strategy.

## Quick reference — ros2 CLI

See **`references/debugging.md` §10 "Quick CLI reference"** for the full
command cheat sheet (workspace, introspection, ros2_control, debugging,
lifecycle). Kept out of this always-loaded file to preserve context budget.
