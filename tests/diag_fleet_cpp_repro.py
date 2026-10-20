#!/usr/bin/env python3
"""Temporary long-run comparison of C++ SIGINT shutdown on Humble (diagnostic branch only).

Modes:
  fleet    the generated C++ fleet (two lifecycle nodes) through
           check_generated_fleet.verify_fleet with transition events discarded, the variant
           that exited -11 on Humble.
  minimal  demo_nodes_cpp talker and listener (no lifecycle, no generated code) through the
           same observer launch wrapper, launch supervisor, and launch-forwarded SIGINT.

--control-main on replaces the generated C++ main with a comparison-only control that owns
SIGINT and SIGTERM and destroys the executor and node before rclcpp::shutdown(). It changes
two variables at once, signal ownership and teardown order, and is never a proposed fix.

--asan on preloads libasan into launch and every node (for the AddressSanitizer builds of Fast
DDS). The ASan report files are the primary evidence; a core is kept only when one is produced.

A node still running SLOW_EXIT_S after SIGINT is sent SIGABRT so that a hang leaves a core. That
is a diagnostic trigger, not a verdict: whether it was a deadlock is decided from the stacks.

Every count comes from launch process-exit events, never from an assumed number of nodes.
Iterations that end in a harness error are excluded from the clean-exit sample. The budget
counts iteration time only: core analysis and identity hashing never shorten it. A core
dumped by a child is matched by pid, analysed with tests/diag_gdb_inspect.py (at most
MAX_ANALYSED per run), and the first one is kept compressed with its executable. A partial
summary is written every 25 iterations; the final summary is written on normal exit, on an
exception, and on SIGTERM.

Verdicts: reproduced (any -11), abnormal_exit (any other non-zero child exit),
inconclusive (harness errors or an interrupted budget), not_reproduced.
"""

import argparse
import collections
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

import check_generated_fleet as fleet  # noqa: E402

FLEET_PACKAGE = 'fleet_cpp_probe'
MINIMAL_PACKAGE = 'minimal_probe'
MAX_FAILURE_LOGS = 10
MAX_ANALYSED = 3
MAX_SLOW_EXIT_ANALYSED = 5
PARTIAL_EVERY = 25
CORE_DIR = Path('/var/crash-cores')
CORE_NAME = re.compile(r'^core\.(?P<comm>.+)\.(?P<pid>\d+)\.(?P<time>\d+)$')
CORE_SIGNALS = {signal.SIGSEGV, signal.SIGABRT, signal.SIGBUS, signal.SIGILL, signal.SIGFPE}
INSPECT = ROOT / 'tests' / 'diag_gdb_inspect.py'
SLOW_EXIT_S = 4.0
ASAN_RUNTIME = Path('/usr/lib/x86_64-linux-gnu/libasan.so.6')
# Tools run for analysis must not inherit the preloaded diagnostics of the nodes.
PRELOAD_VARIABLES = ('LD_PRELOAD', 'ASAN_OPTIONS')

MINIMAL_LAUNCH = """# Diagnostic minimal mode: two plain demo nodes, no lifecycle, no generated code.
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package='demo_nodes_cpp', executable='talker', name='talker', output='screen'),
        Node(package='demo_nodes_cpp', executable='listener', name='listener', output='screen'),
    ])
"""

CONTROL_MAIN = """// Diagnostic control only (comparison arm O); never a proposed fix and never shipped.
// It changes two variables at once: SIGINT/SIGTERM are owned here instead of by rclcpp's
// signal handling, and the executor and node are destroyed before rclcpp::shutdown().
#include <pthread.h>
#include <signal.h>

#include <memory>
#include <thread>

#include <rclcpp/rclcpp.hpp>
#include "{header}"

int main(int argc, char ** argv)
{{
  sigset_t stop_signals;
  sigemptyset(&stop_signals);
  sigaddset(&stop_signals, SIGINT);
  sigaddset(&stop_signals, SIGTERM);
  // Blocked before any thread exists, so every later thread inherits the mask.
  pthread_sigmask(SIG_BLOCK, &stop_signals, nullptr);
  rclcpp::init(argc, argv, rclcpp::InitOptions(), rclcpp::SignalHandlerOptions::None);
  {{
    auto node = std::make_shared<{node_type}>();
    rclcpp::executors::SingleThreadedExecutor exe;
    exe.add_node(node->get_node_base_interface());
    std::thread waiter([&exe, stop_signals]() {{
        int received = 0;
        sigwait(&stop_signals, &received);
        exe.cancel();
      }});
    exe.spin();
    // Releases the waiter if spin returned without a signal; harmless once it has returned.
    pthread_kill(waiter.native_handle(), SIGTERM);
    waiter.join();
    exe.remove_node(node->get_node_base_interface());
  }}
  rclcpp::shutdown();
  return 0;
}}
"""


