#!/usr/bin/env python3
"""Scaffold a ROS 2 package following ros2-engineering-skills conventions.

Usage:
    python create_package.py my_robot_driver --type cpp
    python create_package.py my_robot_monitor --type python
    python create_package.py my_robot_interfaces --type interfaces
    python create_package.py my_robot_driver --type cpp --component
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

__version__ = "0.1.0"

# Apache-2.0 copyright/license headers for generated files
_APACHE2_PY = """# Copyright 2024 {maintainer}
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""

_APACHE2_CPP = """// Copyright 2024 {maintainer}
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
"""


def _copyright_py(maintainer: str = "TODO") -> str:
    return _APACHE2_PY.format(maintainer=maintainer)


def _copyright_cpp(maintainer: str = "TODO") -> str:
    return _APACHE2_CPP.format(maintainer=maintainer)


def _generate_launch_file(name: str, lifecycle: bool = False,
                          maintainer_name: str = "TODO") -> str:
    """Generate a basic bringup.launch.py file for the package."""
    header = _copyright_py(maintainer_name)
    if lifecycle:
        return header + f"""
from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import LifecycleNode
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from launch_ros.substitutions import FindPackageShare
import lifecycle_msgs.msg


def generate_launch_description():
    config = PathJoinSubstitution([
        FindPackageShare('{name}'), 'config', 'params.yaml'
    ])
    node = LifecycleNode(
        package='{name}',
        executable='{name}_node',
        name='{name}',
        namespace='',
        parameters=[config],
        output='screen',
    )
    # Auto-configure on launch
    configure_event = EmitEvent(
        event=ChangeState(
            lifecycle_node_matcher=lambda info: info,
            transition_id=lifecycle_msgs.msg.Transition.TRANSITION_CONFIGURE,
        )
    )
    # Auto-activate after configure succeeds
    activate_event = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=node,
            start_state='configuring',
            goal_state='inactive',
            entities=[
                EmitEvent(event=ChangeState(
                    lifecycle_node_matcher=lambda info: info,
                    transition_id=lifecycle_msgs.msg.Transition.TRANSITION_ACTIVATE,
                )),
            ],
        )
    )
    return LaunchDescription([node, configure_event, activate_event])
"""
    else:
        return header + f"""
from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution([
        FindPackageShare('{name}'), 'config', 'params.yaml'
    ])
    return LaunchDescription([
        Node(
            package='{name}',
            executable='{name}_node',
            name='{name}',
            parameters=[config],
            output='screen',
        ),
    ])
"""


def _generate_readme(name: str) -> str:
    """Generate a basic README.md for the package."""
    return f"""# {name}

TODO: Package description

## Usage

```bash
ros2 launch {name} bringup.launch.py
```

## Parameters

See `config/params.yaml` for default parameters.
"""


def create_cpp_package(name: str, dest: Path, component: bool = False,
                       maintainer_name: str = "TODO",
                       maintainer_email: str = "todo@todo.com") -> None:
    pkg = dest / name
    dirs = [
        pkg / "include" / name,
        pkg / "src",
        pkg / "launch",
        pkg / "config",
        pkg / "test",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    cpp_header = _copyright_cpp(maintainer_name)

    component_cmake = ""
    if component:
        component_cmake = f"""
find_package(rclcpp_components REQUIRED)
rclcpp_components_register_node(${{PROJECT_NAME}}_lib
  PLUGIN "{name}::{_class_name(name)}Node"
  EXECUTABLE ${{PROJECT_NAME}}_component_node
)
"""

    (pkg / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.8)
project({name})

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)

add_library(${{PROJECT_NAME}}_lib SHARED
  src/{name}_node.cpp
)
target_include_directories(${{PROJECT_NAME}}_lib PUBLIC
  $<BUILD_INTERFACE:${{CMAKE_CURRENT_SOURCE_DIR}}/include>
  $<INSTALL_INTERFACE:include/${{PROJECT_NAME}}>
)
target_link_libraries(${{PROJECT_NAME}}_lib PUBLIC
  rclcpp::rclcpp
  rclcpp_lifecycle::rclcpp_lifecycle
)
# Legacy fallback (deprecated since Kilted; prefer target_link_libraries):
# ament_target_dependencies(${{PROJECT_NAME}}_lib rclcpp rclcpp_lifecycle)
{component_cmake}
add_executable({name}_node src/main.cpp)
target_link_libraries({name}_node ${{PROJECT_NAME}}_lib)

