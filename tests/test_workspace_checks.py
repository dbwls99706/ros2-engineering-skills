"""Tests for workspace_checks.py - cross-file checks of ROS 2 Python packages.

Each check is exercised three ways: the defect is reported, the corrected input
is silent, and input the check cannot decide is silent as well.
"""

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import workspace_checks as checks  # noqa: E402

NODE = """\
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Driver(Node):
    def __init__(self):
        super().__init__('driver')
        self.declare_parameter('rate', 10.0)
        self.pub = self.create_publisher(String, 'status', 10)
        self.sub = self.create_subscription(String, 'command', self.on_command, 10)

    def on_command(self, msg):
        self.pub.publish(msg)


def main():
    rclpy.init()
    rclpy.spin(Driver())
"""

LAUNCH = """\
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package='demo', executable='driver', name='driver', output='screen'),
    ])
"""

SETUP = """\
from setuptools import setup

setup(
    name='{name}',
    packages=['{name}'],
    data_files=[{data_files}],
    entry_points={entry_points},
)
"""


def make_package(root, name="demo", *, depends=("rclpy", "std_msgs"), scripts=None, installed=("launch", "config"),
                 entry_points=None, files=None, setup=True, depend_tag="depend"):
    """Write an ament_python package and return its directory."""
    package = root / name
    (package / name).mkdir(parents=True)
    (package / name / "__init__.py").write_text("")
    manifest = "".join(f"  <{depend_tag}>{item}</{depend_tag}>\n" for item in depends)
    (package / "package.xml").write_text(
        f'<?xml version="1.0"?>\n<package format="3">\n  <name>{name}</name>\n{manifest}</package>\n')
    if scripts is None:
        scripts = {"driver": f"{name}.driver:main"}
    if entry_points is None:
        listed = ", ".join(f"'{key} = {value}'" for key, value in scripts.items())
        entry_points = "{'console_scripts': [%s]}" % listed
    if setup:
        data_files = ", ".join(f"('share/{name}/{item}', ['{item}/placeholder'])" for item in installed)
        (package / "setup.py").write_text(SETUP.format(
            name=name, data_files=data_files, entry_points=entry_points))
    for relative, text in {f"{name}/driver.py": NODE, **(files or {})}.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text))
    return package


def found(root, check=None):
    issues, _skipped, _packages = checks.check_workspace(root)
    return [issue for issue in issues if check is None or issue["check"] == check]


def launch(*nodes, extra=""):
    body = ",\n        ".join(nodes)
    return ("from launch import LaunchDescription\nfrom launch_ros.actions import Node, LifecycleNode\n"
            f"{extra}\n\ndef generate_launch_description():\n    return LaunchDescription([\n        {body},\n    ])\n")


def node_source(body, base="Node", header=""):
    """A node module whose constructor body is *body*."""
    lines = textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8)
    return ("import rclpy\nfrom rclpy.node import Node\nfrom std_msgs.msg import String\n"
            f"{header}\n\nclass Driver({base}):\n    def __init__(self):\n        super().__init__('driver')\n"
            f"{lines}\n\n    def tick(self):\n        pass\n\n    def on_message(self, msg):\n        pass\n\n"
            "def main():\n    rclpy.init()\n")


class TestCleanWorkspace:
    def test_a_consistent_package_has_no_findings(self, tmp_path):
        make_package(tmp_path, files={
            "launch/demo.launch.py": LAUNCH,
            "config/params.yaml": "driver:\n  ros__parameters:\n    rate: 20.0\n"})
        issues, skipped, packages = checks.check_workspace(tmp_path)
        assert (issues, skipped) == ([], [])
        assert [package.name for package in packages] == ["demo"]

    def test_a_directory_without_packages_is_empty(self, tmp_path):
        (tmp_path / "notes.py").write_text("import rclpy\n")
        assert checks.check_workspace(tmp_path) == ([], [], [])

    def test_a_package_given_directly_is_checked(self, tmp_path):
        package = make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        assert [issue["check"] for issue in found(package)] == ["install-rules"]

    @pytest.mark.parametrize("manifest", [
        "<package", "<package><name> </name></package>", "<package></package>"])
    def test_an_unreadable_manifest_is_not_a_package(self, tmp_path, manifest):
        package = make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        (package / "package.xml").write_text(manifest)
        assert checks.check_workspace(tmp_path) == ([], [], [])

    def test_build_output_and_hidden_directories_are_not_searched(self, tmp_path):
        for skipped in ("build", "install", "log", ".cache"):
            make_package(tmp_path / skipped, installed=(), files={"launch/demo.launch.py": LAUNCH})
        assert checks.find_packages(tmp_path) == []

    def test_a_package_inside_a_package_is_not_a_second_package(self, tmp_path):
        outer = make_package(tmp_path)
        make_package(outer / "vendored", name="inner")
        assert [package.name for package in checks.find_packages(tmp_path)] == ["demo"]


