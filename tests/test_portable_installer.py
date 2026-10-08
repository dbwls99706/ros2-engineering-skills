"""Portable installs never register hooks or replace unrelated files."""

import json
import os
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
    installer.install(source, dst, force=True, discard_local_changes=True)
    assert not (dst / 'stale.txt').exists()
    assert sibling.read_text() == 'keep'


def test_force_alone_refuses_a_drifted_install(source):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'stale.txt').write_text('stale')
    with pytest.raises(ValueError, match='added: stale.txt'):
        installer.install(source, dst, force=True)
    assert (dst / 'stale.txt').read_text() == 'stale'


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
        installer.install(source, dst, force=True, discard_local_changes=True)
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


def test_double_rename_failure_preserves_recoverable_backup(source, monkeypatch):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'keep.txt').write_text('original must survive')
    rename = Path.rename

    def fail_swap_and_restore(path, to):
        if '.skill-install-' in str(path) or path.name == 'previous':
            raise OSError('simulated rename failure')
        return rename(path, to)

    monkeypatch.setattr(Path, 'rename', fail_swap_and_restore)
    with pytest.raises(OSError, match='preserved at') as error:
        installer.install(source, dst, force=True, discard_local_changes=True)
    backups = list(dst.parent.glob('.skill-backup-*/previous'))
    assert len(backups) == 1
    assert str(backups[0]) in str(error.value)
    assert (backups[0] / 'keep.txt').read_text() == 'original must survive'
    assert not list(dst.parent.glob('.skill-install-*'))
    assert not (dst.parent / ('.' + installer.NAME + '.install-lock')).exists()


def test_backup_rename_failure_leaves_original(source, monkeypatch):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'keep.txt').write_text('original')
    rename = Path.rename

    def refuse_backup(path, to):
        if path == dst:
            raise OSError('cannot move original')
        return rename(path, to)

    monkeypatch.setattr(Path, 'rename', refuse_backup)
    with pytest.raises(OSError, match='cannot move original'):
        installer.install(source, dst, force=True, discard_local_changes=True)
    assert (dst / 'keep.txt').read_text() == 'original'
    assert not list(dst.parent.glob('.skill-backup-*'))


def test_backup_cleanup_failure_is_reported_without_rollback(source, monkeypatch):
    dst = target(source)
    installer.install(source, dst)
    rmtree = shutil.rmtree

    def refuse_backup_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith('.skill-backup-'):
            raise OSError('backup cleanup refused')
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, 'rmtree', refuse_backup_cleanup)
    report = installer.install(source, dst, force=True)
    assert report['status'] == 'installed'
    assert Path(report['backup_retained']).is_dir()
    assert (dst / 'SKILL.md').is_file()


def test_installer_lock_refuses_concurrent_writer(source):
    dst = target(source)
    dst.parent.mkdir()
    lock = dst.parent / ('.' + installer.NAME + '.install-lock')
    lock.mkdir()
    with pytest.raises(ValueError, match='lock exists'):
        installer.install(source, dst)
    assert lock.is_dir()
    assert not dst.exists()


def test_target_rechecked_after_lock_is_acquired(source, monkeypatch):
    dst = target(source)
    dst.parent.mkdir()
    check_target = installer.check_target
    calls = []

    def race(target_path, force):
        calls.append(target_path)
        if len(calls) == 2:
            dst.mkdir()
            (dst / 'private.txt').write_text('unrelated concurrent creator')
        check_target(target_path, force)

    monkeypatch.setattr(installer, 'check_target', race)
    with pytest.raises(ValueError, match='not a skill'):
        installer.install(source, dst, force=True)
    assert (dst / 'private.txt').read_text() == 'unrelated concurrent creator'


# ------------------------------------------------------------------ provenance

def manifest_of(dst):
    return json.loads((dst / installer.PROVENANCE).read_text(encoding='utf-8'))


