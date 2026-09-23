#!/usr/bin/env python3
"""Statically audit QoS declarations across a ROS 2 package or workspace.

Scans rclpy (AST) and rclcpp (lexical) sources for create_publisher and
create_subscription calls, pairs endpoints by (topic, message type), and
evaluates offered/requested compatibility without executing any code.

Usage:
    python qos_audit.py path/to/package
    python qos_audit.py path/to/workspace/src --json
    python qos_audit.py path/to/package --strict

Boundary: this reads declarations only. Remapping, namespaces, launch
parameters, runtime QoS overrides, RMW defaults, and delivery quality are not
observed; confirm the running graph with `ros2 topic info -v` (L3+).
Values that cannot be determined statically are reported as unresolved,
never guessed.
"""

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore[import-untyped]
    HAS_YAML = True
except ImportError:  # pragma: no cover - PyYAML is a declared runtime dependency
    HAS_YAML = False

# scripts/ is intentionally not a package; see rosbag2_qos_checker.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qos_checker import (  # noqa: E402
    Durability, History, Liveliness, QoSProfile, Reliability,
    check_compatibility,
)

__version__ = "0.1.0"

MAX_FILE_BYTES = 1024 * 1024
EXCLUDED_DIRS = {"build", "install", "log", ".git", "__pycache__", "node_modules"}
PY_SUFFIXES = {".py"}
CPP_SUFFIXES = {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"}
YAML_SUFFIXES = {".yaml", ".yml"}

# Policy states. Enum policies keep SYSTEM_DEFAULT/UNKNOWN as-is; they are
# never replaced with a concrete value.
RELIABLE = "reliable"
BEST_EFFORT = "best_effort"
VOLATILE = "volatile"
TRANSIENT_LOCAL = "transient_local"
AUTOMATIC = "automatic"
MANUAL_BY_TOPIC = "manual_by_topic"
KEEP_LAST = "keep_last"
KEEP_ALL = "keep_all"
SYSTEM_DEFAULT = "system_default"
UNKNOWN = "unknown"
UNDETERMINED = (SYSTEM_DEFAULT, UNKNOWN)

DISTRO_SENSITIVE = "distro-sensitive QoS policy"

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
INDETERMINATE = "indeterminate"


class Unresolved(Exception):
    """A value that cannot be determined statically."""


@dataclass
class AuditProfile:
    """QoS profile with explicit undetermined states.

    Durations are milliseconds; None means RMW_DURATION_UNSPECIFIED, which is
    evaluated with the upstream default-sentinel semantics, not as a guess.
    """

    history: str = KEEP_LAST
    depth: Optional[int] = 10
    reliability: str = RELIABLE
    durability: str = VOLATILE
    liveliness: str = SYSTEM_DEFAULT
    deadline_ms: Optional[int] = None
    lifespan_ms: Optional[int] = None
    liveliness_lease_ms: Optional[int] = None
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "history": self.history, "depth": self.depth,
            "reliability": self.reliability, "durability": self.durability,
            "liveliness": self.liveliness,
            "deadline_ms": _duration_str(self.deadline_ms),
            "lifespan_ms": _duration_str(self.lifespan_ms),
            "liveliness_lease_ms": _duration_str(self.liveliness_lease_ms),
            "source": self.source,
        }


def _duration_str(value: Optional[int]) -> Any:
    return "unspecified" if value is None else value


# Standard profiles as defined by rmw/qos_profiles.h (Humble through Rolling).
# Liveliness is SYSTEM_DEFAULT and durations are unspecified in every one of
# them; history/depth/reliability/durability are listed per profile.
def _standard(name: str) -> AuditProfile:
    table = {
        "default": (KEEP_LAST, 10, RELIABLE, VOLATILE),
        "sensor_data": (KEEP_LAST, 5, BEST_EFFORT, VOLATILE),
        "services_default": (KEEP_LAST, 10, RELIABLE, VOLATILE),
        "parameters": (KEEP_LAST, 1000, RELIABLE, VOLATILE),
        "parameter_events": (KEEP_LAST, 1000, RELIABLE, VOLATILE),
    }
    if name == "system_default":
        return AuditProfile(SYSTEM_DEFAULT, None, SYSTEM_DEFAULT, SYSTEM_DEFAULT,
                            SYSTEM_DEFAULT, source=name)
    history, depth, reliability, durability = table[name]
    return AuditProfile(history, depth, reliability, durability, SYSTEM_DEFAULT,
                        source=name)


PY_STANDARD_PROFILES = {
    "qos_profile_sensor_data": "sensor_data",
    "qos_profile_system_default": "system_default",
    "qos_profile_services_default": "services_default",
    "qos_profile_parameters": "parameters",
    "qos_profile_parameter_events": "parameter_events",
}
PY_PRESET_KEYS = {
    "SENSOR_DATA": "sensor_data", "SYSTEM_DEFAULT": "system_default",
    "SERVICES_DEFAULT": "services_default", "PARAMETERS": "parameters",
    "PARAMETER_EVENTS": "parameter_events",
}
CPP_STANDARD_PROFILES = {
    "SensorDataQoS": "sensor_data", "SystemDefaultsQoS": "system_default",
    "ServicesQoS": "services_default", "ParametersQoS": "parameters",
    "ParameterEventsQoS": "parameter_events",
}
CPP_RMW_PROFILES = {
    "rmw_qos_profile_default": "default",
    "rmw_qos_profile_sensor_data": "sensor_data",
    "rmw_qos_profile_system_default": "system_default",
    "rmw_qos_profile_services_default": "services_default",
    "rmw_qos_profile_parameters": "parameters",
    "rmw_qos_profile_parameter_events": "parameter_events",
}

