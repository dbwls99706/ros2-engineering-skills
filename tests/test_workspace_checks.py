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


@pytest.fixture(autouse=True)
def no_installed_sources(monkeypatch):
    """What is installed on the machine running the tests is not part of any case here."""
    monkeypatch.delenv("AMENT_PREFIX_PATH", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)


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

    def setup_with(self, tmp_path, data_files, before="", extra=""):
        package = make_package(tmp_path, setup=False, files={"launch/demo.launch.py": LAUNCH})
        (package / "setup.py").write_text(
            "import glob\nimport os\nfrom setuptools import find_packages, setup\n\n"
            f"package_name = 'demo'\n{before}\nsetup(\n    name=package_name,\n"
            "    packages=find_packages(exclude=['launch']),\n"
            f"    data_files={data_files},\n    install_requires=['launch', 'setuptools'],\n"
            "    entry_points={'console_scripts': ['driver = demo.driver:main']},\n)\n" + extra)
        return found(tmp_path, "install-rules")

    def test_the_word_elsewhere_in_setup_is_not_an_install_rule(self, tmp_path):
        # `launch` in install_requires and in find_packages(exclude=...) installs nothing.
        issue, = self.setup_with(tmp_path, "[('share/' + package_name, ['package.xml'])]")
        assert "launch/ is not installed" in issue["message"]

    @pytest.mark.parametrize("data_files", [
        "[('share/' + package_name + '/launch', glob.glob(os.path.join('launch', '*.launch.py')))]",
        "[(os.path.join('share', package_name, 'launch'), glob.glob('launch/*'))]",
        "[('share/' + package_name, ['package.xml']), (target, sources)]",
    ])
    def test_data_files_that_install_the_directory_are_silent(self, tmp_path, data_files):
        before = "target = 'share/demo/launch'\nsources = glob.glob('launch/*.py')\n"
        assert self.setup_with(tmp_path, data_files, before) == []

    @pytest.mark.parametrize("data_files,before,extra", [
        ("[('share/demo', ['package.xml'])] + launch_entries()", "", ""),
        ("DATA", "DATA = [('share/demo', ['package.xml'])]\nDATA.append(('share/demo/launch', []))\n", ""),
        ("[('share/demo', ['package.xml']), *more]", "more = collect('launch')\n", ""),
        ("[('share/demo', files_under('launch'))]", "", ""),
        ("[('share/demo', found)]", "found = files_under('launch')\n", ""),
        ("[(share, [name]) for share, name in pairs]", "pairs = [('share/demo/launch', 'x')]\n", ""),
        ("[('share/demo', ['package.xml'])]", "", "# data_files gains launch/ in setup.cfg\n"),
    ])
    def test_data_files_not_written_out_are_not_read_as_empty(self, tmp_path, data_files, before, extra):
        assert self.setup_with(tmp_path, data_files, before, extra) == []


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

    ARGUMENT = ("from launch.actions import DeclareLaunchArgument\n"
                "from launch.substitutions import LaunchConfiguration\n"
                "import os\nshare = '/opt/demo/share/demo'\n"
                "default_file = os.path.join(share, 'config', 'params.yaml')\n")

    def from_argument(self, tmp_path, declared, section="driver_node", used="LaunchConfiguration('params_file')"):
        text = launch(*declared, f"Node(package='demo', executable='driver', name='driver', parameters=[{used}])",
                      extra=self.ARGUMENT)
        make_package(tmp_path, files={
            "launch/demo.launch.py": text,
            "config/params.yaml": f"{section}:\n  ros__parameters:\n    rate: 20.0\n"})
        return found(tmp_path, "parameter-section")

    def test_file_that_is_the_default_of_a_launch_argument(self, tmp_path):
        issue, = self.from_argument(tmp_path, ["DeclareLaunchArgument('params_file', default_value=default_file)"])
        assert "node 'driver' is given config/params.yaml" in issue["message"]
        assert "(the default of the launch argument 'params_file')" in issue["message"]

    def test_default_of_a_launch_argument_with_a_matching_section_is_silent(self, tmp_path):
        declared = ["DeclareLaunchArgument('params_file', default_value=default_file)"]
        assert self.from_argument(tmp_path, declared, section="driver") == []

    @pytest.mark.parametrize("declared,used", [
        ([], "LaunchConfiguration('params_file')"),
        (["DeclareLaunchArgument('params_file')"], "LaunchConfiguration('params_file')"),
        (["DeclareLaunchArgument('params_file', default_value=default_file)",
          "DeclareLaunchArgument('params_file', default_value='other.yaml')"], "LaunchConfiguration('params_file')"),
        (["DeclareLaunchArgument('params_file', default_value=default_file)"], "LaunchConfiguration(chosen)"),
        (["DeclareLaunchArgument('params_file', default_value=default_file)"], "LaunchConfiguration('other')"),
        (["DeclareLaunchArgument(*spec)"], "LaunchConfiguration('params_file')"),
        (["DeclareLaunchArgument('params_file', default_value=pick())"], "LaunchConfiguration('params_file')"),
    ])
    def test_launch_argument_whose_default_is_not_known_is_silent(self, tmp_path, declared, used):
        assert self.from_argument(tmp_path, declared, used=used) == []

    @pytest.mark.parametrize("section", ["driver", "/driver", "/**", "robot1/driver", "/*/driver"])
    def test_section_that_applies_to_the_node_is_silent(self, tmp_path, section):
        make_package(tmp_path, files={
            "launch/demo.launch.py": with_parameters(),
            "config/params.yaml": f'"{section}":\n  ros__parameters:\n    rate: 20.0\n'})
        assert found(tmp_path) == []

    def test_section_under_a_namespace_key_is_read(self, tmp_path):
        nested = "robot1:\n  driver:\n    ros__parameters:\n      rate: 20.0\n"
        other = "camera:\n  ros__parameters:\n    fps: 30\n"
        make_package(tmp_path, files={"launch/demo.launch.py": with_parameters(),
                                      "config/params.yaml": other + nested})
        assert found(tmp_path) == []
        wrong = nested.replace("driver:", "driver_node:")
        make_package(tmp_path / "second", files={"launch/demo.launch.py": with_parameters(),
                                                 "config/params.yaml": other + wrong})
        issue, = found(tmp_path / "second", "parameter-section")
        assert "sections: camera, robot1/driver_node" in issue["message"]

    def test_parameters_of_a_namespaced_section_are_compared(self, tmp_path):
        text = "robot1:\n  driver:\n    ros__parameters:\n      rate: 20.0\n      rate_hz: 50.0\n"
        make_package(tmp_path, files={"launch/demo.launch.py": with_parameters(), "config/params.yaml": text})
        issue, = found(tmp_path, "parameter-name")
        assert "section 'robot1/driver' sets 'rate_hz'" in issue["message"] and issue["line"] == 5

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
            "demo_msgs/__init__.py": "", "demo_msgs/shapes.py": "SQUARE = 4\n",
            "demo/driver.py": NODE + "from demo_msgs import shapes\n"})
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