class Interrupted(Exception):
    pass


def read_rows(path):
    """Parse a JSON-lines file that may still be written; skip a partial last line."""
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_exits(events_path):
    """Return (pid, returncode) for every recorded child exit."""
    return [(row.get('pid'), row.get('returncode')) for row in read_rows(events_path)
            if row.get('event') == 'exit']


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def tool_env():
    return {key: value for key, value in os.environ.items() if key not in PRELOAD_VARIABLES}


def demangle(text):
    try:
        result = subprocess.run(['c++filt'], input=text, capture_output=True, text=True, timeout=10,
                                env=tool_env())
    except (OSError, subprocess.SubprocessError):
        return text
    return result.stdout if result.returncode == 0 else text


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


class Tally:
    def __init__(self, budget_s):
        self.budget_s = budget_s
        self.started = time.monotonic()
        self.iteration_s = 0.0
        self.durations = []
        self.attempted = 0
        self.passed = 0
        self.clean_exits = 0
        self.excluded_clean_exits = 0
        self.segv = 0
        self.other_abnormal = collections.Counter()
        self.abnormal_iterations = []
        self.segv_iterations = []
        self.harness_errors = collections.Counter()
        self.failure_logs = []
        self.interrupted = None

    def record(self, exits, error, duration):
        self.attempted += 1
        self.iteration_s += duration
        self.durations.append(duration)
        codes = [code for _, code in exits]
        abnormal = [code for code in codes if code != 0]
        for code in abnormal:
            if code == -signal.SIGSEGV:
                self.segv += 1
            else:
                self.other_abnormal[str(code)] += 1
        clean = sum(code == 0 for code in codes)
        if abnormal:
            # Clean siblings of a crashed child are real clean exits from a completed launch.
            self.clean_exits += clean
            self.abnormal_iterations.append(self.attempted)
            if -signal.SIGSEGV in abnormal:
                self.segv_iterations.append(self.attempted)
                return 'segv'
            return 'abnormal'
        if error is None:
            self.passed += 1
            self.clean_exits += clean
            return 'pass'
        self.excluded_clean_exits += clean
        self.harness_errors[error.splitlines()[0][:200]] += 1
        return 'harness_error'

    def summary(self, final):
        if self.segv:
            verdict = 'reproduced'
        elif self.other_abnormal:
            verdict = 'abnormal_exit'
        elif self.harness_errors or self.interrupted or (final and self.iteration_s < self.budget_s):
            verdict = 'inconclusive'
        else:
            verdict = 'not_reproduced'
        durations = {}
        if self.durations:
            durations = {'mean': round(statistics.fmean(self.durations), 3),
                         'p50': round(percentile(self.durations, 0.5), 3),
                         'p95': round(percentile(self.durations, 0.95), 3),
                         'max': round(max(self.durations), 3)}
        return {
            'verdict': verdict if final else 'in_progress',
            'attempted_iterations': self.attempted,
            'passed_iterations': self.passed,
            'iterations_with_abnormal_exit': len(self.abnormal_iterations),
            'iterations_with_segv': len(self.segv_iterations),
            'abnormal_iterations': self.abnormal_iterations,
            'confirmed_child_exits': self.clean_exits + self.segv + sum(self.other_abnormal.values()),
            'confirmed_clean_child_exits': self.clean_exits,
            'segv_count': self.segv,
            'other_abnormal_exits': dict(self.other_abnormal),
            'harness_errors': dict(self.harness_errors),
            'clean_exits_excluded_with_harness_errors': self.excluded_clean_exits,
            'failure_logs': self.failure_logs,
            'interrupted': self.interrupted,
            'iteration_seconds': durations,
            'iteration_budget_used_s': round(self.iteration_s, 1),
            'budget_s': self.budget_s,
            'wall_elapsed_s': round(time.monotonic() - self.started, 1),
        }


