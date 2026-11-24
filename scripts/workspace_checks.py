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
import functools
import io
import json
import os
import re
import sys
import time
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

__version__ = "0.1.1"

SKIP_DIRS = {"build", "install", "log", ".git", "__pycache__", "node_modules", ".venv", "venv",
             ".tox", ".pytest_cache", ".mypy_cache"}
MAX_PACKAGES = 200
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
# Python modules whose ROS package has another name.
PROVIDED_BY = {"tf2_ros": {"tf2_ros", "tf2_ros_py"}}
# A test dependency is still a declaration: the package says it knows the name.
DEPEND_TAGS = ("depend", "exec_depend", "build_depend", "build_export_depend", "run_depend",
               "test_depend")
# Interface packages rclpy itself depends on. A package that declares rclpy
# gets them with it, so importing them is not an undeclared dependency.
WITH_RCLPY = {"rcl_interfaces", "builtin_interfaces", "action_msgs", "unique_identifier_msgs",
              "rosgraph_msgs", "lifecycle_msgs", "service_msgs", "type_description_interfaces"}
# Parameters every node has without declaring them in its own source.
# Third-party modules and the rosdep key that installs them (rosdistro,
# rosdep/python.yaml). The second item is what a declared dependency contains
# when it provides the module under another key.
ROSDEP_KEYS = {
    "yaml": ("python3-yaml", ("yaml",)),
    "numpy": ("python3-numpy", ("numpy",)),
    "cv2": ("python3-opencv", ("opencv", "cv_bridge")),
    "scipy": ("python3-scipy", ("scipy",)),
    "serial": ("python3-serial", ("serial",)),
    "requests": ("python3-requests", ("requests",)),
    "matplotlib": ("python3-matplotlib", ("matplotlib",)),
    "PIL": ("python3-pil", ("pil", "pillow", "imaging")),
    "psutil": ("python3-psutil", ("psutil",)),
    "transforms3d": ("python3-transforms3d", ("transforms3d", "tf_transformations")),
    "lxml": ("python3-lxml", ("lxml",)),
    "netifaces": ("python3-netifaces", ("netifaces",)),
    "pydot": ("python3-pydot", ("pydot",)),
    "smbus": ("python3-smbus", ("smbus",)),
    "zmq": ("python3-zmq", ("zmq",)),
    "websockets": ("python3-websockets", ("websockets",)),
    "pandas": ("python3-pandas", ("pandas",)),
    "shapely": ("python3-shapely", ("shapely",)),
    "sklearn": ("python3-sklearn", ("sklearn", "scikit")),
    "skimage": ("python3-skimage", ("skimage", "scikit")),
    "jinja2": ("python3-jinja2", ("jinja",)),
    "h5py": ("python3-h5py", ("h5py",)),
    "pexpect": ("python3-pexpect", ("pexpect",)),
}
# What a package that declares rclpy gets with it: rclpy depends on
# python3-yaml, and the generated Python messages depend on python3-numpy.
KEYS_WITH_RCLPY = {"yaml", "numpy"}
IMPLICIT_PARAMETERS = {"use_sim_time", "qos_overrides", "start_type_description_service"}
LIFECYCLE_MARKERS = ("LifecycleNode", "lifecycle_manager", "ChangeState", "lifecycle_msgs",
                     "change_state", "EmitEvent", "autostart")
SPIN_CALLS = {"spin", "spin_once", "spin_until_future_complete"}
# Parameter names of rclpy.node.Node methods: the union over Humble, Jazzy,
# Kilted, Lyrical and Rolling. A keyword outside the set exists in none of them.
RCLPY_KEYWORDS = {
    "create_timer": {"timer_period_sec", "callback", "callback_group", "clock", "autostart",
                     "timer_class"},
    "create_publisher": {"msg_type", "topic", "qos_profile", "callback_group", "event_callbacks",
                         "qos_overriding_options", "publisher_class"},
    "create_subscription": {"msg_type", "topic", "callback", "qos_profile", "callback_group",
                            "event_callbacks", "qos_overriding_options", "raw",
                            "content_filter_options", "subscription_class",
                            "acceptable_buffer_backends"},
    "create_service": {"srv_type", "srv_name", "callback", "qos_profile", "callback_group",
                       "service_class"},
    "create_client": {"srv_type", "srv_name", "qos_profile", "callback_group", "client_class"},
    "declare_parameter": {"name", "value", "descriptor", "ignore_override"},
}
# How many positional arguments rclpy passes to a callback, as (fewest, most)
# over the supported distributions. A subscription callback may take the
# message info from Jazzy on; a signature that only some distributions accept
# is not judged. A timer callback is called without arguments, except that
# from Kilted on a parameter annotated TimerInfo is filled in by keyword.
CALLBACK_ARGUMENTS = {
    "create_subscription": (2, "callback", 1, 2, "the message"),
    "create_timer": (1, "callback", 0, 0, "no argument"),
    "create_service": (2, "callback", 2, 2, "the request and the response"),
}
# Endpoint constructors: (index of the name, keyword of the name, index of the
# type, keyword of the type, kind, role).
ENDPOINTS = {
    "create_publisher": (1, "topic", 0, "msg_type", "topic", "offer"),
    "create_subscription": (1, "topic", 0, "msg_type", "topic", "use"),
    "create_service": (1, "srv_name", 0, "srv_type", "service", "offer"),
    "create_client": (1, "srv_name", 0, "srv_type", "service", "use"),
    "ActionServer": (2, "action_name", 1, "action_type", "action", "offer"),
    "ActionClient": (2, "action_name", 1, "action_type", "action", "use"),
}
# Members every generated interface class has besides its fields.
INTERFACE_MEMBERS = {"get_fields_and_field_types", "SLOT_TYPES", "Request", "Response", "Goal",
                     "Result", "Feedback", "Impl", "Event"}
TEXT_SUFFIXES = {".py", ".cpp", ".cc", ".cxx", ".hpp", ".h", ".yaml", ".yml", ".xml", ".json"}


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
    truncated: bool = False  # more files than MAX_FILES_PER_PACKAGE: not all were read


@dataclass
class LaunchNode:
    file: Path
    line: int
    package: Optional[str]
    executable: Optional[str]
    name: Optional[str]
    name_given: bool
    namespace: Optional[str]
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


@functools.lru_cache(maxsize=4096)
def _parse(source: Optional[str]) -> Optional[ast.AST]:
    """The syntax tree of *source*. Cached by content: several checks read the same file."""
    if source is None:
        return None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


@functools.lru_cache(maxsize=4096)
def _nodes(tree: ast.AST) -> tuple:
    """Every node of *tree*, walked once however many checks ask."""
    return tuple(ast.walk(tree))


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

def _expired(deadline: Optional[float]) -> bool:
    """*deadline* is a time.monotonic() value; None means no limit."""
    return deadline is not None and time.monotonic() >= deadline


def find_packages(root: Path) -> list:
    """ROS packages under *root*: directories that hold a package.xml."""
    return _discover(root)[0]


def _discover(root: Path, deadline: Optional[float] = None) -> tuple:
    """(packages, notes). A note says where the search stopped short of the whole tree."""
    packages: list = []
    notes = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if _expired(deadline):
            notes.append("time limit reached while looking for packages: the directory tree was "
                         "only partly searched")
            break
        if "package.xml" not in names:
            continue
        if len(packages) >= MAX_PACKAGES:
            notes.append(f"more than {MAX_PACKAGES} packages: only the first {MAX_PACKAGES} "
                         "were checked")
            break
        package = _load_package(Path(current))
        if package is not None:
            packages.append(package)
            dirs[:] = []  # a package does not contain packages
            if package.truncated:
                notes.append(f"{package.name}: more than {MAX_FILES_PER_PACKAGE} files, not all "
                             "were read")
    return packages, notes


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
        if len(package.files) > MAX_FILES_PER_PACKAGE:
            package.truncated = True
            break
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        package.files.extend(Path(current) / name for name in sorted(names))
    return package


