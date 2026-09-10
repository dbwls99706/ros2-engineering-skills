# Security

## Table of contents

1. Threat model for ROS 2 systems
2. SROS2 workflow
3. DDS security plugins
4. Certificate and key management
5. Permissions and governance authoring
6. Supply chain hardening
7. Network security and VPN alternatives
8. Performance impact of encryption
9. Development vs production workflow
10. Common failures and fixes

---

## 1. Threat model for ROS 2 systems

ROS 2 DDS communication is **unencrypted by default**. Any device on the same network and DDS domain can publish, subscribe, call services, and invoke actions without authentication. This is acceptable during development but unacceptable for production deployment.

### Attack surface overview

| Attack vector | Impact | Mitigation |
|---|---|---|
| Unencrypted DDS traffic | Eavesdropping on sensor data, commands | Enable DDS security (SROS2) or VPN |
| Unauthorized topic publishing | Spoofed sensor data, rogue commands | DDS permissions grants, with governance enabling access control |
| Malicious node joining | Unauthorized participant gains access | DDS Security PKI-DH authentication and join access control |
| DDS discovery sniffing | Map entire robot architecture | Encrypt discovery with SROS2 |
| Docker image tampering | Supply chain attack | Image signing, pinned base images |
| Rosdep/pip dependency confusion | Malicious package injection | Use official repos, verify checksums |
| Physical access to robot | Firmware tampering, key extraction | Secure boot, encrypted storage, HSM |
| Parameter service abuse | Alter node behavior at runtime | Restrict parameter services in permissions.xml |

