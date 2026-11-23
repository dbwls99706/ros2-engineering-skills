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


# ---------------------------------------------------------------- workspace check

def start_payload(**extra):
    return {'hook_event_name': 'SessionStart', 'source': 'startup', 'cwd': '/workspace',
            'session_id': 'session-1', **extra}


def stop_payload(**extra):
    return {'hook_event_name': 'Stop', 'cwd': '/workspace', 'session_id': 'session-1', **extra}


ISSUE = {'file': 'demo/launch/demo.launch.py', 'line': 7, 'severity': 'error',
         'check': 'launch-executable', 'message': "executable 'other' is not a console script"}
SECOND = {'file': 'demo/setup.py', 'line': 1, 'severity': 'warning', 'check': 'install-rules',
          'message': 'launch/ is not installed'}


def workspace(issues=(), packages=1, **changes):
    """What the workspace hook prints."""
    return completed(status='fail' if any(i['severity'] == 'error' for i in issues) else 'pass',
                     packages=packages, issues=list(issues), **changes)


@pytest.fixture
def state(monkeypatch, tmp_path):
    """A private cache directory, and a helper that runs one hook event against scripted reports."""
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.delenv(hook.WORKSPACE_ENV, raising=False)
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)

    def run(event, payload, *reports):
        # At Stop the changed-file check runs after the workspace check. A test that scripts
        # only the workspace report gets a changed-file check with nothing to say.
        scripted = list(reports)

        def answer(*_args, **_kwargs):
            item = scripted.pop(0) if scripted else completed()
            if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
                raise item
            return item
        calls = Mock(side_effect=answer)
        monkeypatch.setattr(hook.subprocess, 'run', calls)
        return hook.run_hook(event, payload), calls
    return run


def test_session_start_records_and_says_nothing(state):
    result, calls = state('SessionStart', start_payload(), workspace([ISSUE]))
    assert result == ({}, '', 0)
    args, kwargs = calls.call_args
    assert args[0][-1].endswith('/scripts/skill_workspace_hook.py') and args[0][1] == '-B'
    assert kwargs['timeout'] == 12 and 'shell' not in kwargs
    assert json.loads(kwargs['input'])['cwd'] == '/workspace'


BROKEN_MANIFEST = {'file': '/workspace/src/second/package.xml', 'line': 0, 'severity': 'error',
                   'message': 'Failed to parse package.xml: no element found'}


def changed_files(*issues):
    """What the changed-file check prints at Stop."""
    return completed(1 if issues else 0, status='fail' if issues else 'pass', issues=list(issues))


def test_handover_and_changed_file_check_are_returned_at_the_same_stop(state):
    # A repeated Stop is skipped, so the first one has to carry both checks.
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, error, status), calls = state('Stop', stop_payload(), workspace([ISSUE]),
                                           changed_files(BROKEN_MANIFEST))
    assert status == 0 and not error and set(output) == {'decision', 'reason', 'systemMessage'}
    assert output['decision'] == 'block'
    # the user is told as before, and the model is given the same text with the handover
    assert output['systemMessage'].startswith('ROS 2 validation: [error] /workspace/src/second/package.xml:0: ')
    assert "executable 'other' is not a console script" in output['reason']
    assert 'The check of the files this session changed also reports the following' in output['reason']
    assert output['reason'].endswith(
        '[error] /workspace/src/second/package.xml:0: Failed to parse package.xml: no element found')
    assert 'ROS 2 validation: ' not in output['reason']
    assert calls.call_count == 2
    assert 'decision' not in state('Stop', stop_payload(stop_hook_active=True))[0][0]


def test_handover_alone_is_unchanged_when_the_changed_files_are_clean(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]), changed_files())
    assert set(output) == {'decision', 'reason'}
    assert 'files this session changed' not in output['reason']


def test_changed_file_check_alone_stays_advisory(state):
    state('SessionStart', start_payload(), workspace())
    result, _calls = state('Stop', stop_payload(), workspace(), changed_files(BROKEN_MANIFEST))
    output = result[0]
    assert set(output) == {'systemMessage'} and 'Failed to parse package.xml' in output['systemMessage']


