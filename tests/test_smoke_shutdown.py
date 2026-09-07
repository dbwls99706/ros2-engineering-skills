"""Real subprocess checks of the generated-program shutdown requirement."""

import os
import sys

import pytest

from tests.smoke_test_nodes import probe

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')


@pytest.mark.parametrize('exit_code', [0, 7])
def test_graph_success_also_requires_a_clean_shutdown(tmp_path, exit_code):
    ready = tmp_path / 'ready'
    code = f'''import signal,sys,time
from pathlib import Path
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit({exit_code}))
Path({str(ready)!r}).write_text('ready')
while True: time.sleep(0.01)
'''
    command = [sys.executable, '-S', '-c', code]

    def run():
        probe(command, 'live', lambda: {'live'} if ready.exists() else set(),
              startup_timeout=3, stop_grace=0.3, require_clean_exit=True)

    if exit_code:
        with pytest.raises(RuntimeError, match='did not shut down cleanly: 7'):
            run()
    else:
        run()
    assert ready.exists()


def test_forced_termination_is_not_a_clean_shutdown(tmp_path):
    ready = tmp_path / 'ready'
    code = f'''import signal,time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(ready)!r}).write_text('ready')
while True: time.sleep(0.01)
'''
    with pytest.raises(RuntimeError, match='did not shut down cleanly'):
        probe([sys.executable, '-S', '-c', code], 'live',
              lambda: {'live'} if ready.exists() else set(),
              startup_timeout=3, stop_grace=0.1, require_clean_exit=True)


def test_discovery_failure_is_preserved_when_process_also_exits_unsuccessfully():
    with pytest.raises(RuntimeError, match='not discovered before the deadline'):
        probe([sys.executable, '-S', '-c', 'import time; time.sleep(30)'],
              'missing', lambda: set(), startup_timeout=0.15, stop_grace=0.1,
              require_clean_exit=True)