def _console_scripts(setup_text: str) -> tuple:
    """({name: (module, function)}, line) from a literal entry_points, else (None, 1)."""
    tree = _parse(setup_text)
    if tree is None:
        return None, 1
    for call in _nodes(tree):
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


def _literal_strings(node: ast.AST, names: dict, depth: int = 4) -> Optional[list]:
    """Every string *node* is built from, or None when part of it is not written out."""
    called = {id(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)}
    owners = {id(item.value) for item in ast.walk(node) if isinstance(item, ast.Attribute)}
    found = []
    for item in ast.walk(node):
        if isinstance(item, (ast.Starred, ast.ListComp, ast.GeneratorExp, ast.IfExp, ast.Lambda)):
            return None
        if isinstance(item, ast.Call) and (_call_name(item) not in ("glob", "iglob", "join") or _opaque(item)):
            return None
        if isinstance(item, ast.Name) and id(item) not in called and id(item) not in owners:
            if item.id not in names or not depth:
                return None
            inner = _literal_strings(names[item.id], names, depth - 1)
            if inner is None:
                return None
            found.extend(inner)
        text = _text(item)
        if text is not None:
            found.append(text)
    return found


def _installed_text(setup_text: str) -> str:
    """The text that says what setup.py installs.

    The strings of `data_files` when it is a list written out in the call to
    `setup()` and the file mentions `data_files` nowhere else; the whole file
    otherwise, so that a list built elsewhere is never read as empty.
    """
    tree = _parse(setup_text)
    if tree is None or len(re.findall(r"\bdata_files\b", setup_text)) != 1:
        return setup_text
    for call in _nodes(tree):
        if not (isinstance(call, ast.Call) and _call_name(call) == "setup") or _opaque(call):
            continue
        listed = _keyword(call, "data_files")
        if not isinstance(listed, (ast.List, ast.Tuple)):
            return setup_text
        strings = _literal_strings(listed, _assignments(tree))
        return setup_text if strings is None else "\n".join(strings)
    return setup_text


def launch_files(package: Package) -> list:
    return [path for path in package.files
            if path.name.endswith(".launch.py")
            or (path.suffix == ".py" and "launch" in path.relative_to(package.root).parts[:-1])]


def _assignments(tree: ast.AST) -> dict:
    """name -> value for names assigned exactly once anywhere in the file."""
    seen: dict = {}
    for node in _nodes(tree):
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


def _configuration(node: ast.AST) -> Optional[str]:
    """The launch argument a `LaunchConfiguration('name')` reads, else None."""
    if isinstance(node, ast.Call) and _call_name(node) == "LaunchConfiguration" and not _opaque(node):
        name = _argument(node, 0, "variable_name")
        return _text(name) if name is not None else None
    return None


def _argument_defaults(tree: ast.AST) -> dict:
    """name -> default value for launch arguments the file declares exactly once."""
    seen: dict = {}
    for call in _nodes(tree):
        if not isinstance(call, ast.Call) or _call_name(call) != "DeclareLaunchArgument" or _opaque(call):
            continue
        name = _argument(call, 0, "name")
        text = _text(name) if name is not None else None
        if text is not None:
            seen.setdefault(text, []).append(_keyword(call, "default_value"))
    return {name: values[0] for name, values in seen.items()
            if len(values) == 1 and values[0] is not None}


def launch_nodes(package: Package) -> list:
    """Every Node(...) action written in the package's launch files."""
    nodes = []
    for path in launch_files(package):
        tree = _parse(_read(path))
        if tree is None:
            continue
        names = _assignments(tree)
        defaults = _argument_defaults(tree)
        for call in _nodes(tree):
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
                namespace=_text(namespace) if namespace is not None else None,
                parameters=[(element, _constants(defaults.get(_configuration(element), element), names))
                            for element in parameters.elts]
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
        installer, text = package.root / "setup.py", _installed_text(package.setup_text)
    elif package.cmake_text is not None:
        installer, text = package.root / "CMakeLists.txt", package.cmake_text
    else:
        return []
    sources = [(path, _read(path) or "") for path in launch_files(package)]
    issues = []
    for directory in RESOURCE_DIRS:
        prefix = str(package.root / directory) + os.sep
        members = [path for path in package.files if str(path).startswith(prefix)]
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
    for call in _nodes(tree):
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
    for node in _nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = {getattr(base, "attr", getattr(base, "id", "?")) for base in node.bases}
            if bases - {"Node", "LifecycleNode", "object"}:
                return None  # parameters may be declared by a base class elsewhere
    declared = set()
    for call in _nodes(tree):
        if not isinstance(call, ast.Call):
            continue
        if _call_name(call) in ("declare_parameter", "declare_parameters") and _opaque(call):
            return None  # *args or **kwargs: the declared name is not written here
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
                sections = _sections(data)
                if not sections:
                    continue
                matching = [key for key in sections if _section_matches(key, name, node.namespace)]
                if not matching:
                    argument = _configuration(element)
                    origin = f" (the default of the launch argument '{argument}')" if argument else ""
                    issues.append(_issue(
                        package, node.file, node.line, "warning", "parameter-section",
                        f"node '{name}' is given {path.relative_to(package.root)}{origin}, which has no "
                        f"section for that name (sections: {', '.join(sorted(sections))}); its "
                        "parameters stay at the defaults in the code."))
                    continue
                declared = _declared_parameters(source, tree) if tree is not None and source else None
                if declared is None or source_path is None:
                    continue
                # A section that applies only under a pushed namespace may belong to
                # another robot that runs a node of this name: its parameters are not
                # held against this node.
                for key in [key for key in matching if _section_matches(key, name, node.namespace, 0)]:
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


def _sections(data: dict, prefix: str = "", depth: int = 4) -> dict:
    """Node sections of a parameter file: {"namespace/name": {"ros__parameters": ...}}.

    A section may sit under a namespace key instead of at the top level.
    """
    found = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        name = f"{prefix}/{str(key).strip('/')}" if prefix else str(key)
        if "ros__parameters" in value:
            found[name] = value
        elif depth:
            found.update(_sections(value, name, depth - 1))
    return found


def _tokens_match(pattern: list, path: list) -> bool:
    """A parameter file node name against a node path: `*` is one token, `**` any number."""
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_tokens_match(rest, path[skip:]) for skip in range(len(path) + 1))
    return bool(path) and (head == "*" or path[0] is None or head == path[0]) and _tokens_match(
        rest, path[1:])


def _section_matches(key: str, name: str, namespace: Optional[str] = None,
                     pushed: Optional[int] = None) -> bool:
    """Whether the section *key* can apply to the node *name* in *namespace*.

    A section applies to a node whose fully qualified name it matches: `demo`
    is `/demo` and does not apply to `/robot1/demo`. A namespace written with a
    leading slash is the whole namespace. A relative one, or none, may be
    preceded by namespaces pushed around the node, which a launch file does
    not show at the node: *pushed* is how many such tokens to assume, and None
    tries none to six.
    """
    pattern = [token for token in key.split("/") if token]
    tail = [token for token in (namespace or "").split("/") if token] + [name]
    if namespace is not None and namespace.startswith("/"):
        return _tokens_match(pattern, tail)
    counts = range(7) if pushed is None else (pushed,)
    return any(_tokens_match(pattern, [None] * count + tail) for count in counts)