class TestMissingSelf:
    SOURCE = (
        "import rclpy\nfrom rclpy.node import Node\n\n\nclass Buffer(Node):\n"
        "    def __init__(self):\n        super().__init__('buffer')\n        self.items = []\n\n"
        "    {signature}\n        {body}\n\n\ndef main():\n    rclpy.init()\n")

    def findings(self, tmp_path, signature, body="self.items.clear()"):
        make_package(tmp_path, files={"demo/driver.py": self.SOURCE.format(signature=signature, body=body)})
        return found(tmp_path, "missing-self")

    def test_method_that_uses_self_without_taking_it(self, tmp_path):
        issue, = self.findings(tmp_path, "def on_jump(time_jump):")
        assert issue["severity"] == "error" and issue["line"] == 10
        assert "Buffer.on_jump() uses self at line 11 but does not take it" in issue["message"]

    @pytest.mark.parametrize("signature,body", [
        ("def on_jump(self, time_jump):", "self.items.clear()"),
        ("def on_jump(time_jump):", "return time_jump"),
        ("@staticmethod\n    def on_jump(time_jump):", "self.items.clear()"),
        ("def on_jump(*args):", "self.items.clear()"),
        ("def on_jump(**kwargs):", "self.items.clear()"),
        ("def on_jump(this):", "self = this; self.items.clear()"),
        ("def on_jump(this):", "return lambda: self.items"),
        ("async def on_jump(self):", "self.items.clear()"),
        ("def on_jump(node, *, self):", "self.items.clear()"),
    ])
    def test_what_is_correct_or_cannot_be_decided_is_silent(self, tmp_path, signature, body):
        assert self.findings(tmp_path, signature, body) == []

    def test_module_level_name_called_self_is_not_judged(self, tmp_path):
        source = self.SOURCE.format(signature="def on_jump(time_jump):", body="self.items.clear()")
        source = source.replace("class Buffer", "self = None\n\n\nclass Buffer")
        make_package(tmp_path, files={"demo/driver.py": source})
        assert found(tmp_path, "missing-self") == []


class TestKeywordArguments:
    def test_keyword_that_no_distribution_has(self, tmp_path):
        make_package(tmp_path, files={"demo/driver.py": node_source(
            "self.timer = self.create_timer(period=2, callback=self.tick)")})
        issue, = found(tmp_path, "keyword-argument")
        assert issue["severity"] == "error" and issue["line"] == 9
        assert "create_timer() has no parameter 'period'" in issue["message"]
        assert "timer_period_sec" in issue["message"] and "TypeError" in issue["message"]

    def test_node_held_in_a_variable(self, tmp_path):
        source = ("import rclpy\nfrom std_msgs.msg import String\n\n\ndef main():\n    rclpy.init()\n"
                  "    node = rclpy.create_node('driver')\n"
                  "    node.create_publisher(String, topic_name='status', qos_profile=10)\n")
        make_package(tmp_path, files={"demo/driver.py": source})
        issue, = found(tmp_path, "keyword-argument")
        assert "create_publisher() has no parameter 'topic_name'" in issue["message"]

    @pytest.mark.parametrize("call", [
        "self.create_timer(timer_period_sec=2, callback=self.tick, callback_group=None, clock=None)",
        "self.create_timer(2.0, self.tick, autostart=False)",
        "self.create_subscription(String, 'a', self.on_message, 10, content_filter_options=None, raw=False)",
        "self.create_publisher(msg_type=String, topic='a', qos_profile=10, publisher_class=Custom)",
        "self.create_service(srv_type=String, srv_name='a', callback=self.on_message, service_class=Custom)",
        "self.create_client(String, srv_name='a', client_class=Custom)",
        "self.declare_parameter(name='rate', value=1.0, descriptor=None, ignore_override=False)",
    ])
    def test_keywords_some_distribution_has_are_silent(self, tmp_path, call):
        make_package(tmp_path, files={"demo/driver.py": node_source(f"self.endpoint = {call}")})
        assert found(tmp_path, "keyword-argument") == []

    @pytest.mark.parametrize("case", [
        "other base", "mixed bases", "method defined by the class", "attribute receiver", "bound twice",
        "kwargs", "not a node"])
    def test_receiver_that_is_not_known_to_be_a_node_is_silent(self, tmp_path, case):
        source = node_source("self.timer = self.create_timer(period=2, callback=self.tick)")
        if case == "other base":
            source = source.replace("class Driver(Node):", "class Driver(AsyncNode):")
        elif case == "mixed bases":
            source = source.replace("class Driver(Node):", "class Driver(Node, TimerMixin):")
        elif case == "method defined by the class":
            source = source.replace("    def tick(self):", "    def create_timer(self, period, callback):\n"
                                    "        return None\n\n    def tick(self):")
        elif case == "attribute receiver":
            source = source.replace("self.create_timer(period", "self.node.create_timer(period")
        elif case == "bound twice":
            source = ("import rclpy\n\n\ndef main():\n    node = rclpy.create_node('a')\n    node = wrap(node)\n"
                      "    node.create_timer(period=2, callback=print)\n")
        elif case == "kwargs":
            source = source.replace("period=2, callback=self.tick", "**settings")
        elif case == "not a node":
            source = ("import rclpy\n\n\ndef main():\n    pool = make_pool()\n"
                      "    pool.create_timer(period=2, callback=print)\n")
        make_package(tmp_path, files={"demo/driver.py": source})
        assert found(tmp_path, "keyword-argument") == []


def with_callbacks(body, methods):
    source = node_source(body)
    return source.replace("    def tick(self):", textwrap.dedent(methods).strip("\n").replace(
        "\n", "\n    ").join(("    ", "\n\n    def tick(self):")))


class TestCallbackSignatures:
    @pytest.mark.parametrize("call,method,expected", [
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self):\n    pass",
         "takes no argument besides self; rclpy calls it with the message"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, a, b, c):\n    pass",
         "takes 3 required arguments besides self"),
        ("self.create_service(String, 'a', self.cb)", "def cb(self, request):\n    return request",
         "takes 1 required argument besides self; rclpy calls it with the request and the response"),
        ("self.create_service(String, 'a', callback=self.cb)", "def cb(self, a, b, c):\n    return b",
         "takes 3 required arguments besides self"),
        ("self.create_timer(1.0, self.cb)", "def cb(self, first, second):\n    pass",
         "takes 2 required arguments besides self; rclpy calls it with no argument"),
        ("self.create_timer(1.0, self.cb)", "def cb(self, event):\n    pass",
         "takes 1 required argument besides self; rclpy calls it with no argument"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "async def cb(self):\n    pass",
         "takes no argument besides self"),
    ])
    def test_signature_no_distribution_can_call(self, tmp_path, call, method, expected):
        make_package(tmp_path, files={"demo/driver.py": with_callbacks(f"self.endpoint = {call}", method)})
        issue, = found(tmp_path, "callback-signature")
        assert issue["severity"] == "error" and issue["line"] == 11
        assert "self.cb is the callback of" in issue["message"] and "at line 9" in issue["message"]
        assert expected in issue["message"] and "TypeError" in issue["message"]

    def test_module_level_function_as_callback(self, tmp_path):
        source = node_source("self.sub = self.create_subscription(String, 'a', on_text, 10)") + (
            "\n\ndef on_text():\n    pass\n")
        make_package(tmp_path, files={"demo/driver.py": source})
        issue, = found(tmp_path, "callback-signature")
        assert "on_text is the callback of create_subscription()" in issue["message"]
        assert "takes no argument; rclpy calls it with the message" in issue["message"]

    @pytest.mark.parametrize("call,method", [
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, msg):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, msg, info):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, msg, info=None, extra=1):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, *args):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, msg, /):\n    pass"),
        ("self.create_timer(1.0, self.cb)", "def cb(self):\n    pass"),
        ("self.create_timer(1.0, self.cb)", "def cb(self, info: TimerInfo):\n    pass"),
        ("self.create_timer(1.0, self.cb)", "def cb(self, info: 'rclpy.timer.TimerInfo'):\n    pass"),
        ("self.create_timer(1.0, self.cb)", "def cb(self, info=None):\n    pass"),
        ("self.create_service(String, 'a', self.cb)", "def cb(self, request, response):\n    return response"),
        ("self.create_service(String, 'a', self.cb)", "def cb(self, request, response, extra=0):\n    return response"),
    ])
    def test_signature_some_distribution_accepts_is_silent(self, tmp_path, call, method):
        make_package(tmp_path, files={"demo/driver.py": with_callbacks(f"self.endpoint = {call}", method)})
        assert found(tmp_path) == []

    @pytest.mark.parametrize("call,method", [
        ("self.create_subscription(String, 'a', self.cb, 10)", "@staticmethod\ndef cb():\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "@throttled\ndef cb(self):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)",
         "def cb(self):\n    pass\n\ndef cb(self, msg):  # noqa: F811\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb(self, *, key):\n    pass"),
        ("self.create_subscription(String, 'a', self.cb, 10)", "def cb():\n    pass"),
        ("self.create_subscription(String, 'a', lambda: None, 10)", "def cb(self):\n    pass"),
        ("self.create_subscription(String, 'a', partial(self.cb, 1), 10)", "def cb(self):\n    pass"),
        ("self.create_subscription(String, 'a', self.helper.cb, 10)", "def cb(self):\n    pass"),
        ("self.create_subscription(String, 'a', self.inherited, 10)", "def cb(self):\n    pass"),
        ("self.create_subscription(String, 'a', imported_callback, 10)", "def cb(self):\n    pass"),
        ("self.create_subscription(*arguments)", "def cb(self):\n    pass"),
        ("self.node.create_subscription(String, 'a', self.cb, 10)", "def cb(self):\n    pass"),
        ("make(String, 'a', self.cb, 10)", "def cb(self):\n    pass"),
    ])
    def test_callback_that_cannot_be_read_is_silent(self, tmp_path, call, method):
        make_package(tmp_path, files={"demo/driver.py": with_callbacks(f"self.endpoint = {call}", method)})
        assert found(tmp_path, "callback-signature") == []

    def test_class_with_another_base_is_not_judged(self, tmp_path):
        source = with_callbacks("self.s = self.create_subscription(String, 'a', self.cb, 10)",
                                "def cb(self):\n    pass").replace("class Driver(Node):", "class Driver(Base):")
        make_package(tmp_path, files={"demo/driver.py": source})
        assert found(tmp_path, "callback-signature") == []


