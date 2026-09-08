"""Exercise the real adapter and bundled validators together without a client."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name,tool,expected', [
    ('Bash', {'command': 'ros2 topic list'}, 'clean'),
    ('Bash', {'command': 'rm -rf /'}, 'blocked'),
    ('Write', {'file_path': 'node.py', 'content': 'time.sleep(1)'}, 'warning'),
    ('NotebookEdit', {'notebook_path': 'demo.ipynb', 'new_source': 'time.sleep(1)'}, 'warning'),
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


def test_stop_with_real_broken_launch_is_advisory(tmp_path):
    (tmp_path / 'broken.launch.py').write_text('value = 1\n', encoding='utf-8')
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/claude_hook.py'), 'Stop'],
        input=json.dumps({'hook_event_name': 'Stop', 'cwd': str(tmp_path), 'stop_hook_active': False}),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert 'broken.launch.py' in output['systemMessage']
    assert 'decision' not in output and 'hookSpecificOutput' not in output
