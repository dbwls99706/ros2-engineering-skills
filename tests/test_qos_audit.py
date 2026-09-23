"""Tests for qos_audit.py - static QoS declaration audit."""

import contextlib
import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "qos_audit.py"

sys.path.insert(0, str(ROOT / "scripts"))
import qos_audit  # noqa: E402
from qos_audit import (  # noqa: E402
    AuditProfile, COMPATIBLE, INCOMPATIBLE, INDETERMINATE, evaluate,
    project_to_checker, scan, tokenize_cpp,
)

PY_HEADER = """\
from rclpy.node import Node
from rclpy.qos import (QoSProfile, ReliabilityPolicy, DurabilityPolicy,
                       LivelinessPolicy, HistoryPolicy, qos_profile_sensor_data,
                       qos_profile_system_default)
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import String
"""


def write(tmp_path: Path, name: str, body: str, header: str = "") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + textwrap.dedent(body))
    return path


def audit(path: Path, *args: str) -> tuple:
    """Run the CLI in-process so coverage includes the audit logic."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qos_audit.main([str(path), "--json", *args])
    return code, json.loads(buffer.getvalue())


def pairs(data: dict, relation: str = "confirmed") -> list:
    return [p for p in data["pairs"] if p["relation"] == relation]


def py_node(pub_qos: str, sub_qos: str, topic: str = "/image", pub_type: str = "Image",
            sub_type: str = "Image") -> str:
    return f"""
class CameraNode(Node):
    def __init__(self):
        super().__init__('camera')
        pub_qos = {pub_qos}
        sub_qos = {sub_qos}
        self.pub = self.create_publisher({pub_type}, '{topic}', pub_qos)
        self.sub = self.create_subscription({sub_type}, '{topic}', self.cb, sub_qos)
