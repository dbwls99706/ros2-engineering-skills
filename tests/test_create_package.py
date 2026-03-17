"""Tests for create_package.py - package scaffolding utility."""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "create_package.py")


def run_script(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, cwd=cwd,
    )


class TestCppPackage:
    def test_creates_expected_structure(self, tmp_path):
        result = run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        assert result.returncode == 0
        pkg = tmp_path / "my_robot"
        assert (pkg / "CMakeLists.txt").exists()
        assert (pkg / "package.xml").exists()
        assert (pkg / "include" / "my_robot" / "my_robot_node.hpp").exists()
        assert (pkg / "src" / "my_robot_node.cpp").exists()
        assert (pkg / "src" / "main.cpp").exists()
        assert (pkg / "launch" / "bringup.launch.py").exists()
        assert (pkg / "config" / "params.yaml").exists()
        assert (pkg / "test" / "test_my_robot.cpp").exists()
        assert (pkg / "README.md").exists()

    def test_package_xml_valid(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        tree = ET.parse(tmp_path / "my_robot" / "package.xml")
        root = tree.getroot()
        assert root.tag == "package"
        assert root.attrib["format"] == "3"
        assert root.find("name").text == "my_robot"
        assert root.find("buildtool_depend").text == "ament_cmake"
        deps = [d.text for d in root.findall("depend")]
        assert "rclcpp" in deps
        assert "rclcpp_lifecycle" in deps

    def test_cmake_contains_project_name(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        cmake = (tmp_path / "my_robot" / "CMakeLists.txt").read_text()
        assert "project(my_robot)" in cmake
        assert "ament_package()" in cmake
        assert "ament_add_gtest" in cmake

    def test_cpp_class_name_camelcase(self, tmp_path):
        run_script("my_cool_robot", "--type", "cpp", "--dest", str(tmp_path))
        hpp = (tmp_path / "my_cool_robot" / "include" / "my_cool_robot" / "my_cool_robot_node.hpp").read_text()
        assert "MyCoolRobotNode" in hpp

    def test_component_flag(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--component", "--dest", str(tmp_path))
        cmake = (tmp_path / "my_robot" / "CMakeLists.txt").read_text()
        assert "rclcpp_components" in cmake
        assert "rclcpp_components_register_node" in cmake
        cpp = (tmp_path / "my_robot" / "src" / "my_robot_node.cpp").read_text()
        assert "RCLCPP_COMPONENTS_REGISTER_NODE" in cpp

    def test_lifecycle_launch_file(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        launch = (tmp_path / "my_robot" / "launch" / "bringup.launch.py").read_text()
        assert "LifecycleNode" in launch
        assert "generate_launch_description" in launch

    def test_maintainer_args(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path),
                   "--maintainer-name", "Test User", "--maintainer-email", "test@example.com")
        xml = (tmp_path / "my_robot" / "package.xml").read_text()
        assert "Test User" in xml
        assert "test@example.com" in xml


class TestPythonPackage:
    def test_creates_expected_structure(self, tmp_path):
        result = run_script("my_monitor", "--type", "python", "--dest", str(tmp_path))
        assert result.returncode == 0
        pkg = tmp_path / "my_monitor"
        assert (pkg / "setup.py").exists()
        assert (pkg / "setup.cfg").exists()
        assert (pkg / "package.xml").exists()
        assert (pkg / "my_monitor" / "__init__.py").exists()
        assert (pkg / "my_monitor" / "my_monitor_node.py").exists()
        assert (pkg / "launch" / "bringup.launch.py").exists()
        assert (pkg / "config" / "params.yaml").exists()
        assert (pkg / "test" / "test_my_monitor.py").exists()
        assert (pkg / "resource" / "my_monitor").exists()

    def test_package_xml_python_build_type(self, tmp_path):
        run_script("my_monitor", "--type", "python", "--dest", str(tmp_path))
        tree = ET.parse(tmp_path / "my_monitor" / "package.xml")
        root = tree.getroot()
        export = root.find("export")
        assert export is not None
        build_type = export.find("build_type")
        assert build_type is not None
        assert build_type.text == "ament_python"

    def test_entry_point_in_setup(self, tmp_path):
        run_script("my_monitor", "--type", "python", "--dest", str(tmp_path))
        setup = (tmp_path / "my_monitor" / "setup.py").read_text()
        assert "my_monitor_node = my_monitor.my_monitor_node:main" in setup

    def test_node_class_exists(self, tmp_path):
        run_script("my_monitor", "--type", "python", "--dest", str(tmp_path))
        node = (tmp_path / "my_monitor" / "my_monitor" / "my_monitor_node.py").read_text()
        assert "class MyMonitorNode" in node
        assert "def main" in node

    def test_standard_launch_file(self, tmp_path):
        run_script("my_monitor", "--type", "python", "--dest", str(tmp_path))
        launch = (tmp_path / "my_monitor" / "launch" / "bringup.launch.py").read_text()
        # Python packages use regular Node, not LifecycleNode
        assert "Node(" in launch
        assert "generate_launch_description" in launch


class TestInterfacesPackage:
    def test_creates_expected_structure(self, tmp_path):
        result = run_script("my_interfaces", "--type", "interfaces", "--dest", str(tmp_path))
        assert result.returncode == 0
        pkg = tmp_path / "my_interfaces"
        assert (pkg / "CMakeLists.txt").exists()
        assert (pkg / "package.xml").exists()
        assert (pkg / "msg" / "Status.msg").exists()
        assert (pkg / "srv" / "SetMode.srv").exists()

    def test_cmake_has_rosidl(self, tmp_path):
        run_script("my_interfaces", "--type", "interfaces", "--dest", str(tmp_path))
        cmake = (tmp_path / "my_interfaces" / "CMakeLists.txt").read_text()
        assert "rosidl_generate_interfaces" in cmake
        assert "std_msgs" in cmake

    def test_package_xml_has_rosidl_deps(self, tmp_path):
        run_script("my_interfaces", "--type", "interfaces", "--dest", str(tmp_path))
        tree = ET.parse(tmp_path / "my_interfaces" / "package.xml")
        root = tree.getroot()
        buildtool_deps = [d.text for d in root.findall("buildtool_depend")]
        assert "rosidl_default_generators" in buildtool_deps
        exec_deps = [d.text for d in root.findall("exec_depend")]
        assert "rosidl_default_runtime" in exec_deps

    def test_member_of_group_at_package_level(self, tmp_path):
        """member_of_group must be a direct child of <package>, not inside <export>."""
        run_script("my_interfaces", "--type", "interfaces", "--dest", str(tmp_path))
        tree = ET.parse(tmp_path / "my_interfaces" / "package.xml")
        root = tree.getroot()
        # Must exist at package level
        members = [m.text for m in root.findall("member_of_group")]
        assert "rosidl_interface_packages" in members
        # Must NOT be inside <export>
        export = root.find("export")
        export_members = [m.text for m in export.findall("member_of_group")]
        assert len(export_members) == 0


class TestValidation:
    def test_invalid_name_rejected(self, tmp_path):
        result = run_script("InvalidName", "--type", "cpp", "--dest", str(tmp_path))
        assert result.returncode != 0
        assert "invalid" in result.stderr.lower()

    def test_name_starting_with_digit_rejected(self, tmp_path):
        result = run_script("1bad_name", "--type", "cpp", "--dest", str(tmp_path))
        assert result.returncode != 0

    def test_overwrite_protection(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        result = run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        assert result.returncode != 0
        assert "already exists" in result.stderr

    def test_force_overwrite(self, tmp_path):
        run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path))
        result = run_script("my_robot", "--type", "cpp", "--dest", str(tmp_path), "--force")
        assert result.returncode == 0
