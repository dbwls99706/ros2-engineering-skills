# Safety and E-Stop Systems

> **Version scope:** the safety invariants are portable; QoS-event support, mux
> options and security configuration depend on the installed ROS, RMW and package
> versions. Verify those interfaces before applying an example.

This guide covers designing the stop path of a robot: hardware vs software e-stop,
heartbeat-based permits with watchdogs, command arbitration so a stop decision
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

An ordinary ROS 2 stop node does not establish a machine safety rating. Keep an
independent safety chain with suitable e-stop devices, relays or a safety PLC,
and the drive's required stopping functions. Select and validate that chain from
the machine risk assessment and applicable requirements. STO removes drive torque;
it does not by itself establish braking, standstill or load holding. A safety PLC
may use certified software; the requirement is independence from the ordinary
ROS compute and network path, not absence of all software.

The two layers have different jobs:

| Layer | Path | Responsibility |
|---|---|---|
| Independent safety chain | E-stop devices → safety relay/PLC → required drive stop and holding functions | Machine-validated stopping independent of the ROS compute and network path |
| Software stop | Supervisor permit → final command gate → driver stop, with downstream watchdogs | Inhibit motion for application faults such as a bad plan or geofence breach; report the observed outcome |

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
# QoS events assist detection; an application watchdog and downstream stop are still required.
```

## 2. E-stop topic design

### Heartbeat with QoS detection and application watchdogs

The RELIABLE, VOLATILE, KEEP_LAST/1 profile in
`references/engineering-principles.md` Principle 6 is an illustrative starting
point. Derive heartbeat period, deadline, lifespan and accepted sample age from
the verified end-to-end stop budget. The example's 500 ms deadline and 1 s lifespan
are not validated machine limits. DEADLINE reports a missed update; LIFESPAN limits
DDS sample retention. Neither guarantees that a callback runs on time or that a
delivered permit is fresh enough for the application.

**A revocation must persist at its source.** RELIABLE with KEEP_LAST(1) does not
preserve every intermediate value: `false` followed by `true` can leave only `true`
when the gate next takes a sample. Latch revocation in the supervisor and continue
publishing revoked state until the gate acknowledges the corresponding stop
generation and the supervisor accepts explicit rearm authorization. This precedes
the separate gate reset in Section 5; it does not wait for that later reset.
Serialize supervisor rearm and
decision updates so a new fault wins. A brief healthy reading must not
restore positive permits. A gate cannot latch a revocation it never receives.

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
       .lifespan(1s);            // DDS retention only; application age checks still apply
    permit_pub_ = create_publisher<std_msgs::msg::Bool>("/safety/motion_permit", qos);

    timer_ = create_wall_timer(200ms, [this] {   // 2.5x margin under the deadline
      std_msgs::msg::Bool permit;
      if (!checks_pass()) { revoked_ = true; }
      permit.data = !revoked_;       // healthy readings cannot clear a revocation
      permit_pub_->publish(permit);  // data==false OR silence both mean STOP
    });
  }

private:
  bool checks_pass();
  bool revoked_{true};  // Startup is stopped; only the reset protocol below may clear this.
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
    else { last_permit_ = std::chrono::steady_clock::now(); }
  },
  options);
```

These Bool excerpts illustrate heartbeat callbacks, not a complete reset or
freshness protocol. A deployed permit needs a supervisor session, monotonically
identified decision/stop generation, and evidence of generation age. Match stop
acknowledgements and resets to that generation; reject stale sessions and replayed
reset requests. A local receipt timestamp alone cannot distinguish a newly made
decision from delayed data. Account for timestamp clock error when comparing age.
Start the gate stopped, use a steady-clock watchdog even before the first sample,
and keep a downstream controller watchdog for an executor or process that hangs.

