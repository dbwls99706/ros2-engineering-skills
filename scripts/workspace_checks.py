#!/usr/bin/env python3
"""Cross-file static checks of ROS 2 Python packages.

The other validators read one file at a time. The defects that survive a build
and stop a robot at bring-up usually sit between files: a launch file that
starts an executable setup.py does not define, a parameter file whose section
is named after another node, a remapping of a name the node never uses, a
directory that is never installed. This module reads a workspace and reports
those.

Usage:
    python workspace_checks.py path/to/workspace
    python workspace_checks.py path/to/package --json

Boundary: declarations only. Nothing is imported, built or run. A finding
means "these two files disagree as written", and each one says which files.
What cannot be decided from literals (a computed name, a class with an unknown
base, a callback group) is left alone, never guessed: silence is not a pass.
"""

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # type: ignore[import-untyped]
    HAS_YAML = True
except ImportError:  # pragma: no cover - PyYAML is a declared runtime dependency
    HAS_YAML = False

__version__ = "0.1.0"

SKIP_DIRS = {"build", "install", "log", ".git", "__pycache__", "node_modules", ".venv", "venv",
             ".tox", ".pytest_cache", ".mypy_cache"}
MAX_PACKAGES = 40
MAX_FILES_PER_PACKAGE = 300
MAX_FILE_BYTES = 512 * 1024
# Directories a launch file commonly loads from the installed share directory.
RESOURCE_DIRS = ("launch", "config", "params", "rviz", "urdf", "maps", "worlds", "meshes",
                 "description")
# Imports that are ROS packages by name. Interface packages follow a naming
# rule; the rest is a short list of libraries that are always separate packages.
ROS_IMPORTS = {"rclpy", "tf2_ros", "tf2_geometry_msgs", "cv_bridge", "message_filters",
               "image_transport", "rclpy_action"}
INTERFACE_SUFFIXES = ("_msgs", "_srvs", "_interfaces")
# A test dependency is still a declaration: the package says it knows the name.
DEPEND_TAGS = ("depend", "exec_depend", "build_depend", "build_export_depend", "run_depend",
               "test_depend")
# Interface packages rclpy itself depends on. A package that declares rclpy
# gets them with it, so importing them is not an undeclared dependency.
WITH_RCLPY = {"rcl_interfaces", "builtin_interfaces", "action_msgs", "unique_identifier_msgs",
              "rosgraph_msgs", "lifecycle_msgs", "service_msgs", "type_description_interfaces"}
# Parameters every node has without declaring them in its own source.
IMPLICIT_PARAMETERS = {"use_sim_time", "qos_overrides", "start_type_description_service"}
LIFECYCLE_MARKERS = ("LifecycleNode", "lifecycle_manager", "ChangeState", "lifecycle_msgs",
                     "change_state", "EmitEvent", "autostart")
SPIN_CALLS = {"spin", "spin_once", "spin_until_future_complete"}


@dataclass
class Package:
    root: Path
    name: str
    depends: set = field(default_factory=set)
    setup_text: Optional[str] = None
    console_scripts: Optional[dict] = None  # None: not a literal, so not known
    entry_points_line: int = 1
    cmake_text: Optional[str] = None
    files: list = field(default_factory=list)


@dataclass
class LaunchNode:
    file: Path
    line: int
    package: Optional[str]
    executable: Optional[str]
    name: Optional[str]
    name_given: bool
    parameters: list
    remappings: list
    lifecycle_action: bool