install(TARGETS ${{PROJECT_NAME}}_lib
  EXPORT export_${{PROJECT_NAME}}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(TARGETS {name}_node DESTINATION lib/${{PROJECT_NAME}})
install(DIRECTORY include/ DESTINATION include/${{PROJECT_NAME}})
install(DIRECTORY launch config DESTINATION share/${{PROJECT_NAME}})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_{name} test/test_{name}.cpp)
  target_link_libraries(test_{name} ${{PROJECT_NAME}}_lib)
endif()

ament_export_targets(export_${{PROJECT_NAME}} HAS_LIBRARY_TARGET)
ament_export_dependencies(rclcpp rclcpp_lifecycle)
ament_package()
""")

    class_name = _class_name(name)

    (pkg / "include" / name / f"{name}_node.hpp").write_text(
        cpp_header + f"""
#pragma once

#include <rclcpp_lifecycle/lifecycle_node.hpp>

namespace {name}
{{

class {class_name}Node : public rclcpp_lifecycle::LifecycleNode
{{
public:
  explicit {class_name}Node(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

  CallbackReturn on_configure(const rclcpp_lifecycle::State &) override;
  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override;
  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override;
  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override;
}};

}}  // namespace {name}
""")

    component_include = ""
    component_register = ""
    if component:
        component_include = "\n#include <rclcpp_components/register_node_macro.hpp>"
        component_register = f"\n\nRCLCPP_COMPONENTS_REGISTER_NODE({name}::{class_name}Node)\n"

    (pkg / "src" / f"{name}_node.cpp").write_text(
        cpp_header + f"""
#include "{name}/{name}_node.hpp"
{component_include}
namespace {name}
{{

{class_name}Node::{class_name}Node(const rclcpp::NodeOptions & options)
: LifecycleNode("{name}", options)
{{
  RCLCPP_INFO(get_logger(), "Node created");
}}

{class_name}Node::CallbackReturn
{class_name}Node::on_configure(const rclcpp_lifecycle::State &)
{{
  RCLCPP_INFO(get_logger(), "Configuring...");
  return CallbackReturn::SUCCESS;
}}

{class_name}Node::CallbackReturn
{class_name}Node::on_activate(const rclcpp_lifecycle::State &)
{{
  RCLCPP_INFO(get_logger(), "Activating...");
  return CallbackReturn::SUCCESS;
}}

{class_name}Node::CallbackReturn
{class_name}Node::on_deactivate(const rclcpp_lifecycle::State &)
{{
  RCLCPP_INFO(get_logger(), "Deactivating...");
  return CallbackReturn::SUCCESS;
}}

{class_name}Node::CallbackReturn
{class_name}Node::on_cleanup(const rclcpp_lifecycle::State &)
{{
  RCLCPP_INFO(get_logger(), "Cleaning up...");
  return CallbackReturn::SUCCESS;
}}

}}  // namespace {name}
{component_register}""")

    (pkg / "src" / "main.cpp").write_text(
        cpp_header + f"""
#include <rclcpp/rclcpp.hpp>
#include "{name}/{name}_node.hpp"

int main(int argc, char ** argv)
{{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<{name}::{class_name}Node>();
  rclcpp::executors::SingleThreadedExecutor exe;
  exe.add_node(node->get_node_base_interface());
  exe.spin();
  rclcpp::shutdown();
  return 0;
}}
""")

    (pkg / "config" / "params.yaml").write_text(f"""{name}:
  ros__parameters:
    # Add parameters here
    publish_rate: 50.0
""")

    (pkg / "test" / f"test_{name}.cpp").write_text(
        cpp_header + f"""
#include <gtest/gtest.h>
#include <rclcpp/rclcpp.hpp>
#include "{name}/{name}_node.hpp"

class {class_name}Test : public ::testing::Test
{{
protected:
  static void SetUpTestSuite()
  {{
    rclcpp::init(0, nullptr);
  }}
  static void TearDownTestSuite()
  {{
    rclcpp::shutdown();
  }}
}};

TEST_F({class_name}Test, NodeCreation)
{{
  auto node = std::make_shared<{name}::{class_name}Node>();
  ASSERT_NE(node, nullptr);
}}
""")

    # Generate launch file (lifecycle=True since C++ template uses LifecycleNode)
    (pkg / "launch" / "bringup.launch.py").write_text(
        _generate_launch_file(name, lifecycle=True,
                              maintainer_name=maintainer_name))

    # Generate README
    (pkg / "README.md").write_text(_generate_readme(name))

    deps = ["rclcpp", "rclcpp_lifecycle"]
    if component:
        deps.append("rclcpp_components")
    _write_package_xml(pkg, name, "ament_cmake", deps,
                       maintainer_name=maintainer_name,
                       maintainer_email=maintainer_email)
    print(f"Created C++ package: {pkg}")


def create_python_package(name: str, dest: Path,
                          maintainer_name: str = "TODO",
                          maintainer_email: str = "todo@todo.com") -> None:
    pkg = dest / name
    dirs = [
        pkg / name,
        pkg / "launch",
        pkg / "config",
        pkg / "test",
        pkg / "resource",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    py_header = _copyright_py(maintainer_name)

    (pkg / name / "__init__.py").write_text(py_header)
    (pkg / "resource" / name).write_text("")

    class_name = _class_name(name)

    (pkg / name / f"{name}_node.py").write_text(py_header + f"""
import rclpy
from rclpy.node import Node


class {class_name}Node(Node):

