"""Tests for launch_validator.py - ROS 2 launch file static analysis."""

import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "launch_validator.py")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from launch_validator import validate_file, validate_directory, check_raw_patterns, Issue


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True,
    )


def write_launch_file(tmp_path, name: str, content: str) -> str:
    filepath = tmp_path / name
    filepath.write_text(content)
    return str(filepath)


VALID_LAUNCH = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='my_robot',
            executable='my_node',
            name='my_node',
            output='screen',
        ),
    ])
"""

MISSING_GENERATE = """from launch import LaunchDescription
from launch_ros.actions import Node

def some_other_function():
    return LaunchDescription([])
"""

MISSING_PACKAGE = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            executable='my_node',
            name='my_node',
        ),
    ])
"""

DUPLICATE_NODES = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', executable='n1', name='my_node', output='screen'),
        Node(package='b', executable='n2', name='my_node', output='screen'),
    ])
"""

NAMESPACED_GROUPS = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    return LaunchDescription([
        GroupAction(actions=[
            PushRosNamespace('robot_1'),
            Node(package='a', executable='n', name='driver', output='screen'),
        ]),
        GroupAction(actions=[
            PushRosNamespace('robot_2'),
            Node(package='a', executable='n', name='driver', output='screen'),
        ]),
    ])
"""

DUPLICATE_IN_SAME_GROUP = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    return LaunchDescription([
        GroupAction(actions=[
            PushRosNamespace('robot_1'),
            Node(package='a', executable='n1', name='driver', output='screen'),
            Node(package='b', executable='n2', name='driver', output='screen'),
        ]),
    ])
"""

VARIABLE_ACTIONS_GROUPS = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    g1 = [
        PushRosNamespace('robot_1'),
        Node(package='a', executable='n', name='driver', output='screen'),
    ]
    g2 = [
        PushRosNamespace('robot_2'),
        Node(package='a', executable='n', name='driver', output='screen'),
    ]
    return LaunchDescription([
        GroupAction(actions=g1),
        GroupAction(actions=g2),
    ])
"""

DUPLICATE_IN_VARIABLE_ACTIONS = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    g1 = [
        PushRosNamespace('robot_1'),
        Node(package='a', executable='n1', name='driver', output='screen'),
        Node(package='b', executable='n2', name='driver', output='screen'),
    ]
    return LaunchDescription([GroupAction(actions=g1)])
"""

NODE_BEFORE_PUSH = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', executable='n', name='driver', output='screen'),
        GroupAction(actions=[
            Node(package='b', executable='n', name='driver', output='screen'),
            PushRosNamespace('late_ns'),
        ]),
    ])
"""

CONDITIONAL_NODES = """from launch import LaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    use_sim = LaunchConfiguration('use_sim')
    return LaunchDescription([
        Node(package='sim', executable='sim_driver', name='driver',
             output='screen', condition=IfCondition(use_sim)),
        Node(package='real', executable='real_driver', name='driver',
             output='screen', condition=UnlessCondition(use_sim)),
    ])
"""

CONDITIONAL_DIFFERENT_CONFIGS = """from launch import LaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', executable='n1', name='driver', output='screen',
             condition=IfCondition(LaunchConfiguration('use_a'))),
        Node(package='b', executable='n2', name='driver', output='screen',
             condition=IfCondition(LaunchConfiguration('use_b'))),
    ])
"""

CONDITIONAL_IDENTICAL_CONFIGS = """from launch import LaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', executable='n1', name='driver', output='screen',
             condition=IfCondition(LaunchConfiguration('use_sim'))),
        Node(package='b', executable='n2', name='driver', output='screen',
             condition=IfCondition(LaunchConfiguration('use_sim'))),
    ])
"""

MIXED_CONDITIONAL_UNCONDITIONAL = """from launch import LaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', executable='n1', name='driver', output='screen'),
        Node(package='b', executable='n2', name='driver', output='screen',
             condition=IfCondition(LaunchConfiguration('extra'))),
    ])
"""

DYNAMIC_GROUP_NAMESPACE = """from launch import LaunchDescription
from launch.actions import GroupAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace

def generate_launch_description():
    return LaunchDescription([
        GroupAction(actions=[
            PushRosNamespace(LaunchConfiguration('ns')),
            Node(package='a', executable='n', name='driver', output='screen'),
        ]),
        Node(package='b', executable='n', name='driver', output='screen'),
    ])
"""