def _is_test(path: Path) -> bool:
    """A test module or anything under a test directory."""
    name = path.name.rpartition(":")[0] or path.name
    return bool({"test", "tests"} & set(path.parts)) or name.startswith("test") or name.endswith(
        "_test.py")


def check_dependencies(package: Package) -> list:
    """ROS packages the code imports must be declared in package.xml."""
    imported: set = set()
    top_level: set = set()  # imported unconditionally, at module level
    for path in package.files:
        relative = path.relative_to(package.root).parts
        if path.suffix != ".py" or path.name == "setup.py" or relative[0] == "launch" or _is_test(path):
            continue
        tree = _parse(_read(path))
        if tree is None:
            continue
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Import):
                top_level.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                top_level.add(node.module.split(".")[0])
        for node in _nodes(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module.split(".")[0])
    needed = {name for name in imported
              if name in ROS_IMPORTS or name.endswith(INTERFACE_SUFFIXES)}
    if "rclpy" in package.depends:
        needed -= WITH_RCLPY
    # A module the package ships itself is not a dependency.
    own = {path.name for path in package.root.iterdir()} | {package.name}
    missing = sorted(name for name in needed - own
                     if not PROVIDED_BY.get(name, {name}) & package.depends)
    issues = []
    if missing:
        them = "it" if len(missing) == 1 else "them"
        issues.append(_issue(
            package, package.root / "package.xml", 1, "warning", "dependencies",
            f"the code imports {', '.join(missing)}, but package.xml declares no dependency on "
            f"{them}. Unless another dependency brings {them} in, rosdep does not install {them} "
            "and a clean machine fails at import."))
    declared = " ".join(sorted(package.depends)).lower()
    third_party = top_level - own if package.setup_text is not None else set()
    if "rclpy" in package.depends:
        third_party -= KEYS_WITH_RCLPY
    keys = sorted(f"{name} (rosdep key {ROSDEP_KEYS[name][0]})" for name in third_party
                  if name in ROSDEP_KEYS and not any(part in declared for part in ROSDEP_KEYS[name][1]))
    if keys:
        them = "it" if len(keys) == 1 else "them"
        issues.append(_issue(
            package, package.root / "package.xml", 1, "warning", "dependencies",
            f"the code imports {', '.join(keys)}, but package.xml declares no dependency that "
            f"provides {them}. Unless another dependency brings {them} in, rosdep does not "
            f"install {them} and a clean machine fails at import."))
    return issues


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

    for call in _node_calls(tree):
        allowed = RCLPY_KEYWORDS.get(_call_name(call))
        if allowed is None:
            continue
        for keyword in call.keywords:
            if keyword.arg not in allowed:
                add(call, "error", "keyword-argument",
                    f"{_call_name(call)}() has no parameter '{keyword.arg}' (its parameters "
                    f"are: {', '.join(sorted(allowed))}); rclpy raises TypeError when this "
                    "line runs.")
    for call in _nodes(tree):
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

    issues.extend(_callback_signatures(package, path, tree))
    issues.extend(_methods_without_self(package, path, tree))
    # Whether a blocking call inside a callback deadlocks depends on the
    # executor and the callback groups. With either in play it is not decided.
    if re.search(r"MultiThreadedExecutor|CallbackGroup|callback_group", source):
        return issues
    for cls in _nodes(tree):
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


def _node_classes(tree: ast.AST) -> list:
    """Classes whose only bases are rclpy's Node or LifecycleNode, by name.

    Another base may define methods with the same names and other parameters
    (rclpy's experimental AsyncNode does), so such a class is not judged.
    """
    found = []
    for cls in _nodes(tree):
        if isinstance(cls, ast.ClassDef) and cls.bases and all(
                getattr(base, "attr", getattr(base, "id", "")) in ("Node", "LifecycleNode")
                for base in cls.bases):
            found.append(cls)
    return found


def _methods_without_self(package: Package, path: Path, tree: ast.AST) -> list:
    """A method that uses self and does not take it: NameError the first time it runs."""
    issues = []
    module_names = {target.id for node in getattr(tree, "body", []) if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
    for cls in _nodes(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.decorator_list:
                continue
            spec = method.args
            parameters = {arg.arg for arg in spec.posonlyargs + spec.args + spec.kwonlyargs}
            if "self" in parameters or "self" in module_names or spec.vararg or spec.kwarg:
                continue
            bound = {node.id for node in ast.walk(method)
                     if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
            nested = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                           ast.Global, ast.Nonlocal))
                         for node in ast.walk(method) if node is not method)
            uses = [node for node in ast.walk(method)
                    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "self"]
            if not uses or "self" in bound or nested:
                continue
            issues.append(_issue(
                package, path, method.lineno, "error", "missing-self",
                f"{cls.name}.{method.name}() uses self at line {uses[0].lineno} but does not "
                "take it as a parameter; when it is called as a method the first argument is "
                "bound to the wrong name and the body raises NameError or TypeError."))
    return issues


# Calls that read a container without keeping or changing it.
_READERS = {"len", "sorted", "list", "tuple", "set", "frozenset", "any", "all", "enumerate",
            "print", "str", "repr", "min", "max", "sum", "reversed", "iter", "bool", "isinstance"}
_FRESH = {"list", "dict", "set", "sorted", "tuple", "frozenset"}


def _fresh_local(func: ast.AST, name: str) -> bool:
    """*name* is bound once in *func* to a new container and nothing else can reach it.

    Then only a statement that names it can change it: it is never passed to a
    call that might keep it, never given another name and never stored.
    """
    bound = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = node.value
            fresh = isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.SetComp,
                                       ast.DictComp)) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in _FRESH)
            if not fresh or len(node.targets) != 1:
                return False
            bound += 1
        elif isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            parent_ok = False  # every other binding (loop target, with, augmented, import) is not fresh
            if not parent_ok and not any(
                    isinstance(a, ast.Assign) and node in a.targets for a in ast.walk(func)):
                return False
    if bound != 1:
        return False
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            reader = isinstance(node.func, ast.Name) and node.func.id in _READERS
            passed = list(node.args) + [keyword.value for keyword in node.keywords]
            if not reader and any(isinstance(item, ast.Name) and item.id == name
                                  for arg in passed for item in ast.walk(arg)):
                return False
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Yield,
                               ast.YieldFrom)) and node.value is not None:
            if any(isinstance(item, ast.Name) and item.id == name for item in ast.walk(node.value)):
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Compare)):
                    return False
    return True