class TestInstallRules:
    def test_launch_directory_that_is_not_installed(self, tmp_path):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        issue, = found(tmp_path, "install-rules")
        assert issue["severity"] == "warning" and issue["file"] == "demo/setup.py"
        assert "launch/ is not installed" in issue["message"]
        assert "launch/demo.launch.py" in issue["message"]

    def test_installed_launch_directory_is_silent(self, tmp_path):
        make_package(tmp_path, installed=("launch",), files={"launch/demo.launch.py": LAUNCH})
        assert found(tmp_path, "install-rules") == []

    def test_resource_directory_a_launch_file_loads(self, tmp_path):
        loads = LAUNCH.replace("output='screen'", "parameters=[os.path.join(share, 'config', 'p.yaml')]")
        make_package(tmp_path, installed=("launch",), files={
            "launch/demo.launch.py": loads, "config/p.yaml": "driver:\n  ros__parameters: {}\n"})
        issue, = found(tmp_path, "install-rules")
        assert "config/ is not installed" in issue["message"]

    def test_resource_directory_no_launch_file_mentions_is_silent(self, tmp_path):
        make_package(tmp_path, installed=("launch",), files={
            "launch/demo.launch.py": LAUNCH, "config/p.yaml": "a: 1\n", "rviz/view.rviz": ""})
        assert found(tmp_path, "install-rules") == []

    def test_cmake_package_is_read_from_cmakelists(self, tmp_path):
        package = make_package(tmp_path, setup=False, files={"launch/demo.launch.py": LAUNCH})
        (package / "CMakeLists.txt").write_text("project(demo)\nament_package()\n")
        issue, = found(tmp_path, "install-rules")
        assert issue["file"] == "demo/CMakeLists.txt"
        (package / "CMakeLists.txt").write_text(
            "project(demo)\ninstall(DIRECTORY launch DESTINATION share/${PROJECT_NAME})\n")
        assert found(tmp_path, "install-rules") == []

    def test_package_without_a_build_file_is_not_judged(self, tmp_path):
        make_package(tmp_path, setup=False, files={"launch/demo.launch.py": LAUNCH})
        assert found(tmp_path) == []


class TestExecutables:
    def test_launch_names_an_executable_setup_does_not_define(self, tmp_path):
        make_package(tmp_path, files={
            "launch/demo.launch.py": LAUNCH.replace("executable='driver'", "executable='driver_node'")})
        issue, = found(tmp_path, "launch-executable")
        assert issue["severity"] == "error" and issue["file"] == "demo/launch/demo.launch.py"
        assert issue["line"] == 7
        assert "'driver_node' is not a console script of demo" in issue["message"]
        assert "setup.py defines: driver" in issue["message"]

    def test_executable_of_another_package_is_not_judged(self, tmp_path):
        make_package(tmp_path, files={
            "launch/demo.launch.py": LAUNCH.replace("package='demo'", "package='nav2_bringup'")})
        assert found(tmp_path) == []

    @pytest.mark.parametrize("entry_points", [
        "ENTRY_POINTS", "{'console_scripts': SCRIPTS}", "{'console_scripts': ['driver']}",
        "{'console_scripts': [NAME + ' = demo.driver:main']}"])
    def test_entry_points_that_are_not_literal_are_not_judged(self, tmp_path, entry_points):
        make_package(tmp_path, entry_points=entry_points, files={
            "launch/demo.launch.py": LAUNCH.replace("executable='driver'", "executable='other'")})
        assert found(tmp_path) == []

    def test_setup_without_console_scripts_defines_none(self, tmp_path):
        make_package(tmp_path, entry_points="{'pytest11': []}", files={"launch/demo.launch.py": LAUNCH})
        issue, = found(tmp_path, "launch-executable")
        assert "setup.py defines: none" in issue["message"]

    @pytest.mark.parametrize("setup", ["setup(", "print('no call')\n"])
    def test_setup_that_cannot_be_read_is_not_judged(self, tmp_path, setup):
        package = make_package(tmp_path, files={
            "launch/demo.launch.py": LAUNCH.replace("executable='driver'", "executable='other'")})
        (package / "setup.py").write_text("# launch config\n" + setup)
        assert found(tmp_path) == []

    @pytest.mark.parametrize("call", [
        "Node(**settings)", "Node(*positional, package='demo', executable='other')",
        "Node(package='demo', executable='other', namespace=LaunchConfiguration('ns'))",
        "Node(package='demo', executable=LaunchConfiguration('exe'))"])
    def test_launch_calls_that_cannot_be_read_are_not_judged(self, tmp_path, call):
        make_package(tmp_path, files={"launch/demo.launch.py": launch(call)})
        assert found(tmp_path) == []

    def test_launch_file_with_a_syntax_error_is_not_judged(self, tmp_path):
        make_package(tmp_path, files={"launch/demo.launch.py": "def generate_launch_description(:\n"})
        assert found(tmp_path) == []

    def test_python_file_in_a_launch_directory_is_a_launch_file(self, tmp_path):
        make_package(tmp_path, files={
            "launch/bringup.py": LAUNCH.replace("executable='driver'", "executable='other'")})
        assert [issue["check"] for issue in found(tmp_path)] == ["launch-executable"]


