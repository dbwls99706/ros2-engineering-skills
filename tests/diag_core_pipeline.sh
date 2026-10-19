#!/usr/bin/env bash
# Stage 0 of the diagnostic branch, inside the diagnostic container: prove that a crashing
# process linked to Fast DDS leaves a core that gdb reads with Fast DDS debug information.
# The comparison matrix must not start unless tests/diag_core_check.py passes on this evidence.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${EVIDENCE_DIR:-/evidence}/stage0"
mkdir -p "$out"
set +u
source "/opt/ros/${ROS_DISTRO}/setup.bash"
set -u

cat /proc/sys/kernel/core_pattern > "$out/core_pattern.txt"
ulimit -c > "$out/ulimit_core.txt"
dpkg-query -W -f='${Package}=${Version}\n' gdb ros-humble-fastrtps ros-humble-fastrtps-dbgsym \
    > "$out/packages.txt"
cp /opt/diag/*.txt "$out/"

build=/tmp/crash_probe
mkdir -p "$build"
cat > "$build/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(crash_probe CXX)
find_package(fastrtps REQUIRED)
add_executable(crash_probe probe.cpp)
target_link_libraries(crash_probe fastrtps)
EOF
cat > "$build/probe.cpp" <<'EOF'
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>

#include <cstdio>
#include <fstream>
#include <string>

int main()
{
  // A real Fast DDS call keeps the library in DT_NEEDED under --as-needed and maps it.
  auto * factory = eprosima::fastdds::dds::DomainParticipantFactory::get_instance();
  std::printf("factory=%p\n", static_cast<void *>(factory));
  std::ifstream maps("/proc/self/maps");
  for (std::string line; std::getline(maps, line); ) {
    if (line.find("libfastrtps") != std::string::npos) {
      std::printf("mapped: %s\n", line.c_str());
    }
  }
  std::fflush(stdout);
  volatile int * pointer = nullptr;
  return *pointer;
}
EOF
cmake -S "$build" -B "$build/out" -DCMAKE_BUILD_TYPE=Debug > "$out/build.log" 2>&1
cmake --build "$build/out" >> "$out/build.log" 2>&1
exe="$build/out/crash_probe"
readelf -d "$exe" > "$out/readelf-dynamic.txt"
ldd "$exe" > "$out/ldd.txt"

set +e
"$exe" > "$out/probe-output.txt" 2>&1 &
pid=$!
wait "$pid"
status=$?
set -e
echo "$pid" > "$out/pid.txt"
echo "$status" > "$out/exit-status.txt"

shopt -s nullglob
cores=(/var/crash-cores/core.*."$pid".*)
printf '%s\n' "${cores[@]}" > "$out/cores.txt"
if [[ ${#cores[@]} -eq 1 ]]; then
    core="${cores[0]}"
    sha256sum "$core" "$exe" > "$out/sha256.txt"
    timeout 300 gdb -batch -nx -ex 'set pagination off' \
        -ex 'info line eprosima::fastrtps::rtps::StatefulReader::matched_writer_remove' \
        -x "$ROOT/tests/diag_gdb_inspect.py" "$exe" "$core" > "$out/gdb.txt" 2>&1 \
        || echo "gdb exited with $?" >> "$out/gdb.txt"
    gzip -1 -c "$core" > "$out/core.gz"
    cp "$exe" "$out/crash_probe"
fi

python3 "$ROOT/tests/diag_core_check.py" "$out" --write
