"""Execute generated entry points against explicit context-lifecycle test doubles.

These regressions do not claim real DDS behavior. The distro smoke tests also
require the installed generated processes to return zero after a real SIGINT.
"""

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.create_package import create_python_package


@pytest.fixture(params=[False, True], ids=['plain', 'lifecycle'])
def entrypoint(request, monkeypatch, tmp_path):
    events = []
    state = {'active': False, 'spin_failure': None, 'stop_during_spin': False,
             'construct_failure': None, 'destroy_failure': None, 'init_failure': None}

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

    def init(*, args):
        events.append('init')
        if state['init_failure']:
            raise state['init_failure']
        state['active'] = True

    def spin(node):
        events.append('spin')
        if state['stop_during_spin']:
            # This reproduces signal handling invalidating the native context.
            state['active'] = False
        if state['spin_failure']:
            raise state['spin_failure']

    def shutdown():
        if not state['active']:
            raise RuntimeError('rcl_shutdown already called on the given context')
        state['active'] = False
        events.append('shutdown')

    def try_shutdown():
        state['active'] = False
        events.append('try-shutdown')

    modules = {name: ModuleType(name) for name in
               ('rclpy', 'rclpy.node', 'rclpy.lifecycle', 'rclpy.executors')}
    modules['rclpy'].init = init
    modules['rclpy'].spin = spin
    modules['rclpy'].shutdown = shutdown
    modules['rclpy'].try_shutdown = try_shutdown
    modules['rclpy.node'].Node = Node
    modules['rclpy.lifecycle'].LifecycleNode = Node
    modules['rclpy.lifecycle'].LifecycleState = object
    modules['rclpy.lifecycle'].TransitionCallbackReturn = SimpleNamespace(SUCCESS=0)
    modules['rclpy.executors'].ExternalShutdownException = ExternalShutdownException
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    create_python_package('shutdown_probe', tmp_path, lifecycle=request.param)
    source = Path(tmp_path) / 'shutdown_probe/shutdown_probe/shutdown_probe_node.py'
    namespace = runpy.run_path(str(source))
    return namespace['main'], events, state, ExternalShutdownException


@pytest.mark.parametrize('already_stopped', [False, True])
def test_normal_return_and_signal_shutdown_cleanup(entrypoint, already_stopped):
    main, events, state, _ = entrypoint
    state['stop_during_spin'] = already_stopped
    assert main([]) is None
    assert events == ['init', 'construct', 'spin', 'destroy', 'try-shutdown']
    assert not state['active']


@pytest.mark.parametrize('already_stopped', [False, True])
def test_keyboard_interrupt_is_clean_with_either_context_state(entrypoint, already_stopped):
    main, events, state, _ = entrypoint
    state.update(spin_failure=KeyboardInterrupt(), stop_during_spin=already_stopped)
    assert main([]) is None
    assert events[-2:] == ['destroy', 'try-shutdown']
    assert not state['active']


def test_external_shutdown_exception_is_an_expected_stop(entrypoint):
    main, events, state, external_shutdown = entrypoint
    state.update(spin_failure=external_shutdown(), stop_during_spin=True)
    assert main([]) is None
    assert events[-2:] == ['destroy', 'try-shutdown']
    assert not state['active']


def test_real_spin_error_is_not_swallowed(entrypoint):
    main, events, state, _ = entrypoint
    error = RuntimeError('callback defect')
    state['spin_failure'] = error
    with pytest.raises(RuntimeError) as caught:
        main([])
    assert caught.value is error
    assert events[-2:] == ['destroy', 'try-shutdown']
    assert not state['active']


def test_constructor_failure_still_closes_initialized_context(entrypoint):
    main, events, state, _ = entrypoint
    error = RuntimeError('construction defect')
    state['construct_failure'] = error
    with pytest.raises(RuntimeError) as caught:
        main([])
    assert caught.value is error
    assert events == ['init', 'construct', 'try-shutdown']
    assert not state['active']


def test_destroy_failure_cannot_skip_context_cleanup(entrypoint):
    main, events, state, _ = entrypoint
    error = RuntimeError('destruction defect')
    state['destroy_failure'] = error
    with pytest.raises(RuntimeError) as caught:
        main([])
    assert caught.value is error
    assert events[-2:] == ['destroy', 'try-shutdown']
    assert not state['active']


def test_failed_init_is_not_reported_as_success_or_shutdown_again(entrypoint):
    main, events, state, _ = entrypoint
    error = RuntimeError('initialization defect')
    state['init_failure'] = error
    with pytest.raises(RuntimeError) as caught:
        main([])
    assert caught.value is error
    assert events == ['init']
