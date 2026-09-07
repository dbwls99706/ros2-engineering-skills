#!/usr/bin/env bash
# Linux + Docker + GNU timeout. CI and local runs use this same full-suite runner.
# Usage: bash tests/run_ros2_tests.sh [humble jazzy kilted lyrical rolling]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DISTROS=("${@:-}")
if [[ $# -eq 0 ]]; then DISTROS=(humble jazzy kilted lyrical rolling); fi
for distro in "${DISTROS[@]}"; do
    case "$distro" in
        humble|jazzy|kilted|lyrical|rolling) ;;
        *) printf 'Unsupported ROS distro: %s\n' "$distro" >&2; exit 2 ;;
    esac
done
for tool in docker timeout; do
    command -v "$tool" >/dev/null || { echo "Required tool missing: $tool" >&2; exit 2; }
done
LOG_ROOT="${ROS_TEST_LOG_DIR:-${TMPDIR:-/tmp}/ros2-skills-tests-$$}"
mkdir -p "$LOG_ROOT"
LOG_ROOT="$(cd "$LOG_ROOT" && pwd)"
printf 'ROS test evidence: %s\n' "$LOG_ROOT"

run_distro() (
    # This function is called in an if statement: every tested command therefore
    # checks its exit status explicitly rather than relying on Bash errexit.
    distro="$1"
    out="$LOG_ROOT/$distro"
    mkdir -p "$out" || exit 1
    token="ros2-skills-${distro}-$$"
    builder="$token-builder"
    container="$token-tests"
    tag="$token:local"

    cleanup() {
        rc=$?
        trap - EXIT INT TERM
        # Diagnostic commands are bounded and never replace the original result.
        timeout -k 2s 10s docker inspect "$container" > "$out/container.json" 2>&1 || true
        timeout -k 2s 10s docker cp "$container:/ws/ros-environment.txt" "$out/environment.txt" \
            >> "$out/diagnostics.log" 2>&1 || true
        timeout -k 2s 15s docker cp "$container:/ws/test_ws/log" "$out/colcon-log" \
            >> "$out/diagnostics.log" 2>&1 || true
        if [[ $rc -ne 0 ]]; then
            timeout -k 2s 10s docker logs "buildx_buildkit_${builder}0" \
                >> "$out/diagnostics.log" 2>&1 || true
        fi
        timeout -k 2s 10s docker rm -f "$container" >> "$out/diagnostics.log" 2>&1 || true
        timeout -k 2s 15s docker buildx rm "$builder" >> "$out/diagnostics.log" 2>&1 || true
        # Remove only the uniquely named image this invocation created.
        timeout -k 2s 10s docker image rm "$tag" >> "$out/diagnostics.log" 2>&1 || true
        printf '%s\n' "$rc" > "$out/exit-code.txt"
        exit "$rc"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    timeout -k 2s 15s docker version > "$out/docker-version.txt" 2>&1 || exit $?
    timeout -k 2s 15s docker buildx version > "$out/buildx-version.txt" 2>&1 || exit $?
    printf '=== %s: isolated image build (not test evidence) ===\n' "$distro"
    timeout -k 5s 90s docker buildx create --name "$builder" --driver docker-container \
        --bootstrap > "$out/builder.log" 2>&1 || exit $?
    # An isolated builder avoids relying on the host daemon's embedded BuildKit.
    # --load is mandatory: the following runtime gate must execute this image.
    if timeout --signal=TERM --kill-after=10s 20m docker buildx build \
        --builder "$builder" --load --progress=plain \
        -f "$SCRIPT_DIR/Dockerfile.ros2-test" --build-arg "ROS_DISTRO=$distro" \
        -t "$tag" "$PROJECT_DIR" 2>&1 | tee "$out/build.log"; then
        :
    else
        rc=$?; printf 'Image build failed or timed out (%s)\n' "$rc" >&2; exit "$rc"
    fi
    timeout -k 2s 15s docker image inspect "$tag" > "$out/image.json" 2>&1 || exit $?
    printf '=== %s: fresh isolated container / all applicable ROS gates ===\n' "$distro"
    if timeout --signal=TERM --kill-after=10s 390s docker run \
        --name "$container" --init --network none \
        -e ROS_DOMAIN_ID=83 -e ROS_LOCALHOST_ONLY=1 "$tag" \
        timeout --signal=TERM --kill-after=10s 360s bash tests/run_ros2_container_tests.sh \
        2>&1 | tee "$out/runtime.log"; then
        printf '[PASS] ROS 2 %s: build AND all applicable runtime gates completed\n' "$distro"
    else
        rc=$?; printf 'Runtime suite failed or timed out (%s)\n' "$rc" >&2; exit "$rc"
    fi
)

failed=()
for distro in "${DISTROS[@]}"; do
    if run_distro "$distro"; then
        :
    else
        failed+=("$distro")
        printf '[FAIL] ROS 2 %s; inspect %s/%s\n' "$distro" "$LOG_ROOT" "$distro" >&2
    fi
done
if [[ ${#failed[@]} -gt 0 ]]; then
    printf 'Failed distros: %s\n' "${failed[*]}" >&2
    exit 1
fi
echo 'All requested distro runs completed. Rolling exclusions are not DDS passes.'
