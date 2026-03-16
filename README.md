# ros2-engineering-skills

Agent skill for production-grade ROS 2 development — from first workspace to fleet deployment.

Works with [Claude Code](https://code.claude.com), [Codex](https://developers.openai.com/codex), [Cursor](https://cursor.sh), [Gemini CLI](https://github.com/google-gemini/gemini-cli), and any agent supporting the [Agent Skills](https://agentskills.io) standard.

## What this is

A `SKILL.md`-based knowledge module that gives AI coding agents deep ROS 2 engineering expertise. Instead of a shallow cheat sheet, it provides:

- **Decision frameworks** — when to use rclcpp vs rclpy, which QoS profile, lifecycle vs plain node
- **Progressive disclosure** — compact routing in `SKILL.md`, detailed patterns in `references/`
- **Full spectrum** — workspace setup through real-time tuning, Nav2, MoveIt 2, ros2_control, DDS configuration, cross-compilation, and CI/CD
- **Distro-aware** — explicit Humble / Jazzy / Rolling differences with migration paths
- **Anti-pattern documentation** — what breaks in production and why

## How it differs from existing ROS 2 skills

| Aspect | Typical ROS 2 skill | This project |
|---|---|---|
| Depth | Basic QoS + lifecycle intro | DDS vendor tuning, custom executors, intra-process zero-copy |
| Scope | Single SKILL.md file | 15+ reference files via progressive disclosure |
| Hardware | Mentioned in passing | ros2_control hardware interface patterns, serial/CAN/EtherCAT |
| Real-time | Not covered | PREEMPT_RT, memory allocation, callback group strategies |
| Testing | "Use pytest" | launch_testing, gtest, integration patterns, CI caching |
| Deployment | Not covered | Docker multi-stage, cross-compile, fleet OTA |

## Installation

### Claude Code
```bash
# From plugin marketplace
claude plugin marketplace add dbwls99706/ros2-engineering-skills
claude plugin install ros2-engineering@ros2-engineering-skills

# Or clone directly
git clone https://github.com/dbwls99706/ros2-engineering-skills.git ~/.claude/skills/ros2-engineering-skills
```

### Codex / Gemini CLI / OpenCode
```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git ~/.agents/skills/ros2-engineering-skills
```

### Cursor
```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git
# Add to .cursor/rules/ros2-engineering-skills
```

### Any project (symlink)
```bash
ln -s /path/to/ros2-engineering-skills .claude/skills/ros2-engineering-skills
```

## Structure

```
ros2-engineering-skills/
├── SKILL.md                        # Entry point — decision router + core principles
├── references/
│   ├── workspace-build.md          # colcon, ament_cmake, package.xml, overlays
│   ├── nodes-executors.md          # rclcpp/rclpy nodes, executors, callback groups
│   ├── communication.md            # Topics, services, actions, QoS, custom interfaces
│   ├── lifecycle-components.md     # Managed nodes, component loading, composition
│   ├── launch-system.md            # Python launch API, conditions, events, large systems
│   ├── tf2-urdf.md                 # Transforms, URDF, xacro, robot_state_publisher
│   ├── hardware-interface.md       # ros2_control, HW interfaces, controller plugins
│   ├── realtime.md                 # RT kernel, memory, jitter, deterministic execution
│   ├── navigation.md               # Nav2, SLAM, costmaps, BT navigator
│   ├── manipulation.md             # MoveIt 2, planning scene, grasp pipelines
│   ├── perception.md               # image_transport, PCL, cv_bridge, depth
│   ├── testing.md                  # gtest, pytest, launch_testing, CI/CD
│   ├── debugging.md                # ros2 doctor, tracing, profiling, rosbag2
│   ├── deployment.md               # Docker, cross-compile, fleet management
│   └── migration-ros1.md           # ROS 1 → ROS 2 strategy, ros1_bridge
├── scripts/
│   ├── create_package.py           # Scaffold a package with best-practice structure
│   ├── qos_checker.py              # Verify QoS compatibility between pub/sub pairs
│   └── launch_validator.py         # Static analysis for launch file issues
├── LICENSE
└── README.md
```

## Current status

**Phase 1 (complete):** 4 fully written reference files — workspace-build, nodes-executors, communication, hardware-interface — plus the core SKILL.md decision router and `create_package.py` scaffolder.

**Phase 2 (in progress):** 11 additional reference files planned — lifecycle-components, launch-system, tf2-urdf, realtime, navigation, manipulation, perception, testing, debugging, deployment, migration-ros1.

## Supported ROS 2 distributions

- **Jazzy Jalisco** (LTS, recommended) — primary target
- **Humble Hawksbill** (LTS) — fully supported
- **Foxy Fitzroy** (LTS, EOL June 2023) — referenced for legacy migration
- **Rolling Ridley** — latest features, noted where they diverge

## Contributing

Contributions welcome. Please:

1. Keep `SKILL.md` under 500 lines — add depth in `references/`
2. Include working code examples, not pseudocode
3. Document anti-patterns alongside correct patterns
4. Note which ROS 2 distros your change applies to
5. Test with at least one agent (Claude Code, Codex, etc.)

## License

Apache-2.0 — see [LICENSE](LICENSE).