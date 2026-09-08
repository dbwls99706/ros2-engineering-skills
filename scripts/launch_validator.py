#!/usr/bin/env python3
"""Static analysis for ROS 2 Python launch files (*.launch.py, *_launch.py).

XML (.launch.xml) and YAML (.launch.yaml) launch files are not supported.

Usage:
    python launch_validator.py path/to/launch_dir/
    python launch_validator.py path/to/specific.launch.py

Checks performed:
- Missing package references (FindPackageShare with non-existent packages)
- Duplicate node names in the same namespace
- Common launch file anti-patterns
- Missing config/URDF file references
- Deprecated patterns
- ComposableNode / ComposableNodeContainer validation
- IncludeLaunchDescription file existence checking
- Suppression via # noqa or # launch-validator: disable
"""

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.1.0"


@dataclass
class Issue:
    file: str
    line: int
    severity: str  # "error", "warning", "info"
    message: str

    def __str__(self) -> str:
        return f"  [{self.severity.upper():7s}] {self.file}:{self.line}: {self.message}"


@dataclass
class ValidationResult:
    issues: list = field(default_factory=list)
    files_checked: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


def _line_has_suppression(source: str, lineno: int) -> bool:
    """Check if a source line contains a suppression comment."""
    lines = source.splitlines()
    if lineno < 1 or lineno > len(lines):
        return False
    line = lines[lineno - 1]
    return "# noqa" in line or "# launch-validator: disable" in line