class TestEntryPoints:
    def test_console_script_module_that_does_not_exist(self, tmp_path):
        make_package(tmp_path, scripts={"driver": "demo.driver:main", "viewer": "demo.viewer:main"})
        issue, = found(tmp_path, "entry-point")
        assert issue["severity"] == "error" and issue["file"] == "demo/setup.py"
        assert "'viewer' points at module demo.viewer" in issue["message"]

    def test_console_script_function_that_does_not_exist(self, tmp_path):
        make_package(tmp_path, scripts={"driver": "demo.driver:run"})
        issue, = found(tmp_path, "entry-point")
        assert "demo.driver:run" in issue["message"] and "defines no 'run'" in issue["message"]

    @pytest.mark.parametrize("definition", [
        "def run():\n    pass\n", "async def run():\n    pass\n", "class run:\n    pass\n",
        "run = make_main()\n", "from .app import run\n", "from .app import *\n", "from .app import go as run\n"])
    def test_function_defined_in_another_form_is_found(self, tmp_path, definition):
        make_package(tmp_path, scripts={"driver": "demo.driver:run"},
                     files={"demo/driver.py": "import rclpy\n" + definition})
        assert found(tmp_path) == []

    def test_module_as_a_package_directory(self, tmp_path):
        make_package(tmp_path, scripts={"tool": "demo.tool:main"},
                     files={"demo/tool/__init__.py": "def main():\n    pass\n"})
        assert found(tmp_path) == []

    def test_module_outside_the_package_is_not_judged(self, tmp_path):
        make_package(tmp_path, scripts={"driver": "elsewhere.driver:main"})
        assert found(tmp_path) == []

    def test_module_with_a_syntax_error_is_not_judged(self, tmp_path):
        make_package(tmp_path, scripts={"driver": "demo.driver:run"},
                     files={"demo/driver.py": "def run(:\n"})
        assert found(tmp_path) == []


def with_parameters(parameters="[os.path.join(share, 'config', 'params.yaml')]", name="name='driver', "):
    return launch(f"Node(package='demo', executable='driver', {name}parameters={parameters})")


class TestParameterFiles:
    def test_section_named_after_another_node(self, tmp_path):
        make_package(tmp_path, files={
            "launch/demo.launch.py": with_parameters(),
            "config/params.yaml": "driver_node:\n  ros__parameters:\n    rate: 20.0\n"})
        issue, = found(tmp_path, "parameter-section")
        assert issue["severity"] == "warning" and issue["file"] == "demo/launch/demo.launch.py"
        assert "node 'driver' is given config/params.yaml" in issue["message"]
        assert "sections: driver_node" in issue["message"]

    @pytest.mark.parametrize("section", ["driver", "/driver", "/**", "robot1/driver", "/*/driver"])
    def test_section_that_applies_to_the_node_is_silent(self, tmp_path, section):
        make_package(tmp_path, files={
            "launch/demo.launch.py": with_parameters(),
            "config/params.yaml": f'"{section}":\n  ros__parameters:\n    rate: 20.0\n'})
        assert found(tmp_path) == []

    def test_name_is_taken_from_the_source_when_the_launch_gives_none(self, tmp_path):
        make_package(tmp_path, files={
            "launch/demo.launch.py": with_parameters(name=""),
            "config/params.yaml": "other:\n  ros__parameters:\n    rate: 20.0\n"})
        assert "node 'driver'" in found(tmp_path, "parameter-section")[0]["message"]

    def test_file_reached_through_a_variable_is_followed(self, tmp_path):
        text = launch("Node(package='demo', executable='driver', name='driver', parameters=[params])",
                      extra="params = os.path.join(share, 'config', 'params.yaml')")
        make_package(tmp_path, files={
            "launch/demo.launch.py": text,
            "config/params.yaml": "other:\n  ros__parameters:\n    rate: 20.0\n"})
        assert len(found(tmp_path, "parameter-section")) == 1

    @pytest.mark.parametrize("case", [
        "computed name", "unknown name", "inline parameters", "two files", "no such file", "not a mapping",
        "no sections", "broken yaml", "variable assigned twice"])
    def test_what_cannot_be_decided_is_silent(self, tmp_path, case):
        files = {"launch/demo.launch.py": with_parameters(),
                 "config/params.yaml": "other:\n  ros__parameters:\n    rate: 20.0\n"}
        if case == "computed name":
            files["launch/demo.launch.py"] = with_parameters(name="name=LaunchConfiguration('name'), ")
        elif case == "unknown name":
            files["launch/demo.launch.py"] = with_parameters(name="")
            files["demo/driver.py"] = NODE.replace("super().__init__('driver')", "super().__init__(NAME)")
        elif case == "inline parameters":
            files["launch/demo.launch.py"] = with_parameters(
                "[os.path.join(share, 'config', 'params.yaml'), {'rate': 5.0}]")
        elif case == "two files":
            files["config/sim/params.yaml"] = files["config/params.yaml"]
        elif case == "no such file":
            files["launch/demo.launch.py"] = with_parameters("[os.path.join(share, 'config', 'missing.yaml')]")
        elif case == "not a mapping":
            files["config/params.yaml"] = "- a\n- b\n"
        elif case == "no sections":
            files["config/params.yaml"] = "rate: 20.0\nframe: base\n"
        elif case == "broken yaml":
            files["config/params.yaml"] = "driver: [unclosed\n"
        elif case == "variable assigned twice":
            files["launch/demo.launch.py"] = launch(
                "Node(package='demo', executable='driver', name='driver', parameters=[params])",
                extra="params = 'config/params.yaml'\nparams = pick()")
        make_package(tmp_path, files=files)
        assert found(tmp_path, "parameter-section") == []

    def test_missing_yaml_library_is_reported_as_skipped(self, tmp_path, monkeypatch):
        make_package(tmp_path, files={
            "launch/demo.launch.py": with_parameters(),
            "config/params.yaml": "other:\n  ros__parameters:\n    rate: 20.0\n"})
        monkeypatch.setattr(checks, "HAS_YAML", False)
        issues, skipped, _packages = checks.check_workspace(tmp_path)
        assert issues == [] and skipped == ["parameter files: PyYAML is not installed"]