    def __init__(self, **kwargs):
        super().__init__('{name}', **kwargs)
        self.declare_parameter('publish_rate', 50.0)
        rate = self.get_parameter('publish_rate').value
        self.timer = self.create_timer(1.0 / rate, self.timer_callback)
        self.get_logger().info('Node started')

    def timer_callback(self):
        pass  # Implement your logic here


def main(args=None):
    rclpy.init(args=args)
    node = {class_name}Node()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
""")

    (pkg / "setup.py").write_text(py_header + f"""
from setuptools import find_packages, setup

package_name = '{name}'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/bringup.launch.py']),
        ('share/' + package_name + '/config', ['config/params.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={{
        'console_scripts': [
            '{name}_node = {name}.{name}_node:main',
        ],
    }},
)
""")

    (pkg / "setup.cfg").write_text(f"""[develop]
script_dir=$base/lib/{name}
[install]
install_scripts=$base/lib/{name}
""")

    (pkg / "config" / "params.yaml").write_text(f"""{name}:
  ros__parameters:
    publish_rate: 50.0
""")

    (pkg / "test" / f"test_{name}.py").write_text(py_header + f"""
import pytest
import rclpy
from {name}.{name}_node import {class_name}Node


@pytest.fixture(scope='module', autouse=True)
def init_rclpy():
    rclpy.init()
    yield
    rclpy.shutdown()


def test_node_creation():
    node = {class_name}Node()
    assert node.get_name() == '{name}'
    node.destroy_node()
""")

    # Standard ament lint test files for Python packages
    (pkg / "test" / "test_copyright.py").write_text(py_header + """
from ament_copyright.main import main
import pytest


@pytest.mark.copyright
@pytest.mark.linter
def test_copyright():
    rc = main(argv=['.', 'test'])
    assert rc == 0, 'Found errors'
""")

    (pkg / "test" / "test_flake8.py").write_text(py_header + """
from ament_flake8.main import main_with_errors
import pytest


@pytest.mark.flake8
@pytest.mark.linter
def test_flake8():
    rc, errors = main_with_errors(argv=[])
    assert rc == 0, \\
        'Found %d code style errors / warnings:\\n' % len(errors) + \\
        '\\n'.join(errors)
""")

    (pkg / "test" / "test_pep257.py").write_text(py_header + """
from ament_pep257.main import main
import pytest


@pytest.mark.pep257
@pytest.mark.linter
def test_pep257():
    rc = main(argv=['.', 'test'])
    assert rc == 0, 'Found errors'
""")

    # Generate launch file
    (pkg / "launch" / "bringup.launch.py").write_text(
        _generate_launch_file(name, maintainer_name=maintainer_name))

    # Generate README
    (pkg / "README.md").write_text(_generate_readme(name))

    _write_package_xml(pkg, name, "ament_python", ["rclpy"],
                       maintainer_name=maintainer_name,
                       maintainer_email=maintainer_email,
                       extra_test=["ament_copyright", "ament_flake8",
                                   "ament_pep257", "python3-pytest"])
    print(f"Created Python package: {pkg}")


def create_interfaces_package(name: str, dest: Path,
                              maintainer_name: str = "TODO",
                              maintainer_email: str = "todo@todo.com") -> None:
    pkg = dest / name
    dirs = [pkg / "msg", pkg / "srv", pkg / "action"]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    (pkg / "msg" / "Status.msg").write_text("""std_msgs/Header header
uint8 mode
string description
float64 battery_voltage
""")

    (pkg / "srv" / "SetMode.srv").write_text("""string mode
