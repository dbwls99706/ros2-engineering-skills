"""Protocol tests: no grants, no Stop loops, bounded failures, real JSON fields."""

import io
import json
import os
import subprocess
from unittest.mock import Mock

import pytest

from scripts import claude_hook as hook


def payload(event='PreToolUse', **extra):
    return {'hook_event_name': event, 'tool_name': 'Write',
            'tool_input': {'file_path': '/workspace/node.py', 'content': 'pass'}, **extra}


def completed(code=0, **changes):
    report = {'status': 'pass', 'issues': [], 'checks_skipped': [], **changes}
    return subprocess.CompletedProcess([], code, json.dumps(report), '')


def test_clean_event_does_not_grant_permission(monkeypatch):
    run = Mock(return_value=completed())
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert hook.run_hook('PreToolUse', payload()) == ({}, '', 0)
    args, kwargs = run.call_args
    assert args[0][-1].endswith('/scripts/skill_validate_hook.py')
    assert args[0][1] == '-B'  # no __pycache__ in a provenance-tracked install
    assert kwargs['timeout'] == 8
    assert 'shell' not in kwargs
    assert 'TOOL_INPUT' not in kwargs['env']


def test_warning_reaches_model_context(monkeypatch):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(issues=[{
        'file': 'a.py', 'line': 2, 'severity': 'warning', 'message': 'Use a timer',
    }])))
    result, error, status = hook.run_hook('PreToolUse', payload())
    assert status == 0 and not error
    assert result['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    assert 'Use a timer' in result['hookSpecificOutput']['additionalContext']
    assert 'permissionDecision' not in result['hookSpecificOutput']


def test_block_is_stderr_only(monkeypatch):
    result = subprocess.CompletedProcess([], 2, 'ignored', 'refused')
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=result))
    assert hook.run_hook('PreToolUse', payload()) == (None, 'refused', 2)


def test_empty_block_reason(monkeypatch):
    result = subprocess.CompletedProcess([], 2, '', '')
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=result))
    assert hook.run_hook('PreToolUse', payload())[1] == 'Unsafe command refused.'


def test_stop_errors_remain_advisory(monkeypatch):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(
        code=1, status='fail', issues=[{'message': 'broken launch'}])))
    output, _, code = hook.run_hook('Stop', payload('Stop'))
    assert code == 0
    assert 'broken launch' in output['systemMessage']
    assert 'decision' not in output and 'hookSpecificOutput' not in output


def test_repeated_stop_does_not_run_validator(monkeypatch):
    run = Mock(side_effect=AssertionError('must not scan again'))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    output, _, code = hook.run_hook('Stop', payload('Stop', stop_hook_active=True))
    assert code == 0 and 'repeated Stop skipped' in output['systemMessage']
    run.assert_not_called()


@pytest.mark.parametrize('exc', [OSError('missing'), subprocess.TimeoutExpired('x', 8)])
def test_internal_failure_is_skipped(monkeypatch, exc):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(side_effect=exc))
    result, _, code = hook.run_hook('PreToolUse', payload())
    assert code == 0 and 'skipped' in result['systemMessage']


@pytest.mark.parametrize('code,text', [(3, '{}'), (0, 'oops'), (0, '[]'),
                                       (0, '{}'), (1, '{"status":"pass","issues":[],"checks_skipped":[]}')])
def test_bad_validator_output_is_never_a_pass(monkeypatch, code, text):
    result = subprocess.CompletedProcess([], code, text, '')
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=result))
    result, _, status = hook.run_hook('Stop', payload('Stop'))
    assert status == 0 and 'skipped' in result['systemMessage']


@pytest.mark.parametrize('value', [[], None, {}, payload('Other'),
                                   payload(tool_name=[]), payload(tool_input=[]),
                                   payload(tool_input={'content': 3}),
                                   payload(tool_input={'edits': 'wrong'}),
                                   payload(tool_input={'edits': [False]}),
                                   payload(tool_input={'edits': [{'new_string': []}]})])
def test_malformed_envelopes(value):
    result, _, code = hook.run_hook('PreToolUse', value)
    assert code == 0 and 'skipped' in result['systemMessage']


def test_notebook_source_is_inspected():
    result = hook.normalize_payload(payload(tool_name='NotebookEdit', tool_input={
        'notebook_path': 'analysis.ipynb', 'new_source': 'time.sleep(1)'}), 'PreToolUse')
    assert result['tool_name'] == 'Write'
    assert result['tool_input']['content'] == 'time.sleep(1)'
    assert result['tool_input']['file_path'].endswith('.py')


def test_multiedit_valid_strings_remain_intact():
    original = payload(tool_name='MultiEdit', tool_input={'edits': [{'new_string': 'x'}]})
    assert hook.normalize_payload(original, 'PreToolUse') == original


@pytest.mark.parametrize('report', [[], {}, {'status': 'pass', 'issues': {}, 'checks_skipped': []},
                                    {'status': 'pass', 'issues': [None], 'checks_skipped': []},
                                    {'status': 'fail', 'issues': [], 'checks_skipped': []}])