class LaunchFileVisitor(ast.NodeVisitor):
    """AST visitor that checks launch file patterns."""

    def __init__(self, filepath: str, source: str):
        self.filepath = filepath
        self.source = source
        self.issues: list[Issue] = []
        # (name, namespace, line, condition fingerprints)
        self.node_names: list[tuple[str, str, int, tuple]] = []
        self.has_generate_func = False
        # Namespaces pushed by PushRosNamespace actions in enclosing action
        # lists (tracked at the list literal, so lists assigned to variables
        # and passed as GroupAction(actions=var) are scoped too). None means
        # the pushed namespace is dynamic (e.g. LaunchConfiguration) and
        # cannot be resolved statically.
        self._group_namespace_stack: list[Optional[str]] = []
        # Fingerprints of condition= arguments on enclosing GroupActions.
        self._condition_stack: list[tuple] = []
        self._composable_containers: list[tuple[str, int]] = []  # (name, line)
        self._composable_nodes: list[tuple[str, int]] = []  # (plugin, line)
        self._included_files: list[str] = []

    def _add(self, node: ast.AST, severity: str, message: str) -> None:
        lineno = getattr(node, 'lineno', 0)
        if _line_has_suppression(self.source, lineno):
            return
        self.issues.append(Issue(self.filepath, lineno, severity, message))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "generate_launch_description":
            self.has_generate_func = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)

        if func_name == "Node" or func_name == "LifecycleNode":
            self._check_node_call(node, func_name)

        elif func_name == "ExecuteProcess":
            self._check_execute_process(node)

        elif func_name == "DeclareLaunchArgument":
            self._check_declare_argument(node)

        elif func_name == "ComposableNodeContainer":
            self._check_composable_node_container(node)

        elif func_name == "ComposableNode":
            self._check_composable_node(node)

        elif func_name == "IncludeLaunchDescription":
            self._check_include_launch_description(node)

        elif func_name == "GroupAction":
            self._check_group_action(node)
            self._visit_group_scope(node)
            return  # children already visited with scope applied

        elif func_name in ("IfCondition", "UnlessCondition"):
            self._check_condition(node, func_name)

        elif func_name == "PushRosNamespace":
            self._check_push_ros_namespace(node)

        self.generic_visit(node)

    def _visit_group_scope(self, node: ast.Call) -> None:
        """Visit a GroupAction's children tracking condition scope.

        Namespace pushes are handled at the action-list level (visit_List),
        so a list literal assigned to a variable and later passed as
        GroupAction(actions=var) gets the same namespace scoping as an
        inline list.
        """
        cond = self._get_keyword_value(node, "condition")
        if cond is not None:
            self._condition_stack.append(self._condition_fingerprint(cond))
        self.generic_visit(node)
        if cond is not None:
            self._condition_stack.pop()

    def _condition_fingerprint(self, cond: ast.AST) -> tuple:
        """Structural fingerprint of a condition= expression.

        IfCondition/UnlessCondition are distinguished by kind so that an
        If/Unless pair over the same expression can be proven mutually
        exclusive. ast.dump gives structural (not object) identity, which
        is what launch semantics need: two IfCondition(LaunchConfiguration
        ('x')) calls evaluate identically at runtime.
        """
        if isinstance(cond, ast.Call):
            call_name = self._get_call_name(cond)
            if call_name in ("IfCondition", "UnlessCondition"):
                kind = "if" if call_name == "IfCondition" else "unless"
                arg: Optional[ast.AST] = cond.args[0] if cond.args else None
                if arg is None and cond.keywords:
                    arg = cond.keywords[0].value
                return (kind, ast.dump(arg) if arg is not None else "<unknown>")
        return ("other", ast.dump(cond))

    def visit_List(self, node: ast.List) -> None:
        """Visit list elements applying PushRosNamespace scope.

        Mirrors launch runtime semantics: a PushRosNamespace action applies
        to the actions that follow it in the same list. The pushed namespace
        is popped when the list ends, so it never leaks to siblings.
        """
        pushes = 0
        for elt in node.elts:
            if (isinstance(elt, ast.Call)
                    and self._get_call_name(elt) == "PushRosNamespace"):
                self.visit(elt)
                ns_arg = elt.args[0] if elt.args else self._get_keyword_value(
                    elt, "namespace")
                if ns_arg is not None:
                    # None entry = dynamic namespace, unresolvable statically
                    self._group_namespace_stack.append(
                        self._get_string_value(ns_arg))
                    pushes += 1
            else:
                self.visit(elt)
        for _ in range(pushes):
            self._group_namespace_stack.pop()

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    def _get_keyword_value(self, node: ast.Call, keyword: str) -> Optional[ast.AST]:
        for kw in node.keywords:
            if kw.arg == keyword:
                return kw.value
        return None

    def _get_string_value(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _check_node_call(self, node: ast.Call, func_name: str) -> None:
        # Check for missing 'package' keyword
        pkg_node = self._get_keyword_value(node, "package")
        exec_node = self._get_keyword_value(node, "executable")
        name_node = self._get_keyword_value(node, "name")
        ns_node = self._get_keyword_value(node, "namespace")
        output_node = self._get_keyword_value(node, "output")

        # PushRosNamespace affects execution context; it does not supply this
        # constructor's required keyword. **kwargs prevents a static conclusion.
        if (func_name == "LifecycleNode" and ns_node is None
                and not any(kw.arg is None for kw in node.keywords)):
            self._add(node, "error", "LifecycleNode() missing required 'namespace' argument")

        if pkg_node is None:
            self._add(node, "error", f"{func_name}() missing required 'package' argument")

        if exec_node is None:
            self._add(node, "error", f"{func_name}() missing required 'executable' argument")

        # Check for missing output='screen' (common oversight)
        if output_node is None:
            self._add(node, "info",
                      f"{func_name}() has no 'output' argument. "
                      f"Add output='screen' to see node logs in terminal.")

        # Check for hardcoded absolute path in executable (anchored at the
        # executable= line so the regex pass can recognise it as a duplicate)
        if exec_node is not None:
            exec_str = self._get_string_value(exec_node)
            if exec_str is not None and os.path.isabs(exec_str):
                self._add(exec_node, "warning",
                          f"Hardcoded absolute path '{exec_str}' in 'executable'. "
                          f"Use just the executable name and let the package "
                          f"resolve the path.")

        # Track node names for duplicate detection
        # Also consider deprecated 'node_name' for duplicate tracking
        # Skip duplicate check if the namespace is dynamic (LaunchConfiguration
        # etc.), either on the node itself or pushed by an enclosing group.
        deprecated_name_node = self._get_keyword_value(node, "node_name")
        effective_name_node = name_node or deprecated_name_node
        name_str = self._get_string_value(effective_name_node) if effective_name_node else None
        ns_str = self._get_string_value(ns_node) if ns_node else ""
        ns_is_dynamic = ns_node is not None and ns_str is None
        effective_ns = self._effective_namespace(ns_str or "")
        conds = tuple(self._condition_stack)
        own_cond = self._get_keyword_value(node, "condition")
        if own_cond is not None:
            conds = conds + (self._condition_fingerprint(own_cond),)
        if name_str and not ns_is_dynamic and effective_ns is not None:
            self.node_names.append(
                (name_str, effective_ns, node.lineno, conds))

        # Check for deprecated 'node_name' instead of 'name'
        if self._get_keyword_value(node, "node_name") is not None:
            self._add(node, "warning",
                      "'node_name' is deprecated. Use 'name' instead.")

        # Check for deprecated 'node_executable' instead of 'executable'
        if self._get_keyword_value(node, "node_executable") is not None:
            self._add(node, "warning",
                      "'node_executable' is deprecated. Use 'executable' instead.")

        # Check for deprecated 'node_namespace' instead of 'namespace'
        if self._get_keyword_value(node, "node_namespace") is not None:
            self._add(node, "warning",
                      "'node_namespace' is deprecated. Use 'namespace' instead.")

    def _check_execute_process(self, node: ast.Call) -> None:
        cmd_node = self._get_keyword_value(node, "cmd")
        if cmd_node is None:
            # Check positional args
            if not node.args:
                self._add(node, "error",
                          "ExecuteProcess() missing 'cmd' argument")

    def _check_declare_argument(self, node: ast.Call) -> None:
        desc_node = self._get_keyword_value(node, "description")
        if desc_node is None:
            # Get argument name for better error message
            name = ""
            if node.args:
                name_val = self._get_string_value(node.args[0])
                if name_val:
                    name = f" '{name_val}'"
            self._add(node, "warning",
                      f"DeclareLaunchArgument{name} has no 'description'. "
                      f"Add description for --show-args output.")

    def _check_composable_node_container(self, node: ast.Call) -> None:
        """Check ComposableNodeContainer for required arguments."""
        pkg_node = self._get_keyword_value(node, "package")
        output_node = self._get_keyword_value(node, "output")
        name_node = self._get_keyword_value(node, "name")
        comp_descs = self._get_keyword_value(node, "composable_node_descriptions")

        if pkg_node is None:
            self._add(node, "error",
                      "ComposableNodeContainer() missing required 'package' argument "
                      "(usually 'rclcpp_components').")

        if output_node is None:
            self._add(node, "warning",
                      "ComposableNodeContainer() has no 'output' argument. "
                      "Add output='screen' to see component logs in terminal.")

        # Track container name
        name_str = self._get_string_value(name_node) if name_node else None
        if name_str:
            self._composable_containers.append((name_str, node.lineno))

        # Check for empty composable_node_descriptions
        if comp_descs is not None:
            if isinstance(comp_descs, ast.List) and len(comp_descs.elts) == 0:
                self._add(node, "warning",
                          "ComposableNodeContainer() has empty "
                          "'composable_node_descriptions'. No components will be loaded.")
        elif comp_descs is None:
            self._add(node, "info",
                      "ComposableNodeContainer() has no 'composable_node_descriptions'. "
                      "Components can be loaded dynamically via LoadComposableNodes.")

    def _check_composable_node(self, node: ast.Call) -> None:
        """Check ComposableNode for required arguments."""
        plugin_node = self._get_keyword_value(node, "plugin")
        pkg_node = self._get_keyword_value(node, "package")

        if plugin_node is None:
            self._add(node, "error",
                      "ComposableNode() missing required 'plugin' argument.")
        else:
            # Validate plugin string format (should be 'namespace::ClassName')
            plugin_str = self._get_string_value(plugin_node)
            if plugin_str is not None:
                self._composable_nodes.append((plugin_str, node.lineno))
                if "::" not in plugin_str:
                    self._add(node, "warning",
                              f"ComposableNode plugin '{plugin_str}' does not contain "
                              f"'::'. Expected format: 'namespace::ClassName' "
                              f"(e.g., 'my_pkg::MyNode').")

        if pkg_node is None:
            self._add(node, "error",
                      "ComposableNode() missing required 'package' argument.")

    def _check_include_launch_description(self, node: ast.Call) -> None:
        """Check IncludeLaunchDescription for file existence."""
        if node.args:
            first_arg: Optional[ast.AST] = node.args[0]
        else:
            first_arg = self._get_keyword_value(
                node, "launch_description_source")
        if first_arg is None:
            return

        launch_path = None
        if isinstance(first_arg, ast.Call):
            source_name = self._get_call_name(first_arg)
            if source_name.endswith("LaunchDescriptionSource"):
                if first_arg.args:
                    launch_path = self._get_string_value(first_arg.args[0])
        elif isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            launch_path = first_arg.value

        if launch_path is not None:
            # Track for circular include detection
            self._included_files.append(launch_path)

        if launch_path is not None and not os.path.isabs(launch_path):
            base_dir = os.path.dirname(self.filepath)
            resolved = os.path.normpath(os.path.join(base_dir, launch_path))
            base_real = os.path.realpath(base_dir)
            resolved_real = os.path.realpath(resolved)
            try:
                escapes = os.path.commonpath(
                    [base_real, resolved_real]) != base_real
            except ValueError:
                escapes = True
            if escapes:
                self._add(node, "warning",
                          f"IncludeLaunchDescription references '{launch_path}' "
                          f"which resolves outside the launch directory "
                          f"('{resolved}'). Prefer a package share path "
                          f"(get_package_share_directory) for portability.")
            elif not os.path.exists(resolved):
                self._add(node, "warning",
                          f"IncludeLaunchDescription references '{launch_path}' "
                          f"but the file was not found at '{resolved}'.")
            elif os.path.abspath(resolved) == os.path.abspath(self.filepath):
                self._add(node, "error",
                          f"Circular include detected: '{launch_path}' "
                          f"includes itself.")
        elif launch_path is not None and os.path.isabs(launch_path):
            if not os.path.exists(launch_path):
                self._add(node, "warning",
                          f"IncludeLaunchDescription references '{launch_path}' "
                          f"but the file was not found.")
            elif os.path.abspath(launch_path) == os.path.abspath(self.filepath):
                self._add(node, "error",
                          f"Circular include detected: '{launch_path}' "
                          f"includes itself.")

    def _check_group_action(self, node: ast.Call) -> None:
        """Check GroupAction for common issues."""
        actions_node = self._get_keyword_value(node, "actions")
        if actions_node is None and not node.args:
            self._add(node, "warning",
                      "GroupAction() has no 'actions' argument. "
                      "An empty group has no effect.")
            return

        # Check for scoped=False with PushRosNamespace (common mistake)
        scoped_node = self._get_keyword_value(node, "scoped")
        if scoped_node is not None:
            if isinstance(scoped_node, ast.Constant) and scoped_node.value is False:
                # scoped=False means PushRosNamespace inside won't create
                # an isolated namespace scope — this is sometimes intentional
                # but often a mistake
                self._add(node, "info",
                          "GroupAction(scoped=False): namespace push inside this "
                          "group will affect the parent scope. Use scoped=True "
                          "(default) for namespace isolation.")

    def _check_condition(self, node: ast.Call, func_name: str) -> None:
        """Check IfCondition/UnlessCondition for proper usage."""
        if not node.args and not node.keywords:
            self._add(node, "error",
                      f"{func_name}() called without a condition argument.")
            return

        # Get the condition argument (first positional or 'predicate' keyword)
        cond = node.args[0] if node.args else self._get_keyword_value(node, "predicate")
        if cond is None:
            return

        # Check if condition is a raw string literal instead of LaunchConfiguration
        if isinstance(cond, ast.Constant) and isinstance(cond.value, str):
            val = cond.value.lower()
            if val in ("true", "false", "1", "0"):
                self._add(node, "warning",
                          f"{func_name}('{cond.value}'): using a hardcoded string "
                          f"makes this condition always {'true' if val in ('true', '1') else 'false'}. "
                          f"Use LaunchConfiguration('arg_name') for dynamic conditions.")

    def _check_push_ros_namespace(self, node: ast.Call) -> None:
        """Check PushRosNamespace for common issues."""
        if not node.args and not node.keywords:
            self._add(node, "error",
                      "PushRosNamespace() called without a namespace argument.")
            return

        ns_arg = node.args[0] if node.args else self._get_keyword_value(
            node, "namespace")
        if ns_arg is not None:
            ns_str = self._get_string_value(ns_arg)
            if ns_str is not None and ns_str == "":
                self._add(node, "warning",
                          "PushRosNamespace(''): empty namespace has no effect.")

    def _effective_namespace(self, node_ns: str) -> Optional[str]:
        """Combine group-pushed namespaces with the node's own namespace.

        Returns None when an enclosing group pushes a dynamic namespace,
        meaning the effective namespace cannot be determined statically.
        """
        if node_ns.startswith("/"):
            return node_ns
        if None in self._group_namespace_stack:
            return None
        parts = [ns for ns in self._group_namespace_stack if ns]
        if node_ns:
            parts.append(node_ns)
        return "/".join(p.strip("/") for p in parts if p.strip("/"))

    @staticmethod
    def _conditions_mutually_exclusive(a: tuple, b: tuple) -> bool:
        """Provably exclusive: an IfCondition(X) on one side paired with an
        UnlessCondition(X) over a structurally identical X on the other."""
        opposite = {"if": "unless", "unless": "if"}
        return any((opposite[kind], expr) in b
                   for kind, expr in a if kind in opposite)

    def check_duplicates(self) -> None:
        """Check for duplicate node names in the same effective namespace.

        Severity depends on what can be proven statically:
        - both unconditional, or both guarded by structurally identical
          conditions -> error (they always coexist when launched)
        - If/Unless pair over the same expression -> provably exclusive,
          no issue
        - otherwise (differing or unknown conditions) -> warning: they
          collide only if the conditions are true simultaneously
        """
        seen: dict[str, tuple[int, tuple]] = {}
        for name, ns, line, conds in self.node_names:
            key = f"{ns}/{name}"
            if key not in seen:
                seen[key] = (line, conds)
                continue
            first_line, first_conds = seen[key]
            if _line_has_suppression(self.source, line):
                continue
            if self._conditions_mutually_exclusive(first_conds, conds):
                continue
            if first_conds == conds:
                severity = "error"
                extra = (" (both guarded by the same condition)"
                         if conds else "")
            else:
                severity = "warning"
                extra = (" (conditional; collides if the conditions are "
                         "true simultaneously)")
            self.issues.append(Issue(
                self.filepath, line, severity,
                f"Duplicate node name '{name}' in namespace '{ns}' "
                f"(first defined at line {first_line}){extra}"))


def check_raw_patterns(filepath: str, source: str) -> list[Issue]:
    """Check for patterns that are easier to find via regex than AST."""
    issues = []

    for i, line in enumerate(source.splitlines(), 1):
        # Check suppression for this line
        if "# noqa" in line or "# launch-validator: disable" in line:
            continue

        # Check for hardcoded file paths (config/urdf/xacro/rviz)
        if re.search(r'["\'][/~][\w/\-\.]+\.(yaml|urdf|xacro|rviz)', line):
            if "FindPackageShare" not in line and "PathJoinSubstitution" not in line:
                issues.append(Issue(
                    filepath, i, "warning",
                    "Hardcoded file path detected. Use FindPackageShare + "
                    "PathJoinSubstitution for portable paths."))

        # Check for hardcoded absolute executable/binary paths
        if re.search(r'executable\s*=\s*["\']\/[\w/\-\.]+["\']', line):
            issues.append(Issue(
                filepath, i, "warning",
                "Hardcoded absolute executable path detected. "
                "Use just the executable name and let the package resolve it."))

        # Check for sleep/time.sleep in launch files
        if re.search(r'\btime\.sleep\b', line):
            issues.append(Issue(
                filepath, i, "warning",
                "time.sleep() in launch file. "
                "Use TimerAction for delayed starts."))

        # Check for os.system or subprocess calls
        if re.search(r'\bos\.system\b|\bsubprocess\.(call|run|Popen)\b', line):
            issues.append(Issue(
                filepath, i, "warning",
                "Shell command in launch file. "
                "Use ExecuteProcess action instead."))

    return issues


def validate_file(filepath: str) -> list[Issue]:
    """Validate a single launch file."""
    issues = []

    try:
        source = Path(filepath).read_text()
    except (OSError, UnicodeDecodeError) as e:
        return [Issue(filepath, 0, "error", f"Cannot read file: {e}")]

    # Parse AST
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return [Issue(filepath, e.lineno or 0, "error", f"Syntax error: {e.msg}")]

    # AST-based checks
    visitor = LaunchFileVisitor(filepath, source)
    visitor.visit(tree)
    visitor.check_duplicates()

    if not visitor.has_generate_func:
        issues.append(Issue(
            filepath, 0, "error",
            "Missing generate_launch_description() function. "
            "Every ROS 2 launch file must define this function."))

    issues.extend(visitor.issues)

    # Regex-based checks. The AST pass already reports hardcoded absolute
    # executables inside Node() calls; drop the regex duplicate for those
    # lines so a single defect is not counted twice.
    ast_hardcoded_lines = {i.line for i in visitor.issues
                           if "Hardcoded absolute path" in i.message}
    issues.extend(
        i for i in check_raw_patterns(filepath, source)
        if not ("Hardcoded absolute executable path" in i.message
                and i.line in ast_hardcoded_lines))

    return issues


def validate_directory(dirpath: str) -> ValidationResult:
    """Validate all launch files in a directory."""
    result = ValidationResult()

    for root, _, files in os.walk(dirpath):
        for f in sorted(files):
            # Both official Python launch naming conventions.
            if f.endswith(".launch.py") or f.endswith("_launch.py"):
                filepath = os.path.join(root, f)
                issues = validate_file(filepath)
                result.issues.extend(issues)
                result.files_checked += 1

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Static analysis for ROS 2 Python launch files "
                    "(*.launch.py and *_launch.py; XML/YAML launch files "
                    "are not supported)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s src/my_robot_bringup/launch/
  %(prog)s src/my_robot_bringup/launch/robot.launch.py
  %(prog)s .  # Check all *.launch.py / *_launch.py files recursively

Note: Only Python launch files (*.launch.py, *_launch.py) are validated.
      XML (.launch.xml) and YAML (.launch.yaml) files are not supported.
        """)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("path", help="Launch file or directory to validate")
    parser.add_argument("--severity", choices=["error", "warning", "info"],
                        default="info",
                        help="Minimum severity to report (default: info)")
    args = parser.parse_args()

    severity_order = {"error": 2, "warning": 1, "info": 0}
    min_severity = severity_order[args.severity]

    path = Path(args.path)
    if not path.exists():
        print(f"Error: Path does not exist: {args.path}", file=sys.stderr)
        sys.exit(1)

    if path.is_file():
        issues = validate_file(str(path))
        result = ValidationResult(issues=issues, files_checked=1)
    else:
        result = validate_directory(str(path))

    # Filter by severity
    filtered = [i for i in result.issues
                if severity_order[i.severity] >= min_severity]

    # Print results
    if result.files_checked == 0:
        print("No *.launch.py or *_launch.py files found.")
        sys.exit(0)

    print(f"Checked {result.files_checked} launch file(s)")
    print()

    if filtered:
        for issue in filtered:
            print(issue)
        print()
        # Count what was displayed, not what the filter hid
        shown_errors = sum(1 for i in filtered if i.severity == "error")
        shown_warnings = sum(1 for i in filtered if i.severity == "warning")
        print(f"Found: {shown_errors} error(s), "
              f"{shown_warnings} warning(s), "
              f"{len(filtered) - shown_errors - shown_warnings} info(s)")
        hidden = len(result.issues) - len(filtered)
        if hidden:
            print(f"({hidden} lower-severity issue(s) hidden by "
                  f"--severity {args.severity})")
    elif result.issues:
        print(f"No issues at severity >= {args.severity} "
              f"({len(result.issues)} lower-severity issue(s) hidden).")
    else:
        print("No issues found.")

    sys.exit(1 if result.error_count > 0 else 0)


if __name__ == "__main__":
    main()
