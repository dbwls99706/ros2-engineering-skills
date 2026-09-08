# Generated lifecycle startup

## Failure and scope

PR #31's Kilted run (`34173506019`, job `101898273553`) configured both C++
fleet nodes, but only one reached Active. The other remained Inactive until the
20-second readiness check failed. The subsequent SIGINT was cleanup after that
failure, not evidence that the original failure was a shutdown timeout.

The previous generated launch used `OnStateTransition` to trigger activation.
The launch adapter obtains those events from a DDS `transition_event`
subscription; a reachable configure service does not establish that this
subscription has received the completed transition. If the event is missed,
there was no state query to advance startup. The CI log alone does not locate
where an event was lost or delayed.

## Correction

Both single-node and fleet lifecycle launches now query the target node's
`GetState` service. Once configuration has reached Inactive, a node-specific
`ChangeState` event requests activation exactly once. The coroutine exits after
observing Active; it is not a supervisor that reactivates an intentionally
stopped node. Missing services, failed configuration, or failed activation stay
bounded by a 15-second startup deadline and fail with the fully qualified node
name and last observed state. No transition is retried to hide a failure.

Normal launch shutdown cancels the pending state request without turning the
expected coroutine cancellation into a launch error. Unexpected cancellation
still propagates. The launch ROS adapter retains ownership of its shared node
and destroys its clients during shutdown.

## Regression evidence

`tests/check_generated_fleet.py` retains ordinary installed-fleet checks and adds
fresh Python-lifecycle and C++ fleet launches whose launch-side transition-event
callbacks are deliberately discarded. Generated node code is unchanged in that
fault-injection trial. The probe requires evidence that the discard callback ran,
actual Active states, the nondefault 17.0 parameter, sibling isolation, and a
successful exit record for every child process. Prior attempts' telemetry is
cleared before another trial. `tests/test_lifecycle_startup.py` separately tests
state polling, deadlines, targeting, single-request behavior, and cancellation;
these synthetic logic tests are not ROS execution results.

The old generator from `dd98d167dcc7f7e6266b38d16d77b454e2fd160e` fails the
same local event-loss control with its nodes left Inactive. The corrected Humble
run passes both ordinary and event-loss fleet trials. CI runs the same probe in
its stable distribution jobs. Consult the exact workflow revision and result
before claiming a particular release was verified on a distribution.

This repairs startup's dependency on event delivery. It does not establish a
root cause for the separately recorded Cyclone DDS immediate-shutdown timeout,
remove Rolling runtime exclusions, or certify physical robot safety.

Sources: [Kilted lifecycle event manager](https://github.com/ros2/launch_ros/blob/kilted/launch_ros/launch_ros/utilities/lifecycle_event_manager.py)
and [OpaqueCoroutine](https://docs.ros.org/en/ros2_packages/kilted/api/launch/launch.actions.opaque_coroutine.html).
