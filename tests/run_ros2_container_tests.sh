#!/usr/bin/env bash
# Execute every ROS gate in a disposable container; image builds do not run tests.
set -eo pipefail
: "${ROS_DISTRO:?Source a ROS distribution or use the test image}"
source "/opt/ros/${ROS_DISTRO}/setup.bash"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS=/ws/test_ws
cd "$ROOT"

# Preserve exact installed packages, including Rolling's source-overlay revisions.
{
    printf 'ROS_DISTRO=%s\n' "$ROS_DISTRO"
    uname -a
    python3 --version
    cat /opt/ros2-test-schemas/SHA256SUMS
    dpkg-query -W -f='${Package}=${Version}\n'
    if [[ -f "/opt/ros/${ROS_DISTRO}/ci-overlay.repos" ]]; then
        cat "/opt/ros/${ROS_DISTRO}/ci-overlay.repos"
    fi
} > /ws/ros-environment.txt

echo '=== Offline XML schema controls ==='
python3 tests/check_offline_xml.py

echo '=== Repository unit tests ==='
python3 -m pytest tests/ -ra --tb=short --durations=15 -o faulthandler_timeout=45

echo '=== Generate four package types plus component and lifecycle variants ==='
mkdir -p "$WS/src"
python3 scripts/create_package.py test_cpp_pkg --type cpp --dest "$WS/src"
python3 scripts/create_package.py test_py_pkg --type python --dest "$WS/src"
python3 scripts/create_package.py test_component_pkg --type cpp --component --dest "$WS/src"
python3 scripts/create_package.py test_lifecycle_pkg --type python --lifecycle --dest "$WS/src"
python3 scripts/create_package.py test_iface_pkg --type interfaces --dest "$WS/src"
python3 scripts/create_package.py test_hw_pkg --type hardware_interface --dest "$WS/src"
cd "$WS"
colcon build --executor sequential --event-handlers console_direct+ \
    --cmake-args -DCMAKE_BUILD_TYPE=Release

# Preserve the existing Rolling exclusion, without calling it a runtime pass.
# Hardware plugin loading and interface tests still execute on every distro.
test_pkgs=(test_cpp_pkg test_component_pkg test_iface_pkg test_hw_pkg)
pytest_extra=()
if [[ "$ROS_DISTRO" == rolling ]]; then
    test_pkgs=(test_iface_pkg test_hw_pkg)
    pytest_extra=(--deselect test/test_test_py_pkg.py::test_node_creation)
    echo '[EXCLUDED] Rolling rclcpp/rclpy init, node discovery, and QoS runtime gates'
fi
echo '=== CTest / generated hardware plugin tests ==='
colcon test --executor sequential --event-handlers console_direct+ \
    --packages-select "${test_pkgs[@]}"
colcon test-result --verbose

source "$WS/install/setup.bash"
echo '=== Generated Python package tests ==='
(cd "$WS/src/test_py_pkg" && python3 -m pytest test/ -v "${pytest_extra[@]}")
if [[ "$ROS_DISTRO" != rolling ]]; then
    (cd "$WS/src/test_lifecycle_pkg" && python3 -m pytest test/ -v)
    timeout --signal=TERM --kill-after=5s 30s \
        python3 "$ROOT/tests/check_generated_lifecycle.py" test_lifecycle_pkg
fi
cd "$ROOT"
echo '=== Generated launch files and all QoS presets ==='
python3 scripts/launch_validator.py "$WS/src/test_cpp_pkg/launch/"
python3 scripts/launch_validator.py "$WS/src/test_py_pkg/launch/"
python3 scripts/launch_validator.py "$WS/src/test_component_pkg/launch/"
python3 scripts/launch_validator.py "$WS/src/test_lifecycle_pkg/launch/"
for preset in sensor command map diagnostics parameter_events action_feedback safety_heartbeat; do
    python3 scripts/qos_checker.py --preset "$preset"
done

if [[ "$ROS_DISTRO" != rolling ]]; then
    echo '=== Live generated node discovery and process cleanup ==='
    timeout --signal=TERM --kill-after=10s 60s bash tests/smoke_test_nodes.sh
    echo '=== Controlled QoS mismatch, positive control, and repair ==='
    timeout --signal=TERM --kill-after=5s 30s python3 examples/qos_roundtrip.py
fi
printf 'All applicable ROS gates completed for %s; exclusions above are not passes.\n' "$ROS_DISTRO"