class Identity:
    """Exact packages, executables, and loaded objects behind one run."""

    def __init__(self, evidence):
        self.directory = evidence / 'identity'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mapped = None

    def record_environment(self, executables):
        packages = subprocess.run(['dpkg-query', '-W', '-f=${Package}=${Version}\n'],
                                  capture_output=True, text=True, timeout=60, check=True).stdout
        (self.directory / 'dpkg.txt').write_text(packages, encoding='utf-8')
        for source in Path('/opt/diag').glob('*.txt'):
            shutil.copy2(source, self.directory / source.name)
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        write_json(self.directory / 'runtime.json', {
            'core_pattern': Path('/proc/sys/kernel/core_pattern').read_text().strip(),
            'core_limit': ['unlimited' if value == resource.RLIM_INFINITY else value
                           for value in (soft, hard)],
            'cpu_count': os.cpu_count(),
            'executables': {str(path): sha256(path) for path in executables},
        })

    def capture_maps(self, events_path):
        """At the first ready moment, list what each started child actually mapped."""
        if self.mapped is not None:
            return
        mapped = {}
        for row in read_rows(events_path):
            if row.get('event') != 'start':
                continue
            pid = row.get('pid')
            try:
                exe = os.readlink(f'/proc/{pid}/exe')
                lines = Path(f'/proc/{pid}/maps').read_text().splitlines()
            except OSError as exc:
                mapped[str(pid)] = {'error': str(exc)}
                continue
            paths = {fields[5] for fields in (line.split(None, 5) for line in lines)
                     if len(fields) == 6 and fields[5].startswith('/') and '.so' in fields[5]}
            mapped[str(pid)] = {'exe': exe, 'objects': sorted(paths)}
        self.mapped = mapped

    def write_maps(self):
        if self.mapped is None:
            return
        hashes = {}
        for entry in self.mapped.values():
            for path in [entry.get('exe'), *entry.get('objects', [])]:
                if path and path not in hashes:
                    try:
                        hashes[path] = sha256(path)
                    except OSError as exc:
                        hashes[path] = f'unavailable: {exc}'
        write_json(self.directory / 'loaded-objects.json', {'processes': self.mapped,
                                                            'sha256': hashes})


class ObservedDiscover:
    """Forward run_launch's readiness probe and its attributes; note the first ready moment."""

    def __init__(self, inner, on_ready):
        object.__setattr__(self, '_inner', inner)
        object.__setattr__(self, '_on_ready', on_ready)

    def __call__(self):
        ready = self._inner()
        if ready:
            self._on_ready()
        return ready

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