"""


EXPLICIT = ("QoSProfile(depth=5, reliability=ReliabilityPolicy.{rel}, "
            "durability=DurabilityPolicy.{dur}, liveliness=LivelinessPolicy.AUTOMATIC)")


def explicit(rel: str = "RELIABLE", dur: str = "VOLATILE") -> str:
    return EXPLICIT.format(rel=rel, dur=dur)


# ---------------------------------------------------------------------------
# Evaluator semantics (Humble rmw_dds_common)
# ---------------------------------------------------------------------------

def concrete(**kwargs) -> AuditProfile:
    base = dict(history="keep_last", depth=10, reliability="reliable",
                durability="volatile", liveliness="automatic")
    base.update(kwargs)
    return AuditProfile(**base)


class TestEvaluator:
    def test_fully_concrete_compatible_uses_checker(self):
        result = evaluate(concrete(), concrete())
        assert result.status == COMPATIBLE

    def test_fully_concrete_incompatible(self):
        result = evaluate(concrete(reliability="best_effort"), concrete())
        assert result.status == INCOMPATIBLE
        assert "RELIABILITY" in result.issues[0]

    def test_checker_warnings_are_carried(self):
        result = evaluate(concrete(depth=10), concrete(depth=1))
        assert result.status == COMPATIBLE
        assert any("depth" in w.lower() for w in result.warnings)

    @pytest.mark.parametrize("pub,sub,expected", [
        ("unknown", "reliable", INDETERMINATE),
        ("best_effort", "unknown", INDETERMINATE),
        ("unknown", "best_effort", COMPATIBLE),
        ("reliable", "system_default", COMPATIBLE),
        ("system_default", "system_default", INDETERMINATE),
    ])
    def test_reliability_direction(self, pub, sub, expected):
        assert evaluate(concrete(reliability=pub), concrete(reliability=sub)).status == expected

    @pytest.mark.parametrize("pub,sub,expected", [
        ("system_default", "transient_local", INDETERMINATE),
        ("volatile", "unknown", INDETERMINATE),
        ("unknown", "volatile", COMPATIBLE),
        ("transient_local", "system_default", COMPATIBLE),
    ])
    def test_durability_direction(self, pub, sub, expected):
        assert evaluate(concrete(durability=pub), concrete(durability=sub)).status == expected

    @pytest.mark.parametrize("pub,sub,expected", [
        ("system_default", "manual_by_topic", INDETERMINATE),
        ("automatic", "system_default", INDETERMINATE),
        ("system_default", "automatic", COMPATIBLE),
        ("manual_by_topic", "unknown", COMPATIBLE),
        ("automatic", "manual_by_topic", INCOMPATIBLE),
    ])
    def test_liveliness_direction(self, pub, sub, expected):
        assert evaluate(concrete(liveliness=pub), concrete(liveliness=sub)).status == expected

    def test_unspecified_deadline_offered_against_requested_is_incompatible(self):
        result = evaluate(concrete(), concrete(deadline_ms=100))
        assert result.status == INCOMPATIBLE

    def test_both_unspecified_deadline_is_not_a_reason(self):
        result = evaluate(concrete(), concrete())
        assert result.status == COMPATIBLE and not result.reasons

    def test_offered_deadline_longer_than_requested(self):
        assert evaluate(concrete(deadline_ms=200), concrete(deadline_ms=100)).status == INCOMPATIBLE
        assert evaluate(concrete(deadline_ms=50), concrete(deadline_ms=100)).status == COMPATIBLE

    def test_lease_follows_deadline_rules(self):
        assert evaluate(concrete(), concrete(liveliness_lease_ms=100)).status == INCOMPATIBLE
        assert evaluate(concrete(liveliness_lease_ms=200),
                        concrete(liveliness_lease_ms=100)).status == INCOMPATIBLE

    def test_lifespan_does_not_affect_compatibility(self):
        assert evaluate(concrete(lifespan_ms=5), concrete()).status == COMPATIBLE
        partial = evaluate(concrete(liveliness="system_default", lifespan_ms=5),
                           concrete(liveliness="system_default"))
        assert all("lifespan" not in r for r in partial.reasons)

    def test_known_incompatibility_wins_over_undetermined(self):
        result = evaluate(concrete(reliability="best_effort", liveliness="system_default"),
                          concrete(liveliness="system_default"))
        assert result.status == INCOMPATIBLE


class TestProjectionGuard:
    def test_concrete_projects(self):
        assert project_to_checker(concrete()) is not None

    @pytest.mark.parametrize("field_name", ["reliability", "durability", "liveliness", "history"])
    def test_undetermined_policy_is_not_projected(self, field_name):
        assert project_to_checker(concrete(**{field_name: "system_default"})) is None

    def test_unknown_depth_is_not_projected(self):
        assert project_to_checker(concrete(depth=None)) is None


# ---------------------------------------------------------------------------
# Python extraction
# ---------------------------------------------------------------------------

class TestPythonExtraction:
    def test_positive_control_literal_mismatch(self, tmp_path):
        write(tmp_path, "node.py", py_node(explicit("BEST_EFFORT"), explicit("RELIABLE")), PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1
        [pair] = pairs(data)
        assert pair["compatibility"] == INCOMPATIBLE
        assert pair["msg_type"] == "sensor_msgs/msg/Image"

    def test_explicit_compatible_pair(self, tmp_path):
        write(tmp_path, "node.py", py_node(explicit(), explicit()), PY_HEADER)
        code, data = audit(tmp_path, "--strict")
        assert code == 0
        assert pairs(data)[0]["compatibility"] == COMPATIBLE

    def test_default_durations_do_not_make_explicit_policies_indeterminate(self, tmp_path):
        write(tmp_path, "node.py", py_node(explicit("RELIABLE", "TRANSIENT_LOCAL"),
                                           explicit("RELIABLE", "TRANSIENT_LOCAL")), PY_HEADER)
        _, data = audit(tmp_path)
        assert pairs(data)[0]["compatibility"] == COMPATIBLE

    def test_durability_mismatch(self, tmp_path):
        write(tmp_path, "node.py", py_node(explicit(dur="VOLATILE"), explicit(dur="TRANSIENT_LOCAL")),
              PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1 and "DURABILITY" in pairs(data)[0]["issues"][0]

    def test_partial_profile_is_indeterminate_due_to_liveliness(self, tmp_path):
        qos = "QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)"
        write(tmp_path, "node.py", py_node(qos, qos), PY_HEADER)
        code, data = audit(tmp_path)
        pair = pairs(data)[0]
        assert code == 0 and pair["compatibility"] == INDETERMINATE
        assert any("liveliness" in r for r in pair["reasons"])
        assert not any("deadline" in r or "lifespan" in r for r in pair["reasons"])
        assert audit(tmp_path, "--strict")[0] == 1

    def test_sensor_data_pair_is_indeterminate_not_unresolved(self, tmp_path):
        write(tmp_path, "node.py", py_node("qos_profile_sensor_data", "qos_profile_sensor_data"), PY_HEADER)
        code, data = audit(tmp_path)
        pair = pairs(data)[0]
        assert pair["compatibility"] == INDETERMINATE
        assert data["unresolved"] == []
        assert any("liveliness" in r for r in pair["reasons"])
        assert code == 0 and audit(tmp_path, "--strict")[0] == 1

    def test_sensor_data_against_reliable_is_incompatible(self, tmp_path):
        write(tmp_path, "node.py", py_node("qos_profile_sensor_data", "10"), PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1 and pairs(data)[0]["compatibility"] == INCOMPATIBLE

    def test_system_default_preserved_and_indeterminate(self, tmp_path):
        write(tmp_path, "node.py", py_node("qos_profile_system_default", "qos_profile_system_default"),
              PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 0 and pairs(data)[0]["compatibility"] == INDETERMINATE
        report = scan(tmp_path)
        profile = report.endpoints[0].qos
        assert profile.reliability == "system_default" and profile.durability == "system_default"
        assert profile.deadline_ms is None
        assert audit(tmp_path, "--strict")[0] == 1

    def test_preset_enum_value(self, tmp_path):
        header = PY_HEADER + "from rclpy.qos import QoSPresetProfiles\n"
        write(tmp_path, "node.py", py_node("QoSPresetProfiles.SENSOR_DATA.value", "10"), header)
        assert audit(tmp_path)[0] == 1

    def test_best_available_is_distro_sensitive(self, tmp_path):
        qos = "QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_AVAILABLE)"
        write(tmp_path, "node.py", py_node(qos, "10"), PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 0
        [pair] = pairs(data)
        assert pair["compatibility"] is None
        assert "distro-sensitive QoS policy" in pair["reasons"][0]
        assert audit(tmp_path, "--strict")[0] == 1

    def test_best_available_profile_is_distro_sensitive(self, tmp_path):
        write(tmp_path, "node.py", py_node("qos_profile_best_available", "10"),
              PY_HEADER + "from rclpy.qos import qos_profile_best_available\n")
        _, data = audit(tmp_path)
        assert "distro-sensitive" in pairs(data)[0]["reasons"][0]

    @pytest.mark.parametrize("qos,field_name,value", [
        ("QoSProfile(depth=1, reliability=ReliabilityPolicy.SYSTEM_DEFAULT)", "reliability", "system_default"),
        ("QoSProfile(depth=1, durability=DurabilityPolicy.UNKNOWN)", "durability", "unknown"),
        ("QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT)",
         "reliability", "best_effort"),
        ("QoSProfile(history=HistoryPolicy.KEEP_ALL)", "history", "keep_all"),
        ("QoSProfile(depth=1, liveliness=RP.MANUAL_BY_TOPIC)", "liveliness", "manual_by_topic"),
    ])
    def test_policy_spellings(self, tmp_path, qos, field_name, value):
        header = PY_HEADER + ("from rclpy.qos import QoSReliabilityPolicy\n"
                              "from rclpy.qos import LivelinessPolicy as RP\n")
        write(tmp_path, "node.py", py_node(qos, "10"), header)
        profile = scan(tmp_path).endpoints[0].qos
        assert getattr(profile, field_name) == value

    @pytest.mark.parametrize("qos,reason", [
        ("QoSProfile(depth=1, reliability=DurabilityPolicy.VOLATILE)", "reliability uses"),
        ("QoSProfile(depth=1, reliability=ReliabilityPolicy.FAST)", "unsupported reliability"),
        ("QoSProfile(depth=1, reliability='reliable')", "not an enum member"),
        ("QoSProfile(reliability=ReliabilityPolicy.RELIABLE)", "without history or depth"),
        ("QoSProfile(10)", "positional"),
        ("QoSProfile(depth=n)", "not assigned in scope"),
        ("QoSProfile(depth=1.5)", "depth is not a literal"),
        ("make_qos()", "not statically resolvable"),
        ("rclpy.qos.custom_profile", "unrecognized QoS expression"),
    ])
    def test_unsupported_python_qos(self, tmp_path, qos, reason):
        write(tmp_path, "node.py", py_node(qos, "10"), PY_HEADER + "import rclpy.qos\n")
        endpoint = scan(tmp_path).endpoints[0]
        assert endpoint.qos is None and reason in endpoint.reasons[0]

    def test_fake_calls_in_comments_and_strings_are_ignored(self, tmp_path):
        write(tmp_path, "node.py", """