ENDPOINT_NODE = """\
import rclpy
from rclpy.action import ActionClient, ActionServer
from rclpy.node import Node
from example_interfaces.srv import AddTwoInts
from example_interfaces.action import Fibonacci
from std_msgs.msg import String, Int32


class {name}(Node):
    def __init__(self):
        super().__init__('{node}')
        self.endpoint = {call}

    def on_message(self, msg):
        pass

    def on_request(self, request, response):
        return response
"""


def endpoint_package(root, name, call, extra=None):
    files = {f"{name}/driver.py": ENDPOINT_NODE.format(name=name.title(), node=name, call=call)}
    files.update(extra or {})
    return make_package(root, name=name, depends=("rclpy", "std_msgs", "example_interfaces"), files=files)


CLIENT = "self.create_client(AddTwoInts, 'add_two_int')"
SERVER = "self.create_service(AddTwoInts, 'add_two_ints', self.on_request)"


class TestNameMismatch:
    def test_client_and_server_one_character_apart(self, tmp_path):
        endpoint_package(tmp_path, "asker", CLIENT)
        endpoint_package(tmp_path, "adder", SERVER)
        issue, = found(tmp_path, "name-mismatch")
        assert issue["severity"] == "warning"
        assert (issue["file"], issue["line"]) == ("asker/asker/driver.py", 12)
        assert "calls the service 'add_two_int', which nothing in the workspace offers" in issue["message"]
        assert "adder/adder/driver.py:12 offers 'add_two_ints' with the same type (AddTwoInts)" in issue["message"]

    def test_counterpart_with_other_users_is_still_a_candidate(self, tmp_path):
        endpoint_package(tmp_path, "asker", CLIENT)
        endpoint_package(tmp_path, "adder", SERVER)
        endpoint_package(tmp_path, "second", "self.create_client(AddTwoInts, 'add_two_ints')")
        assert len(found(tmp_path, "name-mismatch")) == 1

    @pytest.mark.parametrize("use,offer,words", [
        ("self.create_subscription(String, 'chater', self.on_message, 10)",
         "self.create_publisher(String, '/chatter', 10)", "subscribes to the topic 'chater'"),
        ("ActionClient(self, Fibonacci, 'fibonaci')",
         "ActionServer(self, Fibonacci, 'fibonacci', self.on_message)", "sends goals to the action 'fibonaci'"),
    ])
    def test_topics_and_actions(self, tmp_path, use, offer, words):
        endpoint_package(tmp_path, "asker", use)
        endpoint_package(tmp_path, "adder", offer)
        issue, = found(tmp_path, "name-mismatch")
        assert words in issue["message"]

    @pytest.mark.parametrize("use,offer", [
        ("self.create_client(AddTwoInts, 'add_two_ints')", SERVER),
        ("self.create_client(AddTwoInts, '/add_two_ints')", SERVER),
        ("self.create_client(String, 'add_two_int')", SERVER),
        ("self.create_client(AddTwoInts, 'add_ints')", SERVER),
        ("self.create_client(AddTwoInts, self.service_name)", SERVER),
        ("self.create_client(*arguments)", SERVER),
        ("self.create_client(AddTwoInts, 'add1')", "self.create_service(AddTwoInts, 'add2', self.on_request)"),
        ("self.create_client(AddTwoInts, 'adder_1')", "self.create_service(AddTwoInts, 'adder_2', self.on_request)"),
        ("self.create_client(AddTwoInts, 'sum')", "self.create_service(AddTwoInts, 'sums', self.on_request)"),
        ("self.create_subscription(String, 'chater', self.on_message, 10)",
         "self.create_publisher(Int32, 'chatter', 10)"),
    ])
    def test_what_is_not_a_near_miss_is_silent(self, tmp_path, use, offer):
        endpoint_package(tmp_path, "asker", use)
        endpoint_package(tmp_path, "adder", offer)
        assert found(tmp_path, "name-mismatch") == []

    @pytest.mark.parametrize("relative,text", [
        ("config/names.yaml", "service: 'add_two_int'\n"),
        ("src/server.cpp", 'auto s = node->create_service<AddTwoInts>("add_two_int", cb);\n'),
        ("launch/remap.launch.py", "REMAP = [('add_two_ints', '/add_two_int')]\n"),
    ])
    def test_name_written_in_another_file_is_silent(self, tmp_path, relative, text):
        endpoint_package(tmp_path, "asker", CLIENT)
        endpoint_package(tmp_path, "adder", SERVER, extra={relative: text})
        assert found(tmp_path, "name-mismatch") == []

    def test_two_candidates_are_not_decided(self, tmp_path):
        endpoint_package(tmp_path, "asker", "self.create_client(AddTwoInts, 'add_two_int')")
        endpoint_package(tmp_path, "adder", SERVER)
        endpoint_package(tmp_path, "other", "self.create_service(AddTwoInts, 'add_two_inx', self.on_request)")
        assert found(tmp_path, "name-mismatch") == []

    def test_test_code_and_launch_files_are_not_endpoints(self, tmp_path):
        client = ENDPOINT_NODE.format(name="Asker", node="asker", call=CLIENT)
        endpoint_package(tmp_path, "adder", SERVER, extra={
            "test/test_adder.py": client, "launch/helper.py": client})
        assert found(tmp_path, "name-mismatch") == []

    def test_edit_distance(self):
        assert checks._distance("add_two_int", "add_two_ints") == 1
        assert checks._distance("chater", "chatter") == 1
        assert checks._distance("scan", "scna") == 2
        assert checks._distance("abcdef", "uvwxyz") == 3  # reported as "more than the limit"
        assert checks._distance("a", "abcdef") == 3


