"""Synthetic Docker transport tests; real ROS behavior is gated by the distro jobs."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('timeout'),
                                reason='Linux/GNU timeout runner contract')


@pytest.fixture
def docker_runner(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    docker = binary / 'docker'
    docker.write_text('''#!/usr/bin/env bash
printf '%s\\t' "$@" >> "$DOCKER_CALLS"
printf '\\n' >> "$DOCKER_CALLS"
phase=other
case "${1:-}:${2:-}" in
    buildx:create) phase=create ;;
    buildx:build) phase=build ;;
    image:inspect) phase=inspect ;;
    run:*) phase=run ;;
esac
if [[ "$phase" == build ]]; then echo '#18 DONE -- synthetic output, not command success'; fi
if [[ "$phase" == "$DOCKER_FAIL" ]]; then exit "$DOCKER_CODE"; fi
printf 'synthetic Docker %s\\n' "$*"
''', encoding='utf-8')
    docker.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'DOCKER_CALLS': str(tmp_path / 'calls.jsonl'), 'ROS_TEST_LOG_DIR': str(tmp_path / 'logs')}

    def run(*distros, failure='', code=23):
        result = subprocess.run(['bash', str(ROOT / 'tests/run_ros2_tests.sh'), *distros],
                                env={**env, 'DOCKER_FAIL': failure, 'DOCKER_CODE': str(code)},
                                capture_output=True, text=True, timeout=15)
        path = Path(env['DOCKER_CALLS'])
        calls = [line.rstrip('\t').split('\t') for line in path.read_text().splitlines()] if path.exists() else []
        return result, calls, tmp_path / 'logs'
    return run


def test_success_requires_build_load_inspect_and_runtime(docker_runner):
    result, calls, logs = docker_runner('humble')
    assert result.returncode == 0, result.stderr
    build = next(call for call in calls if call[:2] == ['buildx', 'build'])
    assert '--load' in build and '--builder' in build
    create = next(call for call in calls if call[:2] == ['buildx', 'create'])
    assert create[create.index('--driver') + 1] == 'docker-container'
    run = next(call for call in calls if call[0] == 'run')
    assert '--init' in run and run[run.index('--network') + 1] == 'none'
    assert run[-2:] == ['bash', 'tests/run_ros2_container_tests.sh']
    assert 'timeout' in run and '--kill-after=10s' in run
    assert '[PASS]' in result.stdout
    assert (logs / 'humble/exit-code.txt').read_text().strip() == '0'
    assert any(call[:2] == ['rm', '-f'] for call in calls)
    assert any(call[:2] == ['buildx', 'rm'] for call in calls)


@pytest.mark.parametrize('phase,code', [
    ('create', 21), ('build', 22), ('build', 124),
    ('inspect', 23), ('run', 24), ('run', 124),
])
def test_failures_and_timeouts_are_never_passes(docker_runner, phase, code):
    result, calls, logs = docker_runner('jazzy', failure=phase, code=code)
    assert result.returncode != 0
    assert '[PASS]' not in result.stdout
    assert (logs / 'jazzy/exit-code.txt').read_text().strip() == str(code)
    if phase != 'run':
        assert not any(call[0] == 'run' for call in calls)
    if phase == 'build':
        assert '#18 DONE' in (logs / 'jazzy/build.log').read_text()
    assert any(call[:2] == ['rm', '-f'] for call in calls)
    assert any(call[:2] == ['buildx', 'rm'] for call in calls)
    assert any(call[0] == 'logs' for call in calls)


def test_all_requested_distros_are_attempted_on_failure(docker_runner):
    result, calls, logs = docker_runner('humble', 'jazzy', failure='run')
    assert result.returncode == 1
    assert len([call for call in calls if call[0] == 'run']) == 2
    assert (logs / 'humble/exit-code.txt').is_file()
    assert (logs / 'jazzy/exit-code.txt').is_file()


def test_unknown_distro_is_rejected_before_docker(docker_runner):
    result, calls, _ = docker_runner('humble', 'not-a-distro')
    assert result.returncode == 2
    assert not calls


def test_default_runner_keeps_five_distros(docker_runner):
    result, calls, _ = docker_runner()
    assert result.returncode == 0
    assert len([call for call in calls if call[0] == 'run']) == 5


def test_docker_build_does_not_execute_runtime_tests():
    text = (ROOT / 'tests/Dockerfile.ros2-test').read_text()
    assert 'RUN python3 -m pytest' not in text
    assert 'RUN if' not in text.split('COPY . .', 1)[1]
    assert 'CMD ["bash", "tests/run_ros2_container_tests.sh"]' in text
    assert '|| pip3' not in text
    assert "'hypothesis>=6.100,<7'" in text
    assert 'ci-overlay.repos' in text


def test_runtime_runner_retains_every_gate_and_explicit_rolling_exclusions():
    text = (ROOT / 'tests/run_ros2_container_tests.sh').read_text()
    for command in ('python3 -m pytest tests/', 'colcon build', 'colcon test ',
                    'colcon test-result --verbose', 'python3 -m pytest test/',
                    'launch_validator.py', 'qos_checker.py', 'smoke_test_nodes.sh', 'qos_roundtrip.py'):
        assert command in text
    for package in ('test_cpp_pkg', 'test_py_pkg', 'test_iface_pkg', 'test_hw_pkg'):
        assert f'create_package.py {package} ' in text
    assert 'test_pkgs=(test_iface_pkg test_hw_pkg)' in text
    assert '--deselect test/test_test_py_pkg.py::test_node_creation' in text
    assert '[[ "$ROS_DISTRO" != rolling ]]' in text


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled', 'skipped', 'missing'])
def test_workflow_summary_does_not_accept_non_success(outcome):
    path = ROOT / '.github/workflows/test.yml'
    if not path.is_file():
        pytest.skip('workflow absent in installed bundle')
    workflow = yaml.safe_load(path.read_text())
    jobs = workflow['jobs']
    gate = jobs['ci-gate']
    results = {name: {'result': 'success'} for name in gate['needs']}
    if outcome == 'missing':
        results.pop('ros2-integration')
    else:
        results['ros2-integration']['result'] = outcome
    assert gate['if'] == 'always()'
    script = gate['steps'][0]['run'].split("<<'PYCODE'\n", 1)[1].rsplit('PYCODE', 1)[0]
    result = subprocess.run([sys.executable, '-S', '-c', script], capture_output=True,
                            env={**os.environ, 'JOB_RESULTS': json.dumps(results)}, timeout=5)
    assert (result.returncode == 0) == (outcome == 'success')
    integration = jobs['ros2-integration']
    assert integration['strategy']['matrix']['ros_distro'] == ['humble', 'jazzy', 'kilted', 'lyrical', 'rolling']
    assert integration['timeout-minutes'] == 30
    assert 'run_ros2_tests.sh' in integration['steps'][1]['run']
    assert integration['steps'][2]['if'] == 'always()'
    assert all(not step.get('continue-on-error') for step in integration['steps'])