def verify(dst, capsys):
    code = installer.main(['--verify', '--target', str(dst)])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def fake_git(monkeypatch):
    """Answer git calls from state; every other subprocess runs for real."""
    state = {'head': 'a' * 40, 'porcelain': '', 'returncode': 0, 'calls': []}
    real_run = installer.subprocess.run

    def run(argv, **kwargs):
        if argv[0] != 'git':
            return real_run(argv, **kwargs)
        state['calls'].append(list(argv))
        if argv[3] == 'rev-parse':
            return Mock(returncode=state['returncode'], stdout=state['head'] + '\n', stderr='')
        return Mock(returncode=0, stdout=state['porcelain'], stderr='')

    monkeypatch.setattr(installer.subprocess, 'run', run)
    return state


def test_manifest_records_every_installed_file(source):
    dst = target(source)
    report = installer.install(source, dst)
    manifest = manifest_of(dst)
    assert manifest['schema_version'] == installer.PROVENANCE_SCHEMA
    assert manifest['hash_algorithm'] == 'sha256'
    assert manifest['skill_name'] == installer.NAME
    assert manifest['source_version'] == '1.3.0'
    assert manifest['installer'] == 'scripts/install_skill.py'
    assert manifest['installer_version'] == installer.INSTALLER_VERSION
    assert manifest['layout'] == 'knowledge-only'
    assert manifest['installed_at'].endswith('Z')
    assert report['provenance'] == installer.PROVENANCE
    assert report['source_version'] == '1.3.0'
    assert report['installer_version'] == installer.INSTALLER_VERSION
    assert installer.PROVENANCE not in manifest['files']
    on_disk = {p.relative_to(dst).as_posix() for p in dst.rglob('*') if p.is_file()}
    assert set(manifest['files']) == on_disk - {installer.PROVENANCE}
    for relative, digest in manifest['files'].items():
        assert installer.sha256_file(dst / relative) == digest


def test_manifest_excludes_bytecode(source):
    (source / 'scripts/__pycache__').mkdir()
    (source / 'scripts/__pycache__/temp.pyc').write_bytes(b'test')
    installer.install(source, target(source))
    assert not any(p.startswith('scripts/__pycache__') for p in manifest_of(target(source))['files'])


def test_dry_run_names_the_manifest_without_writing_it(source):
    report = installer.install(source, target(source), dry_run=True)
    assert report['provenance'] == installer.PROVENANCE
    assert not target(source).parent.exists()


def test_source_commit_is_unknown_outside_a_checkout(source):
    assert installer.source_commit(source) == (None, None)
    installer.install(source, target(source))
    manifest = manifest_of(target(source))
    assert manifest['source_commit'] is None and manifest['source_dirty'] is None


def test_source_commit_records_head_and_bundle_scoped_dirty(source, fake_git):
    fake_git['porcelain'] = ' M references/test.md\n'
    installer.install(source, target(source))
    manifest = manifest_of(target(source))
    assert manifest['source_commit'] == 'a' * 40 and manifest['source_dirty'] is True
    status = next(call for call in fake_git['calls'] if call[3] == 'status')
    assert status[4:7] == ['--porcelain', '--ignored=matching', '--']
    assert tuple(status[7:]) == installer.BUNDLE


def test_clean_checkout_is_not_dirty(source, fake_git):
    assert installer.source_commit(source) == ('a' * 40, False)


@pytest.mark.parametrize('porcelain,dirty', [
    ('!! evals/history/2026-10.jsonl\n', True),      # ignored by git but copied
    ('?? references/local-note.md\n', True),          # untracked and copied
    (' M README.md\n', True),
    ('!! scripts/__pycache__/\n', False),            # never copied
    ('!! references/test.md.swp\n', False),          # editor backup, never copied
    ('!! docs/notes.md~\n', False),
])
def test_dirty_follows_the_bytes_that_get_copied(source, fake_git, porcelain, dirty):
    fake_git['porcelain'] = porcelain
    assert installer.source_commit(source) == ('a' * 40, dirty)


def test_editor_backups_are_not_installed(source):
    (source / 'references/test.md~').write_text('backup')
    (source / 'references/.test.md.swp').write_text('swap')
    installer.install(source, target(source))
    assert not (target(source) / 'references/test.md~').exists()
    assert not (target(source) / 'references/.test.md.swp').exists()
    assert 'references/test.md~' not in manifest_of(target(source))['files']