bool force
---
bool success
string message
""")

    (pkg / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.8)
project({name})

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${{PROJECT_NAME}}
  "msg/Status.msg"
  "srv/SetMode.srv"
  DEPENDENCIES std_msgs
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_export_dependencies(rosidl_default_runtime)
ament_package()
""")

    # Generate README
    (pkg / "README.md").write_text(_generate_readme(name))

    _write_package_xml(pkg, name, "ament_cmake",
                       ["std_msgs"],
                       maintainer_name=maintainer_name,
                       maintainer_email=maintainer_email,
                       extra_buildtool=["rosidl_default_generators"],
                       extra_exec=["rosidl_default_runtime"],
                       extra_member=["rosidl_interface_packages"])
    print(f"Created interfaces package: {pkg}")


def _class_name(name: str) -> str:
    """Convert a snake_case package name to CamelCase class name."""
    return "".join(w.capitalize() for w in name.split("_"))


def _write_package_xml(pkg: Path, name: str, build_type: str,
                       deps: list,
                       maintainer_name: str = "TODO",
                       maintainer_email: str = "todo@todo.com",
                       extra_exec: Optional[list] = None,
                       extra_member: Optional[list] = None,
                       extra_buildtool: Optional[list] = None,
                       extra_test: Optional[list] = None) -> None:
    dep_lines = "\n".join(f"  <depend>{d}</depend>" for d in deps)
    exec_lines = ""
    if extra_exec:
        exec_lines = "\n" + "\n".join(
            f"  <exec_depend>{d}</exec_depend>" for d in extra_exec)
    buildtool_lines = ""
    if extra_buildtool:
        buildtool_lines = "\n" + "\n".join(
            f"  <buildtool_depend>{b}</buildtool_depend>" for b in extra_buildtool)
    member_lines = ""
    if extra_member:
        member_lines = "\n" + "\n".join(
            f"  <member_of_group>{m}</member_of_group>" for m in extra_member)
    test_lines = "\n  <test_depend>ament_lint_auto</test_depend>" \
                 "\n  <test_depend>ament_lint_common</test_depend>"
    if extra_test:
        test_lines += "\n" + "\n".join(
            f"  <test_depend>{t}</test_depend>" for t in extra_test)

    (pkg / "package.xml").write_text(f"""<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd"
  schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>{name}</name>
  <version>0.1.0</version>
  <description>TODO: Package description</description>
  <maintainer email="{maintainer_email}">{maintainer_name}</maintainer>
  <license>Apache-2.0</license>

  <buildtool_depend>{build_type}</buildtool_depend>{buildtool_lines}
{dep_lines}{exec_lines}
{test_lines}
{member_lines}
  <export>
    <build_type>{build_type}</build_type>
  </export>
</package>
""")


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a ROS 2 package with best-practice structure")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("name", help="Package name (snake_case)")
    parser.add_argument("--type", choices=["cpp", "python", "interfaces"],
                        default="cpp", help="Package type")
    parser.add_argument("--dest", default=".", help="Destination directory")
    parser.add_argument("--component", action="store_true", default=False,
                        help="Register as a composable node component (C++ only)")
    parser.add_argument("--maintainer-name", default="TODO",
                        help="Maintainer name for package.xml (default: TODO)")
    parser.add_argument("--maintainer-email", default="todo@todo.com",
                        help="Maintainer email for package.xml (default: todo@todo.com)")
    parser.add_argument("--force", action="store_true", default=False,
                        help="Overwrite existing package directory")
    args = parser.parse_args()

    if not re.match(r'^[a-z][a-z0-9_]*$', args.name):
        print(f"Error: Package name '{args.name}' is invalid. "
              "Use snake_case (lowercase letters, digits, underscores; "
              "must start with a letter).", file=sys.stderr)
        sys.exit(1)

    dest = Path(args.dest)
    if not dest.exists():
        dest.mkdir(parents=True)

    # Overwrite protection
    pkg_dir = dest / args.name
    if pkg_dir.exists() and not args.force:
        print(f"Warning: Package directory '{pkg_dir}' already exists. "
              f"Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    m_name = args.maintainer_name
    m_email = args.maintainer_email
    creators = {
        "cpp": lambda n, d: create_cpp_package(
            n, d, args.component, m_name, m_email),
        "python": lambda n, d: create_python_package(
            n, d, m_name, m_email),
        "interfaces": lambda n, d: create_interfaces_package(
            n, d, m_name, m_email),
    }
    creators[args.type](args.name, dest)


if __name__ == "__main__":
    main()
