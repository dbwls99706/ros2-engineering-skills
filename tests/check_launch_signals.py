#!/usr/bin/env python3
"""Inject the SIGINT/ready-callback race with the installed ROS signal manager.

The negative control reproduces a mechanism, not the exact CI interleaving.
The fleet gate separately requires real installed nodes and clean child exits.
"""

import asyncio
import collections
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from launch.utilities import AsyncSafeSignalManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.launch_supervisor import run_service


def cli_controls():
    """Exercise real ROS imports, arguments, inclusion, and failure exit status."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='launch-cli-control-') as temporary:
        path = Path(temporary) / 'control.launch.py'
        path.write_text('''from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration


def check(context):
    if LaunchConfiguration('fail').perform(context) == 'true':
        raise RuntimeError('application-failure-control')
    assert LaunchConfiguration('label').perform(context) == 'a b'
    print('CLI_FILE_EVALUATED', flush=True)


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('label', default_value='unset'),
        DeclareLaunchArgument('fail', default_value='false'),
        OpaqueFunction(function=check),
    ])
''', encoding='utf-8')
        cases = [(['label:=first', 'label:=a b'], True, 'CLI_FILE_EVALUATED'),
                 (['invalid'], False, 'malformed launch argument'),
                 (['fail:=true'], False, 'application-failure-control')]
        for arguments, expected_success, marker in cases:
            result = subprocess.run([sys.executable, str(root / 'scripts/launch_supervisor.py'),
                                     str(path), *arguments], capture_output=True, text=True,
                                    timeout=10.0)
            output = result.stdout + result.stderr
            print(output, flush=True)
            assert (result.returncode == 0) is expected_success, output
            assert marker in output, output
            print(json.dumps({'case': 'installed-cli-control', 'arguments': arguments,
                              'returncode': result.returncode}), flush=True)


class QueueService:
    """Use the installed signal manager and real event-loop queue/task behavior."""

    def __init__(self):
        self.observations = []
        self.loop = None
        self.consumer = None
        self.deadline = None

    def shutdown(self, force_sync=False):
        if self.loop is not None:
            self.queue.put_nowait('shutdown')

    async def run_async(self):
        self.loop = loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()

        async def consume():
            while True:
                value = await self.queue.get()
                self.observations.append(value)
                if value == 'shutdown':
                    return

        self.consumer = consumer = loop.create_task(consume())
        observations = self.observations

        class InjectReady(collections.deque):
            injected = False
            interrupted_handle = None

            def popleft(self):
                handle = super().popleft()
                if (not self.injected
                        and getattr(handle._callback, '__self__', None) is consumer
                        and consumer._fut_waiter is not None):
                    self.injected = True
                    self.interrupted_handle = handle
                    observations.append('sigint-after-ready-pop')
                    os.kill(os.getpid(), signal.SIGINT)
                return handle

        loop._ready = InjectReady(loop._ready)
        loop.call_soon(self.queue.put_nowait, 'seed')
        self.deadline = loop.call_later(1.0, loop.stop)

        def on_sigint(signum):
            observations.append('ros-signal-callback')
            self.shutdown(force_sync=True)

        try:
            with AsyncSafeSignalManager(loop) as manager:
                manager.handle(signal.SIGINT, on_sigint)
                await consumer
        finally:
            self.deadline.cancel()
        return 0


def native_control():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    service = QueueService()
    task = loop.create_task(service.run_async())
    try:
        while True:
            try:
                loop.run_until_complete(task)
                break
            except KeyboardInterrupt:
                # Match LaunchService.run's catch-and-resume behavior.
                service.observations.append('caught-keyboard-interrupt')
            except RuntimeError as exc:
                if str(exc) != 'Event loop stopped before Future completed.':
                    raise
                break
        record = {'case': 'raising-handler-control', 'consumer_done': service.consumer.done(),
                  'observations': list(service.observations)}
        print(json.dumps(record), flush=True)
        assert loop._ready.injected
        assert not record['consumer_done'], record
        assert record['observations'].count('ros-signal-callback') == 1
        assert 'shutdown' not in record['observations']
    finally:
        # Repair the deliberately lost handle only AFTER recording the failed
        # control. This lets the isolated probe clean up without a leaked task.
        signal.signal(signal.SIGINT, previous)
        if not task.done() and loop._ready.interrupted_handle is not None:
            loop.call_soon(loop._ready.interrupted_handle._run)
            loop.run_until_complete(task)
        loop.close()
        asyncio.set_event_loop(None)


def supervised_control():
    previous = signal.getsignal(signal.SIGINT)
    service = QueueService()
    assert run_service(service) == 0
    record = {'case': 'supervised-signal-control', 'consumer_done': service.consumer.done(),
              'observations': service.observations}
    print(json.dumps(record), flush=True)
    assert record['consumer_done'], record
    assert service.loop._ready.injected
    assert record['observations'].count('ros-signal-callback') == 1
    assert record['observations'].count('sigint-after-ready-pop') == 1
    assert 'shutdown' in record['observations']
    assert signal.getsignal(signal.SIGINT) is previous
    assert service.loop.is_closed()


if __name__ == '__main__':
    cli_controls()
    native_control()
    supervised_control()
