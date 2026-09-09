"""Synthetic event validation only; live fleet execution is a separate ROS gate."""

import pytest

from tests.check_generated_fleet import validate_child_exits


def events():
    return [{'event': 'start', 'pid': 11}, {'event': 'start', 'pid': 12},
            {'event': 'exit', 'pid': 12, 'returncode': 0},
            {'event': 'exit', 'pid': 11, 'returncode': 0}]


def test_complete_child_exits_are_required():
    assert validate_child_exits(events()) == [
        {'pid': 11, 'returncode': 0}, {'pid': 12, 'returncode': 0}]


@pytest.mark.parametrize('code', [-2, -9, 1, None, False, '0'])
def test_zero_supervisor_exit_cannot_hide_bad_children(code):
    rows = events()
    rows[-1]['returncode'] = code
    with pytest.raises(RuntimeError, match='did not exit cleanly'):
        validate_child_exits(rows)


@pytest.mark.parametrize('rows', [[], events()[:-1], events() + [events()[-1]],
                                  [events()[-1]], [{'event': 'start', 'pid': True}],
                                  [{'event': 'other', 'pid': 11}], [None]])
def test_incomplete_or_ambiguous_evidence_fails(rows):
    with pytest.raises(RuntimeError):
        validate_child_exits(rows)


@pytest.mark.parametrize('stalled', [False, True])
def test_shutdown_diagnostics_do_not_extend_deadline_or_hide_failure(monkeypatch, stalled):
    import signal
    import subprocess
    from tests import check_generated_fleet as fleet

    sleeps = []
    monkeypatch.setattr(fleet.time, 'sleep', sleeps.append)

    class Process:
        pid = 123

        def __init__(self):
            self.waits = []
            self.signals = []

        def wait(self, timeout):
            self.waits.append(timeout)
            if stalled:
                raise subprocess.TimeoutExpired('synthetic supervisor', timeout)
            return 0

        def send_signal(self, value):
            self.signals.append(value)

    process = Process()
    if stalled:
        # SIGUSR1 is a Linux ROS test diagnostic, not a hardware command.
        monkeypatch.setattr(signal, 'SIGUSR1', 10, raising=False)
        with pytest.raises(subprocess.TimeoutExpired):
            fleet.wait_for_launch_exit(process)
        assert process.waits == [10.0]
        assert sleeps == [0.1]
        assert process.signals == [signal.SIGUSR1]
    else:
        fleet.wait_for_launch_exit(process)
        assert process.waits == [10.0]
        assert sleeps == []
        assert process.signals == []


def test_startup_barrier_requires_complete_distinct_records(tmp_path):
    from tests.check_generated_fleet import startup_complete_count

    path = tmp_path / 'startup.jsonl'
    assert startup_complete_count(path) == 0
    first = '{"event":"startup_complete","action":11}\n'
    second = '{"event":"startup_complete","action":12}'
    path.write_text(first + second, encoding='utf-8')
    assert startup_complete_count(path) == 1
    path.write_text(first + second + '\n', encoding='utf-8')
    assert startup_complete_count(path) == 2


@pytest.mark.parametrize('text', [
    '{"event":"startup_complete","action":11}\n' * 2,
    '{"event":"startup_complete","action":true}\n',
    '{"event":"startup_complete","action":0}\n',
    '{"event":"cancelled","action":11}\n',
    'null\n',
])
def test_invalid_startup_barrier_cannot_be_read_as_ready(tmp_path, text):
    from tests.check_generated_fleet import startup_complete_count

    path = tmp_path / 'startup.jsonl'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(RuntimeError):
        startup_complete_count(path)


@pytest.mark.parametrize('state', ['success', 'pending', 'cancelled', 'failed', 'missing'])
def test_wrapper_only_records_successful_startup_completion(tmp_path, monkeypatch, state):
    import ast
    import json
    import sys
    from types import SimpleNamespace
    from tests import check_generated_fleet as fleet

    class InterceptLaunch(Exception):
        pass

    def stop_before_execution(*args, **kwargs):
        raise InterceptLaunch()

    monkeypatch.setitem(sys.modules, 'smoke_test_nodes', SimpleNamespace(terminate_group=lambda _: None))
    monkeypatch.setattr(fleet.subprocess, 'Popen', stop_before_execution)
    with pytest.raises(InterceptLaunch):
        fleet.run_launch(tmp_path, 'fixture', lambda: True, expected_startups=2)
    wrapper = (tmp_path / 'fixture-observer.launch.py').read_text(encoding='utf-8')
    tree = ast.parse(wrapper)
    compile(tree, '<observer-wrapper>', 'exec')
    handler = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'record_startup')
    namespace = {'json': json}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<startup-observer>', 'exec'), namespace)
    error = RuntimeError('actual startup failure')
    future = SimpleNamespace(done=lambda: state != 'pending',
                             cancelled=lambda: state == 'cancelled',
                             exception=lambda: error if state == 'failed' else None)
    action = SimpleNamespace(get_asyncio_future=lambda: None if state == 'missing' else future)
    event = SimpleNamespace(action=action)
    if state == 'success':
        namespace['record_startup'](event, None)
        assert fleet.startup_complete_count(tmp_path / 'fixture-startup.jsonl') == 1
    else:
        with pytest.raises(RuntimeError):
            namespace['record_startup'](event, None)
        assert not (tmp_path / 'fixture-startup.jsonl').exists()
