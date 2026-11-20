"""Tests for skill_workspace_hook.py - the report on a whole ROS 2 workspace."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import skill_workspace_hook as hook
from tests.test_workspace_checks import LAUNCH, make_package

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'skill_workspace_hook.py'
BROKEN_LAUNCH = LAUNCH.replace("executable='driver'", "executable='other'")


def run_hook(payload=None, **environment):
    """Run the hook as the adapter does and return (report, completed process)."""
    env = {key: value for key, value in os.environ.items()
           if key not in ('SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')}
    env.update(environment)
    done = subprocess.run(
        [sys.executable, '-B', str(SCRIPT)], input='' if payload is None else json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=30)
    return json.loads(done.stdout), done


@pytest.fixture
def workspace(tmp_path):
    """One package whose launch file starts an executable that does not exist."""
    root = tmp_path / 'ws'
    make_package(root / 'src', installed=(), files={'launch/demo.launch.py': BROKEN_LAUNCH})
    return root


class TestReport:
    def test_findings_are_reported_in_the_hook_schema(self, workspace):
        report, done = run_hook({'cwd': str(workspace)})
        assert done.returncode == 0 and done.stderr == ''
        assert set(report) == {'hook', 'version', 'packages', 'tree', 'issues_count', 'issues',
                               'checks_skipped', 'status'}
        assert report['hook'] == 'ros2-engineering-skills:workspace'
        assert len(report['tree']) == 64
        assert report['packages'] == 1 and report['status'] == 'fail'
        assert report['issues_count'] == len(report['issues']) == 2
        assert [issue['check'] for issue in report['issues']] == ['launch-executable', 'install-rules']
        assert all(set(issue) == {'file', 'line', 'severity', 'check', 'message'}
                   for issue in report['issues'])

    def test_errors_never_change_the_exit_status(self, workspace):
        report, done = run_hook(SKILL_WORKSPACE=str(workspace))
        assert report['status'] == 'fail' and done.returncode == 0

    def test_warnings_alone_are_a_pass(self, tmp_path):
        make_package(tmp_path, installed=(), files={'launch/demo.launch.py': LAUNCH})
        report, _done = run_hook(SKILL_WORKSPACE=str(tmp_path))
        assert report['status'] == 'pass' and report['issues_count'] == 1

    def test_directory_without_a_package_is_empty(self, tmp_path):
        (tmp_path / 'main.py').write_text('import rclpy\nnode.create_publisher(String, "x")\n')
        report, done = run_hook({'cwd': str(tmp_path)})
        assert done.returncode == 0
        assert (report['packages'], report['issues'], report['checks_skipped'], report['status']) == (
            0, [], [], 'pass')

    def test_nothing_is_written_into_the_workspace(self, workspace):
        before = sorted(str(path) for path in workspace.rglob('*'))
        run_hook({'cwd': str(workspace)})
        assert sorted(str(path) for path in workspace.rglob('*')) == before


class TestTreeSignature:
    def signature(self, root):
        return hook.survey(str(root))[3]

    def test_unchanged_workspace_has_the_same_signature(self, workspace):
        assert self.signature(workspace) == self.signature(workspace) != ''

    def test_rewriting_adding_or_removing_a_file_changes_it(self, workspace):
        package = workspace / 'src' / 'demo'
        seen = {self.signature(workspace)}
        (package / 'demo' / 'driver.py').write_text((package / 'demo' / 'driver.py').read_text() + '\n# edited\n')
        seen.add(self.signature(workspace))
        (package / 'demo' / 'extra.py').write_text('VALUE = 1\n')
        seen.add(self.signature(workspace))
        (package / 'demo' / 'extra.py').unlink()
        (package / 'config').mkdir()
        (package / 'config' / 'p.yaml').write_text('a: 1\n')
        seen.add(self.signature(workspace))
        assert len(seen) == 4

    def test_files_outside_the_packages_do_not_count(self, workspace):
        before = self.signature(workspace)
        (workspace / 'notes.txt').write_text('scratch')
        (workspace / 'build').mkdir()
        (workspace / 'build' / 'out.py').write_text('x = 1\n')
        assert self.signature(workspace) == before

    def test_directory_without_packages_has_no_signature(self, tmp_path):
        assert hook.survey(str(tmp_path)) == ([], [], [], '')
        report, _done = run_hook(SKILL_WORKSPACE=str(tmp_path))
        assert report['tree'] == '' and report['packages'] == 0

    def test_a_file_that_disappears_while_it_is_read_is_skipped(self, workspace, monkeypatch):
        import workspace_checks
        packages = workspace_checks.find_packages(workspace)
        packages[0].files.append(workspace / 'src' / 'demo' / 'gone.py')
        assert len(hook._tree(packages)) == 64


class TestTooBroad:
    def test_home_directory_is_not_walked(self, workspace, monkeypatch):
        monkeypatch.setenv('HOME', str(workspace))
        assert hook.collect(str(workspace)) == ([], [], 0)
        monkeypatch.setenv('HOME', str(workspace.parent))
        assert hook.collect(str(workspace))[2] == 1

    def test_home_directory_through_a_symlink_is_not_walked(self, workspace, monkeypatch):
        link = workspace.parent / 'link'
        link.symlink_to(workspace)
        monkeypatch.setenv('HOME', str(workspace))
        assert hook.collect(str(link)) == ([], [], 0)

    def test_filesystem_root_is_not_walked(self, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise AssertionError('the filesystem root must not be searched')

        monkeypatch.setattr(os, 'walk', refuse)
        assert hook.collect(os.sep) == ([], [], 0)


class TestWorkspaceResolution:
    def stdin(self, monkeypatch, text):
        monkeypatch.setattr(sys, 'stdin', io.StringIO(text))

    def test_explicit_workspace_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path / 'explicit'))
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
        self.stdin(monkeypatch, json.dumps({'cwd': str(tmp_path)}))
        assert hook._resolve_workspace() == str(tmp_path / 'explicit')

    def test_payload_directory_comes_before_the_project_directory(self, tmp_path, monkeypatch):
        (tmp_path / 'cwd').mkdir()
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
        self.stdin(monkeypatch, json.dumps({'cwd': str(tmp_path / 'cwd')}))
        assert hook._resolve_workspace() == str(tmp_path / 'cwd')

    @pytest.mark.parametrize('text', [
        '', '   ', 'not json', '[]', '"text"', '{"cwd": 5}', '{"cwd": null}', '{}',
        '{"cwd": "/no/such/directory/anywhere"}', '[' * 100000])
    def test_unusable_input_falls_back_to_the_project_directory(self, tmp_path, monkeypatch, text):
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
        self.stdin(monkeypatch, text)
        assert hook._resolve_workspace() == str(tmp_path)

    @pytest.mark.parametrize('project', [None, '', '/no/such/directory/anywhere'])
    def test_last_resort_is_the_current_directory(self, tmp_path, monkeypatch, project):
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        if project is None:
            monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
        else:
            monkeypatch.setenv('CLAUDE_PROJECT_DIR', project)
        monkeypatch.chdir(tmp_path)
        self.stdin(monkeypatch, '{"cwd": 5}')
        assert hook._resolve_workspace() == os.getcwd()

    def test_terminal_input_is_not_read(self, tmp_path, monkeypatch):
        terminal = SimpleNamespace(isatty=lambda: True, read=lambda: pytest.fail('stdin was read'))
        monkeypatch.setattr(sys, 'stdin', terminal)
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
        assert hook._resolve_workspace() == str(tmp_path)

    def test_bad_input_still_produces_a_report(self, tmp_path):
        done = subprocess.run([sys.executable, '-B', str(SCRIPT)], input='{broken', capture_output=True,
                              text=True, cwd=tmp_path, timeout=30,
                              env={k: v for k, v in os.environ.items()
                                   if k not in ('SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')})
        assert done.returncode == 0
        assert json.loads(done.stdout)['issues'] == []


class TestLaunchFindings:
    def test_launch_errors_are_included(self, tmp_path):
        text = LAUNCH.replace("output='screen'", "condition=IfCondition('use_rviz')")
        make_package(tmp_path, installed=('launch',), files={'launch/demo.launch.py': text})
        issues, _skipped, _count = hook.collect(str(tmp_path))
        issue, = issues
        assert (issue['check'], issue['severity'], issue['file']) == (
            'launch', 'error', 'demo/launch/demo.launch.py')
        assert "LaunchConfiguration('use_rviz')" in issue['message']

    def test_launch_style_warnings_are_left_out(self, tmp_path):
        text = LAUNCH.replace(", output='screen'", '')
        make_package(tmp_path, installed=('launch',), files={'launch/demo.launch.py': text})
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            import launch_validator
        finally:
            sys.path.pop(0)
        direct = launch_validator.validate_file(str(tmp_path / 'demo' / 'launch' / 'demo.launch.py'))
        assert direct and all(issue.severity != 'error' for issue in direct)
        assert hook.collect(str(tmp_path)) == ([], [], 1)

    @pytest.mark.parametrize('relative', [
        'launch/__init__.py', 'launch/helpers.py', 'launch/test_motions.py', 'demo/launch/rewritten_yaml.py'])
    def test_other_python_files_in_a_launch_directory_are_not_launch_files(self, tmp_path, relative):
        # A helper module, a node script or a Python package called launch
        # has no generate_launch_description, and that is not a defect.
        make_package(tmp_path, installed=('launch',), files={
            'launch/demo.launch.py': LAUNCH, relative: 'import os\n\n\ndef main():\n    return os.getcwd()\n'})
        assert hook.collect(str(tmp_path)) == ([], [], 1)

    @pytest.mark.parametrize('relative,text', [
        ('launch/demo.launch.py', 'import os\n'),
        ('launch/bringup.py', 'def generate_launch_description(:\n'),
    ])
    def test_a_launch_description_that_is_broken_is_reported(self, tmp_path, relative, text):
        make_package(tmp_path, installed=('launch',), files={relative: text})
        issues, _skipped, _count = hook.collect(str(tmp_path))
        assert [(issue['check'], issue['severity']) for issue in issues] == [('launch', 'error')]

    def test_unreadable_file_in_a_launch_directory_is_left_alone(self, tmp_path):
        package = make_package(tmp_path, installed=('launch',), files={'launch/demo.launch.py': LAUNCH})
        (package / 'launch' / 'blob.py').write_bytes(b'\xff\xfe\x00')
        assert hook.collect(str(tmp_path)) == ([], [], 1)

    def test_errors_come_first(self, workspace):
        issues, _skipped, _count = hook.collect(str(workspace))
        assert [issue['severity'] for issue in issues] == ['error', 'warning']

    def test_import_path_is_left_as_it_was(self, workspace):
        before = list(sys.path)
        hook.collect(str(workspace))
        assert sys.path == before


class TestLimits:
    def test_findings_past_the_cap_are_counted_not_dropped_silently(self, workspace, monkeypatch, capsys):
        monkeypatch.setattr(hook, 'MAX_ISSUES', 1)
        monkeypatch.setenv('SKILL_WORKSPACE', str(workspace))
        hook.main()
        report = json.loads(capsys.readouterr().out)
        assert report['issues_count'] == 1 and report['issues'][0]['severity'] == 'error'
        assert report['checks_skipped'] == ['1 further findings are not listed']

    def test_expired_deadline_is_reported_as_a_partial_check(self, workspace):
        issues, skipped, count = hook.collect(str(workspace), deadline=time.monotonic() - 1)
        assert (issues, count) == ([], 0)
        assert skipped == ['time limit reached while looking for packages: the directory tree was '
                           'only partly searched']

    def test_deadline_reached_during_launch_validation_is_reported(self, workspace, monkeypatch):
        # The workspace checks finish; the clock then jumps past the deadline.
        monkeypatch.setattr(hook, 'time', SimpleNamespace(monotonic=lambda: float('inf')))
        issues, skipped, count = hook.collect(str(workspace), deadline=time.monotonic() + 3600)
        assert count == 1 and len(issues) == 2
        assert skipped == ['time limit reached: not every launch file was validated']

    def test_main_runs_under_the_time_limit(self, workspace, monkeypatch, capsys):
        seen = []

        def survey(path, deadline=None):
            seen.append(deadline - time.monotonic())
            return [], ['time limit reached: 1 of 2 packages were checked'], ['arm', 'base'], 'sig'

        monkeypatch.setattr(hook, 'survey', survey)
        monkeypatch.setenv('SKILL_WORKSPACE', str(workspace))
        hook.main()
        report = json.loads(capsys.readouterr().out)
        assert 0 < seen[0] <= hook.TIME_LIMIT
        assert report['checks_skipped'] == ['time limit reached: 1 of 2 packages were checked']
        assert report['status'] == 'pass' and report['packages'] == 2

    def test_time_limit_fits_inside_the_adapter_and_client_timeouts(self):
        from scripts import claude_hook
        config = json.loads((ROOT / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
        start = config['hooks']['SessionStart'][0]['hooks'][0]['timeout']
        stop = config['hooks']['Stop'][0]['hooks'][0]['timeout']
        # Stop runs the workspace report and then the changed-file check, 12 seconds each.
        assert hook.TIME_LIMIT < 12 < start and 2 * 12 < stop
        assert 'timeout=8 if event == \'PreToolUse\' else 12' in Path(claude_hook.__file__).read_text()


class TestGeneratedPackages:
    @pytest.mark.parametrize('arguments', [
        ('--type', 'python'), ('--type', 'python', '--lifecycle'),
        ('--type', 'python', '--lifecycle', '--robots', '2', '--sros2'),
        ('--type', 'cpp'), ('--type', 'cpp', '--component', '--robots', '2'),
        ('--type', 'interfaces'), ('--type', 'hardware_interface')])
    def test_a_package_from_the_generator_has_no_findings(self, tmp_path, arguments):
        done = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts' / 'create_package.py'), 'generated', *arguments,
             '--dest', str(tmp_path / 'src')], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        assert hook.collect(str(tmp_path)) == ([], [], 1)