SERVICE_NODE = """\
import rclpy
from rclpy.node import Node
from demo_interfaces.srv import AddTwo
from demo_interfaces.msg import Status


class Driver(Node):
    def __init__(self):
        super().__init__('driver')
        self.srv = self.create_service(AddTwo, 'add', self.on_add)
        self.sub = self.create_subscription(Status, 'status', self.on_status, 10)

    def on_add(self, request, response):
        {service_body}
        return response

    def on_status(self, msg):
        {message_body}
"""
INTERFACES = {"srv/AddTwo.srv": "# two addends\nint64 a\nint64 b\n---\nint64 sum  # the result\n",
              "msg/Status.msg": "uint8 OK=0\nuint8 FAILED = 1\nstring text\nstd_msgs/Header header\n"}


def service_workspace(root, service_body="response.sum = request.a + request.b",
                      message_body="self.get_logger().info(msg.text)", interfaces=INTERFACES):
    make_package(root, name="demo_interfaces", setup=False, files=interfaces)
    make_package(root, depends=("rclpy", "std_msgs", "demo_interfaces"), files={
        "demo/driver.py": SERVICE_NODE.format(service_body=service_body, message_body=message_body)})
    return found(root, "interface-field")


class TestInterfaceFields:
    def test_response_field_that_does_not_exist(self, tmp_path):
        issue, = service_workspace(tmp_path, "response.total = request.a + request.b")
        assert issue["severity"] == "error" and (issue["file"], issue["line"]) == ("demo/demo/driver.py", 14)
        assert "'response.total': the response of demo_interfaces/srv/AddTwo has no field 'total'" in (
            issue["message"])
        assert "it has: sum" in issue["message"] and "AttributeError" in issue["message"]

    def test_request_field_that_does_not_exist(self, tmp_path):
        issue, = service_workspace(tmp_path, "response.sum = request.a + request.c")
        assert "'request.c': the request of demo_interfaces/srv/AddTwo" in issue["message"]
        assert "it has: a, b" in issue["message"]

    def test_message_field_that_does_not_exist(self, tmp_path):
        issue, = service_workspace(tmp_path, message_body="self.get_logger().info(msg.data)")
        assert "'msg.data': demo_interfaces/msg/Status has no field 'data'" in issue["message"]
        assert "it has: FAILED, OK, header, text" in issue["message"]

    @pytest.mark.parametrize("message_body", [
        "self.last = (msg.text, msg.header.stamp, msg.OK, msg.FAILED)",
        "self.last = msg.get_fields_and_field_types()",
        "self.last = msg._text",
        "self.last = msg.SLOT_TYPES",
    ])
    def test_fields_constants_and_generated_members_are_silent(self, tmp_path, message_body):
        assert service_workspace(tmp_path, message_body=message_body) == []

    def test_definition_from_an_installed_distribution(self, tmp_path, monkeypatch):
        share = tmp_path / "opt" / "share" / "demo_interfaces"
        (share / "srv").mkdir(parents=True)
        (share / "srv" / "AddTwo.srv").write_text(INTERFACES["srv/AddTwo.srv"])
        monkeypatch.setenv("AMENT_PREFIX_PATH", f"{tmp_path / 'missing'}:{tmp_path / 'opt'}:")
        make_package(tmp_path / "ws", depends=("rclpy", "std_msgs", "demo_interfaces"), files={
            "demo/driver.py": SERVICE_NODE.format(
                service_body="response.total = request.a", message_body="pass")})
        issue, = found(tmp_path / "ws", "interface-field")
        assert "has no field 'total'" in issue["message"]

    def test_definition_that_cannot_be_found_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMENT_PREFIX_PATH", raising=False)
        make_package(tmp_path, depends=("rclpy", "std_msgs", "demo_interfaces"), files={
            "demo/driver.py": SERVICE_NODE.format(
                service_body="response.total = request.a", message_body="print(msg.nothing)")})
        assert found(tmp_path, "interface-field") == []

    @pytest.mark.parametrize("old,new", [
        ("def on_add(self, request, response):", "def on_add(self, request, response):\n        response = make()"),
        ("def on_add(self, request, response):", "def on_add(self, *pair):"),
        ("def on_add(self, request, response):", "@traced\n    def on_add(self, request, response):"),
        ("def on_add(self, request, response):", "def on_add(self, request):"),
        ("self.create_service(AddTwo, 'add', self.on_add)", "self.create_service(AddTwo, 'add', self.helper.on_add)"),
        ("self.create_service(AddTwo, 'add', self.on_add)", "self.create_service(self.kind, 'add', self.on_add)"),
        ("self.create_service(AddTwo, 'add', self.on_add)", "self.create_service(*arguments)"),
        ("from demo_interfaces.srv import AddTwo",
         "from demo_interfaces.srv import AddTwo\nfrom other.srv import AddTwo"),
        ("from demo_interfaces.srv import AddTwo", "from demo_interfaces import AddTwo"),
        ("from demo_interfaces.srv import AddTwo", "from .srv import AddTwo"),
        ("from demo_interfaces.srv import AddTwo", "from demo_interfaces.msg import AddTwo"),
    ])
    def test_what_cannot_be_read_is_silent(self, tmp_path, old, new):
        source = SERVICE_NODE.format(service_body="response.total = request.a", message_body="pass")
        assert old in source
        make_package(tmp_path, name="demo_interfaces", setup=False, files=INTERFACES)
        make_package(tmp_path, depends=("rclpy", "std_msgs", "demo_interfaces"),
                     files={"demo/driver.py": source.replace(old, new)})
        assert found(tmp_path, "interface-field") == []

    def test_import_under_another_name(self, tmp_path):
        source = SERVICE_NODE.format(service_body="response.total = request.a", message_body="pass").replace(
            "import AddTwo", "import AddTwo as Add").replace("create_service(AddTwo", "create_service(Add")
        make_package(tmp_path, name="demo_interfaces", setup=False, files=INTERFACES)
        make_package(tmp_path, depends=("rclpy", "std_msgs", "demo_interfaces"), files={"demo/driver.py": source})
        assert len(found(tmp_path, "interface-field")) == 1

    def test_definition_with_the_wrong_number_of_sections_is_silent(self, tmp_path):
        broken = dict(INTERFACES, **{"srv/AddTwo.srv": "int64 a\nint64 b\n"})
        assert service_workspace(tmp_path, "response.total = request.a", interfaces=broken) == []

    def test_definition_file_is_parsed_by_section(self):
        assert checks._interface_fields("int64 a  # first\n# note\n\nint64 B=2\n---\nbool ok\n---\n") == [
            {"a", "B"}, {"ok"}, set()]


def plain_module(body):
    """A package module that is plain Python: these checks do not need rclpy in the file."""
    return "import os\n\n\n" + textwrap.dedent(body).strip("\n") + "\n"


LOOP = """
def prune(topics, lookup):
    to_remove = []
    for topic in topics:
        if lookup(topic) is None:
            to_remove.append(topic)
    {loop}
    return topics
"""


