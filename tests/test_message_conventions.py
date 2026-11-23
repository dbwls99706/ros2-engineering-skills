"""Message conventions shown when code that uses a message type is written."""

import io
import json
import sys

import pytest

from scripts import skill_validate_hook as hook


def keys(text, filename='node.py'):
    return [issue['key'] for issue in hook.message_conventions(text, filename)]


def test_one_advisory_per_imported_type_in_source_order():
    text = ('import rclpy\nfrom sensor_msgs.msg import (LaserScan,  # lidar\n    Imu)\n'
            'from std_msgs.msg import String\nfrom sensor_msgs.msg import LaserScan\n')
    issues = hook.message_conventions(text, 'node.py')
    assert [issue['key'] for issue in issues] == ['sensor_msgs/LaserScan', 'sensor_msgs/Imu']
    first = issues[0]
    assert set(first) == {'file', 'line', 'severity', 'category', 'key', 'message'}
    assert (first['file'], first['line'], first['severity'], first['category']) == (
        'node.py', 2, 'info', 'convention')
    assert first['message'].startswith('sensor_msgs/LaserScan: ranges holds +inf')


@pytest.mark.parametrize('text,expected', [
    ('from nav_msgs.msg import Odometry, Path\n', ['nav_msgs/Odometry']),
    ('import sensor_msgs.msg\nscan = sensor_msgs.msg.LaserScan()\n', ['sensor_msgs/LaserScan']),
    ('from geometry_msgs.msg import Quaternion as Q\n', ['geometry_msgs/Quaternion']),
    ('from std_msgs.msg import String\n', []),
    ('from my_msgs.msg import LaserScan\n', []),
    ('from sensor_msgs.srv import SetCameraInfo\n', []),
    ('# from sensor_msgs.msg import Imu\n', []),
    ('', []),
])
def test_python_imports(text, expected):
    assert keys(text) == expected


def test_cpp_includes():
    text = ('#include <sensor_msgs/msg/laser_scan.hpp>\n#include "sensor_msgs/msg/point_cloud2.hpp"\n'
            '#include <nav_msgs/msg/occupancy_grid.hpp>\n#include <std_msgs/msg/string.hpp>\n')
    assert keys(text, 'node.cpp') == [
        'sensor_msgs/LaserScan', 'sensor_msgs/PointCloud2', 'nav_msgs/OccupancyGrid']


def test_every_convention_is_a_statement_of_the_reference():
    # Each entry must be backed by references/message-types.md, which carries the sources.
    from pathlib import Path
    reference = (Path(hook.__file__).resolve().parents[1] / 'references' / 'message-types.md').read_text(
        encoding='utf-8')
    for package, name in hook.MESSAGE_CONVENTIONS:
        assert '%s/msg/%s`' % (package, name) in reference or '(`%s`)' % name in reference, (package, name)
    for needle in ('REP-117', '-1.0', 'includes gravity', 'including any padding', 'look up by name',
                   'NOT in the odom frame', '`-1` = unknown', 'is_dense', 'always be normalized'):
        assert needle in reference


def run_event(monkeypatch, capsys, tool_input, cwd):
    payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Write', 'tool_input': tool_input, 'cwd': str(cwd)}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.delenv('TOOL_NAME', raising=False)
    monkeypatch.delenv('TOOL_INPUT', raising=False)
    try:
        hook.main([])
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def test_written_node_gets_the_conventions_of_its_messages(monkeypatch, capsys, tmp_path):
    content = 'import rclpy\nfrom sensor_msgs.msg import LaserScan\n\n\ndef main():\n    rclpy.init()\n'
    report = run_event(monkeypatch, capsys, {'file_path': str(tmp_path / 'stop.py'), 'content': content}, tmp_path)
    assert [i['key'] for i in report['issues'] if i.get('category') == 'convention'] == ['sensor_msgs/LaserScan']
    assert report['status'] == 'pass'


def test_edit_fragment_uses_the_imports_of_the_file_on_disk(monkeypatch, capsys, tmp_path):
    target = tmp_path / 'stop.py'
    target.write_text('import rclpy\nfrom sensor_msgs.msg import Imu\n\n\ndef main():\n    pass\n')
    report = run_event(monkeypatch, capsys, {'file_path': str(target), 'new_string': '    rclpy.init()\n'}, tmp_path)
    assert [i['key'] for i in report['issues'] if i.get('category') == 'convention'] == ['sensor_msgs/Imu']


def test_file_outside_ros_context_gets_nothing(monkeypatch, capsys, tmp_path):
    content = 'from sensor_msgs.msg import LaserScan\n'  # a ROS import is context; plain text is not
    report = run_event(monkeypatch, capsys, {'file_path': str(tmp_path / 'notes.md'), 'content': content}, tmp_path)
    assert report['issues'] == []
    report = run_event(monkeypatch, capsys, {'file_path': str(tmp_path / 'tool.py'), 'content': 'x = 1\n'}, tmp_path)
    assert report['issues'] == []