def test_advise_mode_shows_both_checks_to_the_user(state, monkeypatch):
    monkeypatch.setenv(hook.WORKSPACE_ENV, 'advise')
    state('SessionStart', start_payload(), workspace([ISSUE]))
    result, _calls = state('Stop', stop_payload(), workspace([ISSUE]), changed_files(BROKEN_MANIFEST))
    output = result[0]
    assert set(output) == {'systemMessage'}
    assert 'Failed to parse package.xml' in output['systemMessage']
    assert "executable 'other' is not a console script" in output['systemMessage']


def test_a_changed_file_check_that_cannot_run_does_not_drop_the_handover(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]), OSError('gone'))
    assert output['decision'] == 'block' and "executable 'other'" in output['reason']
    assert 'validator could not run' in output['systemMessage']


@pytest.mark.parametrize('source', ['resume', 'compact', 'fork'])
def test_session_in_progress_keeps_its_record(state, source):
    result, calls = state('SessionStart', start_payload(source=source))
    assert result == ({}, '', 0)
    calls.assert_not_called()


def test_session_start_failure_is_reported_as_skipped(state):
    result, _calls = state('SessionStart', start_payload(),
                           subprocess.CompletedProcess([], 0, 'not json', ''))
    assert set(result[0]) == {'systemMessage'}
    assert 'skipped: workspace check could not run' in result[0]['systemMessage']


def test_findings_are_handed_over_once(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, error, status), calls = state('Stop', stop_payload(), workspace([ISSUE, SECOND]))
    assert status == 0 and not error and set(output) == {'decision', 'reason'}
    assert output['decision'] == 'block'
    reason = output['reason']
    assert reason.startswith('Before you finish: a static check of the ROS 2 workspace still reports')
    assert 'an addition to your own work, not a summary of it' in reason
    assert 'A request to fix or debug the code that names no file covers every finding you verify' in reason
    assert 'A request for one specific change does not: leave the other files alone' in reason
    assert "[error] demo/launch/demo.launch.py:7: executable 'other' is not a console script\n" in reason
    # The second finding appeared during the session and is marked as such.
    assert ('[warning] demo/setup.py:1: launch/ is not installed'
            ' (not reported when this conversation started)') in reason
    assert calls.call_count == 2  # the workspace check, then the changed-file check
    assert calls.call_args_list[1][0][0][-1].endswith('/scripts/skill_stop_hook.py')

    # The same findings are not handed over again; the ordinary Stop check runs instead.
    (output, _error, _status), calls = state('Stop', stop_payload(), workspace([ISSUE, SECOND]),
                                             completed())
    assert output == {} and calls.call_count == 2
    assert calls.call_args[0][0][-1].endswith('/scripts/skill_stop_hook.py')


def test_only_new_findings_are_handed_over_later(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    state('Stop', stop_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE, SECOND]))
    assert output['decision'] == 'block'
    assert 'demo/setup.py:1' in output['reason'] and 'demo.launch.py' not in output['reason']


def test_a_moved_line_is_the_same_finding(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    state('Stop', stop_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([dict(ISSUE, line=9)]),
                                              completed())
    assert output == {}


def test_repeated_stop_never_continues_the_turn(state):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, _error, _status), calls = state('Stop', stop_payload(stop_hook_active=True))
    assert 'repeated Stop skipped' in output['systemMessage'] and 'decision' not in output
    calls.assert_not_called()


def test_a_session_that_changed_nothing_is_handed_the_findings_too(state):
    # "I could not tell which code you mean" is where a beginner needs them most.
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]))
    assert output['decision'] == 'block' and 'not a console script' in output['reason']
    assert 'not reported when this conversation started' not in output['reason']


def test_the_check_reads_where_the_session_started_not_where_cd_left_it(state, tmp_path, monkeypatch):
    monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
    monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
    root = tmp_path / 'ws'
    inside = root / 'src' / 'one_package'
    inside.mkdir(parents=True)
    _result, calls = state('SessionStart', start_payload(cwd=str(root)), workspace([ISSUE]))
    assert calls.call_args_list[0][1]['env']['SKILL_WORKSPACE'] == str(root)
    # The model has changed directory into one package. A check of that
    # package alone would be handed over as if it were the workspace.
    _result, calls = state('Stop', stop_payload(cwd=str(inside)), workspace([ISSUE]))
    assert calls.call_args_list[0][1]['env']['SKILL_WORKSPACE'] == str(root)
    assert json.loads(calls.call_args[1]['input'])['cwd'] == str(inside)


