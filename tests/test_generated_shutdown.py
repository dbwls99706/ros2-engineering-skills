"""Execute generated entry points against explicit context-lifecycle test doubles.

These regressions do not claim real DDS behavior. The distro smoke tests and
the generated-fleet gate also require the installed generated processes to
return zero after a real SIGINT.

Generated Python nodes own SIGINT/SIGTERM: rclpy is initialised with
``SignalHandlerOptions.NO``, the handler only records the request, and the main
thread leaves the spin loop and cleans up once, in order. rclpy's default
handler instead shuts the context down from a native signal thread while the
main thread, woken by KeyboardInterrupt, is already destroying the node and
executor; that race crashed a generated node with SIGSEGV on Humble.
"""

import os
from pathlib import Path
import runpy
import signal
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.create_package import create_python_package


@pytest.fixture(params=[False, True], ids=['plain', 'lifecycle'])
def entrypoint(request, monkeypatch, tmp_path):
    events = []
    state = {'active': False, 'spin_failure': None, 'signal_during_spin': None,
             'external_stop': False, 'construct_failure': None,
             'destroy_failure': None, 'init_failure': None, 'init_kwargs': None,
             'spins': 0}

    class ExternalShutdownException(Exception):
        pass

    class Node:
        def __init__(self, name, **kwargs):
            events.append('construct')
            if state['construct_failure']:
                raise state['construct_failure']

        def declare_parameter(self, name, value):
            pass

        def get_parameter(self, name):
            return SimpleNamespace(value=50.0)

        def create_timer(self, period, callback):
            return object()

        def get_logger(self):
            return SimpleNamespace(info=lambda message: None)

        def destroy_node(self):
            events.append('destroy')
            if state['destroy_failure']:
                raise state['destroy_failure']

    class SingleThreadedExecutor:
        def add_node(self, node):
            events.append('add')

        def remove_node(self, node):
            events.append('remove')

        def shutdown(self):
            events.append('executor-shutdown')

        def spin_once(self, timeout_sec=None):
            assert timeout_sec is not None and timeout_sec > 0
            state['spins'] += 1
            if state['spins'] > 50:
                raise AssertionError('spin loop never observed the stop request')
            events.append('spin') if events[-1] != 'spin' else None
            if state['signal_during_spin'] is not None:
                # A real signal delivered to this process while it spins.
                os.kill(os.getpid(), state['signal_during_spin'])
            if state['external_stop']:
                state['active'] = False
            if state['spin_failure']:
                raise state['spin_failure']

    def init(*, args, **kwargs):
        events.append('init')
        state['init_kwargs'] = kwargs
        if state['init_failure']:
            raise state['init_failure']
        state['active'] = True

    def ok():
        return state['active']

    def try_shutdown():
        state['active'] = False
        events.append('try-shutdown')

    names = ('rclpy', 'rclpy.node', 'rclpy.lifecycle', 'rclpy.executors', 'rclpy.signals')
    modules = {name: ModuleType(name) for name in names}
    modules['rclpy'].init = init
    modules['rclpy'].ok = ok
    modules['rclpy'].try_shutdown = try_shutdown
    modules['rclpy.node'].Node = Node
    modules['rclpy.lifecycle'].LifecycleNode = Node
    modules['rclpy.lifecycle'].LifecycleState = object
    modules['rclpy.lifecycle'].TransitionCallbackReturn = SimpleNamespace(SUCCESS=0)
    modules['rclpy.executors'].ExternalShutdownException = ExternalShutdownException
    modules['rclpy.executors'].SingleThreadedExecutor = SingleThreadedExecutor
    modules['rclpy.signals'].SignalHandlerOptions = SimpleNamespace(NO='no-handlers')
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    create_python_package('shutdown_probe', tmp_path, lifecycle=request.param)
    source = Path(tmp_path) / 'shutdown_probe/shutdown_probe/shutdown_probe_node.py'

    def load(foxy=False):
        if foxy:
            monkeypatch.delitem(sys.modules, 'rclpy.signals')
            monkeypatch.setitem(sys.modules, 'rclpy.signals', None)  # ImportError
        return runpy.run_path(str(source))['main']

    original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield load, events, state, ExternalShutdownException
    for sig, handler in original.items():
        signal.signal(sig, handler)


ORDERLY = ['init', 'construct', 'add', 'spin', 'remove', 'executor-shutdown',
           'destroy', 'try-shutdown']


def _handlers():
    return {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_a_real_signal_stops_the_spin_and_cleans_up_once_in_order(entrypoint, signum):
    load, events, state, _ = entrypoint
    main = load()
    before = _handlers()
    state['signal_during_spin'] = signum
    assert main([]) is None  # no KeyboardInterrupt escapes; exit status 0
    assert events == ORDERLY
    assert state['init_kwargs'] == {'signal_handler_options': 'no-handlers'}
    assert not state['active']
    assert _handlers() == before  # the process's own handlers are restored


def test_foxy_without_signal_handler_options_still_owns_the_signals(entrypoint):
    load, events, state, _ = entrypoint
    main = load(foxy=True)
    state['signal_during_spin'] = signal.SIGINT
    assert main([]) is None
    assert events == ORDERLY
    assert state['init_kwargs'] == {}


def test_external_shutdown_ends_the_loop_cleanly(entrypoint):
    load, events, state, _ = entrypoint
    state['external_stop'] = True
    assert load()([]) is None
    assert events == ORDERLY


@pytest.mark.parametrize('kind', ['external', 'interrupt'])
def test_expected_stops_raised_by_spin_are_clean(entrypoint, kind):
    load, events, state, external_shutdown = entrypoint
    state['spin_failure'] = external_shutdown() if kind == 'external' else KeyboardInterrupt()
    assert load()([]) is None
    assert events == ORDERLY
    assert not state['active']


def test_real_spin_error_is_not_swallowed(entrypoint):
    load, events, state, _ = entrypoint
    error = RuntimeError('callback defect')
    state['spin_failure'] = error
    before = _handlers()
    with pytest.raises(RuntimeError) as caught:
        load()([])
    assert caught.value is error
    assert events == ORDERLY
    assert not state['active']
    assert _handlers() == before


def test_constructor_failure_still_closes_initialized_context(entrypoint):
    load, events, state, _ = entrypoint
    error = RuntimeError('construction defect')
    state['construct_failure'] = error
    before = _handlers()
    with pytest.raises(RuntimeError) as caught:
        load()([])
    assert caught.value is error
    assert events == ['init', 'construct', 'try-shutdown']
    assert not state['active']
    assert _handlers() == before


def test_destroy_failure_cannot_skip_context_cleanup(entrypoint):
    load, events, state, _ = entrypoint
    error = RuntimeError('destruction defect')
    state.update(destroy_failure=error, external_stop=True)
    with pytest.raises(RuntimeError) as caught:
        load()([])
    assert caught.value is error
    assert events[-2:] == ['destroy', 'try-shutdown']
    assert not state['active']


def test_failed_init_is_not_reported_as_success_or_shutdown_again(entrypoint):
    load, events, state, _ = entrypoint
    error = RuntimeError('initialization defect')
    state['init_failure'] = error
    before = _handlers()
    with pytest.raises(RuntimeError) as caught:
        load()([])
    assert caught.value is error
    assert events == ['init']
    assert _handlers() == before  # nothing installed before init succeeded