def _invariant_loops(package: Package, path: Path, tree: ast.AST) -> list:
    """A while loop whose condition has the same value on every pass.

    Reported only when that can be read off the function: the condition is
    made of plain names, the body has no way out and never rebinds them, and
    the container being tested is a new local that nothing in the body names.
    """
    issues = []
    for func in _nodes(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # A nested function, lambda or class can change a name of the enclosing
        # function from elsewhere; the names it mentions are not judged.
        inner = {item.id for node in ast.walk(func) if node is not func and isinstance(
                     node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
                 for item in ast.walk(node) if isinstance(item, ast.Name)}
        if any(isinstance(node, (ast.Global, ast.Nonlocal)) for node in ast.walk(func)):
            continue
        for loop in ast.walk(func):
            if not isinstance(loop, ast.While) or loop.orelse:
                continue
            test = loop.test
            if any(not isinstance(node, (ast.Name, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.Constant,
                                         ast.And, ast.Or, ast.Not, ast.In, ast.NotIn, ast.Eq, ast.NotEq,
                                         ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.Load))
                   for node in ast.walk(test)):
                continue  # a call, an attribute or an index can change between passes
            names = {node.id for node in ast.walk(test) if isinstance(node, ast.Name)}
            containers = {node.comparators[0].id for node in ast.walk(test)
                          if isinstance(node, ast.Compare) and len(node.ops) == 1
                          and isinstance(node.ops[0], (ast.In, ast.NotIn))
                          and isinstance(node.comparators[0], ast.Name)}
            if not names or not containers or names & inner:
                continue  # only membership in a container is judged
            body = [node for statement in loop.body for node in ast.walk(statement)]
            if any(isinstance(node, (ast.Break, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom,
                                     ast.Await, ast.Try, ast.With, ast.AsyncWith)) for node in body):
                continue
            touched = False
            for node in body:
                if isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, (ast.Store, ast.Del)):
                    touched = True
                elif isinstance(node, ast.Name) and node.id in containers:
                    touched = True  # the container is named in the body: it may change
                elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
                        node.value, ast.Name) and node.value.id in names:
                    touched = True
            if touched or not all(_fresh_local(func, name) for name in containers):
                continue
            shown = ", ".join(sorted(names))
            issues.append(_issue(
                package, path, loop.lineno, "warning", "invariant-loop",
                f"the condition of this while loop reads {shown}, and the loop body neither "
                f"rebinds them nor names {', '.join(sorted(containers))}, which is a container "
                "created in this function that nothing else holds. The condition has the same "
                "value on every pass: the loop never runs or never ends, unless it ends in an "
                "exception. A `for` over the container may have been meant."))
    return issues


def _environment_numbers(package: Package, path: Path, tree: ast.AST) -> list:
    """int() or float() of a ROS environment variable that has a default but may be empty."""
    issues = []
    for call in _nodes(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id in ("int", "float") and len(call.args) == 1 and not call.keywords):
            continue
        inner = call.args[0]
        if not isinstance(inner, ast.Call) or _opaque(inner) or len(inner.args) != 2:
            continue
        target = ast.unparse(inner.func)
        if target not in ("os.environ.get", "os.getenv", "environ.get", "getenv"):
            continue
        key = _text(inner.args[0])
        if key is None or not key.startswith(("ROS_", "RMW_")):
            continue
        default = ast.unparse(inner.args[1])
        issues.append(_issue(
            package, path, call.lineno, "warning", "environment-number",
            f"{call.func.id}({target}('{key}', {default})) uses the default only when {key} is "
            f"unset. When it is set but empty (`{key}=` in a shell or a compose file) the call "
            f"raises ValueError. `{call.func.id}({target}('{key}') or {default})` covers both."))
    return issues


