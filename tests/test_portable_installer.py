"""Portable installs never register hooks or replace unrelated files."""

import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from scripts import install_skill as installer
from tests.test_skill_contract import bundle as contract_bundle  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(request):
    bundle = request.getfixturevalue('contract_bundle')
    (bundle / 'README.md').write_text('# Test fixture\n')
    (bundle / 'requirements.txt').write_text('PyYAML>=6,<7\n')
    for folder in ('evals', 'examples'):
        (bundle / folder).mkdir()
    for name in ('CONTRIBUTING.md', 'SECURITY.md', 'ROADMAP.md', 'CHANGELOG.md'):
        (bundle / name).write_text('# Synthetic fixture\n')
    shutil.copy2(ROOT / 'scripts/validate_skill.py', bundle / 'scripts/validate_skill.py')
    return bundle


def target(source):
    return source.parent / 'installed' / installer.NAME


def test_copy_is_knowledge_only(source):
    out = installer.install(source, target(source))
    assert out['hooks_installed'] is False
    assert (target(source) / 'agents/openai.yaml').is_file()
    assert not (target(source) / '.claude-plugin').exists()
    assert not (target(source) / 'hooks').exists()


def test_dry_run_changes_nothing(source):
    assert installer.install(source, target(source), dry_run=True)['status'] == 'dry-run'
    assert not target(source).parent.exists()


def test_existing_install_needs_force(source):
    installer.install(source, target(source))
    with pytest.raises(ValueError, match='--force'):
        installer.install(source, target(source))


def test_force_replaces_only_skill(source):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'stale.txt').write_text('stale')
    sibling = dst.parent / 'keep.txt'
    sibling.write_text('keep')
    installer.install(source, dst, force=True)
    assert not (dst / 'stale.txt').exists()
    assert sibling.read_text() == 'keep'


def test_validation_failure_preserves_previous_install(source):
    dst = target(source)
    installer.install(source, dst)
    previous = (dst / 'SKILL.md').read_text()
    (source / 'SKILL.md').write_text('broken')
    with pytest.raises(ValueError, match='failed validation'):
        installer.install(source, dst, force=True)
    assert (dst / 'SKILL.md').read_text() == previous


def test_rename_failure_restores_previous_install(source, monkeypatch):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'keep.txt').write_text('previous')
    rename = Path.rename

    def fail_final(path, to):
        if '.skill-install-' in str(path) and path.name == installer.NAME:
            raise OSError('cannot rename staging')
        return rename(path, to)

    monkeypatch.setattr(Path, 'rename', fail_final)
    with pytest.raises(OSError, match='cannot rename'):
        installer.install(source, dst, force=True)
    assert (dst / 'keep.txt').read_text() == 'previous'


@pytest.mark.parametrize('place', ['source', 'inside', 'parent', 'wrong-name'])
def test_unsafe_destinations(source, place):
    dst = {'source': source, 'inside': source / 'nested' / installer.NAME,
           'parent': source.parent, 'wrong-name': source.parent / 'wrong'}[place]
    with pytest.raises(ValueError):
        installer.install(source, dst, force=True)


def test_force_does_not_replace_unrelated_directory(source):
    dst = target(source)
    dst.mkdir(parents=True)
    (dst / 'private.txt').write_text('preserve')
    with pytest.raises(ValueError, match='not a skill'):
        installer.install(source, dst, force=True)
    assert (dst / 'private.txt').read_text() == 'preserve'


def test_target_symlink_is_never_followed(source):
    dst = target(source)
    dst.parent.mkdir()
    other = source.parent / 'other'
    other.mkdir()
    dst.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        installer.install(source, dst, force=True)
    assert dst.is_symlink() and other.is_dir()


def test_nested_symlinks_rejected(source):
    (source / 'references/link').symlink_to(source / 'SKILL.md')
    with pytest.raises(ValueError, match='symlinks'):
        installer.install(source, target(source))


def test_incomplete_bundle_rejected(source):
    (source / 'requirements.txt').unlink()
    with pytest.raises(ValueError, match='Incomplete'):
        installer.install(source, target(source))


def test_bytecode_excluded(source):
    (source / 'scripts/__pycache__').mkdir()
    (source / 'scripts/__pycache__/temp.pyc').write_bytes(b'test')
    installer.install(source, target(source))
    assert not (target(source) / 'scripts/__pycache__').exists()


@pytest.mark.parametrize('client,directory', list(installer.CLIENT_DIRS.items()))
def test_discovery_paths(tmp_path, client, directory):
    expected = tmp_path / directory / 'skills' / installer.NAME
    assert installer.destination(client, home=tmp_path) == expected
    assert installer.destination(client, project=tmp_path) == expected


def test_cli_target_and_project_conflict():
    with pytest.raises(SystemExit) as exc:
        installer.main(['--project', '/project', '--target', '/target'])
    assert exc.value.code == 2


def test_cli_returns_install_report(monkeypatch, capsys, tmp_path):
    run = Mock(return_value={'status': 'dry-run'})
    monkeypatch.setattr(installer, 'install', run)
    assert installer.main(['--client', 'codex', '--project', str(tmp_path), '--dry-run']) == 0
    assert run.call_args.args[1] == installer.destination('codex', project=tmp_path)
    assert json.loads(capsys.readouterr().out)['status'] == 'dry-run'


@pytest.mark.parametrize('exc', [ValueError('bad'), OSError('no access'),
                                 subprocess.TimeoutExpired('validator', 20)])
def test_cli_reports_failure(monkeypatch, capsys, exc):
    monkeypatch.setattr(installer, 'install', Mock(side_effect=exc))
    assert installer.main(['--target', '/tmp/ros2-engineering-skills']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_zero_exit_without_valid_report_is_not_installed(source, monkeypatch):
    monkeypatch.setattr(installer.subprocess, 'run',
                        Mock(return_value=Mock(returncode=0, stdout='{}', stderr='')))
    with pytest.raises(ValueError, match='unrecognized report'):
        installer.install(source, target(source))
    assert not target(source).exists()
