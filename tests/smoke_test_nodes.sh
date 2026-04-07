#!/bin/bash
# Smoke-test: verify generated nodes actually start in a ROS 2 environment.
# Called from Dockerfile.ros2-test Stage 7.
set -e

source /opt/ros/${ROS_DISTRO}/setup.bash
source /ws/test_ws/install/setup.bash

smoke_test_node() {
    local pkg="$1"
    local exe="$2"
    local label="$3"

    echo "=== Smoke-test: ${label} ==="
    ros2 run "${pkg}" "${exe}" &
    local node_pid=$!
    sleep 3

    if ros2 node list | grep -q "${pkg}"; then
        echo "${label} started OK"
    else
        echo "FAIL: ${label} not found in ros2 node list"
        kill "${node_pid}" 2>/dev/null || true
        exit 1
    fi

    kill "${node_pid}" 2>/dev/null || true
    wait "${node_pid}" 2>/dev/null || true
}

smoke_test_node test_cpp_pkg test_cpp_pkg_node "C++ lifecycle node"
smoke_test_node test_py_pkg test_py_pkg_node "Python node"

echo "All smoke tests passed."