def test_project_directory_is_used_when_there_is_no_record(state, tmp_path, monkeypatch):
    monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
    project = tmp_path / 'project'
    (project / 'src' / 'pkg').mkdir(parents=True)
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(project))
    _result, calls = state('Stop', stop_payload(cwd=str(project / 'src' / 'pkg')), workspace([ISSUE]))
    assert calls.call_args_list[0][1]['env']['SKILL_WORKSPACE'] == str(project)


@pytest.mark.parametrize('case', ['session left the recorded directory', 'recorded directory is gone',
                                  'record names no directory'])
def test_a_record_that_does_not_fit_is_not_used(state, tmp_path, monkeypatch, case):
    monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
    monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
    root, other = tmp_path / 'ws', tmp_path / 'other'
    root.mkdir()
    other.mkdir()
    state('SessionStart', start_payload(cwd=str(root)), workspace([ISSUE]))
    if case == 'recorded directory is gone':
        root.rmdir()
    elif case == 'record names no directory':
        path = hook._workspace_state_path('session-1')
        path.write_text(json.dumps({'baseline': [], 'root': 5, 'shown': []}))
    _result, calls = state('Stop', stop_payload(cwd=str(other)), workspace([ISSUE]))
    assert calls.call_args_list[0][1]['env']['SKILL_WORKSPACE'] == str(other)


def test_an_explicit_workspace_is_left_alone(state, tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path / 'chosen'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    _result, calls = state('Stop', stop_payload(cwd=str(tmp_path)), workspace([ISSUE]))
    assert calls.call_args_list[0][1]['env']['SKILL_WORKSPACE'] == str(tmp_path / 'chosen')


def test_session_without_a_record_is_handed_the_findings(state):
    # The plugin was enabled mid-session, or the session was resumed: no baseline.
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]))
    assert output['decision'] == 'block'
    assert 'not reported when this conversation started' not in output['reason']


@pytest.mark.parametrize('session_id', [None, '', 'bad id with spaces', 7, 'x' * 65])
def test_without_a_usable_session_id_the_turn_is_never_continued(state, session_id):
    payload = stop_payload()
    if session_id is None:
        del payload['session_id']
    else:
        payload['session_id'] = session_id
    for _ in range(2):  # shown every time, because it cannot be remembered, but never a block
        (output, _error, _status), _calls = state('Stop', payload, workspace([ISSUE]))
        assert set(output) == {'systemMessage'} and 'not a console script' in output['systemMessage']


def test_unwritable_cache_never_continues_the_turn(state, monkeypatch, tmp_path):
    blocked = tmp_path / 'file'
    blocked.write_text('not a directory')
    monkeypatch.setenv('XDG_CACHE_HOME', str(blocked))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]))
    assert set(output) == {'systemMessage'}


def test_symlinked_state_is_not_trusted(state, tmp_path):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    path = hook._workspace_state_path('session-1')
    real = tmp_path / 'elsewhere.json'
    real.write_text(json.dumps({'baseline': [], 'tree': 't0', 'shown': [hook._finding_key(ISSUE)]}))
    path.unlink()
    path.symlink_to(real)
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]))
    assert 'not a console script' in output['systemMessage']  # shown, not hidden, and no block


@pytest.mark.parametrize('content', ['{broken', '[]', '{"shown": "all", "baseline": 5}'])
def test_corrupt_state_shows_the_findings(state, content):
    state('SessionStart', start_payload(), workspace([ISSUE]))
    hook._workspace_state_path('session-1').write_text(content)
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE]))
    assert output['decision'] == 'block' and 'not a console script' in output['reason']


def test_advise_mode_shows_the_user_and_never_continues(state, monkeypatch):
    monkeypatch.setenv(hook.WORKSPACE_ENV, 'Advise')
    state('SessionStart', start_payload(), workspace([ISSUE]))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE, SECOND]))
    assert set(output) == {'systemMessage'} and 'launch/ is not installed' in output['systemMessage']