def _read(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse(source: Optional[str]) -> Optional[ast.AST]:
    if source is None:
        return None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _text(value: ast.AST) -> Optional[str]:
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _argument(call: ast.Call, index: int, name: str) -> Optional[ast.AST]:
    if len(call.args) > index:
        return call.args[index]
    return _keyword(call, name)


def _opaque(call: ast.Call) -> bool:
    """The call has *args or **kwargs, so its arguments cannot be read."""
    return (any(isinstance(arg, ast.Starred) for arg in call.args)
            or any(kw.arg is None for kw in call.keywords))


# ---------------------------------------------------------------- discovery

def find_packages(root: Path) -> list:
    """ROS packages under *root*: directories that hold a package.xml."""
    packages = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if "package.xml" not in names:
            continue
        package = _load_package(Path(current))
        if package is not None:
            packages.append(package)
            dirs[:] = []  # a package does not contain packages
        if len(packages) >= MAX_PACKAGES:
            break
    return packages


def _load_package(root: Path) -> Optional[Package]:
    manifest = _read(root / "package.xml")
    if manifest is None:
        return None
    try:
        tree = ElementTree.fromstring(manifest)
    except ElementTree.ParseError:
        return None
    name = (tree.findtext("name") or "").strip()
    if not name:
        return None
    package = Package(root=root, name=name)
    for tag in DEPEND_TAGS:
        package.depends.update((item.text or "").strip() for item in tree.iter(tag))
    package.cmake_text = _read(root / "CMakeLists.txt")
    package.setup_text = _read(root / "setup.py")
    if package.setup_text is not None:
        package.console_scripts, package.entry_points_line = _console_scripts(package.setup_text)
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        package.files.extend(Path(current) / name for name in sorted(names))
        if len(package.files) > MAX_FILES_PER_PACKAGE:
            break
    return package


def _console_scripts(setup_text: str) -> tuple:
    """({name: (module, function)}, line) from a literal entry_points, else (None, 1)."""
    tree = _parse(setup_text)
    if tree is None:
        return None, 1
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and _call_name(call) == "setup"):
            continue
        entry = _keyword(call, "entry_points")
        if not isinstance(entry, ast.Dict):
            return None, 1
        for key, value in zip(entry.keys, entry.values):
            if key is None or _text(key) != "console_scripts":
                continue
            if not isinstance(value, (ast.List, ast.Tuple)):
                return None, entry.lineno
            scripts = {}
            for item in value.elts:
                match = re.fullmatch(r"\s*([\w.-]+)\s*=\s*([\w.]+)\s*:\s*(\w+)\s*", _text(item) or "")
                if match is None:
                    return None, entry.lineno
                scripts[match.group(1)] = (match.group(2), match.group(3))
            return scripts, entry.lineno
        return {}, entry.lineno
    return None, 1


def launch_files(package: Package) -> list:
    return [path for path in package.files
            if path.name.endswith(".launch.py")
            or (path.suffix == ".py" and "launch" in path.relative_to(package.root).parts[:-1])]


def _assignments(tree: ast.AST) -> dict:
    """name -> value for names assigned exactly once anywhere in the file."""
    seen: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name):
            seen.setdefault(node.targets[0].id, []).append(node.value)
    return {name: values[0] for name, values in seen.items() if len(values) == 1}


def _constants(node: Optional[ast.AST], names: dict, depth: int = 3) -> list:
    """String literals inside *node*, following names assigned once."""
    if node is None:
        return []
    found = []
    for item in ast.walk(node):
        text = _text(item)
        if text is not None:
            found.append(text)
        elif isinstance(item, ast.Name) and depth and item.id in names:
            found.extend(_constants(names[item.id], names, depth - 1))
    return found


def launch_nodes(package: Package) -> list:
    """Every Node(...) action written in the package's launch files."""
    nodes = []
    for path in launch_files(package):
        tree = _parse(_read(path))
        if tree is None:
            continue
        names = _assignments(tree)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or _call_name(call) not in ("Node", "LifecycleNode"):
                continue
            if _opaque(call):
                continue
            name = _keyword(call, "name")
            namespace = _keyword(call, "namespace")
            if namespace is not None and _text(namespace) is None:
                continue  # a computed namespace: the full name is not known
            parameters = _keyword(call, "parameters")
            remappings = _keyword(call, "remappings")
            nodes.append(LaunchNode(
                file=path, line=call.lineno,
                package=_text(_keyword(call, "package") or ast.Constant(None)),
                executable=_text(_keyword(call, "executable") or ast.Constant(None)),
                name=_text(name) if name is not None else None,
                name_given=name is not None,
                parameters=[(element, _constants(element, names)) for element in parameters.elts]
                if isinstance(parameters, (ast.List, ast.Tuple)) else [],
                remappings=[tuple(_text(part) for part in pair.elts)
                            for pair in remappings.elts
                            if isinstance(pair, (ast.Tuple, ast.List)) and len(pair.elts) == 2]
                if isinstance(remappings, (ast.List, ast.Tuple)) else [],
                lifecycle_action=_call_name(call) == "LifecycleNode"))
    return nodes


