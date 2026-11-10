"""Exercise the real adapter and bundled validators together without a client."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name,tool,expected', [
    ('Bash', {'command': 'ros2 topic list'}, 'clean'),
    ('Bash', {'command': 'rm -rf /'}, 'blocked'),
    ('Write', {'file_path': 'node.py', 'content': 'import rclpy\ntime.sleep(1)'}, 'warning'),
    ('Write', {'file_path': 'retry.py', 'content': 'import time\ntime.sleep(1)'}, 'clean'),
    ('NotebookEdit', {'notebook_path': 'demo.ipynb',
                      'new_source': 'import rclpy\ntime.sleep(1)'}, 'warning'),
])
def test_real_pretool_pipeline(name, tool, expected):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/claude_hook.py'), 'PreToolUse'],
        input=json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': name, 'tool_input': tool}),
        capture_output=True, text=True, timeout=12)
    if expected == 'blocked':
        assert result.returncode == 2 and result.stderr and not result.stdout
    else:
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert 'permissionDecision' not in result.stdout
        if expected == 'warning':
            assert output['hookSpecificOutput']['additionalContext']
        else:
            assert output == {}


PACKAGE_XML = ('<?xml version="1.0"?>\n<package format="3">\n  <name>robot</name>\n'
               '  <version>0.1.0</version>\n  <description>d</description>\n'
               '  <maintainer email="a@b.c">m</maintainer>\n  <license>Apache-2.0</license>\n'
               '  <buildtool_depend>ament_cmake</buildtool_depend>\n</package>\n')


def _stop(cwd, **env):
    environ = {key: value for key, value in os.environ.items() if key != 'SKILL_WORKSPACE'}
    environ.update(env)
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts/claude_hook.py'), 'Stop'],
        input=json.dumps({'hook_event_name': 'Stop', 'cwd': str(cwd), 'stop_hook_active': False}),
        capture_output=True, text=True, timeout=15, env=environ)


def test_stop_with_real_broken_launch_is_advisory(tmp_path):
    (tmp_path / 'package.xml').write_text(PACKAGE_XML, encoding='utf-8')
    (tmp_path / 'broken.launch.py').write_text('value = 1\n', encoding='utf-8')
    result = _stop(tmp_path)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert 'broken.launch.py' in output['systemMessage']
    assert 'decision' not in output and 'hookSpecificOutput' not in output


def test_stop_ignores_launch_named_file_without_ros_context(tmp_path):
    # Same file, no ROS package and no launch code: an automatic run must not
    # report a web project's deploy.launch.py, even when it does not parse.
    (tmp_path / 'broken.launch.py').write_text('value = 1\n', encoding='utf-8')
    (tmp_path / 'deploy.launch.py').write_text('def deploy(:\n', encoding='utf-8')
    result = _stop(tmp_path)
    assert result.returncode == 0 and json.loads(result.stdout) == {}


def test_explicit_workspace_keeps_the_strict_launch_check(tmp_path):
    (tmp_path / 'broken.launch.py').write_text('value = 1\n', encoding='utf-8')
    result = _stop(tmp_path, SKILL_WORKSPACE=str(tmp_path))
    assert result.returncode == 0
    assert 'broken.launch.py' in json.loads(result.stdout)['systemMessage']
