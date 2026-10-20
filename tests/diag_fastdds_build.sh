#!/usr/bin/env bash
# Rebuild the installed Humble Fast DDS from its exact source package, in one diagnostic variant
# (diagnostic branch only). Runs inside the pinned osrf/ros:humble-desktop image with network.
#
#   rebuilt        same source, packaging, and flags as the buildfarm; no change (control)
#   patched        + tests/diag_fastdds_listener_race.patch (reader listener gate)
#   rebuilt-asan   rebuilt + AddressSanitizer
#   patched-asan   patched + AddressSanitizer
#
# Writes the .deb and the debug-symbol package to OUT, plus checks.txt with every self-check.
set -euo pipefail
variant=$1
out=$2
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pool=http://packages.ros.org/ros2/ubuntu/pool/main/r/ros-humble-fastrtps
source_version=2.6.12-1jammy
binary_version=2.6.12-1jammy.20260723.233918
case "$variant" in
  rebuilt|patched|rebuilt-asan|patched-asan) ;;
  *) echo "Unknown variant: $variant" >&2; exit 2 ;;
esac
suffix="+diag.${variant//-/.}"
mkdir -p "$out"
checks="$out/checks.txt"
: > "$checks"
check() { echo "$1=$2" | tee -a "$checks"; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
# --no-upgrade: packages already in the image stay at the versions the stock library was built against.
apt-get install -y --no-install-recommends --no-upgrade dpkg-dev debhelper fakeroot curl gdb binutils patch \
    cmake libasio-dev libssl-dev libtinyxml2-dev python3-dev \
    ros-humble-fastcdr ros-humble-foonathan-memory-vendor ros-humble-ros-workspace
check installed_fastrtps "$(dpkg-query -W -f='${Version}' ros-humble-fastrtps)"
check compiler "$(g++ --version | head -1)"

work=/tmp/fastdds-build
rm -rf "$work"
mkdir -p "$work"
cd "$work"
for file in "ros-humble-fastrtps_${source_version}.dsc" "ros-humble-fastrtps_2.6.12.orig.tar.gz" \
        "ros-humble-fastrtps_${source_version}.debian.tar.xz"; do
    curl -fsSL --max-time 300 -o "$file" "$pool/$file"
done
sha256sum ./*.dsc ./*.tar.* | tee "$out/source.sha256"
# Checks every file against the checksums the .dsc lists.
dpkg-source -x "ros-humble-fastrtps_${source_version}.dsc" src
cd src

# The buildfarm stamps the binary version at build time; keep that version and mark the variant.
sed -i "1s/(${source_version})/(${binary_version}${suffix})/" debian/changelog
head -1 debian/changelog | tee -a "$checks"

if [[ "$variant" == patched* ]]; then
    patch -p1 < "$here/diag_fastdds_listener_race.patch"
fi
gated=$(grep -c "listener_gate::with_listener" src/cpp/rtps/reader/StatefulReader.cpp || true)
check patch_applied "$([[ $gated -gt 0 ]] && echo yes || echo no)"
if [[ "$variant" == patched* && $gated -eq 0 ]] || [[ "$variant" == rebuilt* && $gated -ne 0 ]]; then
    echo "Patch state does not match variant $variant" >&2
    exit 1
fi

if [[ "$variant" == *asan ]]; then
    # debian/rules sets DEB_CXXFLAGS_MAINT_APPEND itself; the user-level append is honored on top.
    export DEB_CXXFLAGS_APPEND="-fsanitize=address -fno-omit-frame-pointer"
    export DEB_CFLAGS_APPEND="-fsanitize=address -fno-omit-frame-pointer"
fi
DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)" dpkg-buildpackage -b -uc -us > "$out/build.log" 2>&1 \
    || { tail -200 "$out/build.log"; exit 1; }

flags=$(find .obj-* -path '*fastrtps.dir/flags.make' | head -1)
check flags_file "$flags"
check flags_have_asan "$(grep -q -- '-fsanitize=address' "$flags" && echo yes || echo no)"
cd "$work"
deb=$(ls ros-humble-fastrtps_*"${suffix}"_amd64.deb)
ddeb=$(ls ros-humble-fastrtps-dbgsym_*"${suffix}"_amd64.*deb)
cp "$deb" "$out/ros-humble-fastrtps.deb"
cp "$ddeb" "$out/ros-humble-fastrtps-dbgsym.deb"

# Install the result here to check the library and its debug information as built.
dpkg -i "$out/ros-humble-fastrtps.deb" "$out/ros-humble-fastrtps-dbgsym.deb"
lib=/opt/ros/humble/lib/libfastrtps.so.2.6.12
check installed_version "$(dpkg-query -W -f='${Version}' ros-humble-fastrtps)"
check library_sha256 "$(sha256sum "$lib" | cut -d' ' -f1)"
check needs_libasan "$(readelf -d "$lib" | grep -q 'libasan.so' && echo yes || echo no)"
check asan_instrumented "$(nm -D --undefined-only "$lib" | grep -q '__asan_report' && echo yes || echo no)"
gate=$(gdb -batch -nx -ex 'info functions listener_gate::replace' "$lib" 2>&1)
# gdb echoes the pattern in its header line; only a listed function has the parameter list.
check gate_symbols "$(grep -q 'listener_gate::replace(' <<< "$gate" && echo yes || echo no)"
line=$(gdb -batch -nx -ex 'info line eprosima::fastrtps::rtps::StatefulReader::matched_writer_remove' "$lib" 2>&1)
echo "$line" > "$out/gdb-info-line.txt"
check debug_line "$(grep -qE '^Line [0-9]+ of "[^"]*StatefulReader\.cpp"' <<< "$line" && echo yes || echo no)"
sha256sum "$out"/*.deb | tee "$out/packages.sha256"

expect_asan=no
[[ "$variant" == *asan ]] && expect_asan=yes
expect_gate=no
[[ "$variant" == patched* ]] && expect_gate=yes
status=pass
grep -qx "flags_have_asan=$expect_asan" "$checks" || status=fail
grep -qx "needs_libasan=$expect_asan" "$checks" || status=fail
grep -qx "asan_instrumented=$expect_asan" "$checks" || status=fail
grep -qx "gate_symbols=$expect_gate" "$checks" || status=fail
grep -qx "debug_line=yes" "$checks" || status=fail
grep -qx "installed_version=${binary_version}${suffix}" "$checks" || status=fail
check status "$status"
[[ "$status" == pass ]]
