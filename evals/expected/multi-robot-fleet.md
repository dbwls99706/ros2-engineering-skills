# Expected: Multi-Robot Fleet Architecture

## Key Elements

### Discovery Isolation
- Option A: Separate `ROS_DOMAIN_ID` per robot (simple but limits cross-robot comms)
- Option B: Same domain with DDS partitions or topic namespacing (preferred for fleet manager)
- Recommend Zenoh router for Kilted+ deployments (better scalability)

### Namespace Strategy
- Each robot under `/<robot_name>/` namespace (e.g., `/robot_01/cmd_vel`)
- Fleet manager in root namespace, subscribing to namespaced topics
- Use `PushRosNamespace` in launch files

### TF Frame Conventions
- Per-robot frames: `<robot_name>/base_link`, `<robot_name>/odom`
- Common frame: `map` (shared) or `<robot_name>/map` (isolated)
- Use `frame_prefix` parameter in robot_state_publisher

### Fleet Manager Pattern
- Single node subscribing to `/<robot_name>/status` for each robot
- Action server for task assignment
- Heartbeat monitoring with DEADLINE QoS

### DDS Tuning
- Increase `max_participants` for multi-robot discovery
- Consider `ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET`
- Tune `SPDP/SEDP` discovery intervals for fleet size