def node_source(package: Package, node: LaunchNode) -> Optional[Path]:
    """The module a launch Node of this package runs, when setup.py says so."""
    if node.package != package.name or not package.console_scripts or not node.executable:
        return None
    target = package.console_scripts.get(node.executable)
    return _module_file(package, target[0]) if target else None


def _module_file(package: Package, module: str) -> Optional[Path]:
    base = package.root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


# ------------------------------------------------------------------- checks

def _issue(package: Package, path: Path, line: int, severity: str, check: str, message: str) -> dict:
    try:
        shown = str(path.relative_to(package.root.parent))
    except ValueError:
        shown = str(path)
    return {"file": shown, "line": line, "severity": severity, "check": check, "message": message}


def check_install_rules(package: Package) -> list:
    """A directory a launch file needs is never installed."""
    if package.setup_text is not None:
        installer, text = package.root / "setup.py", package.setup_text
    elif package.cmake_text is not None:
        installer, text = package.root / "CMakeLists.txt", package.cmake_text
    else:
        return []
    sources = [(path, _read(path) or "") for path in launch_files(package)]
    issues = []
    for directory in RESOURCE_DIRS:
        folder = package.root / directory
        members = [path for path in package.files if folder in path.parents]
        if not members or re.search(r"\b%s\b" % directory, text):
            continue
        if directory == "launch":
            needed = bool(sources)
        else:
            needed = any(re.search(r"""['"]%s['"/]""" % directory, source) for _path, source in sources)
        if not needed:
            continue
        example = members[0].relative_to(package.root)
        issues.append(_issue(
            package, installer, 1, "warning", "install-rules",
            f"{directory}/ is not installed: {installer.name} has no entry for it, so {example} "
            f"is missing from the installed package and `ros2 launch {package.name} ...` "
            "cannot find it."))
    return issues


def check_executables(package: Package, nodes: list) -> list:
    """Launch files and setup.py must agree on what can be run."""
    scripts = package.console_scripts
    if scripts is None:
        return []
    issues = []
    for node in nodes:
        if node.package == package.name and node.executable and node.executable not in scripts:
            defined = ", ".join(sorted(scripts)) or "none"
            issues.append(_issue(
                package, node.file, node.line, "error", "launch-executable",
                f"executable '{node.executable}' is not a console script of {package.name} "
                f"(setup.py defines: {defined}); the launch fails when it starts this node."))
    setup = package.root / "setup.py"
    for name, (module, function) in sorted(scripts.items()):
        if not (package.root / module.split(".")[0]).is_dir():
            continue  # points outside this package: not checked
        path = _module_file(package, module)
        if path is None:
            issues.append(_issue(
                package, setup, package.entry_points_line, "error", "entry-point",
                f"console script '{name}' points at module {module}, which does not exist in "
                "this package; the executable fails with ModuleNotFoundError."))
            continue
        tree = _parse(_read(path))
        if tree is None or _defines(tree, function):
            continue
        issues.append(_issue(
            package, setup, package.entry_points_line, "error", "entry-point",
            f"console script '{name}' calls {module}:{function}, but "
            f"{path.relative_to(package.root)} defines no '{function}'."))
    return issues


def _defines(tree: ast.AST, name: str) -> bool:
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                (alias.asname or alias.name) in (name, "*") for alias in node.names):
            return True
    return False