def test_off_mode_does_not_run_the_check(state, monkeypatch):
    monkeypatch.setenv(hook.WORKSPACE_ENV, 'off')
    result, calls = state('SessionStart', start_payload())
    assert result == ({}, '', 0)
    calls.assert_not_called()
    (output, _error, _status), calls = state('Stop', stop_payload(), completed())
    assert output == {} and calls.call_count == 1
    assert calls.call_args[0][0][-1].endswith('/scripts/skill_stop_hook.py')


@pytest.mark.parametrize('value', ['', 'handover', 'HANDOVER', 'something else'])
def test_handover_is_the_default_mode(monkeypatch, value):
    monkeypatch.setenv(hook.WORKSPACE_ENV, value)
    assert hook.workspace_mode() == 'handover'


@pytest.mark.parametrize('value,event', [('SessionStart', 'SessionStart'), ('stop', 'Stop'), ('all', 'Stop')])
def test_disabled_events_do_nothing(state, monkeypatch, value, event):
    monkeypatch.setenv(hook.DISABLE_ENV, value)
    payload = start_payload() if event == 'SessionStart' else stop_payload()
    result, calls = state(event, payload)
    assert result == ({}, '', 0)
    calls.assert_not_called()


def test_knowledge_only_installation_has_no_workspace_check(monkeypatch, tmp_path):
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    (tmp_path / 'INSTALL_PROVENANCE.json').write_text(json.dumps({'layout': 'knowledge-only'}))
    assert hook.hooks_disabled('SessionStart', root=tmp_path)


def test_directory_without_packages_is_left_to_the_ordinary_stop_check(state):
    (output, _error, _status), calls = state(
        'Stop', stop_payload(), workspace([], packages=0, checks_skipped=['time limit reached']), completed())
    assert output == {} and calls.call_count == 2


def test_clean_workspace_is_left_to_the_ordinary_stop_check(state):
    (output, _error, _status), calls = state('Stop', stop_payload(), workspace([]), completed(
        issues=[{'file': 'a.launch.py', 'line': 1, 'severity': 'warning', 'message': 'changed file'}]))
    assert 'changed file' in output['systemMessage'] and calls.call_count == 2


@pytest.mark.parametrize('outcome', [
    subprocess.CompletedProcess([], 0, 'not json', ''),
    subprocess.CompletedProcess([], 0, json.dumps({'status': 'pass'}), ''),
    subprocess.CompletedProcess([], 0, json.dumps({'status': 'pass', 'issues': ['text'], 'checks_skipped': []}), ''),
    subprocess.TimeoutExpired('skill_workspace_hook.py', 12),
    OSError('no interpreter'),
])
def test_a_failed_check_is_said_not_swallowed(state, outcome):
    # An empty answer after a failure would read as "nothing left to hand over".
    (output, error, status), _calls = state('Stop', stop_payload(), outcome, completed())
    assert status == 0 and not error and 'decision' not in output
    assert 'skipped: workspace check could not run' in output['systemMessage']


def test_a_failed_check_does_not_hide_the_changed_file_advisory(state):
    (output, _error, _status), _calls = state(
        'Stop', stop_payload(), OSError('no interpreter'),
        completed(issues=[{'file': 'a.launch.py', 'line': 1, 'severity': 'warning', 'message': 'changed file'}]))
    message = output['systemMessage']
    assert 'changed file' in message and 'skipped: workspace check could not run (OSError)' in message


def test_partial_check_is_said_in_the_handover(state):
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(
        [ISSUE], checks_skipped=['time limit reached: 1 of 2 packages were checked']))
    assert 'Skipped: time limit reached: 1 of 2 packages were checked' in output['reason']


def test_handover_is_bounded(state):
    issues = [dict(ISSUE, message='finding %03d ' % number + 'x' * 600) for number in range(40)]
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(issues))
    reason = output['reason']
    assert len(reason) <= len(hook.HANDOVER_PREFACE) + hook.HANDOVER_FEEDBACK
    assert reason.count('[error]') <= hook.HANDOVER_FINDINGS
    many = [dict(ISSUE, message='finding %03d' % number) for number in range(40)]
    (output, _error, _status), _calls = state('Stop', stop_payload(session_id='session-2'), workspace(many))
    assert output['reason'].count('[error]') == hook.HANDOVER_FINDINGS
    assert 'Additional findings omitted' in output['reason']


