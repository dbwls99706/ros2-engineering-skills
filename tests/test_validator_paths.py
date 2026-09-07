"""Exercise legacy validator branches in-process, not only via subprocesses."""

import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import skill_validate_hook as legacy


def invoke(monkeypatch, capsys, args, payload=None):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload) if payload else ''))
    monkeypatch.delenv('TOOL_NAME', raising=False)
    monkeypatch.delenv('TOOL_INPUT', raising=False)
    with pytest.raises(SystemExit) as raised:
        legacy.main(args)
    output = capsys.readouterr()
    return raised.value.code, output


def test_manual_aggregation_and_debug(monkeypatch, capsys, tmp_path):
    source = tmp_path / 'node.py'
    source.write_text('time.sleep(1)\n', encoding='utf-8')
    missing = tmp_path / 'missing.py'
    code, out = invoke(monkeypatch, capsys,
                       ['--file', str(source), str(missing), '--command', 'rm -rf /', '--debug'])
    report = json.loads(out.out)
    assert code == 1 and report['mode'] == 'manual'
    assert len(report['issues']) >= 3
    assert 'debug' not in report


@pytest.mark.parametrize('kind', ['directory', 'unsupported', 'undecodable', 'missing'])
def test_manual_file_branches(monkeypatch, capsys, tmp_path, kind):
    path = tmp_path / ('file.txt' if kind == 'unsupported' else 'file.py')
    if kind == 'directory':
        path.mkdir()
    elif kind == 'undecodable':
        path.write_bytes(b'\xff')
    elif kind == 'unsupported':
        path.write_text('text', encoding='utf-8')
    code, out = invoke(monkeypatch, capsys, ['--file', str(path)])
    report = json.loads(out.out)
    if kind == 'unsupported':
        assert code == 0 and report['checks_skipped']
    else:
        assert code == 1 and report['issues']


@pytest.mark.parametrize('tool,input_data', [
    ('Write', {'file_path': 'node.py', 'content': 'time.sleep(1)'}),
    ('mcp__files__write', {'path': 'node.py', 'content': 'time.sleep(1)'}),
    ('MultiEdit', {'file_path': 'node.py', 'edits': [None, {}, {'new_string': 'time.sleep(1)'}]}),
    ('Edit', {'file_path': 'node.py', 'new_string': 'time.sleep(1)'}),
])
def test_event_edits_retain_warnings(monkeypatch, capsys, tool, input_data):
    code, out = invoke(monkeypatch, capsys, ['--debug'],
                       {'tool_name': tool, 'tool_input': input_data})
    report = json.loads(out.out)
    assert code == 0 and report['issues_count'] > 0
    assert report['debug']['source'] == 'stdin'


def test_event_refusal_keeps_stderr_only(monkeypatch, capsys):
    code, out = invoke(monkeypatch, capsys, [],
                       {'tool_name': 'Bash', 'tool_input': {'command': 'rm -rf /'}})
    assert code == 2 and out.err and not out.out


def test_manual_empty_command_is_not_event_mode(monkeypatch, capsys):
    code, out = invoke(monkeypatch, capsys, ['--command', ''])
    assert code == 0 and json.loads(out.out)['mode'] == 'manual'


def test_unreadable_stdin_falls_back_to_environment(monkeypatch):
    class BrokenInput:
        def isatty(self):
            return False

        def read(self):
            raise OSError('unreadable')
    monkeypatch.setattr(sys, 'stdin', BrokenInput())
    monkeypatch.setenv('TOOL_NAME', 'Write')
    monkeypatch.setenv('TOOL_INPUT', '{bad')
    name, data, debug = legacy._read_tool_context()
    assert name == 'Write' and data == {} and 'env_parse_error' in debug


def test_check_file_read_error_and_comment_heuristics(tmp_path):
    assert legacy.check_file(str(tmp_path / 'missing.py')) == []
    assert legacy._comment_markers('unknown.txt') == ('#', '//')
    content = 'value = "#"; time.sleep(1)'
    assert not legacy._is_in_comment(content, content.index('time.sleep'))
    assert legacy.check_content(content, 'node.py')