# self.create_publisher(Image, '/image', 10)
DOC = "self.create_subscription(Image, '/image', cb, 10)"
""", PY_HEADER)
        _, data = audit(tmp_path)
        assert data["summary"]["unresolved_endpoints"] == 0 and data["pairs"] == []
        assert data["unpaired"] == []

    def test_multiline_call(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self):
        self.pub = self.create_publisher(
            Image,
            '/image',
            QoSProfile(
                depth=5,
                reliability=ReliabilityPolicy.BEST_EFFORT,
            ),
        )
        self.sub = self.create_subscription(
            msg_type=Image, topic='/image', callback=self.cb, qos_profile=10)
""", PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1 and pairs(data)[0]["compatibility"] == INCOMPATIBLE

    def test_absolute_and_relative_names_are_not_merged(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self):
        self.create_publisher(LaserScan, '/scan', qos_profile_sensor_data)
        self.create_subscription(LaserScan, 'scan', self.cb, 10)
""", PY_HEADER)
        code, data = audit(tmp_path, "--strict")
        assert data["pairs"] == []
        assert sorted(e["topic"] for e in data["unpaired"]) == ["/scan", "scan"]
        assert code == 0

    def test_relative_and_private_same_literal_are_potential(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self):
        self.create_publisher(LaserScan, 'scan', qos_profile_sensor_data)
        self.create_subscription(LaserScan, 'scan', self.cb, 10)
        self.create_publisher(String, '~/status', 10)
        self.create_subscription(String, '~/status', self.cb, 10)
""", PY_HEADER)
        code, data = audit(tmp_path)
        potential = pairs(data, "potential")
        assert {p["topic"] for p in potential} == {"scan", "~/status"}
        scan_pair = next(p for p in potential if p["topic"] == "scan")
        assert scan_pair["compatibility"] == INCOMPATIBLE
        assert code == 0
        assert audit(tmp_path, "--strict")[0] == 1

    def test_dynamic_topic_variable(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self, name):
        self.create_publisher(String, name, 10)
        self.create_publisher(String, f'/{name}/out', 10)
        self.create_publisher(String, '/{node}/out', 10)
