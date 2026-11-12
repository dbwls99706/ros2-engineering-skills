"""The QoS event example written into generated Python nodes.

ROS is not required here: the example is extracted exactly as the container
check does it, inspected statically, and executed against stand-in modules
that mirror the rclpy layout of each distribution. The real import and node
creation run in the ROS containers through tests/check_qos_event_callbacks.py.
"""

import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import check_qos_event_callbacks as check  # noqa: E402
from create_package import create_python_package  # noqa: E402


@pytest.fixture
def package(tmp_path):
    create_python_package('qos_demo', tmp_path)
    return tmp_path / 'qos_demo'


@pytest.fixture
def example(package):
    return check.extract_example(check.node_source(package))


class _Callbacks:
    def __init__(self, *, deadline=None, liveliness=None, incompatible_qos=None,
                 use_default_callbacks=True):
        self.incompatible_qos = incompatible_qos


class _Endpoint:
    def __init__(self, event_callbacks):
        self.event_handlers = [event_callbacks] if event_callbacks is not None else []


class _Node:
    created = []

    def __init__(self, name):
        self.name = name

    def create_subscription(self, msg_type, topic, callback, qos_profile, *,
                            callback_group=None, event_callbacks=None, raw=False):
        return _Endpoint(event_callbacks)

    def create_publisher(self, msg_type, topic, qos_profile, *, callback_group=None,
                         event_callbacks=None):
        return _Endpoint(event_callbacks)

    def destroy_node(self):
        _Node.created.append(self.name)


def install_rclpy(monkeypatch, event_module):
    """Stand-in rclpy whose event classes live in *event_module* only."""
    rclpy = types.ModuleType('rclpy')
    rclpy.__path__ = []
    calls = []
    rclpy.init = lambda: calls.append('init')
    rclpy.shutdown = lambda: calls.append('shutdown')
    events = types.ModuleType(event_module)
    events.SubscriptionEventCallbacks = type('SubscriptionEventCallbacks', (_Callbacks,), {})
    events.PublisherEventCallbacks = type('PublisherEventCallbacks', (_Callbacks,), {})
    for cls in (events.SubscriptionEventCallbacks, events.PublisherEventCallbacks):
        cls.__module__ = event_module
    node = types.ModuleType('rclpy.node')
    node.Node = _Node
    interfaces = types.ModuleType('rcl_interfaces')
    interfaces.__path__ = []
    messages = types.ModuleType('rcl_interfaces.msg')
    messages.Log = type('Log', (), {})
    for name, module in (('rclpy', rclpy), (event_module, events), ('rclpy.node', node),
                         ('rcl_interfaces', interfaces), ('rcl_interfaces.msg', messages)):
        monkeypatch.setitem(sys.modules, name, module)
    for other in ('rclpy.event_handler', 'rclpy.qos_event'):
        if other != event_module:
            monkeypatch.setitem(sys.modules, other, None)  # import raises ImportError
    return calls


def test_generated_node_carries_one_marked_example(package, example):
    source = check.node_source(package)
    assert source.count(check.BEGIN) == 1 and source.count(check.END) == 1
    assert 'QoSEventHandler' not in source
    # ament_flake8 limits generated lines to 99 characters.
    assert max(len(line) for line in source.splitlines()) <= 99
    tree = ast.parse(example)
    assert isinstance(tree.body[0], ast.Try)
    # The current module name is tried first; the Humble name is the fallback.
    assert tree.body[0].body[0].module == 'rclpy.event_handler'
    assert tree.body[0].handlers[0].type.id == 'ImportError'
    assert tree.body[0].handlers[0].body[0].module == 'rclpy.qos_event'
    calls = check.example_calls(tree)
    assert set(calls) == set(check.FACTORIES)
    for call in calls.values():
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        container = keywords['event_callbacks']
        assert [k.arg for k in container.keywords] == ['incompatible_qos']
        assert ast.unparse(container.keywords[0].value) == 'self.on_qos_event'
    assert 'def on_qos_event(self, event):' in source
    assert 'event.last_policy_kind' in source


@pytest.mark.parametrize('event_module', ['rclpy.event_handler', 'rclpy.qos_event'])
def test_example_runs_against_each_rclpy_layout(monkeypatch, example, event_module):
    # Jazzy and later provide rclpy.event_handler; Humble only rclpy.qos_event.
    calls = install_rclpy(monkeypatch, event_module)
    assert check.check_static(example) == [event_module, event_module]
    check.check_runtime(example)
    assert calls == ['init', 'shutdown']
    assert _Node.created[-1] == 'qos_event_example_probe'