class TestParameterNames:
    def workspace(self, tmp_path, yaml_text, source=NODE):
        make_package(tmp_path, files={"launch/demo.launch.py": with_parameters(),
                                      "config/params.yaml": yaml_text, "demo/driver.py": source})
        return found(tmp_path, "parameter-name")

    def test_parameter_the_node_does_not_declare(self, tmp_path):
        issue, = self.workspace(tmp_path, "driver:\n  ros__parameters:\n    rate: 20.0\n    rate_hz: 50.0\n")
        assert issue["severity"] == "warning" and issue["file"] == "demo/config/params.yaml"
        assert issue["line"] == 4
        assert "sets 'rate_hz'" in issue["message"] and "declares: rate" in issue["message"]

    def test_implicit_and_nested_parameters_are_silent(self, tmp_path):
        text = ("driver:\n  ros__parameters:\n    rate: 20.0\n    use_sim_time: true\n"
                "    qos_overrides:\n      /tf:\n        depth: 5\n    limits:\n      max: 1.0\n")
        assert self.workspace(tmp_path, text) == []

    def test_declare_parameters_list_is_read(self, tmp_path):
        source = NODE.replace("self.declare_parameter('rate', 10.0)",
                              "self.declare_parameters('', [('rate', 10.0), ('frame', 'base')])")
        text = "driver:\n  ros__parameters:\n    rate: 1.0\n    frame: odom\n    gain: 2.0\n"
        issue, = self.workspace(tmp_path, text, source)
        assert "sets 'gain'" in issue["message"] and "declares: frame, rate" in issue["message"]

    def test_node_that_declares_nothing(self, tmp_path):
        source = NODE.replace("self.declare_parameter('rate', 10.0)", "pass")
        issue, = self.workspace(tmp_path, "driver:\n  ros__parameters:\n    rate: 1.0\n", source)
        assert "declares: nothing" in issue["message"]

    @pytest.mark.parametrize("old,new", [
        ("self.declare_parameter('rate', 10.0)", "self.declare_parameter(NAME, 10.0)"),
        ("self.declare_parameter('rate', 10.0)", "self.declare_parameter(*spec)"),
        ("self.declare_parameter('rate', 10.0)", "self.declare_parameters('', PARAMETERS)"),
        ("self.declare_parameter('rate', 10.0)", "self.declare_parameters('', [(NAME, 1.0)])"),
        ("self.declare_parameter('rate', 10.0)", "self.declare_parameters('', [spec])"),
        ("super().__init__('driver')",
         "super().__init__('driver', automatically_declare_parameters_from_overrides=True)"),
        ("super().__init__('driver')", "super().__init__('driver', allow_undeclared_parameters=True)"),
        ("class Driver(Node):", "class Driver(BaseDriver):"),
        ("class Driver(Node):", "class Driver(Node, ParameterMixin):"),
    ])
    def test_declarations_that_cannot_be_read_are_silent(self, tmp_path, old, new):
        assert old in NODE
        text = "driver:\n  ros__parameters:\n    rate: 20.0\n    rate_hz: 50.0\n"
        assert self.workspace(tmp_path, text, NODE.replace(old, new)) == []

    def test_section_that_is_not_a_mapping_is_silent(self, tmp_path):
        assert self.workspace(tmp_path, "driver:\n  ros__parameters: null\n") == []


class TestDependencies:
    def test_import_that_package_xml_does_not_declare(self, tmp_path):
        make_package(tmp_path, depends=("rclpy",), files={
            "demo/driver.py": NODE + "from sensor_msgs.msg import Imu\nimport cv_bridge\n"})
        issue, = found(tmp_path, "dependencies")
        assert issue["severity"] == "warning" and issue["file"] == "demo/package.xml"
        assert "imports cv_bridge, sensor_msgs, std_msgs" in issue["message"]
        assert "no dependency on them" in issue["message"]

    def test_single_missing_dependency_is_worded_in_the_singular(self, tmp_path):
        make_package(tmp_path, depends=("rclpy",))
        assert "imports std_msgs, but package.xml declares no dependency on it." in found(
            tmp_path, "dependencies")[0]["message"]

    @pytest.mark.parametrize("tag", checks.DEPEND_TAGS)
    def test_every_dependency_tag_counts_as_a_declaration(self, tmp_path, tag):
        make_package(tmp_path, depend_tag=tag)
        assert found(tmp_path) == []

    def test_tf2_ros_is_provided_by_tf2_ros_py(self, tmp_path):
        make_package(tmp_path, depends=("rclpy", "std_msgs", "tf2_ros_py"),
                     files={"demo/driver.py": NODE + "from tf2_ros import TransformBroadcaster\n"})
        assert found(tmp_path) == []
        make_package(tmp_path / "second", name="other", depends=("rclpy", "std_msgs"),
                     files={"other/driver.py": NODE + "from tf2_ros import TransformBroadcaster\n"})
        assert "imports tf2_ros," in found(tmp_path / "second", "dependencies")[0]["message"]

    def test_interfaces_that_come_with_rclpy(self, tmp_path):
        imports = "from rcl_interfaces.msg import SetParametersResult\nfrom lifecycle_msgs.msg import State\n"
        make_package(tmp_path, files={"demo/driver.py": NODE + imports})
        assert found(tmp_path) == []
        make_package(tmp_path / "second", name="plain", depends=("std_msgs",),
                     files={"plain/driver.py": "from rcl_interfaces.msg import Log\nfrom std_msgs.msg import String\n"
                                               "def main():\n    pass\n"})
        assert "imports rcl_interfaces," in found(tmp_path / "second", "dependencies")[0]["message"]

    def test_test_code_setup_and_launch_files_are_not_read(self, tmp_path):
        make_package(tmp_path, installed=("launch",), files={
            "test/test_driver.py": "from sensor_msgs.msg import Imu\n",
            "demo/test_helpers.py": "from nav_msgs.msg import Path\n",
            "demo/driver_test.py": "from geometry_msgs.msg import Twist\n",
            "launch/demo.launch.py": "from visualization_msgs.msg import Marker\n" + LAUNCH})
        assert found(tmp_path) == []

    def test_module_the_package_ships_is_not_a_dependency(self, tmp_path):
        make_package(tmp_path, files={
            "demo_msgs/__init__.py": "", "demo/driver.py": NODE + "from demo_msgs import shapes\n"})
        assert found(tmp_path) == []

    def test_relative_and_ordinary_imports_are_ignored(self, tmp_path):
        make_package(tmp_path, files={
            "demo/driver.py": NODE + "from . import helpers\nfrom .camera_msgs import Frame\nimport numpy\n",
            "demo/broken.py": "import sensor_msgs.msg (\n"})
        assert found(tmp_path) == []


