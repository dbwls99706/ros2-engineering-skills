"""Protocol tests: no grants, no Stop loops, bounded failures, real JSON fields."""

import io
import json
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
    assert args[0][1].endswith('/scripts/skill_validate_hook.py')
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