def test_bad_reports_rejected(report):
    with pytest.raises(ValueError):
        hook.summarize(report)


def test_feedback_bounded():
    text = hook.summarize({'status': 'pass', 'issues': [{'message': 'x'}] * 14,
                           'checks_skipped': ['yaml'] * 14})
    assert 'Additional findings omitted' in text and 'Skipped: yaml' in text
    assert len(hook.notice('x' * 8000)['systemMessage']) <= hook.MAX_FEEDBACK + 20


@pytest.mark.parametrize('raw', ['', '[]', '{', 'x' * (hook.MAX_INPUT + 1)],
                         ids=['empty', 'array', 'malformed-json', 'oversized'])
def test_cli_invalid_payload_is_advisory(monkeypatch, capsys, raw):
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO(raw))
    assert hook.main(['PreToolUse']) == 0
    assert 'skipped' in json.loads(capsys.readouterr().out)['systemMessage']


def test_cli_blocks_without_stdout(monkeypatch, capsys):
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO(json.dumps(payload())))
    monkeypatch.setattr(hook, 'run_hook', Mock(return_value=(None, 'reason', 2)))
    assert hook.main(['PreToolUse']) == 2
    out = capsys.readouterr()
    assert out.out == '' and out.err.strip() == 'reason'


def test_cli_clean_json(monkeypatch, capsys):
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO(json.dumps(payload('Stop'))))
    monkeypatch.setattr(hook, 'run_hook', Mock(return_value=({}, '', 0)))
    assert hook.main(['Stop']) == 0
    assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.parametrize('raw', [
    '{"hook_event_name":"Stop","hook_event_name":"PreToolUse"}',
    '{"tool_input":{"command":"safe","command":"other"}}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
])
def test_ambiguous_and_nonstandard_json_is_advisory(monkeypatch, capsys, raw):
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO(raw))
    run = Mock(side_effect=AssertionError('invalid input must not reach a validator'))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert hook.main(['PreToolUse']) == 0
    assert 'skipped' in json.loads(capsys.readouterr().out)['systemMessage']
    run.assert_not_called()


def test_multibyte_input_limit_is_in_bytes_not_characters(monkeypatch):
    monkeypatch.setattr(hook, 'MAX_INPUT', 40)
    raw = json.dumps({'text': '로봇' * 8}, ensure_ascii=False)
    assert len(raw) < 40 < len(raw.encode('utf-8'))
    with pytest.raises(ValueError, match='exceeds'):
        hook.read_payload(io.StringIO(raw))


def test_real_binary_stream_is_bounded_and_decoded(monkeypatch):
    monkeypatch.setattr(hook, 'MAX_INPUT', 40)
    data = json.dumps({'text': '로봇'}, ensure_ascii=False).encode('utf-8')
    stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8')
    assert hook.read_payload(stream) == {'text': '로봇'}
    stream = io.TextIOWrapper(io.BytesIO(b'x' * 100), encoding='utf-8')
    with pytest.raises(ValueError, match='exceeds'):
        hook.read_payload(stream)
    assert stream.buffer.tell() == 41


def test_invalid_utf8_stays_advisory(monkeypatch, capsys):
    stream = io.TextIOWrapper(io.BytesIO(b'\xff'), encoding='utf-8')
    monkeypatch.setattr(hook.sys, 'stdin', stream)
    assert hook.main(['Stop']) == 0
    assert 'skipped' in json.loads(capsys.readouterr().out)['systemMessage']


@pytest.mark.parametrize('bad', ['', [], None, 1])
def test_malformed_cwd_does_not_start_a_scan(monkeypatch, bad):
    run = Mock(side_effect=AssertionError('invalid cwd must not reach a validator'))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    report, error, code = hook.run_hook('Stop', payload('Stop', cwd=bad))
    assert code == 0 and not error and 'skipped' in report['systemMessage']
    run.assert_not_called()


@pytest.mark.parametrize('bad', ['true', 0, 1, None, []])
def test_stop_flag_cannot_be_coerced_to_boolean(bad):
    with pytest.raises(ValueError, match='Boolean'):
        hook.normalize_payload(payload('Stop', stop_hook_active=bad), 'Stop')


def test_excessively_nested_json_stays_advisory(monkeypatch, capsys):
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO('[' * 2000 + '0' + ']' * 2000))
    assert hook.main(['Stop']) == 0
    assert 'skipped' in json.loads(capsys.readouterr().out)['systemMessage']


def _stop_report(path):
    return completed(code=1, status='fail', issues=[{
        'file': str(path), 'severity': 'error', 'message': 'broken launch'}])


