#!/usr/bin/env python3
"""Repeatedly signal the installed generated C++ lifecycle node and require a clean exit.

Run only in an isolated, sourced ROS environment after building test_cpp_pkg. The
installed executable is started directly, not through the ros2 CLI, so the CLI's own
SIGINT handling is not part of the result. Each trial drives configure and activate
over the lifecycle services, then signals the node process: right after the activate
response, or after the node has been observed Active for 0.2 s. A final trial sends
SIGTERM. No hardware or motion commands are involved.

When a C compiler is available, tests/segv_backtrace.c is preloaded so a node killed
by SIGSEGV, SIGBUS, or SIGABRT prints a best-effort backtrace before dying from the
same signal. The exit status is never changed by the shim, and a missing backtrace
does not turn a crash into a pass.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'test_cpp_pkg'
SCENARIOS = ('immediate', 'settled')
MAX_REPORTED_LOGS = 3


def plan(iterations):
    """SIGINT trials alternate between the two timings; one SIGTERM trial ends the run."""
    if iterations < 1:
        raise ValueError('iterations must be positive')
    trials = [(SCENARIOS[i % len(SCENARIOS)], signal.SIGINT) for i in range(iterations)]
    trials.append(('settled', signal.SIGTERM))
    return trials


def classify(returncode):
    if returncode == 0:
        return 'clean'
    if returncode < 0:
        try:
            return 'signal:' + signal.Signals(-returncode).name
        except ValueError:
            return f'signal:{-returncode}'
    return f'exit:{returncode}'


def build_shim(out_dir, compiler='cc'):
    """Compile the diagnostic shim; its absence is recorded, never fatal."""
    target = Path(out_dir) / 'libsegv_backtrace.so'
    command = [compiler, '-shared', '-fPIC', '-O0', '-g', '-o', str(target),
               str(ROOT / 'tests' / 'segv_backtrace.c')]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'[WARN] backtrace shim unavailable: {exc}', flush=True)
        return None
    return target


def demangle(text):
    try:
        result = subprocess.run(['c++filt'], input=text, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return text
    return result.stdout if result.returncode == 0 else text


def summarize(records, require_sigterm):
    """SIGINT trials must all exit cleanly; SIGTERM is gating only when required."""
    failures = [r for r in records if r['outcome'] != 'clean'
                and (r['signal'] == 'SIGINT' or require_sigterm)]
    recorded_only = [r for r in records if r['outcome'] != 'clean' and r not in failures]
    return {
        'status': 'fail' if failures else 'pass',
        'trials': len(records),
        'sigint_trials': sum(r['signal'] == 'SIGINT' for r in records),
        'sigint_clean': sum(r['signal'] == 'SIGINT' and r['outcome'] == 'clean' for r in records),
        'sigterm_outcome': next((r['outcome'] for r in records if r['signal'] == 'SIGTERM'), None),
        'sigterm_gating': require_sigterm,
        'failures': [{k: r[k] for k in ('trial', 'scenario', 'signal', 'outcome')} for r in failures],
        'recorded_only': [{k: r[k] for k in ('trial', 'scenario', 'signal', 'outcome')}
                          for r in recorded_only],
    }


def run_trial(executable, shim, scenario, signum, executor, observer, interfaces):
    from check_generated_fleet import call_service, read_service
    from smoke_test_nodes import terminate_group

    ChangeState, GetState, Transition, State = interfaces
    namespace = '/cpp_shutdown_' + uuid.uuid4().hex[:12]
    env = dict(os.environ)
    if shim is not None:
        env['LD_PRELOAD'] = ' '.join(filter(None, [str(shim), env.get('LD_PRELOAD', '')]))
    with tempfile.TemporaryFile(mode='w+b') as output:
        process = subprocess.Popen([str(executable), '--ros-args', '-r', '__ns:=' + namespace],
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=env)
        change = observer.create_client(ChangeState, f'{namespace}/{PACKAGE}/change_state')
        state = observer.create_client(GetState, f'{namespace}/{PACKAGE}/get_state')
        try:
            deadline = time.monotonic() + 15.0
            while not (change.service_is_ready() and state.service_is_ready()):
                if process.poll() is not None:
                    raise RuntimeError('Node exited before its lifecycle services appeared')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Lifecycle services were not discovered within 15 s')
                executor.spin_once(timeout_sec=0.05)
            for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
                request = ChangeState.Request()
                request.transition.id = transition
                if not call_service(change, request, executor, timeout=5.0).success:
                    raise RuntimeError(f'Lifecycle transition {transition} was rejected')
            if scenario == 'settled':
                current = read_service(state, GetState.Request(), executor).current_state
                if current.id != State.PRIMARY_STATE_ACTIVE:
                    raise RuntimeError(f'Node is not Active before signalling: {current.label}')
                time.sleep(0.2)
            os.kill(process.pid, signum)
            returncode = process.wait(timeout=10.0)
        finally:
            observer.destroy_client(change)
            observer.destroy_client(state)
            if process.poll() is None:
                terminate_group(process)
            output.seek(0)
            log = output.read().decode('utf-8', errors='replace')
    return returncode, log


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('install', type=Path, help='colcon install directory containing test_cpp_pkg')
    parser.add_argument('--iterations', type=int, default=30)
    parser.add_argument('--require-sigterm', action='store_true',
                        help='fail on an unclean SIGTERM exit instead of recording it')
    args = parser.parse_args(argv)
    executable = args.install / PACKAGE / 'lib' / PACKAGE / f'{PACKAGE}_node'
    if not executable.is_file():
        raise RuntimeError('Generated C++ executable is missing: ' + str(executable))

    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from lifecycle_msgs.msg import State, Transition
    from lifecycle_msgs.srv import ChangeState, GetState

    started = time.monotonic()
    records, logs = [], []
    context = Context()
    rclpy.init(context=context)
    observer = executor = None
    with tempfile.TemporaryDirectory(prefix='cpp-shutdown-') as scratch:
        shim = build_shim(scratch)
        try:
            observer = Node('cpp_shutdown_observer_' + uuid.uuid4().hex, context=context)
            executor = SingleThreadedExecutor(context=context)
            executor.add_node(observer)
            for trial, (scenario, signum) in enumerate(plan(args.iterations), start=1):
                returncode, log = run_trial(executable, shim, scenario, signum, executor, observer,
                                            (ChangeState, GetState, Transition, State))
                record = {'trial': trial, 'scenario': scenario, 'signal': signum.name,
                          'returncode': returncode, 'outcome': classify(returncode)}
                records.append(record)
                if record['outcome'] != 'clean' and len(logs) < MAX_REPORTED_LOGS:
                    logs.append((record, demangle(log)))
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
    for record, log in logs:
        print(f'--- trial {record["trial"]} ({record["scenario"]}, {record["signal"]}): '
              f'{record["outcome"]} ---', flush=True)
        print(log[-16384:], flush=True)
    summary = summarize(records, args.require_sigterm)
    summary.update({'distro': os.environ.get('ROS_DISTRO'), 'backtrace_shim': shim is not None,
                    'elapsed_s': round(time.monotonic() - started, 1),
                    'scope': 'Installed generated C++ lifecycle node, direct executable'})
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
