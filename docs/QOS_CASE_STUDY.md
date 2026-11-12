# Controlled QoS failure and repair

This is a runnable software experiment, not a report of a field deployment and
not an output attributed to a model. It exercises the middleware behavior that
motivates the skill's evidence-first QoS guidance.

`examples/qos_roundtrip.py` creates a unique topic and a BEST_EFFORT publisher.
A matching positive-control subscriber receives messages while a RELIABLE
subscriber on the same topic cannot match that offered policy. The script then
replaces the incompatible subscriber with BEST_EFFORT and requires live delivery.
A timeout or failed positive control raises an error instead of being called a
successful demonstration. The JSON result records before/after counters, RMW,
distribution, and the verification boundary.

```bash
# Use the actual sourced distro; this example intentionally publishes test data.
python3 examples/qos_roundtrip.py
```

Run in a disposable ROS container or isolated ROS domain. No actuator command,
robot driver, hardware interface, `/cmd_vel`, or physical fault injection is
involved. Unique names prevent accidental topic collision; isolation remains the
operator's responsibility when running outside CI. Receiving data after changing
QoS does not prove bounded latency, adequate loss behavior, or safe actuation.

CI runs this after the existing generated-package container build on the stable
distribution matrix. Rolling's already-documented runtime limitation remains an
explicit skip, not an L3 pass. Read the job log for the exact image and result;
the presence of this script alone proves only that the example exists.

The static audit, `scripts/qos_audit.py`, is a separate check: it reads
declarations and never runs them. A Python profile kept in a variable is judged
from its constructor only while the object cannot have changed. Plain attribute
assignments in the same function body before the use are applied in statement
order; any other change, and any alias or call that could change the object,
leaves the endpoint unresolved instead of confirmed. A shared profile such as
`qos_profile_sensor_data` is held to the same rule across the module, under
every name it has there (an import alias, a module path,
`QoSPresetProfiles.SENSOR_DATA.value`), and an alias imported for more than one
profile stands for all of them: reading an attribute and passing it as
an endpoint's QoS keep it trusted, and anything else — `setattr`, a helper, a
client, a service, a message filter — does not. rclpy endpoints keep the
profile object they are given and `qos_overriding_options` writes to it, so
such a call, and any assignment through some `.qos_profile`, also counts as a
change. The audit reads one module at a time, so a change made in another
module is not seen. The running graph (`ros2 topic info -v`) remains the
evidence for what is actually offered.

Source: [ROS 2 Humble QoS compatibility tables](https://docs.ros.org/en/humble/Concepts/Intermediate/About-Quality-of-Service-Settings.html).