def test_command_line_reads_the_generated_package(monkeypatch, package, capsys):
    install_rclpy(monkeypatch, 'rclpy.event_handler')
    assert check.main([str(package)]) == 0
    assert 'containers and signatures ok' in capsys.readouterr().out
    assert check.main([str(package), '--runtime']) == 0
    assert 'created with callbacks attached' in capsys.readouterr().out


@pytest.mark.parametrize('wrong', [
    # The form the generator used to print.
    'from rclpy.event_handler import QoSEventHandler\n'
    'self.s = self.create_subscription(Log, "t", None, 10, event_callbacks=QoSEventHandler('
    'incompatible_qos_callback=self.on_qos_event))\n',
    # Right classes, wrong argument name.
    'from rclpy.event_handler import PublisherEventCallbacks, SubscriptionEventCallbacks\n'
    'SubscriptionEventCallbacks(incompatible_qos_callback=None)\n'
    'self.s = self.create_subscription(Log, "t", None, 10, qos_callbacks=None)\n'
    'self.p = self.create_publisher(Log, "t", 10, event_callbacks=None)\n',
    # No event_callbacks passed at all.
    'from rclpy.event_handler import PublisherEventCallbacks, SubscriptionEventCallbacks\n'
    'self.s = self.create_subscription(Log, "t", None, 10)\n'
    'self.p = self.create_publisher(Log, "t", 10)\n',
    # Only one endpoint.
    'from rclpy.event_handler import PublisherEventCallbacks, SubscriptionEventCallbacks\n'
    'self.p = self.create_publisher(Log, "t", 10, event_callbacks=None)\n',
])
def test_a_wrong_example_fails_the_check(monkeypatch, wrong):
    install_rclpy(monkeypatch, 'rclpy.event_handler')
    with pytest.raises((AssertionError, ImportError, KeyError)):
        check.check_static(wrong)


def test_runtime_check_requires_attached_handlers(monkeypatch):
    install_rclpy(monkeypatch, 'rclpy.event_handler')
    detached = ('from rcl_interfaces.msg import Log\n'
                'self.example_sub = self.create_subscription(Log, "t", None, 10)\n'
                'self.example_pub = self.create_publisher(Log, "t", 10)\n')
    with pytest.raises(AssertionError, match='no QoS event handler'):
        check.check_runtime(detached)


@pytest.mark.parametrize('source', [
    'no markers here\n',
    check.BEGIN + '\n' + check.END + '\n',
    check.BEGIN + '\n# a = 1\n',
    check.BEGIN + '\n# a = 1\n' + check.END + '\n' + check.BEGIN + '\n# b = 2\n' + check.END + '\n',
    check.BEGIN + '\na = 1\n' + check.END + '\n',
])
def test_extraction_rejects_a_missing_or_malformed_block(source):
    with pytest.raises(ValueError):
        check.extract_example(source)


def test_extraction_keeps_indentation_and_blank_comment_lines():
    source = '\n'.join(['    ' + check.BEGIN, '    # if True:', '    #     x = 1', '    #',
                        '    # y = 2', '    ' + check.END])
    assert check.extract_example(source) == 'if True:\n    x = 1\n\ny = 2\n'


def test_node_source_needs_exactly_one_node_module(tmp_path):
    with pytest.raises(ValueError):
        check.node_source(tmp_path)


def test_container_runner_executes_the_example_in_both_modes():
    text = (ROOT / 'tests/run_ros2_container_tests.sh').read_text(encoding='utf-8')
    static = 'python3 "$ROOT/tests/check_qos_event_callbacks.py" "$WS/src/test_py_pkg"\n'
    runtime = 'python3 "$ROOT/tests/check_qos_event_callbacks.py" "$WS/src/test_py_pkg" --runtime'
    assert static in text and runtime in text
    # Imports and signatures run on every distribution; creating endpoints
    # stays inside the block that excludes Rolling.
    gate = text.index('if [[ "$ROS_DISTRO" != rolling ]]; then\n    (cd "$WS/src/test_lifecycle_pkg"')
    assert text.index(static) < gate < text.index(runtime)
