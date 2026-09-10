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

Generated fleet runtime checks use the shipped `scripts/launch_supervisor.py`
entry point. Its POSIX asyncio signal handler stays active through finalization,
preventing the default handler from raising `KeyboardInterrupt` between
dequeueing and executing a callback. Every trial still requires the supervisor to return zero
within 10 seconds and a zero-exit event for every started child. Diagnostic or
forced-cleanup exits cannot satisfy those assertions. Startup, parameter loading,
sibling isolation, discarded transition events, and rapid shutdown remain checked.

`check_launch_signals.py` injects SIGINT at that callback boundary using the
installed ROS signal manager. The raising-handler negative control must expose
the lost callback; the shipped supervisor must complete under the same injection.
The unit regression also delivers real SIGINT during handler installation and
requires exit 130 without starting the service. The installed CLI controls allow
10 seconds each for cold imports; their 40-second outer budget includes three
cases and signal controls. The fleet shutdown deadline remains 10 seconds.
Native `ros2 launch --show-args` checks
installed fleet discovery/imports separately. A passing supervised run does not
claim that native `ros2 launch` shutdown was fixed; see the
[execution guidance](../references/launch-system.md#supervisor-shutdown-stalls).

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

The `Required test gates` job requires every declared Test-workflow dependency
to report success. Failure, cancellation, skip, or a missing result fails the
summary. Client discovery is a separate workflow and must also be inspected for
the exact revision. Older commits keep their historical results; a later passing
commit does not retroactively validate an earlier one. A lone green check is not
evidence that all workflow suites ran.

Enumerate runs for the exact `head_sha` across **both** `push` and `pull_request`
events, including every page and relevant attempt. Inspect Test and Client
discovery in each event. An API limited to one event is not a complete CI
inventory. Preserve failed attempts when reporting reliability; a successful
rerun does not erase the first outcome.

Synthetic Docker transport and process-lifecycle regression tests do not execute
ROS, prove middleware behavior, or benchmark an AI model. The actual distro jobs
provide the corresponding execution evidence. Hardware behavior remains untested.

Sources: [Docker build drivers](https://docs.docker.com/build/builders/drivers/),
[Docker init process](https://docs.docker.com/reference/cli/docker/container/run/#init),
and [GNU timeout](https://www.gnu.org/software/coreutils/manual/html_node/timeout-invocation.html).

## Offline XML schema validation

The network-isolated runtime still runs `ament_xmllint`. Generated `package.xml`
files reference `http://download.ros.org/schema/package_format3.xsd`. A pristine
container without this schema cannot validate those files offline; a successful
image build does not satisfy this runtime dependency.

The image now downloads `package_format3.xsd` **and** `package_common.xsd` from
ROS REP commit `11ca24a41f31480dfb9562ba99f2a5b93d3ebda5`, checks their SHA-256
hashes, and installs a catalog through `XML_CATALOG_FILES`. The catalog maps the
original HTTP/HTTPS schema identifiers to the local files. The generator and
its emitted manifests are unchanged. No schema validation or runtime network
isolation is disabled.

Before the suite, `tests/check_offline_xml.py` requires both `xmllint --nonet`
and `ament_xmllint` to accept a valid control and reject a manifest missing its
required license. A missing tool, failed positive control, accepted negative
control, or timed-out command fails. Newer ament versions may report that their
initial URL download failed before falling back to libxml's catalog; the actual
validation exit status, not that warning alone, determines the result.

Sources: [REP schema](https://github.com/ros-infrastructure/rep/blob/11ca24a41f31480dfb9562ba99f2a5b93d3ebda5/xsd/package_format3.xsd)
and [ament XML validation](https://github.com/ament/ament_lint/blob/rolling/ament_xmllint/ament_xmllint/main.py).
