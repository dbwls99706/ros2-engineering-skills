"""Static extraction checks; the separate ROS gate executes the real examples."""

import ast
from pathlib import Path

import pytest

from tests.check_lifecycle_reference import example


ROOT = Path(__file__).resolve().parents[1]


def test_selects_one_section_not_a_following_example():
    text = '## A\n\n```python\nx = 1\n```\n## B\n```python\nx = 2\n```\n'
    assert example(text, 'A', 'python') == 'x = 1\n'


@pytest.mark.parametrize('text', [
    '', '## A\nno code', '## A\n```cpp\nx\n```',
    '## A\n```python\nx', '## A\n```python\n\n```',
    '## A\n```python\nx\n```\n## A\n',
])
def test_malformed_reference_is_not_a_runtime_pass(text):
    with pytest.raises(ValueError):
        example(text, 'A', 'python')


def test_shipped_examples_are_extractable_and_python_parses():
    text = (ROOT / 'references/lifecycle-components.md').read_text(encoding='utf-8')
    source = example(text, '3. Implementing lifecycle transitions (rclpy)', 'python')
    ast.parse(source)
    cpp = example(text, '2. Implementing lifecycle transitions (rclcpp)', 'cpp')
    assert 'class LidarProcessor' in cpp


def test_ros_runtime_runner_executes_reference_probe():
    script = (ROOT / 'tests/run_ros2_container_tests.sh').read_text(encoding='utf-8')
    assert 'python3 "$ROOT/tests/check_lifecycle_reference.py"' in script
