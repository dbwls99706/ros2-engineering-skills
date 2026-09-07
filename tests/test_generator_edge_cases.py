"""Executable regressions for generator inputs, filesystem scope, and packaging."""

import ast
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts import create_package as generator
from scripts.launch_validator import validate_file

SCRIPT = Path(generator.__file__)


def generate(dest, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), 'edge_bot', '--dest', str(dest), *extra],
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize('kind', ['cpp', 'python', 'interfaces', 'hardware_interface'])
@pytest.mark.parametrize('place', ['root', 'nested', 'dangling'])
def test_force_never_follows_package_symlinks(tmp_path, kind, place):
    dest = tmp_path / 'workspace'
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'package.xml'
    sentinel.write_text('original user data', encoding='utf-8')
    dest.mkdir()
    package = dest / 'edge_bot'
    if place == 'root':
        package.symlink_to(outside, target_is_directory=True)
    else:
        package.mkdir()
        (package / 'package.xml').symlink_to(sentinel if place == 'nested' else outside / 'new.xml')
    before = sorted(str(p.relative_to(package)) for p in package.rglob('*'))
    result = generate(dest, '--type', kind, '--force')
    assert result.returncode != 0, result.stdout
    assert 'symlink' in result.stderr.lower()
    assert sentinel.read_text(encoding='utf-8') == 'original user data'
    assert not (outside / 'new.xml').exists()
    assert before == sorted(str(p.relative_to(package)) for p in package.rglob('*'))


@pytest.mark.parametrize('field,value', [('name', 'edge_bot\n'), ('email', 'dev@example.com\n'),
                                         ('maintainer', 'Dev\nraise RuntimeError("injected")')])
def test_invalid_metadata_rejected_before_writing(tmp_path, field, value):
    dest = tmp_path / 'not-created'
    args = [sys.executable, str(SCRIPT), value if field == 'name' else 'edge_bot',
            '--type', 'python', '--dest', str(dest)]
    if field == 'email':
        args += ['--maintainer-email', value]
    elif field == 'maintainer':
        args += ['--maintainer-name', value]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert not dest.exists()


@pytest.mark.parametrize('lifecycle', [False, True])
def test_python_installs_every_generated_launch(tmp_path, monkeypatch, lifecycle):
    extra = ['--type', 'python', '--robots', '2'] + (['--lifecycle'] if lifecycle else [])
    assert generate(tmp_path, *extra).returncode == 0
    package = tmp_path / 'edge_bot'
    monkeypatch.chdir(package)
    with patch('setuptools.setup') as setup:
        runpy.run_path(str(package / 'setup.py'), run_name='test_generated_setup')
    installed = dict(setup.call_args.kwargs['data_files'])['share/edge_bot/launch']
    assert set(installed) == {'launch/bringup.launch.py', 'launch/fleet.launch.py'}


def test_lifecycle_fleet_has_required_namespace_and_scoped_transitions():
    tree = ast.parse(generator._generate_fleet_launch('edge_bot', 2, lifecycle=True))
    calls = [item for item in ast.walk(tree) if isinstance(item, ast.Call)]
    lifecycle_nodes = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == 'LifecycleNode']
    assert len(lifecycle_nodes) == 2
    assert all(any(k.arg == 'namespace' for k in c.keywords) for c in lifecycle_nodes)
    changes = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == 'ChangeState']
    assert len(changes) == 4
    for change in changes:
        matcher = next(k.value for k in change.keywords if k.arg == 'lifecycle_node_matcher')
        assert isinstance(matcher, ast.Call) and matcher.func.id == 'matches_action'


@pytest.mark.parametrize('arguments,missing', [("package='p', executable='n'", True),
                                               ("package='p', executable='n', namespace=''", False),
                                               ("package='p', executable='n', **kwargs", False)])
def test_launch_validator_recognizes_required_lifecycle_namespace(tmp_path, arguments, missing):
    path = tmp_path / 'probe.launch.py'
    path.write_text('from launch_ros.actions import LifecycleNode\n'
                    'def generate_launch_description():\n'
                    f'    return LifecycleNode({arguments})\n', encoding='utf-8')
    findings = validate_file(str(path))
    assert any(i.severity == 'error' and "'namespace'" in i.message for i in findings) is missing