# Enum spellings: rclpy (ReliabilityPolicy.RELIABLE and the older
# RMW_QOS_POLICY_* members), rclcpp enum classes, and rmw C constants.
POLICY_VALUES = {
    "reliability": {"RELIABLE": RELIABLE, "BEST_EFFORT": BEST_EFFORT,
                    "BESTEFFORT": BEST_EFFORT},
    "durability": {"VOLATILE": VOLATILE, "TRANSIENT_LOCAL": TRANSIENT_LOCAL,
                   "TRANSIENTLOCAL": TRANSIENT_LOCAL},
    "liveliness": {"AUTOMATIC": AUTOMATIC, "MANUAL_BY_TOPIC": MANUAL_BY_TOPIC,
                   "MANUALBYTOPIC": MANUAL_BY_TOPIC},
    "history": {"KEEP_LAST": KEEP_LAST, "KEEP_ALL": KEEP_ALL,
                "KEEPLAST": KEEP_LAST, "KEEPALL": KEEP_ALL},
}
PY_POLICY_CLASSES = {
    "reliability": {"ReliabilityPolicy", "QoSReliabilityPolicy"},
    "durability": {"DurabilityPolicy", "QoSDurabilityPolicy"},
    "liveliness": {"LivelinessPolicy", "QoSLivelinessPolicy"},
    "history": {"HistoryPolicy", "QoSHistoryPolicy"},
}


def _policy_member(policy: str, member: str) -> str:
    """Map an enum member spelling to a policy state or raise Unresolved."""
    key = member.upper()
    prefix = f"RMW_QOS_POLICY_{policy.upper()}_"
    if key.startswith(prefix):
        key = key[len(prefix):]
    key = key.replace("SYSTEMDEFAULT", "SYSTEM_DEFAULT")
    if key == "SYSTEM_DEFAULT":
        return SYSTEM_DEFAULT
    if key == "UNKNOWN":
        return UNKNOWN
    if "BEST_AVAILABLE" in key or "BESTAVAILABLE" in key:
        raise Unresolved(f"{DISTRO_SENSITIVE}: {policy} {member}")
    if key in POLICY_VALUES[policy]:
        return POLICY_VALUES[policy][key]
    raise Unresolved(f"unsupported {policy} value {member}")


@dataclass
class Endpoint:
    kind: str  # "publisher" or "subscription"
    file: str
    line: int
    language: str
    topic: Optional[str] = None
    topic_kind: str = "dynamic"  # absolute, relative, private, dynamic
    msg_type: Optional[str] = None
    qos: Optional[AuditProfile] = None
    reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "location": self.location,
            "language": self.language, "topic": self.topic,
            "topic_kind": self.topic_kind, "msg_type": self.msg_type,
            "qos": self.qos.to_dict() if self.qos else None,
            "unresolved": list(self.reasons), "notes": list(self.notes),
        }


def classify_topic(topic: Optional[str]) -> str:
    if not topic or "{" in topic:
        return "dynamic"
    if topic.startswith("~"):
        return "private"
    if topic.startswith("/"):
        return "absolute"
    return "relative"


def _set_topic(endpoint: Endpoint, topic: Optional[str]) -> None:
    endpoint.topic = topic
    endpoint.topic_kind = classify_topic(topic)
    if endpoint.topic_kind == "dynamic":
        endpoint.reasons.append("dynamic topic" if topic is None
                                else f"topic uses substitution: {topic}")


# ---------------------------------------------------------------------------
# Compatibility evaluation
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    status: str
    issues: list
    warnings: list
    reasons: list  # why indeterminate


def project_to_checker(profile: AuditProfile, label: str = "") -> Optional[QoSProfile]:
    """Return a qos_checker profile only for a compatibility-preserving projection.

    Every enum policy must be concrete. Unspecified durations map to 0, which
    qos_checker and rmw_dds_common both treat as "no deadline/lease". A KEEP_ALL
    profile without a depth gets a 0 sentinel: the projection is not lossless,
    but KEEP_ALL depth never takes part in the compatibility decision.
    """
    if (profile.reliability in UNDETERMINED or profile.durability in UNDETERMINED
            or profile.liveliness in UNDETERMINED or profile.history in UNDETERMINED):
        return None
    depth = profile.depth
    if depth is None:
        if profile.history != KEEP_ALL:
            return None
        depth = 0
    return QoSProfile(
        reliability=Reliability(profile.reliability),
        durability=Durability(profile.durability),
        history=History(profile.history),
        depth=depth,
        label=label,
        deadline_ms=profile.deadline_ms or 0,
        lifespan_ms=profile.lifespan_ms or 0,
        liveliness=Liveliness(profile.liveliness),
        liveliness_lease_ms=profile.liveliness_lease_ms or 0,
    )


def _check_duration(name: str, pub: Optional[int], sub: Optional[int]) -> Optional[str]:
    # rmw_dds_common: an unspecified offered value against a specified
    # requested value is an error; two specified values need offered <= requested.
    if pub is None and sub is not None:
        return f"INCOMPATIBLE {name.upper()}: publisher has no {name} but subscription requests {sub}ms"
    if pub is not None and sub is not None and sub < pub:
        return f"INCOMPATIBLE {name.upper()}: offered {pub}ms exceeds requested {sub}ms"
    return None


def _undetermined_reason(policy: str, pub: str, sub: str, strong: str, weak: str) -> Optional[str]:
    """Mirror the rmw_dds_common warning cases for one request/offer policy."""
    pub_unknown = pub in UNDETERMINED
    sub_unknown = sub in UNDETERMINED
    if pub_unknown and sub_unknown:
        return f"{policy} is {pub} on the publisher and {sub} on the subscription"
    if pub_unknown and sub == strong:
        return f"publisher {policy} is {pub}; subscription requests {strong}"
    if pub == weak and sub_unknown:
        return f"subscription {policy} is {sub}; publisher offers {weak}"
    return None