DEPRECATED_KEYWORDS = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='my_robot',
            node_executable='my_node',
            node_name='my_node',
            node_namespace='/ns',
        ),
    ])
"""

HARDCODED_PATH = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    config = '/home/user/catkin_ws/config/params.yaml'
    return LaunchDescription([
        Node(
            package='my_robot',
            executable='my_node',
            name='my_node',
            parameters=[config],
            output='screen',
        ),
    ])
"""

WITH_SLEEP = """from launch import LaunchDescription
from launch_ros.actions import Node
import time

def generate_launch_description():
    time.sleep(2)
    return LaunchDescription([
        Node(package='a', executable='n', name='n', output='screen'),
    ])
"""

WITH_SUPPRESSION = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(  # noqa
            executable='my_node',
            name='my_node',
        ),
    ])
"""

HARDCODED_EXECUTABLE = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='my_robot',
            executable='/usr/bin/listener',
            name='my_node',
            output='screen',
        ),
    ])
"""

DUPLICATE_NODES_DEPRECATED = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package='a', node_executable='talker', node_name='my_node'),
        Node(package='b', executable='listener', name='my_node'),
    ])
"""

COMPOSABLE_MISSING_PLUGIN = """from launch import LaunchDescription
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode

def generate_launch_description():
    return LaunchDescription([
        ComposableNodeContainer(
            name='container',
            namespace='',
            package='rclcpp_components',
            executable='component_container',
            composable_node_descriptions=[
                ComposableNode(
                    package='my_pkg',
                    name='my_node',
                ),
            ],
            output='screen',
        ),
    ])
"""

MISSING_OUTPUT = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='my_robot',
            executable='my_node',
            name='my_node',
        ),
    ])
"""

DECLARE_ARG_NO_DESC = """from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument

def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time'),
    ])
"""

SYNTAX_ERROR_LAUNCH = """from launch import LaunchDescription

def generate_launch_description(
    # Missing closing paren
"""


