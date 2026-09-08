"""Static generator regressions; actual compilation and transitions run in ROS CI."""

import ast

import pytest

from scripts.create_package import create_cpp_package, create_python_package


@pytest.mark.parametrize('component', [False, True])
def test_component_registration_has_a_target_dependency(tmp_path, component):
    create_cpp_package('variant_probe', tmp_path, component=component)
    cmake = (tmp_path / 'variant_probe/CMakeLists.txt').read_text()
    target = 'target_link_libraries(${PROJECT_NAME}_lib PUBLIC rclcpp_components::component)'
    assert (target in cmake) is component
    assert ('ament_export_dependencies(rclcpp_components)' in cmake) is component


def test_lifecycle_parameter_declaration_is_not_repeated_on_configure(tmp_path):
    create_python_package('variant_probe', tmp_path, lifecycle=True)
    source = (tmp_path / 'variant_probe/variant_probe/variant_probe_node.py').read_text()
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef))
    methods = {item.name: item for item in node.body if isinstance(item, ast.FunctionDef)}

    def declares(method):
        return [item for item in ast.walk(method)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                and item.func.attr == 'declare_parameter']

    assert len(declares(methods['__init__'])) == 1
    assert not declares(methods['on_configure'])
    for name in ('on_deactivate', 'on_cleanup', 'on_shutdown', 'on_error'):
        assert any(isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                   and item.func.attr == '_release_timer' for item in ast.walk(methods[name]))


def test_ros_ci_covers_optional_generated_variants():
    from pathlib import Path
    script = (Path(__file__).parent / 'run_ros2_container_tests.sh').read_text()
    assert 'test_component_pkg --type cpp --component' in script
    assert 'test_lifecycle_pkg --type python --lifecycle' in script
    assert 'test_pkgs=(test_cpp_pkg test_component_pkg test_iface_pkg test_hw_pkg)' in script
    assert 'check_generated_lifecycle.py" test_lifecycle_pkg' in script