@pytest.mark.parametrize('extra', [{'source': 5}, {'cwd': ''}, {'cwd': 7}, {'hook_event_name': 'Stop'}])
def test_session_start_with_a_malformed_envelope_does_not_scan(state, extra):
    (output, error, status), calls = state('SessionStart', start_payload(**extra))
    assert status == 0 and not error and 'skipped' in output['systemMessage']
    calls.assert_not_called()


def test_handover_end_to_end(tmp_path, monkeypatch):
    from tests.test_workspace_checks import LAUNCH, make_package
    package = make_package(tmp_path / 'src', installed=(), files={
        'launch/demo.launch.py': LAUNCH.replace("executable='driver'", "executable='other'")})
    script = os.path.join(os.path.dirname(hook.__file__), 'claude_hook.py')
    env = {key: value for key, value in os.environ.items()
           if key not in (hook.DISABLE_ENV, hook.WORKSPACE_ENV, 'SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')}
    env['XDG_CACHE_HOME'] = str(tmp_path / 'cache')

    def run(event, cwd=package / 'demo', **extra):
        # From the second event on the shell sits inside the package, as after `cd`.
        payload = {'hook_event_name': event, 'cwd': str(cwd), 'session_id': 'end-to-end', **extra}
        done = subprocess.run([hook.sys.executable, '-B', script, event], capture_output=True, text=True,
                              env=env, input=json.dumps(payload), timeout=60)
        assert done.returncode == 0 and done.stderr == ''
        return json.loads(done.stdout)

    assert run('SessionStart', cwd=tmp_path, source='startup') == {}
    first = run('Stop')
    assert first['decision'] == 'block' and "executable 'other'" in first['reason']
    assert 'not reported when this conversation started' not in first['reason']
    # The session breaks something new, as a session that makes a package can.
    (package / 'demo' / 'driver.py').write_text(
        (package / 'demo' / 'driver.py').read_text().replace("'status', 10)", "'status')"))
    output = run('Stop')
    assert output['decision'] == 'block'
    assert 'create_publisher() is called without a QoS profile' in output['reason']
    assert 'not reported when this conversation started' in output['reason']
    assert "executable 'other'" not in output['reason']  # handed over at the first Stop
    assert 'decision' not in run('Stop', stop_hook_active=True)


def test_tests_of_a_changed_package_end_to_end(tmp_path):
    from tests.test_workspace_checks import make_package
    package = make_package(tmp_path / 'src', files={'test/test_driver.py': 'def test_it():\n    assert True\n'})
    script = os.path.join(os.path.dirname(hook.__file__), 'claude_hook.py')
    env = {key: value for key, value in os.environ.items()
           if key not in (hook.DISABLE_ENV, hook.WORKSPACE_ENV, 'SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')}
    env['XDG_CACHE_HOME'] = str(tmp_path / 'cache')

    def run(event, **extra):
        payload = {'hook_event_name': event, 'cwd': str(tmp_path), 'session_id': 'tests-end-to-end', **extra}
        done = subprocess.run([hook.sys.executable, '-B', script, event], capture_output=True, text=True,
                              env=env, input=json.dumps(payload), timeout=60)
        assert done.returncode == 0 and done.stderr == ''
        return json.loads(done.stdout)

    assert run('SessionStart', source='startup') == {}
    assert 'decision' not in run('Stop')  # nothing changed, nothing reported
    driver = package / 'demo' / 'driver.py'
    driver.write_text(driver.read_text() + '\n# edited in the session\n')
    output = run('Stop')
    assert output['decision'] == 'block'
    assert ('demo: `cd src/demo && python3 -m pytest test -q` '
            '(in a built workspace: `colcon test --packages-select demo`)') in output['reason']
    assert 'leaves the shell in the package directory' in output['reason']
    driver.write_text(driver.read_text() + '# again\n')
    assert 'decision' not in run('Stop')  # named once


# ---------------------------------------------------------------- message conventions

def tool_payload(name, **tool_input):
    return {'hook_event_name': 'PreToolUse', 'tool_name': name, 'tool_input': tool_input,
            'cwd': '/workspace', 'session_id': 'session-1'}