class Cores:
    """Match kernel cores to crashed children, analyse a bounded number, keep the first."""

    def __init__(self, directory, evidence, executables):
        self.directory = directory
        self.evidence = evidence
        self.executables = executables
        self.seen = set(self.listing())
        self.preexisting = sorted(self.seen)
        self.records = []
        self.analysed = 0
        self.slow_exit_analysed = 0
        self.kept = None
        self.analysis_s = 0.0

    def listing(self):
        try:
            return {path.name for path in self.directory.iterdir() if CORE_NAME.match(path.name)}
        except FileNotFoundError:
            return set()

    def executable_for(self, core):
        if len(self.executables) == 1:
            return self.executables[0]
        try:
            result = subprocess.run(['gdb', '-batch', '-nx', '-c', str(core)], capture_output=True,
                                    text=True, timeout=120, env=tool_env())
            match = re.search(r"Core was generated by `([^'\s]+)", result.stdout + result.stderr)
        except (OSError, subprocess.SubprocessError):
            match = None
        for candidate in self.executables:
            # psargs is truncated to 80 bytes; a prefix still identifies the executable.
            if match and str(candidate).startswith(match.group(1)):
                return candidate
        named = CORE_NAME.match(core.name)
        for candidate in self.executables:
            if named and candidate.name[:15] == named.group('comm'):
                return candidate
        return self.executables[0]

    def analyse(self, core, label):
        executable = self.executable_for(core)
        output = self.evidence / f'gdb-{label}.txt'
        command = ['gdb', '-batch', '-nx', '-ex', 'set pagination off', '-x', str(INSPECT),
                   str(executable), str(core)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                    env=tool_env())
            text = result.stdout + ('\n[stderr]\n' + result.stderr if result.stderr else '')
        except (OSError, subprocess.SubprocessError) as exc:
            text = f'gdb failed: {exc}\n'
        output.write_text(text, encoding='utf-8')
        crash = re.search(r'^CRASH_STACK: (.*)$', text, re.M)
        return {'executable': str(executable), 'gdb': output.name,
                'gdb_completed': '[diag_gdb_inspect] done' in text,
                'crash_stack': crash.group(1).split(' <- ')[:12] if crash else None,
                'teardown_frames': re.findall(r'^TEARDOWN_FRAME: (.*)$', text, re.M)[:20]}

    def keep(self, core, executable, label):
        target = self.evidence / f'core-{label}.gz'
        with open(core, 'rb') as source, gzip.open(target, 'wb', compresslevel=1) as sink:
            shutil.copyfileobj(source, sink, 1 << 20)
        copy = self.evidence / f'exe-{label}'
        shutil.copy2(executable, copy)
        self.kept = target.name
        return {'kept_core': target.name, 'kept_executable': copy.name, 'core_sha256': sha256(core)}

    def collect(self, iteration, exits, slow_exit_pids=()):
        began = time.monotonic()
        new = sorted(self.listing() - self.seen)
        self.seen.update(new)
        signalled = {pid: code for pid, code in exits if type(code) is int and code < 0}
        dumped = set()
        for name in new:
            pid = int(CORE_NAME.match(name).group('pid'))
            dumped.add(pid)
            path = self.directory / name
            label = f'{iteration}-{pid}'
            record = {'iteration': iteration, 'core': name, 'pid': pid,
                      'size': path.stat().st_size, 'returncode': signalled.get(pid),
                      'attributed_to_child_exit': pid in signalled,
                      'slow_exit_capture': pid in slow_exit_pids}
            # Stacks of a slow exit decide whether it was a deadlock, so they get their own budget.
            slow_exit = pid in slow_exit_pids and self.slow_exit_analysed < MAX_SLOW_EXIT_ANALYSED
            if self.analysed < MAX_ANALYSED or slow_exit:
                if slow_exit:
                    self.slow_exit_analysed += 1
                else:
                    self.analysed += 1
                record.update(self.analyse(path, label))
                if self.kept is None:
                    record.update(self.keep(path, Path(record['executable']), label))
            else:
                record['analysis'] = 'skipped: per-run analysis limit reached'
            path.unlink(missing_ok=True)
            self.records.append(record)
        # A child killed by a core-dumping signal without a core is a capture gap, not a pass.
        for pid, code in signalled.items():
            if pid not in dumped and -code in {int(value) for value in CORE_SIGNALS}:
                self.records.append({'iteration': iteration, 'pid': pid, 'returncode': code,
                                     'core': None})
        self.analysis_s += time.monotonic() - began

    def summary(self):
        return {'preexisting': self.preexisting, 'records': self.records,
                'analysed': self.analysed, 'slow_exit_analysed': self.slow_exit_analysed,
                'kept': self.kept,
                'analysis_s': round(self.analysis_s, 1)}


class SlowExitCapture:
    """Abort nodes still running SLOW_EXIT_S after SIGINT, so a hang leaves a core to inspect."""

    def __init__(self, events_path):
        self.events_path = events_path
        self.records = []
        self.pending = []

    def install(self):
        original = fleet.wait_for_launch_exit

        def wait_for_launch_exit(process, timeout=10.0):
            began = time.monotonic()
            try:
                process.wait(timeout=SLOW_EXIT_S)
                return
            except subprocess.TimeoutExpired:
                pass
            self.abort_running_children(time.monotonic() - began)
            # Keep the harness's own overall deadline unchanged.
            return original(process, timeout=max(0.0, timeout - (time.monotonic() - began)))

        fleet.wait_for_launch_exit = wait_for_launch_exit

    def abort_running_children(self, after_s):
        rows = read_rows(self.events_path)
        started = {row.get('pid') for row in rows if row.get('event') == 'start'}
        exited = {row.get('pid') for row in rows if row.get('event') == 'exit'}
        for pid in sorted(p for p in started - exited if type(p) is int):
            try:
                os.kill(pid, signal.SIGABRT)
            except ProcessLookupError:
                continue
            self.pending.append({'pid': pid, 'after_s': round(after_s, 2)})

    def take(self, iteration):
        """Attach this iteration's captures to it and return the captured pids."""
        captured = [{'iteration': iteration, **record} for record in self.pending]
        self.pending = []
        self.records.extend(captured)
        return {record['pid'] for record in captured}


