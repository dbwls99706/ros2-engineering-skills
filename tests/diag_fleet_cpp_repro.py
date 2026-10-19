#!/usr/bin/env python3
"""Temporary long-run reproduction of the generated C++ fleet SIGINT exit (diagnostic branch only).

Builds the generated C++ fleet once, then repeats check_generated_fleet.verify_fleet with
transition events discarded (the variant that once exited -11 on Humble) until the time
budget is spent. Every count comes from the launch process-exit events, never from an
assumed number of nodes. Iterations that end in a harness error are excluded from the
clean-exit sample. A partial summary is written every 25 iterations, and the final summary
is written on normal exit, on an exception, and on SIGTERM.

Verdicts: reproduced (any -11), abnormal_exit (any other non-zero child exit),
inconclusive (harness errors or an interrupted budget), not_reproduced.
"""

import collections
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

import check_generated_cpp_shutdown as stress  # noqa: E402
import check_generated_fleet as fleet  # noqa: E402

PACKAGE = 'fleet_cpp_probe'
MAX_FAILURE_LOGS = 10
PARTIAL_EVERY = 25


class Interrupted(Exception):
    pass


def read_exits(events_path):
    """Return (pid, returncode) for every recorded child exit; tolerate a partial file."""
    exits = []
    try:
        lines = events_path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return exits
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('event') == 'exit':
            exits.append((row.get('pid'), row.get('returncode')))
    return exits