class TestValidateFile:
    def test_valid_file_no_errors(self, tmp_path):
        path = write_launch_file(tmp_path, "good.launch.py", VALID_LAUNCH)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

    def test_missing_generate_function(self, tmp_path):
        path = write_launch_file(tmp_path, "bad.launch.py", MISSING_GENERATE)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("generate_launch_description" in i.message for i in errors)

    def test_missing_package_arg(self, tmp_path):
        path = write_launch_file(tmp_path, "bad.launch.py", MISSING_PACKAGE)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("package" in i.message for i in errors)

    def test_duplicate_node_names(self, tmp_path):
        path = write_launch_file(tmp_path, "dup.launch.py", DUPLICATE_NODES)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("Duplicate" in i.message for i in errors)

    def test_same_name_in_different_group_namespaces_ok(self, tmp_path):
        path = write_launch_file(tmp_path, "fleet.launch.py",
                                 NAMESPACED_GROUPS)
        issues = validate_file(path)
        assert not any("Duplicate" in i.message for i in issues)

    def test_duplicate_within_same_group_namespace(self, tmp_path):
        path = write_launch_file(tmp_path, "dup_group.launch.py",
                                 DUPLICATE_IN_SAME_GROUP)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("Duplicate" in i.message and "robot_1" in i.message
                   for i in errors)

    def test_variable_actions_lists_not_flagged_as_duplicates(self, tmp_path):
        # actions passed as a variable (not an inline list literal) must get
        # the same PushRosNamespace scoping as inline lists
        path = write_launch_file(tmp_path, "var_fleet.launch.py",
                                 VARIABLE_ACTIONS_GROUPS)
        issues = validate_file(path)
        assert not any("Duplicate" in i.message for i in issues)

    def test_duplicate_within_variable_actions_list(self, tmp_path):
        path = write_launch_file(tmp_path, "var_dup.launch.py",
                                 DUPLICATE_IN_VARIABLE_ACTIONS)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("Duplicate" in i.message and "robot_1" in i.message
                   for i in errors)

    def test_push_only_scopes_following_actions(self, tmp_path):
        # launch runtime applies PushRosNamespace to actions AFTER it in the
        # list; a node before the push stays in the root namespace and does
        # collide with a root-namespace node outside the group
        path = write_launch_file(tmp_path, "order.launch.py",
                                 NODE_BEFORE_PUSH)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("Duplicate" in i.message for i in errors)

    def test_conditional_nodes_not_flagged_as_duplicates(self, tmp_path):
        path = write_launch_file(tmp_path, "cond.launch.py",
                                 CONDITIONAL_NODES)
        issues = validate_file(path)
        assert not any("Duplicate" in i.message for i in issues)

    def test_different_conditions_warn_not_error(self, tmp_path):
        # IfCondition(use_a) / IfCondition(use_b) can both be true, but that
        # cannot be proven statically: warning, not error (exit stays 0)
        path = write_launch_file(tmp_path, "diff_cond.launch.py",
                                 CONDITIONAL_DIFFERENT_CONFIGS)
        issues = validate_file(path)
        dups = [i for i in issues if "Duplicate" in i.message]
        assert len(dups) == 1
        assert dups[0].severity == "warning"

    def test_identical_conditions_are_error(self, tmp_path):
        # Both nodes launch together whenever use_sim is true
        path = write_launch_file(tmp_path, "same_cond.launch.py",
                                 CONDITIONAL_IDENTICAL_CONFIGS)
        issues = validate_file(path)
        dups = [i for i in issues if "Duplicate" in i.message]
        assert len(dups) == 1
        assert dups[0].severity == "error"

    def test_mixed_conditional_unconditional_warns(self, tmp_path):
        path = write_launch_file(tmp_path, "mixed_cond.launch.py",
                                 MIXED_CONDITIONAL_UNCONDITIONAL)
        issues = validate_file(path)
        dups = [i for i in issues if "Duplicate" in i.message]
        assert len(dups) == 1
        assert dups[0].severity == "warning"

    def test_dynamic_group_namespace_skips_duplicate_check(self, tmp_path):
        path = write_launch_file(tmp_path, "dyn.launch.py",
                                 DYNAMIC_GROUP_NAMESPACE)
        issues = validate_file(path)
        assert not any("Duplicate" in i.message for i in issues)

    def test_deprecated_keywords_warned(self, tmp_path):
        path = write_launch_file(tmp_path, "dep.launch.py", DEPRECATED_KEYWORDS)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("deprecated" in i.message.lower() for i in warnings)

    def test_hardcoded_path_warned(self, tmp_path):
        path = write_launch_file(tmp_path, "hard.launch.py", HARDCODED_PATH)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("Hardcoded" in i.message for i in warnings)

    def test_hardcoded_executable_path_warned(self, tmp_path):
        path = write_launch_file(tmp_path, "exec.launch.py",
                                 HARDCODED_EXECUTABLE)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("Hardcoded" in i.message and "executable" in i.message.lower()
                   for i in warnings)

    def test_duplicate_nodes_with_deprecated_name(self, tmp_path):
        path = write_launch_file(tmp_path, "dup_dep.launch.py",
                                 DUPLICATE_NODES_DEPRECATED)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("Duplicate" in i.message for i in errors)

    def test_legacy_namespace_distinguishes_same_basename(self, tmp_path):
        source = DUPLICATE_NODES.replace(
            "name='my_node', output='screen'",
            "node_name='my_node', node_namespace='/sensors', output='screen'", 1)
        path = write_launch_file(tmp_path, "legacy_ns.launch.py", source)
        issues = validate_file(path)
        assert not any("Duplicate" in issue.message for issue in issues)

    def test_legacy_and_modern_namespaces_can_collide(self, tmp_path):
        source = DUPLICATE_NODES.replace(
            "name='my_node', output='screen'",
            "node_name='my_node', node_namespace='/sensors', output='screen'", 1)
        source = source.replace(
            "name='my_node', output='screen'",
            "name='my_node', namespace='/sensors', output='screen'")
        path = write_launch_file(tmp_path, "legacy_collision.launch.py", source)
        issues = validate_file(path)
        assert any("Duplicate" in issue.message and "sensors" in issue.message
                   for issue in issues)

    def test_dynamic_legacy_namespace_does_not_prove_collision(self, tmp_path):
        source = DUPLICATE_NODES.replace(
            "name='my_node', output='screen'",
            "node_name='my_node', node_namespace=LaunchConfiguration('ns'), output='screen'", 1)
        path = write_launch_file(tmp_path, "legacy_dynamic.launch.py", source)
        issues = validate_file(path)
        assert not any("Duplicate" in issue.message for issue in issues)

    def test_sleep_warned(self, tmp_path):
        path = write_launch_file(tmp_path, "sleep.launch.py", WITH_SLEEP)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("sleep" in i.message for i in warnings)

    def test_suppression_respected(self, tmp_path):
        path = write_launch_file(tmp_path, "sup.launch.py", WITH_SUPPRESSION)
        issues = validate_file(path)
        # The Node() missing 'package' error on the suppressed line should be gone
        errors = [i for i in issues if i.severity == "error" and "package" in i.message.lower()]
        assert len(errors) == 0

    def test_composable_missing_plugin(self, tmp_path):
        path = write_launch_file(tmp_path, "comp.launch.py", COMPOSABLE_MISSING_PLUGIN)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("plugin" in i.message.lower() for i in errors)

    def test_missing_output_info(self, tmp_path):
        path = write_launch_file(tmp_path, "out.launch.py", MISSING_OUTPUT)
        issues = validate_file(path)
        infos = [i for i in issues if i.severity == "info"]
        assert any("output" in i.message.lower() for i in infos)

    def test_declare_arg_no_description(self, tmp_path):
        path = write_launch_file(tmp_path, "arg.launch.py", DECLARE_ARG_NO_DESC)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("description" in i.message.lower() for i in warnings)

    def test_syntax_error_reported(self, tmp_path):
        path = write_launch_file(tmp_path, "syn.launch.py", SYNTAX_ERROR_LAUNCH)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("yntax" in i.message for i in errors)

    def test_nonexistent_file(self):
        issues = validate_file("/nonexistent/file.launch.py")
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) > 0


