"""Real subprocess lifecycle tests, with synthetic children rather than ROS nodes."""

import os
from pathlib import Path
import signal
import sys
import time

import pytest

from tests.smoke_test_nodes import SmokeInterrupted, probe

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')


def child(tmp_path, ignore=False):
    pidfile = tmp_path / 'pid.txt'
    code = ('import os,signal,time\n'
            + ('signal.signal(signal.SIGINT, signal.SIG_IGN)\n'
               'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' if ignore else '')
            + f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\n'
            + 'while True: time.sleep(0.05)\n')
    return [sys.executable, '-S', '-c', code], pidfile


def assert_reaped(pidfile):
    if pidfile.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)


def test_success_owns_and_reaps_the_actual_process(tmp_path):
    command, pidfile = child(tmp_path)
    probe(command, 'unique_node', lambda: {'unique_node'} if pidfile.exists() else set(),
          startup_timeout=3, stop_grace=0.2)
    assert pidfile.exists()
    assert_reaped(pidfile)


def test_wrong_graph_identity_cannot_pass(tmp_path):
    command, pidfile = child(tmp_path)
    with pytest.raises(RuntimeError, match='deadline'):
        probe(command, 'unique_node', lambda: {'unique_node_old'}, startup_timeout=0.4, stop_grace=0.2)
    assert_reaped(pidfile)


def test_early_exit_is_failure_even_when_graph_reports_name(monkeypatch):
    from tests import smoke_test_nodes as smoke
    popen = smoke.subprocess.Popen
    children = []

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        children.append(process)
        return process

    def stale_graph():
        children[0].wait(timeout=3)
        return {'stale'}

    monkeypatch.setattr(smoke.subprocess, 'Popen', start)
    with pytest.raises(RuntimeError, match='exited'):
        probe([sys.executable, '-S', '-c', 'raise SystemExit(7)'], 'stale', stale_graph,
              startup_timeout=3, stop_grace=0.2)


@pytest.mark.parametrize('failure', [RuntimeError('observer failed'), SmokeInterrupted(signal.SIGTERM)])
def test_observer_failure_and_cancellation_cleanup_owned_process(tmp_path, failure):
    command, pidfile = child(tmp_path)

    def fail():
        if pidfile.exists():
            raise failure
        return set()
    with pytest.raises(type(failure)):
        probe(command, 'test', fail, startup_timeout=3, stop_grace=0.2)
    assert pidfile.exists()
    assert_reaped(pidfile)


def test_uncooperative_child_is_killed_with_bounded_escalation(tmp_path):
    command, pidfile = child(tmp_path, ignore=True)
    started = time.monotonic()
    probe(command, 'test', lambda: {'test'} if pidfile.exists() else set(),
          startup_timeout=3, stop_grace=0.15)
    assert time.monotonic() - started < 4
    assert_reaped(pidfile)


def test_exited_parent_does_not_leave_running_grandchild(tmp_path):
    pidfile = tmp_path / 'grandchild.txt'
    code = f'''import subprocess,sys
p = subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(30)'])
open({str(pidfile)!r}, 'w').write(str(p.pid))
'''
    with pytest.raises(RuntimeError, match='exited'):
        probe([sys.executable, '-S', '-c', code], 'absent', lambda: set(),
              startup_timeout=3, stop_grace=0.15)
    assert pidfile.exists()
    proc = Path('/proc') / pidfile.read_text().strip() / 'stat'
    if proc.exists():
        # A container init reaps the orphan. Without one, a zombie is dead but
        # may still have a /proc entry; it must not be confused with a live child.
        assert proc.read_text().rsplit(')', 1)[1].split()[0] == 'Z'


def test_launch_failure_propagates_without_success():
    with pytest.raises(OSError):
        probe(['/nonexistent/skill-smoke-executable'], 'missing', lambda: {'missing'})


@pytest.mark.parametrize('startup,grace', [(0, 1), (1, 0), (-1, 1)])
def test_invalid_deadlines_rejected(startup, grace):
    with pytest.raises(ValueError, match='positive'):
        probe([], 'test', lambda: set(), startup_timeout=startup, stop_grace=grace)
