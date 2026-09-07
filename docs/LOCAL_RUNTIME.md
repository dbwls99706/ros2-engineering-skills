# Local execution with an offline ROS environment

`tests/Dockerfile.runtime-bundle` prepares a Humble development environment from
OSRF's official `ros:humble-ros-base-jammy` image. It includes build tools,
Fast DDS and Cyclone DDS, repository test dependencies, and the same hash-checked
XML schemas used by the existing ROS CI. It does not bake in a passing test result.

`runtime-bundle.yml` exports this filesystem as a short-lived artifact when its
bundle definition changes on the development branch, or on manual dispatch.
The artifact records the source revision, base image digest, built image config,
package inventory, and archive checksum. Verify the checksum before use.
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