""", PY_HEADER)
        _, data = audit(tmp_path)
        assert len(data["unresolved"]) == 3
        assert all(e["topic_kind"] == "dynamic" for e in data["unresolved"])

    def test_local_single_assignment_resolves(self, tmp_path):
        write(tmp_path, "node.py", """
TOPIC = '/image'

class CameraNode(Node):
    def __init__(self):
        qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.pub = self.create_publisher(Image, TOPIC, qos)
        self.sub = self.create_subscription(Image, TOPIC, self.cb, 10)
""", PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1 and pairs(data)[0]["topic"] == "/image"

    @pytest.mark.parametrize("body", [
        # reassignment
        "qos = 10\n        qos = qos_profile_sensor_data\n        self.create_publisher(Image, '/i', qos)",
        # conditional assignment
        "if x:\n            qos = 10\n        self.create_publisher(Image, '/i', qos)",
        # loop
        "for qos in (1, 2):\n            self.create_publisher(Image, '/i', qos)",
        # assigned after use
        "self.create_publisher(Image, '/i', qos)\n        qos = 10",
        # parameter
        "self.create_publisher(Image, '/i', x)",
    ])
    def test_ambiguous_names_are_unresolved(self, tmp_path, body):
        write(tmp_path, "node.py", f"""
class N(Node):
    def __init__(self, x):
        {body}