class TestInvariantLoops:
    def findings(self, tmp_path, loop, template=LOOP):
        make_package(tmp_path, files={"demo/tools.py": plain_module(template.format(loop=loop))})
        return found(tmp_path, "invariant-loop")

    def test_while_written_where_for_was_meant(self, tmp_path):
        issue, = self.findings(tmp_path, "while (topic in to_remove):\n        topics.remove(topic)")
        assert issue["severity"] == "warning" and (issue["file"], issue["line"]) == ("demo/demo/tools.py", 9)
        assert "reads to_remove, topic" in issue["message"]
        assert "never runs or never ends" in issue["message"] and "`for`" in issue["message"]

    def test_the_loop_that_was_meant_is_silent(self, tmp_path):
        assert self.findings(tmp_path, "for topic in to_remove:\n        topics.remove(topic)") == []

    @pytest.mark.parametrize("loop", [
        "while topic in to_remove:\n        to_remove.remove(topic)",
        "while topic in to_remove:\n        to_remove = to_remove[1:]",
        "while topic in to_remove:\n        topic = topics.pop()",
        "while topic in to_remove:\n        topic.refresh()",
        "while topic in to_remove:\n        if lookup(topic):\n            break",
        "while topic in to_remove:\n        return topics",
        "while topic in to_remove:\n        raise ValueError(topic)",
        "while topic in to_remove:\n        prune_one(topics, to_remove)",
        "while topic in to_remove:\n        try:\n            topics.remove(topic)\n"
        "        except ValueError:\n            pass",
        "while topic in lookup(to_remove):\n        topics.remove(topic)",
        "while topic in self.pending:\n        topics.remove(topic)",
        "while topics:\n        topics.pop()",
        "while topic == 'a':\n        topics.remove(topic)",
        "while True:\n        topics.remove(topic)",
        "while topic in to_remove:\n        topics.remove(topic)\n    else:\n        pass",
    ])
    def test_a_loop_that_can_end_or_cannot_be_read_is_silent(self, tmp_path, loop):
        assert self.findings(tmp_path, loop) == []

    @pytest.mark.parametrize("old,new", [
        ("    to_remove = []\n", "    to_remove = lookup.pending\n"),
        ("    to_remove = []\n", "    to_remove = []\n    register(to_remove)\n"),
        ("    to_remove = []\n", "    to_remove = []\n    alias = to_remove\n"),
        ("    to_remove = []\n", "    to_remove = []\n    watch = lambda: to_remove.clear()\n"),
        ("    to_remove = []\n", "    global to_remove\n    to_remove = []\n"),
        ("def prune(topics, lookup):", "def prune(topics, lookup, to_remove):"),
        ("    to_remove = []\n", "    to_remove = []\n    to_remove = list(topics)\n"),
    ])
    def test_a_container_something_else_can_reach_is_not_judged(self, tmp_path, old, new):
        template = LOOP.replace(old, new)
        assert template != LOOP
        if "def prune(topics, lookup, to_remove)" in template:
            template = template.replace("    to_remove = []\n", "")
        assert self.findings(tmp_path, "while topic in to_remove:\n        topics.remove(topic)", template) == []

    def test_reading_the_container_elsewhere_keeps_it_judged(self, tmp_path):
        template = LOOP.replace("    return topics", "    print(len(to_remove), sorted(to_remove))\n    return topics")
        assert len(self.findings(tmp_path, "while topic in to_remove:\n        topics.remove(topic)", template)) == 1


class TestEnvironmentNumbers:
    def findings(self, tmp_path, expression):
        make_package(tmp_path, files={"demo/tools.py": plain_module(f"PORT = 11511 + {expression}")})
        return found(tmp_path, "environment-number")

    @pytest.mark.parametrize("expression", [
        "int(os.environ.get('ROS_DOMAIN_ID', 0))", "int(os.getenv('ROS_DOMAIN_ID', '0'))",
        "float(os.environ.get('RMW_TIMEOUT', 1.5))"])
    def test_number_from_a_ros_variable_with_a_default(self, tmp_path, expression):
        issue, = self.findings(tmp_path, expression)
        assert issue["severity"] == "warning" and issue["line"] == 4
        assert "set but empty" in issue["message"] and "raises ValueError" in issue["message"]
        assert " or " in issue["message"]

    @pytest.mark.parametrize("expression", [
        "int(os.environ.get('ROS_DOMAIN_ID') or 0)",
        "int(os.environ.get('ROS_DOMAIN_ID'))",
        "int(os.environ['ROS_DOMAIN_ID'])",
        "int(os.environ.get('PORT', 8080))",
        "int(os.environ.get(NAME, 0))",
        "int(os.environ.get('ROS_DOMAIN_ID', 0), 10)",
        "int(settings.get('ROS_DOMAIN_ID', 0))",
        "len(os.environ.get('ROS_DOMAIN_ID', ''))",
        "int(os.environ.get(*pair))",
    ])
    def test_what_handles_empty_or_is_not_this_pattern_is_silent(self, tmp_path, expression):
        assert self.findings(tmp_path, expression) == []


class TestImportNames:
    def workspace(self, tmp_path, importer, helpers="def scale(value):\n    return value\n\nLIMIT = 3\n", extra=None):
        files = {"demo/helpers.py": helpers, "demo/tools.py": importer}
        files.update(extra or {})
        make_package(tmp_path, files=files)
        return found(tmp_path, "import-name")

    def test_name_the_module_does_not_define(self, tmp_path):
        issue, = self.workspace(tmp_path, "from demo.helpers import scale, rescale\n")
        assert issue["severity"] == "error" and (issue["file"], issue["line"]) == ("demo/demo/tools.py", 1)
        assert "`from demo.helpers import rescale`: helpers.py defines no `rescale`" in issue["message"]
        assert "ImportError" in issue["message"]

    def test_relative_import(self, tmp_path):
        issue, = self.workspace(tmp_path, "from .helpers import rescale\n")
        assert "`from .helpers import rescale`" in issue["message"]

    def test_name_missing_from_a_package_init(self, tmp_path):
        issue, = self.workspace(tmp_path, "from demo import missing\n")
        assert "demo/__init__.py defines no `missing`" in issue["message"]

    def test_module_of_another_package_in_the_workspace(self, tmp_path):
        make_package(tmp_path, name="shared", files={"shared/units.py": "def to_si(value):\n    return value\n"})
        make_package(tmp_path, files={"demo/tools.py": "from shared.units import to_si, to_imperial\n"})
        issue, = found(tmp_path, "import-name")
        assert "units.py defines no `to_imperial`" in issue["message"]

    def test_module_on_the_python_path(self, tmp_path, monkeypatch):
        site = tmp_path / "opt" / "lib" / "python3.12" / "site-packages" / "launch_ros" / "substitutions"
        site.mkdir(parents=True)
        (site.parent / "__init__.py").write_text("")
        (site / "__init__.py").write_text("from .find_package import FindPackageShare\n")
        (site / "find_package.py").write_text("class FindPackageShare:\n    pass\n")
        monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path / "opt"))
        monkeypatch.delenv("PYTHONPATH", raising=False)
        make_package(tmp_path / "ws", files={
            "demo/tools.py": "from launch_ros.substitutions import FileContent, FindPackageShare\n"})
        issue, = found(tmp_path / "ws", "import-name")
        assert "substitutions/__init__.py defines no `FileContent`" in issue["message"]

    @pytest.mark.parametrize("importer", [
        "from demo.helpers import scale, LIMIT\n",
        "from demo.helpers import *\n",
        "from demo import helpers\n",
        "from demo import driver\n",
        "import demo.helpers\n",
        "from unknown_package.helpers import anything\n",
        "from os.path import nothing_here\n",
        "try:\n    from demo.helpers import rescale\nexcept ImportError:\n    rescale = None\n",
        "if True:\n    from demo.helpers import rescale\n",
        "from . import helpers\n",
    ])
    def test_what_exists_or_cannot_be_resolved_is_silent(self, tmp_path, importer):
        assert self.workspace(tmp_path, importer) == []

    @pytest.mark.parametrize("helpers", [
        "from .other import *\n",
        "def __getattr__(name):\n    return name\n",
        "globals()['rescale'] = 1\n",
        "import sys\nsys.modules[__name__].rescale = 1\n",
        "setattr(object, 'x', 1)\n",
        "def scale(:\n",
    ])
    def test_module_whose_names_cannot_be_read_is_silent(self, tmp_path, helpers):
        assert self.workspace(tmp_path, "from demo.helpers import rescale\n", helpers,
                              extra={"demo/other.py": "rescale = 1\n"}) == []

    @pytest.mark.parametrize("helpers", [
        "if True:\n    rescale = 1\n",
        "try:\n    import json as rescale\nexcept ImportError:\n    rescale = None\n",
        "for rescale in range(1):\n    pass\n",
        "with open(__file__) as rescale:\n    pass\n",
        "rescale: int = 1\n",
        "first, rescale = 1, 2\n",
        "class rescale:\n    pass\n",
        "async def rescale():\n    pass\n",
        "import numpy.linalg as rescale\n",
    ])
    def test_every_way_a_module_binds_a_name_counts(self, tmp_path, helpers):
        assert self.workspace(tmp_path, "from demo.helpers import rescale\n", helpers) == []

    def test_two_modules_of_the_same_name_are_not_decided(self, tmp_path):
        make_package(tmp_path, name="shared", files={"common/units.py": "A = 1\n"})
        make_package(tmp_path, name="other", files={"common/units.py": "B = 1\n"})
        make_package(tmp_path, files={"demo/tools.py": "from common.units import C\n"})
        assert found(tmp_path, "import-name") == []

    def test_test_code_is_not_read(self, tmp_path):
        make_package(tmp_path, files={"demo/helpers.py": "A = 1\n",
                                      "test/test_tools.py": "from demo.helpers import B\n"})
        assert found(tmp_path, "import-name") == []