class TestMissingQos:
    @pytest.mark.parametrize("call,name", [
        ("self.create_publisher(String, 'status')", "create_publisher()"),
        ("self.create_subscription(String, 'command', self.on_message)", "create_subscription()"),
        ("self.create_subscription(String, 'command', callback=self.on_message)", "create_subscription()"),
    ])
    def test_endpoint_without_a_qos_argument(self, tmp_path, call, name):
        make_package(tmp_path, files={"demo/driver.py": node_source(f"self.endpoint = {call}")})
        issue, = found(tmp_path, "missing-qos")
        assert issue["severity"] == "error" and issue["file"] == "demo/demo/driver.py"
        assert issue["line"] == 9
        assert name in issue["message"] and "TypeError" in issue["message"]

    @pytest.mark.parametrize("call", [
        "self.create_publisher(String, 'status', 10)",
        "self.create_publisher(String, 'status', qos_profile=10)",
        "self.create_publisher(String, 'status', QoSProfile(depth=1))",
        "self.create_subscription(String, 'command', self.on_message, 10)",
        "self.create_subscription(String, 'command', self.on_message, qos_profile=profile)",
        "self.create_publisher(*arguments)",
        "self.create_subscription(String, 'command', self.on_message, **options)",
    ])
    def test_endpoint_with_qos_or_unreadable_arguments_is_silent(self, tmp_path, call):
        make_package(tmp_path, files={"demo/driver.py": node_source(f"self.endpoint = {call}")})
        assert found(tmp_path) == []

    def test_file_that_does_not_use_rclpy_is_not_read(self, tmp_path):
        make_package(tmp_path, files={
            "demo/bus.py": "class Bus:\n    def start(self):\n        self.create_publisher(int, 'x')\n"})
        assert found(tmp_path) == []


class TestTimerPeriod:
    @pytest.mark.parametrize("call,expected", [
        ("self.create_timer(50, self.tick)  # 50 Hz", "says 50 Hz, which is 0.02 s"),
        ("self.create_timer(20, self.tick)  # every 20 ms", "says 20 ms, which is 0.02 s"),
        ("self.create_timer(\n    100.0,  # 100hz control loop\n    self.tick)", "says 100 hz, which is 0.01 s"),
    ])
    def test_period_that_contradicts_the_rate_next_to_it(self, tmp_path, call, expected):
        make_package(tmp_path, files={"demo/driver.py": node_source(f"self.timer = {call}")})
        issue, = found(tmp_path, "timer-period")
        assert issue["severity"] == "warning"
        assert expected in issue["message"] and "in seconds" in issue["message"]

    @pytest.mark.parametrize("call", [
        "self.create_timer(0.02, self.tick)  # 50 Hz",
        "self.create_timer(0.0205, self.tick)  # 50 Hz",
        "self.create_timer(0.02, self.tick)  # 20 ms",
        "self.create_timer(50, self.tick)",
        "self.create_timer(50, self.tick)  # watchdog",
        "self.create_timer(50, self.tick)  # 0 Hz",
        "self.create_timer(1.0 / rate, self.tick)  # 50 Hz",
        "self.create_timer(period, self.tick)  # 50 Hz",
        "self.create_timer(True, self.tick)  # 50 Hz",
        "self.create_timer(timer_period_sec=50, callback=self.tick)  # 50 Hz",
    ])
    def test_consistent_or_undecidable_period_is_silent(self, tmp_path, call):
        make_package(tmp_path, files={"demo/driver.py": node_source(f"self.timer = {call}")})
        assert found(tmp_path) == []


BLOCKING = """\
import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger
{header}

class Driver(Node):
    def __init__(self):
        super().__init__('driver')
        self.client = self.create_client(Trigger, 'arm')
        self.timer = self.create_timer(1.0, self.tick{timer_extra})
        self.sub = self.create_subscription(Trigger.Request, 'go', self.on_go, 10)
        self.srv = self.create_service(Trigger, 'reset', self.on_reset)

    def tick(self):
        {tick}

    def on_go(self, msg):
        {on_go}

    def on_reset(self, request, response):
        return response

    def helper(self):
        {helper}


def main():
    rclpy.init()
    node = Driver()
    rclpy.spin(node)
"""


