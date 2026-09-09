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


def wait_for_launch_exit(process, timeout=10.0, diagnostic_after=3.0):
    """Keep the shutdown deadline while capturing a stalled supervisor's threads."""
    deadline = time.monotonic() + timeout
    try:
        process.wait(timeout=min(diagnostic_after, timeout))
    except subprocess.TimeoutExpired:
        print(f'Fleet supervisor {process.pid}: shutdown still pending; requesting stacks',
              flush=True)
        # The observer wrapper registers this with faulthandler before launching.
        # This signal only captures evidence; it must not complete the shutdown.
        try:
            process.send_signal(signal.SIGUSR1)
        except ProcessLookupError:
            pass
        process.wait(timeout=max(0.0, deadline - time.monotonic()))


def run_launch(work, package, discover, drop_transition_events=False):
    from smoke_test_nodes import terminate_group

    events = work / (package + '-events.jsonl')
    dropped = work / (package + '-dropped-events.jsonl')
    # Repeated attempts need fresh evidence, not earlier successful child exits.
    events.unlink(missing_ok=True)
    dropped.unlink(missing_ok=True)
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
from launch.actions import IncludeLaunchDescription, RegisterEventHandler
from launch.event_handlers import OnProcessExit, OnProcessStart
from launch.launch_description_sources import PythonLaunchDescriptionSource


faulthandler.register(signal.SIGUSR1, all_threads=True)


{fault}def record(kind, event):
    row = {{'event': kind, 'pid': event.pid}}
    if kind == 'exit':
        row['returncode'] = event.returncode
    with open({str(events)!r}, 'a', encoding='utf-8') as output:
        output.write(json.dumps(row) + '\\n')


def generate_launch_description():
    return LaunchDescription([
        RegisterEventHandler(OnProcessStart(on_start=lambda event, context: record('start', event))),
        RegisterEventHandler(OnProcessExit(on_exit=lambda event, context: record('exit', event))),
        IncludeLaunchDescription(PythonLaunchDescriptionSource({str(installed_launch)!r})),
    ])
""", encoding='utf-8')
    command = ['bash', '-c', 'source "$1"; exec ros2 launch "$2"',
               'fleet-probe', str(work / 'install/setup.bash'), str(wrapper)]
    with tempfile.TemporaryFile(mode='w+b') as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 20.0
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Fleet launch exited before verification')
                if discover():
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Fleet parameters/lifecycle did not become ready: ' +
                                       json.dumps(discover.last_observation, sort_keys=True))
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


def verify_fleet(work, package, lifecycle, drop_transition_events=False):
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
            future = endpoint.call_async(request)
            deadline = time.monotonic() + timeout
            while not future.done() and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.05)
            if not future.done():
                future.cancel()
                raise RuntimeError('Fleet service did not respond: ' + endpoint.srv_name)
            try:
                response = future.result()
            except Exception as exc:
                raise RuntimeError('Fleet service failed: ' + endpoint.srv_name) from exc
            if response is None:
                raise RuntimeError('Fleet service did not respond: ' + endpoint.srv_name)
            return response

        def read_call(endpoint, request):
            """Retry idempotent reads only; never replay state-changing requests."""
            last_error = None
            for attempt in range(3):
                try:
                    return call(endpoint, request, timeout=2.0)
                except RuntimeError as exc:
                    last_error = exc
                    if attempt < 2:
                        executor.spin_once(timeout_sec=0.1)
            raise RuntimeError(
                'Fleet read service did not respond after 3 attempts: ' + endpoint.srv_name
            ) from last_error

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
            if discover.active_since is None:
                discover.active_since = now
                discover.last_observation['stable_active_for'] = 0.0
                return set()
            stable_for = now - discover.active_since
            discover.last_observation['stable_active_for'] = stable_for
            if stable_for < 0.2:
                return set()
            for c in parameters:
                values = read_call(c, GetParameters.Request(names=['publish_rate'])).values
                if len(values) != 1 or values[0].type != 3 or values[0].double_value != 17.0:
                    raise RuntimeError('Namespaced fleet did not load the nondefault YAML value')
            if lifecycle and not drop_transition_events:
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
            return {verified}

        discover.last_observation = {}
        discover.active_since = None
        children = run_launch(work, package, discover, drop_transition_events)
        return {'package': package, 'nodes': names, 'publish_rate': 17.0,
                'lifecycle': lifecycle,
                'sibling_isolation': lifecycle and not drop_transition_events,
                'transition_events_discarded': drop_transition_events,
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
    print(json.dumps({'status': 'pass', 'scope': 'Actual generated ROS fleet behavior',
                      'results': records}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
