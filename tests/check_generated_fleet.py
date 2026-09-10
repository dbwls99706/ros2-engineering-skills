#!/usr/bin/env python3
"""Build generated fleets and verify installed launch, parameters, and lifecycle.

Run only in an isolated, sourced ROS environment. No hardware or motion commands.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


class FleetServiceTimeout(RuntimeError):
    """Only an unanswered request is eligible for a read-only retry."""


def call_service(endpoint, request, executor, *, timeout=5.0, deadline=None):
    """Issue once and retire the pending request on every exit path."""
    now = time.monotonic()
    if deadline is not None and now >= deadline:
        raise RuntimeError('Fleet readiness deadline expired before request: ' + endpoint.srv_name)
    request_deadline = now + timeout
    if deadline is not None:
        request_deadline = min(request_deadline, deadline)
    future = endpoint.call_async(request)
    try:
        while not future.done() and not future.cancelled():
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise FleetServiceTimeout('Fleet service did not respond: ' + endpoint.srv_name)
            executor.spin_once(timeout_sec=min(0.05, remaining))
        if future.cancelled():
            raise RuntimeError('Fleet service request was cancelled: ' + endpoint.srv_name)
        try:
            response = future.result()
        except Exception as exc:
            raise RuntimeError('Fleet service failed: ' + endpoint.srv_name) from exc
        if response is None:
            raise RuntimeError('Fleet service returned no response: ' + endpoint.srv_name)
        # A callback can overrun spin_once's wait duration. A late success is not
        # evidence that readiness was reached within the advertised deadline.
        if time.monotonic() >= request_deadline:
            raise RuntimeError('Fleet service response arrived after deadline: ' + endpoint.srv_name)
        return response
    finally:
        try:
            if not future.done() and not future.cancelled():
                future.cancel()
        finally:
            endpoint.remove_pending_request(future)


def read_service(endpoint, request, executor, *, deadline=None):
    """Retry missing read responses, not completed errors or state transitions."""
    last_error = None
    for attempt in range(3):
        try:
            return call_service(endpoint, request, executor, timeout=2.0, deadline=deadline)
        except FleetServiceTimeout as exc:
            last_error = exc
            if attempt < 2:
                pause = 0.1 if deadline is None else min(0.1, deadline - time.monotonic())
                if pause > 0:
                    executor.spin_once(timeout_sec=pause)
    raise FleetServiceTimeout(
        'Fleet read service did not respond after 3 attempts: ' + endpoint.srv_name
    ) from last_error


def validate_child_exits(events, expected=2):
    """Require a successful exit for every distinct process that actually started."""
    started, exited = set(), {}
    for event in events:
        if not isinstance(event, dict) or type(event.get('pid')) is not int or event['pid'] <= 0:
            raise RuntimeError('Malformed launch process event')
        pid = event['pid']
        if event.get('event') == 'start' and pid not in started:
            started.add(pid)
        elif event.get('event') == 'exit' and pid in started and pid not in exited:
            code = event.get('returncode')
            if type(code) is not int or code != 0:
                raise RuntimeError(f'Fleet child {pid} did not exit cleanly: {code}')
            exited[pid] = code
        else:
            raise RuntimeError('Duplicate, unordered, or unknown launch process event')
    if len(started) != expected or set(exited) != started:
        raise RuntimeError('Missing child start/exit evidence')
    return [{'pid': pid, 'returncode': code} for pid, code in sorted(exited.items())]


def wait_for_launch_exit(process, timeout=10.0):
    """Capture timeout evidence, but never turn diagnostic-assisted exit into PASS."""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f'Fleet supervisor {process.pid}: shutdown deadline expired; requesting stacks',
              flush=True)
        try:
            process.send_signal(signal.SIGUSR1)
        except ProcessLookupError:
            pass
        # Let faulthandler write before final cleanup. Acceptance has already failed:
        # even if this diagnostic signal wakes the process, preserve the timeout.
        time.sleep(0.1)
        raise


def startup_complete_count(path):
    """Only complete, unique success records satisfy the startup barrier."""
    if not path.exists():
        return 0
    text = path.read_text(encoding='utf-8')
    # A concurrent append may not have written the trailing newline yet.
    lines = text.split('\n')[:-1]
    actions = set()
    for line in lines:
        row = json.loads(line)
        action = row.get('action') if isinstance(row, dict) else None
        if (type(action) is not int or action <= 0 or action in actions
                or row.get('event') != 'startup_complete'):
            raise RuntimeError('Malformed or duplicate startup completion evidence')
        actions.add(action)
    return len(actions)


def run_launch(work, package, discover, drop_transition_events=False, expected_startups=0):
    from smoke_test_nodes import terminate_group

    events = work / (package + '-events.jsonl')
    dropped = work / (package + '-dropped-events.jsonl')
    startup = work / (package + '-startup.jsonl')
    # Repeated attempts need fresh evidence, not earlier successful child exits.
    events.unlink(missing_ok=True)
    dropped.unlink(missing_ok=True)
    startup.unlink(missing_ok=True)
    fault = ''
    if drop_transition_events:
        fault = f'''# Fault injection in the observer process only; generated nodes are unchanged.
try:
    from launch_ros.utilities.lifecycle_event_manager import LifecycleEventManager as EventOwner
except ImportError:
    from launch_ros.actions import LifecycleNode as EventOwner


def discard_transition(self, context, message):
    with open({str(dropped)!r}, 'a', encoding='utf-8') as output:
        output.write(json.dumps({{'node': self.node_name, 'goal': message.goal_state.id}}) + '\\n')


EventOwner._on_transition_event = discard_transition


'''

    wrapper = work / (package + '-observer.launch.py')
    installed_launch = work / 'install' / package / 'share' / package / 'launch/fleet.launch.py'
    # This adds process telemetry around the unmodified installed fleet launch.
    # Child exits are events from launch, not inferred from the supervisor's code.
    wrapper.write_text(f"""import faulthandler