class AsanReports:
    """Index ASan report files (asan.<pid>) by the iteration in which they appeared."""

    def __init__(self, directory):
        self.directory = directory
        self.seen = set()
        self.records = []

    def collect(self, iteration):
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.iterdir()):
            if path.name in self.seen:
                continue
            self.seen.add(path.name)
            text = path.read_text(encoding='utf-8', errors='replace')
            error = re.search(r'^==\d+==ERROR: AddressSanitizer: .*$', text, re.M)
            self.records.append({'iteration': iteration, 'file': path.name,
                                 'error': error.group(0) if error else None})


def read_text_or_none(path):
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        return None


def build_shim(out_dir):
    target = Path(out_dir) / 'libsegv_backtrace.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O0', '-g', '-o', str(target),
                    str(ROOT / 'tests' / 'segv_backtrace.c')],
                   check=True, capture_output=True, timeout=60)
    return target


def write_control_main(main_cpp, identity_dir):
    generated = main_cpp.read_text(encoding='utf-8')
    header = re.search(r'#include "([^"]+_node\.hpp)"', generated)
    node_type = re.search(r'std::make_shared<([\w:]+)>\(\)', generated)
    if not (header and node_type):
        raise RuntimeError('Generated main.cpp no longer matches the control rewrite')
    control = CONTROL_MAIN.format(header=header.group(1), node_type=node_type.group(1))
    (identity_dir / 'generated-main.cpp').write_text(generated, encoding='utf-8')
    (identity_dir / 'control-main.cpp').write_text(control, encoding='utf-8')
    main_cpp.write_text(control, encoding='utf-8')


def prepare_fleet(work, control_main, identity_dir):
    subprocess.run([sys.executable, str(ROOT / 'scripts/create_package.py'), FLEET_PACKAGE,
                    '--type', 'cpp', '--robots', '2', '--dest', str(work / 'src')],
                   check=True, timeout=60)
    config = work / 'src' / FLEET_PACKAGE / 'config/params.yaml'
    config.write_text(config.read_text(encoding='utf-8').replace('50.0', '17.0'), encoding='utf-8')
    if control_main:
        write_control_main(work / 'src' / FLEET_PACKAGE / 'src/main.cpp', identity_dir)
    subprocess.run(['colcon', 'build', '--event-handlers', 'console_direct+', '--cmake-args',
                    '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release'],
                   cwd=work, check=True, timeout=600)
    executable = work / 'install' / FLEET_PACKAGE / 'lib' / FLEET_PACKAGE / f'{FLEET_PACKAGE}_node'
    if not executable.is_file():
        raise RuntimeError('Generated C++ executable is missing: ' + str(executable))
    return [executable], lambda: fleet.verify_fleet(work, FLEET_PACKAGE, True,
                                                    drop_transition_events=True)


def prepare_minimal(work, identity_dir):
    share = work / 'install' / MINIMAL_PACKAGE / 'share' / MINIMAL_PACKAGE / 'launch'
    share.mkdir(parents=True)
    (share / 'fleet.launch.py').write_text(MINIMAL_LAUNCH, encoding='utf-8')
    (identity_dir / 'minimal.launch.py').write_text(MINIMAL_LAUNCH, encoding='utf-8')
    # run_launch sources this file; the ROS environment is already sourced.
    (work / 'install' / 'setup.bash').write_text(':\n', encoding='utf-8')
    prefix = Path('/opt/ros') / os.environ.get('ROS_DISTRO', 'humble') / 'lib' / 'demo_nodes_cpp'
    executables = [prefix / 'talker', prefix / 'listener']
    missing = [str(path) for path in executables if not path.is_file()]
    if missing:
        raise RuntimeError('demo_nodes_cpp executables are missing: ' + ', '.join(missing))
    return executables, lambda: verify_minimal(work)


