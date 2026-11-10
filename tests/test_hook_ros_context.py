"""Automatic hook advice applies only where ROS context is confirmed.

The plugin hooks run in every project a session works in. These tests pin
which evidence establishes ROS context, per file, and that the validation
functions themselves are unchanged for explicit use.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import skill_stop_hook as stop  # noqa: E402
import skill_validate_hook as pre  # noqa: E402

AMENT_CMAKE = ('<?xml version="1.0"?>\n<package format="3">\n  <name>robot</name>\n'
               '  <license>Apache-2.0</license>\n'
               '  <buildtool_depend>ament_cmake</buildtool_depend>\n</package>\n')
AMENT_PYTHON = ('<?xml version="1.0"?>\n<package format="3">\n  <name>robot</name>\n'
                '  <license>Apache-2.0</license>\n'
                '  <export><build_type>ament_python</build_type></export>\n</package>\n')
MANIFESTS = {
    'ament_cmake': (AMENT_CMAKE, True),
    'ament_python': (AMENT_PYTHON, True),
    'catkin': (AMENT_CMAKE.replace('ament_cmake', 'catkin'), False),
    'no build declaration': ('<package><name>x</name></package>', False),
    'other root element': ('<project><buildtool_depend>ament_cmake</buildtool_depend></project>',
                           False),
    'not xml': ('{"name": "web"}', False),
    'empty': ('', False),
}


def _event(tool_input, cwd=None, tool='Write', env=None):
    payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input}
    if cwd is not None:
        payload['cwd'] = str(cwd)
    environ = {key: value for key, value in os.environ.items() if key != 'ROS_DISTRO'}
    environ.update(env or {})
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/skill_validate_hook.py')],
        input=json.dumps(payload), capture_output=True, text=True, timeout=15,
        cwd=str(ROOT), env=environ)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('name', sorted(MANIFESTS))
def test_both_hooks_agree_on_what_a_ros_package_manifest_is(tmp_path, name):
    text, expected = MANIFESTS[name]
    path = tmp_path / 'package.xml'
    path.write_text(text, encoding='utf-8')
    assert pre._is_ros_package_manifest(str(path)) is expected
    assert stop._is_ros_package_manifest(str(path)) is expected
    source = tmp_path / 'src' / 'node.py'
    assert pre._ros_package_above(str(source), str(tmp_path)) is expected
    assert stop._ros_package_above(str(source), str(tmp_path)) is expected


def test_manifest_lookup_ignores_missing_oversized_and_non_regular_files(tmp_path):
    assert not pre._is_ros_package_manifest(str(tmp_path / 'package.xml'))
    (tmp_path / 'package.xml').mkdir()
    assert not pre._is_ros_package_manifest(str(tmp_path / 'package.xml'))
    assert not stop._is_ros_package_manifest(str(tmp_path / 'package.xml'))
    big = tmp_path / 'big' / 'package.xml'
    big.parent.mkdir()
    big.write_text(AMENT_CMAKE + '<!--' + 'x' * pre._MANIFEST_MAX_BYTES + '-->', encoding='utf-8')
    assert not pre._is_ros_package_manifest(str(big))
    assert not stop._is_ros_package_manifest(str(big))


def test_manifest_walk_stops_at_the_workspace_and_at_its_depth_limit(tmp_path):
    (tmp_path / 'package.xml').write_text(AMENT_CMAKE, encoding='utf-8')
    workspace = tmp_path / 'ws'
    deep = workspace.joinpath(*['d'] * (pre._ANCESTOR_LIMIT + 1)) / 'node.py'
    for module in (pre, stop):
        # A manifest above the workspace is outside the task.
        assert not module._ros_package_above(str(workspace / 'src' / 'node.py'), str(workspace))
        # Without a usable stop directory the walk is bounded by depth.
        assert module._ros_package_above(str(workspace / 'src' / 'node.py'), '')
        assert not module._ros_package_above(str(deep), '')
        # A stop directory that is not an ancestor is ignored, not trusted.
        assert module._ros_package_above(str(workspace / 'node.py'), str(tmp_path / 'other'))


@pytest.mark.parametrize('path,content', [
    ('/srv/webapp/retry.py', 'import time\n\ndef retry():\n    global counter\n    time.sleep(2)\n'),
    ('/srv/webapp/tasks.py', 'from launch import rocket\nimport time\ntime.sleep(1)\n'),
    ('/srv/webapp/build.py', 'ament_like = 1\nfrom ament_tools import x\ntime.sleep(1)\n'),
    ('/srv/app/main.cpp', '#include <chrono>\n#include "launch/config.h"\nvoid f() { time.sleep(1); }\n'),
    ('/srv/app/legacy.cpp', '#include <sensor_msgs/Image.h>\nvoid f() { time.sleep(1); }\n'),
    ('', 'import time\ntime.sleep(1)\n'),
])
def test_sources_without_confirmed_ros_context_get_no_advice(path, content):
    report = _event({'file_path': path, 'content': content}, cwd='/srv')
    assert report['issues'] == [] and report['status'] == 'pass'


@pytest.mark.parametrize('path,content', [
    ('/w/node.py', 'import rclpy\ntime.sleep(1)\n'),
    ('/w/node.py', 'from rclpy.node import Node\ntime.sleep(1)\n'),
    ('/w/node.py', 'from sensor_msgs.msg import Image\ntime.sleep(1)\n'),
    ('/w/node.py', 'from my_interfaces.srv import Arm\ntime.sleep(1)\n'),
    ('/w/node.py', '    from launch_ros.actions import Node\ntime.sleep(1)\n'),
    ('/w/node.cpp', '#include <rclcpp/rclcpp.hpp>\nvoid f() { time.sleep(1); }\n'),
    ('/w/node.hpp', '#include "rclcpp_lifecycle/lifecycle_node.hpp"\nvoid f() { time.sleep(1); }\n'),
    ('/w/node.cpp', '#include <sensor_msgs/msg/image.hpp>\nvoid f() { time.sleep(1); }\n'),
    ('', 'import rclpy\ntime.sleep(1)\n'),
    ('', '#include <rclcpp/rclcpp.hpp>\nvoid f() { time.sleep(1); }\n'),
])
def test_an_unambiguous_ros_import_or_include_is_enough(path, content):
    report = _event({'file_path': path, 'content': content})
    assert [issue['message'] for issue in report['issues'] if 'time.sleep' in issue['message']]


def test_launch_import_counts_only_in_a_launch_named_file():
    content = ('from launch import LaunchDescription\n'
               'from other import Node\n'
               'x = Node(node_executable="talker")\n')
    assert _event({'file_path': '/w/tool.py', 'content': content})['issues'] == []
    issues = _event({'file_path': '/w/bringup.launch.py', 'content': content})['issues']
    assert any('node_executable' in issue['message'] for issue in issues)


def test_valid_package_above_the_file_is_enough_for_a_fragment(tmp_path):
    (tmp_path / 'robot' / 'robot').mkdir(parents=True)
    (tmp_path / 'robot' / 'package.xml').write_text(AMENT_PYTHON, encoding='utf-8')
    (tmp_path / 'webapp').mkdir()
    fragment = {'old_string': 'pass', 'new_string': 'time.sleep(1)\n'}
    inside = _event({'file_path': str(tmp_path / 'robot/robot/node.py'), **fragment},
                    cwd=tmp_path, tool='Edit')
    assert inside['issues_count'] == 1
    # A ROS package elsewhere in the repository says nothing about this file.
    outside = _event({'file_path': str(tmp_path / 'webapp/retry.py'), **fragment},
                     cwd=tmp_path, tool='Edit')
    assert outside['issues'] == []


def test_a_file_merely_named_package_xml_is_not_evidence(tmp_path):
    (tmp_path / 'package.xml').write_text('<project><name>web</name></project>', encoding='utf-8')
    report = _event({'file_path': str(tmp_path / 'retry.py'), 'content': 'time.sleep(1)\n'},
                    cwd=tmp_path)
    assert report['issues'] == []


def test_cwd_and_tool_input_are_read_from_one_payload(tmp_path):
    # The relative path only resolves to the ROS package through the payload
    # cwd; the hook process itself runs elsewhere. Both fields come from the
    # single read of stdin.
    (tmp_path / 'src').mkdir()
    (tmp_path / 'package.xml').write_text(AMENT_CMAKE, encoding='utf-8')
    tool_input = {'file_path': 'src/node.py', 'content': 'time.sleep(1)\n'}
    assert _event(tool_input, cwd=tmp_path)['issues_count'] == 1
    assert _event(tool_input)['issues'] == []
    assert _event(tool_input, cwd='   ')['issues'] == []


def test_read_tool_context_keeps_its_three_values(monkeypatch):
    class Payload:
        def isatty(self):
            return False

        def read(self):
            return json.dumps({'tool_name': 'Write', 'cwd': '/w',
                               'tool_input': {'file_path': 'a.py'}})
    monkeypatch.setattr(sys, 'stdin', Payload())
    assert pre._read_hook_payload()[:3] == ('Write', {'file_path': 'a.py'}, '/w')
    name, tool_input, debug = pre._read_tool_context()
    assert (name, tool_input, debug['source']) == ('Write', {'file_path': 'a.py'}, 'stdin')


def test_existing_file_supplies_context_for_an_edit_fragment(tmp_path):
    node = tmp_path / 'node.py'
    node.write_text('import rclpy\n\n\ndef main():\n    pass\n', encoding='utf-8')
    plain = tmp_path / 'retry.py'
    plain.write_text('import time\n\n\ndef main():\n    pass\n', encoding='utf-8')
    fragment = {'old_string': 'pass', 'new_string': 'time.sleep(1)\n'}
    assert _event({'file_path': str(node), **fragment}, tool='Edit')['issues_count'] == 1
    assert _event({'file_path': str(plain), **fragment}, tool='Edit')['issues'] == []


def test_existing_file_is_read_only_up_to_its_documented_limit(tmp_path):
    # Known limit: an import that first appears after the head is not seen.
    late = tmp_path / 'late.py'
    late.write_text('#' + 'x' * pre._CONTEXT_HEAD_BYTES + '\nimport rclpy\n', encoding='utf-8')
    early = tmp_path / 'early.py'
    early.write_text('import rclpy\n#' + 'x' * pre._CONTEXT_HEAD_BYTES + '\n', encoding='utf-8')
    fragment = {'old_string': 'pass', 'new_string': 'time.sleep(1)\n'}
    assert _event({'file_path': str(late), **fragment}, tool='Edit')['issues'] == []
    assert _event({'file_path': str(early), **fragment}, tool='Edit')['issues_count'] == 1


def test_manual_file_check_is_not_scoped(tmp_path):
    # The user named the file, so the explicit check keeps its full reach.
    plain = tmp_path / 'retry.py'
    plain.write_text('import time\ntime.sleep(1)\n', encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/skill_validate_hook.py'), '--file', str(plain)],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and json.loads(result.stdout)['issues_count'] == 1
    assert pre.check_content('time.sleep(1)\n', 'retry.py')


@pytest.mark.parametrize('distro,fragment', [
    (None, 'ROS_DISTRO is not set'), ('', 'ROS_DISTRO is not set'),
    ('jazzy', 'Use ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST instead'),
    ('rolling', 'Use ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST instead')])
def test_localhost_only_advice_is_conditional_when_the_distro_is_unknown(monkeypatch, distro, fragment):
    if distro is None:
        monkeypatch.delenv('ROS_DISTRO', raising=False)
    else:
        monkeypatch.setenv('ROS_DISTRO', distro)
    messages = [issue['message'] for issue in
                pre.check_content('export ROS_LOCALHOST_ONLY=1\n', 'bringup.py')]
    assert len(messages) == 1 and fragment in messages[0] and 'since Iron' in messages[0]
    # An unknown distribution must not be told to switch variables outright.
    assert ('Humble and earlier' in messages[0]) is (not distro)


def test_advice_wording_does_not_overstate_the_defect():
    spin = pre.check_content('rclpy.spin_until_future_complete(node, future)\n', 'node.py')
    assert 'can deadlock depending on executor and callback-group' in spin[0]['message']
    assert 'main() is not inherently an anti-pattern' in spin[0]['message']
    sleep = pre.check_content('time.sleep(1)\n', 'node.py')
    assert 'inside a callback' in sleep[0]['message']
    assert 'create_wall_timer() instead' not in sleep[0]['message']


# --- Stop: which launch-named files an automatic run validates ----------------

MISSING_ENTRY = 'def generate_description():\n    return None\n'


def _stop_main(workspace, monkeypatch, capsys, explicit=False):
    if explicit:
        monkeypatch.setenv('SKILL_WORKSPACE', str(workspace))
    else:
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
    monkeypatch.setattr(stop, '_resolve_workspace', lambda: str(workspace))
    monkeypatch.setattr(stop, '_resolve_log_path', lambda workspace: None)
    with pytest.raises(SystemExit):
        stop.main()
    return json.loads(capsys.readouterr().out)['issues']


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_validation_function_stays_strict_for_everything_it_is_given(tmp_path):
    path = _write(tmp_path, 'launch/robot.launch.py', MISSING_ENTRY)
    issues = stop.validate_launch_file_syntax(str(path))
    assert len(issues) == 1 and 'Missing' in issues[0]['message']
    broken = _write(tmp_path, 'deploy.launch.py', 'def deploy(:\n')
    assert 'Syntax error' in stop.validate_launch_file_syntax(str(broken))[0]['message']


def test_automatic_run_validates_per_file_not_per_workspace(tmp_path, monkeypatch, capsys):
    _write(tmp_path, 'robot/package.xml', AMENT_CMAKE)
    _write(tmp_path, 'robot/launch/bringup.launch.py', MISSING_ENTRY)
    _write(tmp_path, 'webapp/deploy.launch.py', 'from django.core import management\n')
    _write(tmp_path, 'webapp/broken.launch.py', 'def deploy(:\n')
    issues = _stop_main(tmp_path, monkeypatch, capsys)
    assert [Path(issue['file']).name for issue in issues] == ['bringup.launch.py']
    assert 'Missing' in issues[0]['message']


def test_explicit_workspace_validates_every_launch_named_file(tmp_path, monkeypatch, capsys):
    _write(tmp_path, 'webapp/deploy.launch.py', MISSING_ENTRY)
    _write(tmp_path, 'webapp/broken.launch.py', 'def deploy(:\n')
    assert _stop_main(tmp_path, monkeypatch, capsys) == []
    issues = _stop_main(tmp_path, monkeypatch, capsys, explicit=True)
    assert sorted(Path(issue['file']).name for issue in issues) == [
        'broken.launch.py', 'deploy.launch.py']


@pytest.mark.parametrize('text,expected', [
    # A launch import or the entry-point name in the file is ROS launch code.
    ('from launch import LaunchDescription\ndef generate_launch_description(:\n', 'Syntax error'),
    ('generate_launch_description = None\n', 'not'),
    ('import launch_ros\nvalue = 1\n', 'Missing'),
    # Nothing ties this file to ROS launch.
    (MISSING_ENTRY, None),
    ('def deploy(:\n', None),
])
def test_launch_code_in_the_file_is_enough_without_a_package(tmp_path, monkeypatch, capsys,
                                                             text, expected):
    _write(tmp_path, 'tools/robot.launch.py', text)
    issues = _stop_main(tmp_path, monkeypatch, capsys)
    if expected is None:
        assert issues == []
    else:
        assert len(issues) == 1 and expected in issues[0]['message']


def test_committed_manifest_scopes_a_newly_added_launch_file(tmp_path, monkeypatch, capsys):
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True,
                       env={**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
                            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull})
    git('init', '-q')
    _write(tmp_path, 'robot/package.xml', AMENT_CMAKE)
    git('add', '-A')
    git('commit', '-q', '-m', 'package')
    _write(tmp_path, 'robot/launch/new.launch.py', MISSING_ENTRY)
    _write(tmp_path, 'notes/deploy.launch.py', MISSING_ENTRY)
    # The manifest is unchanged, so it is not among the changed candidates...
    launch_files, package_xmls, _ = stop._validation_files(str(tmp_path))
    assert package_xmls == [] and len(launch_files) == 2
    # ...and the new launch file beside it is still recognized and reported.
    issues = _stop_main(tmp_path, monkeypatch, capsys)
    assert [Path(issue['file']).name for issue in issues] == ['new.launch.py']