History semantics: [DDS reliability and history](https://fast-dds.docs.eprosima.com/en/v2.14.5/fastdds/dds_layer/core/policy/standardQosPolicies.html#historyqospolicy).

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
`/safety/estop_state` (retained status, informs humans and UIs). Retained status is
not a live permission. A late joiner starts stopped and requires a fresh valid
permit plus the reset protocol; neither cached status nor a returning heartbeat
may clear its latch.

## 3. Command arbitration

An e-stop that publishes zero velocity *once* loses the race against a planner
publishing at 20 Hz. Arbitrate all command sources through a priority multiplexer so
the sources have a defined order, then apply the stop decision at a final gate.

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
  -r cmd_vel_out:=/cmd_vel_selected  # internal selection; the final gate owns /cmd_vel
```

The lock suppresses lower-priority inputs; it does not itself publish a stop.
A priority-255 lock would also mask a priority-200 zero-command source. The
`estop_gate` therefore subscribes to `/cmd_vel_selected` and owns the only
driver-facing `/cmd_vel` publisher. It forwards selected motion only while the
permit, latch, source generation and command-age checks pass. On stop it fences
forwarding and emits the driver's documented stop command directly, independently
of mux input selection. Serialize that check with every output write.

Keep downstream command timeouts for a gate or mux crash. The example timeouts are
starting points, not measured stopping limits. Check the installed mux's message
type and output topic; `Twist` and `TwistStamped` options vary by package version.
See [twist_mux masking and callbacks](https://github.com/ros-teleop/twist_mux/blob/rolling/include/twist_mux/topic_handle.hpp).

### Close the bypass hole

The final gate must be the *sole* publisher on the driver-facing command topic.
Remap producers to mux inputs and the mux output to the gate's input:

```bash
# Audit: exactly one publisher (the final gate) may appear here
ros2 topic info /cmd_vel -v
```

On a secured system, enforce this ownership: only the gate's
enclave gets publish permission on `/cmd_vel` (Section 4).

### Zero on a topic is not a stopped robot

`ros2 topic echo /cmd_vel` showing zeros proves one thing: a message was
published. It is not evidence that the robot stopped. Verify command ownership,
driver translation, transport submission, device acceptance and measured response
separately.

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
ros2 topic info /cmd_vel -v     # single-arbiter design: exactly 1 (the final gate)
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

Only the full chain supports a verified-stop claim. Topic observations do not
establish measured hardware response (Principle 13 in
`references/engineering-principles.md`). Measuring stopping from commanded motion
requires a restrained platform and an operator under the conditions in Section 6;
it is never an unattended CI step or an agent-executed physical fault injection.

### Stopping through ros2_control

A project may implement the driver stop through a stop-capable ros2_control
controller or a verified deactivation path. Assign the authorized switching caller
and its service permissions, check the installed interface, and handle switching
failure within the stop budget. A successful switch or deactivation is not proof
of physical stopping; confirm what the hardware writes and observe the response.
See `references/hardware-interface.md` for driver cleanup, and the
[controller-manager interface](https://control.ros.org/jazzy/doc/ros2_control/controller_manager/doc/userdoc.html)
for switching and fallback limitations.

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
| Forged estop-clear or ACK | Latched stop released without operator action | Gate owns state and stop ACK; only the supervisor may request its versioned reset (Section 5) |
| Command-topic bypass | Malicious node publishes `/cmd_vel` directly, skipping the gate | Only `estop_gate` enclave may publish `/cmd_vel` |
| Unauthorized node joins domain | Foothold for all of the above | Governance rejects unauthenticated participants and enables join access control; each process uses `Enforce` |

### Least-privilege policy for the safety topics

```xml
<!-- policy/safety_policy.xml — sros2 policy format (converted to signed
     permissions with `ros2 security create_permission`, see security.md §2) -->
<policy version="0.2.0"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <enclaves>
    <!-- Supervisor owns permits and requests gate reset. -->
    <enclave path="/safety_supervisor">
      <profiles>
        <profile ns="/" node="safety_supervisor">
          <topics publish="ALLOW">
            <topic>safety/motion_permit</topic>
          </topics>
          <topics subscribe="ALLOW">
            <topic>diagnostics_agg</topic>
            <topic>safety/stop_ack</topic>
            <topic>safety/estop_state</topic>
          </topics>
          <services request="ALLOW">
            <service>estop_gate/reset</service>
          </services>
        </profile>
      </profiles>
    </enclave>

    <!-- Gate owns effective stop state, ACK and driver-facing commands. -->
    <enclave path="/estop_gate">
      <profiles>
        <profile ns="/" node="estop_gate">
          <topics publish="ALLOW">
            <topic>cmd_vel</topic>
            <topic>safety/stop_ack</topic>
            <topic>safety/estop_state</topic>
            <topic>safety/estop_active</topic>
          </topics>
          <topics subscribe="ALLOW">
            <topic>safety/motion_permit</topic>
            <topic>cmd_vel_selected</topic>
          </topics>
          <services reply="ALLOW">
            <service>estop_gate/reset</service>
          </services>
        </profile>
      </profiles>
    </enclave>

    <!-- The mux selects sources but cannot bypass the final gate. -->
    <enclave path="/twist_mux">
      <profiles>
        <profile ns="/" node="twist_mux">
          <topics publish="ALLOW">
            <topic>cmd_vel_selected</topic>
          </topics>
          <topics subscribe="ALLOW">
            <topic>cmd_vel_nav</topic>
            <topic>cmd_vel_teleop</topic>
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

This safety-specific fragment gives the named ACK and reset routes in both
directions. Add only the application's actual hardware feedback, infrastructure
and authorized start/source-generation interfaces; then generate and verify the
effective DDS grants. It is not a complete policy for unspecified nodes.
Service policy syntax: [SROS2 client/server example](https://github.com/ros2/sros2/blob/jazzy/sros2/test/policies/add_two_ints.policy.xml).

Key points:

- **Default-deny permissions.** `<default>DENY</default>` belongs in each DDS
  permissions grant, not the governance document. Governance must also reject
  unauthenticated participants and enable join, read and write access control for
  the protected topics (`security.md` §5).
- **Audit effective grants in every enclave, including the supervisor.** Broad or
  wildcard grants must not accidentally authorize a protected publication. Inspect
  the XML structure and overlapping rules; filtering individual lines containing
  `publish` cannot establish the effective permissions of a multiline grant.

- Remember DDS topic mangling: in *hand-written DDS permissions* the ROS topic
  `/safety/motion_permit` appears as `rt/safety/motion_permit` (`security.md` §5
  "Topic name prefixes"). The sros2 policy format above handles the prefix for you.
- Run with `ROS_SECURITY_ENABLE=true` and `ROS_SECURITY_STRATEGY=Enforce` on every
  participating process so missing or invalid security artifacts cannot silently
  fall back to unsecured operation. Peer admission and topic access still depend
  on governance and permissions; `Enforce` alone does not define those rules.

### Verify the isolation

```bash
# From a shell with NO enclave (or a wrong one): both must fail under Enforce
ros2 topic pub --once /safety/motion_permit std_msgs/msg/Bool '{data: true}'
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}'
# Expected: participant fails authentication / permission denied in DDS logs,
# and `ros2 topic info -v` on the robot shows no new publisher appeared.
```

Automate this check only with actuation disconnected, such as an isolated simulation.
Treat HIL as physical whenever it can actuate hardware. On physical hardware, run it as an
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

Both supervisor and gate start stopped. Their protocol must preserve revocation
across delayed callbacks, reconnects and restarts:

1. The supervisor latches a new revocation generation and keeps issuing revoked
   state. The gate fences command output, invalidates the command generation,
   latches its incident, and acknowledges that specific supervisor generation.
   An acknowledgement means the software gate latched; physical standstill needs
   separate feedback.
2. Following deliberate operator **rearm authorization**, the supervisor may offer a new
   permit only after the current stop acknowledgement, cleared cause and hardware
   prerequisites have been verified. A missing or old acknowledgement keeps it
   revoked. Recovering health alone cannot rearm it.
3. The gate remains stopped until the later **gate reset** request, authorized for
   the current incident, validates the
   current supervisor session/generation, its own current incident, fresh permit
   and measured stationary state. Reject a reset from a prior incident. A plain
   `std_srvs/Trigger` carries no incident identifier; use a versioned reset request
   or an equally explicit stale-request exclusion protocol. Serialize reset and
   new stop events so a concurrent fault wins.
4. Reset reaches **armed but stationary**. It does not start navigation or accept
   an old producer's continuing command stream. Require deliberate start/resume
   for a new command generation, and neutral/reasserted deadman for teleoperation.
   A bare Twist has no intent-generation field; use a typed command envelope or
   an authenticated source-enable protocol that excludes old goals and queued data.

An interrupted handshake leaves the gate stopped. Do not require a positive
motion permit before acknowledging the revoked state; that creates a reset
deadlock. Protect acknowledgement, permit and reset identities with the actual
security policy, not caller-supplied names alone.

Reset rules that survive incident reviews:

- **Reset restores *permission*, not *motion*.** A recently republished command
  from an old goal is still an old intent. Require the new authorized command
  generation before forwarding it; never replay the pre-stop command.
- **Refuse reset while the condition persists** (button still pressed, heartbeat still
  absent, geofence still violated).
- **Log engage and reset with cause and identity** — feed `/safety/estop_state`
  into rosbag or fleet telemetry; it is the first artifact an incident review asks for.
- Hardware chains have their own reset (usually a physical twist-release + reset
  button). Software reset must not be able to clear a hardware stop: the supervisor's
  `checks_pass()` reads the hardware chain status, so the permit stays false until the
  physical chain is closed.

## 6. Testing the stop path

Without relevant fault tests, the stop path remains unverified; compilation does
not establish its behavior. Test the *failure* behaviors as well as the happy path.
General launch_testing setup is in
`references/testing.md`; these are the safety-specific cases.

Physical stop-path and spoofing checks in this section are deliberately high-risk
fault-injection tests. User authorization is necessary but does not delegate execution
authority: on physical hardware this reference reserves their execution to the
operator. An agent may prepare the exact bounded procedure and evaluate the evidence,
but it must not execute these physical fault injections itself.

### Fault-injection integration test

Use `launch_testing` with the application's actual versioned permit, ACK, reset
and state types on an isolated graph with actuation disconnected. A generic test
cannot invent these interfaces or their arming fixture. Implement this sequence:

1. Wait for the expected supervisor, gate and mock driver to start and discover
   the required services. Complete supervisor rearm and gate reset for the current
   generations. Assert **armed but stationary**, with stopped output. Failure to
   reach this precondition is a setup failure, not a successful stop test.
2. Clear previous observations, record a steady-clock fault time, and terminate
   the identified supervisor once. Retain evidence of which process was signalled.
3. Require a new latched gate incident within the configured detection/executor
   budget, and verify the final command and mock driver's timeout/stop behavior.
   A retained startup-stopped sample or an old incident cannot satisfy this check.
4. In a separate disconnected-mock trial, deliberately enable a new command
   generation and observe a nonzero command at the mock driver before injecting
   the fault. Keep the producer publishing; require the gate to replace or inhibit
   that command and the mock to enter its defined stopped state within budget.
   This checks interruption of active commands, not physical braking.
5. Separately exercise a brief revoked decision followed by healthy decisions
   while the consumer is delayed; the supervisor must retain revocation. Cover
   missing/old ACKs, stale resets, reconnect sessions, and a new stop racing reset.
   None may rearm the gate or replay an old goal.
6. Require every started process to exit during bounded teardown and retain state
   generations, signal order, logs, versions and failed deadlines. Do not lengthen
   a bound or reuse a previous trial's evidence to make a failed attempt pass.

These checks establish software protocol behavior only. Hardware response still
requires the controlled commissioning tests below.

### Stop-path checklist

Select the applicable cases for commissioning and changes that affect the stop
path or its dependencies. Record the tested configuration and retain the required
release gates; an unrelated prose edit does not itself require new physical fault
injection. Physical runs use the restrained conditions below.

| # | Fault injected | Required behavior |
|---|---|---|
| 1 | `kill -9` the safety supervisor | Gate latches and stopping response meets the approved end-to-end budget; middleware notification alone is insufficient |
| 2 | Pull the network cable / radio between operator and robot | Same as 1 — network partition is indistinguishable from a dead node |
| 3 | Publish `/cmd_vel` from a rogue shell while stopped | No motion; under Enforce the publisher never matches |
| 4 | Publish a forged permit from an enclave-less shell | Authentication/permission failure; robot stays stopped |
| 5 | Request reset while the e-stop button is still pressed | Reset refused with an explanatory message |
| 6 | Reset after a genuine clear | Robot stays stationary until deliberate start/resume establishes a new command generation |
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
| Planner "wins" against the e-stop's zero command | Competing writers bypass the stop decision | Mux selects inputs; the final gate fences motion and directly emits the stop (Section 3) |
| Node publishes `/cmd_vel` directly, bypassing the gate | Producers not remapped; command ownership is unenforced | Remap through mux selection and the final gate; only the gate enclave may publish `/cmd_vel` |
| Zero command visible on the topic, robot keeps moving | The driver discarded the zero as "no command", the vendor call failed, or a competing publisher overwrote it | Verify all four links — command ownership, driver translation, submission plus remote-acceptance evidence, measured hardware response (Section 3) |
| Stop clears itself when the flaky link recovers | Stop state derived directly from the live condition | Latch the stop; clear only via reset service that re-checks the condition (Section 5) |
| Any node can publish the permit topic | Authentication/access control disabled, unsecured fallback, or overbroad grants | Enforce startup, access-control governance and default-deny permissions with supervisor-only permit publication (Section 4) |
| Spoof test "passes" (spoof succeeds) in the lab | Nodes launched without enclaves fall back to unsecured participants | Launch every node with its enclave (`security.md` §9); make the spoof-must-fail check part of bringup |
| Robot lurches on reset | An old goal republishes commands or queued data survives reset | Reset remains stationary; require deliberate start/resume and a new authorized command generation (Section 5) |
| Late-started dashboard shows "no e-stop" during an incident | State topic is VOLATILE — late joiner missed the latch | `TRANSIENT_LOCAL` durability on `/safety/estop_state` (Section 2) |
| Operators treat the ROS e-stop as THE e-stop | Software stop presented as safety-rated | Document the hardware chain as the safety function (ISO 13849/IEC 62061); ROS layer is a protective stop only (Section 1) |
