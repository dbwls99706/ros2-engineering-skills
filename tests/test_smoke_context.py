"""Context ownership regressions; actual DDS discovery stays in the ROS jobs."""

from pathlib import Path
import signal
import sys
from types import ModuleType

import pytest

from tests import smoke_test_nodes as smoke


@pytest.fixture
def isolated_ros(monkeypatch, tmp_path):
    events = []
    contexts = []
    settings = {'shutdown': True, 'failure': None}

    class Context:
        def __init__(self):
            self.active = False
            contexts.append(self)

        def ok(self):
            return self.active

        def shutdown(self):
            events.append('context-stop')
            self.active = False

    def init(*, context):
        context.active = True
        events.append('init')

    class Node:
        def __init__(self, name, *, context):
            assert context is contexts[0] and context.ok()
            if settings['failure'] == 'node':
                raise RuntimeError('node construction failed')
            self.context = context
            events.append('node')

        def get_node_names(self):
            return ['observed']

        def destroy_node(self):
            events.append('node-stop')

    class Executor:
        def __init__(self, *, context):
            assert context is contexts[0] and context.ok()
            if settings['failure'] == 'executor':
                raise RuntimeError('executor construction failed')
            self.context = context
            self.node = None
            events.append('executor')

        def add_node(self, node):
            assert node.context is self.context
            self.node = node
            events.append('add-node')

        def spin_once(self, *, timeout_sec):
            assert self.node is not None and self.context.ok()
            assert timeout_sec == 0.1
            events.append('spin')

        def shutdown(self, *, timeout_sec):
            assert timeout_sec == 2.0
            assert self.context.ok()
            events.append('executor-stop')
            return settings['shutdown']

    modules = {name: ModuleType(name) for name in
               ('rclpy', 'rclpy.context', 'rclpy.node', 'rclpy.executors')}
    modules['rclpy'].init = init
    modules['rclpy.context'].Context = Context
    modules['rclpy.node'].Node = Node
    modules['rclpy.executors'].SingleThreadedExecutor = Executor

    def global_spin(*args, **kwargs):
        raise AssertionError('The global executor must never be used with the private context')

    modules['rclpy'].spin_once = global_spin
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def probe(command, name, discover, *, require_clean_exit):
        assert require_clean_exit is True
        assert Path(command[0]).is_file()
        assert command[-1] == '__node:=' + name
        assert discover() == {'observed'}
        events.append('probe')

    monkeypatch.setattr(smoke, 'probe', probe)
    install = tmp_path / 'install'
    for package in ('test_cpp_pkg', 'test_py_pkg'):
        path = install / package / 'lib' / package / (package + '_node')
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield install, events, settings
    assert all(signal.getsignal(sig) == handler for sig, handler in handlers.items())
    assert not contexts[0].ok()


def test_observer_and_executor_share_the_initialized_context(isolated_ros):
    install, events, _ = isolated_ros
    assert smoke.main([str(install)]) == 0
    assert events == ['init', 'node', 'executor', 'add-node', 'spin', 'probe',
                      'spin', 'probe', 'executor-stop', 'node-stop', 'context-stop']


@pytest.mark.parametrize('failure,expected', [
    (RuntimeError('discovery failed'), 1),
    (smoke.SmokeInterrupted(signal.SIGTERM), 128 + signal.SIGTERM),
])
def test_probe_failure_and_interruption_release_ros_resources(isolated_ros, monkeypatch, failure, expected):
    install, events, _ = isolated_ros

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(smoke, 'probe', fail)
    assert smoke.main([str(install)]) == expected
    assert events[-3:] == ['executor-stop', 'node-stop', 'context-stop']


def test_missing_executable_still_releases_initialized_resources(isolated_ros):
    install, events, _ = isolated_ros
    next(install.rglob('test_cpp_pkg_node')).unlink()
    assert smoke.main([str(install)]) == 1
    assert 'probe' not in events
    assert events[-3:] == ['executor-stop', 'node-stop', 'context-stop']


@pytest.mark.parametrize('phase,tail', [
    ('node', ['context-stop']), ('executor', ['node-stop', 'context-stop']),
])
def test_partial_initialization_is_cleaned_up(isolated_ros, phase, tail):
    install, events, settings = isolated_ros
    settings['failure'] = phase
    assert smoke.main([str(install)]) == 1
    assert events[-len(tail):] == tail


def test_executor_shutdown_failure_cannot_skip_node_context_or_signal_cleanup(isolated_ros):
    install, events, settings = isolated_ros
    settings['shutdown'] = False
    with pytest.raises(RuntimeError, match='shut down'):
        smoke.main([str(install)])
    assert events[-3:] == ['executor-stop', 'node-stop', 'context-stop']
