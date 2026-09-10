#!/usr/bin/env python3
"""Run an authorized ROS launch file with event-loop-owned SIGINT handling.

This Linux/POSIX entry point starts real processes. It is not a validator or a
hardware stop mechanism. Source the ROS distribution and workspace first.
"""

import argparse
import asyncio
from pathlib import Path
import signal
import sys
import threading


__version__ = '0.1.0'


def run_service(service):
    """Own the loop and SIGINT handler through launch execution and finalization."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('The launch supervisor must run in the main thread')
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('The launch supervisor requires its own event loop')

    loop = asyncio.new_event_loop()
    previous_handler = signal.getsignal(signal.SIGINT)
    interrupted = False
    handler_installed = False

    def request_shutdown():
        nonlocal interrupted
        interrupted = True
        # This callback runs in the loop, not inside a Python signal handler.
        # Launch's own signal manager may have already requested shutdown.
        service.shutdown(force_sync=True)

    async def run():
        if interrupted:
            return 130
        return await service.run_async()

    async def finalize():
        pending = asyncio.all_tasks(loop) - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await loop.shutdown_asyncgens()
        await loop.shutdown_default_executor()

    try:
        # asyncio installs a non-raising Python handler and a wakeup fd. ROS
        # AsyncSafeSignalManager forwards to that fd, retaining both callbacks.
        # KeyboardInterrupt must not discard a ready callback between pop/run.
        loop.add_signal_handler(signal.SIGINT, request_shutdown)
        handler_installed = True
        try:
            return loop.run_until_complete(run())
        finally:
            loop.run_until_complete(finalize())
    finally:
        try:
            if handler_installed:
                loop.remove_signal_handler(signal.SIGINT)
                signal.signal(signal.SIGINT, previous_handler)
        finally:
            loop.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('launch_file', type=Path, help='Path to an installed launch file')
    parser.add_argument('launch_arguments', nargs='*', help='Launch arguments: name:=value')
    args = parser.parse_args(argv)
    if sys.platform == 'win32':
        parser.error('This supervisor requires POSIX event-loop signal handling')
    if not args.launch_file.is_file():
        parser.error('Launch file does not exist: ' + str(args.launch_file))

    # Help/version/path errors work without a ROS installation. Only execution
    # loads ROS modules, and ordinary import or launch errors retain failure.
    from launch import LaunchDescription, LaunchService
    from launch.actions import IncludeLaunchDescription
    from launch.launch_description_sources import AnyLaunchDescriptionSource
    from ros2launch.api import parse_launch_arguments

    service = LaunchService(argv=args.launch_arguments, noninteractive=True, debug=args.debug)
    service.include_launch_description(LaunchDescription([
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(str(args.launch_file.resolve())),
            launch_arguments=parse_launch_arguments(args.launch_arguments)),
    ]))
    return run_service(service)


if __name__ == '__main__':
    raise SystemExit(main())
