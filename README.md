# ros2-engineering-skills

[![Test](https://github.com/dbwls99706/ros2-engineering-skills/actions/workflows/test.yml/badge.svg)](https://github.com/dbwls99706/ros2-engineering-skills/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/dbwls99706/ros2-engineering-skills?style=social)](https://github.com/dbwls99706/ros2-engineering-skills/stargazers)

Production-grade ROS 2 engineering guidance for coding agents, with focused
reference documents, static validators, regression tests, and a
multi-distribution ROS 2 CI matrix.

The project is intended for work that crosses ROS 2 layers: package and launch
structure, executors and callback groups, DDS QoS, lifecycle, tf2, ros2_control,
Nav2, MoveIt 2, perception, hardware safety, runtime provenance, testing, and
deployment.

## Why this exists

A syntactically correct ROS 2 answer can still be operationally wrong. Common
examples include:

- changing subscriber QoS without first inspecting the live publisher;
- editing a YAML file in `src/` while the process loads another installed copy;
- treating a zero command on a topic as proof that the driver and hardware
  stopped;
- generating APIs for a different ROS 2 distribution or package version;
- reporting static checks as if they were hardware validation.

This skill makes those evidence boundaries explicit and routes each task to the
smallest relevant reference document.

## Before / After

> **Prompt:** *"My ROS 2 subscriber isn't receiving any sensor messages. Help me fix it."*

<table>
<tr>
<th width="50%">Without this skill</th>
<th width="50%">With this skill loaded</th>
</tr>
<tr>
<td>

```python
# Guess at the fix before inspecting the live endpoints.
from rclpy.qos import qos_profile_sensor_data

sub = node.create_subscription(
    Image, '/camera/image_raw',
    callback, qos_profile_sensor_data)
```

May work by accident, but assumes the publisher exists, the topic type is
correct, and BEST_EFFORT QoS is the actual mismatch.

</td>
<td>

```bash
ros2 topic list
ros2 topic type /camera/image_raw
ros2 topic info /camera/image_raw -v
ros2 node list --no-daemon
```

```python
# Apply this only when endpoint inspection shows a
# BEST_EFFORT publisher that the subscriber must match.
from rclpy.qos import qos_profile_sensor_data

sub = node.create_subscription(
    Image, '/camera/image_raw',
    callback, qos_profile_sensor_data)
```

Checks graph existence, type, namespaces, and offered/requested QoS first, then
changes code only when the observed endpoints support that diagnosis.

</td>
</tr>
</table>

> **Prompt:** *"Create a C++ driver package for my LiDAR sensor."*

<table>
<tr>
<th width="50%">Without this skill</th>
<th width="50%">With this skill loaded</th>
</tr>
<tr>
<td>

```text
my_lidar_driver/
├── src/main.cpp
├── CMakeLists.txt
└── package.xml
```

A bare node with no launch/config split, tests, explicit QoS and frame contract,
or documented resource and shutdown behavior.

</td>
<td>

```text
my_lidar_driver/
├── include/my_lidar_driver/
│   └── my_lidar_driver_node.hpp
├── src/
│   ├── my_lidar_driver_node.cpp
│   └── main.cpp
├── launch/bringup.launch.py
├── config/params.yaml
├── test/
│   ├── test_driver.cpp
│   └── test_bringup.py
├── CMakeLists.txt
└── package.xml
```

Chooses `Node` or `LifecycleNode` from resource ownership and supervisor design,
declares QoS, frames, timestamps, limits, and dependencies, and adds
distro-compatible build, launch, configuration, and test scaffolding.

</td>
</tr>
</table>

## Installation

### Claude Code plugin

```bash
claude plugin marketplace add dbwls99706/ros2-engineering-skills
claude plugin install ros2-engineering@ros2-engineering-skills
```

Inside Claude Code, the equivalent commands are:

```text
/plugin marketplace add dbwls99706/ros2-engineering-skills
/plugin install ros2-engineering@ros2-engineering-skills
```

The plugin manifest lives at `.claude-plugin/plugin.json`. Claude-specific
hooks live at `hooks/hooks.json`; they are not part of the portable Agent
Skills metadata.

### Agent Skills clients

For clients configured to discover skills under `~/.agents/skills`:

```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git \
  ~/.agents/skills/ros2-engineering-skills
```

The root `SKILL.md` and `references/` are platform-neutral. Automatic discovery,
triggering, tool permissions, and hook support remain client-specific.

### Install from a checkout

Clone once, then copy or link the checkout into a client-specific skill path:

```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git
cd ros2-engineering-skills

# Default: ~/.agents/skills/ros2-engineering-skills
./install.sh

# Project-local symbolic link
./install.sh --target /path/to/project/.agents/skills/ros2-engineering-skills --link
```

Windows PowerShell provides the same copy, link, force, and dry-run controls:

```powershell
.\install.ps1
.\install.ps1 -Target C:\path\to\skills\ros2-engineering-skills -Link
```

Existing targets are preserved unless `--force` or `-Force` is supplied. Use
`--dry-run` or `-DryRun` to inspect the operation without changing files.

### Manual validator use

```bash
# Inspect source files for selected ROS 2 anti-patterns
python3 scripts/skill_validate_hook.py --file src/my_node.py

# Inspect a command string without executing it
python3 scripts/skill_validate_hook.py --command 'ros2 topic list'

# Validate selected workspace artifacts after changes
SKILL_WORKSPACE=/path/to/ros2_ws python3 scripts/skill_stop_hook.py
```

Python 3.10 or newer is required for the bundled validators. CI currently tests
Python 3.10, 3.11, and 3.12. Build and runtime checks require the target ROS 2
environment.

## What is included

### Knowledge

The decision router in [`SKILL.md`](SKILL.md) links to 25 focused references:

- workspaces, packages, nodes, executors, communication, QoS, and DDS;
- lifecycle, launch, tf2, URDF, ros2_control, and real-time behavior;
- Nav2, MoveIt 2, perception, sensor integration, and simulation;
- security, emergency stop design, micro-ROS, and multi-robot systems;
- testing, debugging, runtime provenance, system diagnosis, and deployment;
- system bringup, message conventions, and ROS 1 migration.

### Utilities

| Utility | Purpose | Boundary |
|---|---|---|
| `create_package.py` | Scaffold ROS 2 package layouts | Generated structure must still be built in the target distribution |
| `qos_checker.py` | Evaluate publisher/subscriber QoS compatibility | Does not prove delivery quality or semantic correctness |
| `rosbag2_qos_checker.py` | Compare bag metadata with playback subscribers | Static metadata analysis |
| `launch_validator.py` | Detect selected Python launch mistakes | Does not import every plugin or start the graph |
| `skill_validate_hook.py` | Inspect edits and command strings | Best-effort guard, not a security boundary |
| `skill_stop_hook.py` | Check launch, package, and selected Nav2 YAML issues | Lightweight validation, not a complete build |
| `eval_runner.py` | Validate eval fixtures or score supplied captures | Does not invoke a model by itself |

## Verification levels

The skill uses a seven-level evidence ladder:

| Level | Evidence |
|---|---|
| L0 | Static review |
| L1 | Unit tests |
| L2 | Build and launch smoke test |
| L3 | Runtime with simulation or mock hardware |
| L4 | Powered hardware with actuation disabled or isolated |
| L5 | Restrained bench motion or supervised fault injection |
| L6 | Supervised field operation |

A passing test suite is not evidence that a robot is safe to drive. Responses
should state the highest level actually reached.

## CI scope

The workflow contains these gates:

- flake8 and mypy;
- Python 3.10, 3.11, and 3.12 unit-test matrix with script coverage;
- Markdown lint;
- package-generation, installer, and validator smoke checks;
- Docker integration builds for Humble, Jazzy, Kilted, Lyrical, and Rolling.

The Docker jobs verify the generated packages and tools in those containers.
They do not establish device compatibility, real-time performance, actuator
safety, or field behavior.

## Evaluation scope

The default `eval_runner.py` mode checks whether expected-answer fixtures cover
the criteria declared in `evals/eval.yaml`. It is a fixture-integrity check,
not a model benchmark.

Real comparisons require immutable skill-on and skill-off outputs plus the
client, model, date, repository revision, prompt, environment, criteria, and
limitations. See [`docs/EVAL_WORKFLOW.md`](docs/EVAL_WORKFLOW.md).

## Repository structure

```text
.
├── SKILL.md
├── references/
├── scripts/
├── hooks/hooks.json
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── install.sh
├── install.ps1
├── evals/
├── tests/
└── .github/workflows/test.yml
```

## Safety

This repository provides engineering guidance and static tooling. It does not
certify a robot, controller, emergency-stop function, or deployment. First
motion and fault injection on physical hardware require an operator, a
restrained test setup, conservative limits, and an independent physical stop
path.

## Contributing

Corrections supported by upstream documentation, installed-version evidence,
or reproducible tests are especially valuable. See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

Security-sensitive reports should follow [`SECURITY.md`](SECURITY.md).

## Roadmap

Planned work is tracked in [`ROADMAP.md`](ROADMAP.md). The priorities are
reproducible client-trigger tests, published skill-on/skill-off captures,
distribution-sensitive factual audits, and real workspace case studies.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