def _own_name(tree: ast.AST) -> Optional[str]:
    """The name a node gives itself: the literal passed to super().__init__."""
    names = set()
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "__init__" and call.args):
            text = _text(call.args[0])
            if text is not None:
                names.add(text)
    return names.pop() if len(names) == 1 else None


def _declared_parameters(source: str, tree: ast.AST) -> Optional[set]:
    """Names the module declares, or None when that cannot be read off the file."""
    if "automatically_declare_parameters_from_overrides" in source or (
            "allow_undeclared_parameters" in source):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {getattr(base, "attr", getattr(base, "id", "?")) for base in node.bases}
            if bases - {"Node", "LifecycleNode", "object"}:
                return None  # parameters may be declared by a base class elsewhere
    declared = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _opaque(call):
            continue
        if _call_name(call) == "declare_parameter":
            name = _text(_argument(call, 0, "name") or ast.Constant(None))
            if name is None:
                return None
            declared.add(name)
        elif _call_name(call) == "declare_parameters":
            listed = _argument(call, 1, "parameters")
            if not isinstance(listed, (ast.List, ast.Tuple)):
                return None
            for item in listed.elts:
                first = item.elts[0] if isinstance(item, (ast.Tuple, ast.List)) and item.elts else None
                name = _text(first) if first is not None else None
                if name is None:
                    return None
                declared.add(name)
    return declared


def _line_of(text: str, key: str) -> int:
    match = re.search(r"^[ \t]*%s[ \t]*:" % re.escape(key), text, re.M)
    return text.count("\n", 0, match.start()) + 1 if match else 1


def check_parameter_files(package: Package, nodes: list) -> tuple:
    """A parameter file must have a section for the node it is given to."""
    issues, skipped = [], []
    by_name: dict = {}
    for path in package.files:
        if path.suffix in (".yaml", ".yml"):
            by_name.setdefault(path.name, []).append(path)
    loaded: dict = {}
    for node in nodes:
        source_path = node_source(package, node)
        source = _read(source_path) if source_path else None
        tree = _parse(source)
        name = node.name if node.name_given else (_own_name(tree) if tree is not None else None)
        if name is None:
            continue
        if any(isinstance(element, ast.Dict) for element, _constants in node.parameters):
            # The node is also configured inline. A shared file with no
            # section for it is then a normal arrangement, not an oversight.
            continue
        for element, constants in node.parameters:
            for basename in {os.path.basename(text) for text in constants
                             if text.endswith((".yaml", ".yml"))}:
                candidates = by_name.get(basename, [])
                if len(candidates) != 1:
                    continue
                path = candidates[0]
                if not HAS_YAML:
                    skipped.append("parameter files: PyYAML is not installed")
                    continue
                if path not in loaded:
                    text = _read(path)
                    try:
                        loaded[path] = (text, yaml.safe_load(text) if text is not None else None)
                    except yaml.YAMLError:
                        loaded[path] = (text, None)
                text, data = loaded[path]
                if not isinstance(data, dict):
                    continue
                sections = {str(key): value for key, value in data.items()
                            if isinstance(value, dict) and "ros__parameters" in value}
                if not sections:
                    continue
                matching = [key for key in sections if _section_matches(key, name)]
                if not matching:
                    issues.append(_issue(
                        package, node.file, node.line, "warning", "parameter-section",
                        f"node '{name}' is given {path.relative_to(package.root)}, which has no "
                        f"section for that name (sections: {', '.join(sorted(sections))}); its "
                        "parameters stay at the defaults in the code."))
                    continue
                declared = _declared_parameters(source, tree) if tree is not None and source else None
                if declared is None or source_path is None:
                    continue
                for key in matching:
                    values = sections[key]["ros__parameters"]
                    if not isinstance(values, dict):
                        continue
                    unknown = sorted(str(param) for param, value in values.items()
                                     if str(param) not in declared | IMPLICIT_PARAMETERS
                                     and not isinstance(value, dict))
                    for param in unknown:
                        issues.append(_issue(
                            package, path, _line_of(text or "", param), "warning",
                            "parameter-name",
                            f"section '{key}' sets '{param}', which "
                            f"{source_path.relative_to(package.root)} does not declare (it "
                            f"declares: {', '.join(sorted(declared)) or 'nothing'}); the value "
                            "is ignored."))
    return issues, sorted(set(skipped))