def blocking(tick="pass", on_go="pass", helper="pass", header="", timer_extra=""):
    return BLOCKING.format(tick=tick, on_go=on_go, helper=helper, header=header, timer_extra=timer_extra)


class TestBlockingCalls:
    def workspace(self, tmp_path, source):
        make_package(tmp_path, depends=("rclpy", "std_srvs"), files={"demo/driver.py": source})
        return found(tmp_path)

    def test_synchronous_service_call_inside_a_callback(self, tmp_path):
        issue, = self.workspace(tmp_path, blocking(tick="self.client.call(Trigger.Request())"))
        assert (issue["check"], issue["severity"], issue["line"]) == ("blocking-call", "warning", 15)
        assert "inside the callback tick()" in issue["message"] and "call_async()" in issue["message"]

    @pytest.mark.parametrize("call", [
        "rclpy.spin_until_future_complete(self, future)", "rclpy.spin_once(self)", "rclpy.spin(self)"])
    def test_spinning_inside_a_callback(self, tmp_path, call):
        issue, = self.workspace(tmp_path, blocking(on_go=call))
        assert issue["check"] == "blocking-call" and "inside the callback on_go()" in issue["message"]

    def test_the_same_calls_outside_a_callback_are_silent(self, tmp_path):
        source = blocking(helper="self.client.call(Trigger.Request()); rclpy.spin_once(self)")
        assert self.workspace(tmp_path, source) == []

    def test_other_objects_with_a_call_method_are_silent(self, tmp_path):
        source = blocking(tick="self.bus.call(1); executor.spin_once(); self.client.call_async(None)")
        assert self.workspace(tmp_path, source) == []

    @pytest.mark.parametrize("header,timer_extra", [
        ("from rclpy.executors import MultiThreadedExecutor", ""),
        ("from rclpy.callback_groups import ReentrantCallbackGroup", ""),
        ("", ", callback_group=self.group"),
    ])
    def test_executor_or_callback_group_makes_it_undecidable(self, tmp_path, header, timer_extra):
        source = blocking(tick="self.client.call(Trigger.Request())", header=header, timer_extra=timer_extra)
        assert self.workspace(tmp_path, source) == []


def remapped(*pairs):
    listed = ", ".join(repr(pair) for pair in pairs)
    return launch(f"Node(package='demo', executable='driver', name='driver', remappings=[{listed}])")


class TestRemappings:
    def test_remapping_of_a_name_the_node_never_uses(self, tmp_path):
        make_package(tmp_path, files={"launch/demo.launch.py": remapped(("cmd", "/robot/cmd"))})
        issue, = found(tmp_path, "remapping")
        assert issue["severity"] == "warning" and issue["file"] == "demo/launch/demo.launch.py"
        assert "remaps 'cmd' to '/robot/cmd'" in issue["message"]
        assert "it uses: command, status" in issue["message"]

    @pytest.mark.parametrize("origin", ["command", "/command", "~/status", "__node", "__ns"])
    def test_remapping_of_a_name_the_node_uses_is_silent(self, tmp_path, origin):
        make_package(tmp_path, files={"launch/demo.launch.py": remapped((origin, "/robot/x"))})
        assert found(tmp_path) == []

    def test_services_and_actions_are_names_too(self, tmp_path):
        source = NODE.replace(
            "self.pub = ", "self.cli = self.create_client(String, 'arm')\n        "
            "self.act = ActionClient(self, String, 'follow')\n        self.pub = ")
        make_package(tmp_path, files={
            "demo/driver.py": source,
            "launch/demo.launch.py": remapped(("arm", "/a"), ("follow", "/f"), ("stop", "/s"))})
        issue, = found(tmp_path, "remapping")
        assert "remaps 'stop'" in issue["message"]

    @pytest.mark.parametrize("old,new", [
        ("self.create_publisher(String, 'status', 10)", "self.create_publisher(String, self.topic, 10)"),
        ("self.create_publisher(String, 'status', 10)", "self.create_publisher(*arguments)"),
        ("self.create_publisher(String, 'status', 10)", "self.create_publisher(String, qos_profile=10)"),
    ])
    def test_computed_names_make_it_undecidable(self, tmp_path, old, new):
        assert old in NODE
        make_package(tmp_path, files={"demo/driver.py": NODE.replace(old, new),
                                      "launch/demo.launch.py": remapped(("cmd", "/robot/cmd"))})
        assert found(tmp_path, "remapping") == []

    def test_computed_remapping_and_other_shapes_are_silent(self, tmp_path):
        text = launch("Node(package='demo', executable='driver', remappings=[(source, '/x'), pair, ('a', 'b', 'c')])",
                      "Node(package='demo', executable='driver', remappings=REMAPPINGS)")
        make_package(tmp_path, files={"launch/demo.launch.py": text})
        assert found(tmp_path) == []

    def test_node_without_endpoints_is_not_judged(self, tmp_path):
        make_package(tmp_path, files={"demo/driver.py": "import rclpy\ndef main():\n    pass\n",
                                      "launch/demo.launch.py": remapped(("cmd", "/robot/cmd"))})
        assert found(tmp_path) == []


LIFECYCLE_NODE = NODE.replace("from rclpy.node import Node", "from rclpy.lifecycle import LifecycleNode").replace(
    "class Driver(Node):", "class Driver(LifecycleNode):")