def test_same_stop_advisory_is_shown_once_per_session(monkeypatch, tmp_path):
    # Git status cannot attribute an untracked file to a session, so an
    # unchanged finding would otherwise repeat at every turn end (18 of 26
    # Stops in one session).
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    broken = tmp_path / 'robot.launch.py'
    broken.write_text('x = 1\n')
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=_stop_report(broken)))
    first = hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]
    assert 'broken launch' in first['systemMessage']
    assert hook.run_hook('Stop', payload('Stop', session_id='s1')) == ({}, '', 0)
    other = hook.run_hook('Stop', payload('Stop', session_id='s2'))[0]
    assert 'broken launch' in other['systemMessage']
    stamp = broken.stat().st_mtime_ns + 5_000_000_000
    os.utime(broken, ns=(stamp, stamp))  # edited again: report again
    again = hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]
    assert 'broken launch' in again['systemMessage']
    # A different finding on the same file is new information.
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(
        code=1, status='fail', issues=[{'file': str(broken), 'severity': 'error',
                                        'message': 'async entry point'}])))
    assert 'async entry point' in hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]['systemMessage']
    state_dir = tmp_path / 'cache' / 'ros2-engineering-skills' / 'stop-advisories'
    assert (state_dir.stat().st_mode & 0o777) == 0o700


@pytest.mark.parametrize('session_id', [None, '', 7, 'x' * 65, 'a/b', 'a b', '../x'])
def test_stop_without_a_usable_session_id_is_always_shown(monkeypatch, tmp_path, session_id):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(hook.subprocess, 'run',
                        Mock(return_value=_stop_report(tmp_path / 'gone.launch.py')))
    extra = {} if session_id is None else {'session_id': session_id}
    for _ in range(2):
        assert 'broken launch' in hook.run_hook('Stop', payload('Stop', **extra))[0]['systemMessage']
    assert not (tmp_path / 'cache').exists()


def test_stop_cache_failures_fall_back_to_showing(monkeypatch, tmp_path):
    # The cache only reduces repetition: unwritable state, a symlinked state
    # file or garbage content must never hide a finding or change exit codes.
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(hook.subprocess, 'run',
                        Mock(return_value=_stop_report(tmp_path / 'gone.launch.py')))
    (tmp_path / 'cache').mkdir()
    (tmp_path / 'cache' / 'ros2-engineering-skills').write_text('not a directory')
    for _ in range(2):
        result = hook.run_hook('Stop', payload('Stop', session_id='blocked'))
        assert result[1:] == ('', 0) and 'broken launch' in result[0]['systemMessage']
    (tmp_path / 'cache' / 'ros2-engineering-skills').unlink()
    shown = hook.run_hook('Stop', payload('Stop', session_id='linked'))[0]
    assert 'broken launch' in shown['systemMessage']
    state = hook._advisory_state_path('linked')
    content = state.read_text()
    state.unlink()
    (tmp_path / 'elsewhere').write_text(content)
    state.symlink_to(tmp_path / 'elsewhere')
    assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='linked'))[0]['systemMessage']
    assert state.is_symlink() and (tmp_path / 'elsewhere').read_text() == content
    state.unlink()
    state.write_text('garbage')
    assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='linked'))[0]['systemMessage']
    assert hook.run_hook('Stop', payload('Stop', session_id='linked')) == ({}, '', 0)


@pytest.mark.parametrize('value,event,disabled', [
    ('all', 'Stop', True), ('stop', 'Stop', True), ('Stop', 'PreToolUse', False),
    ('pretooluse, stop', 'PreToolUse', True), ('', 'Stop', False), (' ALL ', 'PreToolUse', True)])
def test_hooks_can_be_disabled_per_event(monkeypatch, value, event, disabled):
    monkeypatch.setenv(hook.DISABLE_ENV, value)
    run = Mock(return_value=completed())
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert hook.hooks_disabled(event) is disabled
    if disabled:
        assert hook.run_hook(event, payload(event)) == ({}, '', 0)
        run.assert_not_called()


def test_knowledge_only_installation_never_runs_hooks(monkeypatch, tmp_path):
    # A session that registered the plugin before a knowledge-only reinstall
    # keeps invoking scripts/claude_hook.py from the replaced directory.
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    manifest = tmp_path / 'INSTALL_PROVENANCE.json'
    manifest.write_text(json.dumps({'layout': 'knowledge-only'}))
    assert hook.hooks_disabled('PreToolUse', root=tmp_path)
    manifest.write_text(json.dumps({'layout': 'plugin'}))
    assert not hook.hooks_disabled('PreToolUse', root=tmp_path)
    manifest.write_text('[]')
    assert not hook.hooks_disabled('PreToolUse', root=tmp_path)
    manifest.write_text('{broken')
    assert not hook.hooks_disabled('PreToolUse', root=tmp_path)
    assert not hook.hooks_disabled('PreToolUse', root=tmp_path / 'missing')
    assert not hook.hooks_disabled('Stop')  # this checkout carries no manifest


def test_symlinked_advisory_state_directory_is_not_used(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(hook.subprocess, 'run',
                        Mock(return_value=_stop_report(tmp_path / 'gone.launch.py')))
    state_dir = tmp_path / 'cache' / 'ros2-engineering-skills' / 'stop-advisories'
    state_dir.parent.mkdir(parents=True)
    (tmp_path / 'elsewhere').mkdir()
    state_dir.symlink_to(tmp_path / 'elsewhere', target_is_directory=True)
    for _ in range(2):
        assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]['systemMessage']
    assert list((tmp_path / 'elsewhere').iterdir()) == []
