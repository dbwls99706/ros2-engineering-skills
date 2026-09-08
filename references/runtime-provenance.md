# Runtime Provenance and Overlay Auditing

A build that succeeds, a launch file that parses, and a test suite that passes
all describe **source code**. This file is about the other question: *what is
actually running right now, and where did it come from?* Those two answers
diverge constantly — an old `install/` prefix, a second overlay on the
`AMENT_PREFIX_PATH`, a launch-time parameter override, a second copy of a node
nobody remembers starting.

Every check here is read-only and runs at verification level **L3–L4**
(`references/engineering-principles.md` Principle 13): it observes a live system,
it does not move a robot.

## Table of contents

1. [What the current shell resolves](#1-what-the-current-shell-resolves)
2. [What the running process actually inherited](#2-what-the-running-process-actually-inherited)
3. [Comparing the two](#3-comparing-the-two)
4. [Source tree vs installed copy](#4-source-tree-vs-installed-copy)
5. [Which parameters are actually in effect?](#5-which-parameters-are-actually-in-effect)
6. [Who actually publishes this topic?](#6-who-actually-publishes-this-topic)
7. [TF provenance: authority, duplicates, freshness](#7-tf-provenance-authority-duplicates-freshness)
8. [Stale daemon vs real processes](#8-stale-daemon-vs-real-processes)
9. [Provenance checklist](#9-provenance-checklist)
10. [Common failures and fixes](#10-common-failures-and-fixes)

---

## 1. What the current shell resolves

These commands answer how **the environment you are typing in** resolves a
package. That is a real and useful answer — it is just not, on its own, an
answer about any running node.

```bash
ros2 pkg prefix my_robot_bringup           # install prefix THIS shell resolves
ros2 pkg prefix --share my_robot_bringup   # its share/ directory (config, launch, urdf)
python3 -c "import my_robot_monitor as m; print(m.__file__)"
python3 -c "from ament_index_python.packages import get_package_share_directory as g; print(g('my_robot_bringup'))"
```

**Proves:** how this shell's `AMENT_PREFIX_PATH` / `PYTHONPATH` resolve the
package right now — which is what a command you launch *from here* will get.

**Does not prove:**

- that an already-running process was started from this environment;
- anything about a process launched from another terminal, a systemd unit, a
  different container, or a launch file that modified the environment;
- which module a composable node loaded inside a component container — the
  `python3 -c` above starts a *new* interpreter and repeats your shell's import
  resolution, not the running node's.

## 2. What the running process actually inherited

For a claim about a running node, read that process:

```bash
pid=$(pgrep -f controller_server | head -1)

tr '\0' ' ' < /proc/$pid/cmdline     # how it was actually invoked (args, remaps, params-file)
readlink /proc/$pid/exe              # the executing binary
readlink /proc/$pid/cwd              # working directory — relative paths resolve here
tr '\0' '\n' < /proc/$pid/environ |
  grep -E 'AMENT_PREFIX_PATH|COLCON_PREFIX_PATH|PYTHONPATH|LD_LIBRARY_PATH|ROS_DISTRO|ROS_DOMAIN_ID|RMW_IMPLEMENTATION'

ls -l /proc/$pid/fd | grep -v socket  # config/log/device files it holds open
```

`AMENT_PREFIX_PATH` is the resolution order: the **first** prefix containing a
package wins, so a forgotten workspace early in that list shadows the one you
are editing.

Limits to state alongside any finding from this section:

- **`exe` is the interpreter for Python nodes.** It reads
  `/usr/bin/python3`, which says nothing about which script or module ran.
  Script identity comes from `cmdline`, the process environment, the installed
  console-script entry point, and — if it matters — a process-specific Python
  introspection tool.
- **Open files are supporting evidence, not an inventory.** `ls -l
  /proc/$pid/fd` (or `lsof -p $pid` where available, both Linux) shows what the
  process currently holds open. Imported Python modules are not guaranteed to
  stay open, so the *absence* of a file descriptor is not proof that a module
  was never loaded.
- **`/proc` inspection requires sufficient permission** — normally the same
  user, or elevated diagnostic access — and may be restricted by container
  isolation or host security policy.
- **Node names and PIDs are not 1:1.** One component container process hosts
  many nodes; a node name may also be duplicated across processes (§8).
- **`/proc` is Linux-only, and namespace-scoped.** PIDs are valid only inside
  the same PID namespace: inspecting a containerized node from the host (or the
  reverse) sees a different numbering, and possibly no `/proc` entry at all.
  Enter the container's namespace, or run the check inside it.

## 3. Comparing the two

The finding is the **difference** between §1 and §2. Resolve the same package in
both, and treat any divergence as the explanation for "my change had no effect":

```bash
# What this shell would use
ros2 pkg prefix --share my_robot_bringup

# What the running process can see, in its own order
tr '\0' '\n' < /proc/$pid/environ | grep '^AMENT_PREFIX_PATH='
```

If the prefix you inspected is not the first entry in the *process's* path that
contains the package, you have been reading a different copy of every launch
file, YAML, and plugin XML than the one that ran.

## 4. Source tree vs installed copy

Nodes read from the **install space**, not from `src/`. Diff them explicitly
rather than assuming a rebuild happened:

```bash
share=$(ros2 pkg prefix --share my_robot_bringup)
diff -u src/my_robot_bringup/config/nav2_params.yaml "$share/config/nav2_params.yaml"
diff -ru src/my_robot_bringup/launch "$share/launch"
```

`colcon build --symlink-install` reduces but does not remove this gap:

- Python modules, launch files, and data files installed through the symlink
  path point back at the source — editing the source changes what runs.
- C++ artifacts always need a rebuild.
- Files added *after* the last build are missing from the install space
  entirely, symlinks or not.
- A package built once **without** `--symlink-install` leaves real copies
  behind; a later symlink build does not necessarily clean them up. When in
  doubt, `ls -l "$share/config/"` and look for symlink arrows.

**Stale-overlay smell test.** If a file exists in the install space but no
longer exists in the source tree, the install space is stale — colcon does not
remove artifacts of deleted sources:

```bash
# Any installed launch file whose source counterpart is gone
for f in "$share"/launch/*; do
  [ -e "src/my_robot_bringup/launch/$(basename "$f")" ] || echo "orphaned: $f"
done
```

The fix is to delete `build/`, `install/`, and `log/` for that package and
rebuild — and the same reasoning is why
`references/engineering-principles.md` Principle 10 restricts caching those
directories in CI.

The same diff applies to Python modules, with the §1 caveat attached:
`get_package_share_directory()` and a module's `__file__` resolve through the
ament index and `PYTHONPATH` **of whichever interpreter runs them**. Run them
inside the node's environment — otherwise you are comparing your shell's
resolution against the source tree and learning nothing about the node.

## 5. Which parameters are actually in effect?

A YAML file on disk is a proposal. The parameter server holds the outcome,
after launch-time overrides, remappings, node-name mismatches (a YAML block
keyed to a node name that does not match the running node is silently ignored),
and `set_parameter` calls at runtime.

```bash
ros2 param dump /controller_server            # everything the node currently holds
ros2 param get /controller_server FollowPath.max_vel_x
ros2 param describe /controller_server FollowPath.max_vel_x   # type, range, description
```

Dump the live values into a file and diff them against the YAML you believe was
loaded:

```bash
ros2 param dump /controller_server > /tmp/live_params.yaml
diff -u "$share/config/nav2_params.yaml" /tmp/live_params.yaml   # structure differs; compare values
```

Parameters that appear with default values when your YAML sets something else
mean the file did not reach the node — wrong node name key, wrong namespace,
`--params-file` not passed, or a later `--ros-args -p` override.

## 6. Who actually publishes this topic?

`ros2 topic echo` shows *a* message. It never shows who sent it, and it cannot
show that two nodes are alternating writes on the same topic — which is exactly
the failure mode behind "the stop command worked, then the robot kept moving."

```bash
ros2 topic info /cmd_vel -v      # per-endpoint node name, namespace, GID, and full QoS
```

Read the **Publisher count** first. On any command topic the answer must match
the number your architecture allows — normally one arbiter
(`references/safety-estop.md` §3). With two compatible publishers, the
subscriber can receive commands from both. There is no defined command priority
or arbitration at the DDS level; which command affects the actuator depends on
delivery and callback-processing timing.

```bash
ros2 node info /twist_mux        # the other direction: what this node pubs/subs
```

Caveats worth knowing before you trust the output:

- Endpoint node names come from the discovery data. A node that has been killed
  can linger briefly; a node that never spun may not appear at all.
- Nodes in different namespaces publishing "the same" relative topic resolve to
  different absolute topics — check the namespace column, not just the name.
- QoS is reported per endpoint, so this same command answers "why does nobody
  receive it" (`references/communication.md`).

## 7. TF provenance: authority, duplicates, freshness

A `lookupTransform` that succeeds proves the chain **connects**. It does not
prove the transform is fresh, nor that one node owns that edge.

**Authority cannot be recovered from the TF buffer in ROS 2.** ROS 1 attributed
each transform to its publisher via the connection header; ROS 2 has no such
header, so `tf2_ros::TransformListener` stores the literal string
`"Authority undetectable"` for every transform it receives. Anything built on
the buffer's authority field — `tf2_monitor`'s broadcaster column, the
`Broadcaster:` line in `view_frames` output — inherits that placeholder. Do not
read it as an answer.

Start from the topic's endpoints instead:

```bash
ros2 topic info /tf -v           # live /tf publisher endpoints: node, namespace, GID, QoS
ros2 topic info /tf_static -v
```

**Read this as a first step, not a verdict.** `ros2 topic info /tf -v`
identifies the live `/tf` publisher endpoints and their GIDs. It does not by
itself attribute each individual parent→child edge to an endpoint: one publisher
can carry many transforms in a single `TFMessage`, and the CLI output does not
connect a specific edge to the endpoint that sent it.

When you genuinely need per-edge attribution, one of these is additionally
required:

- stop or isolate candidate publishers one at a time and observe which edge
  disappears;
- a diagnostic subscriber that records each received transform together with the
  middleware publisher GID exposed through message metadata;
- narrowing the candidates structurally, from the launch and process layout;
- process logs, or an instrumented broadcaster that announces what it publishes.

Then check the three things a successful lookup does not cover:

```bash
# Rate and end-to-end delay per frame chain
ros2 run tf2_ros tf2_monitor
ros2 run tf2_ros tf2_monitor odom base_link     # one chain only

# The transform as of now, with its timestamp
ros2 run tf2_ros tf2_echo odom base_link
```

- **Duplicate edges.** Two broadcasters publishing the same parent→child pair
  both insert into that edge's history, and a lookup returns whatever the
  interleaved history yields for the requested time — note that an identical
  timestamp is *ignored* rather than overwriting the earlier sample. tf2 logs
  `TF_REPEATED_DATA ignoring data with redundant timestamp` when the *same*
  stamp arrives twice — treat that as a **clue, not proof**: a single publisher
  resending an identical stamp, or a bad replay path (a bag played back
  alongside the live publisher), produces the same warning. Equally, the warning
  is *absent* when two broadcasters emit different timestamps, which is the
  worse case. Confirm structurally instead: every edge should have exactly one
  owner, written down. `robot_state_publisher` plus a driver both publishing an
  odom→base_link or a joint-derived edge is the classic collision.
- **Freshness.** `tf2_monitor`'s average/max delay is the number to watch. A
  chain that stopped updating still resolves for as long as the buffer's cache
  window holds data (10 s by default), so a stale pose looks like a working one
  until the lookups start failing.
- **Time source.** If some nodes run `use_sim_time: true` and others do not,
  timestamps come from two clocks and the extrapolation errors will name a
  frame that is not the actual problem (`references/tf2-urdf.md`).

## 8. Stale daemon vs real processes

`ros2 node list` and friends answer from the CLI daemon's cached view of the
graph. The cache can outlive the nodes, and it is bound to the discovery
settings in effect when the daemon started.

```bash
ros2 node list --no-daemon        # bypass the cache, discover directly (slower)
ros2 daemon stop && ros2 daemon start
ros2 daemon status
```

If `ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION`, or the discovery range changed after
the daemon started, every CLI answer reflects the old configuration until it is
restarted (`references/deployment.md` §"ROS 2 daemon").

Then confirm against the operating system, which does not cache:

```bash
pgrep -af 'ros2|controller_server|robot_state_publisher'
ps -eo pid,ppid,etime,cmd | grep -i my_robot | grep -v grep
systemctl status my-robot-bringup.service      # if launched by systemd
```

**ROS 2 permits duplicate node names.** Two processes with the same node name
both join the graph; `ros2 node list` shows the name once (or twice, depending
on the CLI version), while both are publishing. A leftover process from a
previous run — a launch that was Ctrl-C'd but left an orphan, a systemd unit
that restarted while a manual copy was running — presents as one node and
behaves as two publishers. The process list is the authoritative answer, and
`ros2 topic info -v` on its output topic confirms the duplication.

## 9. Provenance checklist

Work through this before concluding that a config change had any effect. Each
row lists what the answer does **not** prove, because that is where the wrong
conclusions come from.

| # | Question | Command | Does not prove |
|---|---|---|---|
| 1 | How does *this shell* resolve the package? | `ros2 pkg prefix [--share]`, module `__file__` | anything about an already-running process (§1) |
| 2 | How was the process invoked? | `tr '\0' ' ' < /proc/$pid/cmdline` | which module a Python entry point loaded |
| 3 | Which binary is executing? | `readlink /proc/$pid/exe` | script identity — for Python nodes this is the interpreter |
| 4 | Which prefixes can it see, in what order? | `AMENT_PREFIX_PATH` from `/proc/$pid/environ` | which prefix a given package resolved to |
| 5 | Where did its config come from? | `ros2 pkg prefix --share`, then `diff` vs source | that the node read that file |
| 6 | Which parameters are in effect? | `ros2 param dump` | that they were applied to the running behavior |
| 7 | Who publishes the command topic? | `ros2 topic info <topic> -v` | that no other node will start publishing later — it is a point-in-time observation |
| 8 | Which endpoints publish `/tf`? | `ros2 topic info /tf -v` + written ownership map | which endpoint sent a *specific* edge (§7), or that the data is fresh |
| 9 | Is the graph view current? | `--no-daemon`, `ros2 daemon stop/start` | that only one process backs each node name |
| 10 | Which processes are really alive? | `pgrep -af`, `systemctl status` | that they are all the ones you launched |

Limits that apply to the whole checklist, not to single rows:

- **Node names and OS PIDs are not 1:1.** A component container is one process
  holding many nodes, and ROS 2 permits duplicate node names across processes —
  so "the node" may be neither one process nor one name.
- **`/proc` inspection is Linux-only and PID-namespace-scoped**, and requires
  sufficient permission — normally the same user or elevated diagnostic access,
  subject to container isolation and host security policy. A PID means something
  different inside a container than on the host; run the check in the same
  namespace as the process, or the answer belongs to another process entirely.
- **Point-in-time observations do not cover intermittent faults.** Publisher
  contention that appears for 200 ms during a reconnect will not show up in a
  single `ros2 topic info -v`. Sample repeatedly, or monitor graph events.

Cite the level with the result (`references/engineering-principles.md` Principle
13): this checklist reaches **L3** with the robot disconnected, **L4** with
hardware powered and actuation isolated. It never reaches L5 — nothing here
commands motion.

## 10. Common failures and fixes

| Symptom | Why it happens | Fix |
|---|---|---|
| Config edit has no effect | Node loaded the installed copy, not `src/` | `diff` source against `ros2 pkg prefix --share`; rebuild, or use `--symlink-install` |
| Rebuilt, still the old behavior | Another overlay earlier in `AMENT_PREFIX_PATH` shadows the package | Read `AMENT_PREFIX_PATH` from `/proc/<pid>/environ`; re-source in the right order |
| Deleted file still loads | `install/` keeps artifacts of removed sources | Delete `build/`, `install/`, `log/` for that package and rebuild |
| YAML values ignored, defaults in force | Node-name key or namespace in the YAML does not match the running node | Compare `ros2 param dump` against the file; fix the key or pass `--params-file` correctly |
| Stop command "sent" but robot moves | A second publisher on the command topic overwrites it | `ros2 topic info <topic> -v` — enforce a single arbiter (`references/safety-estop.md` §3) |
| Pose looks right, robot behaves wrong | TF edge published by two broadcasters, or stale within the cache window | Identify `/tf` publishers, map edge ownership, check `tf2_monitor` delay |
| `view_frames` broadcaster says nothing useful | ROS 2 stores `"Authority undetectable"` — there is no per-transform publisher identity | Use `ros2 topic info /tf -v` instead |
| Node listed but nothing responds | Stale CLI daemon cache, or discovery env changed after the daemon started | `--no-daemon`, then `ros2 daemon stop && ros2 daemon start` |
| Two behaviors from "one" node | Duplicate node names — an orphan process from a previous run | `pgrep -af` against the expected process list; kill the orphan |

---

**See also:** `references/debugging.md` for the introspection CLI reference and
DDS-level diagnosis, `references/workspace-build.md` §6 for overlay mechanics at
build time, `references/system-diagnostics.md` for faults that originate outside
the ROS graph, `references/safety-estop.md` §3 for command-topic ownership.