class TestLifecycle:
    def test_lifecycle_node_that_nothing_activates(self, tmp_path):
        make_package(tmp_path, files={"demo/driver.py": LIFECYCLE_NODE, "launch/demo.launch.py": LAUNCH})
        issue, = found(tmp_path, "lifecycle")
        assert issue["severity"] == "warning" and issue["file"] == "demo/launch/demo.launch.py"
        assert "'driver' is a lifecycle node" in issue["message"]

    def test_qualified_base_class_is_recognised(self, tmp_path):
        source = LIFECYCLE_NODE.replace("class Driver(LifecycleNode):", "class Driver(rclpy.lifecycle.LifecycleNode):")
        make_package(tmp_path, files={"demo/driver.py": source, "launch/demo.launch.py": LAUNCH})
        assert len(found(tmp_path, "lifecycle")) == 1

    @pytest.mark.parametrize("launch_text", [
        launch("LifecycleNode(package='demo', executable='driver', name='driver', namespace='')"),
        launch("Node(package='demo', executable='driver')",
               "Node(package='nav2_lifecycle_manager', executable='lifecycle_manager')"),
        launch("Node(package='demo', executable='driver')", extra="from lifecycle_msgs.msg import Transition"),
        launch("Node(package='demo', executable='driver')", "EmitEvent(event=ChangeState(transition_id=1))"),
    ])
    def test_launch_file_that_manages_the_node_is_silent(self, tmp_path, launch_text):
        make_package(tmp_path, depends=("rclpy", "std_msgs", "lifecycle_msgs"),
                     files={"demo/driver.py": LIFECYCLE_NODE, "launch/demo.launch.py": launch_text})
        assert found(tmp_path, "lifecycle") == []

    def test_node_that_activates_itself_is_silent(self, tmp_path):
        source = LIFECYCLE_NODE + "\n\ndef start(node):\n    node.trigger_configure()\n    node.trigger_activate()\n"
        make_package(tmp_path, files={"demo/driver.py": source, "launch/demo.launch.py": LAUNCH})
        assert found(tmp_path, "lifecycle") == []

    def test_ordinary_node_is_silent(self, tmp_path):
        make_package(tmp_path, files={"launch/demo.launch.py": LAUNCH})
        assert found(tmp_path, "lifecycle") == []


QOS_PAIR = """\
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String


class Talker(Node):
    def __init__(self):
        super().__init__('talker')
        self.pub = self.create_publisher(String, 'chatter', {publisher})


class Listener(Node):
    def __init__(self):
        super().__init__('listener')
        self.sub = self.create_subscription(String, 'chatter', self.on_message, {subscription})

    def on_message(self, msg):
        pass
"""
BEST_EFFORT = "QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)"
RELIABLE = "QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)"
LATCHED = "QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)"
VOLATILE = "QoSProfile(depth=1, durability=DurabilityPolicy.VOLATILE)"


class TestQos:
    def workspace(self, tmp_path, publisher, subscription, path="demo/pair.py"):
        make_package(tmp_path, files={path: QOS_PAIR.format(publisher=publisher, subscription=subscription)})
        return found(tmp_path)

    def test_best_effort_publisher_with_a_reliable_subscription(self, tmp_path):
        issue, = self.workspace(tmp_path, BEST_EFFORT, RELIABLE)
        assert (issue["check"], issue["severity"]) == ("qos-incompatible", "warning")
        assert (issue["file"], issue["line"]) == ("demo/demo/pair.py", 16)
        assert "topic 'chatter'" in issue["message"] and "no data arrives" in issue["message"]

    def test_matching_reliability_is_silent(self, tmp_path):
        assert self.workspace(tmp_path, RELIABLE, RELIABLE) == []
        assert self.workspace(tmp_path / "second", RELIABLE, BEST_EFFORT) == []

    def test_volatile_subscription_to_a_latched_publisher(self, tmp_path):
        issue, = self.workspace(tmp_path, LATCHED, VOLATILE)
        assert (issue["check"], issue["severity"]) == ("qos-late-joiner", "info")
        assert (issue["file"], issue["line"]) == ("demo/demo/pair.py", 16)
        assert "TRANSIENT_LOCAL" in issue["message"] and "never receives that sample" in issue["message"]

    def test_latched_subscription_to_a_latched_publisher_is_silent(self, tmp_path):
        assert self.workspace(tmp_path, LATCHED, LATCHED) == []

    def test_volatile_publisher_is_silent(self, tmp_path):
        assert self.workspace(tmp_path, VOLATILE, VOLATILE) == []

    @pytest.mark.parametrize("subscription", ["profile", "make_profile()"])
    def test_profile_that_cannot_be_resolved_is_silent(self, tmp_path, subscription):
        assert self.workspace(tmp_path, BEST_EFFORT, subscription) == []
        assert self.workspace(tmp_path / "second", LATCHED, subscription) == []

    @pytest.mark.parametrize("path", ["test/test_pair.py", "demo/test_pair.py"])
    def test_endpoints_in_test_code_are_not_paired(self, tmp_path, path):
        assert self.workspace(tmp_path, BEST_EFFORT, RELIABLE, path) == []
        assert self.workspace(tmp_path / "second", LATCHED, VOLATILE, path) == []

    def test_findings_are_ordered_by_severity_then_place(self, tmp_path):
        make_package(tmp_path, installed=(), files={
            "launch/demo.launch.py": LAUNCH.replace("executable='driver'", "executable='other'"),
            "demo/pair.py": QOS_PAIR.format(publisher=LATCHED, subscription=VOLATILE)})
        assert [issue["severity"] for issue in found(tmp_path)] == ["error", "warning", "info"]