CONVENTION = {'file': 'stop.py', 'line': 2, 'severity': 'info', 'category': 'convention',
              'key': 'sensor_msgs/LaserScan', 'message': 'sensor_msgs/LaserScan: ranges holds +inf'}


def test_message_convention_is_shown_once_per_session(state):
    write = tool_payload('Write', file_path='/workspace/stop.py', content='pass')
    (output, _error, _status), _calls = state('PreToolUse', write, completed(issues=[CONVENTION]))
    assert '[info] stop.py:2: sensor_msgs/LaserScan: ranges holds +inf' in (
        output['hookSpecificOutput']['additionalContext'])
    (output, _error, _status), _calls = state('PreToolUse', write, completed(issues=[CONVENTION]))
    assert output == {}
    other = dict(CONVENTION, key='sensor_msgs/Imu', message='sensor_msgs/Imu: a covariance')
    warning = {'file': 'stop.py', 'line': 9, 'severity': 'warning', 'message': 'time.sleep() inside a callback'}
    (output, _error, _status), _calls = state('PreToolUse', write, completed(issues=[CONVENTION, other, warning]))
    context = output['hookSpecificOutput']['additionalContext']
    assert 'sensor_msgs/Imu' in context and 'time.sleep()' in context and 'LaserScan' not in context


def test_convention_without_a_session_id_is_shown_each_time(state):
    write = tool_payload('Write', file_path='/workspace/stop.py', content='pass')
    del write['session_id']
    for _ in range(2):
        (output, _error, _status), _calls = state('PreToolUse', write, completed(issues=[CONVENTION]))
        assert 'LaserScan' in output['hookSpecificOutput']['additionalContext']


# ---------------------------------------------------------------- tests of changed packages

def states(fingerprint='aaaa', tests='src/demo/test', **extra):
    return [{'package': 'demo', 'path': 'src/demo', 'fingerprint': fingerprint, 'tests': tests, **extra}]


def test_changed_package_with_tests_that_were_not_run_is_handed_over_once(state):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    (output, error, status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')))
    assert status == 0 and not error and output['decision'] == 'block'
    reason = output['reason']
    assert reason.startswith('No test of these packages was run in this conversation. For each package below '
                             'that you changed')
    assert 'unless they would move connected hardware' in reason
    assert ('demo: `cd src/demo && python3 -m pytest test -q` (in a built workspace: '
            '`colcon test --packages-select demo`)') in reason
    assert 'static check' not in reason  # no finding, so no finding preface
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('cccc')),
                                              completed())
    assert output == {}


def test_tests_follow_the_findings_in_one_handover(state):
    state('SessionStart', start_payload(), workspace([ISSUE], package_state=states()))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE], package_state=states('bbbb')))
    reason = output['reason']
    assert reason.index('Before you finish: a static check') < reason.index("executable 'other'") < reason.index(
        'No test of these packages was run in this conversation')


@pytest.mark.parametrize('command', [
    'python3 -m pytest src/demo/test -q', 'cd src/demo && pytest', 'colcon test --packages-select demo',
    'colcon  test', 'python -m unittest discover', 'launch_test src/demo/test/launch.py', 'py.test -x',
    'cd src/demo && python3 -m pytest test -q', '(cd src/demo && python3 -m pytest test -q 2>&1 | tail -5)',
    'python3 -m pytest src -q', 'python3 -m pytest src/demo/test/test_driver.py::test_it -k fast',
    'PYTHONPATH=src/demo timeout 120 python3 -m pytest src/demo/test', 'sudo -E env A=1 pytest src/demo',
    'colcon test --packages-select other demo --event-handlers console_direct+',
    'colcon build\ncolcon test --packages-up-to demo', 'cd /workspace/src/demo; python3.12 -m pytest',
    'colcon test --packages-skip other', 'colcon test --packages-select=demo --return-code-on-test-failure',
    'colcon test --event-handlers console_direct+ --pytest-args -k fast', 'pytest --ignore=src/other src',
    'python3 -m pytest -s src/demo/test', 'pytest --ignore src/demo/test/slow src/demo/test/test_driver.py',
    'echo start && /usr/bin/python3 -m pytest -o cache_dir=/tmp/c src/demo/test'])