def _section_matches(key: str, name: str) -> bool:
    key = key.strip("/")
    return "*" in key or key == name or key.endswith("/" + name)


def check_dependencies(package: Package) -> list:
    """ROS packages the code imports must be declared in package.xml."""
    imported: set = set()
    for path in package.files:
        relative = path.relative_to(package.root).parts
        if path.suffix != ".py" or path.name == "setup.py" or relative[0] in ("launch", "test", "tests"):
            continue
        tree = _parse(_read(path))
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module.split(".")[0])
    needed = {name for name in imported
              if name in ROS_IMPORTS or name.endswith(INTERFACE_SUFFIXES)}
    if "rclpy" in package.depends:
        needed -= WITH_RCLPY
    missing = sorted(needed - package.depends - {package.name})
    if not missing:
        return []
    them = "it" if len(missing) == 1 else "them"
    return [_issue(
        package, package.root / "package.xml", 1, "warning", "dependencies",
        f"the code imports {', '.join(missing)}, but package.xml declares no dependency on "
        f"{them}. Unless another dependency brings {them} in, rosdep does not install {them} "
        "and a clean machine fails at import.")]


def _comment(source: str, first: int, last: int) -> str:
    """Comment text on source lines *first*..*last* (1-based, inclusive)."""
    parts = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and first <= token.start[0] <= last:
                parts.append(token.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return ""
    return " ".join(parts)


def _self_attribute(node: Optional[ast.AST]) -> Optional[str]:
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    return None


def check_node_source(package: Package, path: Path) -> list:
    """rclpy calls that are wrong as written, whatever the rest of the system does."""
    source = _read(path)
    tree = _parse(source)
    if tree is None or source is None or "rclpy" not in source:
        return []
    issues = []

    def add(node, severity, check, message):
        issues.append(_issue(package, path, node.lineno, severity, check, message))

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _opaque(call):
            continue
        name = _call_name(call)
        if name == "create_subscription" and len(call.args) < 4 and _keyword(call, "qos_profile") is None:
            add(call, "error", "missing-qos",
                "create_subscription() is called without a QoS profile or depth; rclpy raises "
                "TypeError when the node is constructed.")
        elif name == "create_publisher" and len(call.args) < 3 and _keyword(call, "qos_profile") is None:
            add(call, "error", "missing-qos",
                "create_publisher() is called without a QoS profile or depth; rclpy raises "
                "TypeError when the node is constructed.")
        elif name == "create_timer" and call.args:
            period = call.args[0]
            if isinstance(period, ast.Constant) and isinstance(period.value, (int, float)) and not isinstance(
                    period.value, bool):
                issue = _timer_period(call, float(period.value), source)
                if issue:
                    add(call, "warning", "timer-period", issue)

    # Whether a blocking call inside a callback deadlocks depends on the
    # executor and the callback groups. With either in play it is not decided.
    if re.search(r"MultiThreadedExecutor|CallbackGroup|callback_group", source):
        return issues
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        callbacks: set = set()
        clients: set = set()
        for node in ast.walk(cls):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _call_name(
                    node.value) == "create_client":
                clients.update(filter(None, (_self_attribute(target) for target in node.targets)))
            if not isinstance(node, ast.Call) or _opaque(node):
                continue
            position = {"create_subscription": 2, "create_timer": 1, "create_service": 2}.get(
                _call_name(node))
            if position is not None:
                callback = _self_attribute(_argument(node, position, "callback"))
                if callback:
                    callbacks.add(callback)
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef) or method.name not in callbacks:
                continue
            for call in ast.walk(method):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                func = call.func
                if func.attr == "call" and _self_attribute(func.value) in clients:
                    add(call, "warning", "blocking-call",
                        f"synchronous service call inside the callback {method.name}(): the "
                        "executor running this callback is the one that must deliver the "
                        "response, so with the default single-threaded executor the call never "
                        "returns. Use call_async() and handle the future.")
                elif func.attr in SPIN_CALLS and isinstance(func.value, ast.Name) and (
                        func.value.id == "rclpy"):
                    add(call, "warning", "blocking-call",
                        f"rclpy.{func.attr}() inside the callback {method.name}(): the node is "
                        "already being spun by the executor that called this callback. Use "
                        "call_async() with add_done_callback(), or a separate callback group "
                        "and executor.")
    return issues


