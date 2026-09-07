#!/usr/bin/env bash
# Runtime-only smoke test. Source ROS without nounset (setup hooks may use unset vars).
set -eo pipefail
: "${ROS_DISTRO:?ROS_DISTRO is required}"
source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /ws/test_ws/install/setup.bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/smoke_test_nodes.py" /ws/test_ws/install
