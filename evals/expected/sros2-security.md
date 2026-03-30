# Expected: SROS2 Security Configuration

## Key Elements

### Keystore & Enclave Setup
- `ros2 security create_keystore <path>`
- `ros2 security create_enclave <path> /arm_controller`
- `ros2 security create_enclave <path> /camera_driver`
- `ros2 security create_enclave <path> /safety_monitor`

### Environment Variables
- `ROS_SECURITY_KEYSTORE` pointing to keystore path
- `ROS_SECURITY_ENABLE=true`
- `ROS_SECURITY_STRATEGY=Enforce` (not Permissive for production)

### Policy XML
- `/safety_monitor` enclave has `<allow_subscribe>` for topics but `<deny_publish>` for all except `/e_stop`
- Each node has its own enclave with least-privilege access

### DDS Security Plugins
- Authentication plugin: `dds.sec.auth.builtin.PKI-DH`
- Access control plugin: `dds.sec.access.builtin.Access-Permissions`
- Crypto plugin: `dds.sec.crypto.builtin.AES-GCM-GMAC`

### Launch Integration
- `additional_env` or `enclave` parameter in Node action
- Use `FindPackageShare` for keystore path (no hardcoded paths)