def _timer_period(call: ast.Call, period: float, source: str) -> Optional[str]:
    """A period that contradicts the rate written next to it."""
    comment = _comment(source, call.lineno, getattr(call, "end_lineno", call.lineno) or call.lineno)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(hz|ms)\b", comment, re.I)
    if match is None:
        return None
    value, unit = float(match.group(1)), match.group(2).lower()
    if value <= 0:
        return None
    expected = 1.0 / value if unit == "hz" else value / 1000.0
    if abs(period - expected) <= 0.05 * expected:
        return None
    return (f"create_timer({period:g}, ...) is a period of {period:g} seconds, but the comment "
            f"says {match.group(1)} {match.group(2)}, which is {expected:g} s. The period "
            "argument is in seconds.")


def _names_used(tree: ast.AST) -> Optional[set]:
    """Topic, service and action names the module creates, or None if any is computed."""
    used = set()
    positions = {"create_subscription": (1, "topic"), "create_publisher": (1, "topic"),
                 "create_client": (1, "srv_name"), "create_service": (1, "srv_name"),
                 "ActionClient": (2, "action_name"), "ActionServer": (2, "action_name")}
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _call_name(call) not in positions:
            continue
        if _opaque(call):
            return None
        index, keyword = positions[_call_name(call)]
        value = _argument(call, index, keyword)
        text = _text(value) if value is not None else None
        if text is None:
            return None
        used.add(text)
    return used


def _plain(name: str) -> str:
    return re.sub(r"^(~/|/)", "", name)


def check_launch_against_source(package: Package, nodes: list) -> list:
    """What a launch file assumes about a node of this package, against its source."""
    issues = []
    launch_text = {path: _read(path) or "" for path in {node.file for node in nodes}}
    for node in nodes:
        path = node_source(package, node)
        source = _read(path) if path else None
        tree = _parse(source)
        if tree is None or path is None or source is None:
            continue
        used = _names_used(tree)
        if used:
            plain = {_plain(name) for name in used}
            for origin, target in node.remappings:
                if origin is None or origin.startswith("__") or _plain(origin) in plain:
                    continue
                issues.append(_issue(
                    package, node.file, node.line, "warning", "remapping",
                    f"remaps '{origin}' to '{target}', but {path.relative_to(package.root)} "
                    f"creates no topic, service or action with that name (it uses: "
                    f"{', '.join(sorted(used))}); the remapping has no effect."))
        lifecycle = any(
            isinstance(cls, ast.ClassDef) and any(
                getattr(base, "attr", getattr(base, "id", "")) == "LifecycleNode" for base in cls.bases)
            for cls in ast.walk(tree))
        if (lifecycle and not node.lifecycle_action
                and not re.search(r"trigger_configure|trigger_activate", source)
                and not any(marker in launch_text[node.file] for marker in LIFECYCLE_MARKERS)):
            issues.append(_issue(
                package, node.file, node.line, "warning", "lifecycle",
                f"'{node.executable}' is a lifecycle node, and nothing in this launch file "
                "configures and activates it. Unless something else manages it, it stays "
                "unconfigured and its publishers and timers never start."))
    return issues


