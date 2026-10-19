"""Synthetic checks of the C++ shutdown stress harness; live trials are a separate ROS gate."""

import shutil
import signal
import subprocess

import pytest

from tests import check_generated_cpp_shutdown as stress


def record(trial, sig, outcome, scenario='settled'):
    return {'trial': trial, 'scenario': scenario, 'signal': sig, 'returncode': 0, 'outcome': outcome}


def test_plan_alternates_sigint_timings_and_ends_with_one_sigterm():
    trials = stress.plan(4)
    assert trials[:4] == [('immediate', signal.SIGINT), ('settled', signal.SIGINT),
                          ('immediate', signal.SIGINT), ('settled', signal.SIGINT)]
    assert trials[-1] == ('settled', signal.SIGTERM)
    with pytest.raises(ValueError):
        stress.plan(0)


@pytest.mark.parametrize('code, outcome', [(0, 'clean'), (-11, 'signal:SIGSEGV'),
                                           (-6, 'signal:SIGABRT'), (1, 'exit:1'), (-200, 'signal:200')])
def test_exit_status_is_reported_not_reinterpreted(code, outcome):
    assert stress.classify(code) == outcome


def test_any_unclean_sigint_trial_fails_the_gate():
    records = [record(1, 'SIGINT', 'clean'), record(2, 'SIGINT', 'signal:SIGSEGV'),
               record(3, 'SIGTERM', 'clean')]
    summary = stress.summarize(records, require_sigterm=False)
    assert summary['status'] == 'fail'
    assert summary['sigint_trials'] == 2 and summary['sigint_clean'] == 1
    assert summary['failures'] == [{'trial': 2, 'scenario': 'settled', 'signal': 'SIGINT',
                                    'outcome': 'signal:SIGSEGV'}]


def test_sigterm_is_recorded_until_required():
    records = [record(1, 'SIGINT', 'clean'), record(2, 'SIGTERM', 'exit:1')]
    recorded = stress.summarize(records, require_sigterm=False)
    assert recorded['status'] == 'pass'
    assert recorded['sigterm_outcome'] == 'exit:1'
    assert recorded['recorded_only'][0]['signal'] == 'SIGTERM'
    assert stress.summarize(records, require_sigterm=True)['status'] == 'fail'


def test_missing_compiler_drops_only_the_backtrace(tmp_path):
    assert stress.build_shim(tmp_path, compiler='/nonexistent/cc') is None


@pytest.mark.skipif(shutil.which('cc') is None, reason='no C compiler')
def test_shim_preserves_the_original_fatal_signal(tmp_path):
    shim = stress.build_shim(tmp_path)
    assert shim is not None
    victim = tmp_path / 'victim.c'
    victim.write_text('int main(void) { volatile int *p = 0; return *p; }\n', encoding='utf-8')
    subprocess.run(['cc', '-O0', '-o', str(tmp_path / 'victim'), str(victim)], check=True, timeout=60)
    result = subprocess.run([str(tmp_path / 'victim')], capture_output=True, text=True, timeout=10,
                            env={'LD_PRELOAD': str(shim)})
    assert result.returncode == -signal.SIGSEGV
    assert '[segv_backtrace] fatal signal' in result.stderr


def test_demangle_falls_back_to_raw_text(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('c++filt')
    monkeypatch.setattr(stress.subprocess, 'run', missing)
    assert stress.demangle('_ZN6rclcpp8shutdownEv') == '_ZN6rclcpp8shutdownEv'
