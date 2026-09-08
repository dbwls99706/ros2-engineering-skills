#!/usr/bin/env python3
"""Discover live generated nodes without a CLI daemon; own and reap node processes.

The observer uses the ROS graph directly. Subprocess ownership/timeout tests use
synthetic children and do not claim ROS or DDS behavior.
"""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid


class SmokeInterrupted(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum


def terminate_group(process, grace=2.0):
    """Bound cleanup even if a node ignores signals or its parent has exited."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait(timeout=grace)
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()  # Reap our direct child, including an early-exit parent.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
    process.wait(timeout=grace)
    # Docker --init reaps orphaned descendants; container teardown is the final
    # boundary. A zombie process group may briefly exist after SIGKILL.


def probe(command, expected_name, discover, startup_timeout=15.0, stop_grace=2.0,
          require_clean_exit=False):
    """Require both exact unique graph identity and a still-live owned process."""
    if startup_timeout <= 0 or stop_grace <= 0:
        raise ValueError('Timeouts must be positive')
    # A file rather than PIPE prevents inherited output descriptors from keeping
    # communicate() open after the parent exits. Only a bounded log tail is read.
    with tempfile.TemporaryFile(mode='w+b') as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        discovered = False
        try:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'{expected_name} exited early: {process.returncode}')
                names = discover()
                if process.poll() is not None:
                    raise RuntimeError(f'{expected_name} exited during discovery: {process.returncode}')
                if expected_name in names:
                    print(f'{expected_name}: live process and graph discovery verified', flush=True)
                    discovered = True
                    break
                time.sleep(0.05)
            if not discovered:
                raise RuntimeError(f'{expected_name} was not discovered before the deadline')
        finally:
            try:
                terminate_group(process, stop_grace)
            finally:
                output.seek(0, os.SEEK_END)
                output.seek(max(0, output.tell() - 32768))
                tail = output.read().decode('utf-8', errors='replace')
                if tail:
                    print(tail, end='' if tail.endswith('\n') else '\n', flush=True)

        # Discovery is not enough: generated programs must also handle SIGINT
        # without a traceback, forced kill, or other unsuccessful termination.
        if require_clean_exit and process.poll() != 0:
            raise RuntimeError(f'{expected_name} did not shut down cleanly: {process.returncode}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('install', type=Path, help='Generated colcon workspace install directory')
    args = parser.parse_args(argv)
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node

    context = Context()
    observer = executor = None
    handlers = {}
    try:
        rclpy.init(context=context)

        def interrupted(signum, frame):
            raise SmokeInterrupted(signum)

        # Install after rclpy.init so a host cancellation reaches our finally blocks.
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.signal(sig, interrupted)
        observer = Node('skill_smoke_observer_' + uuid.uuid4().hex, context=context)
        # rclpy.spin_once without an executor uses the uninitialized default
        # context, not this observer's private context. Keep all three together.
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(observer)

        def discover():
            executor.spin_once(timeout_sec=0.1)
            return set(observer.get_node_names())

        for package in ('test_cpp_pkg', 'test_py_pkg'):
            executable = args.install / package / 'lib' / package / (package + '_node')
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise RuntimeError('Missing generated executable: ' + str(executable))
            name = 'skill_smoke_' + uuid.uuid4().hex
            probe([str(executable), '--ros-args', '-r', '__node:=' + name], name, discover,
                  require_clean_exit=True)
        print('All smoke tests passed; owned node processes have been cleaned up.', flush=True)
        return 0
    except SmokeInterrupted as exc:
        print(f'Smoke test interrupted by signal {exc.signum}', file=sys.stderr)
        return 128 + exc.signum
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Smoke test failed: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        try:
            if executor is not None and not executor.shutdown(timeout_sec=2.0):
                raise RuntimeError('Observer executor did not shut down before the deadline')
        finally:
            try:
                if observer is not None:
                    observer.destroy_node()
            finally:
                try:
                    if context.ok():
                        context.shutdown()
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())