class Tally:
    def __init__(self, budget_s):
        self.budget_s = budget_s
        self.started = time.monotonic()
        self.attempted = 0
        self.passed = 0
        self.clean_exits = 0
        self.excluded_clean_exits = 0
        self.segv = 0
        self.other_abnormal = collections.Counter()
        self.harness_errors = collections.Counter()
        self.failure_logs = []
        self.interrupted = None

    def record(self, exits, error):
        self.attempted += 1
        codes = [code for _, code in exits]
        abnormal = [code for code in codes if code != 0]
        for code in abnormal:
            if code == -signal.SIGSEGV:
                self.segv += 1
            else:
                self.other_abnormal[str(code)] += 1
        clean = sum(code == 0 for code in codes)
        if error is None and not abnormal:
            self.passed += 1
            self.clean_exits += clean
            return 'pass'
        if abnormal:
            # Clean siblings of a crashed child are real clean exits from a completed launch.
            self.clean_exits += clean
            return 'segv' if -signal.SIGSEGV in abnormal else 'abnormal'
        self.excluded_clean_exits += clean
        self.harness_errors[error.splitlines()[0][:200]] += 1
        return 'harness_error'

    def summary(self, final):
        elapsed = time.monotonic() - self.started
        if self.segv:
            verdict = 'reproduced'
        elif self.other_abnormal:
            verdict = 'abnormal_exit'
        elif self.harness_errors or self.interrupted or (final and elapsed < self.budget_s):
            verdict = 'inconclusive'
        else:
            verdict = 'not_reproduced'
        confirmed = self.clean_exits + self.segv + sum(self.other_abnormal.values())
        return {
            'verdict': verdict if final else 'in_progress',
            'attempted_iterations': self.attempted,
            'passed_iterations': self.passed,
            'confirmed_child_exits': confirmed,
            'confirmed_clean_child_exits': self.clean_exits,
            'segv_count': self.segv,
            'other_abnormal_exits': dict(self.other_abnormal),
            'harness_errors': dict(self.harness_errors),
            'clean_exits_excluded_with_harness_errors': self.excluded_clean_exits,
            'failure_logs': self.failure_logs,
            'interrupted': self.interrupted,
            'elapsed_s': round(elapsed, 1),
            'budget_s': self.budget_s,
        }


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def versions():
    try:
        result = subprocess.run(
            ['dpkg-query', '-W', '-f=${Package}=${Version}\n', 'ros-humble-rclcpp',
             'ros-humble-rclcpp-lifecycle', 'ros-humble-rmw-fastrtps-cpp', 'ros-humble-fastrtps'],
            capture_output=True, text=True, timeout=30)
        return result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        return [f'unavailable: {exc}']


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    budget_s = float(argv[0]) * 60 if argv else 1800.0
    evidence = Path(os.environ.get('EVIDENCE_DIR', '/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    context = {'commit': os.environ.get('GITHUB_SHA'), 'distro': os.environ.get('ROS_DISTRO'),
               'rmw': os.environ.get('RMW_IMPLEMENTATION', 'default'), 'packages': versions(),
               'variant': 'verify_fleet(lifecycle=True, drop_transition_events=True)',
               'scope': 'Installed generated C++ fleet, two lifecycle nodes, launch-forwarded SIGINT'}

    def on_term(signum, frame):
        raise Interrupted(signal.Signals(signum).name)

    signal.signal(signal.SIGTERM, on_term)
    tally = None
    shim = None
    try:
        with tempfile.TemporaryDirectory(prefix='generated-fleet-') as temporary:
            work = Path(temporary)
            subprocess.run([sys.executable, str(ROOT / 'scripts/create_package.py'), PACKAGE,
                            '--type', 'cpp', '--robots', '2', '--dest', str(work / 'src')],
                           check=True, timeout=60)
            config = work / 'src' / PACKAGE / 'config/params.yaml'
            config.write_text(config.read_text(encoding='utf-8').replace('50.0', '17.0'),
                              encoding='utf-8')
            subprocess.run(['colcon', 'build', '--event-handlers', 'console_direct+', '--cmake-args',
                            '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release'],
                           cwd=work, check=True, timeout=600)
            shim = stress.build_shim(work)
            if shim is not None:
                # Inherited by the launch supervisor and both nodes; a diagnostic, not a fix.
                os.environ['LD_PRELOAD'] = str(shim)
            events = work / (PACKAGE + '-events.jsonl')
            tally = Tally(budget_s)
            print(f'Repeating {context["variant"]} for {budget_s:.0f} s', flush=True)
            while time.monotonic() - tally.started < budget_s:
                buffer = io.StringIO()
                error = None
                try:
                    with contextlib.redirect_stdout(buffer):
                        fleet.verify_fleet(work, PACKAGE, True, drop_transition_events=True)
                except Interrupted:
                    raise
                except Exception:
                    error = traceback.format_exc()
                exits = read_exits(events)
                outcome = tally.record(exits, None if error is None else error.strip().splitlines()[-1])
                if outcome != 'pass' and len(tally.failure_logs) < MAX_FAILURE_LOGS:
                    name = f'iter-{tally.attempted}.log'
                    detail = '\n'.join([f'outcome: {outcome}', f'exits: {exits}', error or '',
                                        buffer.getvalue()])
                    (evidence / name).write_text(stress.demangle(detail), encoding='utf-8')
                    tally.failure_logs.append({'iteration': tally.attempted, 'outcome': outcome,
                                               'file': name})
                    print(f'iteration {tally.attempted}: {outcome} {exits}', flush=True)
                if tally.attempted % PARTIAL_EVERY == 0:
                    partial = {**context, 'backtrace_shim': shim is not None, **tally.summary(False)}
                    write_json(evidence / 'summary.partial.json', partial)
                    print(f'progress: {tally.attempted} iterations, '
                          f'{partial["confirmed_clean_child_exits"]} clean exits, '
                          f'{tally.segv} SIGSEGV, {sum(tally.other_abnormal.values())} other, '
                          f'{sum(tally.harness_errors.values())} harness errors, '
                          f'{partial["elapsed_s"]} s', flush=True)
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
            final = {**context, 'backtrace_shim': shim is not None, **tally.summary(True)}
        write_json(evidence / 'summary.json', final)
        print(json.dumps(final, indent=2), flush=True)
    return {'reproduced': 1, 'abnormal_exit': 1, 'inconclusive': 2,
            'not_reproduced': 0}[final['verdict']]


if __name__ == '__main__':
    raise SystemExit(main())