def evaluate(pub: AuditProfile, sub: AuditProfile) -> Evaluation:
    """Tri-state compatibility following Humble rmw_dds_common semantics."""
    issues = []
    if pub.reliability == BEST_EFFORT and sub.reliability == RELIABLE:
        issues.append("INCOMPATIBLE RELIABILITY: publisher BEST_EFFORT, subscription RELIABLE")
    if pub.durability == VOLATILE and sub.durability == TRANSIENT_LOCAL:
        issues.append("INCOMPATIBLE DURABILITY: publisher VOLATILE, subscription TRANSIENT_LOCAL")
    if pub.liveliness == AUTOMATIC and sub.liveliness == MANUAL_BY_TOPIC:
        issues.append("INCOMPATIBLE LIVELINESS: publisher AUTOMATIC, subscription MANUAL_BY_TOPIC")
    for name, pub_value, sub_value in (
            ("deadline", pub.deadline_ms, sub.deadline_ms),
            ("liveliness lease", pub.liveliness_lease_ms, sub.liveliness_lease_ms)):
        issue = _check_duration(name, pub_value, sub_value)
        if issue:
            issues.append(issue)

    reasons = [r for r in (
        _undetermined_reason("reliability", pub.reliability, sub.reliability, RELIABLE, BEST_EFFORT),
        _undetermined_reason("durability", pub.durability, sub.durability, TRANSIENT_LOCAL, VOLATILE),
        _undetermined_reason("liveliness", pub.liveliness, sub.liveliness, MANUAL_BY_TOPIC, AUTOMATIC),
    ) if r]

    warnings: list = []
    pub_checker = project_to_checker(pub, "publisher")
    sub_checker = project_to_checker(sub, "subscription")
    if pub_checker is not None and sub_checker is not None:
        checked = check_compatibility(pub_checker, sub_checker)
        warnings = list(checked.warnings)
        if checked.compatible != (not issues):  # pragma: no cover - consistency guard
            raise AssertionError("qos_checker and audit evaluator disagree")
        if not checked.compatible:
            issues = list(checked.issues)

    if issues:
        return Evaluation(INCOMPATIBLE, issues, warnings, reasons)
    if reasons:
        return Evaluation(INDETERMINATE, [], warnings, reasons)
    return Evaluation(COMPATIBLE, [], warnings, [])


# ---------------------------------------------------------------------------
# Python extraction (ast)
# ---------------------------------------------------------------------------

def _dotted(node: ast.AST) -> Optional[str]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Scope:
    """Single-assignment lookup for one function or module body."""

    def __init__(self, node: ast.AST, parent: Optional["_Scope"]):
        self.node = node
        self.parent = parent
        self.assign_counts: dict = {}
        self.simple: dict = {}  # name -> (value, line) for top-level single Assign
        self.params: set = set()
        self.declared: set = set()  # global / nonlocal
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                self.params.add(arg.arg)
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    self.params.add(extra.arg)
        body = getattr(node, "body", [])
        if isinstance(body, list):
            for stmt in body:
                if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)):
                    self.simple.setdefault(stmt.targets[0].id, []).append((stmt.value, stmt.lineno))
                elif (isinstance(stmt, ast.AnnAssign) and stmt.value is not None
                        and isinstance(stmt.target, ast.Name)):
                    self.simple.setdefault(stmt.target.id, []).append((stmt.value, stmt.lineno))
        for child in _scope_nodes(node):
            for name in _bound_names(child):
                self.assign_counts[name] = self.assign_counts.get(name, 0) + 1
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                self.declared.update(child.names)

    def lookup(self, name: str, line: int) -> ast.AST:
        if name in self.params or name in self.declared:
            raise Unresolved(f"name '{name}' is a parameter or global/nonlocal")
        count = self.assign_counts.get(name, 0)
        if count == 0:
            if self.parent is None:
                raise Unresolved(f"name '{name}' is not assigned in scope")
            if not isinstance(self.parent.node, ast.Module):
                raise Unresolved(f"name '{name}' comes from an enclosing function (closure)")
            # Module constants are read when the function runs, so any single
            # top-level module assignment qualifies regardless of line order.
            return self.parent.lookup(name, line=10 ** 9)
        entries = self.simple.get(name, [])
        if count != 1 or len(entries) != 1:
            raise Unresolved(f"name '{name}' is reassigned or conditionally assigned")
        value, assigned_line = entries[0]
        if assigned_line >= line:
            raise Unresolved(f"name '{name}' is assigned after use")
        return value