import json
import signal
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, OpaqueCoroutine, RegisterEventHandler
from launch.event_handlers import OnExecutionComplete, OnProcessExit, OnProcessStart
from launch.launch_description_sources import PythonLaunchDescriptionSource


faulthandler.register(signal.SIGUSR1, all_threads=True)
print('Fleet supervisor SIGINT handler:', signal.getsignal(signal.SIGINT), flush=True)


{fault}def record(kind, event):
    row = {{'event': kind, 'pid': event.pid}}
    if kind == 'exit':
        row['returncode'] = event.returncode
    with open({str(events)!r}, 'a', encoding='utf-8') as output:
        output.write(json.dumps(row) + '\\n')


def record_startup(event, context):
    future = event.action.get_asyncio_future()
    if future is None or not future.done() or future.cancelled():
        raise RuntimeError('Startup completion event has no successful future')
    if future.exception() is not None:
        raise future.exception()
    with open({str(startup)!r}, 'a', encoding='utf-8') as output:
        output.write(json.dumps({{'event': 'startup_complete',
                                 'action': id(event.action)}}) + '\\n')


def generate_launch_description():
    return LaunchDescription([
        RegisterEventHandler(OnExecutionComplete(
            target_action=lambda action: isinstance(action, OpaqueCoroutine),
            on_completion=record_startup)),
        RegisterEventHandler(OnProcessStart(on_start=lambda event, context: record('start', event))),
        RegisterEventHandler(OnProcessExit(on_exit=lambda event, context: record('exit', event))),
        IncludeLaunchDescription(PythonLaunchDescriptionSource({str(installed_launch)!r})),
    ])