class TestImportShadowing:
    def test_data_directory_named_like_an_installed_module_is_not_that_module(self, tmp_path):
        # launch/__init__.py in a package is not the module `launch`.
        make_package(tmp_path, installed=("launch",), files={
            "launch/__init__.py": "", "launch/demo.launch.py": LAUNCH,
            "config/__init__.py": "", "demo/tools.py": "from config import settings\nfrom launch import Thing\n"})
        assert found(tmp_path, "import-name") == []

    def test_installed_module_is_read_past_a_data_directory_of_its_name(self, tmp_path, monkeypatch):
        site = tmp_path / "opt" / "lib" / "python3.12" / "site-packages" / "launch"
        site.mkdir(parents=True)
        (site / "__init__.py").write_text("class LaunchDescription:\n    pass\n")
        monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path / "opt"))
        make_package(tmp_path / "ws", installed=("launch",), files={
            "launch/__init__.py": "Thing = 1\n", "launch/demo.launch.py": LAUNCH,
            "demo/tools.py": "from launch import LaunchDescription\nfrom launch import Thing\n"})
        issue, = found(tmp_path / "ws", "import-name")
        assert "`Thing`" in issue["message"]

    def test_compiled_or_namespace_module_is_not_read(self, tmp_path, monkeypatch):
        site = tmp_path / "opt" / "lib" / "python3.12" / "site-packages"
        (site / "fastlib").mkdir(parents=True)
        (site / "native.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")
        (site / "typed.pyi").write_text("def f() -> None: ...\n")
        monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path / "opt"))
        monkeypatch.setenv("PYTHONPATH", str(tmp_path / "missing"))
        make_package(tmp_path / "ws", files={
            "demo/tools.py": "from fastlib import a\nfrom native import b\nfrom typed import c\n"})
        assert found(tmp_path / "ws", "import-name") == []