def _scope_nodes(scope: ast.AST):
    """Yield nodes lexically inside scope, not descending into nested scopes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _bound_names(node: ast.AST):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        yield node.id
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield (alias.asname or alias.name).split(".")[0]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        yield node.name


class PythonExtractor:
    def __init__(self, path: str, source: str):
        self.path = path
        self.tree = ast.parse(source, filename=path)
        self.imports = self._collect_imports()
        self.endpoints: list = []

    def _collect_imports(self) -> dict:
        found: dict = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        found.setdefault(alias.asname, set()).add(alias.name)
                    else:
                        head = alias.name.split(".")[0]
                        found.setdefault(head, set()).add(head)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    found.setdefault(alias.asname or alias.name, set()).add(
                        f"{node.module}.{alias.name}")
        # A name imported with two different meanings is ambiguous.
        return {name: next(iter(targets)) for name, targets in found.items() if len(targets) == 1}

    def run(self) -> list:
        self._visit(self.tree, None)
        return sorted(self.endpoints, key=lambda e: e.line)

    def _visit(self, node: ast.AST, parent: Optional[_Scope]) -> None:
        scope = _Scope(node, parent)
        for child in _scope_nodes(node):
            if isinstance(child, ast.Call):
                self._maybe_endpoint(child, scope)
        for child in _scope_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._visit(child, scope)
            elif isinstance(child, ast.ClassDef):
                # Class bodies do not form closures for methods; methods see
                # the module scope directly.
                self._visit_class(child, scope)

    def _visit_class(self, node: ast.ClassDef, module_scope: _Scope) -> None:
        class_scope = _Scope(node, module_scope)
        for child in _scope_nodes(node):
            if isinstance(child, ast.Call):
                self._maybe_endpoint(child, class_scope)
        for child in _scope_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._visit(child, module_scope)
            elif isinstance(child, ast.ClassDef):
                self._visit_class(child, module_scope)

    def _resolve(self, node: ast.AST, scope: _Scope, line: int, depth: int = 0) -> ast.AST:
        if isinstance(node, ast.Name) and node.id not in self.imports:
            if depth > 8:
                raise Unresolved(f"name '{node.id}' resolution is too deep")
            return self._resolve(scope.lookup(node.id, line), scope, line, depth + 1)
        return node

    def _maybe_endpoint(self, call: ast.Call, scope: _Scope) -> None:
        if not isinstance(call.func, ast.Attribute):
            return
        name = call.func.attr
        if name not in ("create_publisher", "create_subscription"):
            return
        kind = "publisher" if name == "create_publisher" else "subscription"
        endpoint = Endpoint(kind, self.path, call.lineno, "python")
        self.endpoints.append(endpoint)
        if any(isinstance(a, ast.Starred) for a in call.args) or any(
                k.arg is None for k in call.keywords):
            endpoint.reasons.append("call uses *args/**kwargs")
            return
        order = (["msg_type", "topic", "qos_profile"] if kind == "publisher"
                 else ["msg_type", "topic", "callback", "qos_profile"])
        args: dict = dict(zip(order, call.args))
        for keyword in call.keywords:
            args[keyword.arg or ""] = keyword.value
        if "qos_overriding_options" in args:
            endpoint.notes.append("qos_overriding_options set: parameters may override the declared QoS")

        type_node = args.get("msg_type")
        endpoint.msg_type = self._msg_type(type_node) if type_node is not None else None
        if endpoint.msg_type is None:
            endpoint.reasons.append("message type not statically resolvable")

        topic_node = args.get("topic")
        topic = None
        if topic_node is not None:
            try:
                resolved = self._resolve(topic_node, scope, call.lineno)
                if isinstance(resolved, ast.Constant) and isinstance(resolved.value, str):
                    topic = resolved.value
            except Unresolved:
                topic = None
        _set_topic(endpoint, topic)

        qos_node = args.get("qos_profile")
        if qos_node is None:
            endpoint.reasons.append("QoS argument missing")
            return
        try:
            endpoint.qos = self._qos(qos_node, scope, call.lineno)
        except Unresolved as exc:
            endpoint.reasons.append(f"QoS: {exc}")

    def _expand(self, dotted: str) -> str:
        """Replace an imported alias at the head of a dotted name."""
        head, _, rest = dotted.partition(".")
        target = self.imports.get(head)
        if target is None:
            return dotted
        return target + ("." + rest if rest else "")

    def _msg_type(self, node: ast.AST) -> Optional[str]:
        dotted = _dotted(node)
        if dotted is None or dotted.partition(".")[0] not in self.imports:
            return None
        parts = self._expand(dotted).split(".")
        if len(parts) == 3 and parts[1] == "msg":
            return f"{parts[0]}/msg/{parts[2]}"
        return None

    def _qos(self, node: ast.AST, scope: _Scope, line: int) -> AuditProfile:
        node = self._resolve(node, scope, line)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            profile = _standard("default")
            profile.depth = node.value
            profile.source = f"depth {node.value}"
            return profile
        dotted = _dotted(node)
        if dotted is not None:
            dotted = self._expand(dotted)
            last = dotted.split(".")[-1]
            if last in PY_STANDARD_PROFILES:
                return _standard(PY_STANDARD_PROFILES[last])
            if last == "qos_profile_best_available":
                raise Unresolved(f"{DISTRO_SENSITIVE}: {last}")
            parts = dotted.split(".")
            if len(parts) >= 3 and parts[-3] == "QoSPresetProfiles" and parts[-1] == "value":
                if parts[-2] in PY_PRESET_KEYS:
                    return _standard(PY_PRESET_KEYS[parts[-2]])
                if "BEST_AVAILABLE" in parts[-2]:
                    raise Unresolved(f"{DISTRO_SENSITIVE}: QoSPresetProfiles.{parts[-2]}")
            raise Unresolved(f"unrecognized QoS expression {dotted}")
        if isinstance(node, ast.Call) and _dotted(node.func) is not None:
            func = self._expand(_dotted(node.func) or "").split(".")[-1]
            if func == "QoSProfile":
                return self._qos_profile_call(node, scope, line)
        raise Unresolved("QoS expression is not statically resolvable")

    def _qos_profile_call(self, call: ast.Call, scope: _Scope, line: int) -> AuditProfile:
        if call.args or any(k.arg is None for k in call.keywords):
            raise Unresolved("QoSProfile uses positional or ** arguments")
        profile = _standard("default")
        profile.source = "QoSProfile"
        given = {k.arg: k.value for k in call.keywords}
        if "history" not in given and "depth" not in given:
            raise Unresolved("QoSProfile without history or depth")
        for key, value in given.items():
            if key in POLICY_VALUES:
                setattr(profile, key, self._policy(key, value, scope, line))
            elif key == "depth":
                resolved = self._resolve(value, scope, line)
                if not (isinstance(resolved, ast.Constant) and isinstance(resolved.value, int)
                        and not isinstance(resolved.value, bool)):
                    raise Unresolved("QoSProfile depth is not a literal integer")
                profile.depth = resolved.value
            else:
                # deadline, lifespan, liveliness_lease_duration and others are
                # not parsed in this version; never assume their values.
                raise Unresolved(f"QoSProfile argument '{key}' is not parsed")
        # rclpy (Humble+) raises InvalidQoSProfileException for an explicit
        # KEEP_LAST without depth; otherwise an omitted depth keeps the
        # rmw default, which KEEP_ALL ignores.
        if "history" in given and "depth" not in given and profile.history == KEEP_LAST:
            raise Unresolved("invalid QoSProfile: KEEP_LAST without depth "
                             "(rclpy raises InvalidQoSProfileException)")
        return profile

    def _policy(self, policy: str, node: ast.AST, scope: _Scope, line: int) -> str:
        node = self._resolve(node, scope, line)
        dotted = _dotted(node)
        if dotted is None or "." not in dotted:
            raise Unresolved(f"{policy} is not an enum member")
        dotted = self._expand(dotted)
        cls, member = dotted.split(".")[-2:]
        if cls not in PY_POLICY_CLASSES[policy]:
            raise Unresolved(f"{policy} uses {dotted}")
        return _policy_member(policy, member)


# ---------------------------------------------------------------------------
# C++ extraction (lexical, no external parser)
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str  # ident, string, number, punct
    text: str
    line: int


def tokenize_cpp(source: str) -> list:
    tokens = []
    i, line, n = 0, 1, len(source)
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch.isspace():
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += source.count("\n", i, end)
            i = end
        elif source.startswith('R"', i):
            open_paren = source.find("(", i + 2)
            if open_paren < 0:
                i += 2
                continue
            delim = source[i + 2:open_paren]
            close = source.find(")" + delim + '"', open_paren)
            end = n if close < 0 else close + len(delim) + 2
            tokens.append(Token("string", source[open_paren + 1:close if close >= 0 else n], line))
            line += source.count("\n", i, end)
            i = end
        elif ch in "\"'":
            j = i + 1
            while j < n and source[j] != ch and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            if ch == '"':
                tokens.append(Token("string", source[i + 1:j], line))
            i = j + 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            tokens.append(Token("ident", source[i:j], line))
            i = j
        elif ch.isdigit():
            j = i
            while j < n and (source[j].isalnum() or source[j] in "._'"):
                j += 1
            tokens.append(Token("number", source[i:j], line))
            i = j
        elif source.startswith("::", i) or source.startswith("->", i):
            tokens.append(Token("punct", source[i:i + 2], line))
            i += 2
        else:
            tokens.append(Token("punct", ch, line))
            i += 1
    return tokens


def _int_literal(text: str) -> Optional[int]:
    digits = text.replace("'", "").rstrip("uUlLzZ")
    return int(digits) if digits.isdigit() else None


class CppExtractor:
    PAIRS = {"(": ")", "[": "]", "{": "}"}

    def __init__(self, path: str, source: str):
        self.path = path
        self.tokens = tokenize_cpp(source)

    def run(self) -> list:
        endpoints = []
        toks = self.tokens
        for i, tok in enumerate(toks):
            if tok.kind != "ident" or tok.text not in ("create_publisher", "create_subscription"):
                continue
            if i + 1 >= len(toks) or toks[i + 1].text != "<":
                continue
            template_end = self._match_angle(i + 1)
            if template_end is None or template_end + 1 >= len(toks) or toks[template_end + 1].text != "(":
                continue
            call_end = self._match(template_end + 1)
            if call_end is None:
                continue
            kind = "publisher" if tok.text == "create_publisher" else "subscription"
            endpoint = Endpoint(kind, self.path, tok.line, "cpp")
            endpoints.append(endpoint)
            free_function = i >= 2 and toks[i - 1].text == "::" and toks[i - 2].text == "rclcpp"
            args = self._split_args(template_end + 2, call_end)
            if free_function:
                args = args[1:]
            self._fill(endpoint, toks[i + 2:template_end], args)
        return endpoints

    def _match_angle(self, start: int) -> Optional[int]:
        depth = 0
        for j in range(start, len(self.tokens)):
            text = self.tokens[j].text
            if text == "<":
                depth += 1
            elif text == ">":
                depth -= 1
                if depth == 0:
                    return j
            elif text in (";", "{", "}"):
                return None
        return None

    def _match(self, start: int) -> Optional[int]:
        stack = []
        for j in range(start, len(self.tokens)):
            text = self.tokens[j].text
            if text in self.PAIRS:
                stack.append(self.PAIRS[text])
            elif stack and text == stack[-1]:
                stack.pop()
                if not stack:
                    return j
            elif text in (")", "]", "}"):
                return None
        return None

    def _split_args(self, start: int, end: int) -> list:
        args: list = []
        current: list = []
        depth = 0
        for tok in self.tokens[start:end]:
            if tok.text in self.PAIRS:
                depth += 1
            elif tok.text in (")", "]", "}"):
                depth -= 1
            if tok.text == "," and depth == 0:
                args.append(current)
                current = []
            else:
                current.append(tok)
        if current:
            args.append(current)
        return args

    def _fill(self, endpoint: Endpoint, type_tokens: list, args: list) -> None:
        endpoint.msg_type = self._msg_type(type_tokens)
        if endpoint.msg_type is None:
            endpoint.reasons.append("message type not statically resolvable")
        topic = None
        if args and args[0] and all(t.kind == "string" for t in args[0]):
            topic = "".join(t.text for t in args[0])
        _set_topic(endpoint, topic)
        if len(args) < 2:
            endpoint.reasons.append("QoS argument missing")
            return
        try:
            endpoint.qos = self._qos(args[1])
        except Unresolved as exc:
            endpoint.reasons.append(f"QoS: {exc}")

    @staticmethod
    def _msg_type(tokens: list) -> Optional[str]:
        texts = [t.text for t in tokens]
        if texts and texts[0] == "::":
            texts = texts[1:]
        if len(texts) == 5 and texts[1] == "::" and texts[2] == "msg" and texts[3] == "::":
            if tokens[-1].kind == "ident" and texts[0].isidentifier():
                return f"{texts[0]}/msg/{texts[4]}"
        return None

    def _qos(self, tokens: list) -> AuditProfile:
        if len(tokens) == 1 and tokens[0].kind == "number":
            depth = _int_literal(tokens[0].text)
            if depth is None:
                raise Unresolved(f"depth literal {tokens[0].text}")
            profile = _standard("default")
            profile.depth, profile.source = depth, f"depth {depth}"
            return profile
        pos = 0
        texts = [t.text for t in tokens]
        if texts[:2] == ["rclcpp", "::"]:
            pos = 2
        if pos >= len(tokens) or tokens[pos].kind != "ident":
            raise Unresolved("QoS expression is not statically resolvable")
        ctor = tokens[pos].text
        if pos + 1 >= len(tokens) or texts[pos + 1] != "(":
            raise Unresolved(f"QoS from variable or expression '{ctor}' (C++ data-flow is not tracked)")
        close = self._local_match(tokens, pos + 1)
        ctor_args = self._local_split(tokens[pos + 2:close])
        if ctor == "QoS":
            profile = self._qos_ctor(ctor_args)
        elif ctor in CPP_STANDARD_PROFILES:
            profile = _standard(CPP_STANDARD_PROFILES[ctor])
            if ctor_args:
                self._apply_init(profile, ctor_args[0])
        elif ctor == "BestAvailableQoS":
            raise Unresolved(f"{DISTRO_SENSITIVE}: {ctor}")
        else:
            raise Unresolved(f"unrecognized QoS constructor {ctor}")
        profile.source = f"rclcpp::{ctor}"
        self._apply_chain(profile, tokens[close + 1:])
        return profile

    def _qos_ctor(self, args: list) -> AuditProfile:
        if len(args) not in (1, 2):
            raise Unresolved("rclcpp::QoS with unsupported arguments")
        base = "default"
        if len(args) == 2:
            name = "".join(t.text for t in args[1]).lstrip(":")
            if name not in CPP_RMW_PROFILES:
                raise Unresolved(f"rclcpp::QoS base profile {name}")
            base = CPP_RMW_PROFILES[name]
        profile = _standard(base)
        self._apply_init(profile, args[0])
        return profile

    def _apply_init(self, profile: AuditProfile, tokens: list) -> None:
        texts = [t.text for t in tokens]
        if len(tokens) == 1 and tokens[0].kind == "number":
            depth = _int_literal(texts[0])
            if depth is None:
                raise Unresolved(f"depth literal {texts[0]}")
            profile.history, profile.depth = KEEP_LAST, depth
            return
        if texts[:2] == ["rclcpp", "::"]:
            texts = texts[2:]
        if len(texts) == 4 and texts[0] == "KeepLast" and texts[1] == "(" and texts[3] == ")":
            depth = _int_literal(texts[2])
            if depth is None:
                raise Unresolved(f"KeepLast({texts[2]})")
            profile.history, profile.depth = KEEP_LAST, depth
            return
        if texts == ["KeepAll", "(", ")"]:
            profile.history, profile.depth = KEEP_ALL, None
            return
        raise Unresolved("QoS history initialization is not statically resolvable")

    SIMPLE_CHAIN = {
        "reliable": ("reliability", RELIABLE), "best_effort": ("reliability", BEST_EFFORT),
        "transient_local": ("durability", TRANSIENT_LOCAL),
        "durability_volatile": ("durability", VOLATILE),
    }
    POLICY_CHAIN = {"reliability": "reliability", "durability": "durability",
                    "liveliness": "liveliness"}

    def _apply_chain(self, profile: AuditProfile, tokens: list) -> None:
        pos = 0
        while pos < len(tokens):
            if (tokens[pos].text != "." or pos + 2 >= len(tokens)
                    or tokens[pos + 1].kind != "ident" or tokens[pos + 2].text != "("):
                raise Unresolved("unsupported QoS expression after constructor")
            method = tokens[pos + 1].text
            close = self._local_match(tokens, pos + 2)
            inner = tokens[pos + 3:close]
            inner_texts = [t.text for t in inner]
            if method in self.SIMPLE_CHAIN and not inner:
                policy, value = self.SIMPLE_CHAIN[method]
                setattr(profile, policy, value)
            elif method == "keep_last" and len(inner) == 1 and _int_literal(inner_texts[0]) is not None:
                profile.history, profile.depth = KEEP_LAST, _int_literal(inner_texts[0])
            elif method == "keep_all" and not inner:
                profile.history, profile.depth = KEEP_ALL, None
            elif method in self.POLICY_CHAIN and inner and inner[-1].kind == "ident":
                setattr(profile, method, _policy_member(method, inner_texts[-1]))
            elif "best_available" in method:
                raise Unresolved(f"{DISTRO_SENSITIVE}: .{method}()")
            else:
                # deadline, lifespan, liveliness_lease_duration and others are
                # not parsed in this version; never assume their values.
                raise Unresolved(f"QoS method .{method}() is not parsed")
            pos = close + 1

    @staticmethod
    def _local_match(tokens: list, start: int) -> int:
        depth = 0
        for j in range(start, len(tokens)):
            if tokens[j].text in "([{":
                depth += 1
            elif tokens[j].text in ")]}":
                depth -= 1
                if depth == 0:
                    return j
        raise Unresolved("unbalanced QoS expression")

    @staticmethod
    def _local_split(tokens: list) -> list:
        args: list = []
        current: list = []
        depth = 0
        for tok in tokens:
            if tok.text in "([{":
                depth += 1
            elif tok.text in ")]}":
                depth -= 1
            if tok.text == "," and depth == 0:
                args.append(current)
                current = []
            else:
                current.append(tok)
        if current:
            args.append(current)
        return args


# ---------------------------------------------------------------------------
# YAML override candidates
# ---------------------------------------------------------------------------

def find_yaml_candidates(path: str, text: str) -> list:
    """List qos_overrides keys. They are candidates only: whether a launch file
    loads the file and the node allows the override is not established."""
    documents = list(yaml.safe_load_all(text))  # YAMLError is handled by scan()
    candidates: list = []

    def walk(node: Any, trail: list) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "qos_overrides" and isinstance(value, dict):
                    for topic, entities in value.items():
                        if not isinstance(entities, dict):
                            continue
                        for entity, policies in entities.items():
                            if isinstance(policies, dict):
                                candidates.append({
                                    "file": path, "node": ".".join(str(t) for t in trail) or None,
                                    "topic": str(topic), "entity": str(entity),
                                    "policies": {str(k): v for k, v in policies.items()},
                                })
                else:
                    walk(value, trail + [key])
        elif isinstance(node, list):
            for item in node:
                walk(item, trail)

    for document in documents:
        walk(document, [])
    return candidates


# ---------------------------------------------------------------------------
# Scanning and pairing
# ---------------------------------------------------------------------------

@dataclass
class AuditReport:
    root: str
    scanned_files: int = 0
    endpoints: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    yaml_candidates: list = field(default_factory=list)
    pairs: list = field(default_factory=list)
    type_conflicts: list = field(default_factory=list)
    multi_type_topics: list = field(default_factory=list)
    unpaired: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)

    def counts(self) -> dict:
        def pair_count(relation: str, status: Optional[str]) -> int:
            return sum(1 for p in self.pairs if p["relation"] == relation and p["compatibility"] == status)
        return {
            "confirmed_incompatible": pair_count("confirmed", INCOMPATIBLE),
            "confirmed_indeterminate": pair_count("confirmed", INDETERMINATE),
            "confirmed_compatible": pair_count("confirmed", COMPATIBLE),
            "potential_pairs": sum(1 for p in self.pairs if p["relation"] == "potential"),
            "not_evaluated_pairs": sum(1 for p in self.pairs if p["compatibility"] is None),
            "confirmed_type_conflicts": sum(1 for c in self.type_conflicts
                                            if c["relation"] == "confirmed" and c["status"] == "definite"),
            "possible_type_conflicts": sum(1 for c in self.type_conflicts
                                           if c["relation"] != "confirmed" or c["status"] != "definite"),
            "unresolved_endpoints": len(self.unresolved),
            "incomplete_files": len(self.skipped),
        }

    def exit_code(self, strict: bool) -> int:
        c = self.counts()
        if c["confirmed_incompatible"] or c["confirmed_type_conflicts"]:
            return 1
        # Anything the audit could not establish fails only in strict mode:
        # skipped or unparsed files, unresolved endpoints (paired or not),
        # potential or unevaluated pairs, and indeterminate results.
        if strict and (c["confirmed_indeterminate"] or c["potential_pairs"] or c["not_evaluated_pairs"]
                       or c["possible_type_conflicts"] or c["unresolved_endpoints"]
                       or c["incomplete_files"]):
            return 1
        return 0


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def scan(root: Path) -> AuditReport:
    report = AuditReport(str(root))
    base = root if root.is_dir() else root.parent
    for path in iter_files(root):
        suffix = path.suffix.lower()
        if suffix not in PY_SUFFIXES | CPP_SUFFIXES | YAML_SUFFIXES:
            continue
        display = path.relative_to(base).as_posix() if path != base else path.name
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                report.skipped.append({"file": display, "reason": f"larger than {MAX_FILE_BYTES} bytes"})
                continue
            text = path.read_bytes().decode("utf-8")
        except OSError as exc:
            report.skipped.append({"file": display, "reason": f"unreadable: {exc.strerror}"})
            continue
        except UnicodeDecodeError:
            report.skipped.append({"file": display, "reason": "not valid UTF-8"})
            continue
        report.scanned_files += 1
        if suffix in PY_SUFFIXES:
            if "create_publisher" not in text and "create_subscription" not in text:
                continue
            try:
                report.endpoints.extend(PythonExtractor(display, text).run())
            except (SyntaxError, ValueError) as exc:
                line = getattr(exc, "lineno", None)
                report.skipped.append({"file": display, "reason": f"Python parse error (line {line})"})
        elif suffix in CPP_SUFFIXES:
            if "create_publisher" in text or "create_subscription" in text:
                report.endpoints.extend(CppExtractor(display, text).run())
        elif "qos_overrides" in text:
            if not HAS_YAML:
                report.skipped.append({"file": display,
                                       "reason": "PyYAML not installed; qos_overrides not parsed"})
                continue
            try:
                report.yaml_candidates.extend(find_yaml_candidates(display, text))
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                line = mark.line + 1 if mark is not None else None
                report.skipped.append({"file": display, "reason": f"YAML parse error (line {line})"})
    pair_endpoints(report)
    return report


def pair_endpoints(report: AuditReport) -> None:
    # Unresolved is a state, not a bucket: an endpoint with any unresolved
    # field is listed there and still takes part in grouping and pairing.
    report.unresolved = [e for e in report.endpoints if e.reasons]
    dynamic = [e for e in report.endpoints if e.topic_kind == "dynamic"]
    groups: dict = {}
    for endpoint in report.endpoints:
        if endpoint.topic_kind != "dynamic":
            groups.setdefault((endpoint.topic_kind, endpoint.topic), []).append(endpoint)

    for (topic_kind, topic), group in sorted(groups.items()):
        relation = "confirmed" if topic_kind == "absolute" else "potential"
        members = [e for e in group if e.msg_type is not None]
        unknown_type = [e for e in group if e.msg_type is None]
        pubs = [e for e in members if e.kind == "publisher"]
        subs = [e for e in members if e.kind == "subscription"]
        pub_types = {e.msg_type for e in pubs}
        sub_types = {e.msg_type for e in subs}
        common = pub_types & sub_types
        if pubs and subs and not common:
            incomplete = _type_set_gaps(unknown_type, dynamic, pub_types, sub_types)
            report.type_conflicts.append({
                "relation": relation, "topic": topic,
                "status": "unresolved" if incomplete else "definite",
                "publisher_types": sorted(pub_types), "subscription_types": sorted(sub_types),
                "locations": [e.location for e in group],
                "detail": ("possible type conflict: type set incomplete (" + "; ".join(incomplete) + ")"
                           if incomplete else "type conflict: no matching type in scanned scope"),
            })
            continue
        if common and len(pub_types | sub_types) > 1:
            report.multi_type_topics.append({
                "relation": relation, "topic": topic,
                "types": sorted(pub_types | sub_types), "paired_types": sorted(common),
                "detail": "multi-type topic: support and behavior differ between RMW implementations",
            })
        for endpoint in members:
            has_counterpart = endpoint.msg_type in common
            if not has_counterpart:
                report.unpaired.append(endpoint)
        for pub in pubs:
            for sub in subs:
                if pub.msg_type != sub.msg_type:
                    continue
                report.pairs.append(_pair(relation, topic, pub, sub))


def _type_set_gaps(unknown_type: list, dynamic: list, pub_types: set, sub_types: set) -> list:
    """Explain why an empty type intersection may not be final.

    An endpoint on this topic with an unresolved type, or a dynamic-topic
    endpoint whose type could complete the intersection, may be the missing
    match. Downgrading on any such dynamic endpoint is an intended
    false-negative tradeoff: a definite conflict must not rest on guesses.
    """
    gaps = [f"{e.kind} {e.location} has unresolved type" for e in unknown_type]
    for e in dynamic:
        could_match = (e.msg_type is None
                       or (e.kind == "publisher" and e.msg_type in sub_types)
                       or (e.kind == "subscription" and e.msg_type in pub_types))
        if could_match:
            gaps.append(f"dynamic-topic {e.kind} {e.location} may publish or subscribe here")
    return gaps


def _pair(relation: str, topic: str, pub: Endpoint, sub: Endpoint) -> dict:
    pair: dict = {
        "relation": relation, "topic": topic, "msg_type": pub.msg_type,
        "publisher": pub.location, "subscription": sub.location,
        "compatibility": None, "issues": [], "warnings": [], "reasons": [],
    }
    if pub.qos is None or sub.qos is None:
        missing = [e for e in (pub, sub) if e.qos is None]
        pair["reasons"] = [f"{e.kind} {e.location}: " + "; ".join(e.reasons) for e in missing]
        return pair
    result = evaluate(pub.qos, sub.qos)
    pair.update(compatibility=result.status, issues=result.issues,
                warnings=result.warnings, reasons=result.reasons)
    return pair


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

BOUNDARY = ("Static declarations only: remapping, namespaces, launch parameters, runtime "
            "QoS overrides, and RMW defaults are not observed. Confirm with `ros2 topic info -v`.")


def report_to_dict(report: AuditReport, strict: bool) -> dict:
    return {
        "tool": "qos_audit", "version": __version__, "root": report.root,
        "scanned_files": report.scanned_files, "skipped_files": report.skipped,
        "summary": report.counts(), "strict": strict, "exit_code": report.exit_code(strict),
        "pairs": report.pairs, "type_conflicts": report.type_conflicts,
        "multi_type_topics": report.multi_type_topics,
        "unpaired": [e.to_dict() for e in report.unpaired],
        "unresolved": [e.to_dict() for e in report.unresolved],
        "yaml_override_candidates": report.yaml_candidates,
        "boundary": BOUNDARY,
    }


def print_report(report: AuditReport, strict: bool) -> None:
    counts = report.counts()
    print(f"QoS audit of {report.root}: {report.scanned_files} files, "
          f"{len(report.endpoints)} endpoints")
    print("=" * 60)

    for relation in ("confirmed", "potential"):
        pairs = [p for p in report.pairs if p["relation"] == relation]
        if not pairs:
            continue
        title = "Confirmed pairs" if relation == "confirmed" else \
            "Potential pairs (same relative/private name; namespace not resolved)"
        print(f"\n{title}:")
        for p in pairs:
            status = (p["compatibility"] or "not evaluated").upper()
            print(f"  [{status}] {p['topic']} ({p['msg_type']})")
            print(f"      pub {p['publisher']}  ->  sub {p['subscription']}")
            for line in p["issues"]:
                print(f"      issue: {line}")
            for line in p["reasons"]:
                print(f"      reason: {line}")
            for line in p["warnings"]:
                print(f"      warning: {line}")

    if report.type_conflicts:
        print("\nType conflicts:")
        for c in report.type_conflicts:
            print(f"  [{c['relation'].upper()}/{c['status'].upper()}] {c['topic']}: "
                  f"publishers {c['publisher_types']} "
                  f"vs subscriptions {c['subscription_types']}")
            print(f"      {c['detail']}; at {', '.join(c['locations'])}")
    if report.multi_type_topics:
        print("\nMulti-type topics (warning):")
        for m in report.multi_type_topics:
            print(f"  {m['topic']}: {m['types']} ({m['detail']})")
    if report.unpaired:
        print("\nUnpaired endpoints (counterpart may be outside the scanned scope):")
        for e in report.unpaired:
            print(f"  {e.kind} {e.topic} ({e.msg_type}) at {e.location}")
    if report.unresolved:
        print("\nUnresolved endpoints (not inferred):")
        for e in report.unresolved:
            print(f"  {e.kind} at {e.location}: {'; '.join(e.reasons)}")
    notes = [e for e in report.endpoints if e.notes]
    if notes:
        print("\nNotes:")
        for e in notes:
            print(f"  {e.location}: {'; '.join(e.notes)}")
    if report.yaml_candidates:
        print("\nYAML QoS override candidates (not applied; load and node acceptance unverified):")
        for c in report.yaml_candidates:
            print(f"  {c['file']}: {c['topic']} {c['entity']} {c['policies']}")
    if report.skipped:
        print("\nSkipped files (incomplete scan):")
        for s in report.skipped:
            print(f"  {s['file']}: {s['reason']}")

    print("\nSummary: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"Boundary: {BOUNDARY}")
    print(f"Exit: {report.exit_code(strict)}" + (" (strict)" if strict else ""))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Statically audit ROS 2 QoS declarations across a package or workspace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Exit codes:
  0  no confirmed QoS incompatibility and no confirmed type conflict
  1  confirmed QoS incompatibility or confirmed type conflict
     (with --strict: also potential pairs, unresolved endpoints, indeterminate results,
     possible type conflicts, or skipped/unparsed files)
  2  usage error or unreadable input path

Pairs are confirmed only for identical absolute topics with the same message
type. Relative (scan) and private (~/scan) names are matched only to the same
literal and reported as potential; /scan and scan are never merged.
Indeterminate means the result depends on SYSTEM_DEFAULT/UNKNOWN policies.
YAML qos_overrides are listed as candidates and never applied.
""")
    parser.add_argument("path", help="package, workspace, or source file to scan")
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--strict", action="store_true",
                        help="also fail on potential, unresolved, indeterminate, or incomplete-scan results")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"Error: path not found: {args.path}", file=sys.stderr)
        return 2
    # The root the user named must be readable; only files found beneath a
    # directory root are reported as incomplete instead.
    needed = os.R_OK | os.X_OK if root.is_dir() else os.R_OK
    if not os.access(root, needed):
        print(f"Error: path not readable: {args.path}", file=sys.stderr)
        return 2

    report = scan(root)
    if args.json:
        print(json.dumps(report_to_dict(report, args.strict), indent=2))
    else:
        print_report(report, args.strict)
    return report.exit_code(args.strict)


if __name__ == "__main__":
    sys.exit(main())
