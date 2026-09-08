# Safety and E-Stop Systems

> **Distro stability:** every pattern in this guide (QoS events, twist_mux, SROS2
> permissions) works identically on Humble, Jazzy, Kilted, and Rolling. Distro-sensitive
> details are tagged inline.

This guide covers designing the stop path of a robot: hardware vs software e-stop,
heartbeat-based e-stop topics with enforcing QoS, command arbitration so a stop always
wins, SROS2 isolation so only the safety node can command (or clear) a stop, and reset
semantics. SROS2 keystore/enclave mechanics live in `references/security.md` §2 and §5 —
this file covers the *safety architecture* built on top of them.

## Table of contents

1. [E-stop system architecture](#1-e-stop-system-architecture)
2. [E-stop topic design](#2-e-stop-topic-design)
3. [Command arbitration](#3-command-arbitration)
4. [SROS2 e-stop isolation](#4-sros2-e-stop-isolation)
5. [Recovery and reset semantics](#5-recovery-and-reset-semantics)
6. [Testing the stop path](#6-testing-the-stop-path)
7. [Common failures and fixes](#7-common-failures-and-fixes)

---

## 1. E-stop system architecture

### Software e-stop is NOT safety-rated

State this in every design review: a ROS 2 e-stop runs on a non-real-time OS, over a
best-effort network, through software with no certified failure analysis. Standards for
machine safety (ISO 13849 performance levels, IEC 62061 SIL) require a hardware safety
chain — physically-wired e-stop buttons, safety relays or a safety PLC, and motor
drivers whose STO (Safe Torque Off) input cuts torque independently of the compute.

The two layers have different jobs:

```text
┌────────────────────────────────────────────────────────────┐
│ HARDWARE SAFETY CHAIN (safety-rated, no software involved) │
│  e-stop buttons ─► safety relay ─► motor driver STO        │
│  Stops the robot even if every computer is frozen.         │
└────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────┐
│ SOFTWARE E-STOP (ROS 2 — protective stop, not a substitute)│
│  /e_stop heartbeat ─► arbitration mux ─► zero commands     │
│  Faster, remote-triggerable, recoverable without a         │
│  power cycle; catches faults the hardware chain cannot     │
│  see (bad plan, runaway node, geofence breach).            │
└────────────────────────────────────────────────────────────┘
```

Design both, and make the software layer *report* the hardware layer's state (the
safety PLC's status output wired to a GPIO/fieldbus input) so operators see one
picture. Never route the hardware chain *through* ROS.

### Fail-safe means "silence stops the robot"

The single most important design rule: the robot must stop when the safety signal
**disappears**, not when a stop message arrives. A "send `true` to stop" topic fails
dangerous — crash the safety node, unplug the radio, or partition the network and the
robot never receives the stop. A heartbeat fails safe: no heartbeat, no motion.

```python
# BAD — fail-dangerous: a lost message or dead node means the robot keeps moving
if msg.emergency_stop:
    self.stop_motors()

# GOOD — fail-safe: motion is *enabled* by a fresh heartbeat, stop is the default
# (implemented with QoS DEADLINE below — no hand-rolled timeout bookkeeping)
```

## 2. E-stop topic design

### Heartbeat with enforcing QoS

Use the safety-heartbeat QoS profile from `references/engineering-principles.md`
Principle 6: RELIABLE, VOLATILE, KEEP_LAST/1, DEADLINE 500 ms, LIFESPAN 1 s.
DEADLINE turns "the heartbeat stopped" into a middleware event; LIFESPAN prevents
a stale queued message from being read as a fresh permit after a hiccup.

```cpp
// safety_heartbeat_publisher — runs on the safety node (operator station or
// safety supervisor). Publishing FROM the decision loop, so a hung loop stops
// the heartbeat (same rule as references/system-bringup.md §4).
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/bool.hpp>

using namespace std::chrono_literals;

class SafetySupervisor : public rclcpp::Node
{
public:
  SafetySupervisor() : Node("safety_supervisor")
  {
    rclcpp::QoS qos(rclcpp::KeepLast(1));
    qos.reliable()
       .deadline(500ms)          // consumers get an event if we go silent
       .lifespan(1s);            // stale permits are never delivered
    permit_pub_ = create_publisher<std_msgs::msg::Bool>("/safety/motion_permit", qos);

    timer_ = create_wall_timer(200ms, [this] {   // 2.5x margin under the deadline
      std_msgs::msg::Bool permit;
      permit.data = checks_pass();   // geofence, operator e-stop UI, HW chain status
      permit_pub_->publish(permit);  // data==false OR silence both mean STOP
    });
  }

private:
  bool checks_pass();
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr permit_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};
```

```cpp
// Consumer side — the base controller (or a dedicated estop_gate node).
// Two triggers, one handler: an explicit false OR a missed deadline.
rclcpp::QoS qos(rclcpp::KeepLast(1));
qos.reliable().deadline(500ms).lifespan(std::chrono::seconds(1));

rclcpp::SubscriptionOptions options;
options.event_callbacks.deadline_callback =
  [this](rclcpp::QOSDeadlineRequestedInfo &) {
    engage_estop("heartbeat lost");         // fail-safe: silence == stop
  };

permit_sub_ = create_subscription<std_msgs::msg::Bool>(
  "/safety/motion_permit", qos,
  [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
    if (!msg->data) { engage_estop("permit revoked"); }
    else { last_permit_ = now(); }
  },
  options);
```

> **RxO reminder:** DEADLINE is request-vs-offered. The publisher must *offer* a
> deadline ≤ the subscriber's requested 500 ms or the pair silently never matches —
> the #1 cause of "my e-stop subscriber receives nothing." Verify with
> `ros2 topic info /safety/motion_permit -v`.

### Latched stop state alongside the heartbeat

The heartbeat says "motion is permitted *right now*." Operators and late-joining nodes
also need "is the system currently e-stopped, and why" — publish that as latched state:

```python
# Latched e-stop state — TRANSIENT_LOCAL so a node started mid-incident sees it
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

estop_state_qos = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL)
self.state_pub = self.create_publisher(EstopState, '/safety/estop_state', estop_state_qos)
```

Two topics, two jobs: `/safety/motion_permit` (heartbeat, gates motion) and
`/safety/estop_state` (latched, informs humans and UIs). Do not merge them — a latched
topic cannot be a heartbeat, and a heartbeat cannot inform late joiners.

## 3. Command arbitration

An e-stop that publishes zero velocity *once* loses the race against a planner
publishing at 20 Hz. Arbitrate all command sources through a priority multiplexer so
the stop path structurally outranks everything.

### twist_mux priority configuration

```yaml
# config/twist_mux.yaml
twist_mux:
  ros__parameters:
    topics:
      navigation:
        topic: /cmd_vel_nav        # Nav2 output
        timeout: 0.5
        priority: 10
      teleop:
        topic: /cmd_vel_teleop     # operator joystick overrides autonomy
        timeout: 0.5
        priority: 100
      watchdog_stop:
        topic: /cmd_vel_watchdog   # heartbeat watchdog's zero command
        timeout: 0.5
        priority: 200
    locks:
      # A lock is stronger than any topic priority: while /safety/estop_active
      # is true (or SILENT past its timeout!), every lower-priority source is masked.
      estop:
        topic: /safety/estop_active
        timeout: 0.5               # lock also engages if the safety node dies
        priority: 255
```

```bash
sudo apt install ros-${ROS_DISTRO}-twist-mux
ros2 run twist_mux twist_mux --ros-args --params-file config/twist_mux.yaml \
  -r cmd_vel_out:=/cmd_vel      # only the mux publishes the real /cmd_vel
```

The lock's `timeout` gives arbitration the same fail-safe property as the heartbeat:
a dead safety node engages the lock. (Jazzy+ ships `twist_mux` with `TwistStamped`
support via the `use_stamped` parameter; Humble's release is unstamped `Twist` only.)

### Close the bypass hole

Arbitration only works if the mux is the *sole* publisher on the real command topic.
Remap every producer onto its mux input and enforce it:

```bash
# Audit: exactly one publisher (the mux) may appear here
ros2 topic info /cmd_vel -v
```

On a secured system, make the bypass impossible instead of just audited: only the mux's
enclave gets publish permission on `/cmd_vel` (Section 4).

### Zero on a topic is not a stopped robot

`ros2 topic echo /cmd_vel` showing zeros proves one thing: a message was
published. It is not evidence that the robot stopped, and treating it as such is
how a stop path passes review and fails in the field. Four links sit between the
message and a stationary machine, and each one has to be verified on its own.

**1. Command ownership matches the declared architecture.** With two publishers
there is no defined command priority. Without an arbiter the subscriber can
process commands from every compatible publisher, and which one takes effect
depends on message and callback processing timing — so a one-shot zero is
routinely superseded by a planner republishing at 20 Hz. The
observed active publisher set must match the command-ownership architecture you
declared — normally exactly one authorized arbiter on the driver-facing command
topic. **Under the single-arbiter invariant this guide recommends, more than one
active driver-facing command publisher is a failure.** (Redundant or
hot-standby designs exist; they need their own written ownership rule, not the
absence of one.) Verify the count against that rule, do not assume it:

```bash
ros2 topic info /cmd_vel -v     # single-arbiter design: exactly 1 (the mux)
```

Three distinct activities, easy to collapse into one and wrong when you do:
**observe** current publisher ownership with `ros2 topic info -v`; **constrain**
unauthorized publishers with an enabled SROS2 policy (Section 4); **verify that
enforcement separately** at the appropriate safety level — a policy that is not
actually being enforced looks identical to one that is.

The observation is also **point-in-time**. A publisher that appears for 200 ms
during a reconnect or a node restart will not show up in a single check; where
contention is intermittent, sample repeatedly or monitor graph events.
`references/runtime-provenance.md` ("Who actually publishes this topic?") covers
the audit when the count is wrong and the culprit is not obvious.

**2. The driver translates zero into the vendor's actual stop.** A zero Twist is
a *value*, and some vendor bridges do not treat it as a command at all — they
forward only non-zero motion and expect an explicit stop, idle, or damping call
for "hold still". A driver written against such an SDK silently drops the most
important message in the system. Read the driver's command path (or the vendor
API docs) and confirm which call a zero Twist produces; if the SDK has a
dedicated stop primitive, the driver must invoke it rather than sending zeros.

**3. The command was submitted, and — separately — accepted.** These are two
strengths of evidence and collapsing them is the most common overclaim here. A
successful SDK return usually means the command was enqueued locally or the
socket write succeeded; it says nothing about the device receiving or applying
it. Remote acceptance needs remote evidence: a protocol acknowledgement, a
device-side status transition, an echoed sequence number. Log a failure at ERROR
and surface it on `/diagnostics` — a stop that failed to transmit must never
look like a stop that worked.

**4. The hardware measurably responded.** Confirm from feedback the command path
does not produce: wheel/joint velocity from encoders, motor current, IMU, or
direct observation on a restrained platform. This is the only link that
distinguishes "we asked it to stop" from "it stopped".

#### Acceptance criteria

"Each link was checked" means different things to different engineers, and the
loosest reading passes a broken stop path — "the feedback value got smaller" is
not a stop. Write the criteria down with times and tolerances. Let `t0` be the
moment the arbiter issues stop:

```text
1. Command ownership
   - The observed active publisher set on the driver-facing command topic
     matches the declared ownership architecture; under the single-arbiter
     invariant, exactly one authorized publisher endpoint.

2. Driver translation
   - The driver invokes the documented stop/idle vendor operation, or sends
     the vendor command equivalent, within T_driver of t0.

3. Transport submission and device acceptance
   - The driver reports successful local submission within T_send.
     A successful enqueue/send return is submission evidence only.
   - Remote evidence — vendor acknowledgement, device-side status
     transition, echoed sequence number, or equivalent — appears within
     T_ack of t0.

4. Hardware response
   - Absolute wheel/joint velocity falls below epsilon_stop within T_stop.
   - It remains below epsilon_stop for T_hold.
   - No unexpected current/torque or physical movement is observed.
```

**When the protocol exposes no acknowledgement**, state that remote acceptance
was not directly verified. A subsequent device-side status change or hardware
response is *downstream* evidence that the command took effect by some path —
it must not be relabeled as a protocol acknowledgement. Knowing the robot
stopped is not knowing the stop command was received.

`epsilon_stop`, `T_driver`, `T_send`, `T_ack`, `T_stop`, and `T_hold` are
**measured per platform and recorded** — derived from the drivetrain's braking
behavior, the vendor's documented command latency, and the sensor noise floor (a
threshold below your encoder's resolution is not a criterion). Publish them with
the stop-path test results so a later run can be compared against the same bar.

| Link | Evidence | Level (`references/engineering-principles.md` Principle 13) |
|---|---|---|
| Command ownership | `ros2 topic info -v` publisher set vs declared architecture, SROS2 policy, enforcement test | L3 |
| Driver translation | driver source / vendor API path taken by a zero command | L0 + L4 |
| Transport submission | SDK return code within `T_send` — local acceptance only | L4 |
| Device acceptance | vendor ack / device status transition / echoed sequence within `T_ack`, or an explicit "not directly verified" | L4 |
| Hardware response | encoder/current/IMU feedback vs `epsilon_stop`/`T_stop`/`T_hold` | L5 |

Only the full chain is a verified stop. Reporting link 1 as if it were link 4 is
pitfall 15 in `references/engineering-principles.md`. Link 4 requires commanded
motion on a restrained platform with an operator present — the conditions in
Section 6 apply in full, and it is never an unattended CI step or an AI agent
action.

### Stopping through ros2_control

Zero velocity through the mux handles kinematic stops. For a stronger protective stop,
switch to a stop-capable controller or deactivate the active one — controller
switching and hardware-level safe-stop patterns (including `on_deactivate` zero-command
discipline) are in `references/hardware-interface.md`. The e-stop gate node calls
`/controller_manager/switch_controller` with the stop controller at `STRICT` switching.

## 4. SROS2 e-stop isolation

Without access control, *any* process on the DDS domain can publish
`/safety/motion_permit` (spoofing a fresh permit past a real stop) or flood
`/safety/estop_active` with `false` (masking the lock). SROS2 access control makes the
safety topics writable by exactly one identity.

Keystore creation, enclave generation, and signing are covered in
`references/security.md` §2; permissions XML structure in §5. What follows is the
safety-specific policy.

### Threat model for the stop path

| Attack | Effect without isolation | Countermeasure |
|---|---|---|
| Spoofed permit heartbeat | Robot keeps moving through a real e-stop | Only `safety_supervisor` enclave may publish `/safety/motion_permit` |
| Forged estop-clear | Latched stop released without operator action | Only `safety_supervisor` may publish `/safety/estop_state`; reset only via authenticated service (Section 5) |
| Command-topic bypass | Malicious node publishes `/cmd_vel` directly, skipping the mux | Only `twist_mux` enclave may publish `/cmd_vel` |
| Unauthorized node joins domain | Foothold for all of the above | `ROS_SECURITY_STRATEGY=Enforce` — unauthenticated participants cannot join |

### Least-privilege policy for the safety topics

```xml
<!-- policy/safety_policy.xml — sros2 policy format (converted to signed
     permissions with `ros2 security create_permission`, see security.md §2) -->
<policy version="0.2.0"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <enclaves>
    <!-- The ONLY identity allowed to write the safety topics -->
    <enclave path="/safety_supervisor">
      <profiles>
        <profile ns="/" node="safety_supervisor">
          <topics publish="ALLOW">
            <topic>safety/motion_permit</topic>
            <topic>safety/estop_state</topic>
            <topic>safety/estop_active</topic>
          </topics>
          <topics subscribe="ALLOW">
            <topic>diagnostics_agg</topic>
          </topics>
        </profile>
      </profiles>
    </enclave>

    <!-- The mux: sole writer of the real command topic -->
    <enclave path="/twist_mux">
      <profiles>
        <profile ns="/" node="twist_mux">
          <topics publish="ALLOW">
            <topic>cmd_vel</topic>
          </topics>
          <topics subscribe="ALLOW">
            <topic>cmd_vel_nav</topic>
            <topic>cmd_vel_teleop</topic>
            <topic>cmd_vel_watchdog</topic>
            <topic>safety/estop_active</topic>
          </topics>
        </profile>
      </profiles>
    </enclave>

    <!-- Consumers may READ safety topics but never write them -->
    <enclave path="/base_controller">
      <profiles>
        <profile ns="/" node="base_controller">
          <topics subscribe="ALLOW">
            <topic>cmd_vel</topic>
            <topic>safety/motion_permit</topic>
          </topics>
          <topics publish="ALLOW">
            <topic>odom</topic>
            <topic>joint_states</topic>
          </topics>
        </profile>
      </profiles>
    </enclave>
  </enclaves>
</policy>
```

Key points:

- **Default-deny governance.** Set `<default>DENY</default>` for publish/subscribe
  rules in the governance file (`security.md` §5) — the profiles above are then the
  complete write surface for safety topics. With default-allow, the policy is decoration.
- **No wildcard publish grants** in *any* enclave that isn't the supervisor. A profile
  with `<topic>*</topic>` publish access can spoof the permit; audit for wildcards:

```bash
# Audit every signed permissions file for wildcard publish grants
grep -rn '\*' keystore/enclaves/*/permissions.xml | grep -i publish
```

- Remember DDS topic mangling: in *hand-written DDS permissions* the ROS topic
  `/safety/motion_permit` appears as `rt/safety/motion_permit` (`security.md` §5
  "Topic name prefixes"). The sros2 policy format above handles the prefix for you.
- Run with `ROS_SECURITY_ENABLE=true` and `ROS_SECURITY_STRATEGY=Enforce` on every
  node of the robot — `Permissive` mode lets an unauthenticated participant publish
  the permit topic, which defeats the entire section.

### Verify the isolation

```bash
# From a shell with NO enclave (or a wrong one): both must fail under Enforce
ros2 topic pub --once /safety/motion_permit std_msgs/msg/Bool '{data: true}'
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}'
# Expected: participant fails authentication / permission denied in DDS logs,
# and `ros2 topic info -v` on the robot shows no new publisher appeared.
```

Automate this check only in simulation/HIL. On physical hardware, run it as an
operator-approved test on a restrained platform (same rules as the stop-path
checklist below); never as unattended CI or by an AI agent — if enforcement is
broken, the spoofed permit or `/cmd_vel` goes through. A bringup check that
*tries* to spoof the permit and fails provides direct runtime evidence that
the policy is enforced.

## 5. Recovery and reset semantics

### Latch the stop, require a deliberate reset

An e-stop that clears itself the moment the trigger condition disappears invites
oscillation (robot lurches every time a flaky heartbeat recovers) and violates the
principle that a human must confirm the hazard is gone. Latch the stop; clear it only
through an explicit reset action:

```python
# estop_gate node — latching state machine
from std_srvs.srv import Trigger

class EstopGate(Node):
    def __init__(self):
        super().__init__('estop_gate')
        self.latched = False
        # Reset is a SERVICE, not a topic: request/response confirms receipt,
        # and SROS2 can restrict callers (only the operator UI enclave).
        self.reset_srv = self.create_service(Trigger, '~/reset', self.on_reset)

    def engage(self, reason: str):
        if not self.latched:
            self.latched = True
            self.get_logger().error(f'E-STOP ENGAGED: {reason}')
            self.publish_estop_state(engaged=True, reason=reason)

    def on_reset(self, request, response):
        if self.trigger_condition_still_present():
            response.success = False
            response.message = 'Reset refused: stop condition still active'
            return response
        self.latched = False
        self.publish_estop_state(engaged=False, reason='operator reset')
        response.success = True
        return response
```

Reset rules that survive incident reviews:

- **Reset restores *permission*, not *motion*.** After reset, the robot stays
  stationary until a fresh command arrives from an active source. Never replay the
  pre-stop command.
- **Refuse reset while the condition persists** (button still pressed, heartbeat still
  absent, geofence still violated).
- **Log engage and reset with cause and identity** — feed `/safety/estop_state`
  into rosbag or fleet telemetry; it is the first artifact an incident review asks for.
- Hardware chains have their own reset (usually a physical twist-release + reset
  button). Software reset must not be able to clear a hardware stop: the supervisor's
  `checks_pass()` reads the hardware chain status, so the permit stays false until the
  physical chain is closed.

## 6. Testing the stop path

A stop path that has never been fault-injected does not work — it only compiles. Test
the *failure* behaviors, not the happy path. General launch_testing setup is in
`references/testing.md`; these are the safety-specific cases.

Physical stop-path and spoofing checks in this section are deliberately high-risk
fault-injection tests. User authorization is necessary but does not delegate execution
authority: on physical hardware this reference reserves their execution to the
operator. An agent may prepare the exact bounded procedure and evaluate the evidence,
but it must not execute these physical fault injections itself.

### Fault-injection integration test

```python
# test/test_estop_gate.py — launch_testing: kill the supervisor, assert the stop
import time
import unittest

import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool


@pytest.mark.launch_test
def generate_test_description():
    supervisor = launch_ros.actions.Node(
        package='my_robot_safety', executable='safety_supervisor',
        name='safety_supervisor')
    gate = launch_ros.actions.Node(
        package='my_robot_safety', executable='estop_gate', name='estop_gate')
    return launch.LaunchDescription([
        supervisor, gate, launch_testing.actions.ReadyToTest(),
    ]), {'supervisor': supervisor}


class TestEstopFailSafe(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node('estop_test_probe')

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def test_supervisor_death_engages_stop(self, launch_service, supervisor, proc_info):
        states = []
        sub = self.node.create_subscription(
            Bool, '/safety/estop_engaged', lambda m: states.append(m.data), 10)

        # Fault injection: kill the heartbeat source outright.
        supervisor_action = supervisor
        launch_service.emit_event(
            launch.events.process.SignalProcess(
                signal_number=9,
                process_matcher=launch.events.process.matches_action(supervisor_action)))

        # The DEADLINE (500 ms) must fire and latch the stop well within 2 s.
        end = time.time() + 2.0
        while time.time() < end and not any(states):
            rclpy.spin_once(self.node, timeout_sec=0.1)
        self.assertTrue(any(states),
                        'estop_gate never engaged after supervisor SIGKILL')
        self.node.destroy_subscription(sub)
```

### Stop-path checklist

Run these on the real robot (wheels off the ground / in a cage) before every release:

| # | Fault injected | Required behavior |
|---|---|---|
| 1 | `kill -9` the safety supervisor | Stop engaged within one deadline period; mux lock active |
| 2 | Pull the network cable / radio between operator and robot | Same as 1 — network partition is indistinguishable from a dead node |
| 3 | Publish `/cmd_vel` from a rogue shell while stopped | No motion; under Enforce the publisher never matches |
| 4 | Publish a forged permit from an enclave-less shell | Authentication/permission failure; robot stays stopped |
| 5 | Request reset while the e-stop button is still pressed | Reset refused with an explanatory message |
| 6 | Reset after a genuine clear | Robot stays stationary until a *fresh* command arrives |
| 7 | Press the hardware e-stop with the software stack frozen | Motors de-energize via STO — proves the layers are independent |

Items 1–6 inject real faults into a machine that moves if a layer is broken.
Run them only with operator approval on a physically restrained platform — a
dedicated HIL/test rig, or the robot on a stand or in a cage with speed and
torque limits and the physical e-stop in hand (same rules as the failsafe
kill test in `references/hardware-interface.md`). They can be scripted for
repeatability on that restrained rig (see `references/system-bringup.md` §5
for the oneshot check pattern), but must never run as an unattended CI step
or be executed by an AI agent on hardware. Item 7 is always a manual
commissioning test.

## 7. Common failures and fixes

| Symptom | Why it happens | Fix |
|---|---|---|
| Robot keeps moving after safety node crashes | Stop is a "send true to stop" message — fail-dangerous | Heartbeat permit + DEADLINE event; silence engages the stop (Section 2) |
| E-stop subscriber never receives the permit | DEADLINE RxO mismatch — publisher offers no (or a longer) deadline | Offer deadline ≤ requested on the publisher; check `ros2 topic info -v` |
| Planner "wins" against the e-stop's zero command | Both publish the same topic; the subscriber can process commands from both, with no defined priority, so periodic planner commands can supersede a one-shot zero depending on processing timing | Arbitrate through twist_mux; e-stop is a lock at priority 255 (Section 3) |
| Node publishes `/cmd_vel` directly, bypassing the mux | Producers not remapped; nothing enforces the mux as sole writer | Remap all producers to mux inputs; SROS2: only the mux enclave may publish `/cmd_vel` |
| Zero command visible on the topic, robot keeps moving | The driver discarded the zero as "no command", the vendor call failed, or a competing publisher overwrote it | Verify all four links — command ownership, driver translation, submission plus remote-acceptance evidence, measured hardware response (Section 3) |
| Stop clears itself when the flaky link recovers | Stop state derived directly from the live condition | Latch the stop; clear only via reset service that re-checks the condition (Section 5) |
| Any node can publish the permit topic | No access control, or `ROS_SECURITY_STRATEGY=Permissive` | Enforce mode + default-deny governance + supervisor-only publish grant (Section 4) |
| Spoof test "passes" (spoof succeeds) in the lab | Nodes launched without enclaves fall back to unsecured participants | Launch every node with its enclave (`security.md` §9); make the spoof-must-fail check part of bringup |
| Robot lurches on reset | Pre-stop command replayed or still latched in a queue | Reset restores permission only; producers must publish fresh commands (LIFESPAN on command topics helps) |
| Late-started dashboard shows "no e-stop" during an incident | State topic is VOLATILE — late joiner missed the latch | `TRANSIENT_LOCAL` durability on `/safety/estop_state` (Section 2) |
| Operators treat the ROS e-stop as THE e-stop | Software stop presented as safety-rated | Document the hardware chain as the safety function (ISO 13849/IEC 62061); ROS layer is a protective stop only (Section 1) |