def test_symlink_under_an_ignored_name_is_still_drift(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    outside = source.parent / 'cache_outside'
    outside.mkdir()
    (dst / '.mypy_cache').symlink_to(outside, target_is_directory=True)
    code, report = verify(dst, capsys)
    assert code == 1 and report['symlinks'] == ['.mypy_cache']


@pytest.mark.parametrize('head,returncode', [('a' * 40, 1), ('short', 0)])
def test_source_commit_rejects_unusable_git_output(source, fake_git, head, returncode):
    fake_git.update(head=head, returncode=returncode)
    assert installer.source_commit(source) == (None, None)


def test_source_commit_tolerates_missing_git(source, monkeypatch):
    monkeypatch.setattr(installer.subprocess, 'run', Mock(side_effect=FileNotFoundError('git')))
    assert installer.source_commit(source) == (None, None)


def test_source_version_requires_metadata_version(source):
    (source / 'SKILL.md').write_text('---\nname: ros2-engineering-skills\n---\nbody\n')
    with pytest.raises(ValueError, match='no version'):
        installer.source_version(source)
    (source / 'SKILL.md').write_text('broken')
    with pytest.raises(ValueError, match='failed validation'):
        installer.source_version(source)


def test_staged_symlink_is_never_recorded(source):
    stage = source.parent / 'stage'
    stage.mkdir()
    (stage / 'link').symlink_to(source / 'SKILL.md')
    with pytest.raises(ValueError, match='symlinks or special files'):
        installer.write_provenance(stage, source)


def test_verify_is_clean_after_install(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    code, report = verify(dst, capsys)
    assert code == 0 and report['status'] == 'clean'
    assert report['source_version'] == '1.3.0'
    assert report['installer_version'] == installer.INSTALLER_VERSION
    assert not any(report[key] for key in ('modified', 'missing', 'added', 'symlinks', 'other'))


def test_verify_reports_modified_missing_and_added(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'README.md').write_text('edited locally\n')
    (dst / 'LICENSE').unlink()
    (dst / 'notes.md').write_text('local note\n')
    code, report = verify(dst, capsys)
    assert code == 1 and report['status'] == 'drifted'
    assert report['modified'] == ['README.md']
    assert report['missing'] == ['LICENSE']
    assert report['added'] == ['notes.md']


def test_generated_caches_are_not_drift(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    for relative in ('.mypy_cache/x', 'htmlcov/index.html', 'coverage.xml', '.hypothesis/y',
                     'scripts/__pycache__/a.pyc', '.pytest_cache/v'):
        path = dst / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated')
    code, report = verify(dst, capsys)
    assert code == 0 and report['status'] == 'clean', report


def test_symlink_inside_install_is_drift_and_is_never_opened(source, capsys, monkeypatch):
    dst = target(source)
    installer.install(source, dst)
    outside = source.parent / 'outside.txt'
    outside.write_text('secret')
    outside_dir = source.parent / 'outside_dir'
    outside_dir.mkdir()
    (outside_dir / 'inner.txt').write_text('secret')
    (dst / 'leak').symlink_to(outside)
    (dst / 'leakdir').symlink_to(outside_dir, target_is_directory=True)
    hashed = []
    real_sha256 = installer.sha256_file

    def recording_sha256(path):
        hashed.append(Path(path).resolve())
        return real_sha256(path)

    monkeypatch.setattr(installer, 'sha256_file', recording_sha256)
    code, report = verify(dst, capsys)
    assert code == 1 and report['status'] == 'drifted'
    assert report['symlinks'] == ['leak', 'leakdir']
    assert not any(p.startswith('leakdir') for p in report['added'])
    assert outside.resolve() not in hashed
    assert (outside_dir / 'inner.txt').resolve() not in hashed


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='requires POSIX named pipes')
def test_special_files_are_drift_and_are_never_opened(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    os.mkfifo(dst / 'pipe')
    code, report = verify(dst, capsys)
    assert code == 1 and report['other'] == ['pipe']


@pytest.mark.parametrize('mutate,reason', [
    (lambda m: m.update(hash_algorithm='md5'), 'unsupported hash algorithm'),
    (lambda m: m.update(schema_version=2), 'unsupported provenance schema'),
    (lambda m: m.update(files=['SKILL.md']), 'malformed'),
])
def test_unusable_manifest_is_unverified(source, capsys, mutate, reason):
    dst = target(source)
    installer.install(source, dst)
    manifest = manifest_of(dst)
    mutate(manifest)
    (dst / installer.PROVENANCE).write_text(json.dumps(manifest))
    code, report = verify(dst, capsys)
    assert code == 1 and report['status'] == 'unverified' and reason in report['reason']


def test_broken_or_missing_manifest_is_unverified(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    (dst / installer.PROVENANCE).write_text('{not json')
    assert verify(dst, capsys)[1]['reason'].startswith('unreadable provenance manifest')
    (dst / installer.PROVENANCE).write_text('[]')
    assert verify(dst, capsys)[1]['reason'] == 'provenance manifest is not an object'
    (dst / installer.PROVENANCE).unlink()
    assert verify(dst, capsys)[1]['reason'] == 'no provenance manifest'
    (dst / installer.PROVENANCE).symlink_to(source / 'SKILL.md')
    assert verify(dst, capsys)[1]['reason'] == 'provenance manifest is a symlink'


def test_verify_never_follows_a_target_symlink(source, capsys):
    dst = target(source)
    installer.install(source, dst)
    link = dst.parent / 'alias'
    link.symlink_to(dst, target_is_directory=True)
    code, report = installer.main(['--verify', '--target', str(link)]), json.loads(capsys.readouterr().out)
    assert code == 1 and report['reason'] == 'target is a symlink'
    assert verify(dst.parent / 'absent', capsys)[1]['reason'] == 'target does not exist'


def test_force_refuses_a_drifted_install_and_changes_nothing(source):
    dst = target(source)
    installer.install(source, dst)
    (dst / 'README.md').write_text('edited locally\n')
    before = {p: p.read_bytes() for p in dst.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='differs from its provenance manifest .*modified: README.md'):
        installer.install(source, dst, force=True)
    assert {p: p.read_bytes() for p in dst.rglob('*') if p.is_file()} == before


def test_force_with_discard_replaces_a_drifted_install(source):
    dst = target(source)
    installer.install(source, dst)
    old_manifest = manifest_of(dst)
    (dst / 'README.md').write_text('edited locally\n')
    report = installer.install(source, dst, force=True, discard_local_changes=True)
    assert report['status'] == 'installed'
    assert (dst / 'README.md').read_text() == '# Test fixture\n'
    assert manifest_of(dst)['files'] == old_manifest['files']
    assert installer.drift_report(dst)['status'] == 'clean'


def test_install_without_manifest_needs_discard(source):
    dst = target(source)
    installer.install(source, dst)
    (dst / installer.PROVENANCE).unlink()
    with pytest.raises(ValueError, match='cannot be verified .*no provenance manifest'):
        installer.install(source, dst, force=True)
    assert not (dst / installer.PROVENANCE).exists()
    installer.install(source, dst, force=True, discard_local_changes=True)
    assert manifest_of(dst)['schema_version'] == installer.PROVENANCE_SCHEMA


def test_cli_discard_requires_force(capsys):
    with pytest.raises(SystemExit) as exc:
        installer.main(['--discard-local-changes', '--target', '/tmp/ros2-engineering-skills'])
    assert exc.value.code == 2
    assert 'requires --force' in capsys.readouterr().err


@pytest.mark.parametrize('extra', [['--force'], ['--dry-run'], ['--force', '--discard-local-changes']])
def test_cli_verify_is_exclusive(capsys, extra):
    with pytest.raises(SystemExit) as exc:
        installer.main(['--verify', '--target', '/tmp/ros2-engineering-skills', *extra])
    assert exc.value.code == 2


def test_cli_verify_reports_errors(monkeypatch, capsys):
    monkeypatch.setattr(installer, 'drift_report', Mock(side_effect=OSError('unreadable')))
    assert installer.main(['--verify', '--target', '/tmp/ros2-engineering-skills']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        installer.main(['--version'])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == installer.INSTALLER_VERSION
