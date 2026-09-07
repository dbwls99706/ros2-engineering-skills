# Local execution with an offline ROS environment

`tests/Dockerfile.runtime-bundle` prepares a Humble development environment from
OSRF's official `ros:humble-ros-base-jammy` image. It includes build tools,
Fast DDS and Cyclone DDS, repository test dependencies, and the same hash-checked
XML schemas used by the existing ROS CI. It does not bake in a passing test result.

`runtime-bundle.yml` exports this filesystem as a short-lived artifact when its
bundle definition changes on the development branch, or on manual dispatch.
The artifact records the source revision, base image digest, built image config,
package inventory, and archive checksum. Developer dependencies live in
`/opt/validation-venv`; ROS tests retain the distribution Python/pytest stack. Verify the checksum before use.
An exported filesystem is not a Docker image archive: use `docker import`, not
`docker load`, and restore the environment explicitly before running a task.

```bash
sha256sum --check SHA256SUMS
docker import rootfs.tar.gz ros2-offline-runtime
docker run --rm --init --network none -v "$PWD:/work/skill:ro" \
  ros2-offline-runtime bash -lc \
  'source /opt/ros/humble/setup.bash; python3 -c "import rclpy; print(rclpy.__file__)"'
```

A trusted disposable sandbox can instead extract the filesystem and run it in
`chroot` when nested Docker is unavailable. Chroot is not a security boundary;
record the host kernel, missing virtual filesystems, transport configuration,
actual task commands, and execution results. Do not use it on a physical robot.
A successful export establishes environment availability, not ROS task success,
skill selection by a client, model improvement, or hardware safety.

Source: [Official ROS images](https://hub.docker.com/_/ros).

## Recorded local acceptance

On 2026-09-07, the exact `61ab6d68189381aebb2cc9354244c8c0b5bf2c93` source
archive was transferred into an independent execution sandbox. The environment
was Ubuntu 22.04 userland, Python 3.10.12, rclpy 3.3.21, rclcpp 16.0.19,
and hardware_interface 2.54.0 on a Linux 6.18.35 host. This was actual local
execution, not playback of earlier CI logs and not a model A/B evaluation.

It exposed three generator defects: missing component target linkage, repeated
parameter declaration on configure, and an ordinary timer surviving active
shutdown. Invalid timer rates were also accepted or raised transition errors.
After correction, all six generated variants built; live configure/activate/
deactivate/cleanup cycles, reactivation, active shutdown, invalid-rate rejection,
and repair after rejection passed. The reusable check is:

```bash
source /path/to/generated_ws/install/setup.bash
python3 tests/check_generated_lifecycle.py YOUR_LIFECYCLE_PACKAGE
```

Additional local tasks verified real generated launch activation, dynamic C++
component loading/listing/unloading, and source/install parameter divergence
(edited source: 7 Hz; unchanged installed configuration: 50 Hz; rebuilt/restarted:
7 Hz). QoS mismatch/repair was repeated three times on each of Fast DDS and
Cyclone DDS; the incompatible subscriber received zero samples and the repaired
subscriber received at least 25. A bounded callback wait stalled in the same
mutually exclusive group, while async return and separate groups with sufficient
workers completed; both middleware implementations were exercised.

These results reach L2/L3 in this isolated software environment. They do not
establish cross-client automatic skill selection, comparative model quality,
network performance on other hosts, or physical safety. Chroot lacked mounted
`/proc` and `/sys`; `/dev/shm` was an ordinary directory. Local subprocess PIDs,
command results, initial provisioning failures, and before/after logs must remain
part of the evidence rather than being silently replaced by successful runs.

## Executing the reference examples themselves

A separate local run began from `0867b34df11d7fd8a4779fd7edbeb946ada1799c`
with the matching exported Humble runtime. Six generated variants built, both
DDS implementations passed repeated QoS/lifecycle/callback checks, and live
launch, component load/list/unload, and source/install parameter checks passed.

Executing the verbatim lifecycle reference exposed two additional defects:
C++ activation returned success without enabling its publisher, and Python
cleanup destroyed the native publisher without unregistering its managed object.
The original C++ example received zero filtered samples despite a positive input
control. After three Python cleanup cycles, three destroyed publishers remained
retained. The corrected examples delegate activation/deactivation and use
`destroy_lifecycle_publisher`, with explicit shutdown/error resource cleanup.

`tests/check_lifecycle_reference.py` now extracts those exact examples, compiles
and executes the C++ node, and checks Python object release across repeated
transitions, active shutdown, and deliberate transition ERROR recovery. The C++
probe labels samples by their input phase so queued active samples are not
misclassified as newly published inactive samples. Reverting either example
makes the same probe fail. Both corrected examples passed locally with Fast DDS
and Cyclone DDS; stable ROS CI jobs also run the probe. The pure-Python extraction
tests are not substitutes for this runtime check.
