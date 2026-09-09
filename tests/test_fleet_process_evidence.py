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

    times = iter([10.0, 13.0])
    monkeypatch.setattr(fleet.time, 'monotonic', lambda: next(times))

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
        assert process.waits == [3.0, 7.0]
        assert process.signals == [signal.SIGUSR1]
    else:
        fleet.wait_for_launch_exit(process)
        assert process.waits == [3.0]
        assert process.signals == []
