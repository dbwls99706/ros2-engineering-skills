# ROS CI execution and diagnosis

## Build is not execution

`tests/Dockerfile.ros2-test` builds the dependency environment and copies the
repository. It does not run tests in cached image layers. Its default command is
`tests/run_ros2_container_tests.sh`, not an unconditional success message.

Both CI and local Linux development use:

```bash
bash tests/run_ros2_tests.sh humble
bash tests/run_ros2_tests.sh humble jazzy kilted lyrical rolling
```

The host needs Docker with Buildx and GNU `timeout`. Each distribution uses a
uniquely named `docker-container` builder, loads the completed image, inspects it,
and executes the suite in a fresh `--init --network none` container. All names are
owned by that invocation; cleanup does not prune unrelated Docker resources.

Image build, container execution, and cleanup have bounded deadlines. A build
failure, runtime failure, or timeout returns nonzero. An exporter printing `DONE`
is not sufficient: the build command must return zero, the image must be present,
and the runtime suite must finish. Logs are piped with `pipefail`, so `tee` cannot
turn a failed Docker command into success. Cleanup preserves the original result.

## Gates retained

The runtime suite executes the repository Python tests, generates all four
package types, compiles them with testing enabled, runs CTest and checks its
results, runs the generated Python tests, validates both launch directories,
and checks all seven QoS presets. Stable distributions additionally run live
node discovery and the positive-control QoS mismatch/repair experiment.

Smoke tests launch the installed executables directly, assign unique node names,
and observe the graph through a separate rclpy node. No `ros2 run` wrapper or
CLI discovery daemon is involved. Both the exact graph name and a live owned
process are required. Process-group cleanup escalates from SIGINT to SIGTERM to
SIGKILL within bounded waits, including on failed discovery and interruption.
The runtime container's init reaps orphaned children; host cleanup removes the
owned container even after failure.

Rolling retains the pre-existing source-overlay workaround and explicit
rclcpp/rclpy initialization, node discovery, and QoS runtime exclusions. Its
interface and hardware-plugin tests still run. Passing applicable Rolling gates
is not an L3 DDS claim, and the exclusions are not automatically waived.

## Evidence and interpretation

The runner prints its evidence directory. Override it with `ROS_TEST_LOG_DIR`.
CI uploads a separate `ros2-evidence-<distro>` artifact on success or failure.
It includes build/runtime logs, Docker and Buildx versions, image/container
inspection, an exit-code file, and, when the container reached those stages,
installed package versions, exact Rolling overlay revisions, and colcon logs.
A hard runner termination can prevent final cleanup or artifact upload; missing
evidence must not be treated as success.

The motivating run at revision `e76cea8` printed passing tests and image-export
completion, then was cancelled near its 30-minute job limit. That establishes
that the job did not complete, not which Docker or process defect caused the
post-export delay. This refactor removes ROS process lifetimes from BuildKit,
separates actual execution from caching, and retains diagnostic evidence rather
than asserting an unproven root cause.

The `Required test gates` job requires every declared Test-workflow dependency
to report success. Failure, cancellation, skip, or a missing result fails the
summary. Client discovery is a separate workflow and must also be inspected for
the exact revision. Older commits keep their historical results; a later passing
commit does not retroactively validate an earlier one. A lone green check is not
evidence that all workflow suites ran.

Synthetic Docker transport and process-lifecycle regression tests do not execute
ROS, prove middleware behavior, or benchmark an AI model. The actual distro jobs
provide the corresponding execution evidence. Hardware behavior remains untested.

Sources: [Docker build drivers](https://docs.docker.com/build/builders/drivers/),
[Docker init process](https://docs.docker.com/reference/cli/docker/container/run/#init),
and [GNU timeout](https://www.gnu.org/software/coreutils/manual/html_node/timeout-invocation.html).