def test_tests_the_session_ran_are_not_asked_for(state, command):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    state('PreToolUse', tool_payload('Bash', command=command), completed())
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')),
                                              completed())
    assert output == {}


@pytest.mark.parametrize('command', [
    'ls test', 'cat pytest.ini', 'colcon build', 'echo my-pytest-notes', 'grep unittests x',
    # the word is an argument, not the command
    'echo pytest', 'echo "done; pytest src/demo"', 'grep -rn pytest src/demo', 'pip install pytest',
    'which pytest colcon', 'cat notes | grep unittest', 'colcon test-result --verbose', 'python3 -m pip show pytest',
    # a test command that reaches another package only
    'python3 -m pytest src/other/test -q', 'cd src/other && python3 -m pytest test -q',
    'colcon test --packages-select other', 'cd /elsewhere && pytest', 'launch_test test/launch.py',
    # a line that cannot be split into words is not read
    'pytest "src/demo',
    # collecting or listing runs no test
    'pytest --collect-only src/demo/test', 'python3 -m pytest --co -q', 'cd src/demo && pytest --collect-only -q',
    'pytest --help', 'python3 -m pytest --version', 'pytest --fixtures src/demo/test', 'pytest --setup-plan',
    'colcon test --help', 'python3 -m unittest -h',
    # the package is left out of the run
    'colcon test --packages-skip demo', 'colcon test --packages-skip other demo',
    'colcon test --packages-skip=demo', 'python3 -m pytest --ignore=src/demo', 'pytest --ignore src/demo/test src',
    'pytest --ignore=src', 'pytest --ignore-glob="*demo*"',
    # a selection that is not worked out leaves the tests asked for
    'colcon test --packages-select-regex "de.*"', 'colcon test --packages-skip-regex other',
    'colcon test --packages-select-by-dep rclpy', 'colcon test --base-paths src/other',
    'colcon test --packages-select-test-failures'])
def test_other_commands_are_not_a_test_run(state, command):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    state('PreToolUse', tool_payload('Bash', command=command), completed())
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')))
    assert output['decision'] == 'block'


def test_a_test_run_of_one_package_leaves_the_other_asked_for(state):
    both = states() + [{'package': 'other', 'path': 'src/other', 'fingerprint': 'x', 'tests': 'src/other/test'}]
    state('SessionStart', start_payload(), workspace(packages=2, package_state=both))
    state('PreToolUse', tool_payload('Bash', command='cd src/other && python3 -m pytest test -q'), completed())
    now = [dict(both[0], fingerprint='bbbb'), dict(both[1], fingerprint='y')]
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(packages=2, package_state=now))
    assert 'demo: `cd src/demo && python3 -m pytest test -q`' in output['reason']
    assert 'other:' not in output['reason']


def test_the_record_of_an_earlier_release_still_counts(state):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    hook._log_append(hook._session_log(stop_payload(), '.tests'), ['run'])
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')),
                                              completed())
    assert output == {}


@pytest.mark.parametrize('line', ['not json', '{"kind": "pytest"}', '[1, 2]', '{"kind": 3, "args": 4}'])
def test_a_damaged_record_is_not_a_test_run(state, line):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    hook._log_append(hook._session_log(stop_payload(), '.tests'), [line])
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')))
    assert output['decision'] == 'block'


def test_test_commands_are_read_with_the_directory_a_cd_leaves():
    runs = hook.test_runs('cd src/demo && ls; cd test\npytest -q . ; cd /tmp && echo pytest', '/workspace')
    assert runs == [{'cwd': '/workspace/src/demo/test', 'kind': 'pytest', 'args': ['-q', '.']}]
    assert hook.test_runs('pytest', None) == [{'cwd': None, 'kind': 'pytest', 'args': []}]
    assert hook.test_runs('', '/workspace') == [] and hook.test_runs('timeout 30', '/workspace') == []


@pytest.mark.parametrize('at_start,at_stop', [
    (states(), states()),                                   # nothing changed
    (states(tests=None), states('bbbb', tests=None)),       # changed, no tests of its own
    ([], states('bbbb')[:0]),                               # no package
])
def test_unchanged_or_untested_packages_are_left_alone(state, at_start, at_stop):
    state('SessionStart', start_payload(), workspace(package_state=at_start))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=at_stop), completed())
    assert output == {}


