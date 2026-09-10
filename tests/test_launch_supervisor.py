"""Real POSIX signals/asyncio with a service double; ROS gates use real launch."""

import asyncio
import collections
import os
import signal
import sys
import threading
from types import SimpleNamespace

import pytest

from scripts.launch_supervisor import main, run_service


pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX supervisor')


@pytest.mark.parametrize('during_cleanup', [False, True])
def test_sigint_cannot_discard_a_dequeued_callback(during_cleanup):
    events = []
    loops = []
    prior = signal.getsignal(signal.SIGINT)

    class InjectReady(collections.deque):
        def popleft(self):
            handle = super().popleft()
            if handle._callback is selected_callback:
                events.append('signal')
                os.kill(os.getpid(), signal.SIGINT)
            return handle

    def selected_callback():
        events.append('callback')

    class Service:
        async def run_async(self):
            loop = asyncio.get_running_loop()
            loops.append(loop)
            loop._ready = InjectReady(loop._ready)
            self.stopped = loop.create_future()
            if during_cleanup:
                async def background():
                    try:
                        await asyncio.Future()
                    finally:
                        loop.call_soon(selected_callback)
                        await asyncio.sleep(0)
                        events.append('cleanup')

                loop.create_task(background())
                await asyncio.sleep(0)
            else:
                loop.call_soon(selected_callback)
                await asyncio.wait_for(self.stopped, timeout=2.0)
            return 0

        def shutdown(self, force_sync=False):
            assert force_sync
            events.append('shutdown')
            if not self.stopped.done():
                self.stopped.set_result(None)

    assert run_service(Service()) == 0
    assert events[:2] == ['signal', 'callback']
    assert events.count('shutdown') == 1
    if during_cleanup:
        assert 'cleanup' in events
    assert signal.getsignal(signal.SIGINT) is prior
    assert loops[0].is_closed()


@pytest.mark.parametrize('failure', [None, RuntimeError('launch failed')])
def test_return_status_failure_and_resource_ownership(failure):
    prior = signal.getsignal(signal.SIGINT)
    loops = []

    class Service:
        async def run_async(self):
            loop = asyncio.get_running_loop()
            loops.append(loop)
            await loop.run_in_executor(None, lambda: 42)
            if failure:
                raise failure
            return 7

    if failure:
        with pytest.raises(RuntimeError) as caught:
            run_service(Service())
        assert caught.value is failure
    else:
        assert run_service(Service()) == 7
    assert signal.getsignal(signal.SIGINT) is prior
    assert loops[0].is_closed()


def test_rejects_an_existing_running_loop():
    async def nested():
        with pytest.raises(RuntimeError, match='own event loop'):
            run_service(object())
    asyncio.run(nested())


def test_rejects_a_background_thread():
    errors = []

    def invoke():
        try:
            run_service(object())
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=invoke)
    thread.start()
    thread.join(timeout=2.0)
    assert errors == ['The launch supervisor must run in the main thread']


def test_prestart_stop_does_not_start_the_service(monkeypatch):
    loop = asyncio.new_event_loop()
    install = loop.add_signal_handler
    events = []

    def install_with_pending_stop(signum, callback):
        install(signum, callback)
        loop.call_soon(callback)

    class Service:
        async def run_async(self):
            events.append('started')
            return 0

        def shutdown(self, force_sync=False):
            events.append('shutdown')

    monkeypatch.setattr(asyncio, 'new_event_loop', lambda: loop)
    monkeypatch.setattr(loop, 'add_signal_handler', install_with_pending_stop)
    assert run_service(Service()) == 130
    assert events == ['shutdown']
    assert loop.is_closed()


def test_preserves_an_existing_idle_loop():
    previous_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(previous_loop)

    class Service:
        async def run_async(self):
            assert asyncio.get_running_loop() is not previous_loop
            return 0

    try:
        assert run_service(Service()) == 0
        assert asyncio.get_event_loop() is previous_loop
        assert not previous_loop.is_closed()
    finally:
        previous_loop.close()
        asyncio.set_event_loop(None)


def test_handler_setup_failure_still_closes_owned_loop(monkeypatch):
    loop = asyncio.new_event_loop()
    prior = signal.getsignal(signal.SIGINT)

    def fail(*args):
        raise NotImplementedError('unsupported signal backend')

    monkeypatch.setattr(asyncio, 'new_event_loop', lambda: loop)
    monkeypatch.setattr(loop, 'add_signal_handler', fail)
    with pytest.raises(NotImplementedError, match='unsupported'):
        run_service(object())
    assert loop.is_closed()
    assert signal.getsignal(signal.SIGINT) is prior


@pytest.mark.parametrize('argument', ['--help', '--version', '/missing/launch.py'])
def test_cli_preflight_does_not_import_ros(argument):
    with pytest.raises(SystemExit) as caught:
        main([argument])
    assert caught.value.code == (2 if argument.startswith('/missing') else 0)


def test_cli_includes_the_real_file_and_passes_arguments(tmp_path, monkeypatch):
    from scripts import launch_supervisor as supervisor

    path = tmp_path / 'bringup.launch.py'
    path.write_text('# test double only\n', encoding='utf-8')
    captures = {}

    class Service:
        def __init__(self, **kwargs):
            captures['options'] = kwargs

        def include_launch_description(self, value):
            captures['description'] = value

    monkeypatch.setitem(sys.modules, 'launch', SimpleNamespace(
        LaunchDescription=list, LaunchService=Service))
    monkeypatch.setitem(sys.modules, 'launch.actions', SimpleNamespace(
        IncludeLaunchDescription=lambda source, **kw: (source, kw)))
    monkeypatch.setitem(sys.modules, 'launch.launch_description_sources', SimpleNamespace(
        AnyLaunchDescriptionSource=lambda value: value))
    monkeypatch.setitem(sys.modules, 'ros2launch.api', SimpleNamespace(
        parse_launch_arguments=lambda values: [value.split(':=', 1) for value in values]))
    monkeypatch.setattr(supervisor, 'run_service', lambda service: 7)
    assert main([str(path), 'rate:=17.0', 'label:=a b']) == 7
    assert captures['options'] == {
        'argv': ['rate:=17.0', 'label:=a b'], 'noninteractive': True, 'debug': False}
    assert captures['description'] == [(str(path.resolve()), {
        'launch_arguments': [['rate', '17.0'], ['label', 'a b']]})]