def verify_minimal(work):
    """Same launch wrapper and SIGINT path as the fleet; readiness mirrors its settle rule."""
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rcl_interfaces.msg import ParameterType
    from rcl_interfaces.srv import GetParameters

    context = Context()
    rclpy.init(context=context)
    observer = executor = None
    try:
        observer = Node('minimal_observer_' + uuid.uuid4().hex, context=context)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(observer)
        clients = [observer.create_client(GetParameters, f'/{name}/get_parameters')
                   for name in ('talker', 'listener')]

        def discover():
            executor.spin_once(timeout_sec=0.05)
            missing = [client.srv_name for client in clients if not client.service_is_ready()]
            chatter = {'publishers': observer.count_publishers('/chatter'),
                       'subscribers': observer.count_subscribers('/chatter')}
            discover.last_observation = {'missing_services': missing, 'chatter': chatter}
            if missing or not (chatter['publishers'] and chatter['subscribers']):
                discover.active_since = None
                return set()
            now = time.monotonic()
            if discover.active_since is None:
                discover.active_since = now
                return set()
            stable_for = now - discover.active_since
            discover.last_observation['stable_for'] = stable_for
            if stable_for < 0.2:
                return set()
            for client in clients:
                values = fleet.read_service(client, GetParameters.Request(names=['use_sim_time']),
                                            executor, deadline=discover.deadline).values
                if len(values) != 1 or values[0].type != ParameterType.PARAMETER_BOOL:
                    raise RuntimeError('Demo node parameter service returned an unexpected value')
            return {'minimal: ready'}

        discover.last_observation = {}
        discover.deadline = None
        discover.active_since = None
        children = fleet.run_launch(work, MINIMAL_PACKAGE, discover, expected_startups=0)
        return {'package': MINIMAL_PACKAGE, 'children': children, 'status': 'pass'}
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