def test_a_package_made_in_the_session_counts_as_changed(state):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    made = states() + [{'package': 'extra', 'path': 'src/extra', 'fingerprint': 'dddd', 'tests': 'src/extra/test'}]
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(packages=2, package_state=made))
    assert 'extra: `cd src/extra && python3 -m pytest test -q`' in output['reason']
    assert 'demo:' not in output['reason']


def test_without_the_record_from_the_start_nothing_is_claimed(state):
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')),
                                              completed())
    assert output == {}


@pytest.mark.parametrize('entry', [
    'demo', {'package': 'demo'},
    {'package': 'demo; rm -rf /', 'path': 'src/demo', 'fingerprint': 'b', 'tests': 'src/demo/test'},
    {'package': 'demo', 'path': '../outside', 'fingerprint': 'b', 'tests': 'src/demo/test'},
    {'package': 'demo', 'path': 'src/demo', 'fingerprint': 'b', 'tests': 'src/demo/test`; reboot`'},
    {'package': 'demo', 'path': 'src/demo', 'fingerprint': 'b', 'tests': '../../etc'},
    {'package': 'demo', 'path': 'src/demo', 'fingerprint': 7, 'tests': 'src/demo/test'},
])
def test_malformed_package_states_never_reach_the_model(state, entry):
    state('SessionStart', start_payload(), workspace(package_state=states()))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=[entry]), completed())
    assert output == {}


def test_only_a_few_packages_are_named(state):
    def many(fingerprint):
        return [{'package': 'p%d' % n, 'path': 'src/p%d' % n, 'fingerprint': fingerprint,
                 'tests': 'src/p%d/test' % n} for n in range(8)]
    state('SessionStart', start_payload(), workspace(packages=8, package_state=many('a')))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(packages=8, package_state=many('b')))
    assert output['reason'].count('python3 -m pytest') == 5
    assert 'and 3 more packages with tests' in output['reason']


@pytest.mark.parametrize('path,tests,expected', [
    ('src/demo', 'src/demo/test', 'cd src/demo && python3 -m pytest test -q'),
    ('demo', 'demo/tests', 'cd demo && python3 -m pytest tests -q'),
    ('src/group/demo', 'src/group/demo/test/unit', 'cd src/group/demo && python3 -m pytest test/unit -q'),
    # tests that are not below the package are named as they are, from the workspace root
    ('src/demo', 'src/demo_test', 'python3 -m pytest src/demo_test -q'),
    ('src/demo', 'src/demo', 'python3 -m pytest src/demo -q'),
    ('src/demo', 'elsewhere/test', 'python3 -m pytest elsewhere/test -q'),
])
def test_pytest_is_started_in_the_package_directory(path, tests, expected):
    assert hook.pytest_command(path, tests) == expected


def test_advise_mode_names_the_tests_to_the_user(state, monkeypatch):
    monkeypatch.setenv(hook.WORKSPACE_ENV, 'advise')
    state('SessionStart', start_payload(), workspace(package_state=states()))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace(package_state=states('bbbb')))
    assert set(output) == {'systemMessage'}
    assert 'cd src/demo && python3 -m pytest test -q` (in a built workspace' in output['systemMessage']


def test_package_of_a_finding_handed_over_now_is_named_before_it_is_changed(state):
    # The turn continues once: the tests of what is about to be fixed go into the same handover.
    state('SessionStart', start_payload(), workspace([ISSUE], package_state=states()))
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE], package_state=states()))
    assert "executable 'other'" in output['reason']
    assert 'demo: `cd src/demo && python3 -m pytest test -q`' in output['reason']


def test_changed_packages_are_named_before_those_only_about_to_change(state):
    both = states() + [{'package': 'other', 'path': 'src/other', 'fingerprint': 'x', 'tests': 'src/other/test'}]
    state('SessionStart', start_payload(), workspace([ISSUE], packages=2, package_state=both))
    now = [dict(both[0]), dict(both[1], fingerprint='y')]
    (output, _error, _status), _calls = state('Stop', stop_payload(), workspace([ISSUE], packages=2, package_state=now))
    reason = output['reason']
    assert reason.index('other: `cd src/other && python3 -m pytest') < reason.index('demo: `cd src/demo')