@functools.lru_cache(maxsize=4096)
def _module_names(path: Path) -> Optional[frozenset]:
    """Names a module defines at import time, or None when that cannot be read off the file."""
    source = _read(path)
    tree = _parse(source)
    if tree is None or source is None or re.search(
            r"__getattr__|globals\(\)|setattr\(|import \*|sys\.modules|__dict__|exec\(", source):
        return None
    names: set = set()

    def collect(statements: list) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    names.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                for field_name in ("body", "orelse", "finalbody"):
                    collect(getattr(node, field_name, []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    collect(handler.body)
                if isinstance(node, (ast.For, ast.With)):
                    names.update(item.id for item in ast.walk(node) if isinstance(item, ast.Name)
                                 and isinstance(item.ctx, ast.Store))
    collect(getattr(tree, "body", []))
    return frozenset(names)


def _python_roots(packages: list) -> tuple:
    """(package roots, installed roots, package names): where a top-level import can resolve."""
    installed = []
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry and os.path.isdir(entry):
            installed.append(Path(entry))
    for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
        if prefix:
            installed.extend(sorted(Path(prefix).glob("lib/python3*/site-packages")))
            installed.extend(sorted(Path(prefix).glob("local/lib/python3*/dist-packages")))
    return (tuple(package.root for package in packages), tuple(installed),
            frozenset(package.name for package in packages))


# Directories of a package that are installed as data or run as tests. A
# launch/ directory with an __init__.py is not the module `launch`.
_NOT_MODULES = set(RESOURCE_DIRS) | {"test", "tests", "scripts", "doc", "docs", "resource"}


@functools.lru_cache(maxsize=8192)
def _resolve_module(roots: tuple, module: str) -> Optional[Path]:
    """The one file *module* names; None when there is none or it cannot be read here.

    *roots* is (package roots, installed roots, names of the workspace
    packages). Under a package root only a real module directory counts, not a
    data directory that happens to share the name of an installed module.

    A module the workspace provides is read from the workspace, also when an
    installed distribution has one of the same name: packages of an overlay
    override those of the underlay. A package of the workspace whose Python
    is generated at build time, or kept where it cannot be found from the
    package root, is not judged by an installed copy of another release.
    """
    workspace, installed = roots[0], roots[1]
    names = roots[2] if len(roots) > 2 else frozenset()
    top = module.split(".")[0]
    found = []
    for root in () if top in _NOT_MODULES else workspace:
        base = root.joinpath(*module.split("."))
        if base.with_suffix(".py").is_file():
            found.append(base.with_suffix(".py"))
        elif (base / "__init__.py").is_file():
            found.append(base / "__init__.py")
    if not found and top not in _NOT_MODULES and (top in names or any(
            (root / folder / top).is_dir() for root in workspace for folder in ("python", "src"))):
        return None
    for root in () if found else installed:
        base = root.joinpath(*module.split("."))
        if base.with_suffix(".py").is_file():
            found.append(base.with_suffix(".py"))
        elif (base / "__init__.py").is_file():
            found.append(base / "__init__.py")
        elif (base.is_dir() or list(base.parent.glob(base.name + ".*.so"))
              or base.with_suffix(".pyi").is_file()):
            return None  # a namespace package or a compiled module: not readable here
    unique = {str(path.resolve()) for path in found}
    return found[0] if len(unique) == 1 else None


def _installed_release(roots: tuple, target: Path, top: str) -> Optional[str]:
    """'name version (prefix)' when *target* lies in an installed root; None inside the workspace."""
    for root in roots[1]:
        if root not in target.parents:
            continue
        for prefix in list(root.parents)[1:4]:
            manifest = _read(prefix / "share" / top / "package.xml") or ""
            version = re.search(r"<version>\s*([^<\s]+)\s*</version>", manifest)
            if version:
                return f"{top} {version.group(1)} ({prefix})"
        return f"{top} ({root})"
    return None


_OTHER_RELEASE = (" Either the name is wrong here or this code is written for another release of "
                  "{top}; in the second case it is the installation that differs, not this file.")


def _binds_at_top(target: Path, module: str, name: str) -> bool:
    """True when the file at *target* binds *name* at its top level whichever branch is taken.

    A name bound under `if TYPE_CHECKING:`, in a `try`, a loop or a `with`
    need not exist once the module is imported, and one imported from
    *module* fails together with the import that is being reported.
    """
    bound = False
    for node in getattr(_parse(_read(target)), "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = {node.name}
        elif isinstance(node, ast.Assign) or (isinstance(node, ast.AnnAssign) and node.value is not None):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {item.id for target_node in targets for item in ast.walk(target_node)
                     if isinstance(item, ast.Name)}
        elif isinstance(node, ast.Import):
            names = {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not (node.module == module and not node.level):
            names = {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Delete):
            if any(isinstance(item, ast.Name) and item.id == name for item in node.targets):
                bound = False
            continue
        else:
            continue
        bound = bound or name in names
    return bound


def _defined_elsewhere(roots: tuple, package: Package, tree: ast.AST, module: str, name: str) -> list:
    """Where *name* is defined instead: the modules of the same path under other packages in use.

    `from launch_ros.substitutions import FileContent` names a class that
    launch.substitutions defines. Only packages this file imports or the
    package declares are looked at, and only the module of the same path, so
    what is said is a fact that was read, not a search for a similar name.
    Every such module is returned: which of several is meant is not known.
    """
    top, _, rest = module.partition(".")
    tops = set(package.depends)
    for node in _nodes(tree):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
    places = []
    for other in sorted(tops - {top}):
        sibling = other + ("." + rest if rest else "")
        target = _resolve_module(roots, sibling)
        if target is not None and _module_names(target) is not None and _binds_at_top(target, module, name):
            release = _installed_release(roots, target, other)
            places.append(f"`{sibling}` of {'the installed ' + release if release else 'this workspace'}")
    return places


def _elsewhere(places: list, name: str, top: Optional[str] = None) -> str:
    """The sentence that says where *name* is defined; with *top*, for a module of an installation."""
    listed = places[0] if len(places) == 1 else ", ".join(places[:-1]) + " and " + places[-1]
    text = f" {listed} {'defines' if len(places) == 1 else 'each define'} `{name}`"
    if top is None:
        return text + "."
    return (f"{text}: unless this code is written for a release of {top} that has the name, import it "
            f"from {'there' if len(places) == 1 else 'the one of them that is meant'}.")


def _note_repeats(seen: dict) -> None:
    """Finish the one finding kept for an installed module from all the imports of the package.

    Where the name is defined is taken from every file that repeats the
    import, not only from the first, and the number of repeats is said.
    """
    for entry in seen.values():
        places = sorted(entry["places"])
        entry["issue"]["message"] = entry["base"] + (
            _elsewhere(places, entry["name"], entry["top"]) if places else _OTHER_RELEASE.format(top=entry["top"]))
        if entry["repeats"]:
            entry["issue"]["message"] += (f" The same import is in {entry['repeats']} more "
                                          f"place{'s' if entry['repeats'] > 1 else ''} of this package.")
    seen.clear()


def check_python(roots: tuple, package: Package, path: Path, seen: Optional[dict] = None) -> list:
    """Defects of plain Python that can be read off one file and stop a node at run time."""
    tree = _parse(_read(path))
    if tree is None:
        return []
    return (_invariant_loops(package, path, tree) + _environment_numbers(package, path, tree)
            + check_imports(roots, package, path, seen) + _string_methods(package, path, tree)
            + _other_clients(package, path, tree) + _class_attributes(package, path, tree)
            + _scan_ranges(package, path, tree) + _local_keywords(package, path, tree))


def _local_keywords(package: Package, path: Path, tree: ast.AST) -> list:
    """A function of this module is called with a keyword it does not take: TypeError at the call."""
    body = getattr(tree, "body", [])
    bound: dict = {}
    for node in _nodes(tree):
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [(alias.asname or alias.name).split(".")[0] for alias in node.names]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        for name in names:
            bound[name] = bound.get(name, 0) + 1
    functions = {node.name: node for node in body
                 if isinstance(node, ast.FunctionDef) and not node.decorator_list
                 and bound.get(node.name) == 1 and node.args.kwarg is None}
    issues = []
    for call in _nodes(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id in functions) or _opaque(call):
            continue
        spec = functions[call.func.id].args
        accepted = {arg.arg for arg in spec.args + spec.kwonlyargs}
        for keyword in call.keywords:
            if keyword.arg not in accepted:
                close = sorted(name for name in accepted if _distance(name, keyword.arg or "") <= 2)
                hint = f" It takes `{close[0]}`." if len(close) == 1 else ""
                issues.append(_issue(
                    package, path, call.lineno, "error", "keyword-argument",
                    f"{call.func.id}() is defined at line {functions[call.func.id].lineno} of this "
                    f"file and has no parameter `{keyword.arg}`; the call raises TypeError "
                    f"(unexpected keyword argument).{hint}"))
    return issues


def _own_nodes(function: ast.AST) -> list:
    """Nodes of *function* that are not inside a function or class nested in it."""
    found, stack = [], list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        found.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))
    return found


def _string_methods(package: Package, path: Path, tree: ast.AST) -> list:
    """`__str__` or `__repr__` with a path that returns None: str() and print() raise TypeError."""
    issues = []
    for cls in _nodes(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if (not isinstance(method, ast.FunctionDef) or method.name not in ("__str__", "__repr__")
                    or method.decorator_list):
                continue
            for node in sorted(_own_nodes(method), key=lambda item: getattr(item, "lineno", 0)):
                if isinstance(node, ast.Return) and (
                        node.value is None
                        or (isinstance(node.value, ast.Constant) and node.value.value is None)):
                    shown = "str()" if method.name == "__str__" else "repr()"
                    issues.append(_issue(
                        package, path, node.lineno, "error", "returns-none",
                        f"{cls.name}.{method.name}() returns None here; {shown}, print() and "
                        f"string formatting raise TypeError ({method.name} returned non-string)."))
                    break
    return issues


_WAITS = {"wait_for_server", "wait_for_service"}
_REQUESTS = {"send_goal_async", "send_goal", "call_async", "call"}


def _other_clients(package: Package, path: Path, tree: ast.AST) -> list:
    """A function waits for one client and sends its request through another."""
    issues = []
    for function in _nodes(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        waited: dict = {}
        asked: dict = {}
        other = False
        for node in _own_nodes(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name = _self_attribute(node.func.value)
            if node.func.attr in _WAITS | _REQUESTS and name is None:
                other = True  # a client that is not a plain attribute of self
            elif node.func.attr in _WAITS:
                waited.setdefault(name, node.lineno)
            elif node.func.attr in _REQUESTS:
                asked.setdefault(name, node.lineno)
        if other or len(waited) != 1 or len(asked) != 1 or set(waited) == set(asked):
            continue
        (wait_name, wait_line), (ask_name, ask_line) = *waited.items(), *asked.items()
        rebound = any(isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                      and _self_attribute(node) in (wait_name, ask_name) for node in ast.walk(function))
        if rebound:
            continue
        issues.append(_issue(
            package, path, ask_line, "warning", "other-client",
            f"{function.name}() waits for self.{wait_name} at line {wait_line} and sends its "
            f"request through self.{ask_name}, which it never waits for; the request can go out "
            "before that server is available."))
    return issues


def _class_attributes(package: Package, path: Path, tree: ast.AST) -> list:
    """An attribute is set on an imported class in a function that has just made an instance of it."""
    imported = {alias.asname or alias.name for node in getattr(tree, "body", [])
                if isinstance(node, ast.ImportFrom) for alias in node.names}
    defined = {node.name for node in _nodes(tree) if isinstance(node, ast.ClassDef)}
    issues = []
    for function in _nodes(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        instances: dict = {}
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and not node.value.args
                    and not node.value.keywords):
                instances.setdefault(node.value.func.id, (node.targets[0].id, node.lineno))
        for node in ast.walk(function):
            if not (isinstance(node, (ast.Assign, ast.AugAssign))):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)):
                    continue
                cls = target.value.id
                if (cls not in instances or cls not in imported or cls in defined
                        or not cls[:1].isupper() or instances[cls][1] > node.lineno):
                    continue
                name, line = instances[cls]
                issues.append(_issue(
                    package, path, node.lineno, "warning", "class-attribute",
                    f"{cls}.{target.attr} is assigned on the class, while `{name} = {cls}()` at "
                    f"line {line} made the instance this function goes on to use; {name}."
                    f"{target.attr} keeps its default and the class is changed for every other user."))
    return issues


_SCAN_LIMITS = re.compile(r"range_min|range_max")
_SCAN_GUARDS = re.compile(r"isfinite|isinf|isnan|\binf\b|\bnan\b", re.I)
_REDUCERS = {"min", "max", "sum", "sorted", "mean", "median", "array", "asarray", "amin", "amax",
             "argmin", "argmax", "any", "all", "filter", "map", "enumerate", "zip", "list", "tuple"}


def _measured(node: ast.AST, parents: dict) -> bool:
    """*node* is indexed, iterated or reduced: its elements are read one by one."""
    parent = parents.get(node)
    if isinstance(parent, ast.Call) and node in parent.args and _call_name(parent) == "enumerate":
        node, parent = parent, parents.get(parent)
    return ((isinstance(parent, ast.Subscript) and parent.value is node)
            or (isinstance(parent, (ast.For, ast.comprehension)) and parent.iter is node)
            or (isinstance(parent, ast.Call) and node in parent.args
                and _call_name(parent) in _REDUCERS))


def _measured_by_helper(node: ast.AST, parents: dict, tree: ast.AST) -> bool:
    """*node* is passed by position to a function of this file that reads that parameter's elements."""
    call = parents.get(node)
    if not (isinstance(call, ast.Call) and node in call.args) or _opaque(call):
        return False
    name = call.func.id if isinstance(call.func, ast.Name) else _self_attribute(call.func)
    helpers = [item for item in _nodes(tree)
               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name]
    if name is None or len(helpers) != 1 or helpers[0].args.vararg:
        return False
    spec = helpers[0].args
    names = [arg.arg for arg in spec.posonlyargs + spec.args]
    if names[:1] == ["self"] and not isinstance(call.func, ast.Name):
        names = names[1:]
    index = call.args.index(node)
    if index >= len(names):
        return False
    return any(isinstance(item, ast.Name) and item.id == names[index] and isinstance(item.ctx, ast.Load)
               and _measured(item, parents) for item in ast.walk(helpers[0]))


def _scan_ranges(package: Package, path: Path, tree: ast.AST) -> list:
    """LaserScan ranges are read as distances in a file that never excludes invalid readings."""
    source = _read(path) or ""
    imports_scan = any(isinstance(node, ast.ImportFrom) and node.module == "sensor_msgs.msg"
                       and any(alias.name == "LaserScan" for alias in node.names)
                       for node in _nodes(tree))
    if not imports_scan or _SCAN_LIMITS.search(source):
        return []
    parents = {child: parent for parent in _nodes(tree) for child in ast.iter_child_nodes(parent)}
    for function in _nodes(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        spec = function.args
        parameters = {arg.arg for arg in spec.posonlyargs + spec.args + spec.kwonlyargs} - {"self"}
        for node in sorted(ast.walk(function), key=lambda item: getattr(item, "lineno", 0)):
            if not (isinstance(node, ast.Attribute) and node.attr == "ranges"
                    and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name)
                    and node.value.id in parameters):
                continue
            if not (_measured(node, parents) or _measured_by_helper(node, parents, tree)):
                continue
            if _SCAN_GUARDS.search(source):
                return [_issue(
                    package, path, node.lineno, "warning", "scan-ranges",
                    f"{node.value.id}.ranges is read as distances and this file never reads "
                    "range_min or range_max. A value outside range_min..range_max is not a "
                    "distance (REP 117): a driver that reports 0.0 for no return would be read "
                    "as an obstacle at zero distance. Use only values within "
                    f"{node.value.id}.range_min..{node.value.id}.range_max.")]
            return [_issue(
                package, path, node.lineno, "warning", "scan-ranges",
                f"{node.value.id}.ranges is read as distances and this file never excludes "
                "invalid readings. A LaserScan holds +inf where nothing returned, -inf where a "
                "return was too close and NaN for a failed measurement, and a value outside "
                "range_min..range_max is not a distance (REP 117): use only finite values "
                "within range_min..range_max.")]
    return []


def check_imports(roots: tuple, package: Package, path: Path, seen: Optional[dict] = None) -> list:
    """`from module import name` where the module's own source defines no such name.

    A module read from an installed distribution may be of another release
    than the one the code is written for. Such a finding is a warning that
    says so, and it is made once per package: *seen* carries what has been
    said from one file of the package to the next.
    """
    tree = _parse(_read(path))
    if tree is None:
        return []
    imports = [node for node in _nodes(tree) if isinstance(node, ast.ImportFrom)]
    if not imports:
        return []
    guarded = {id(node) for block in _nodes(tree) if isinstance(block, (ast.Try, ast.If))
               for node in ast.walk(block) if isinstance(node, ast.ImportFrom)}
    loaded = {id(node) for node in getattr(tree, "body", [])}
    own = seen is None
    seen = {} if seen is None else seen
    issues = []

    def report(node: ast.ImportFrom, target: Path, key: str, workspace: str, installed: str, error: str) -> None:
        when = "when this file is loaded" if id(node) in loaded else "when this line runs"
        top = (node.module or "").split(".")[0]
        release = None if node.level else _installed_release(roots, target, top)
        places = (_defined_elsewhere(roots, package, tree, node.module, key)
                  if key and node.module and not node.level else [])
        if release is None:
            issues.append(_issue(package, path, node.lineno, "error", "import-name",
                                 f"{workspace}; the import raises {error} {when}."
                                 + (_elsewhere(places, key) if places else "")))
            return
        if (node.module, key) in seen:
            seen[(node.module, key)]["repeats"] += 1
            seen[(node.module, key)]["places"].update(places)
            return
        base = installed.format(release=release) + f"; under that installation the import raises {error} {when}."
        issue = _issue(package, path, node.lineno, "warning", "import-name", base)
        seen[(node.module, key)] = {"issue": issue, "repeats": 0, "base": base, "top": top, "name": key,
                                    "places": set(places)}
        issues.append(issue)

    for node in imports:
        if id(node) in guarded or not node.module:
            continue  # inside try or if: an optional import is the author's choice
        if node.level:
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            target = _resolve_module(((), (base,)), node.module)
        else:
            target = _resolve_module(roots, node.module)
        if target is None and not node.level and "." in node.module:
            # The parent package can be read and has no such submodule.
            parent_name, _, leaf = node.module.rpartition(".")
            parent = _resolve_module(roots, parent_name)
            if (parent is not None and parent.name == "__init__.py" and _module_names(parent) is not None
                    and leaf not in (_module_names(parent) or ())
                    and not (parent.parent / leaf).exists()
                    and not list(parent.parent.glob(leaf + ".*"))):
                shown = f"`from {node.module} import ...`"
                report(node, parent, "", f"{shown}: the package {parent_name} has no module `{leaf}`",
                       f"{shown}: the installed {{release}} has no module `{leaf}`"
                       + ("" if "." not in parent_name else f" in {parent_name}"),
                       "ModuleNotFoundError")
            continue
        if target is None or target.resolve() == path.resolve():
            continue
        defined = _module_names(target)
        if defined is None:
            continue
        for alias in node.names:
            if alias.name == "*" or alias.name in defined:
                continue
            folder = target.parent if target.name == "__init__.py" else None
            if folder is not None and ((folder / (alias.name + ".py")).is_file()
                                       or (folder / alias.name).is_dir()
                                       or list(folder.glob(alias.name + ".*"))):
                continue  # a submodule
            shown = f"`from {'.' * node.level}{node.module} import {alias.name}`"
            module_file = target.name if target.name != "__init__.py" else target.parent.name + "/__init__.py"
            report(node, target, alias.name, f"{shown}: {module_file} defines no `{alias.name}`",
                   f"{shown}: {module_file} of the installed {{release}} defines no `{alias.name}`",
                   "ImportError")
    if own:
        _note_repeats(seen)
    return issues


def _node_calls(tree: ast.AST) -> list:
    """Method calls whose receiver is known to be an rclpy node.

    Either self inside a class that derives only from Node or LifecycleNode and
    does not define the method itself, or a name bound exactly once in the
    file to rclpy.create_node(...), Node(...) or LifecycleNode(...).
    """
    calls = []
    for cls in _node_classes(tree):
        overridden = {node.name for node in cls.body
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for call in ast.walk(cls):
            if (isinstance(call, ast.Call) and not _opaque(call)
                    and _self_attribute(call.func) is not None
                    and _call_name(call) not in overridden):
                calls.append(call)
    nodes = {name for name, value in _assignments(tree).items()
             if isinstance(value, ast.Call) and _call_name(value) in (
                 "create_node", "Node", "LifecycleNode")}
    for call in _nodes(tree):
        if (isinstance(call, ast.Call) and not _opaque(call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id in nodes):
            calls.append(call)
    return calls


def _arity(function: ast.AST, method: bool) -> Optional[tuple]:
    """(required, accepted) positional arguments, or None when it cannot be read."""
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.decorator_list:
        return None
    spec = function.args
    if any(default is None for default in spec.kw_defaults):
        return None  # a keyword-only parameter without a default: not a plain callback
    positional = spec.posonlyargs + spec.args
    if method:
        if not positional:
            return None
        positional = positional[1:]
    required = len(positional) - len(spec.defaults)
    return max(required, 0), (float("inf") if spec.vararg else len(positional))


def _callback_signatures(package: Package, path: Path, tree: ast.AST) -> list:
    """A callback whose signature no supported distribution can call."""
    issues = []
    module_functions: dict = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module_functions.setdefault(node.name, []).append(node)
    for cls in _node_classes(tree):
        methods: dict = {}
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.setdefault(node.name, []).append(node)
        for call in ast.walk(cls):
            if not isinstance(call, ast.Call) or _opaque(call) or _self_attribute(call.func) is None:
                continue
            spec = CALLBACK_ARGUMENTS.get(_call_name(call))
            if spec is None:
                continue
            index, keyword, fewest, most, given = spec
            callback = _argument(call, index, keyword)
            attribute = _self_attribute(callback)
            if attribute is not None:
                found, method, shown = methods.get(attribute, []), True, f"self.{attribute}"
            elif isinstance(callback, ast.Name):
                found, method, shown = module_functions.get(callback.id, []), False, callback.id
            else:
                continue
            arity = _arity(found[0], method) if len(found) == 1 else None
            if arity is None:
                continue
            if _call_name(call) == "create_timer" and "TimerInfo" in ast.dump(found[0].args):
                continue  # filled in by keyword from Kilted on: depends on the distribution
            required, accepted = arity
            if required <= most and accepted >= fewest:
                continue
            takes = "no argument" if accepted == 0 else (
                f"{required} required argument{'s' if required != 1 else ''}")
            issues.append(_issue(
                package, path, found[0].lineno, "error", "callback-signature",
                f"{shown} is the callback of {_call_name(call)}() at line {call.lineno} and "
                f"takes {takes} besides self; rclpy calls it with {given}, so it raises "
                "TypeError."
                if method else
                f"{shown} is the callback of {_call_name(call)}() at line {call.lineno} and "
                f"takes {takes}; rclpy calls it with {given}, so it raises TypeError."))
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
    for call in _nodes(tree):
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
            for cls in _nodes(tree))
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
        if _is_test(Path(pair["publisher"])) or _is_test(Path(pair["subscription"])):
            continue  # tests build mismatched endpoints on purpose
        file, _, line = pair["subscription"].rpartition(":")
        scope = ("" if pair["relation"] == "confirmed"
                 else " if both are the same topic at runtime (namespaces and remapping are "
                      "not resolved)")
        issues.append({
            "file": file, "line": int(line) if line.isdigit() else 0, "severity": "warning",
            "check": "qos-incompatible",
            "message": f"topic '{pair['topic']}': {'; '.join(pair['issues'])}. The publisher at "
                       f"{pair['publisher']} and this subscription do not connect{scope}, and "
                       "no data arrives. This is the rule of the DDS middlewares (Fast DDS, "
                       "Cyclone DDS, Connext); rmw_zenoh_cpp connects them."})
    for sub in report.endpoints:
        if sub.kind != "subscription" or sub.reasons or sub.qos is None or sub.msg_type is None:
            continue
        if sub.topic_kind == "dynamic" or _is_test(Path(sub.file)):
            continue  # a test sets up its endpoints in a known order
        if sub.qos.durability == qos_audit.VOLATILE:
            why = "is VOLATILE and does not ask for it"
        elif (sub.qos.durability == qos_audit.TRANSIENT_LOCAL
              and sub.qos.reliability == qos_audit.BEST_EFFORT):
            # Measured on Fast DDS and Cyclone DDS; Fast DDS documents it for the DataReader.
            why = ("asks for it but is BEST_EFFORT, and Fast DDS and Cyclone DDS hand past "
                   "samples only to a RELIABLE subscription")
        else:
            continue
        for pub in report.endpoints:
            if (pub.kind == "publisher" and not pub.reasons and pub.qos is not None
                    and (pub.topic_kind, pub.topic, pub.msg_type) == (
                        sub.topic_kind, sub.topic, sub.msg_type)
                    and pub.qos.durability == qos_audit.TRANSIENT_LOCAL):
                issues.append({
                    "file": sub.file, "line": sub.line, "severity": "info", "check": "qos-late-joiner",
                    "message": f"topic '{sub.topic}': the publisher at {pub.location} keeps its "
                               f"last sample for late joiners (TRANSIENT_LOCAL), but this "
                               f"subscription {why}. If it can start after the publish, it "
                               "never receives that sample."})
                break
    return issues, []


def _type_name(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _node_modules(package: Package) -> list:
    """Python files of the package that are neither launch files, setup.py nor tests."""
    launch = set(launch_files(package))
    return [path for path in package.files
            if path.suffix == ".py" and path not in launch and path.name != "setup.py"
            and not _is_test(path.relative_to(package.root))]


def _distance(first: str, second: str, limit: int = 2) -> int:
    """Edit distance, or limit + 1 once it is known to be larger."""
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    row = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        previous, row[0] = row[0], i
        for j, b in enumerate(second, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (a != b))
        if min(row) > limit:
            return limit + 1
    return row[-1]


def check_names(packages: list) -> list:
    """An endpoint with no counterpart whose name is one or two characters from one that has none.

    A client of 'add_two_int' next to a server of 'add_two_ints' is two halves
    of one connection with a typing error between them. Both sides must be
    without a counterpart anywhere in the workspace, have the same interface
    type, and the lone name must not be written in any other file: a C++ node,
    a parameter file or a remapping may be the real counterpart.
    """
    endpoints = []
    texts: dict = {}
    for package in packages:
        for path in package.files:
            if path.suffix in TEXT_SUFFIXES:
                texts[path] = None
        for path in _node_modules(package):
            source = _read(path)
            tree = _parse(source)
            if tree is None or source is None or "rclpy" not in source:
                continue
            for call in _nodes(tree):
                if not isinstance(call, ast.Call) or _call_name(call) not in ENDPOINTS:
                    continue
                name_at, name_key, type_at, type_key, kind, role = ENDPOINTS[_call_name(call)]
                if _opaque(call):
                    continue
                name = _argument(call, name_at, name_key)
                text = _text(name) if name is not None else None
                kind_of = _type_name(_argument(call, type_at, type_key))
                if text is None or kind_of is None:
                    continue
                endpoints.append((kind, role, _plain(text), text, kind_of, package, path, call.lineno))
    issues = []
    for kind in ("topic", "service", "action"):
        offered = {entry[2] for entry in endpoints if entry[0] == kind and entry[1] == "offer"}
        lone_uses = [e for e in endpoints if e[0] == kind and e[1] == "use" and e[2] not in offered]
        offers = [e for e in endpoints if e[0] == kind and e[1] == "offer"]
        for use in lone_uses:
            near = [offer for offer in offers
                    if offer[4] == use[4] and 1 <= _distance(use[2], offer[2]) <= 2
                    and min(len(use[2]), len(offer[2])) >= 5
                    and re.sub(r"\d", "", use[2]) != re.sub(r"\d", "", offer[2])]
            if len({offer[2] for offer in near}) != 1:
                continue  # none, or more than one candidate: not decided
            offer = near[0]
            if _written_elsewhere(use, endpoints, texts):
                continue
            verb = {"topic": ("subscribes to", "publishes"), "service": ("calls", "offers"),
                    "action": ("sends goals to", "serves")}[kind]
            where = f"{offer[6].relative_to(offer[5].root.parent)}:{offer[7]}"
            issues.append(_issue(
                use[5], use[6], use[7], "warning", "name-mismatch",
                f"this node {verb[0]} the {kind} '{use[3]}', which nothing in the workspace "
                f"{verb[1]}, while {where} {verb[1]} '{offer[3]}' with the same type "
                f"({use[4]}). If they are meant to be the same {kind}, the names differ and "
                "the two never connect."))
    return issues


def _written_elsewhere(entry: tuple, endpoints: list, texts: dict) -> bool:
    """The name is a quoted literal in a file other than the ones that create it."""
    kind, role, plain = entry[0], entry[1], entry[2]
    own = {e[6] for e in endpoints if e[0] == kind and e[1] == role and e[2] == plain}
    pattern = re.compile(r"""['"]~?/?%s['"]""" % re.escape(plain))
    for path in texts:
        if path in own:
            continue
        if texts[path] is None:
            texts[path] = _read(path) or ""
        if pattern.search(texts[path]):
            return True
    return False


def _interface_fields(definition: str) -> list:
    """Field and constant names of each section of a .msg, .srv or .action file."""
    sections: list = [set()]
    for line in definition.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("---"):
            sections.append(set())
            continue
        parts = line.split()
        if len(parts) >= 2:
            sections[-1].add(parts[1].split("=", 1)[0])
    return sections


def _find_interface(packages: list, package_name: str, kind: str, name: str) -> tuple:
    """(sections of the interface definition, the installed file it was read from or None).

    A package of the workspace is read from the workspace only: an installed
    copy of it may be of another release.
    """
    candidates = [(package.root / kind / f"{name}.{kind}", False) for package in packages
                  if package.name == package_name]
    for prefix in () if candidates else os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
        if prefix:
            candidates.append((Path(prefix) / "share" / package_name / kind / f"{name}.{kind}", True))
    for candidate, installed in candidates:
        if candidate.is_file():
            text = _read(candidate)
            return (_interface_fields(text) if text is not None else None,
                    candidate if installed else None)
    return None, None


def _field_uses(function: ast.AST, parameter: str) -> Optional[list]:
    """(attribute, line) for parameter.attribute in the function; None if the name is rebound."""
    uses = []
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == parameter and isinstance(
                node.ctx, (ast.Store, ast.Del)):
            return None
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == parameter):
            uses.append((node.attr, node.lineno))
    return uses


def check_interface_fields(packages: list, package: Package, path: Path) -> list:
    """A field a callback reads or writes that the interface definition does not have."""
    source = _read(path)
    tree = _parse(source)
    if tree is None or source is None or "rclpy" not in source:
        return []
    imported: dict = {}
    for node in _nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            parts = node.module.split(".")
            if len(parts) == 2 and parts[1] in ("msg", "srv"):
                for alias in node.names:
                    imported.setdefault(alias.asname or alias.name, []).append(
                        (parts[0], parts[1], alias.name))
    issues = []
    for cls in _nodes(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        methods: dict = {}
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.setdefault(node.name, []).append(node)
        for call in ast.walk(cls):
            if not isinstance(call, ast.Call) or _opaque(call) or _call_name(call) not in (
                    "create_subscription", "create_service"):
                continue
            kind = "msg" if _call_name(call) == "create_subscription" else "srv"
            if any(keyword.arg in (None, "raw") and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
                   for keyword in call.keywords):
                continue  # a raw subscription hands the callback bytes, not the message
            type_node = _argument(call, 0, "msg_type" if kind == "msg" else "srv_type")
            known = imported.get(type_node.id, []) if isinstance(type_node, ast.Name) else []
            if len(known) != 1 or known[0][1] != kind:
                continue
            found = methods.get(_self_attribute(_argument(call, 2, "callback")) or "", [])
            if len(found) != 1 or found[0].decorator_list or found[0].args.vararg:
                continue
            sections, origin = _find_interface(packages, known[0][0], kind, known[0][2])
            parameters = [arg.arg for arg in found[0].args.posonlyargs + found[0].args.args][1:]
            if sections is None or len(sections) != (1 if kind == "msg" else 2) or len(
                    parameters) < len(sections):
                continue
            for parameter, fields, part in zip(parameters, sections, ("request", "response")):
                uses = _field_uses(found[0], parameter)
                for attribute, line in uses or []:
                    if attribute in fields or attribute.startswith("_") or (
                            attribute in INTERFACE_MEMBERS):
                        continue
                    what = (f"{known[0][0]}/{kind}/{known[0][2]}" if kind == "msg"
                            else f"the {part} of {known[0][0]}/{kind}/{known[0][2]}")
                    issues.append(_issue(
                        package, path, line, "error", "interface-field",
                        f"'{parameter}.{attribute}': {what} has no field '{attribute}' "
                        f"(it has: {', '.join(sorted(fields)) or 'none'}); this line raises "
                        "AttributeError." + (f" The definition was read from {origin}." if origin else "")))
    return issues


def check_workspace(root, deadline: Optional[float] = None) -> tuple:
    """(issues, skipped, packages) for every ROS package found under *root*.

    *deadline* is a time.monotonic() value. Once it has passed, the checks that
    have not started are not run, and *skipped* says which: a partial result is
    labelled as partial, never returned as if it were the whole workspace.
    """
    root = Path(root).resolve()
    issues: list = []
    packages, skipped = _discover(root, deadline)
    checked = 0
    roots = _python_roots(packages)
    _resolve_module.cache_clear()  # files may have changed since the last run in this process
    _module_names.cache_clear()
    for package in packages:
        if _expired(deadline):
            break
        checked += 1
        nodes = launch_nodes(package)
        issues.extend(check_install_rules(package))
        issues.extend(check_executables(package, nodes))
        found, missed = check_parameter_files(package, nodes)
        issues.extend(found)
        skipped.extend(missed)
        issues.extend(check_dependencies(package))
        issues.extend(check_launch_against_source(package, nodes))
        launch = set(launch_files(package))
        seen: dict = {}
        for path in package.files:
            if path.suffix == ".py" and path not in launch and path.name != "setup.py":
                issues.extend(check_node_source(package, path))
                issues.extend(check_interface_fields(packages, package, path))
            if path.suffix == ".py" and path.name != "setup.py" and not _is_test(
                    path.relative_to(package.root)):
                issues.extend(check_python(roots, package, path, seen))
        _note_repeats(seen)
    if checked < len(packages):
        skipped.append(f"time limit reached: {checked} of {len(packages)} packages were checked")
    elif packages and not _expired(deadline):
        issues.extend(check_names(packages))
    if packages and _expired(deadline):
        skipped.append("time limit reached: QoS declarations were not compared")
    elif packages:
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
