# Sensor Integration

> **Distro stability:** driver APIs, tf2 MessageFilter, and diagnostic_updater are
> stable across Humble, Jazzy, Kilted, and Rolling. Watch two deltas: the modern
> `static_transform_publisher` flag syntax (`--x`, `--frame-id`, …) replaced positional
> args in Humble and the old form is removed in Jazzy+, and several LiDAR drivers
> (Ouster, Velodyne) moved to lifecycle-node architecture in their Humble+ releases.
> Differences are tagged inline.

This guide covers attaching real cameras and LiDARs to a ROS 2 robot: driver bringup
and QoS, mounting frames, clock/timestamp synchronization, transform-synchronized
processing with `tf2_ros::MessageFilter`, LiDAR-camera extrinsic calibration, and
driver-level diagnostics. Image/point-cloud *processing* (cv_bridge, PCL, depth
pipelines, intrinsic calibration, `message_filters` fusion, EKF) is in
`references/perception.md`; persistent device naming and boot ordering are in
`references/system-bringup.md`.

## Table of contents

1. [Sensor driver bringup](#1-sensor-driver-bringup)
2. [Sensor mounting frames](#2-sensor-mounting-frames)
3. [Time synchronization](#3-time-synchronization)
4. [tf2-synchronized processing](#4-tf2-synchronized-processing)
5. [LiDAR-camera extrinsic calibration](#5-lidar-camera-extrinsic-calibration)
6. [Sensor diagnostics](#6-sensor-diagnostics)
7. [Common failures and fixes](#7-common-failures-and-fixes)

---

## 1. Sensor driver bringup

### Driver checklist — what "integrated" means

A sensor driver is integrated when all of these hold, not when data first appears
in RViz:

1. Device opens by **persistent name** (`/dev/lidar`, not `/dev/ttyUSB0`) — udev
   rules in `references/system-bringup.md` §2.
2. Publishes with **sensor-data QoS** (BEST_EFFORT, VOLATILE, shallow KEEP_LAST) —
   a RELIABLE camera stream stalls the driver when any subscriber falls behind.
3. Every message carries a **correct `header.stamp`** (capture time, not publish
   time) and a **correct `header.frame_id`** that exists in the URDF (Section 2).
4. Wrapped in a **lifecycle node** where the driver supports it, so bringup can
   order activation (`references/lifecycle-components.md`).
5. Publishes **diagnostics** for rate and timestamp health (Section 6).

### USB camera (V4L2) — `usb_cam`

```bash
sudo apt install ros-${ROS_DISTRO}-usb-cam
```

```yaml
# config/camera_front.yaml
camera_front:
  ros__parameters:
    video_device: /dev/camera_front    # udev symlink, never /dev/video0
    pixel_format: mjpeg2rgb            # MJPEG off the wire, RGB out — 30 fps at 1080p
    image_width: 1920
    image_height: 1080
    framerate: 30.0
    camera_frame_id: camera_front_optical_frame   # must match the URDF (Section 2)
    # Intrinsics from `camera_calibration` (perception.md §5). Without this file the
    # driver publishes zeroed CameraInfo and everything downstream silently degrades.
    camera_info_url: package://my_robot_bringup/config/camera_front_info.yaml
```

USB bandwidth is the classic multi-camera failure: two uncompressed 1080p30 streams
exceed one USB 3 root hub. Prefer MJPEG on the wire, and check allocation with
`lsusb -t` — put each camera on its own host controller where possible. RealSense and
other depth cameras have a dedicated pipeline in `references/perception.md` §4.

### 2D LiDAR (serial) — `sllidar_ros2`

```yaml
sllidar_node:
  ros__parameters:
    serial_port: /dev/lidar
    serial_baudrate: 256000
    frame_id: lidar_link
    angle_compensate: true
    scan_mode: Standard
```

Serial 2D LiDARs saturate a UART: if `scan` rate drops below spec, check baud rate
first, CPU governor second (`references/realtime.md`).

### 3D LiDAR (Ethernet) — Velodyne / Ouster pattern

Ethernet LiDARs need network plumbing before ROS is involved:

```bash
# Dedicated NIC with a static address on the sensor's subnet — never DHCP on a robot
sudo ip addr add 192.168.1.10/24 dev eth1
ping 192.168.1.201          # sensor reachable?
sudo tcpdump -i eth1 udp port 2368 -c 3   # packets actually arriving?
```

```yaml
# Velodyne VLP-16 — three-stage pipeline: driver → transform → (optional) laserscan
velodyne_driver_node:
  ros__parameters:
    device_ip: 192.168.1.201
    model: VLP16
    rpm: 600.0
    frame_id: velodyne
velodyne_transform_node:
  ros__parameters:
    model: VLP16
    calibration: /opt/ros/${ROS_DISTRO}/share/velodyne_pointcloud/params/VLP16db.yaml
    fixed_frame: ''            # leave empty unless de-skewing against odom (Section 3)
```

The Ouster driver (`ros-${ROS_DISTRO}-ouster-ros`, Humble+) is a **lifecycle node** —
drive it through `configure`/`activate` in bringup rather than expecting data on
process start, and read its metadata service for the exact beam intrinsics.

Point cloud QoS at the driver is `qos_profile_sensor_data`; a 3D LiDAR at 10 Hz ×
~2 MB per cloud will exhaust a RELIABLE history queue the first time RViz runs over
Wi-Fi. Large-message DDS tuning (fragment size, buffer sizes) is in
`references/communication.md`.

## 2. Sensor mounting frames

### Every sensor gets a URDF link

Data is only as good as the transform describing where the sensor sits. Model the
mount in the URDF/xacro (full tf2/URDF treatment in `references/tf2-urdf.md`):

```xml
<!-- urdf/sensors.xacro -->
<joint name="lidar_mount_joint" type="fixed">
  <parent link="base_link"/>
  <child link="lidar_link"/>
  <!-- Measured from CAD or calibrated (Section 5). REP 103: x forward, y left, z up -->
  <origin xyz="0.18 0.0 0.32" rpy="0 0 0"/>
</joint>
<link name="lidar_link"/>

<joint name="camera_mount_joint" type="fixed">
  <parent link="base_link"/>
  <child link="camera_front_link"/>
  <origin xyz="0.22 0.0 0.25" rpy="0 0 0"/>
</joint>
<link name="camera_front_link"/>

<!-- The optical frame: z forward, x right, y down (image conventions).
     This fixed rotation is the same for every camera — model it once here
     and NEVER bake it into calibration numbers. -->
<joint name="camera_front_optical_joint" type="fixed">
  <parent link="camera_front_link"/>
  <child link="camera_front_optical_frame"/>
  <origin xyz="0 0 0" rpy="${-pi/2} 0 ${-pi/2}"/>
</joint>
<link name="camera_front_optical_frame"/>
```

Rules that prevent months of confusion:

- **Body frame vs optical frame.** `camera_front_link` follows REP 103 (x forward,
  z up); `camera_front_optical_frame` follows the image convention (z forward,
  x right, y down). The driver's `frame_id` must be the **optical** frame for image
  topics. Mixing them makes projected points appear rotated 90°/upside-down.
- `robot_state_publisher` broadcasts these fixed joints on `/tf_static` — do not also
  run `static_transform_publisher` for the same frames (duplicate-parent tf errors).
- For a quick experiment without touching the URDF (Humble+ flag syntax; positional
  args removed in Jazzy):

```bash
ros2 run tf2_ros static_transform_publisher \
  --x 0.22 --y 0 --z 0.25 --roll 0 --pitch 0 --yaw 0 \
  --frame-id base_link --child-frame-id camera_front_link
```

## 3. Time synchronization

Fusion quality is bounded by timestamp quality. A robot moving at 1 m/s with 50 ms of
clock error smears every fused point by 5 cm — more than most calibrations are worth.

### Three clocks, three problems

| Clock | Problem | Tool |
|---|---|---|
| Robot OS clock vs world | Logs/fleet data misaligned; tf breaks on NTP step at boot | chrony (+ `time-sync.target` gating, `system-bringup.md` §3) |
| Sensor internal clock vs robot OS | LiDAR stamps drift ms/hour against the host | PTP (IEEE 1588 / gPTP) or driver host-stamping |
| Multi-machine robots | tf from machine A, sensor from machine B | PTP between machines; one machine is grandmaster |

### chrony — good to ~1 ms on a LAN

```bash
sudo apt install chrony
# /etc/chrony/chrony.conf on the robot:
#   server <fleet-server-or-router> iburst
#   makestep 1.0 3        # step only during the first 3 updates (boot), slew after
chronyc tracking          # "System time" offset should be < 1 ms once locked
```

`makestep` matters on robots: stepping the clock while nodes run breaks tf history and
rosbag ordering — allow steps only at boot, before bringup (gate with
`time-sync.target`).

### PTP — required for hardware-stamping LiDARs

Ethernet LiDARs (Ouster, newer Velodyne/Hesai) can timestamp *inside the sensor* with
sub-microsecond accuracy — but only if the host disciplines them over PTP:

```bash
sudo apt install linuxptp
ethtool -T eth1 | grep -i hardware    # NIC must list hardware-transmit/receive stamping

# Host as PTP master toward the sensor segment:
sudo ptp4l -i eth1 -m &               # gPTP profile for Ouster: add -f /etc/linuxptp/gPTP.cfg
sudo phc2sys -c eth1 -s CLOCK_REALTIME -O 0 -m &   # push system time into the NIC's PHC
# Then set the sensor's timestamp mode to PTP (e.g. Ouster: TIME_FROM_PTP_1588).
```

If PTP is not available, configure the driver to stamp with **host receive time**
minus a fixed transport latency — worse than PTP, but *consistent*. The failure mode
to avoid is the default on several drivers: sensor-internal time with no
synchronization, which drifts unboundedly.

### Measure before trusting

```bash
# Difference between header.stamp and arrival time — the end-to-end stamp latency
ros2 topic delay /points          # steadily growing => unsynchronized sensor clock
ros2 topic delay /camera/image_raw

ros2 topic hz /points             # rate sanity — pair with the diagnostics in §6
```

For camera+LiDAR fusion, also check *relative* skew: subscribe to both, compare
`header.stamp` for physically simultaneous events (wave a hand in front of both).
Persistent relative offset ⇒ fix clocks or set a `time_offset` parameter where the
driver provides one; jittery offset ⇒ use ApproximateTime sync with a realistic slop
(`references/perception.md` §7). Hardware triggering (camera exposure fired by the
LiDAR's sync output or an external PPS) removes the jitter entirely and is worth the
wiring on any robot doing tight fusion.

### De-skewing spinning LiDARs

A 10 Hz spinning LiDAR smears each cloud across 100 ms — at 1 m/s that is 10 cm of
motion inside one scan. Drivers publishing per-point timestamps enable motion
de-skewing: set the Velodyne transform node's `fixed_frame` to `odom` to correct
per-point using tf, or enable the equivalent driver option. SLAM stacks
(`slam_toolbox`, LIO pipelines) expect de-skewed clouds or per-point stamps —
check which before feeding them.

## 4. tf2-synchronized processing

### The problem MessageFilter solves

A callback that immediately calls `lookup_transform` on sensor data races tf: the
transform for `header.stamp` typically arrives a few ms *after* the data. The naive
fixes are both wrong:

```cpp
// BAD — races tf; throws ExtrapolationException a few times per second
void cloud_cb(sensor_msgs::msg::PointCloud2::ConstSharedPtr msg) {
  auto tf = tf_buffer_->lookupTransform("map", msg->header.frame_id,
                                        msg->header.stamp);   // often not there YET
}

// BAD — lookup with Time(0) "fixes" the exception by silently using the wrong
// transform: latest pose + old data = points dragged through space while turning
auto tf = tf_buffer_->lookupTransform("map", msg->header.frame_id, tf2::TimePointZero);
```

`tf2_ros::MessageFilter` buffers each message until the transform *at its stamp* is
available, then delivers it — no race, no wrong-time lookup:

```cpp
// cloud_to_map_node.cpp — queue clouds until their transform into map exists
#include <chrono>
#include <memory>

#include <message_filters/subscriber.h>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/create_timer_ros.h>
#include <tf2_ros/message_filter.h>
#include <tf2_ros/transform_listener.h>
#include <tf2_sensor_msgs/tf2_sensor_msgs.hpp>   // doTransform for PointCloud2

using sensor_msgs::msg::PointCloud2;

class CloudToMap : public rclcpp::Node
{
public:
  CloudToMap() : Node("cloud_to_map")
  {
    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(get_clock());
    // MessageFilter needs a timer interface on the buffer to run its timeout logic
    tf_buffer_->setCreateTimerInterface(
      std::make_shared<tf2_ros::CreateTimerROS>(
        get_node_base_interface(), get_node_timers_interface()));
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

    cloud_sub_.subscribe(this, "points", rmw_qos_profile_sensor_data);
    // Args: subscriber, buffer, target frame, queue size, node interfaces, timeout.
    // Queue must absorb (tf latency x message rate): 10 Hz clouds, ~200 ms worst-case
    // tf latency => a queue of 5 is the minimum that never drops.
    tf_filter_ = std::make_shared<tf2_ros::MessageFilter<PointCloud2>>(
      cloud_sub_, *tf_buffer_, "map", 5,
      get_node_logging_interface(), get_node_clock_interface(),
      std::chrono::milliseconds(500));
    tf_filter_->registerCallback(&CloudToMap::cloud_ready, this);

    map_cloud_pub_ = create_publisher<PointCloud2>("points_map", 10);
  }

private:
  void cloud_ready(PointCloud2::ConstSharedPtr msg)
  {
    // Guaranteed to succeed: the filter held the message until this transform existed
    auto tf = tf_buffer_->lookupTransform("map", msg->header.frame_id,
                                          msg->header.stamp);
    PointCloud2 out;
    tf2::doTransform(*msg, out, tf);
    map_cloud_pub_->publish(out);
  }

  message_filters::Subscriber<PointCloud2> cloud_sub_;
  std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
  std::shared_ptr<tf2_ros::MessageFilter<PointCloud2>> tf_filter_;
  rclcpp::Publisher<PointCloud2>::SharedPtr map_cloud_pub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<CloudToMap>());
  rclcpp::shutdown();
  return 0;
}
```

Tuning notes:

- **Queue size** trades memory for tolerance to tf latency. Watch the filter's log
  ("discarding message because the queue is full") — that is data loss, size up.
- The **timeout** bounds how long a message waits before being dropped; messages
  dropped on timeout mean tf for that time never arrived — a frame is missing or the
  clock is wrong (Section 3), not a filter problem.
- Python equivalent: `tf2_ros.MessageFilter(sub, buffer, 'map', 5, node)` over a
  `message_filters.Subscriber` — same semantics.
- To synchronize **two sensors AND tf** (camera + LiDAR into a common frame), chain
  an ApproximateTime synchronizer (`references/perception.md` §7) *into* a
  MessageFilter: sync the pair first, then gate the synced callback's target frame.

## 5. LiDAR-camera extrinsic calibration

Intrinsic calibration (`camera_calibration`, checkerboards) is in
`references/perception.md` §5. The extrinsic problem is different: find the 6-DoF
transform `camera_optical_frame ← lidar_link` so LiDAR points project onto the right
pixels.

### Procedure

1. **Start from CAD.** The URDF mounting values (Section 2) are the initial guess;
   calibration refines the last few degrees/centimeters.
2. **Capture pairs.** A calibration target visible to both sensors — a checkerboard
   with a rigid border works: corners for the camera, the board plane/edges for the
   LiDAR. 15–30 poses covering the shared field of view, near and far.
3. **Solve.** Estimate the target plane in each sensor frame per pose, then solve the
   rigid transform aligning the plane sets (SVD/least-squares, or an off-the-shelf
   target-based calibration tool). Record the result as `x y z roll pitch yaw`.
4. **Write it back to the URDF** — the calibrated joint origin replaces the CAD guess.
   Do not leave calibration output in a side YAML that fights the URDF: one source of
   truth for every transform.

```xml
<!-- After calibration: lidar→camera chain now reflects reality -->
<joint name="camera_front_mount_joint" type="fixed">
  <parent link="base_link"/>
  <child link="camera_front_link"/>
  <origin xyz="0.2212 -0.0031 0.2489" rpy="0.0021 0.0187 -0.0043"/>
</joint>
```

### Verify by re-projection

The acceptance test is visual and quantitative — project the cloud into the image:

```python
# verify_extrinsics.py — overlay LiDAR points on the camera image
import cv2
import numpy as np

def project_cloud(points_lidar, T_cam_lidar, K, D, image):
    """points_lidar: Nx3 in lidar_link; T_cam_lidar: 4x4 optical<-lidar from tf2."""
    pts_h = np.hstack([points_lidar, np.ones((len(points_lidar), 1))])
    pts_cam = (T_cam_lidar @ pts_h.T).T[:, :3]
    pts_cam = pts_cam[pts_cam[:, 2] > 0.1]          # keep points in front of the camera
    pixels, _ = cv2.projectPoints(pts_cam, np.zeros(3), np.zeros(3), K, D)
    depths = pts_cam[:, 2]
    for (u, v), d in zip(pixels.reshape(-1, 2).astype(int), depths):
        if 0 <= u < image.shape[1] and 0 <= v < image.shape[0]:
            color = (0, int(max(0, 255 - d * 25)), int(min(255, d * 25)))
            cv2.circle(image, (u, v), 1, color, -1)
    return image
```

Judge it on structure: edges of doorframes, poles, and boxes must line up between the
colored points and the pixels — check at both 1 m and 10 m (a rotation error grows
with range; a translation error dominates up close). If alignment is perfect on a
static scene but smears when moving, the extrinsics are fine and the *time*
synchronization is not (Section 3).

## 6. Sensor diagnostics

Every driver should self-report rate and timestamp health so the aggregator
(`references/system-bringup.md` §5) and fleet monitoring see sensor degradation
before the operator does.

```cpp
// Inside a driver or a thin monitor node next to it
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <diagnostic_updater/publisher.hpp>

class LidarMonitor : public rclcpp::Node
{
public:
  LidarMonitor()
  : Node("lidar_monitor"),
    updater_(this),
    // Alarm outside 8–12 Hz (10 Hz nominal), and if header.stamp is more than
    // 100 ms behind now or 10 ms ahead (clock skew — see Section 3).
    freq_param_(&min_freq_, &max_freq_, /*tolerance=*/0.1, /*window=*/10),
    stamp_param_(/*min_acceptable=*/-0.010, /*max_acceptable=*/0.100),
    diagnosed_("scan", updater_, freq_param_, stamp_param_)
  {
    updater_.setHardwareID("lidar-front");
    scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
      "scan", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::LaserScan::ConstSharedPtr msg) {
        diagnosed_.tick(msg->header.stamp);   // feeds both frequency and stamp checks
      });
  }

private:
  double min_freq_{8.0}, max_freq_{12.0};
  diagnostic_updater::Updater updater_;
  diagnostic_updater::FrequencyStatusParam freq_param_;
  diagnostic_updater::TimeStampStatusParam stamp_param_;
  diagnostic_updater::HeaderlessTopicDiagnostic diagnosed_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
};
```

Wire the outputs into `diagnostic_aggregator` analyzers (`contains: ['lidar']`) so a
slow sensor turns the robot's health tree WARN/ERROR — and gate autonomy on that tree,
not on "the driver process exists."

## 7. Common failures and fixes

| Symptom | Why it happens | Fix |
|---|---|---|
| Camera images drop when a second subscriber joins | RELIABLE QoS on a bandwidth-bound stream — retransmissions stall the driver | `SensorDataQoS` (BEST_EFFORT) at the driver; compressed transport for remote viewers (`perception.md` §1) |
| Projected points rotated 90° / upside-down | Image `frame_id` set to the body frame instead of the optical frame | Publish images in `*_optical_frame`; keep the fixed body→optical rotation in the URDF only (Section 2) |
| `ExtrapolationException` a few times per second | Callback looks up tf at `header.stamp` before that transform arrives | `tf2_ros::MessageFilter` with adequate queue size (Section 4) |
| Points drag through space during turns | Lookup at `Time(0)` (latest) instead of the data's stamp | Look up at `msg->header.stamp` via MessageFilter; never "fix" extrapolation with Time(0) (Section 4) |
| `ros2 topic delay` grows without bound | Sensor stamps from its own unsynchronized clock | PTP-discipline the sensor, or switch the driver to host-time stamping (Section 3) |
| Fusion good when still, smeared when moving | Camera/LiDAR relative timestamp skew, or no de-skew on the spinning LiDAR | Fix clock sync / hardware trigger; enable per-point de-skew with `fixed_frame: odom` (Section 3) |
| Extrinsics perfect at 1 m, off at 10 m | Rotation error in the calibration (grows with range) | Re-calibrate with far-field target poses; verify re-projection at multiple ranges (Section 5) |
| Two nodes fight over the camera transform | URDF fixed joint AND a `static_transform_publisher` for the same child frame | One source of truth: the URDF via `robot_state_publisher` (Section 2) |
| Ethernet LiDAR silent after reboot | NIC got a DHCP address on the wrong subnet, or driver started before the link | Static IP on a dedicated NIC; order the driver after the device/network (`system-bringup.md` §3) |
| `CameraInfo` all zeros downstream | `camera_info_url` missing or file not found (driver logs a warning once, then runs) | Point `camera_info_url` at the calibration YAML; alarm on zeroed K in a sanity check |
| Point clouds arrive at 2 Hz instead of 10 Hz over Wi-Fi | Cloud size × RELIABLE retransmit over lossy link | BEST_EFFORT for live viewing, record on-robot; DDS fragment tuning in `communication.md` |
| `static_transform_publisher` args rejected | Old positional syntax removed | Use flag syntax `--x … --frame-id …` (Humble+ supports it; Jazzy+ requires it) |

For disputed calibration thresholds, independent observations, and metrics that
must not be substituted, see `references/evidence-progression.md` section 6.