def versions():
    try:
        result = subprocess.run(
            ['dpkg-query', '-W', '-f=${Package}=${Version}\n', 'ros-humble-rclcpp',
             'ros-humble-rclcpp-lifecycle', 'ros-humble-rmw-fastrtps-cpp', 'ros-humble-fastrtps',
             'ros-humble-launch-ros'],
            capture_output=True, text=True, timeout=30)
        return result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        return [f'unavailable: {exc}']


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--minutes', type=float, default=30.0, help='iteration-time budget')
    parser.add_argument('--mode', choices=('fleet', 'minimal'), default='fleet')
    parser.add_argument('--shim', choices=('on', 'off'), default='off')
    parser.add_argument('--control-main', choices=('on', 'off'), default='off')
    parser.add_argument('--asan', choices=('on', 'off'), default='off')
    parser.add_argument('--label', default='unlabelled')
    args = parser.parse_args(argv)
    if args.mode == 'minimal' and args.control_main == 'on':
        parser.error('--control-main applies to the generated fleet only')
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    budget_s = args.minutes * 60
    evidence = Path(os.environ.get('EVIDENCE_DIR', '/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    identity = Identity(evidence)
    upgrade = Path('/opt/diag/upgrade.txt')
    context = {
        'label': args.label, 'mode': args.mode, 'shim': args.shim,
        'control_main': args.control_main,
        'upgrade': upgrade.read_text().strip() if upgrade.exists() else None,
        'fastdds_variant': read_text_or_none(Path('/opt/diag/fastdds-variant.txt')),
        'fastdds_lib_sha256': read_text_or_none(Path('/opt/diag/fastdds-lib.sha256')),
        'asan': args.asan, 'slow_exit_s': SLOW_EXIT_S,
        'diag_commit': os.environ.get('GITHUB_SHA'), 'base_commit': os.environ.get('BASE_SHA'),
        'distro': os.environ.get('ROS_DISTRO'),
        'rmw': os.environ.get('RMW_IMPLEMENTATION', 'default'), 'packages': versions(),
        'variant': ('verify_fleet(lifecycle=True, drop_transition_events=True)'
                    if args.mode == 'fleet' else 'demo_nodes_cpp talker + listener'),
        'scope': ('Launch-forwarded SIGINT after the observed settle point; '
                  'process exits come from launch events'),
    }

    def on_term(signum, frame):
        raise Interrupted(signal.Signals(signum).name)

    signal.signal(signal.SIGTERM, on_term)
    tally = cores = slow_exits = asan_reports = None
    try:
        with tempfile.TemporaryDirectory(prefix='generated-fleet-') as temporary:
            work = Path(temporary)
            if args.mode == 'fleet':
                executables, run_once = prepare_fleet(work, args.control_main == 'on',
                                                      identity.directory)
                package = FLEET_PACKAGE
            else:
                executables, run_once = prepare_minimal(work, identity.directory)
                package = MINIMAL_PACKAGE
            identity.record_environment(executables)
            if args.asan == 'on':
                # The Fast DDS build is instrumented; the executables are not, so preload the runtime.
                (evidence / 'asan').mkdir(exist_ok=True)
                os.environ['LD_PRELOAD'] = str(ASAN_RUNTIME)
                os.environ['ASAN_OPTIONS'] = ('detect_leaks=0:abort_on_error=1:verify_asan_link_order=0:'
                                              'log_path=' + str(evidence / 'asan' / 'asan'))
            if args.shim == 'on':
                # Inherited by the launch supervisor and every node; a diagnostic, not a fix.
                os.environ['LD_PRELOAD'] = str(build_shim(work))
            events = work / (package + '-events.jsonl')
            original_run_launch = fleet.run_launch

            def run_launch(work_dir, name, discover, *rest, **options):
                observed = ObservedDiscover(discover, lambda: identity.capture_maps(events))
                return original_run_launch(work_dir, name, observed, *rest, **options)

            slow_exits = SlowExitCapture(events)
            slow_exits.install()
            asan_reports = AsanReports(evidence / 'asan')

            fleet.run_launch = run_launch
            cores = Cores(CORE_DIR, evidence, executables)
            tally = Tally(budget_s)
            print(f'[{args.label}] repeating {context["variant"]} for {budget_s:.0f} s of '
                  f'iteration time', flush=True)
            while tally.iteration_s < budget_s:
                buffer = io.StringIO()
                error = None
                began = time.monotonic()
                try:
                    with contextlib.redirect_stdout(buffer):
                        run_once()
                except Interrupted:
                    raise
                except Exception:
                    error = traceback.format_exc()
                duration = time.monotonic() - began
                exits = read_exits(events)
                outcome = tally.record(exits, None if error is None else
                                       error.strip().splitlines()[-1], duration)
                if tally.attempted == 1:
                    identity.write_maps()
                captured = slow_exits.take(tally.attempted)
                cores.collect(tally.attempted, exits, captured)
                asan_reports.collect(tally.attempted)
                if outcome != 'pass' and len(tally.failure_logs) < MAX_FAILURE_LOGS:
                    name = f'iter-{tally.attempted}.log'
                    detail = '\n'.join([f'outcome: {outcome}', f'exits: {exits}', error or '',
                                        buffer.getvalue()])
                    (evidence / name).write_text(demangle(detail), encoding='utf-8')
                    tally.failure_logs.append({'iteration': tally.attempted, 'outcome': outcome,
                                               'file': name})
                    print(f'iteration {tally.attempted}: {outcome} {exits}', flush=True)
                if tally.attempted % PARTIAL_EVERY == 0:
                    partial = {**context, **tally.summary(False), 'cores': cores.summary(),
                               'slow_exit_captures': slow_exits.records,
                               'asan_reports': asan_reports.records}
                    write_json(evidence / 'summary.partial.json', partial)
                    print(f'progress: {tally.attempted} iterations, '
                          f'{partial["confirmed_clean_child_exits"]} clean exits, '
                          f'{tally.segv} SIGSEGV, {sum(tally.other_abnormal.values())} other, '
                          f'{sum(tally.harness_errors.values())} harness errors, '
                          f'{partial["iteration_budget_used_s"]} s', flush=True)
    except Interrupted as exc:
        if tally is not None:
            tally.interrupted = str(exc)
    except Exception:
        (evidence / 'setup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if tally is not None:
            tally.interrupted = 'exception: see setup-error.log'
    finally:
        if tally is None:
            final = {**context, 'verdict': 'inconclusive', 'reason': 'setup did not complete'}
        else:
            final = {**context, **tally.summary(True),
                     'cores': cores.summary() if cores is not None else None,
                     'slow_exit_captures': slow_exits.records if slow_exits is not None else None,
                     'asan_reports': asan_reports.records if asan_reports is not None else None}
        write_json(evidence / 'summary.json', final)
        print(json.dumps(final, indent=2), flush=True)
    return {'reproduced': 1, 'abnormal_exit': 1, 'inconclusive': 2,
            'not_reproduced': 0}[final['verdict']]


if __name__ == '__main__':
    raise SystemExit(main())