class TestValidateDirectory:
    def test_finds_launch_files(self, tmp_path):
        write_launch_file(tmp_path, "a.launch.py", VALID_LAUNCH)
        write_launch_file(tmp_path, "b.launch.py", VALID_LAUNCH)
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 2

    def test_ignores_non_launch_files(self, tmp_path):
        write_launch_file(tmp_path, "a.launch.py", VALID_LAUNCH)
        (tmp_path / "helpers.py").write_text("print('hello')")
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 1

    def test_finds_underscore_launch_files(self, tmp_path):
        # *_launch.py is the other official Python launch naming convention
        write_launch_file(tmp_path, "robot_launch.py", VALID_LAUNCH)
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 1

    def test_empty_directory(self, tmp_path):
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 0


class TestCheckRawPatterns:
    def test_detects_os_system(self):
        source = "os.system('roslaunch ...')"
        issues = check_raw_patterns("test.py", source)
        assert any("Shell command" in i.message for i in issues)

    def test_detects_subprocess(self):
        source = "subprocess.run(['ros2', 'run'])"
        issues = check_raw_patterns("test.py", source)
        assert any("Shell command" in i.message for i in issues)

    def test_detects_hardcoded_executable_path(self):
        source = "            executable='/usr/bin/my_node',"
        issues = check_raw_patterns("test.py", source)
        assert any("executable" in i.message.lower() for i in issues)

    def test_suppression_in_raw_patterns(self):
        source = "os.system('something')  # noqa"
        issues = check_raw_patterns("test.py", source)
        assert len(issues) == 0


class TestCLI:
    def test_valid_file(self, tmp_path):
        path = write_launch_file(tmp_path, "good.launch.py", VALID_LAUNCH)
        result = run_script(path)
        assert result.returncode == 0
        assert "Checked 1" in result.stdout

    def test_errors_return_nonzero(self, tmp_path):
        path = write_launch_file(tmp_path, "bad.launch.py", MISSING_GENERATE)
        result = run_script(path)
        assert result.returncode != 0

    def test_severity_filter(self, tmp_path):
        path = write_launch_file(tmp_path, "info.launch.py", MISSING_OUTPUT)
        # With --severity error, info-level "missing output" should not appear
        result = run_script(path, "--severity", "error")
        assert result.returncode == 0

    def test_nonexistent_path(self):
        result = run_script("/nonexistent/path")
        assert result.returncode != 0

    def test_directory_scan(self, tmp_path):
        write_launch_file(tmp_path, "a.launch.py", VALID_LAUNCH)
        write_launch_file(tmp_path, "b.launch.py", VALID_LAUNCH)
        result = run_script(str(tmp_path))
        assert "Checked 2" in result.stdout