def check_qos(root: Path) -> tuple:
    """Publisher and subscription declarations that cannot work together."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import qos_audit
    except ImportError:  # pragma: no cover - the audit ships next to this file
        return [], ["qos: qos_audit.py is not available"]
    finally:
        sys.path.pop(0)
    report = qos_audit.scan(root)  # scan() pairs the endpoints it finds
    issues = []
    for pair in report.pairs:
        if pair["compatibility"] != qos_audit.INCOMPATIBLE:
            continue
        file, _, line = pair["subscription"].rpartition(":")
        scope = ("" if pair["relation"] == "confirmed"
                 else " if both are the same topic at runtime (namespaces and remapping are "
                      "not resolved)")
        issues.append({
            "file": file, "line": int(line) if line.isdigit() else 0, "severity": "warning",
            "check": "qos-incompatible",
            "message": f"topic '{pair['topic']}': {'; '.join(pair['issues'])}. The publisher at "
                       f"{pair['publisher']} and this subscription do not connect{scope}, and "
                       "no data arrives."})
    for sub in report.endpoints:
        if sub.kind != "subscription" or sub.reasons or sub.qos is None or sub.msg_type is None:
            continue
        if sub.qos.durability != qos_audit.VOLATILE or sub.topic_kind == "dynamic":
            continue
        if {"test", "tests"} & set(Path(sub.file).parts):
            continue  # a test sets up its endpoints in a known order
        for pub in report.endpoints:
            if (pub.kind == "publisher" and not pub.reasons and pub.qos is not None
                    and (pub.topic_kind, pub.topic, pub.msg_type) == (
                        sub.topic_kind, sub.topic, sub.msg_type)
                    and pub.qos.durability == qos_audit.TRANSIENT_LOCAL):
                issues.append({
                    "file": sub.file, "line": sub.line, "severity": "info", "check": "qos-late-joiner",
                    "message": f"topic '{sub.topic}': the publisher at {pub.location} keeps its "
                               "last sample for late joiners (TRANSIENT_LOCAL), but this "
                               "subscription is VOLATILE and does not ask for it. If it can "
                               "start after the publish, it never receives that sample."})
                break
    return issues, []


def check_workspace(root) -> tuple:
    """(issues, skipped, packages) for every ROS package found under *root*."""
    root = Path(root).resolve()
    issues, skipped = [], []
    packages = find_packages(root)
    for package in packages:
        nodes = launch_nodes(package)
        issues.extend(check_install_rules(package))
        issues.extend(check_executables(package, nodes))
        found, missed = check_parameter_files(package, nodes)
        issues.extend(found)
        skipped.extend(missed)
        issues.extend(check_dependencies(package))
        issues.extend(check_launch_against_source(package, nodes))
        launch = set(launch_files(package))
        for path in package.files:
            if path.suffix == ".py" and path not in launch and path.name != "setup.py":
                issues.extend(check_node_source(package, path))
    if packages:
        found, missed = check_qos(root)
        for issue in found:
            issue["file"] = _relative(issue["file"], root, packages)
        issues.extend(found)
        skipped.extend(missed)
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda issue: (order.get(issue["severity"], 3), issue["file"], issue["line"]))
    return issues, sorted(set(skipped)), packages


def _relative(file: str, root: Path, packages: list) -> str:
    path = (root / file).resolve() if not os.path.isabs(file) else Path(file)
    for package in packages:
        try:
            return str(path.relative_to(package.root.parent))
        except ValueError:
            continue
    return file


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", help="workspace or package directory")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if not os.path.isdir(args.path):
        print(f"not a directory: {args.path}", file=sys.stderr)
        return 2
    issues, skipped, packages = check_workspace(args.path)
    if args.json:
        print(json.dumps({"tool": "workspace_checks", "version": __version__,
                          "packages": [package.name for package in packages],
                          "issues": issues, "checks_skipped": skipped}, indent=2))
    else:
        print(f"Checked {len(packages)} package(s)")
        for issue in issues:
            print(f"  [{issue['severity'].upper():7s}] {issue['file']}:{issue['line']}: "
                  f"{issue['message']}")
        for item in skipped:
            print(f"  [SKIPPED] {item}")
        if not issues:
            print("No cross-file findings. This is not a pass: only literal declarations are read.")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
