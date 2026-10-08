"""Tests for the portable checkout installers."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / 'install.sh'
INSTALL_PS1 = ROOT / 'install.ps1'


def _run(*args):
    return subprocess.run(
        ['bash', str(INSTALL_SH), *map(str, args)],
        capture_output=True,
        text=True,
    )


def test_help():
    result = _run('--help')
    assert result.returncode == 0
    assert '--target PATH' in result.stdout
    assert '--dry-run' in result.stdout


def test_dry_run_does_not_create_target(tmp_path):
    target = tmp_path / 'skill'
    result = _run('--target', target, '--dry-run')
    assert result.returncode == 0
    assert 'mode=copy' in result.stdout
    assert not target.exists()


def test_copy_install(tmp_path):
    target = tmp_path / 'skill'
    result = _run('--target', target)
    assert result.returncode == 0, result.stderr
    assert (target / 'SKILL.md').is_file()
    assert (target / 'references').is_dir()
    assert not (target / '.git').exists()


def test_existing_target_requires_force(tmp_path):
    target = tmp_path / 'skill'
    target.mkdir()
    result = _run('--target', target)
    assert result.returncode == 1
    assert 'target already exists' in result.stderr


def test_force_replaces_existing_target(tmp_path):
    target = tmp_path / 'skill'
    target.mkdir()
    stale = target / 'stale.txt'
    stale.write_text('stale', encoding='utf-8')
    result = _run('--target', target, '--force')
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert (target / 'SKILL.md').is_file()


def test_link_install(tmp_path):
    target = tmp_path / 'skill'
    result = _run('--target', target, '--link')
    assert result.returncode == 0, result.stderr
    assert target.is_symlink()
    assert target.resolve() == ROOT.resolve()


def test_unsafe_target_is_rejected():
    result = _run('--target', '/')
    assert result.returncode == 2
    assert 'refusing unsafe target' in result.stderr


def test_target_inside_checkout_is_rejected():
    target = ROOT / 'nested-install-target'
    result = _run('--target', target)
    assert result.returncode == 2
    assert 'refusing unsafe target' in result.stderr
    assert not target.exists()


def test_unknown_option_is_rejected():
    result = _run('--unknown')
    assert result.returncode == 2
    assert 'unknown option' in result.stderr


def test_symlink_target_is_never_replaced(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    (real / 'marker.txt').write_text('keep', encoding='utf-8')
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    for args in (('--force',), (), ('--link', '--force'), ('--force', '--dry-run')):
        result = _run('--target', link, *args)
        assert result.returncode == 1, args
        assert 'symbolic link' in result.stderr
        assert str(real.resolve()) in result.stderr
        assert link.is_symlink()
        assert (real / 'marker.txt').read_text(encoding='utf-8') == 'keep'
    assert 'never replaced' in _run('--help').stdout


def test_install_files_are_present_and_documented():
    assert os.access(INSTALL_SH, os.X_OK)
    powershell = INSTALL_PS1.read_text(encoding='utf-8')
    required = ('[CmdletBinding()]', '$Target', '$Link', '$Force', '$DryRun', 'ReparsePoint')
    for token in required:
        assert token in powershell


def test_missing_target_argument_is_rejected():
    result = _run('--target')
    assert result.returncode == 2
    assert 'requires a path' in result.stderr