""", encoding='utf-8')
    # Exercise the shipped user entry point, including its event-loop signal
    # ownership. The native CLI's SIGINT race is a separate documented limit.
    command = ['bash', '-c', 'source "$1"; exec python3 "$2" "$3"',
               'fleet-probe', str(work / 'install/setup.bash'),
               str(ROOT / 'scripts/launch_supervisor.py'), str(wrapper)]
    with tempfile.TemporaryFile(mode='w+b') as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 20.0
            discover.deadline = deadline
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Fleet launch exited before verification')
                completed = startup_complete_count(startup)
                if expected_startups and completed > expected_startups:
                    raise RuntimeError('Unexpected number of lifecycle startup helpers')
                # Active is a node state, not proof that launch-side startup/cleanup ended.
                # Do not race sibling transitions against the unfinished startup helper.
                ready = completed >= expected_startups and discover()
                if completed < expected_startups:
                    time.sleep(0.01)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Fleet parameters/lifecycle did not become ready: ' +
                                       json.dumps({'startup_helpers_completed': completed,
                                                   'observation': discover.last_observation},
                                                  sort_keys=True))
                if ready:
                    break
            # Signal the supervisor only: launch forwards SIGINT to its children.
            # Signalling the group too would interrupt Python cleanup a second time.
            print(f'Fleet supervisor {process.pid}: requesting SIGINT shutdown', flush=True)
            process.send_signal(signal.SIGINT)
            wait_for_launch_exit(process)
            if process.returncode != 0:
                raise RuntimeError('Fleet launch did not exit cleanly')
            if drop_transition_events and (not dropped.exists() or not dropped.read_text().strip()):
                raise RuntimeError('Fault injection did not observe any transition events')
            return validate_child_exits([
                json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()])
        finally:
            try:
                terminate_group(process)
            finally:
                output.seek(0, os.SEEK_END)
                output.seek(max(0, output.tell() - 32768))
                print(output.read().decode('utf-8', errors='replace'), flush=True)


def verify_fleet(work, package, lifecycle, drop_transition_events=False, rapid_shutdown=False):
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from lifecycle_msgs.msg import State, Transition
    from lifecycle_msgs.srv import ChangeState, GetState
    from rcl_interfaces.srv import GetParameters

    context = Context()
    rclpy.init(context=context)
    observer = executor = None
    names = [f'/robot_{i}/{package}_robot_{i}' for i in (1, 2)]
    clients = []
    try:
        observer = Node('fleet_observer_' + uuid.uuid4().hex, context=context)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(observer)

        def client(kind, path):
            value = observer.create_client(kind, path)
            clients.append(value)
            return value

        parameters = [client(GetParameters, n + '/get_parameters') for n in names]
        states = [client(GetState, n + '/get_state') for n in names] if lifecycle else []
        changes = [client(ChangeState, n + '/change_state') for n in names] if lifecycle else []

        def call(endpoint, request, *, timeout=5.0):
            return call_service(endpoint, request, executor, timeout=timeout,
                                deadline=discover.deadline)

        def read_call(endpoint, request):
            return read_service(endpoint, request, executor, deadline=discover.deadline)

        verified = package + ': fleet verified'

        def discover():
            executor.spin_once(timeout_sec=0.05)
            missing = [c.srv_name for c in clients if not c.service_is_ready()]
            discover.last_observation = {'missing_services': missing}
            if missing:
                discover.active_since = None
                return set()
            observed = [read_call(c, GetState.Request()).current_state for c in states]
            discover.last_observation['states'] = {
                name: {'id': state.id, 'label': state.label}
                for name, state in zip(names, observed)}
            if any(state.id != State.PRIMARY_STATE_ACTIVE for state in observed):
                discover.active_since = None
                return set()
            now = time.monotonic()
            if not rapid_shutdown and discover.active_since is None:
                discover.active_since = now
                discover.last_observation['stable_active_for'] = 0.0
                return set()
            stable_for = now - (discover.active_since or now)
            discover.last_observation['stable_active_for'] = stable_for
            if not rapid_shutdown and stable_for < 0.2:
                return set()
            for c in parameters:
                values = read_call(c, GetParameters.Request(names=['publish_rate'])).values
                if len(values) != 1 or values[0].type != 3 or values[0].double_value != 17.0:
                    raise RuntimeError('Namespaced fleet did not load the nondefault YAML value')
            if lifecycle and not drop_transition_events and not rapid_shutdown:
                request = ChangeState.Request()
                request.transition.id = Transition.TRANSITION_DEACTIVATE
                if not call(changes[0], request).success:
                    raise RuntimeError('Could not deactivate first fleet node')
                ids = [read_call(c, GetState.Request()).current_state.id for c in states]
                if ids != [State.PRIMARY_STATE_INACTIVE, State.PRIMARY_STATE_ACTIVE]:
                    raise RuntimeError('Transition leaked to a sibling or auto-reactivated: ' + str(ids))
                request.transition.id = Transition.TRANSITION_ACTIVATE
                if not call(changes[0], request).success:
                    raise RuntimeError('Could not reactivate first fleet node')
                ids = [read_call(c, GetState.Request()).current_state.id for c in states]
                if ids != [State.PRIMARY_STATE_ACTIVE, State.PRIMARY_STATE_ACTIVE]:
                    raise RuntimeError(
                        'Fleet did not return to active after sibling test: ' + str(ids))
                # Keep orderly lifecycle shutdown separate from the abrupt active-state
                # SIGINT exercised by rapid_shutdown. Some Humble rclcpp releases can
                # fault when a LifecycleNode is destroyed before it reaches Finalized.
                for change in changes:
                    request = ChangeState.Request()
                    request.transition.id = Transition.TRANSITION_ACTIVE_SHUTDOWN
                    if not call(change, request).success:
                        raise RuntimeError('Could not finalize fleet node before shutdown')
                ids = [read_call(c, GetState.Request()).current_state.id for c in states]
                if ids != [State.PRIMARY_STATE_FINALIZED, State.PRIMARY_STATE_FINALIZED]:
                    raise RuntimeError('Fleet did not reach finalized before shutdown: ' + str(ids))
            return {verified}

        discover.last_observation = {}
        discover.deadline = None
        discover.active_since = None
        children = run_launch(work, package, discover, drop_transition_events,
                              expected_startups=2 if lifecycle and not rapid_shutdown else 0)
        return {'package': package, 'nodes': names, 'publish_rate': 17.0,
                'lifecycle': lifecycle,
                'sibling_isolation': lifecycle and not drop_transition_events and not rapid_shutdown,
                'orderly_lifecycle_shutdown': (
                    lifecycle and not drop_transition_events and not rapid_shutdown),
                'startup_completion_required': lifecycle and not rapid_shutdown,
                'rapid_shutdown_after_active': rapid_shutdown,
                'transition_events_discarded': drop_transition_events,
                'entrypoint': 'scripts/launch_supervisor.py',
                'children': children, 'status': 'pass'}
    finally:
        try:
            if executor is not None:
                executor.shutdown(timeout_sec=2.0)
        finally:
            try:
                if observer is not None:
                    observer.destroy_node()
            finally:
                context.try_shutdown()


def main():
    variants = [('fleet_plain_probe', 'python', False),
                ('fleet_lifecycle_probe', 'python', True),
                ('fleet_cpp_probe', 'cpp', True)]
    with tempfile.TemporaryDirectory(prefix='generated-fleet-') as temporary:
        work = Path(temporary)
        for package, kind, lifecycle in variants:
            command = [sys.executable, str(ROOT / 'scripts/create_package.py'), package,
                       '--type', kind, '--robots', '2', '--dest', str(work / 'src')]
            if kind == 'python' and lifecycle:
                command.append('--lifecycle')
            subprocess.run(command, check=True, timeout=20)
            config = work / 'src' / package / 'config/params.yaml'
            config.write_text(config.read_text(encoding='utf-8').replace('50.0', '17.0'),
                              encoding='utf-8')
        subprocess.run(['colcon', 'build', '--executor', 'sequential',
                        '--event-handlers', 'console_direct+', '--cmake-args',
                        '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release'],
                       cwd=work, check=True, timeout=180)
        # Retain native CLI discovery/import compatibility for every variant.
        # This does not claim to fix upstream ros2 launch SIGINT handling.
        for package, _, _ in variants:
            launch = work / 'install' / package / 'share' / package / 'launch/fleet.launch.py'
            subprocess.run(['bash', '-c', 'source "$1"; exec ros2 launch "$2" --show-args',
                            'fleet-native-probe', str(work / 'install/setup.bash'), str(launch)],
                           check=True, timeout=10)
        records = []
        # Fresh launches expose startup/shutdown races; any failed trial stops the gate.
        # This is not retry-until-pass: every recorded trial must succeed.
        for trial in range(3):
            for package, _, lifecycle in variants:
                launch = work / 'install' / package / 'share' / package / 'launch/fleet.launch.py'
                if not launch.is_file():
                    raise RuntimeError('Fleet launch missing from installed package: ' + package)
                result = verify_fleet(work, package, lifecycle)
                records.append({**result, 'trial': trial + 1})
                if lifecycle:
                    # A deterministic negative control for the former event-only startup.
                    result = verify_fleet(work, package, lifecycle, drop_transition_events=True)
                    records.append({**result, 'trial': trial + 1})
                    # Keep abrupt shutdown coverage separate from settled sibling isolation.
                    result = verify_fleet(work, package, lifecycle, rapid_shutdown=True)
                    records.append({**result, 'trial': trial + 1})
    print(json.dumps({'status': 'pass', 'scope': 'Actual generated ROS fleet behavior',
                      'results': records}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
