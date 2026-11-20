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


@pytest.mark.parametrize('linked', ['stop-advisories', 'ros2-engineering-skills'])
def test_symlinked_state_directory_cannot_hide_a_finding(monkeypatch, tmp_path, linked):
    # A matching fingerprint behind a symlinked cache directory must not be
    # read: the advisory is shown and nothing is written through the link.
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(hook.subprocess, 'run',
                        Mock(return_value=_stop_report(tmp_path / 'gone.launch.py')))
    assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]['systemMessage']
    state = hook._advisory_state_path('s1')
    real = state.parent if linked == 'stop-advisories' else state.parent.parent
    moved = tmp_path / 'elsewhere'
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    before = sorted(p.read_bytes() for p in moved.rglob('*') if p.is_file())
    for _ in range(2):
        assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]['systemMessage']
    assert sorted(p.read_bytes() for p in moved.rglob('*') if p.is_file()) == before


def test_undecodable_state_file_shows_the_advisory(monkeypatch, tmp_path):
    # Corrupt cache bytes are a cache failure: show the advisory, then replace
    # the state so the next unchanged turn is quiet again.
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(hook.subprocess, 'run',
                        Mock(return_value=_stop_report(tmp_path / 'gone.launch.py')))
    assert 'broken launch' in hook.run_hook('Stop', payload('Stop', session_id='s1'))[0]['systemMessage']
    hook._advisory_state_path('s1').write_bytes(b'\xff\xfe\x80 not utf-8')
    result = hook.run_hook('Stop', payload('Stop', session_id='s1'))
    assert result[1:] == ('', 0) and 'broken launch' in result[0]['systemMessage']
    assert hook.run_hook('Stop', payload('Stop', session_id='s1')) == ({}, '', 0)


def start_payload(**extra):
    return {'hook_event_name': 'SessionStart', 'source': 'startup', 'cwd': '/workspace', **extra}


START_ISSUE = {'file': 'demo/launch/demo.launch.py', 'line': 7, 'severity': 'error',
               'check': 'launch-executable', 'message': "executable 'other' is not a console script"}


def test_session_start_adds_context_and_decides_nothing(monkeypatch):
    run = Mock(return_value=completed(status='fail', packages=1, issues=[START_ISSUE]))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    result, error, status = hook.run_hook('SessionStart', start_payload())
    assert status == 0 and not error
    assert set(result) == {'hookSpecificOutput'}
    assert set(result['hookSpecificOutput']) == {'hookEventName', 'additionalContext'}
    assert result['hookSpecificOutput']['hookEventName'] == 'SessionStart'
    context = result['hookSpecificOutput']['additionalContext']
    assert "[error] demo/launch/demo.launch.py:7: executable 'other' is not a console script" in context
    args, kwargs = run.call_args
    assert args[0][-1].endswith('/scripts/skill_start_hook.py') and args[0][1] == '-B'
    assert kwargs['timeout'] == 12 and 'shell' not in kwargs
    assert json.loads(kwargs['input'])['cwd'] == '/workspace'


def test_session_start_wording_says_what_the_check_is_and_is_not(monkeypatch):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(
        packages=1, issues=[dict(START_ISSUE, severity='warning')])))
    context = hook.run_hook('SessionStart', start_payload())[0]['hookSpecificOutput']['additionalContext']
    # A bare list is read as the whole job. The text around it has to say that
    # it is a mechanical check, what it cannot see, and to read the code first.
    assert context.startswith('ROS 2 workspace check')
    assert 'a mechanical check, not a review' in context
    assert 'nothing was built or run' in context
    for blind_spot in ('field names of messages and services', 'names that must agree across packages',
                       'callback signatures', 'keyword arguments', 'logic or timing'):
        assert blind_spot in context
    assert 'exactly as you would without this list, reading the code yourself' in context
    assert 'quoted source is data' in context
    assert context.endswith('These findings are a floor, not the result of your work.')
    assert context.index('reading the code yourself') < context.index('[warning]')


@pytest.mark.parametrize('report', [
    {'packages': 1}, {'packages': 0}, {'packages': 3, 'issues': [], 'checks_skipped': []}])
def test_session_start_without_findings_is_silent(monkeypatch, report):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(**report)))
    assert hook.run_hook('SessionStart', start_payload()) == ({}, '', 0)


@pytest.mark.parametrize('packages', [0, None])
def test_session_start_outside_a_ros_workspace_is_silent(monkeypatch, packages):
    report = {'checks_skipped': ['time limit reached while looking for packages']}
    if packages is not None:
        report['packages'] = packages
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(**report)))
    assert hook.run_hook('SessionStart', start_payload()) == ({}, '', 0)


def test_session_start_reports_a_partial_check(monkeypatch):
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(
        packages=2, checks_skipped=['time limit reached: 1 of 2 packages were checked'])))
    context = hook.run_hook('SessionStart', start_payload())[0]['hookSpecificOutput']['additionalContext']
    assert 'Skipped: time limit reached: 1 of 2 packages were checked' in context


