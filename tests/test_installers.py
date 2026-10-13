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


def _fake_install(target, name='ros2-engineering-skills', references=True):
    """A previous installation: SKILL.md naming the skill plus references/."""
    target.mkdir(parents=True, exist_ok=True)
    (target / 'SKILL.md').write_text(f'---\nname: {name}\n---\n', encoding='utf-8')
    if references:
        (target / 'references').mkdir(exist_ok=True)
    stale = target / 'stale.txt'
    stale.write_text('stale', encoding='utf-8')
    return stale


def test_force_replaces_existing_target(tmp_path):
    # A custom directory name stays supported: the structure is what counts.
    target = tmp_path / 'skill'
    stale = _fake_install(target)
    result = _run('--target', target, '--force')
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert (target / 'SKILL.md').is_file()


def test_force_replaces_a_knowledge_only_install_with_provenance(tmp_path):
    target = tmp_path / 'ros2-engineering-skills'
    stale = _fake_install(target)
    (target / 'INSTALL_PROVENANCE.json').write_text(
        '{"schema_version": 1, "skill_name": "ros2-engineering-skills"}', encoding='utf-8')
    result = _run('--target', target, '--force')
    assert result.returncode == 0, result.stderr
    assert not stale.exists()


REFUSED = 'refusing to replace'


def _assert_refused(result, reason):
    assert result.returncode == 1, result.stdout
    assert REFUSED in result.stderr and reason in result.stderr


def test_force_never_deletes_client_configuration_directories(tmp_path):
    # ~/.claude, ~/.claude/skills and friends are protected even when empty or
    # when a stray SKILL.md would otherwise make them look like an install.
    home = tmp_path / 'home'
    env = {**os.environ, 'HOME': str(home)}
    (home / '.claude' / 'skills' / 'other').mkdir(parents=True)
    (home / '.claude' / 'skills' / 'other' / 'SKILL.md').write_text(
        '---\nname: other\n---\n', encoding='utf-8')
    (home / '.claude' / 'settings.json').write_text('{}', encoding='utf-8')
    (home / '.codex' / 'skills').mkdir(parents=True)  # empty skills parent
    (tmp_path / 'skills').mkdir()  # a skills/ parent outside HOME, also empty
    _fake_install(home / '.claude')  # even a SKILL.md there must not help
    for target in (home / '.claude', home / '.claude' / 'skills', home / '.codex' / 'skills',
                   home / '.codex', tmp_path / 'skills'):
        for extra in ((), ('--dry-run',)):
            result = subprocess.run(['bash', str(INSTALL_SH), '--target', str(target), '--force', *extra],
                                    capture_output=True, text=True, env=env)
            _assert_refused(result, 'client configuration directory')
    assert (home / '.claude' / 'settings.json').read_text(encoding='utf-8') == '{}'
    assert (home / '.claude' / 'skills' / 'other' / 'SKILL.md').is_file()
    assert (home / '.codex' / 'skills').is_dir()


def test_force_never_deletes_a_directory_that_is_not_this_skill(tmp_path):
    other = tmp_path / 'other'
    _fake_install(other, name='other')
    empty = tmp_path / 'ros2-engineering-skills'
    empty.mkdir()
    no_refs = tmp_path / 'norefs'
    _fake_install(no_refs, references=False)
    afile = tmp_path / 'notes.txt'
    afile.write_text('keep', encoding='utf-8')
    for target in (other, empty, no_refs, afile):
        for extra in ((), ('--dry-run',)):
            _assert_refused(_run('--target', target, '--force', *extra),
                            'not an existing ros2-engineering-skills installation')
    assert (other / 'SKILL.md').is_file() and (other / 'stale.txt').is_file()
    assert empty.is_dir() and (no_refs / 'stale.txt').is_file()
    assert afile.read_text(encoding='utf-8') == 'keep'


def test_force_refuses_a_provenance_manifest_for_another_skill(tmp_path):
    target = tmp_path / 'ros2-engineering-skills'
    _fake_install(target)
    for manifest in ('{"schema_version": 1, "skill_name": "other"}',
                     '{"skill_name": "ros2-engineering-skills"}',
                     '{not json'):
        (target / 'INSTALL_PROVENANCE.json').write_text(manifest, encoding='utf-8')
        _assert_refused(_run('--target', target, '--force'), 'provenance manifest')
    assert (target / 'stale.txt').is_file()


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


def test_link_install_explains_possible_hook_registration(tmp_path):
    target = tmp_path / 'skill'
    result = _run('--target', target, '--link')
    assert result.returncode == 0, result.stderr
    assert 'may register its PreToolUse and Stop' in result.stdout
    assert 'does not configure hooks' in result.stdout
    assert 'may register' not in _run('--target', tmp_path / 'copy').stdout


def test_bundled_scripts_with_a_shebang_are_executable():
    # SKILL.md tells agents to call the tools by absolute path.
    for script in sorted((ROOT / 'scripts').glob('*.py')):
        if script.read_text(encoding='utf-8').startswith('#!'):
            assert os.access(script, os.X_OK), script.name


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