class TestImportOfAMissingModule:
    def site(self, tmp_path, monkeypatch):
        package = tmp_path / "opt" / "lib" / "python3.12" / "site-packages" / "rclpy"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("from rclpy.node import Node\n")
        (package / "executors.py").write_text("class ExternalShutdownException(Exception):\n    pass\n")
        (package / "node.py").write_text("class Node:\n    pass\n")
        monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path / "opt"))
        monkeypatch.delenv("PYTHONPATH", raising=False)
        return package

    def test_submodule_the_package_does_not_have(self, tmp_path, monkeypatch):
        self.site(tmp_path, monkeypatch)
        make_package(tmp_path / "ws", files={
            "demo/tools.py": "from rclpy.executor import ExternalShutdownException\n"})
        issue, = found(tmp_path / "ws", "import-name")
        assert issue["severity"] == "error"
        assert "the package rclpy has no module `executor`" in issue["message"]
        assert "ModuleNotFoundError" in issue["message"]

    @pytest.mark.parametrize("importer", [
        "from rclpy.executors import ExternalShutdownException\n",
        "from rclpy.node import Node\n",
        "from rclpy.missing.deeper import X\n",
        "from unknown.executor import X\n",
    ])
    def test_existing_or_unresolvable_module_is_silent(self, tmp_path, monkeypatch, importer):
        self.site(tmp_path, monkeypatch)
        make_package(tmp_path / "ws", files={"demo/tools.py": importer})
        assert found(tmp_path / "ws", "import-name") == []

    @pytest.mark.parametrize("change", ["compiled", "dynamic", "bound by the package"])
    def test_what_may_exist_without_a_source_file_is_silent(self, tmp_path, monkeypatch, change):
        package = self.site(tmp_path, monkeypatch)
        if change == "compiled":
            (package / "executor.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")
        elif change == "dynamic":
            (package / "__init__.py").write_text("def __getattr__(name):\n    return name\n")
        else:
            (package / "__init__.py").write_text("from rclpy import executors as executor\n")
        make_package(tmp_path / "ws", files={"demo/tools.py": "from rclpy.executor import X\n"})
        assert found(tmp_path / "ws", "import-name") == []

    def test_without_an_installed_distribution_nothing_is_said(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMENT_PREFIX_PATH", raising=False)
        monkeypatch.delenv("PYTHONPATH", raising=False)
        make_package(tmp_path, files={"demo/tools.py": "from rclpy.executor import X\n"})
        assert found(tmp_path, "import-name") == []


class TestThirdPartyDependencies:
    def findings(self, tmp_path, imports, depends=("rclpy", "std_msgs"), **options):
        make_package(tmp_path, depends=depends, files={"demo/tools.py": imports}, **options)
        return found(tmp_path, "dependencies")

    def test_module_whose_rosdep_key_is_not_declared(self, tmp_path):
        issue, = self.findings(tmp_path, "import serial\nfrom cv2 import imread\n")
        assert issue["severity"] == "warning" and issue["file"] == "demo/package.xml"
        assert "imports cv2 (rosdep key python3-opencv), serial (rosdep key python3-serial)" in issue["message"]
        assert "declares no dependency that provides them" in issue["message"]

    @pytest.mark.parametrize("imports,depends", [
        ("import serial\n", ("rclpy", "std_msgs", "python3-serial")),
        ("import cv2\n", ("rclpy", "std_msgs", "cv_bridge")),
        ("import cv2\n", ("rclpy", "std_msgs", "libopencv-dev")),
        ("from PIL import Image\n", ("rclpy", "std_msgs", "python3-pil")),
        ("import sklearn\n", ("rclpy", "std_msgs", "python3-scikit-learn")),
    ])
    def test_declared_under_any_key_that_provides_it_is_silent(self, tmp_path, imports, depends):
        assert self.findings(tmp_path, imports, depends) == []

    @pytest.mark.parametrize("imports", [
        "import yaml\nimport numpy as np\n",                       # rclpy brings both
        "try:\n    import serial\nexcept ImportError:\n    serial = None\n",
        "def read():\n    import serial\n    return serial\n",
        "import importlib\nserial = importlib.import_module('serial')\n",
        "import toml\nimport attrs\n",                              # not in the table
        "from . import serial\n",
    ])
    def test_what_is_brought_in_or_cannot_be_decided_is_silent(self, tmp_path, imports):
        assert self.findings(tmp_path, imports) == []

    def test_yaml_without_rclpy_is_reported(self, tmp_path):
        issues = self.findings(tmp_path, "import yaml\n", depends=("std_msgs",))
        issue, = [item for item in issues if "rosdep key" in item["message"]]
        assert "imports yaml (rosdep key python3-yaml)" in issue["message"]
        assert "provides it" in issue["message"]

    def test_module_the_package_ships_and_packages_without_setup_are_not_judged(self, tmp_path):
        make_package(tmp_path, files={"demo/tools.py": "import serial\n", "serial/__init__.py": ""})
        assert found(tmp_path, "dependencies") == []
        package = make_package(tmp_path / "second", setup=False, files={"demo/tools.py": "import serial\n"})
        (package / "CMakeLists.txt").write_text("project(demo)\nament_package()\n")
        assert found(tmp_path / "second", "dependencies") == []


class TestStringMethods:
    SOURCE = "class Flags:\n    def __init__(self, flags):\n        self.flags = flags\n\n    {method}\n"

    def findings(self, tmp_path, method):
        make_package(tmp_path, files={"demo/tools.py": plain_module(self.SOURCE.format(method=method))})
        return found(tmp_path, "returns-none")

    @pytest.mark.parametrize("name,shown", [("__str__", "str()"), ("__repr__", "repr()")])
    def test_path_that_returns_none(self, tmp_path, name, shown):
        issue, = self.findings(
            tmp_path, f"def {name}(self):\n        if not self.flags:\n            return\n        return self.flags\n")
        assert issue["severity"] == "error" and issue["line"] == 10
        assert f"Flags.{name}() returns None here; {shown}, print()" in issue["message"]

    def test_explicit_none_is_reported_once_per_method(self, tmp_path):
        issue, = self.findings(
            tmp_path, "def __str__(self):\n        if not self.flags:\n            return None\n        return None\n")
        assert issue["line"] == 10

    @pytest.mark.parametrize("method", [
        "def __str__(self):\n        if not self.flags:\n            return ''\n        return self.flags\n",
        "def __str__(self):\n        raise NotImplementedError\n",
        "def __str__(self):\n        def inner():\n            return\n        inner()\n        return 'x'\n",
        "def describe(self):\n        return\n",
        "@property\n    def __str__(self):\n        return\n",
        "def __bool__(self):\n        return\n",
    ])
    def test_what_returns_a_string_or_is_not_that_method_is_silent(self, tmp_path, method):
        assert self.findings(tmp_path, method) == []


class TestOtherClient:
    SOURCE = ("class Navigator:\n    def __init__(self, node):\n        self.backup_client = node.a\n"
              "        self.drive_client = node.b\n\n    def drive(self, goal):\n{body}\n")

    def findings(self, tmp_path, body):
        text = self.SOURCE.format(body=textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8))
        make_package(tmp_path, files={"demo/tools.py": plain_module(text)})
        return found(tmp_path, "other-client")

    def test_request_through_a_client_that_was_not_waited_for(self, tmp_path):
        issue, = self.findings(tmp_path, """
            while not self.backup_client.wait_for_server(timeout_sec=1.0):
                pass
            return self.drive_client.send_goal_async(goal)
        """)
        assert issue["severity"] == "warning" and issue["line"] == 12
        assert "drive() waits for self.backup_client at line 10" in issue["message"]
        assert "sends its request through self.drive_client, which it never waits for" in issue["message"]

    def test_service_client_is_read_the_same_way(self, tmp_path):
        issue, = self.findings(tmp_path, """
            self.backup_client.wait_for_service()
            return self.drive_client.call_async(goal)
        """)
        assert "self.drive_client" in issue["message"]

    @pytest.mark.parametrize("body", [
        "self.drive_client.wait_for_server()\nreturn self.drive_client.send_goal_async(goal)",
        "self.backup_client.wait_for_server()\nself.drive_client.wait_for_server()\n"
        "return self.drive_client.send_goal_async(goal)",
        "self.backup_client.wait_for_server()\nself.backup_client.send_goal_async(goal)\n"
        "return self.drive_client.send_goal_async(goal)",
        "client = self.pick()\nclient.wait_for_server()\nreturn self.drive_client.send_goal_async(goal)",
        "self.backup_client.wait_for_server()\nreturn self.clients[0].send_goal_async(goal)",
        "self.drive_client = self.backup_client\nself.backup_client.wait_for_server()\n"
        "return self.drive_client.send_goal_async(goal)",
        "self.backup_client.wait_for_server()\nreturn lambda: self.drive_client.send_goal_async(goal)",
        "return self.drive_client.send_goal_async(goal)",
        "self.backup_client.wait_for_server()",
    ])
    def test_what_is_consistent_or_cannot_be_decided_is_silent(self, tmp_path, body):
        assert self.findings(tmp_path, body) == []


class TestClassAttribute:
    SOURCE = "{imports}\n\n{classes}\ndef build(name, value):\n{body}\n    return parameter\n"

    def findings(self, tmp_path, body, imports="from rcl_interfaces.msg import Parameter", classes=""):
        text = self.SOURCE.format(imports=imports, classes=classes,
                                  body=textwrap.indent(textwrap.dedent(body).strip("\n"), "    "))
        make_package(tmp_path, depends=("rclpy", "std_msgs", "rcl_interfaces"), files={"demo/tools.py": text})
        return found(tmp_path, "class-attribute")

    def test_attribute_set_on_the_class_after_an_instance_was_made(self, tmp_path):
        issue, = self.findings(tmp_path, "parameter = Parameter()\nParameter.name = name\nparameter.value = value")
        assert issue["severity"] == "warning" and issue["line"] == 6
        assert "Parameter.name is assigned on the class" in issue["message"]
        assert "`parameter = Parameter()` at line 5" in issue["message"]

    @pytest.mark.parametrize("body,imports,classes", [
        ("parameter = Parameter()\nparameter.name = name", "from rcl_interfaces.msg import Parameter", ""),
        ("Parameter.name = name\nparameter = Parameter()", "from rcl_interfaces.msg import Parameter", ""),
        ("Parameter.name = name\nparameter = make()", "from rcl_interfaces.msg import Parameter", ""),
        ("parameter = Parameter(name=name)\nParameter.count = 1", "from rcl_interfaces.msg import Parameter", ""),
        ("parameter = Parameter()\nParameter.count = 1", "import os",
         "class Parameter:\n    count = 0\n\n"),
        ("parameter = msg.Parameter()\nmsg.Parameter.name = name", "from rcl_interfaces import msg", ""),
        ("parameter = settings()\nsettings.name = name", "from demo.config import settings", ""),
    ])
    def test_what_sets_the_instance_or_cannot_be_decided_is_silent(self, tmp_path, body, imports, classes):
        assert self.findings(tmp_path, body, imports, classes) == []