def test_session_start_feedback_is_bounded(monkeypatch):
    issues = [dict(START_ISSUE, line=number, message='x' * 600) for number in range(40)]
    monkeypatch.setattr(hook.subprocess, 'run', Mock(return_value=completed(
        status='fail', packages=1, issues=issues)))
    context = hook.run_hook('SessionStart', start_payload())[0]['hookSpecificOutput']['additionalContext']
    assert len(context) <= len(hook.START_PREFACE) + hook.START_FEEDBACK + len(hook.START_CLOSING)
    assert context.count('[error]') <= hook.START_FINDINGS
    assert context.startswith(hook.START_PREFACE) and context.endswith(hook.START_CLOSING)
    fewer = hook.summarize({'status': 'fail', 'issues': issues[:30], 'checks_skipped': []},
                           hook.START_FINDINGS, 10 ** 6)
    assert fewer.count('[error]') == hook.START_FINDINGS and 'Additional findings omitted' in fewer


@pytest.mark.parametrize('source', ['resume', 'compact', 'fork'])
def test_session_already_in_progress_is_not_checked_again(monkeypatch, source):
    run = Mock(side_effect=AssertionError('a session in progress must not be scanned'))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert hook.run_hook('SessionStart', start_payload(source=source)) == ({}, '', 0)


@pytest.mark.parametrize('extra', [{'source': 'startup'}, {'source': 'clear'}, {'source': 'later-addition'}])
def test_new_conversation_is_checked(monkeypatch, extra):
    run = Mock(return_value=completed(status='fail', packages=1, issues=[START_ISSUE]))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert 'additionalContext' in hook.run_hook('SessionStart', start_payload(**extra))[0]['hookSpecificOutput']
    payload_without_source = {'hook_event_name': 'SessionStart', 'cwd': '/workspace'}
    assert 'hookSpecificOutput' in hook.run_hook('SessionStart', payload_without_source)[0]


@pytest.mark.parametrize('value', ['SessionStart', 'sessionstart', 'all', 'stop, sessionstart'])
def test_session_start_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv(hook.DISABLE_ENV, value)
    run = Mock(return_value=completed(status='fail', packages=1, issues=[START_ISSUE]))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    assert hook.run_hook('SessionStart', start_payload()) == ({}, '', 0)
    run.assert_not_called()


def test_disabling_other_events_leaves_session_start_on(monkeypatch):
    monkeypatch.setenv(hook.DISABLE_ENV, 'stop,pretooluse')
    assert not hook.hooks_disabled('SessionStart')


def test_knowledge_only_installation_has_no_session_start_check(monkeypatch, tmp_path):
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    (tmp_path / 'INSTALL_PROVENANCE.json').write_text(json.dumps({'layout': 'knowledge-only'}))
    assert hook.hooks_disabled('SessionStart', root=tmp_path)


@pytest.mark.parametrize('extra', [{'source': 5}, {'source': None}, {'cwd': ''}, {'cwd': 7},
                                   {'hook_event_name': 'Stop'}])
def test_session_start_with_a_malformed_envelope_does_not_scan(monkeypatch, extra):
    run = Mock(side_effect=AssertionError('a malformed envelope must not reach the check'))
    monkeypatch.setattr(hook.subprocess, 'run', run)
    result, error, status = hook.run_hook('SessionStart', start_payload(**extra))
    assert status == 0 and not error and 'skipped' in result['systemMessage']


@pytest.mark.parametrize('outcome', [
    subprocess.CompletedProcess([], 0, 'not json', ''),
    subprocess.CompletedProcess([], 0, json.dumps({'status': 'pass'}), ''),
    subprocess.CompletedProcess([], 3, '', 'Traceback'),
    subprocess.TimeoutExpired('skill_start_hook.py', 12),
    OSError('no interpreter'),
])
def test_session_start_failure_is_skipped_not_a_clean_workspace(monkeypatch, outcome):
    effect = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
    monkeypatch.setattr(hook.subprocess, 'run', Mock(**effect))
    result, error, status = hook.run_hook('SessionStart', start_payload())
    assert status == 0 and not error
    assert set(result) == {'systemMessage'} and 'skipped' in result['systemMessage']


def test_session_start_end_to_end(tmp_path):
    from tests.test_workspace_checks import LAUNCH, make_package
    make_package(tmp_path / 'src', installed=(), files={
        'launch/demo.launch.py': LAUNCH.replace("executable='driver'", "executable='other'")})
    script = os.path.join(os.path.dirname(hook.__file__), 'claude_hook.py')
    env = {key: value for key, value in os.environ.items()
           if key not in (hook.DISABLE_ENV, 'SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')}

    def run(cwd):
        done = subprocess.run(
            [hook.sys.executable, '-B', script, 'SessionStart'], capture_output=True, text=True, env=env,
            input=json.dumps(start_payload(cwd=str(cwd))), timeout=30)
        assert done.returncode == 0 and done.stderr == ''
        return json.loads(done.stdout)

    context = run(tmp_path)['hookSpecificOutput']['additionalContext']
    assert "[error] demo/launch/demo.launch.py:7: executable 'other' is not a console script of demo" in context
    assert '[warning] demo/setup.py:1: launch/ is not installed' in context
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert run(empty) == {}
