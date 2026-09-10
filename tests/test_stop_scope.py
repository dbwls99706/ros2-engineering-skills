"""Exercise candidate selection with real Git status and bounded fallback scans."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import skill_stop_hook as hook


def git(root, *args):
    return subprocess.run(
        ['git', '-C', str(root), '-c', 'user.name=Test',
         '-c', 'user.email=test@example.invalid', *args],
        check=True, capture_output=True, text=True)


def no_walk(*args, **kwargs):
    pytest.fail('Git-scoped validation must not walk the workspace')


def test_doc_only_git_change_does_not_walk_or_validate_untouched_errors(
        tmp_path, monkeypatch, capsys):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'broken.launch.py').write_text('not valid python !')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    (tmp_path / 'notes.md').write_text('documentation change\n')
    monkeypatch.setattr(hook.os, 'walk', no_walk)
    monkeypatch.setattr(hook, '_resolve_workspace', lambda: str(tmp_path))
    monkeypatch.setattr(hook, '_resolve_log_path', lambda workspace: None)
    with pytest.raises(SystemExit) as result:
        hook.main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)['issues'] == []


def test_package_subdirectory_keeps_modified_and_untracked_files_in_scope(
        tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    package = tmp_path / 'src/robot'
    package.mkdir(parents=True)
    tracked = package / 'robot.launch.py'
    deleted = package / 'deleted.launch.py'
    for path in (tracked, deleted, tmp_path / 'outside.launch.py'):
        path.write_text('baseline')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    tracked.write_text('changed')
    deleted.unlink()
    (tmp_path / 'outside.launch.py').write_text('unrelated change')
    unusual = package / 'café node_launch.py'
    unusual.write_text('new')
    (package / 'package.xml').write_text('<package/>')
    (package / 'nav.yaml').write_text('node: {}')
    for directory in ['build', 'vendor', '.hidden', 'a/b/c/d/e/f/g']:
        ignored = package / directory
        ignored.mkdir(parents=True, exist_ok=True)
        (ignored / 'ignored.launch.py').write_text('ignored')
    monkeypatch.setattr(hook.os, 'walk', no_walk)
    launches, manifests, yaml_files = hook._validation_files(str(package))
    assert set(launches) == {str(tracked), str(unusual)}
    assert manifests == [str(package / 'package.xml')]
    assert yaml_files == [str(package / 'nav.yaml')]


def test_git_rename_checks_destination(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    old = tmp_path / 'old.launch.py'
    old.write_text('old')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    git(tmp_path, 'mv', old.name, 'new.launch.py')
    monkeypatch.setattr(hook.os, 'walk', no_walk)
    assert hook._validation_files(str(tmp_path))[0] == [str(tmp_path / 'new.launch.py')]


def test_unknown_git_state_scans_once_and_keeps_exclusions(tmp_path, monkeypatch):
    (tmp_path / 'robot.launch.py').write_text('new')
    (tmp_path / 'package.xml').write_text('<package/>')
    (tmp_path / 'config.yml').write_text('node: {}')
    (tmp_path / 'build').mkdir()
    (tmp_path / 'build/ignored.launch.py').write_text('ignored')
    monkeypatch.setattr(hook, '_git_touched_paths', lambda root: None)
    real_walk = os.walk
    roots = []

    def count_walk(root):
        roots.append(root)
        return real_walk(root)

    monkeypatch.setattr(hook.os, 'walk', count_walk)
    launches, manifests, yaml_files = hook._validation_files(str(tmp_path))
    assert launches == [str(tmp_path / 'robot.launch.py')]
    assert manifests == [str(tmp_path / 'package.xml')]
    assert yaml_files == [str(tmp_path / 'config.yml')]
    assert roots == [str(tmp_path)]


def test_candidate_links_cannot_escape_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = tmp_path / 'outside.txt'
    target.write_text('outside')
    internal = workspace / 'inside.txt'
    internal.write_text('inside')
    try:
        (workspace / 'escape.launch.py').symlink_to(target)
        (workspace / 'alias.launch.py').symlink_to(internal)
    except OSError:
        pytest.skip('Symlinks unavailable')
    git(workspace, 'init', '-q')
    monkeypatch.setattr(hook.os, 'walk', no_walk)
    assert hook._validation_files(str(workspace))[0] == [str(workspace / 'alias.launch.py')]