class TestScanRanges:
    SOURCE = ("import rclpy\nfrom rclpy.node import Node\nfrom sensor_msgs.msg import LaserScan\n"
              "from std_msgs.msg import Bool\n\n\nclass Guard(Node):\n    def __init__(self):\n"
              "        super().__init__('guard')\n        self.pub = self.create_publisher(Bool, 'blocked', 10)\n"
              "        self.sub = self.create_subscription(LaserScan, 'scan', self.on_scan, 10)\n\n"
              "    def on_scan(self, scan):\n{body}\n")

    def findings(self, tmp_path, body, source=None):
        text = (source or self.SOURCE).format(body=textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8))
        make_package(tmp_path, depends=("rclpy", "std_msgs", "sensor_msgs"), files={"demo/guard.py": text})
        return found(tmp_path, "scan-ranges")

    @pytest.mark.parametrize("body", [
        "self.pub.publish(Bool(data=min(scan.ranges) < 0.5))",
        "for distance in scan.ranges:\n    if distance < 0.5:\n        self.pub.publish(Bool(data=True))",
        "self.pub.publish(Bool(data=any(r < 0.5 for r in scan.ranges)))",
        "self.pub.publish(Bool(data=scan.ranges[0] < 0.5))",
    ])
    def test_ranges_read_as_distances_without_excluding_invalid_readings(self, tmp_path, body):
        issue, = self.findings(tmp_path, body)
        assert issue["severity"] == "warning" and issue["line"] == 14
        assert "scan.ranges is read as distances and this file never excludes invalid readings" in issue["message"]
        assert "REP 117" in issue["message"]

    @pytest.mark.parametrize("body", [
        "valid = [r for r in scan.ranges if scan.range_min <= r <= scan.range_max]\n"
        "self.pub.publish(Bool(data=bool(valid) and min(valid) < 0.5))",
        "import math\nvalid = [r for r in scan.ranges if math.isfinite(r) and r >= scan.range_min]\n"
        "self.pub.publish(Bool(data=bool(valid) and min(valid) < 0.5))",
        "self.last = scan.ranges",
        "self.pub.publish(Bool(data=len(scan.ranges) > 0))",
        "self.forward(scan.ranges)",
        "ranges = self.latest.ranges\nself.pub.publish(Bool(data=min(ranges) < 0.5))",
    ])
    def test_filtered_or_passed_on_or_undecidable_is_silent(self, tmp_path, body):
        assert self.findings(tmp_path, body) == []

    @pytest.mark.parametrize("body", [
        "import math\nvalid = [r for r in scan.ranges if math.isfinite(r)]\n"
        "self.pub.publish(Bool(data=bool(valid) and min(valid) < 0.5))",
        "for r in scan.ranges:\n    if r != r or r < 0.5:  # nan counts as blocked\n"
        "        self.pub.publish(Bool(data=True))",
        "close = [r for r in scan.ranges if r != float('inf')]\nself.pub.publish(Bool(data=bool(close)))",
    ])
    def test_inf_and_nan_handled_but_the_range_limits_never_read(self, tmp_path, body):
        issue, = self.findings(tmp_path, body)
        assert "never reads range_min or range_max" in issue["message"]
        assert "scan.range_min..scan.range_max" in issue["message"] and "REP 117" in issue["message"]

    HELPER = ("def blocked(ranges, limit):\n    for i, r in enumerate(ranges):\n        if r < limit:\n"
              "            return True\n    return False\n\n\ndef keep(ranges, limit):\n    return ranges\n\n\n")

    @pytest.mark.parametrize("body", [
        "self.pub.publish(Bool(data=blocked(scan.ranges, 0.5)))",
        "self.pub.publish(Bool(data=self.near(scan.ranges)))\n\ndef near(self, ranges):\n    return min(ranges) < 0.5",
    ])
    def test_ranges_handed_to_a_function_of_this_file_that_reads_them(self, tmp_path, body):
        source = self.SOURCE.replace("class Guard(Node):", self.HELPER + "class Guard(Node):")
        issue, = self.findings(tmp_path, body, source)
        assert "scan.ranges is read as distances" in issue["message"]

    @pytest.mark.parametrize("body", [
        "self.last = keep(scan.ranges, 0.5)",
        "self.pub.publish(Bool(data=blocked(limit=0.5, ranges=scan.ranges)))",
        "self.pub.publish(Bool(data=blocked(*[scan.ranges, 0.5])))",
        "self.pub.publish(Bool(data=other.blocked(scan.ranges, 0.5)))",
        "self.pub.publish(Bool(data=blocked(0.5, scan.ranges)))",
    ])
    def test_helper_that_does_not_read_them_or_cannot_be_matched_is_silent(self, tmp_path, body):
        source = self.SOURCE.replace("class Guard(Node):", self.HELPER + "class Guard(Node):")
        assert self.findings(tmp_path, body, source) == []

    def test_file_that_does_not_import_laserscan_is_not_judged(self, tmp_path):
        source = self.SOURCE.replace("from sensor_msgs.msg import LaserScan\n", "from demo.types import LaserScan\n")
        assert self.findings(tmp_path, "self.pub.publish(Bool(data=min(scan.ranges) < 0.5))", source) == []


class TestLocalKeywords:
    SOURCE = ("def spawners(controller_names, controller_params_files=None, extra_spawner_args=None):\n"
              "    return controller_names, controller_params_files, extra_spawner_args\n\n\n"
              "def load(name, params_file):\n    files = [params_file] if params_file else None\n"
              "    return {call}\n")

    def findings(self, tmp_path, call, source=None, extra=""):
        text = (source or self.SOURCE).format(call=call) + extra
        make_package(tmp_path, files={"demo/tools.py": plain_module(text)})
        return [issue for issue in found(tmp_path, "keyword-argument") if issue["file"].endswith("tools.py")]

    def test_keyword_the_function_of_this_module_does_not_take(self, tmp_path):
        issue, = self.findings(tmp_path, "spawners(controller_names=[name], controller_params_file=files)")
        assert issue["severity"] == "error" and issue["line"] == 10
        assert "spawners() is defined at line 4 of this file and has no parameter `controller_params_file`" in (
            issue["message"])
        assert "It takes `controller_params_files`." in issue["message"]

    def test_keyword_far_from_every_parameter_carries_no_hint(self, tmp_path):
        issue, = self.findings(tmp_path, "spawners([name], timeout=3)")
        assert "no parameter `timeout`" in issue["message"] and "It takes" not in issue["message"]

    @pytest.mark.parametrize("call,extra", [
        ("spawners(controller_names=[name], controller_params_files=files)", ""),
        ("spawners([name], files, extra_spawner_args=None)", ""),
        ("spawners([name], **{{'controller_params_file': files}})", ""),
        ("spawners(*[name], controller_params_file=files)", ""),
        ("helper.spawners([name], controller_params_file=files)", ""),
        ("spawners([name], controller_params_file=files)", "\n\nspawners = None\n"),
        ("spawners([name], controller_params_file=files)",
         "\n\ndef spawners(names, controller_params_file=None):\n    return names\n"),
        ("spawners([name], controller_params_file=files)", "\n\nfrom demo.other import spawners\n"),
        ("unknown([name], controller_params_file=files)", ""),
    ])
    def test_what_is_accepted_or_cannot_be_decided_is_silent(self, tmp_path, call, extra):
        assert self.findings(tmp_path, call, extra=extra) == []

    @pytest.mark.parametrize("signature", [
        "def spawners(controller_names, **options):", "@wraps\ndef spawners(controller_names):",
        "async def spawners(controller_names):"])
    def test_function_that_may_take_anything_is_not_judged(self, tmp_path, signature):
        source = signature + "\n    return controller_names\n\n\ndef load(name, files):\n    return {call}\n"
        assert self.findings(tmp_path, "spawners([name], controller_params_file=files)", source) == []

    def test_parameter_or_local_of_the_same_name_is_not_the_function(self, tmp_path):
        source = ("def spawners(controller_names):\n    return controller_names\n\n\n"
                  "def load(name, spawners):\n    return {call}\n")
        assert self.findings(tmp_path, "spawners([name], controller_params_file=1)", source) == []
