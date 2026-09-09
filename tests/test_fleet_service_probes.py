"""Synthetic service outcomes must not manufacture successful fleet evidence."""

from types import SimpleNamespace
import sys

import pytest

from tests import check_generated_fleet as fleet

PENDING = object()


class Future:
    def __init__(self, outcome):
        self.outcome = outcome
        self.was_cancelled = False

    def done(self):
        # rclpy distinguishes cancelled from finished.
        return self.outcome is not PENDING and not self.was_cancelled

    def cancelled(self):
        return self.was_cancelled

    def cancel(self):
        self.was_cancelled = True

    def result(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Endpoint:
    srv_name = '/fixture/get_state'

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.futures = []
        self.removed = []

    def call_async(self, request):
        # No unresolved earlier request may be left when a retry is sent.
        assert self.futures == self.removed
        future = Future(self.outcomes.pop(0))
        self.futures.append(future)
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=0.0)
    monkeypatch.setattr(fleet.time, 'monotonic', lambda: value.now)

    def spin_once(timeout_sec):
        value.now += timeout_sec

    value.executor = SimpleNamespace(spin_once=spin_once)
    return value


@pytest.mark.parametrize('outcome, message', [
    (RuntimeError('server error'), 'service failed'),
    (ValueError('invalid response'), 'service failed'),
    (None, 'returned no response'),
])
def test_completed_error_is_not_retried_or_replaced_by_later_success(clock, outcome, message):
    endpoint = Endpoint([outcome, 'later success'])
    with pytest.raises(RuntimeError, match=message):
        fleet.read_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 1
    assert endpoint.removed == endpoint.futures
    assert clock.now == 0.0


def test_missing_read_response_is_retired_before_retry(clock):
    response = object()
    endpoint = Endpoint([PENDING, response])
    assert fleet.read_service(endpoint, object(), clock.executor) is response
    assert len(endpoint.futures) == 2
    assert endpoint.futures[0].cancelled()
    assert endpoint.removed == endpoint.futures
    assert clock.now == pytest.approx(2.1)


def test_three_unanswered_reads_fail_without_unbounded_retries(clock):
    endpoint = Endpoint([PENDING] * 3)
    with pytest.raises(fleet.FleetServiceTimeout, match='after 3 attempts'):
        fleet.read_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 3
    assert all(f.cancelled() for f in endpoint.futures)
    assert endpoint.removed == endpoint.futures
    assert clock.now == pytest.approx(6.2)


def test_state_change_timeout_is_never_replayed(clock):
    endpoint = Endpoint([PENDING, 'would succeed'])
    endpoint.srv_name = '/fixture/change_state'
    with pytest.raises(fleet.FleetServiceTimeout):
        fleet.call_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 1
    assert endpoint.removed == endpoint.futures
    assert clock.now == pytest.approx(5.0)


def test_read_retry_cannot_reset_shared_readiness_deadline(clock):
    endpoint = Endpoint([PENDING] * 3)
    with pytest.raises(RuntimeError, match='readiness deadline expired'):
        fleet.read_service(endpoint, object(), clock.executor, deadline=0.12)
    assert len(endpoint.futures) == 1
    assert clock.now == pytest.approx(0.12)
    assert endpoint.removed == endpoint.futures


def test_expired_readiness_never_submits_a_request(clock):
    endpoint = Endpoint(['ready'])
    with pytest.raises(RuntimeError, match='before request'):
        fleet.call_service(endpoint, object(), clock.executor, deadline=0.0)
    assert endpoint.futures == endpoint.removed == []


def test_executor_error_retires_pending_read_without_retry(clock):
    endpoint = Endpoint([PENDING, 'later success'])

    def fail_spin(**kwargs):
        raise RuntimeError('executor failed')

    clock.executor.spin_once = fail_spin
    with pytest.raises(RuntimeError, match='executor failed'):
        fleet.read_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 1
    assert endpoint.futures[0].cancelled()
    assert endpoint.removed == endpoint.futures


def test_external_cancellation_is_not_classified_as_missing_response(clock):
    endpoint = Endpoint([PENDING, 'later success'])

    def cancel_on_spin(**kwargs):
        endpoint.futures[-1].cancel()

    clock.executor.spin_once = cancel_on_spin
    with pytest.raises(RuntimeError, match='was cancelled'):
        fleet.read_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 1
    assert endpoint.removed == endpoint.futures


def test_completed_response_after_callback_overrun_is_not_a_pass(clock):
    endpoint = Endpoint([PENDING, 'later success'])

    def overrun_spin(**kwargs):
        clock.now += 3.0
        endpoint.futures[-1].outcome = 'late response'

    clock.executor.spin_once = overrun_spin
    with pytest.raises(RuntimeError, match='response arrived after deadline'):
        fleet.read_service(endpoint, object(), clock.executor)
    assert len(endpoint.futures) == 1
    assert endpoint.removed == endpoint.futures


def test_successful_probe_retires_request_without_cancelling(clock):
    endpoint = Endpoint(['ready'])
    assert fleet.call_service(endpoint, object(), clock.executor) == 'ready'
    assert endpoint.removed == endpoint.futures
    assert not endpoint.futures[0].cancelled()


def test_successful_discovery_after_global_deadline_is_rejected(tmp_path, monkeypatch, clock):
    process = SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(fleet.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setitem(sys.modules, 'smoke_test_nodes', SimpleNamespace(terminate_group=lambda _: None))

    def discover():
        assert discover.deadline == 20.0
        clock.now = 21.0
        return True

    discover.last_observation = {}
    with pytest.raises(RuntimeError, match='did not become ready'):
        fleet.run_launch(tmp_path, 'fixture', discover)
