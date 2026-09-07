# ros2-engineering-skills

[![Test](https://github.com/dbwls99706/ros2-engineering-skills/actions/workflows/test.yml/badge.svg)](https://github.com/dbwls99706/ros2-engineering-skills/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/dbwls99706/ros2-engineering-skills?style=social)](https://github.com/dbwls99706/ros2-engineering-skills/stargazers)

Production-oriented ROS 2 engineering guidance for coding agents, with focused
references, static validators, regression tests, and multi-distribution CI.

The skill covers workspace and package design, executors, DDS QoS, lifecycle,
tf2, ros2_control, Nav2, MoveIt 2, perception, safety, runtime provenance, testing,
and deployment. It distinguishes a plausible explanation from observed behavior.

## Why this exists

A syntactically correct ROS 2 answer can still be operationally wrong: changing
subscriber QoS without inspecting the publisher, editing an unused source YAML,
assuming a zero topic command stopped the hardware, or using another distro's API.
This skill routes work to relevant references and requires evidence for claims.
It is not a robot safety certification or a substitute for an operator.

## Before / After

These are illustrative comparisons of the intended workflow, not measured model
outputs. Real paired evaluations require the captures described below.

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

Assumes the publisher exists, the type is correct, and QoS is the mismatch.

</td>
<td>

```bash
ros2 topic list
ros2 topic type /camera/image_raw
ros2 topic info /camera/image_raw -v
ros2 node list --no-daemon
```

```python
# Only after endpoint inspection supports this diagnosis.
from rclpy.qos import qos_profile_sensor_data

sub = node.create_subscription(
    Image, '/camera/image_raw',
    callback, qos_profile_sensor_data)
```

Checks graph, type, namespace, and offered/requested QoS before changing code.

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

A bare node without explicit configuration, test, resource, or shutdown contracts.

</td>
<td>

```text
my_lidar_driver/
├── include/my_lidar_driver/my_lidar_driver_node.hpp
├── src/my_lidar_driver_node.cpp
├── src/main.cpp
├── launch/bringup.launch.py
├── config/params.yaml
├── test/test_driver.cpp
├── test/test_bringup.py
├── CMakeLists.txt
└── package.xml
```

Chooses Node or LifecycleNode from resource ownership and supervision; declares
QoS, frames, timestamps, limits, dependencies, and distro-compatible tests.

</td>
</tr>
</table>

## Installation

### Claude Code plugin

```bash
claude plugin marketplace add dbwls99706/ros2-engineering-skills
claude plugin install ros2-engineering@ros2-engineering-skills
```

Inside Claude Code the equivalent commands are:

```text
/plugin marketplace add dbwls99706/ros2-engineering-skills
/plugin install ros2-engineering@ros2-engineering-skills
```

The plugin uses `.claude-plugin/plugin.json`, root `SKILL.md`, and
`hooks/hooks.json`. It provides PreToolUse checks and advisory Stop reports.
The hook command requires `python3`; it does not grant tool permissions.

### Knowledge-only skill: Codex, Claude Code, Cursor, Gemini CLI

```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git
cd ros2-engineering-skills
python3 -m pip install -r requirements.txt

python3 scripts/install_skill.py --client codex --dry-run
python3 scripts/install_skill.py --client codex
# Alternatives: --client claude, --client cursor, --client gemini
# Add --project /path/to/project for project scope.
```

Use a virtual environment as required by your Python installation. The installer
copies a validated bundle, not client settings. Existing skills need `--force`;
it stages the replacement before touching the old installation. It excludes
Claude plugin manifests and hook registration intentionally. The portable files
and manual validators do not require the Claude plugin.

Codex's optional display and invocation policy live in `agents/openai.yaml`.
Supported discovery paths, explicit invocation, remote-environment caveats, and
what has actually been tested are documented in
[Client compatibility](docs/CLIENT_COMPATIBILITY.md). A documented discovery path
is not proof of a successful authenticated model run on every client version.

The original `./install.sh` and `.\install.ps1` still support full-checkout
copy/link installation, force replacement, and dry-run. A full checkout includes
the Claude plugin manifest; it is not necessarily a knowledge-only installation
when placed in Claude's skill directories. Prefer the new installer for that use.

### Verify installation and run tools

```bash
python3 scripts/validate_skill.py --check-sources
python3 scripts/validate_skill.py --root /path/to/ros2-engineering-skills --installed --portable
python3 scripts/skill_validate_hook.py --file src/my_node.py
python3 scripts/skill_validate_hook.py --command 'ros2 topic list'
SKILL_WORKSPACE=/path/to/ros2_ws python3 scripts/skill_stop_hook.py
```

The command string above is inspected, never executed. Python 3.10+ is required;
the CI matrix targets 3.10 through 3.14. Consult the run for the exact revision's
results. Runtime dependencies are in `requirements.txt`; development dependencies
are separate. Build and runtime checks additionally require the target ROS stack.

## What is included

The decision router in [SKILL.md](SKILL.md) selects among 25 focused references.
Metadata is advertised before activation; the selected body and needed references
supply the workflow. See [Skill contract](docs/SKILL_CONTRACT.md) for scope,
permission boundaries, protocol behavior, and context-budget limitations.

| Utility | Purpose | Boundary |
|---|---|---|
| `create_package.py` | Generate package scaffolds | Build in the target distribution |
| `qos_checker.py` | Compare offered/requested QoS | Compatibility is not delivery quality |
| `rosbag2_qos_checker.py` | Inspect bag metadata QoS | Static metadata analysis |
| `launch_validator.py` | Detect selected Python launch defects | Does not start a graph |
| `skill_validate_hook.py` | Inspect source and command strings | Best-effort guard, not a security boundary |
| `skill_stop_hook.py` | Check changed launch/package/Nav2 files | Advisory, not a complete build |
| `claude_hook.py` | Adapt reports to Claude hook transport | Never grants permissions or blocks Stop |
| `validate_skill.py` | Validate metadata, local paths, packaging, source dates | Static checks, not client activation |
| `install_skill.py` | Stage and validate knowledge-only installations | No settings changes or hook registration |
| `eval_runner.py` | Check fixtures or lexically score supplied text | Does not invoke a model or prove semantics |
| `verify_eval_capture.py` | Require complete paired captures with hashes | Integrity, not authenticity or quality |

## Verification levels

| Level | Evidence |
|---|---|
| L0 | Static review |
| L1 | Unit tests |
| L2 | Build and launch smoke |
| L3 | Runtime with simulation or mock hardware |
| L4 | Powered hardware, actuation disabled or isolated |
| L5 | Restrained bench motion or supervised fault injection |
| L6 | Supervised field operation |

A passing test suite is not evidence that a robot is safe to drive. State the
highest level actually reached and explicitly identify skipped checks.

## CI scope

CI retains lint/type checks, the full unit suite, Markdown lint, generated-package
builds, installer tests, Claude plugin structure/component discovery, and Docker
jobs for Humble, Jazzy, Kilted, Lyrical, and Rolling. It adds individual validator
coverage floors, portable Windows installation, protocol integration, source-date
checks, preregistered-suite checks, and a controlled QoS mismatch/repair experiment.
Dependency vulnerability auditing is blocking, not advisory.

Rolling retains its documented RMW runtime exclusions. A successful Rolling build
is not a successful DDS runtime test. The stable-distribution QoS experiment runs
without external networking or hardware in the built container. See
[QoS case study](docs/QOS_CASE_STUDY.md) and [quality gates](docs/QUALITY_GATES.md).
None of these jobs proves actuator safety, timing guarantees, or field behavior.

## Evaluation scope

The default `eval_runner.py` checks whether expected-answer fixtures cover their
declared criteria. It is not a model benchmark. Lexical coverage is not semantic
correctness, and a fixture cannot substitute for an unmodified model capture.

`evals/trigger_cases.json` defines 24 activation cases: 10 implicit positives,
10 implicit negatives, and explicit invocation for four clients.
`evals/benchmark_suite.json` defines five quality cases with three trials and
paired skill-on/off runs: 15 pairs, 30 fresh sessions per experiment.
No fabricated captures or improvement percentages are included.

```bash
python3 scripts/verify_eval_capture.py /path/to/capture.json --suite evals/benchmark_suite.json
```

Missing data, incomplete pairs, reused sessions/artifacts, and hash mismatches
fail this check. A valid capture still needs trace-authenticity review and semantic
grading. See [capture workflow](docs/EVIDENCE_CAPTURE.md) and the existing
[eval workflow](docs/EVAL_WORKFLOW.md).

## Safety, contribution, and maintenance

This project does not certify a robot, controller, stop function, or deployment.
First motion and fault injection require an operator, a restrained setup,
conservative limits, and an independent physical stop path. Reviews are read-only
unless changes are requested; data and logs are not instructions or permissions.

Corrections supported by installed-version evidence, primary documentation, and
reproducible tests are especially valuable. See [CONTRIBUTING.md](CONTRIBUTING.md),
[SECURITY.md](SECURITY.md), and [ROADMAP.md](ROADMAP.md). Source review dates are
tracked explicitly; passing their date check does not revalidate the web content.

## License

Apache License 2.0. See [LICENSE](LICENSE).