EXECUTE_PROCESS_MISSING_CMD = """from launch import LaunchDescription
from launch.actions import ExecuteProcess

def generate_launch_description():
    return LaunchDescription([
        ExecuteProcess(),
    ])
"""

INCLUDE_LAUNCH_RELATIVE = """from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource

def generate_launch_description():
    return LaunchDescription([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource('nonexistent.launch.py')
        ),
    ])
"""

INCLUDE_LAUNCH_ABS = """from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription

def generate_launch_description():
    return LaunchDescription([
        IncludeLaunchDescription('/nonexistent/path.launch.py'),
    ])
"""

INCLUDE_LAUNCH_KWARG = """from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource

def generate_launch_description():
    return LaunchDescription([
        IncludeLaunchDescription(
            launch_description_source=PythonLaunchDescriptionSource(
                '/nonexistent/kwarg.launch.py')
        ),
    ])
"""

COMPOSABLE_CONTAINER_MISSING_PKG = """from launch import LaunchDescription
from launch_ros.actions import ComposableNodeContainer

def generate_launch_description():
    return LaunchDescription([
        ComposableNodeContainer(
            name='container',
            namespace='',
            executable='component_container',
        ),
    ])
"""


class TestMainFunction:
    """Test main() directly for coverage."""

    def test_main_valid_file(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        path = write_launch_file(tmp_path, "good.launch.py", VALID_LAUNCH)
        monkeypatch.setattr(
            "sys.argv", ["launch_validator.py", path])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_error_file(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        path = write_launch_file(tmp_path, "bad.launch.py", MISSING_GENERATE)
        monkeypatch.setattr(
            "sys.argv", ["launch_validator.py", path])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_directory(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        write_launch_file(tmp_path, "a.launch.py", VALID_LAUNCH)
        monkeypatch.setattr(
            "sys.argv", ["launch_validator.py", str(tmp_path)])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_severity_filter(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        path = write_launch_file(tmp_path, "info.launch.py", MISSING_OUTPUT)
        monkeypatch.setattr(
            "sys.argv",
            ["launch_validator.py", path, "--severity", "error"])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_nonexistent(self, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        monkeypatch.setattr(
            "sys.argv", ["launch_validator.py", "/nonexistent/path"])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_empty_directory(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from launch_validator import main
        monkeypatch.setattr(
            "sys.argv", ["launch_validator.py", str(tmp_path)])
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


class TestAdditionalVisitors:
    """Test AST visitor paths not covered by main test constants."""

    def test_execute_process_missing_cmd(self, tmp_path):
        path = write_launch_file(tmp_path, "exec.launch.py",
                                 EXECUTE_PROCESS_MISSING_CMD)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("cmd" in i.message.lower() for i in errors)

    def test_include_launch_relative_missing(self, tmp_path):
        path = write_launch_file(tmp_path, "inc.launch.py",
                                 INCLUDE_LAUNCH_RELATIVE)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("not found" in i.message for i in warnings)

    def test_include_launch_abs_missing(self, tmp_path):
        path = write_launch_file(tmp_path, "inc_abs.launch.py",
                                 INCLUDE_LAUNCH_ABS)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("not found" in i.message for i in warnings)

    def test_include_launch_kwarg_form_missing(self, tmp_path):
        path = write_launch_file(tmp_path, "inc_kw.launch.py",
                                 INCLUDE_LAUNCH_KWARG)
        issues = validate_file(path)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any("not found" in i.message for i in warnings)

    def test_hardcoded_executable_reported_once(self, tmp_path):
        # AST and regex passes both detect this; only one issue should remain
        path = write_launch_file(tmp_path, "exec1.launch.py",
                                 HARDCODED_EXECUTABLE)
        issues = validate_file(path)
        hardcoded = [i for i in issues
                     if "Hardcoded" in i.message
                     and "executable" in i.message.lower()]
        assert len(hardcoded) == 1

    def test_composable_container_missing_package(self, tmp_path):
        path = write_launch_file(tmp_path, "cont.launch.py",
                                 COMPOSABLE_CONTAINER_MISSING_PKG)
        issues = validate_file(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("package" in i.message.lower() for i in errors)

    def test_validation_result_counts(self, tmp_path):
        from launch_validator import ValidationResult
        result = ValidationResult()
        result.issues.append(
            Issue("test.py", 1, "error", "err"))
        result.issues.append(
            Issue("test.py", 2, "warning", "warn"))
        result.issues.append(
            Issue("test.py", 3, "info", "info"))
        assert result.error_count == 1
        assert result.warning_count == 1

    def test_suppression_out_of_range_line(self):
        from launch_validator import _line_has_suppression
        assert _line_has_suppression("hello\nworld", 0) is False
        assert _line_has_suppression("hello\nworld", 5) is False


class TestVersion:
    def test_version_flag(self):
        result = run_script("--version")
        assert result.returncode == 0
        assert "0.1.0" in result.stdout


class TestIssueDisplay:
    def test_issue_str_format(self):
        issue = Issue("test.launch.py", 10, "error", "something wrong")
        s = str(issue)
        assert "ERROR" in s
        assert "test.launch.py:10" in s
        assert "something wrong" in s


OPAQUE_EXCLUSIVE_RETURNS = """from launch import LaunchDescription
from launch.actions import OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _nodes(context):
    if LaunchConfiguration('split').perform(context) == 'false':
        return [Node(package='p', executable='bridge', name='bridge', output='screen')]
    return [
        Node(package='p', executable='bridge', name='bridge', output='screen'),
        Node(package='p', executable='native', name='native', output='screen'),
    ]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=_nodes)])
"""


class TestControlFlowDuplicates:
    def test_exclusive_opaque_function_returns_are_not_an_error(self, tmp_path):
        # Only one return value is ever launched; a real bringup file failed
        # with exit 1 on this shape. The file does not prove which branch
        # runs, so a warning remains.
        path = write_launch_file(tmp_path, "opaque.launch.py", OPAQUE_EXCLUSIVE_RETURNS)
        dups = [i for i in validate_file(path) if "Duplicate" in i.message]
        assert len(dups) == 1 and dups[0].severity == "warning"
        assert "different branches or return statements" in dups[0].message
        assert run_script(path).returncode == 0

    def test_duplicate_inside_one_return_is_still_an_error(self, tmp_path):
        source = OPAQUE_EXCLUSIVE_RETURNS.replace("name='native'", "name='bridge'")
        path = write_launch_file(tmp_path, "dup_return.launch.py", source)
        errors = [i for i in validate_file(path) if i.severity == "error" and "Duplicate" in i.message]
        assert len(errors) == 1
        assert run_script(path).returncode == 1

    def test_if_else_branches_are_exclusive_but_if_and_after_are_not(self, tmp_path):
        source = """from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    nodes = []
    if True:
        nodes.append(Node(package='a', executable='x', name='cam', output='screen'))
    else:
        nodes.append(Node(package='b', executable='y', name='cam', output='screen'))
    nodes.append(Node(package='c', executable='z', name='lidar', output='screen'))
    nodes.append(Node(package='d', executable='w', name='lidar', output='screen'))
    return LaunchDescription(nodes)
"""
        issues = {i.message.split("'")[1]: i for i in validate_file(
            write_launch_file(tmp_path, "branches.launch.py", source)) if "Duplicate" in i.message}
        assert issues["cam"].severity == "warning"
        assert issues["lidar"].severity == "error"

    def test_unconditional_duplicates_and_same_condition_stay_errors(self, tmp_path):
        path = write_launch_file(tmp_path, "dup.launch.py", DUPLICATE_NODES)
        assert any(i.severity == "error" and "Duplicate" in i.message for i in validate_file(path))


class TestEntryPointBinding:
    def test_reexported_or_aliased_entry_point_is_accepted(self, tmp_path):
        for name, source in [
                ("reexport.launch.py",
                 "from my_pkg.common import generate_launch_description\n"),
                ("alias.launch.py",
                 "from launch import LaunchDescription\n"
                 "def _build():\n    return LaunchDescription([])\n"
                 "generate_launch_description = _build\n"),
                ("test_talker.launch.py",
                 "import launch\nimport launch_testing.actions\n"
                 "def generate_test_description():\n"
                 "    return launch.LaunchDescription([launch_testing.actions.ReadyToTest()])\n")]:
            path = write_launch_file(tmp_path, name, source)
            assert not any("generate_launch_description" in i.message
                           for i in validate_file(path)), name

    def test_nested_entry_point_is_not_module_scope(self, tmp_path):
        path = write_launch_file(tmp_path, "nested.launch.py",
                                 "def helper():\n"
                                 "    def generate_launch_description():\n"
                                 "        return None\n")
        assert any(i.severity == "error" and "Missing generate_launch_description" in i.message
                   for i in validate_file(path))

    def test_loop_bodies_bind_only_on_some_paths(self, tmp_path):
        # `for _ in []` provably never runs: missing. Other loops may run zero
        # times and `else` is skipped after `break`: a warning, not an error.
        never = write_launch_file(tmp_path, "never.launch.py",
                                  "for _ in []:\n"
                                  "    def generate_launch_description():\n"
                                  "        return None\n")
        assert any(i.severity == "error" and "Missing" in i.message for i in validate_file(never))
        for source in ("for i in range(1):\n    break\nelse:\n"
                       "    def generate_launch_description():\n        return None\n",
                       "for i in range(1):\n    def generate_launch_description():\n"
                       "        return None\n",
                       "if flag:\n    def generate_launch_description():\n        return None\n"):
            path = write_launch_file(tmp_path, "maybe.launch.py", source)
            entry = [i for i in validate_file(path) if "generate_launch_description" in i.message]
            assert [i.severity for i in entry] == ["warning"], source
            assert "some execution paths" in entry[0].message
            assert run_script(path).returncode == 0
        both = write_launch_file(tmp_path, "both.launch.py",
                                 "try:\n    from a import generate_launch_description\n"
                                 "except ImportError:\n"
                                 "    def generate_launch_description():\n        return None\n")
        assert not any("generate_launch_description" in i.message for i in validate_file(both))

    def test_utf8_source_is_read_independently_of_the_locale(self, tmp_path):
        path = tmp_path / "robot.launch.py"
        path.write_bytes(("# 로봇 브링업\n" + VALID_LAUNCH).encode("utf-8"))
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        result = subprocess.run([sys.executable, SCRIPT, str(path)],
                                capture_output=True, text=True, env=env)
        assert "Cannot read file" not in result.stdout
        assert result.returncode == 0, result.stdout

    def test_coding_declaration_is_honoured_and_bad_bytes_are_an_error(self, tmp_path):
        legacy = tmp_path / "legacy.launch.py"
        legacy.write_bytes(("# -*- coding: cp949 -*-\n# 로봇\n" + VALID_LAUNCH).encode("cp949"))
        assert not any("Cannot read" in i.message for i in validate_file(str(legacy)))
        broken = tmp_path / "broken.launch.py"
        broken.write_bytes(("# 로봇\n" + VALID_LAUNCH).encode("cp949"))
        issues = validate_file(str(broken))
        # tokenize reports undecodable bytes as a missing encoding declaration.
        assert len(issues) == 1 and "encoding" in issues[0].message


class TestDirectoryScope:
    def test_colcon_outputs_and_non_launch_modules_are_skipped(self, tmp_path):
        src = tmp_path / "src" / "pkg"
        (src / "launch").mkdir(parents=True)
        (src / "test").mkdir()
        (src / "launch" / "robot.launch.py").write_text(VALID_LAUNCH)
        (src / "launch" / "robot_launch.py").write_text(VALID_LAUNCH)
        # pytest module (even importing launch) and process fake that merely end in _launch.py
        (src / "test" / "test_http_launch.py").write_text(
            "import launch\nimport subprocess\ndef test_x():\n    assert subprocess\n")
        (src / "test" / "fake_launch.py").write_text("import signal\n")
        (src / "tools").mkdir()
        (src / "tools" / "helper_launch.py").write_text("import os\n")
        for copy in ("install/pkg/share/pkg/launch", "build/pkg/launch", ".git/x"):
            (tmp_path / copy).mkdir(parents=True)
            (tmp_path / copy / "robot.launch.py").write_text(MISSING_GENERATE)
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 2
        assert result.error_count == 0

    def test_underscore_launch_file_with_entry_point_in_tests_is_checked(self, tmp_path):
        (tmp_path / "test").mkdir()
        (tmp_path / "test" / "test_bringup_launch.py").write_text(MISSING_GENERATE.replace(
            "some_other_function", "generate_launch_description").replace(
            "LaunchDescription([])", "None"))
        assert validate_directory(str(tmp_path)).files_checked == 1


# Entry-point shapes shared with the Stop hook: both validators must reach the
# same verdict, since one runs at turn end and the other in CI and by hand.
ENTRY_POINT_CASES = [
    ("def", "def generate_launch_description():\n    return None\n", []),
    ("reexport", "from my_pkg.common import generate_launch_description\n", []),
    ("alias", "def _b():\n    return None\ngenerate_launch_description = _b\n", []),
    ("lambda", "generate_launch_description = lambda: None\n", []),
    ("factory", "import f\ngenerate_launch_description = f.make()\n", []),
    ("none", "generate_launch_description = None\n", ["error"]),
    ("literal", "generate_launch_description = 'x'\n", ["error"]),
    ("list", "generate_launch_description = []\n", ["error"]),
    ("module", "import os as generate_launch_description\n", ["error"]),
    ("async", "async def generate_launch_description():\n    return None\n", ["error"]),
    ("async-alias", "async def _b():\n    return None\ngenerate_launch_description = _b\n", ["error"]),
    ("none-alias", "x = None\ngenerate_launch_description = x\n", ["error"]),
    ("rebound", "def generate_launch_description():\n    return None\n"
                "generate_launch_description = None\n", ["error"]),
    ("deleted", "def generate_launch_description():\n    return None\n"
                "del generate_launch_description\n", ["error"]),
    ("missing", "def other():\n    return None\n", ["error"]),
    ("if-only", "if flag:\n    def generate_launch_description():\n        return None\n", ["warning"]),
    ("empty-loop", "for _ in []:\n    def generate_launch_description():\n        return None\n",
     ["error"]),
]


def _entry_issues(issues):
    return sorted(i.severity for i in issues if "generate_launch_description" in i.message)


class TestEntryPointMustBeASyncCallable:
    @pytest.mark.parametrize("label,source,expected", ENTRY_POINT_CASES,
                             ids=[case[0] for case in ENTRY_POINT_CASES])
    def test_entry_point_verdict(self, tmp_path, label, source, expected):
        # A name that is bound to None, a literal, a module or a coroutine
        # function is provably not a callable that returns a LaunchDescription.
        path = write_launch_file(tmp_path, f"{label}.launch.py", source)
        assert _entry_issues(validate_file(path)) == expected
        assert run_script(path).returncode == (1 if "error" in expected else 0)

    @pytest.mark.parametrize("label,source,expected", ENTRY_POINT_CASES,
                             ids=[case[0] for case in ENTRY_POINT_CASES])
    def test_stop_hook_reaches_the_same_verdict(self, tmp_path, label, source, expected):
        import skill_stop_hook
        path = write_launch_file(tmp_path, f"{label}.launch.py", source)
        hook = sorted(i["severity"] for i in skill_stop_hook.validate_launch_file_syntax(path))
        assert hook == _entry_issues(validate_file(path)) == expected


class TestCandidateSelectionUsesTheSyntaxTree:
    def test_a_mention_in_a_comment_is_not_launch_code(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "test").mkdir()
        (tmp_path / "tools" / "helper_launch.py").write_text(
            "# see generate_launch_description in launch/robot.launch.py\nimport os\n")
        (tmp_path / "test" / "test_http_launch.py").write_text(
            '"""generate_launch_description is covered elsewhere."""\nimport pytest\n')
        assert validate_directory(str(tmp_path)).files_checked == 0

    def test_bound_entry_points_and_launch_imports_are_candidates(self, tmp_path):
        (tmp_path / "launch").mkdir()
        (tmp_path / "test").mkdir()
        (tmp_path / "launch" / "a_launch.py").write_text(
            "async def generate_launch_description():\n    return None\n")
        (tmp_path / "launch" / "b_launch.py").write_text(
            "def build():\n    from launch import LaunchDescription\n    return LaunchDescription([])\n")
        (tmp_path / "test" / "test_c_launch.py").write_text(
            "generate_launch_description = None\n")
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 3 and result.error_count == 3

    def test_unparseable_file_falls_back_to_its_text(self, tmp_path):
        (tmp_path / "launch").mkdir()
        (tmp_path / "launch" / "broken_launch.py").write_text(
            "from launch import LaunchDescription\ndef generate_launch_description(:\n")
        (tmp_path / "launch" / "noise_launch.py").write_text("import os\ndef main(:\n")
        result = validate_directory(str(tmp_path))
        assert result.files_checked == 1
        assert any("Syntax error" in i.message for i in result.issues)
