"""State-polling logic regressions; real DDS event-loss trials run in ROS CI."""

import asyncio
from types import SimpleNamespace

import pytest

from scripts.create_package import _LIFECYCLE_ACTIVATION


def run_case(states=(1, 2, 2, 3), *, ready=True, pending=False, shutdown_after=None,
             cancel_on_sleep=False):
    clock = [0.0]
    events, destroyed, futures = [], [], []
    context = SimpleNamespace(is_shutdown=False, emit_event_sync=events.append)

    async def sleep(delay):
        clock[0] += delay
        if shutdown_after is not None and clock[0] >= shutdown_after:
            context.is_shutdown = True
        if cancel_on_sleep:
            raise asyncio.CancelledError()
        await asyncio.sleep(0)

    class Future:
        cancelled = False

        def done(self):
            return not pending

        def result(self):
            value = states[min(len(futures) - 1, len(states) - 1)]
            if isinstance(value, Exception):
                raise value
            if value is None:
                return None
            return SimpleNamespace(current_state=SimpleNamespace(id=value, label=str(value)))

        def cancel(self):
            self.cancelled = True

    class Client:
        def service_is_ready(self):
            return ready

        def call_async(self, request):
            future = Future()
            futures.append(future)
            return future

    client = Client()
    bindings = []

    def create_client(kind, path):
        bindings.append(path)
        return client

    ros_node = SimpleNamespace(create_client=create_client, destroy_client=destroyed.append)
    namespace = {
        'asyncio': SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
        'time': SimpleNamespace(monotonic=lambda: clock[0]),
        'get_ros_node': lambda _: ros_node,
        'lifecycle_msgs': SimpleNamespace(
            srv=SimpleNamespace(GetState=SimpleNamespace(Request=lambda: object())),
            msg=SimpleNamespace(State=SimpleNamespace(
                PRIMARY_STATE_ACTIVE=3, PRIMARY_STATE_INACTIVE=2, PRIMARY_STATE_FINALIZED=4))),
    }
    exec(_LIFECYCLE_ACTIVATION, namespace)
    event = object()
    node = SimpleNamespace(node_name='/robot_2/probe')
    error = None
    try:
        asyncio.run(namespace['_activate_when_configured'](context, node, event, timeout=0.4))
    except Exception as exc:
        error = exc
    return SimpleNamespace(error=error, events=events, event=event, destroyed=destroyed,
                           client=client, futures=futures, bindings=bindings)


def test_observed_inactive_triggers_exactly_one_targeted_activation():
    result = run_case()
    assert result.error is None
    assert result.events == [result.event]
    assert result.bindings == ['/robot_2/probe/get_state']
    assert result.destroyed == [result.client]


def test_already_active_does_not_send_another_activation():
    result = run_case((3,))
    assert result.error is None
    assert result.events == []


@pytest.mark.parametrize('states,expected_requests', [((1,), 0), ((2,), 1)])
def test_failed_transition_is_bounded_and_not_retried(states, expected_requests):
    result = run_case(states)
    assert isinstance(result.error, RuntimeError)
    assert 'timed out' in str(result.error)
    assert '/robot_2/probe' in str(result.error)
    assert len(result.events) == expected_requests
    assert result.destroyed == [result.client]


@pytest.mark.parametrize('value', [4, None, RuntimeError('service failed')])
def test_invalid_or_failed_state_response_is_not_success(value):
    result = run_case((value,))
    assert isinstance(result.error, RuntimeError)
    assert result.events == []
    assert result.destroyed == [result.client]


def test_missing_service_has_a_deadline():
    result = run_case(ready=False)
    assert 'service unavailable' in str(result.error)
    assert result.events == []
    assert result.futures == []
    assert result.destroyed == [result.client]


def test_pending_request_is_cancelled_at_deadline():
    result = run_case(pending=True)
    assert 'timed out' in str(result.error)
    assert len(result.futures) == 1
    assert result.futures[0].cancelled
    assert result.destroyed == [result.client]


def test_launch_shutdown_cancels_request_without_reusing_destroyed_adapter():
    result = run_case(pending=True, shutdown_after=0.1)
    assert result.error is None
    assert result.futures[0].cancelled
    assert result.events == []
    assert result.destroyed == []  # Launch's ROS adapter owns shutdown cleanup.


def test_normal_launch_cancellation_does_not_fail_shutdown():
    result = run_case(pending=True, shutdown_after=0.01, cancel_on_sleep=True)
    assert result.error is None
    assert result.futures[0].cancelled
    assert result.destroyed == []


def test_unexpected_cancellation_is_not_silenced():
    with pytest.raises(asyncio.CancelledError):
        run_case(pending=True, cancel_on_sleep=True)