DDS authentication uses the PKI-DH plugin with participant certificates; see the
[ROS 2 DDS-Security design](https://design.ros2.org/articles/ros2_dds_security.html).

### Why DDS domain isolation is not security

```bash
# Domain isolation separates DDS discovery/traffic; it is NOT authentication
# The DDS-derived ROS 2 range is 0 through 232 inclusive (233 values)
export ROS_DOMAIN_ID=42  # Does NOT prevent eavesdropping or spoofing
```

The usable range is further constrained by platform ephemeral-port settings.
See the [ROS 2 Domain ID guidance](https://github.com/ros2/ros2_documentation/blob/humble/source/Concepts/Intermediate/About-Domain-ID.rst).

---

## 2. SROS2 workflow

### Step 1: Generate keystore and enclaves

```bash
# Install SROS2 if not already present
sudo apt install ros-${ROS_DISTRO}-sros2

# Create SROS2 keystore
ros2 security create_keystore ~/sros2_keystore

# Create one enclave for each participant/context boundary in this deployment.
# Matching a one-node-per-process enclave to the node FQN is only a convention.
ros2 security create_enclave ~/sros2_keystore /my_robot/driver
ros2 security create_enclave ~/sros2_keystore /my_robot/planner
ros2 security create_enclave ~/sros2_keystore /my_robot/controller
```

DDS security files belong to a DomainParticipant, which maps to a ROS context in
a process. Separately launched one-node processes can use distinct enclaves as
above. Composed nodes in one component container normally share its context,
participant, and enclave unless the application deliberately separates them.
Choose enclave boundaries from the required privilege separation; the enclave
path does not have to equal a node name.

### Step 2: Generate policy from runtime introspection

```bash
# Option A: Auto-generate from running system (run system WITHOUT security first).
# IMPORTANT: This captures only currently-active communications. Run it while
# the system exercises ALL code paths (all topics, services, actions in use).
# Ephemeral communications (service calls during startup) are easily missed.
ros2 security generate_policy policy.xml

# Option B: Write policy manually (recommended for production — more precise)
# See Section 5 for detailed policy authoring
```

### Step 3: Create permissions from policy

```bash
ros2 security create_permission ~/sros2_keystore /my_robot/driver policy.xml
ros2 security create_permission ~/sros2_keystore /my_robot/planner policy.xml
ros2 security create_permission ~/sros2_keystore /my_robot/controller policy.xml
```

### Step 4: Configure environment

```bash
export ROS_SECURITY_KEYSTORE=~/sros2_keystore
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce
```

### Step 5: Launch secured system

```python
# launch/secured_robot.launch.py
from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node
import os

def generate_launch_description():
    keystore_path = os.path.expanduser('~/sros2_keystore')

    return LaunchDescription([
        SetEnvironmentVariable('ROS_SECURITY_KEYSTORE', keystore_path),
        SetEnvironmentVariable('ROS_SECURITY_ENABLE', 'true'),
        SetEnvironmentVariable('ROS_SECURITY_STRATEGY', 'Enforce'),

        Node(
            package='my_robot_driver',
            executable='driver_node',
            name='driver',
            namespace='my_robot',
            additional_env={'ROS_SECURITY_ENCLAVE_OVERRIDE': '/my_robot/driver'},
        ),
        Node(
            package='my_robot_planner',
            executable='planner_node',
            name='planner',
            namespace='my_robot',
            additional_env={'ROS_SECURITY_ENCLAVE_OVERRIDE': '/my_robot/planner'},
        ),
    ])
```

### Step 6: Monitor and rotate

```bash
# Check certificate expiry
openssl x509 -in ~/sros2_keystore/enclaves/my_robot/driver/cert.pem \
  -noout -enddate

# Check all certificate expiry dates across the keystore
find ~/sros2_keystore -name "cert.pem" -exec sh -c \
  'echo "=== {} ===" && openssl x509 -in {} -noout -enddate -subject' \;
```

Current Humble and Jazzy SROS2 releases generate certificates with 3,650-day
(approximately 10-year) validity. This is an implementation default, not a
deployment policy: inspect the installed version and rotate on a shorter,
explicit production schedule. See the
[SROS2 certificate builder](https://github.com/ros2/sros2/blob/jazzy/sros2/sros2/_utilities.py).

---

## 3. DDS security plugins

The OMG DDS Security specification defines five plugin interfaces. ROS 2 uses
the Authentication, Access Control, and Cryptographic interfaces.

### Authentication (DDS:Auth:PKI-DH)

`DDS:Auth:PKI-DH` performs mutual participant authentication using X.509
certificates and a PKI-DH handshake. Each participant proves its identity and
validates the peer's certificate chain against its configured identity CA. This
is DDS Security authentication, not TLS.

### Access control (DDS:Access:Permissions)

Controls which DDS operations each participant/enclave identity may perform.
ROS topics, services, and actions are compiled into the corresponding DDS topic
permissions. The two signed inputs are:

- **governance.xml** -- domain-level rules (what protections are enabled)
- **permissions.xml** -- per-participant/enclave rules (what that identity can access)

Both files are signed by the permissions CA and distributed as `.p7s` (PKCS#7) files.

### Cryptographic (DDS:Crypto:AES-GCM-GMAC)

| Protection kind | Confidentiality | Integrity | Use case |
|---|---|---|---|
| `NONE` | No | No | Development only |
| `SIGN` | No | Yes | High-bandwidth sensor data (verify origin, skip encryption overhead) |
| `ENCRYPT` | Yes | Yes | Commands, credentials, sensitive data |

### How SROS2 configures the plugins

The RMW reads `ROS_SECURITY_KEYSTORE` and `ROS_SECURITY_ENCLAVE_OVERRIDE` to locate all required files automatically. The enclave directory contains: `cert.pem`, `key.pem`, `governance.p7s`, `permissions.p7s`, `identity_ca.cert.pem`, `permissions_ca.cert.pem`. No manual DDS XML configuration is needed when using SROS2 environment variables.

---

## 4. Certificate and key management

### Keystore directory structure

```text
~/sros2_keystore/
+-- enclaves/
|   +-- my_robot/
|       +-- driver/
|       |   +-- cert.pem                # Enclave/participant certificate
|       |   +-- key.pem                 # Enclave private key (protect!)
|       |   +-- governance.p7s          # Signed governance
|       |   +-- permissions.p7s         # Signed permissions
|       |   +-- permissions.xml         # Human-readable permissions
|       |   +-- identity_ca.cert.pem
|       |   +-- permissions_ca.cert.pem
|       +-- planner/
|           +-- ...
+-- private/
|   +-- ca.key.pem     # CA private key — NEVER distribute this
+-- public/
    +-- ca.cert.pem
    +-- identity_ca.cert.pem
    +-- permissions_ca.cert.pem
```

### CA certificate vs enclave certificates

```bash
# WRONG — storing CA key on the robot
scp ~/sros2_keystore/private/ca.key.pem robot@192.168.1.100:/opt/robot/keystore/

# CORRECT — distribute the participant enclave cert + key, not the CA key
scp -r ~/sros2_keystore/enclaves/my_robot/driver/ \
  robot@192.168.1.100:/opt/robot/keystore/enclaves/my_robot/driver/
```

The CA private key should remain on a secure build server or HSM. Deploy only
the enclave directories required by each runtime participant.

### Certificate rotation at fleet scale

`create_enclave` is idempotent: if both `cert.pem` and `key.pem` already exist,
it leaves them unchanged. Issue replacements in a staging copy, prove that the
certificate changed, and deploy only after every staged enclave validates.

```bash
#!/bin/bash
# scripts/rotate_certs.sh: stage, verify, and distribute replacement certificates
set -euo pipefail

KEYSTORE="$HOME/sros2_keystore"
POLICY="$HOME/fleet_policy.xml"
ROBOTS_FILE="$HOME/fleet_robots.txt"  # one hostname per line
STAGING="$(mktemp -d)"
trap 'rm -rf -- "$STAGING"' EXIT

# Preserve the CAs and governance while replacing enclave identities off-line.
cp -a "$KEYSTORE/." "$STAGING/"

# Remove only the staged identity, then create and sign a replacement.
while IFS= read -r enclave; do
    live="$KEYSTORE/enclaves${enclave}"
    staged="$STAGING/enclaves${enclave}"
    old_serial="$(openssl x509 -in "$live/cert.pem" -noout -serial)"
    old_end="$(openssl x509 -in "$live/cert.pem" -noout -enddate)"

    rm -f -- "$staged/cert.pem" "$staged/key.pem"
    ros2 security create_enclave "$STAGING" "$enclave"
    ros2 security create_permission "$STAGING" "$enclave" "$POLICY"

    new_serial="$(openssl x509 -in "$staged/cert.pem" -noout -serial)"
    new_end="$(openssl x509 -in "$staged/cert.pem" -noout -enddate)"
    test "$new_serial" != "$old_serial"
    test "$new_end" != "$old_end"
    openssl verify -CAfile "$STAGING/public/identity_ca.cert.pem" \
      "$staged/cert.pem"
done < <(find "$KEYSTORE/enclaves" -name "cert.pem" -exec dirname {} \; \
         | sed "s|$KEYSTORE/enclaves||")

# Distribute only after the complete staged set passes validation.
while IFS= read -r robot; do
    rsync -avz --delete "$STAGING/enclaves/" \
      "robot@${robot}:/opt/robot/keystore/enclaves/"
    ssh "robot@${robot}" 'sudo systemctl restart ros2-robot'
    # Require the application's ROS health and command-path checks here before
    # continuing to the next robot; service-active alone is not readiness proof.
done < "$ROBOTS_FILE"

# Keep the signing keystore aligned only after the fleet rollout succeeds.
rsync -a --delete "$STAGING/" "$KEYSTORE/"
```

This keeps the CA identities stable. Rotating a CA is a separate trust-migration
operation and requires an overlap plan for every participant. The source behavior
is defined in the
[SROS2 enclave implementation](https://github.com/ros2/sros2/blob/jazzy/sros2/sros2/keystore/_enclave.py).

### HSM integration

For production, store the CA key in a hardware security module. SROS2 does not natively support HSM -- wrap OpenSSL signing operations with your HSM provider's PKCS#11 engine:

```bash
# Generate CA key inside HSM (key never leaves hardware)
pkcs11-tool --module /usr/lib/softhsm/libsofthsm2.so \
  --login --pin 5678 --keypairgen --key-type rsa:4096 \
  --label "ros2-ca-key"

# Sign certificates using the HSM-stored key
openssl req -engine pkcs11 -keyform engine \
  -key "pkcs11:token=ros2-ca;object=ros2-ca-key;type=private" \
  -new -x509 -days 365 -out ca.cert.pem \
  -subj "/CN=ROS2 Security CA"
```

---

## 5. Permissions and governance authoring

### Governance XML

```xml
<?xml version="1.0" encoding="UTF-8"?>
<dds xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:noNamespaceSchemaLocation="http://www.omg.org/spec/DDS-SECURITY/20170901/omg_shared_ca_governance.xsd">
  <domain_access_rules>
    <domain_rule>
      <domains><id>0</id></domains>
      <allow_unauthenticated_participants>false</allow_unauthenticated_participants>
      <enable_join_access_control>true</enable_join_access_control>
      <discovery_protection_kind>ENCRYPT</discovery_protection_kind>
      <liveliness_protection_kind>ENCRYPT</liveliness_protection_kind>
      <rtps_protection_kind>ENCRYPT</rtps_protection_kind>
      <topic_access_rules>
        <!-- Use SIGN only when the threat model permits sensor-data disclosure -->
        <topic_rule>
          <topic_expression>rt/camera/*</topic_expression>
          <enable_discovery_protection>true</enable_discovery_protection>
          <enable_liveliness_protection>true</enable_liveliness_protection>
          <enable_read_access_control>true</enable_read_access_control>
          <enable_write_access_control>true</enable_write_access_control>
          <metadata_protection_kind>SIGN</metadata_protection_kind>
          <data_protection_kind>SIGN</data_protection_kind>
        </topic_rule>
        <!-- All other topics: full encryption -->
        <topic_rule>
          <topic_expression>*</topic_expression>
          <enable_discovery_protection>true</enable_discovery_protection>
          <enable_liveliness_protection>true</enable_liveliness_protection>
          <enable_read_access_control>true</enable_read_access_control>
          <enable_write_access_control>true</enable_write_access_control>
          <metadata_protection_kind>ENCRYPT</metadata_protection_kind>
          <data_protection_kind>ENCRYPT</data_protection_kind>
        </topic_rule>
      </topic_access_rules>
    </domain_rule>
  </domain_access_rules>
</dds>
```

### Permissions XML: per-participant/enclave access control

```xml
<?xml version="1.0" encoding="UTF-8"?>
<dds xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:noNamespaceSchemaLocation="http://www.omg.org/spec/DDS-SECURITY/20170901/omg_shared_ca_permissions.xsd">
  <permissions>
    <grant name="/my_robot/driver">
      <subject_name>CN=/my_robot/driver</subject_name>
      <validity>
        <not_before>2025-01-01T00:00:00</not_before>
        <not_after>2028-01-01T00:00:00</not_after>
      </validity>

      <!-- Topics this enclave identity can publish -->
      <allow_rule>
        <domains><id>0</id></domains>
        <publish>
          <topics>
            <topic>rt/joint_states</topic>
            <topic>rt/diagnostics</topic>
            <topic>rt/rosout</topic>
          </topics>
        </publish>
      </allow_rule>

      <!-- Topics this enclave identity can subscribe to -->
      <allow_rule>
        <domains><id>0</id></domains>
        <subscribe>
          <topics>
            <topic>rt/joint_commands</topic>
            <topic>rt/parameter_events</topic>
          </topics>
        </subscribe>
      </allow_rule>

      <!-- Service request/reply topics (required for services to work) -->
      <allow_rule>
        <domains><id>0</id></domains>
        <publish>
          <topics>
            <topic>rr/my_robot/driver/get_parametersReply</topic>
            <topic>rr/my_robot/driver/set_parametersReply</topic>
          </topics>
        </publish>
        <subscribe>
          <topics>
            <topic>rq/my_robot/driver/get_parametersRequest</topic>
            <topic>rq/my_robot/driver/set_parametersRequest</topic>
          </topics>
        </subscribe>
      </allow_rule>

      <!-- Deny everything else -->
      <deny_rule>
        <domains><id>0</id></domains>
        <publish><topics><topic>*</topic></topics></publish>
        <subscribe><topics><topic>*</topic></topics></subscribe>
      </deny_rule>
      <default>DENY</default>
    </grant>
  </permissions>
</dds>
```

### Topic name prefixes in permissions

DDS topic names differ from ROS 2 topic names:

| ROS 2 entity | DDS topic prefix | Example |
|---|---|---|
| Topic `/foo` | `rt/foo` | `rt/joint_states` |
| Service request `/bar` | `rq/barRequest` | `rq/my_node/get_parametersRequest` |
| Service reply `/bar` | `rr/barReply` | `rr/my_node/get_parametersReply` |
| Action topics | `rt/.../_action/...` | `rt/navigate/_action/feedback` |
| Parameter events | `rt/parameter_events` | `rt/parameter_events` |

### Signing and validating policy files

```bash
# Sign governance after manual edits with the permissions CA
openssl smime -sign -text \
  -in governance.xml -out governance.p7s \
  -signer ~/sros2_keystore/public/permissions_ca.cert.pem \
  -inkey ~/sros2_keystore/private/permissions_ca.key.pem

# Sign permissions with the same permissions CA
openssl smime -sign -text \
  -in permissions.xml -out permissions.p7s \
  -signer ~/sros2_keystore/public/permissions_ca.cert.pem \
  -inkey ~/sros2_keystore/private/permissions_ca.key.pem

# Locate and validate against the schemas shipped by the installed SROS2
SCHEMA_DIR="$(python3 -c \
  'from sros2.policy import get_transport_schema; print(get_transport_schema("dds", "governance.xsd").parent)')"
xmllint --schema "$SCHEMA_DIR/governance.xsd" governance.xml --noout
xmllint --schema "$SCHEMA_DIR/permissions.xsd" permissions.xml --noout
```

---

## 6. Supply chain hardening

### Pin Docker base images by digest

```dockerfile
# WRONG — tag can be overwritten with a compromised image
FROM ros:jazzy-ros-base AS base

# CORRECT — pin by SHA256 digest (immutable reference)
FROM ros:jazzy-ros-base@sha256:a1b2c3d4e5f6... AS base
```

### Minimal packages and verified dependencies

```dockerfile
# WRONG — installs unnecessary packages, larger attack surface
RUN apt-get update && apt-get install -y ros-jazzy-desktop

# CORRECT — install only what you need
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-ros-base \
    ros-jazzy-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
```

### Pin Python dependencies with hash verification

```bash
# Generate requirements with hashes
pip-compile --generate-hashes requirements.in -o requirements.txt

# Install with hash verification — rejects tampered packages
pip install --no-cache-dir --require-hashes -r requirements.txt
```

### Container image scanning

```bash
# Scan with Trivy — fail CI on critical vulnerabilities
trivy image --exit-code 1 --severity CRITICAL,HIGH my_robot:latest
```

```yaml
# GitHub Actions integration
- name: Scan for vulnerabilities
  uses: aquasecurity/trivy-action@0.28.0  # Pin to specific version
  with:
    image-ref: my_robot:${{ github.sha }}
    exit-code: 1
    severity: CRITICAL,HIGH
```

### ROS package source verification

```bash
# WRONG — adding untrusted third-party PPAs
sudo add-apt-repository ppa:random-user/ros-packages

# CORRECT — use only the official OSRF repositories
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
```

---

## 7. Network security and VPN alternatives

### Comparison of approaches

| Approach | Protection boundary | Discovery/routing consideration | Per-topic control |
|---|---|---|---|
| SROS2 (DDS Security) | DDS participant and topic operations | Retains the selected RMW transport | Yes |
| Routed VPN (for example, WireGuard) | Traffic crossing the tunnel | Discovery multicast is often not routed by default | No |
| SSH tunnel | Explicit forwarded connections | Does not transparently provide DDS discovery | No |
| Mesh overlay | Traffic admitted to the overlay | Multicast support and NAT traversal vary by product | No |

Measure latency and CPU cost on the target RMW, transport, message sizes, and
hardware. A product name alone does not determine the added latency.

### DDS multicast across routed VPNs

Many layer-3 VPN configurations do not forward DDS discovery multicast by
default. Verify the actual overlay and RMW behavior. When multicast discovery is
unavailable, configure supported unicast peers or a discovery server, for example:

```xml
<!-- cyclonedds_vpn.xml — unicast discovery for VPN -->
<CycloneDDS>
  <Domain>
    <General>
      <Interfaces>
        <NetworkInterface name="wg0" />
      </Interfaces>
    </General>
    <Discovery>
      <Peers>
        <Peer address="10.0.0.1" />
        <Peer address="10.0.0.2" />
      </Peers>
      <ParticipantIndex>auto</ParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
```

### Field robots behind NAT

For robots behind firewalls, NAT, or cellular connections, select an overlay or
router whose connectivity and identity model has been verified for the deployment.
When using Zenoh, configure its transport security explicitly:

```bash
# Tailscale: install on each robot, assigns 100.x.y.z addresses
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=robot-001
# Use Tailscale IPs in CycloneDDS peer list

# Zenoh transport security is separate from the DDS security stack
# Configure TLS in Zenoh router config for encrypted transport
```

---

## 8. Performance impact of encryption

### Measure the deployed path

There is no portable SROS2 latency or CPU multiplier. Results depend on the DDS
vendor and version, crypto implementation, CPU features, transport, payload size,
rate, topology, and protection kinds. Benchmark the unsecured baseline, `SIGN`,
and `ENCRYPT` on the deployed path. Record at least throughput, CPU load, missed
deadlines, and latency percentiles appropriate to the control budget. Do not copy
example numbers into an acceptance threshold.

### Selective encryption strategy

Use mixed protection levels in governance.xml to balance security and performance:

- **ENCRYPT** for commands (`cmd_vel`, `joint_commands`) and sensitive data.
- **SIGN** only when confidentiality is explicitly outside the threat model and
  target measurements show encryption would violate a real budget.
- **ENCRYPT** as the default when disclosure would be harmful.

See the governance.xml example in Section 5 for the full pattern.

### Hardware acceleration check

```bash
# Verify AES-NI is available (x86)
grep -o aes /proc/cpuinfo | head -1

# ARM crypto extensions (Raspberry Pi 4+, Jetson)
grep -o 'aes\|pmull\|sha' /proc/cpuinfo | sort -u

# OpenSSL benchmark to verify
openssl speed -evp aes-256-gcm
```

---

## 9. Development vs production workflow

### Three-stage security adoption

```text
Development          Testing              Production
+-----------+     +------------+     +---------------+
| No SROS2  |     | Enforce    |     | Enforce       |
| Fast iter | --> | Broad perms| --> | Minimal perms |
+-----------+     +------------+     +---------------+
```

**Development:** No security enabled. Use `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`
(Jazzy+) or `ROS_LOCALHOST_ONLY=1` (Humble) and domain IDs for isolation.

**Zenoh note (Kilted+):** SROS2/DDS Security plugins only apply when using a DDS-based
RMW (`rmw_cyclonedds_cpp`, `rmw_fastrtps_cpp`). When using `rmw_zenoh_cpp` (Tier 1
in Kilted), security is handled via Zenoh's own TLS configuration, NOT SROS2.
If you switch to Zenoh without configuring Zenoh TLS, your system is unprotected
even with `ROS_SECURITY_ENABLE=true`.

**Testing:** use `ROS_SECURITY_STRATEGY=Enforce` with the intended governance and
permissions on an isolated non-actuating system. Test both allowed and denied
operations. `Permissive` allows initialization to fall back to unsecured operation
when security artifacts are unavailable; it is not a log-only mode for access
control violations. Valid secured participants remain subject to DDS permissions.
See the [rcl security initialization path](https://github.com/ros2/rcl/blob/jazzy/rcl/src/rcl/security.c).

**Production:** `ROS_SECURITY_STRATEGY=Enforce` with hand-crafted minimal permissions (principle of least privilege).

### Common transition gotchas

```bash
# One-node-per-process example: include every participating process
ros2 security create_enclave ~/sros2_keystore /my_robot/driver
ros2 security create_enclave ~/sros2_keystore /my_robot/planner
ros2 security create_enclave ~/sros2_keystore /my_robot/robot_state_publisher
ros2 security create_enclave ~/sros2_keystore /my_robot/lifecycle_manager
ros2 security create_enclave ~/sros2_keystore /my_robot/component_container
```

Composed nodes in `component_container` normally share that process's context,
participant, and enclave. A shared enclave is valid, but every node in that
participant receives the union of its grants. Split processes or contexts when
separate identities are required; do not claim per-node isolation that the
deployment topology does not provide.

### Docker deployment

```dockerfile
# Copy only enclaves — NEVER bake the CA private key into images
COPY keystore/enclaves/ /opt/robot/keystore/enclaves/
COPY keystore/public/ /opt/robot/keystore/public/
# The CA key stays on the build server

ENV ROS_SECURITY_KEYSTORE=/opt/robot/keystore
ENV ROS_SECURITY_ENABLE=true
ENV ROS_SECURITY_STRATEGY=Enforce
```

---

## 10. Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Node fails to start with security enabled | Missing enclave or wrong enclave path | Verify `ROS_SECURITY_ENCLAVE_OVERRIDE` matches a path under `$ROS_SECURITY_KEYSTORE/enclaves/` |
| "unable to find valid identity" | Certificate expired or CA mismatch | Regenerate certs; check `openssl x509 -in cert.pem -noout -enddate` |
| Participants cannot discover each other | Keystores do not share CA | Ensure their certificates chain to mutually trusted CAs |
| Latency or CPU budget fails after enabling protection | Protection cost was not measured on the deployed path | Benchmark each protection kind; use `SIGN` only if disclosure is acceptable |
| Service calls timeout with security | Access control denying request/reply topics | Check permissions.xml includes both `rq/...Request` and `rr/...Reply` patterns |
| "Inconsistent security policy" | governance.xml format error or unsigned | Validate against OMG XSD schema; re-sign with `openssl smime -sign` |
| ros2 CLI tools cannot see topics | CLI identity or permissions do not allow access | Give the CLI its own enclave and the required narrow grants; inspect authentication and permission errors |
| Nodes start but topics have no data | Permissions allow subscribe but deny publish (or vice versa) | Check both publish and subscribe rules for every topic |
| "PKCS7 signature verification failed" | permissions.p7s signed with wrong CA key | Re-sign with the same CA key used to create the keystore |
| Lifecycle transitions fail | Missing permissions for lifecycle service topics | Add `rq/...change_stateRequest` and `rr/...change_stateReply` to permissions |

### Debugging security issues

```bash
# Step 1: Verify environment
env | grep ROS_SECURITY

# Step 2: Verify enclave directory has all required files
ls -la "$ROS_SECURITY_KEYSTORE/enclaves$(echo $ROS_SECURITY_ENCLAVE_OVERRIDE)"
# Must contain: cert.pem, key.pem, governance.p7s, permissions.p7s,
#               identity_ca.cert.pem, permissions_ca.cert.pem

# Step 3: Check certificate validity
openssl x509 -in cert.pem -noout -text | grep -A2 "Validity"

# Step 4: Keep Enforce while testing authentication and authorization failures
export ROS_SECURITY_STRATEGY=Enforce
# If initialization fallback itself must be isolated, use Permissive only in an
# isolated, non-actuating diagnostic. It may start an unsecured participant and
# therefore cannot validate that an access-control denial works.

# Step 5: Enable CycloneDDS security tracing
```

```xml
<!-- cyclonedds_security_debug.xml -->
<CycloneDDS>
  <Domain>
    <Tracing>
      <Category>discovery,security</Category>
      <OutputFile>/tmp/cyclonedds_security.log</OutputFile>
      <Verbosity>fine</Verbosity>
    </Tracing>
  </Domain>
</CycloneDDS>
```

```bash
export CYCLONEDDS_URI=file:///opt/robot/config/cyclonedds_security_debug.xml
ros2 run my_robot_driver driver_node
cat /tmp/cyclonedds_security.log | grep -i "error\|fail\|denied"
```

---

**See also:** `references/communication.md` for DDS configuration and multicast settings, `references/multi-robot.md` for securing fleet communication, `references/deployment.md` for container and supply chain hardening.
