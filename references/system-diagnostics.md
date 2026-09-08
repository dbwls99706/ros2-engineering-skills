# Cross-Layer System Diagnostics

Most ROS 2 failures that survive a code review are not ROS bugs. They start
outside the graph — a link drops, a bridge process blocks, a USB device
renumbers, a clock steps — and arrive as a ROS symptom several layers away from
the cause. The node that logs the loudest error is usually the last link in the
chain, not the broken one.

This file is a **diagnostic pointer**: how to trace a symptom back across layer
boundaries, and which boundary to instrument. It does not document non-ROS
stacks (WebRTC, Wi-Fi, CAN, vendor SDKs) — consult their own tooling once this
process tells you which one to open.

## Table of contents

1. [The shape of a cross-layer failure](#1-the-shape-of-a-cross-layer-failure)
2. [Bisect by first stop, not by loudest error](#2-bisect-by-first-stop-not-by-loudest-error)
3. [Instrument the boundaries](#3-instrument-the-boundaries)
4. [Bridge nodes: executors meeting other event loops](#4-bridge-nodes-executors-meeting-other-event-loops)
5. [Symptom to layer table](#5-symptom-to-layer-table)
6. [Common failures and fixes](#6-common-failures-and-fixes)

---

## 1. The shape of a cross-layer failure

A worked example, from a quadruped whose autonomy stack talks to the robot over
a vendor WebRTC/Wi-Fi link:

```text
Wi-Fi degradation
  → WebRTC session drops
    → vendor bridge stops publishing robot state
      → the node deriving odom→base_link from that state stops broadcasting TF
        → costmap updates fail: "sensor origin out of map bounds" / TF extrapolation
          → Nav2 controller aborts, robot stops mid-mission
```

Every arrow crosses a layer, and every layer reports the failure in its own
vocabulary. Nav2's error message is truthful and useless: the costmap really
did fail, for a reason four layers upstream. Three properties make this class
of failure hard, and all three are worth recognizing on sight:

- **The error surfaces where the data is consumed, not where it was lost.**
  Consumers are the components with assertions; producers just go quiet.
- **Silence propagates faster than errors.** A stopped publisher is not an
  exception anywhere — downstream nodes simply time out, one after another,
  producing a cascade of unrelated-looking messages.
- **Recovery is not symmetric.** The link returns, the bridge reconnects, but
  a lifecycle node that transitioned to error, a latched TF gap, or a costmap
  that cleared itself may not come back without intervention.

## 2. Bisect by first stop, not by loudest error

The diagnostic question is always **which producer went quiet first**, in wall
time. Answer it from recorded data, not from live poking — the failure is
usually intermittent.

Record the chain's boundaries continuously, so any incident is already
captured:

```bash
# Every producer along the suspected chain plus the clock, small and cheap
ros2 bag record -o incident \
  /tf /tf_static /diagnostics /rosout \
  /robot_state /odom /scan \
  --max-bag-duration 300
```

**`--max-bag-duration` splits the recording; it does not bound total disk
use.** It starts a new file every N seconds and keeps every previous one, so
left running it fills the disk — which on a robot takes down more than the
recording. Circular retention (deleting the oldest splits) is a separate
capability that rosbag2 gained later, spelled `--max-bag-files` and
companions. Check what your installed version actually offers before relying
on it:

```bash
ros2 bag record --help | grep -i 'max-'
```

If the installed rosbag2 has no retention option, bound the disk outside
rosbag2 — rotate or stop the recorder from a supervisor, cap the partition, or
record to a fixed-size volume. Do not assume splitting is retention.

Then order the last message on each topic:

```bash
ros2 bag info incident        # per-topic message counts and duration
```

For an exact ordering, read the bag programmatically and print the final
timestamp per topic (`references/debugging.md` §5 has the `SequentialReader`
pattern). The topic whose last message is *earliest* is the closest to the
cause; everything that stopped after it is a consequence.

`/rosout` in the same bag gives the log lines interleaved with the data, so the
reconnection attempts of a bridge and the first TF failure land on one timeline.

Two rules that keep this honest:

- **Compare message timestamps, not receive order**, and confirm both endpoints
  agree on the clock (`use_sim_time`, NTP/PTP — `references/sensor-integration.md`).
  A clock step looks exactly like a stalled producer.
- **A rate drop is a stop.** Nav2, costmaps, and controllers fail at *degraded*
  rates well before zero. Compare against each producer's nominal rate rather
  than checking for the absence of messages.

## 3. Instrument the boundaries

Every place data enters or leaves the ROS graph deserves a health signal that
is published *by the component that owns the boundary*, using
`diagnostic_updater` (`references/debugging.md` §1):

| Boundary | Publish | Fails as |
|---|---|---|
| Network link to robot / operator | RSSI, packet loss, RTT, session state | topics go quiet, no error |
| Vendor SDK / WebRTC session | connected, last successful call, reconnect count | stale data at the old rate, or silence |
| Serial / CAN / USB device | bytes/s, framing and CRC errors, reopen count | partial messages, plausible but wrong values |
| Time sync | offset to reference, sync state | TF extrapolation errors, bag replay skew |
| Compute host | CPU load, thermal throttle, free memory | rate drops that look like network loss |

The point is not dashboards. It is that when a chain breaks, the answer to
"which layer" should already be in `/diagnostics` and in the bag, instead of
being reconstructed from a robot that is now standing still.

Boundary components should also **report degraded, not just failed**: a bridge
that is reconnecting is materially different from one that is down, and
consumers can react (stop accepting goals) before the data disappears.

## 4. Bridge nodes: executors meeting other event loops

The node that owns a boundary usually runs two schedulers at once — a ROS 2
executor and something else (asyncio, a vendor SDK's callback thread, a
WebSocket loop). This is where the same failure keeps being reintroduced:

- **Blocking the executor.** A synchronous vendor call, a reconnect with a
  built-in retry sleep, or `await`-less blocking I/O inside a subscription or
  timer callback stops *every* callback in that executor, including the
  heartbeat the safety layer depends on. Offload to a dedicated thread or a
  reentrant callback group with a `MultiThreadedExecutor`
  (`references/nodes-executors.md`).
- **Two loops, one thread.** Driving `rclpy` from inside an asyncio loop (or
  the reverse) without a deliberate integration means one of them starves
  whenever the other is busy. Run the executor in its own thread and hand data
  across with a queue, or use the client library's supported async model
  (`references/engineering-principles.md` Principle 8).
- **Reconnect storms.** Retry with bounded exponential backoff. An unbounded
  retry loop competes for the same degraded link and turns a recoverable
  dropout into a permanent one.
- **State on reconnect.** After the link returns, decide explicitly what is
  stale: latched topics need republishing, lifecycle nodes may need
  reactivation, and any command received before the drop must be discarded
  rather than replayed (`references/safety-estop.md` §5).

A bridge node is a resource-owning node in the sense of
`references/engineering-principles.md` Principle 9 — lifecycle transitions give
the system a defined way to take it out of service when the far side is gone,
instead of leaving a half-connected node publishing stale data.

## 5. Symptom to layer table

The ROS-visible symptom, the layer that usually caused it, and the command that
confirms it before you start changing configuration.

| ROS symptom | Suspect first | Confirm with |
|---|---|---|
| TF extrapolation errors, costmap "out of bounds" | the producer feeding that TF edge stopped | last-message ordering in the bag; `ros2 run tf2_ros tf2_monitor` |
| Topic rate drops but never reaches zero | link congestion, CPU throttle, or RELIABLE retransmits | `ros2 topic hz`, `/diagnostics` link stats, `uptime`/thermal counters |
| All remote topics vanish at once | transport/session down, or discovery lost | link state, `ros2 topic info -v` on the robot itself |
| Sensor values plausible but wrong | device framing/CRC errors, or a renumbered device | driver error counters; `ls -l /dev/serial/by-id/` (`references/system-bringup.md`) |
| Timestamps jump or go backwards | clock step, or mixed `use_sim_time` | `chronyc tracking` / PTP state; compare `/clock` consumers |
| Node alive, callbacks stopped | executor blocked by a bridge or vendor call | `py-spy dump` / `gdb` thread backtrace; check callback groups |
| Everything recovers except one node | error-state lifecycle node or stale latched data | `ros2 lifecycle list`; re-check latched topics after reconnect |
| Behavior differs after a reconnect | stale config or a duplicate process from the restart | `references/runtime-provenance.md`, "Stale daemon vs real processes" |

## 6. Common failures and fixes

| Symptom | Why it happens | Fix |
|---|---|---|
| Diagnosis chases the node that logged the error | The consumer asserts; the producer just goes silent | Order producers by last message time; fix the earliest stop |
| "It only happens on the robot" | The failing layer (radio, SDK, thermal) does not exist on the desk | Record boundary diagnostics continuously so incidents are captured, not reproduced |
| Post-incident analysis has no data | Nothing was recording when it happened | Record the chain's boundaries continuously, with retention bounded by the installed rosbag2's option or an external rotation (section 2) |
| Continuous recording fills the disk | `--max-bag-duration` splits files but keeps every split | Use the installed version's retention option if it has one, otherwise rotate or cap storage outside rosbag2 |
| Heartbeat stops whenever the link degrades | Bridge blocks the executor during reconnect | Dedicated thread or reentrant group; bounded backoff; never sleep in a callback |
| Link returns but autonomy stays broken | Lifecycle in error, latched data stale, no republish on reconnect | Define reconnect semantics explicitly; re-activate and republish latched state |
| Robot resumes a pre-dropout command | Queued command delivered after reconnect | LIFESPAN on command topics; require a fresh command after any gap (`references/safety-estop.md` §5) |

---

**See also:** `references/runtime-provenance.md` for "what is actually running",
`references/evidence-progression.md` for latched-state and recovery evidence,
`references/debugging.md` for tracing, rosbag2, and DDS-level tools,
`references/system-bringup.md` for udev, boot ordering, and watchdogs,
`references/nodes-executors.md` for executor and callback-group behavior.