""", PY_HEADER)
        _, data = audit(tmp_path)
        endpoint = data["unresolved"][0] if data["unresolved"] else None
        if endpoint is None:
            endpoint = data["unpaired"][0]
            assert endpoint["qos"] is None and endpoint["unresolved"]
        else:
            assert endpoint["unresolved"]

    def test_closure_is_unresolved(self, tmp_path):
        write(tmp_path, "node.py", """
def outer(node):
    qos = 10
    def inner():
        node.create_publisher(Image, '/i', qos)
    return inner
""", PY_HEADER)
        report = scan(tmp_path)
        assert report.endpoints[0].qos is None
        assert "closure" in report.endpoints[0].reasons[0]

    def test_import_alias_resolves_type(self, tmp_path):
        write(tmp_path, "node.py", """
import sensor_msgs.msg as smsg
from sensor_msgs import msg
from sensor_msgs.msg import Image as Img

class N(Node):
    def __init__(self):
        self.create_publisher(Img, '/a', 10)
        self.create_subscription(smsg.Image, '/a', self.cb, 10)
        self.create_subscription(msg.Image, '/a', self.cb, 10)
""", "from rclpy.node import Node\n")
        _, data = audit(tmp_path)
        assert {p["msg_type"] for p in data["pairs"]} == {"sensor_msgs/msg/Image"}
        assert len(data["pairs"]) == 2

    def test_unresolved_type(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self, msg_cls):
        self.create_publisher(msg_cls, '/a', 10)
        self.create_publisher(UndefinedType, '/a', 10)
""", PY_HEADER)
        _, data = audit(tmp_path)
        assert len(data["unresolved"]) == 2
        assert all("message type" in e["unresolved"][0] for e in data["unresolved"])

    def test_type_conflict_without_common_type_fails(self, tmp_path):
        write(tmp_path, "node.py", py_node("10", "10", topic="/camera", sub_type="LaserScan"), PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 1
        [conflict] = data["type_conflicts"]
        assert conflict["relation"] == "confirmed"
        assert "no matching type" in conflict["detail"]
        assert data["pairs"] == []

    def test_multi_type_topic_warns_only(self, tmp_path):
        write(tmp_path, "node.py", """
from debug_msgs.msg import DebugImage

class N(Node):
    def __init__(self):
        self.create_publisher(Image, '/image', qos_profile_sensor_data)
        self.create_publisher(DebugImage, '/image', 10)
        self.create_subscription(Image, '/image', self.cb, qos_profile_sensor_data)
""", PY_HEADER)
        code, data = audit(tmp_path)
        assert code == 0
        assert data["type_conflicts"] == []
        [multi] = data["multi_type_topics"]
        assert "RMW" in multi["detail"]
        [pair] = pairs(data)
        assert pair["msg_type"] == "sensor_msgs/msg/Image"
        assert [e["msg_type"] for e in data["unpaired"]] == ["debug_msgs/msg/DebugImage"]

    def test_qos_overriding_options_is_noted(self, tmp_path):
        write(tmp_path, "node.py", """
class N(Node):
    def __init__(self):
        self.create_publisher(String, '/a', 10, qos_overriding_options=opts)
""", PY_HEADER)
        report = scan(tmp_path)
        assert "override" in report.endpoints[0].notes[0]

    def test_unparsed_duration_argument_is_unresolved(self, tmp_path):
        write(tmp_path, "node.py", py_node("QoSProfile(depth=1, deadline=Duration(seconds=1))", "10"),
              PY_HEADER)
        _, data = audit(tmp_path)
        assert "deadline" in pairs(data)[0]["reasons"][0]

    def test_syntax_error_is_skipped(self, tmp_path):
        write(tmp_path, "bad.py", "def broken(:\n    self.create_publisher(\n")
        code, data = audit(tmp_path)
        assert code == 0 and "parse error" in data["skipped_files"][0]["reason"]


# ---------------------------------------------------------------------------
# C++ extraction
# ---------------------------------------------------------------------------

class TestCppExtraction:
    def test_tokenizer_skips_comments_and_keeps_strings(self):
        tokens = tokenize_cpp('// create_publisher<a::msg::B>("x", 1)\n/* x */ auto s = "y"; R"(raw)";')
        texts = [t.text for t in tokens]
        assert "create_publisher" not in texts
        assert "y" in texts and "raw" in texts

    def test_chain_and_standard_profile(self, tmp_path):
        write(tmp_path, "node.cpp", """
#include <rclcpp/rclcpp.hpp>
// pub_ = create_publisher<sensor_msgs::msg::Image>("/image", rclcpp::QoS(1));
void f() {
  pub_ = this->create_publisher<sensor_msgs::msg::Image>(
      "/image", rclcpp::SensorDataQoS().keep_last(1));
  sub_ = node->create_subscription<sensor_msgs::msg::Image>(
      "/image", rclcpp::QoS(rclcpp::KeepLast(10)).reliable().durability_volatile(),
      [this](const sensor_msgs::msg::Image::SharedPtr msg) { (void)msg; });
}
""")
        code, data = audit(tmp_path)
        assert code == 1
        [pair] = pairs(data)
        assert pair["compatibility"] == INCOMPATIBLE
        assert data["summary"]["unresolved_endpoints"] == 0

    def test_explicit_policies_and_rmw_base(self, tmp_path):
        write(tmp_path, "node.cpp", """
void f() {
  auto a = create_publisher<std_msgs::msg::String>("/s",
      rclcpp::QoS(10, rmw_qos_profile_sensor_data).liveliness(RMW_QOS_POLICY_LIVELINESS_AUTOMATIC));
  auto b = create_subscription<std_msgs::msg::String>("/s",
      rclcpp::QoS(5).best_effort().liveliness(rclcpp::LivelinessPolicy::Automatic), cb);
  auto c = rclcpp::create_publisher<std_msgs::msg::String>(node, "/t", rclcpp::QoS(rclcpp::KeepAll()));
}
""")
        code, data = audit(tmp_path)
        pair = next(p for p in data["pairs"] if p["topic"] == "/s")
        assert pair["compatibility"] == COMPATIBLE
        assert any(e["topic"] == "/t" for e in data["unpaired"])
        assert code == 0

    def test_system_defaults_qos(self, tmp_path):
        write(tmp_path, "node.cpp", """
void f() {
  create_publisher<std_msgs::msg::String>("/s", rclcpp::SystemDefaultsQoS());
  create_subscription<std_msgs::msg::String>("/s", rclcpp::SystemDefaultsQoS(), cb);
}
""")
        code, data = audit(tmp_path)
        assert code == 0 and pairs(data)[0]["compatibility"] == INDETERMINATE
        assert audit(tmp_path, "--strict")[0] == 1

    @pytest.mark.parametrize("qos,reason", [
        ("qos", "data-flow"),
        ("rclcpp::BestAvailableQoS()", "distro-sensitive"),
        ("rclcpp::QoS(1).reliability(rclcpp::ReliabilityPolicy::BestAvailable)", "distro-sensitive"),
        ("rclcpp::QoS(1).reliability_best_available()", "distro-sensitive"),
        ("rclcpp::QoS(1).deadline(std::chrono::milliseconds(10))", "not parsed"),
        ("rclcpp::RosoutQoS()", "unrecognized"),
        ("rclcpp::QoS(depth)", "history initialization"),
    ])
    def test_unresolved_qos(self, tmp_path, qos, reason):
        write(tmp_path, "node.cpp", f"""
void f() {{
  create_publisher<std_msgs::msg::String>("/s", {qos});
}}
""")
        code, data = audit(tmp_path)
        assert reason in data["unpaired"][0]["unresolved"][0]
        assert code == 0

    def test_dynamic_topic_and_alias_type(self, tmp_path):
        write(tmp_path, "node.hpp", """
using Msg = std_msgs::msg::String;
void f() {
  create_publisher<std_msgs::msg::String>(topic_name, 10);
  create_publisher<Msg>("/s", 10);
}
""")
        _, data = audit(tmp_path)
        reasons = [e["unresolved"][0] for e in data["unresolved"]]
        assert reasons == ["dynamic topic", "message type not statically resolvable"]

    def test_cross_language_pair(self, tmp_path):
        write(tmp_path, "pkg/src/talker.cpp", """
void f() { create_publisher<std_msgs::msg::String>("/chatter", rclcpp::QoS(10).best_effort()); }
""")
        write(tmp_path, "pkg/pkg/listener.py", """
class L(Node):
    def __init__(self):
        self.create_subscription(String, '/chatter', self.cb, 10)
""", PY_HEADER)
        code, data = audit(tmp_path)
        [pair] = pairs(data)
        assert pair["publisher"].endswith("talker.cpp:2")
        assert pair["subscription"].endswith("listener.py:10")
        assert pair["compatibility"] == INCOMPATIBLE and code == 1


# ---------------------------------------------------------------------------
# YAML candidates, scanning scope, CLI
# ---------------------------------------------------------------------------

class TestScopeAndCli:
    def test_yaml_override_is_candidate_only(self, tmp_path):
        write(tmp_path, "node.py", py_node("qos_profile_sensor_data", "10"), PY_HEADER)
        write(tmp_path, "config/qos.yaml", """
camera:
  ros__parameters:
    qos_overrides:
      /image:
        publisher:
          reliability: reliable
""")
        code, data = audit(tmp_path)
        [candidate] = data["yaml_override_candidates"]
        assert candidate["topic"] == "/image" and candidate["entity"] == "publisher"
        assert candidate["node"] == "camera.ros__parameters"
        assert pairs(data)[0]["compatibility"] == INCOMPATIBLE and code == 1

    def test_yaml_candidate_alone_does_not_fail_strict(self, tmp_path):
        write(tmp_path, "qos.yaml", "n:\n  ros__parameters:\n    qos_overrides:\n      /a:\n"
                                    "        subscription:\n          depth: 3\n")
        write(tmp_path, "broken.yaml", "qos_overrides: [unclosed\n")
        code, data = audit(tmp_path, "--strict")
        assert code == 0 and len(data["yaml_override_candidates"]) == 1

    def test_excluded_dirs_and_size_limit(self, tmp_path, monkeypatch):
        body = py_node(explicit("BEST_EFFORT"), explicit())
        write(tmp_path, "build/node.py", body, PY_HEADER)
        write(tmp_path, "install/node.py", body, PY_HEADER)
        write(tmp_path, "big.py", body, PY_HEADER)
        monkeypatch.setattr(qos_audit, "MAX_FILE_BYTES", 10)
        report = scan(tmp_path)
        assert report.endpoints == []
        assert report.skipped[0]["file"] == "big.py"

    def test_single_file_input(self, tmp_path):
        path = write(tmp_path, "node.py", py_node(explicit("BEST_EFFORT"), explicit()), PY_HEADER)
        code, data = audit(path)
        assert code == 1 and pairs(data)[0]["publisher"].startswith("node.py:")

    def test_missing_path_exit_2(self, tmp_path):
        proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope")],
                              capture_output=True, text=True)
        assert proc.returncode == 2 and "not found" in proc.stderr

    def test_usage_error_exit_2(self):
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        assert proc.returncode == 2

    def test_subprocess_json_matches_in_process(self, tmp_path):
        write(tmp_path, "node.py", py_node(explicit("BEST_EFFORT"), explicit()), PY_HEADER)
        proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--json"],
                              capture_output=True, text=True)
        assert proc.returncode == 1
        assert json.loads(proc.stdout)["summary"] == audit(tmp_path)[1]["summary"]

    def test_version(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True)
        assert proc.stdout.strip().endswith(qos_audit.__version__)

    def test_text_output_sections(self, tmp_path, capsys):
        write(tmp_path, "node.py", py_node("qos_profile_sensor_data", "10"), PY_HEADER)
        write(tmp_path, "other.py", """
class N(Node):
    def __init__(self, t):
        self.create_publisher(String, t, 10, qos_overriding_options=o)
        self.create_publisher(String, 'rel', 10)
        self.create_subscription(String, 'rel', self.cb, 10)
        self.create_publisher(Image, '/x', 10)
        self.create_subscription(LaserScan, '/x', self.cb, 10)
        self.create_publisher(Image, '/y', 10)
""", PY_HEADER)
        write(tmp_path, "c.yaml", "n:\n  qos_overrides:\n    /a:\n      publisher:\n        depth: 1\n")
        big = write(tmp_path, "big.py", "x = 1\n")
        os.truncate(big, qos_audit.MAX_FILE_BYTES + 1)
        code = qos_audit.main([str(tmp_path)])
        out = capsys.readouterr().out
        for heading in ("Confirmed pairs", "Potential pairs", "Type conflicts", "Unpaired endpoints",
                        "Unresolved endpoints", "Notes", "YAML QoS override candidates",
                        "Skipped files", "Boundary:"):
            assert heading in out
        assert code == 1

    def test_main_json_strict(self, tmp_path, capsys):
        write(tmp_path, "node.py", py_node("10", "10"), PY_HEADER)
        code = qos_audit.main([str(tmp_path), "--json", "--strict"])
        data = json.loads(capsys.readouterr().out)
        assert data["strict"] is True and data["exit_code"] == code == 1


class TestRepositoryControls:
    def test_qos_roundtrip_dynamic_topic_is_not_inferred(self):
        code, data = audit(ROOT / "examples" / "qos_roundtrip.py")
        assert code == 0
        assert data["pairs"] == [] and data["type_conflicts"] == []
        assert len(data["unresolved"]) == 4
        assert all("dynamic topic" in e["unresolved"] for e in data["unresolved"])

    def test_generated_packages_do_not_crash(self, tmp_path):
        for pkg_type in ("cpp", "python"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / "create_package.py"),
                            f"pkg_{pkg_type}", "--type", pkg_type, "--dest", str(tmp_path)],
                           check=True, capture_output=True, text=True)
        code, data = audit(tmp_path, "--strict")
        assert code == 0
        assert [e["topic"] for e in data["unpaired"]] == ["managed_probe"]
        assert data["unpaired"][0]["msg_type"] == "std_msgs/msg/String"