class TestLimits:
    def test_packages_past_the_limit_are_reported_as_not_checked(self, tmp_path, monkeypatch):
        for name in ("alpha", "beta", "gamma"):
            make_package(tmp_path, name=name)
        monkeypatch.setattr(checks, "MAX_PACKAGES", 2)
        _issues, skipped, packages = checks.check_workspace(tmp_path)
        assert [package.name for package in packages] == ["alpha", "beta"]
        assert skipped == ["more than 2 packages: only the first 2 were checked"]

    def test_exactly_the_limit_is_not_reported(self, tmp_path, monkeypatch):
        for name in ("alpha", "beta"):
            make_package(tmp_path, name=name)
        monkeypatch.setattr(checks, "MAX_PACKAGES", 2)
        assert checks.check_workspace(tmp_path)[1] == []

    def test_files_past_the_limit_are_reported_as_not_read(self, tmp_path, monkeypatch):
        make_package(tmp_path, files={"launch/demo.launch.py": LAUNCH, "config/a.yaml": "", "config/b.yaml": ""})
        monkeypatch.setattr(checks, "MAX_FILES_PER_PACKAGE", 2)
        _issues, skipped, _packages = checks.check_workspace(tmp_path)
        assert skipped == ["demo: more than 2 files, not all were read"]

    def test_file_over_the_size_limit_is_not_read(self, tmp_path, monkeypatch):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        monkeypatch.setattr(checks, "MAX_FILE_BYTES", 64)
        assert checks.check_workspace(tmp_path) == ([], [], [])  # even package.xml is too large

    def test_file_that_is_not_utf8_is_not_read(self, tmp_path):
        package = make_package(tmp_path, installed=("launch",), files={"launch/demo.launch.py": LAUNCH})
        (package / "launch" / "demo.launch.py").write_bytes(b"\xff\xfe broken")
        assert found(tmp_path) == []

    def test_expired_deadline_stops_before_any_package(self, tmp_path):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        issues, skipped, packages = checks.check_workspace(tmp_path, deadline=time.monotonic() - 1)
        assert issues == [] and packages == []
        assert skipped == ["time limit reached while looking for packages: the directory tree was "
                           "only partly searched"]

    def test_deadline_reached_between_packages_is_reported(self, tmp_path, monkeypatch):
        for name in ("alpha", "beta"):
            make_package(tmp_path, name=name, installed=(), files={"launch/x.launch.py": LAUNCH})
        calls = []

        def expired(deadline):
            calls.append(deadline)
            return len(calls) > 4  # the search and the first package run, the rest does not

        monkeypatch.setattr(checks, "_expired", expired)
        issues, skipped, packages = checks.check_workspace(tmp_path, deadline=1.0)
        assert len(packages) == 2
        assert {issue["file"].split("/")[0] for issue in issues} == {"alpha"}
        assert skipped == ["time limit reached: 1 of 2 packages were checked",
                           "time limit reached: QoS declarations were not compared"]

    def test_deadline_in_the_future_changes_nothing(self, tmp_path):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        assert checks.check_workspace(tmp_path, deadline=time.monotonic() + 60)[:2] == (
            checks.check_workspace(tmp_path)[:2])


class TestCommandLine:
    def test_text_report_and_exit_status(self, tmp_path, capsys):
        make_package(tmp_path, installed=(), files={
            "launch/demo.launch.py": LAUNCH.replace("executable='driver'", "executable='other'")})
        assert checks.main([str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "Checked 1 package(s)" in out
        assert "[ERROR  ] demo/launch/demo.launch.py:7:" in out
        assert "[WARNING] demo/setup.py:1:" in out

    def test_warnings_alone_exit_zero(self, tmp_path, capsys):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        assert checks.main([str(tmp_path)]) == 0
        assert "[WARNING]" in capsys.readouterr().out

    def test_no_findings_is_not_called_a_pass(self, tmp_path, capsys):
        make_package(tmp_path)
        assert checks.main([str(tmp_path)]) == 0
        assert "This is not a pass" in capsys.readouterr().out

    def test_skipped_checks_are_printed(self, tmp_path, capsys, monkeypatch):
        for name in ("alpha", "beta"):
            make_package(tmp_path, name=name)
        monkeypatch.setattr(checks, "MAX_PACKAGES", 1)
        checks.main([str(tmp_path)])
        assert "[SKIPPED] more than 1 packages" in capsys.readouterr().out

    def test_json_report(self, tmp_path, capsys):
        make_package(tmp_path, installed=(), files={"launch/demo.launch.py": LAUNCH})
        assert checks.main([str(tmp_path), "--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["tool"] == "workspace_checks" and report["version"] == checks.__version__
        assert report["packages"] == ["demo"] and report["checks_skipped"] == []
        assert set(report["issues"][0]) == {"file", "line", "severity", "check", "message"}

    def test_path_that_is_not_a_directory(self, tmp_path, capsys):
        target = tmp_path / "file.txt"
        target.write_text("x")
        assert checks.main([str(target)]) == 2
        assert "not a directory" in capsys.readouterr().err

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            checks.main(["--version"])
        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == checks.__version__
