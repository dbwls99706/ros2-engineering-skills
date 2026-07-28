"""Tests for Skills 2.0 hook scripts — validates hook execution and output.

These tests ensure:
1. Hook scripts are executable and produce valid JSON output
2. Stop hook correctly validates ROS 2 artifacts
3. PreToolUse hook detects anti-patterns
4. Hooks return correct exit codes
"""

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts')

sys.path.insert(0, SCRIPTS_DIR)
from skill_stop_hook import (
    find_generated_launch_files,
    validate_launch_file_syntax,
    validate_package_xml,
    find_package_xmls,
    find_yaml_files,
    validate_nav2_yaml,
    _distro_at_least,
    _git_touched_paths,
    _resolve_log_path,
)
from skill_validate_hook import (
    check_content,
    check_file,
    _check_dangerous_commands,
    ANTIPATTERN_CHECKS,
    CHECKABLE_EXTENSIONS,
    DANGEROUS_COMMAND_PATTERNS,
)


# Exit code the PreToolUse hook must use to actually refuse a tool call.
# Claude Code only treats 2 as blocking — it explicitly documents that
# exit 1 is a non-blocking error and that the action proceeds anyway. A
# guard returning 1 prints its refusal and then lets `rm -rf /` run, so
# this constant is the difference between a real gate and a log line.
# Manual CLI mode (--file/--command) keeps the conventional 1; see
# TestValidateHookManualCLI.
BLOCKING_EXIT = 2


class TestStopHookLaunchValidation:
    """Test the stop hook's launch file validation."""

    def test_valid_launch_file(self, tmp_path):
        launch = tmp_path / 'test.launch.py'
        launch.write_text(
            'from launch import LaunchDescription\n'
            'def generate_launch_description():\n'
            '    return LaunchDescription([])\n'
        )
        issues = validate_launch_file_syntax(str(launch))
        assert len(issues) == 0

    def test_missing_generate_function(self, tmp_path):
        launch = tmp_path / 'bad.launch.py'
        launch.write_text(
            'from launch import LaunchDescription\n'
            'def create_nodes():\n'
            '    return LaunchDescription([])\n'
        )
        issues = validate_launch_file_syntax(str(launch))
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'
        assert 'generate_launch_description' in issues[0]['message']

    def test_entry_point_may_be_imported(self, tmp_path):
        # A launch file that re-exports a shared entry point is valid; only
        # matching `def` would call this working file broken.
        launch = tmp_path / 'reexport.launch.py'
        launch.write_text(
            'from my_pkg.common import generate_launch_description\n'
        )
        assert validate_launch_file_syntax(str(launch)) == []

    def test_entry_point_may_be_an_alias(self, tmp_path):
        launch = tmp_path / 'alias.launch.py'
        launch.write_text(
            'from launch import LaunchDescription\n'
            'def _build():\n'
            '    return LaunchDescription([])\n'
            'generate_launch_description = _build\n'
        )
        assert validate_launch_file_syntax(str(launch)) == []

    def test_entry_point_may_be_an_import_alias(self, tmp_path):
        launch = tmp_path / 'importalias.launch.py'
        launch.write_text(
            'from my_pkg.common import build as generate_launch_description\n'
        )
        assert validate_launch_file_syntax(str(launch)) == []

    def test_entry_point_may_be_conditional(self, tmp_path):
        # `if`/`try` bodies still bind at module scope.
        launch = tmp_path / 'conditional.launch.py'
        launch.write_text(
            'import os\n'
            'if os.environ.get("SIM"):\n'
            '    def generate_launch_description():\n'
            '        return None\n'
            'else:\n'
            '    def generate_launch_description():\n'
            '        return None\n'
        )
        assert validate_launch_file_syntax(str(launch)) == []


class TestStopHookLaunchEntryPointRejections:
    """The loader imports the module and calls the top-level attribute.

    Anything that is not a module-scope, synchronous, callable binding
    fails at launch time, so accepting it here would be a silent pass on a
    broken file — a strictly worse failure than the false positive that
    only matching `def` used to produce.
    """

    def _issues(self, tmp_path, name, source):
        launch = tmp_path / name
        launch.write_text(source)
        return validate_launch_file_syntax(str(launch))

    def test_nested_function_is_not_module_scope(self, tmp_path):
        issues = self._issues(tmp_path, 'nested.launch.py',
                              'def helper():\n'
                              '    def generate_launch_description():\n'
                              '        pass\n')
        assert len(issues) == 1
        assert 'Missing' in issues[0]['message']

    def test_method_on_a_class_is_not_module_scope(self, tmp_path):
        issues = self._issues(tmp_path, 'method.launch.py',
                              'class Builder:\n'
                              '    def generate_launch_description(self):\n'
                              '        pass\n')
        assert len(issues) == 1

    def test_async_def_is_reported_specifically(self, tmp_path):
        issues = self._issues(tmp_path, 'async.launch.py',
                              'async def generate_launch_description():\n'
                              '    return None\n')
        assert len(issues) == 1
        assert 'coroutine' in issues[0]['message']

    def test_bare_annotation_binds_nothing(self, tmp_path):
        issues = self._issues(tmp_path, 'annotation.launch.py',
                              'generate_launch_description: object\n')
        assert len(issues) == 1

    def test_assigned_none_is_not_callable(self, tmp_path):
        issues = self._issues(tmp_path, 'none.launch.py',
                              'generate_launch_description = None\n')
        assert len(issues) == 1

    def test_assigned_literal_is_not_callable(self, tmp_path):
        issues = self._issues(tmp_path, 'literal.launch.py',
                              'generate_launch_description = "nope"\n')
        assert len(issues) == 1

    def test_plain_import_alias_binds_a_module(self, tmp_path):
        issues = self._issues(tmp_path, 'modalias.launch.py',
                              'import my_pkg as generate_launch_description\n')
        assert len(issues) == 1

    # The loader reads the attribute after the module has finished running,
    # so what matters is the final binding. A check that returned as soon as
    # it saw one valid definition accepted every case below.

    def test_valid_function_rebound_to_none_is_rejected(self, tmp_path):
        issues = self._issues(tmp_path, 'rebound.launch.py',
                              'from launch import LaunchDescription\n'
                              'def generate_launch_description():\n'
                              '    return LaunchDescription([])\n'
                              '\n'
                              'generate_launch_description = None\n')
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'
        assert 'callable' in issues[0]['message']

    def test_valid_function_rebound_to_literal_is_rejected(self, tmp_path):
        issues = self._issues(tmp_path, 'reboundstr.launch.py',
                              'def generate_launch_description():\n'
                              '    return None\n'
                              'generate_launch_description = "later"\n')
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'

    def test_alias_to_local_async_function_is_rejected(self, tmp_path):
        # The alias is only as good as its target.
        issues = self._issues(tmp_path, 'asyncalias.launch.py',
                              'async def _build():\n'
                              '    return None\n'
                              'generate_launch_description = _build\n')
        assert len(issues) == 1
        assert 'coroutine' in issues[0]['message']

    def test_definition_inside_false_branch_is_rejected(self, tmp_path):
        issues = self._issues(tmp_path, 'deadbranch.launch.py',
                              'if False:\n'
                              '    def generate_launch_description():\n'
                              '        return None\n')
        assert len(issues) == 1
        assert 'Missing' in issues[0]['message']

    def test_deleted_entry_point_is_rejected(self, tmp_path):
        issues = self._issues(tmp_path, 'deleted.launch.py',
                              'def generate_launch_description():\n'
                              '    return None\n'
                              'del generate_launch_description\n')
        assert len(issues) == 1
        assert 'Missing' in issues[0]['message']

    def test_alias_through_a_variable_resolves(self, tmp_path):
        # Tracking only the entry point's own name missed this: `value` had
        # no recorded state, so the alias was assumed callable.
        issues = self._issues(tmp_path, 'indirect.launch.py',
                              'value = None\n'
                              'generate_launch_description = value\n')
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'
        assert 'callable' in issues[0]['message']

    def test_chained_async_alias_resolves(self, tmp_path):
        issues = self._issues(tmp_path, 'chained.launch.py',
                              'async def _build():\n'
                              '    return None\n'
                              'mid = _build\n'
                              'generate_launch_description = mid\n')
        assert len(issues) == 1
        assert 'coroutine' in issues[0]['message']

    def test_helper_differing_across_branches_is_not_definite(self, tmp_path):
        # The helper is async on one path and sync on the other, so the
        # alias cannot be called definitely correct.
        issues = self._issues(tmp_path, 'helperbranch.launch.py',
                              'import os\n'
                              'if os.environ.get("SIM"):\n'
                              '    async def _build():\n'
                              '        return None\n'
                              'else:\n'
                              '    def _build():\n'
                              '        return None\n'
                              'generate_launch_description = _build\n')
        assert len(issues) == 1
        assert issues[0]['severity'] == 'warning'

    def test_chained_sync_alias_still_passes(self, tmp_path):
        assert self._issues(tmp_path, 'chainok.launch.py',
                            'def _build():\n'
                            '    return None\n'
                            'mid = _build\n'
                            'generate_launch_description = mid\n') == []

    def test_try_except_import_fallback_passes(self, tmp_path):
        # Both paths bind it, so the outcome is definite.
        assert self._issues(tmp_path, 'fallback.launch.py',
                            'try:\n'
                            '    from a import generate_launch_description\n'
                            'except ImportError:\n'
                            '    from b import generate_launch_description\n'
                            ) == []

    def test_conditionally_bound_entry_point_is_a_warning(self, tmp_path):
        """Bound on one path only — not provably broken, not provably fine.

        Erroring here would fail a launch file whose guard is always true
        in practice; staying silent would hide a real half-defined entry
        point. A warning says what is known without failing the hook.
        """
        issues = self._issues(tmp_path, 'maybe.launch.py',
                              'import os\n'
                              'if os.environ.get("SIM"):\n'
                              '    def generate_launch_description():\n'
                              '        return None\n')
        assert len(issues) == 1
        assert issues[0]['severity'] == 'warning'
        assert 'some execution paths' in issues[0]['message']

    def test_syntax_error(self, tmp_path):
        launch = tmp_path / 'syntax.launch.py'
        launch.write_text('def broken(\n')
        issues = validate_launch_file_syntax(str(launch))
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'
        assert 'yntax' in issues[0]['message']

    def test_nonexistent_file(self):
        issues = validate_launch_file_syntax('/nonexistent/file.launch.py')
        assert len(issues) == 0  # File read errors are silently skipped

    def test_find_launch_files(self, tmp_path):
        launch_dir = tmp_path / 'pkg' / 'launch'
        launch_dir.mkdir(parents=True)
        (launch_dir / 'a.launch.py').write_text('# launch')
        (launch_dir / 'b.launch.py').write_text('# launch')
        # *_launch.py is the other official naming convention
        (launch_dir / 'c_launch.py').write_text('# launch')
        (tmp_path / 'helpers.py').write_text('# not a launch')
        files = find_generated_launch_files(str(tmp_path))
        assert len(files) == 3

    def test_find_launch_files_skips_hidden(self, tmp_path):
        hidden = tmp_path / '.hidden' / 'launch'
        hidden.mkdir(parents=True)
        (hidden / 'skip.launch.py').write_text('# skip')
        files = find_generated_launch_files(str(tmp_path))
        assert len(files) == 0

    def test_find_launch_files_skips_build(self, tmp_path):
        build = tmp_path / 'build' / 'pkg' / 'launch'
        build.mkdir(parents=True)
        (build / 'skip.launch.py').write_text('# skip')
        files = find_generated_launch_files(str(tmp_path))
        assert len(files) == 0


class TestStopHookPackageXmlValidation:
    """Test the stop hook's package.xml validation."""

    def test_valid_package_xml(self, tmp_path):
        pkg_xml = tmp_path / 'package.xml'
        pkg_xml.write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            '  <name>test_pkg</name>\n'
            '  <version>0.1.0</version>\n'
            '  <description>Test</description>\n'
            '  <maintainer email="a@b.c">Test</maintainer>\n'
            '  <license>Apache-2.0</license>\n'
            '</package>\n'
        )
        issues = validate_package_xml(str(pkg_xml))
        assert len(issues) == 0

    def test_old_format_warns(self, tmp_path):
        pkg_xml = tmp_path / 'package.xml'
        pkg_xml.write_text(
            '<?xml version="1.0"?>\n'
            '<package format="2">\n'
            '  <name>test_pkg</name>\n'
            '  <license>Apache-2.0</license>\n'
            '</package>\n'
        )
        issues = validate_package_xml(str(pkg_xml))
        warnings = [i for i in issues if i['severity'] == 'warning']
        assert any('format' in i['message'] for i in warnings)

    def test_missing_name_errors(self, tmp_path):
        pkg_xml = tmp_path / 'package.xml'
        pkg_xml.write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            '  <license>Apache-2.0</license>\n'
            '</package>\n'
        )
        issues = validate_package_xml(str(pkg_xml))
        errors = [i for i in issues if i['severity'] == 'error']
        assert any('name' in i['message'] for i in errors)

    def test_missing_license_warns(self, tmp_path):
        pkg_xml = tmp_path / 'package.xml'
        pkg_xml.write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            '  <name>test_pkg</name>\n'
            '</package>\n'
        )
        issues = validate_package_xml(str(pkg_xml))
        warnings = [i for i in issues if i['severity'] == 'warning']
        assert any('license' in i['message'] for i in warnings)

    def test_invalid_xml_errors(self, tmp_path):
        pkg_xml = tmp_path / 'package.xml'
        pkg_xml.write_text('not xml at all')
        issues = validate_package_xml(str(pkg_xml))
        assert any(i['severity'] == 'error' for i in issues)

    def test_find_package_xmls(self, tmp_path):
        (tmp_path / 'pkg_a').mkdir()
        (tmp_path / 'pkg_a' / 'package.xml').write_text('<package/>')
        (tmp_path / 'pkg_b').mkdir()
        (tmp_path / 'pkg_b' / 'package.xml').write_text('<package/>')
        files = find_package_xmls(str(tmp_path))
        assert len(files) == 2

    def test_find_package_xmls_skips_build(self, tmp_path):
        build = tmp_path / 'build' / 'pkg'
        build.mkdir(parents=True)
        (build / 'package.xml').write_text('<package/>')
        files = find_package_xmls(str(tmp_path))
        assert len(files) == 0


class TestStopHookCLI:
    """Test the stop hook as a CLI command."""

    def test_clean_workspace_passes(self, tmp_path):
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'
        assert data['issues_count'] == 0

    def test_workspace_with_valid_artifacts(self, tmp_path):
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'test.launch.py').write_text(
            'from launch import LaunchDescription\n'
            'def generate_launch_description():\n'
            '    return LaunchDescription([])\n'
        )
        (tmp_path / 'package.xml').write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            '  <name>test</name>\n'
            '  <license>Apache-2.0</license>\n'
            '</package>\n'
        )
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'

    def test_workspace_with_errors_fails(self, tmp_path):
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'bad.launch.py').write_text(
            'from launch import LaunchDescription\n'
            'def wrong_name():\n'
            '    return LaunchDescription([])\n'
        )
        (tmp_path / 'package.xml').write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            '  <license>Apache-2.0</license>\n'
            '</package>\n'
        )
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data['status'] == 'fail'
        assert data['issues_count'] >= 1

    def _git(self, cwd, *args):
        return subprocess.run(
            ['git', '-C', str(cwd), *args],
            capture_output=True, text=True, check=True,
            env={**os.environ,
                 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'},
        )

    def test_committed_broken_file_does_not_block_stop(self, tmp_path):
        # A pre-existing broken launch file, committed and untouched by the
        # session, must not fail the Stop hook forever.
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'bad.launch.py').write_text(
            'def wrong_name():\n    pass\n')
        self._git(tmp_path, 'init', '-q')
        self._git(tmp_path, 'add', '-A')
        self._git(tmp_path, 'commit', '-qm', 'x')
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'

    def test_failure_reason_reaches_stderr(self, tmp_path):
        """Exit 1 is non-blocking, and Claude Code shows stderr for it.

        The stdout JSON is the machine-readable report, but it is not what
        the user is shown on a non-blocking hook failure — without a stderr
        copy the session ends with a bare "hook error" and no clue which
        file is broken.
        """
        (tmp_path / 'broken.launch.py').write_text(
            'def not_the_entry_point():\n    pass\n')
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)})
        assert result.returncode == 1
        assert json.loads(result.stdout)['status'] == 'fail'
        assert 'broken.launch.py' in result.stderr
        assert 'generate_launch_description' in result.stderr

    def test_git_touched_paths_non_repo_returns_none(self, tmp_path):
        assert _git_touched_paths(str(tmp_path)) is None

    def test_git_touched_paths_lists_modified_and_untracked(self, tmp_path):
        (tmp_path / 'tracked.txt').write_text('v1')
        self._git(tmp_path, 'init', '-q')
        self._git(tmp_path, 'add', '-A')
        self._git(tmp_path, 'commit', '-qm', 'x')
        (tmp_path / 'tracked.txt').write_text('v2')
        (tmp_path / 'new file.txt').write_text('n')  # quoted in porcelain
        touched = _git_touched_paths(str(tmp_path))
        assert touched is not None
        assert os.path.realpath(str(tmp_path / 'tracked.txt')) in touched
        assert os.path.realpath(str(tmp_path / 'new file.txt')) in touched

    def test_git_touched_paths_handles_escaped_non_ascii(self, tmp_path):
        """Non-ASCII paths must survive the porcelain round-trip.

        With core.quotePath at its default, `git status --porcelain`
        renders `café.txt` as `"caf\\303\\251.txt"`. Stripping the quotes
        without undoing the octal escapes yields a path that exists
        nowhere, so the file drops out of the touched set and is never
        validated — a silent gap, since the hook then just reports fewer
        files. Passing `-z` sidesteps the quoting entirely.
        """
        self._git(tmp_path, 'init', '-q')
        (tmp_path / 'café.txt').write_text('n', encoding='utf-8')
        (tmp_path / 'naïve node.launch.py').write_text('x', encoding='utf-8')
        touched = _git_touched_paths(str(tmp_path))
        assert touched is not None
        assert os.path.realpath(str(tmp_path / 'café.txt')) in touched
        assert os.path.realpath(
            str(tmp_path / 'naïve node.launch.py')) in touched

    def test_untracked_broken_file_still_fails(self, tmp_path):
        # In a git workspace, a broken file the session just created
        # (untracked) is still validated and fails the hook.
        self._git(tmp_path, 'init', '-q')
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'new.launch.py').write_text(
            'def wrong_name():\n    pass\n')
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data['status'] == 'fail'

    def test_output_is_valid_json(self, tmp_path):
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            capture_output=True, text=True,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
        )
        data = json.loads(result.stdout)
        assert 'hook' in data
        assert 'version' in data
        assert 'issues_count' in data
        assert 'issues' in data
        assert 'status' in data


class TestValidateHookAntiPatterns:
    """Test the PreToolUse hook's anti-pattern detection."""

    def test_detects_time_sleep(self):
        issues = check_content('time.sleep(5)', 'test.py')
        assert len(issues) >= 1
        assert any('sleep' in i['message'] for i in issues)

    def test_detects_spin_until_future_complete(self):
        issues = check_content(
            'rclpy.spin_until_future_complete(node, future)', 'test.py')
        assert len(issues) >= 1
        assert any('spin_until_future_complete' in i['message'] for i in issues)

    def test_detects_global_variables(self):
        issues = check_content('global node_state', 'test.py')
        assert len(issues) >= 1
        assert any('Global' in i['message'] for i in issues)

    def test_detects_ros_localhost_only(self):
        issues = check_content(
            'os.environ["ROS_LOCALHOST_ONLY"] = "1"', 'test.py')
        assert len(issues) >= 1
        assert any('ROS_LOCALHOST_ONLY' in i['message'] for i in issues)

    def test_detects_deprecated_node_executable(self):
        # Deprecated launch_ros kwarg - check only fires on .launch.py files
        # (false-positive guard added 2026-05; see ANTIPATTERN_CHECKS).
        issues = check_content(
            'Node(node_executable="my_node")', 'bringup.launch.py')
        assert len(issues) >= 1
        assert any('deprecated' in i['message'] for i in issues)

    def test_detects_deprecated_node_name(self):
        issues = check_content(
            'Node(node_name="my_node")', 'bringup.launch.py')
        assert len(issues) >= 1
        assert any('deprecated' in i['message'] for i in issues)

    def test_detects_deprecated_node_namespace(self):
        issues = check_content(
            'Node(node_namespace="/ns")', 'bringup.launch.py')
        assert len(issues) >= 1
        assert any('deprecated' in i['message'] for i in issues)

    def test_deprecated_kwargs_not_flagged_in_non_launch_file(self):
        """Regression: anti-pattern checks for launch-only kwargs must NOT
        fire on regular Python files. A dataclass or test fixture happening
        to use a `node_name=` parameter is not a deprecated launch_ros API."""
        for kwarg in ('node_name', 'node_executable', 'node_namespace'):
            src = (f'class Robot:\n'
                   f'    {kwarg} = "default_robot_name"\n')
            issues = check_content(src, 'robot.py')
            assert all('deprecated' not in i['message'] for i in issues), (
                f'{kwarg} should not be flagged in non-.launch.py files; '
                f'got: {issues}'
            )

    def test_global_false_positive_avoided_in_string_literal(self):
        """Regression: 'global' inside a string literal must NOT be flagged
        as a global statement. Only the `global X` Python statement counts."""
        src = 'config = {"global": True, "scope": "process"}\n'
        issues = check_content(src, 'cfg.py')
        assert all('Global variables' not in i['message'] for i in issues), (
            f'string-literal global must not be flagged; got: {issues}'
        )

    def test_global_statement_still_detected_at_line_start(self):
        """The legitimate Python `global` statement should still be caught."""
        src = ('def f():\n'
               '    global state\n'
               '    state = 1\n')
        issues = check_content(src, 'cfg.py')
        assert any('Global variables' in i['message'] for i in issues), (
            f'real global statement must still be flagged; got: {issues}'
        )

    def test_global_identifier_not_at_line_start_not_flagged(self):
        """An identifier containing 'global' (e.g. `global_var`) is not a
        `global` statement and must not trigger the warning."""
        src = ('global_var = 1\n'
               'my_global = "hi"\n'
               'x = global_settings.get("x")\n')
        issues = check_content(src, 'mod.py')
        assert all('Global variables' not in i['message'] for i in issues), (
            f'identifier with "global" prefix must not be flagged; '
            f'got: {issues}'
        )

    def test_clean_code_no_issues(self):
        clean_code = (
            'import rclpy\n'
            'class MyNode(Node):\n'
            '    def __init__(self):\n'
            '        super().__init__("my_node")\n'
        )
        issues = check_content(clean_code, 'test.py')
        assert len(issues) == 0

    def test_docstring_with_antipattern_is_flagged(self):
        # Documented limitation: the comment-skipping heuristic only handles
        # `#` and `//` single-line comments, not Python triple-quoted strings.
        # A docstring that mentions `time.sleep()` is expected to trigger a
        # warning. This test pins that behavior so the docstring in
        # skill_validate_hook.py stays accurate.
        code = (
            'def f():\n'
            '    """Avoid time.sleep(5) in ROS 2 nodes."""\n'
            '    return 1\n'
        )
        issues = check_content(code, 'test.py')
        assert any('time.sleep' in i['message'] for i in issues)

    def test_floor_division_not_treated_as_comment_in_python(self):
        # `//` is floor division in Python, not a comment; a match after it
        # on the same line must still be reported.
        code = 'n = total // 2; time.sleep(1)\n'
        issues = check_content(code, 'test.py')
        assert any('time.sleep' in i['message'] for i in issues)

    def test_double_slash_comment_skipped_in_cpp(self):
        code = 'int x = 1; // calls time.sleep(1) in the python port\n'
        issues = check_content(code, 'test.cpp')
        assert len(issues) == 0

    def test_hash_comment_skipped_in_python(self):
        code = 'x = 1  # time.sleep(1) would be wrong here\n'
        issues = check_content(code, 'test.py')
        assert len(issues) == 0

    def test_deprecated_kwargs_flagged_in_underscore_launch_file(self):
        code = "node_name='talker'\n"
        issues = check_content(code, 'robot_launch.py')
        assert any('node_name' in i['message'] for i in issues)

    def test_check_file_returns_empty_for_non_checkable(self, tmp_path):
        f = tmp_path / 'test.yaml'
        f.write_text('key: value')
        issues = check_file(str(f))
        assert len(issues) == 0

    def test_check_file_checks_python(self, tmp_path):
        f = tmp_path / 'test.py'
        f.write_text('time.sleep(1)')
        issues = check_file(str(f))
        assert len(issues) >= 1

    def test_check_file_checks_cpp(self, tmp_path):
        f = tmp_path / 'test.cpp'
        f.write_text('// clean C++ code\n')
        issues = check_file(str(f))
        assert len(issues) == 0

    def test_check_file_nonexistent(self):
        issues = check_file('/nonexistent/file.py')
        assert len(issues) == 0

    def test_antipattern_checks_non_empty(self):
        assert len(ANTIPATTERN_CHECKS) >= 5

    def test_checkable_extensions(self):
        assert '.py' in CHECKABLE_EXTENSIONS
        assert '.cpp' in CHECKABLE_EXTENSIONS
        assert '.hpp' in CHECKABLE_EXTENSIONS


class TestDangerousCommandDetection:
    """Test dangerous command detection in the PreToolUse hook."""

    def test_rm_rf_root(self):
        issues = _check_dangerous_commands('rm -rf /')
        assert len(issues) >= 1
        assert any('root' in i['message'].lower() for i in issues)

    def test_rm_rf_root_star(self):
        issues = _check_dangerous_commands('rm -rf /*')
        assert len(issues) >= 1

    def test_rm_rf_opt_ros(self):
        issues = _check_dangerous_commands('rm -rf /opt/ros')
        assert len(issues) >= 1
        assert any('ROS' in i['message'] for i in issues)

    def test_rm_rf_system_dirs(self):
        for d in ['/usr', '/bin', '/etc', '/var', '/boot', '/lib']:
            issues = _check_dangerous_commands(f'rm -rf {d}')
            assert len(issues) >= 1, f"Should detect rm -rf {d}"

    def test_rm_rf_home(self):
        issues = _check_dangerous_commands('rm -rf ~')
        assert len(issues) >= 1

    def test_mkfs_detected(self):
        issues = _check_dangerous_commands('mkfs.ext4 /dev/sda1')
        assert len(issues) >= 1
        assert any('mkfs' in i['message'] for i in issues)

    def test_dd_to_disk(self):
        issues = _check_dangerous_commands('dd if=/dev/zero of=/dev/sda')
        assert len(issues) >= 1
        assert any('dd' in i['message'].lower() for i in issues)

    def test_chmod_777_root(self):
        issues = _check_dangerous_commands('chmod -R 777 /')
        assert len(issues) >= 1
        assert any('chmod' in i['message'] for i in issues)

    def test_rm_rf_system_dir_with_trailing_slash_or_star(self):
        for cmd in ['rm -rf /usr/', 'rm -rf /var/*']:
            issues = _check_dangerous_commands(cmd)
            assert len(issues) >= 1, f"Should detect {cmd}"

    def test_rm_rf_in_compound_command(self):
        for cmd in ['rm -rf /; echo done', 'rm -rf / && true',
                    'rm -rf ~ && echo gone', 'rm -rf /etc; ls']:
            issues = _check_dangerous_commands(cmd)
            assert len(issues) >= 1, f"Should detect {cmd}"

    def test_subpaths_of_system_dirs_allowed(self):
        # Deleting under a system root is routine (e.g. Docker apt cleanup);
        # only removal of the root itself should be blocked.
        for cmd in ['rm -rf /var/lib/apt/lists/*',
                    'rm -rf /var/tmp/build_cache',
                    'rm -rf /usr/local/share/mystuff']:
            issues = _check_dangerous_commands(cmd)
            assert len(issues) == 0, f"Safe command flagged: {cmd}"

    def test_chmod_777_non_root_path_allowed(self):
        issues = _check_dangerous_commands('chmod -R 777 /home/user/my_ws')
        assert len(issues) == 0

    def test_chmod_777_root_in_compound_command(self):
        issues = _check_dangerous_commands('chmod -R 777 / && echo done')
        assert len(issues) >= 1

    def test_safe_commands_pass(self):
        safe_commands = [
            'colcon build',
            'ros2 run demo_nodes_cpp talker',
            'rm -rf build/ install/ log/',
            'cat /etc/os-release',
        ]
        for cmd in safe_commands:
            issues = _check_dangerous_commands(cmd)
            assert len(issues) == 0, f"Safe command flagged: {cmd}"

    def test_dangerous_patterns_non_empty(self):
        assert len(DANGEROUS_COMMAND_PATTERNS) >= 5


class TestPowerShellDangerousCommands:
    """PowerShell / Windows destructive-command coverage.

    Maintainer works on Windows and `pwsh`/`powershell` invocations can be
    forwarded under TOOL_NAME=Bash by the harness. The bash-only patterns left
    the entire Windows surface unguarded — Remove-Item, Format-Volume, etc.
    slipped through. These checks pin the regression and verify that safe
    PowerShell operations are not over-matched.
    """

    def test_remove_item_drive_root(self):
        issues = _check_dangerous_commands('Remove-Item -Recurse -Force C:/')
        assert len(issues) >= 1
        assert any('drive root' in i['message'].lower() for i in issues)

    def test_remove_item_drive_root_backslash(self):
        issues = _check_dangerous_commands('Remove-Item -Recurse -Force C:\\')
        assert len(issues) >= 1

    def test_remove_item_flag_order_swapped(self):
        # PowerShell parameter order is free — -Force first must also be caught.
        issues = _check_dangerous_commands('Remove-Item -Force -Recurse C:/')
        assert len(issues) >= 1

    def test_remove_item_case_insensitive(self):
        # PowerShell cmdlets are case-insensitive.
        issues = _check_dangerous_commands('remove-item -recurse -force c:/')
        assert len(issues) >= 1

    def test_remove_item_home(self):
        for target in ['$HOME', '$env:USERPROFILE', '~']:
            issues = _check_dangerous_commands(
                f'Remove-Item -Recurse -Force {target}')
            assert len(issues) >= 1, f'should flag home target {target!r}'

    def test_remove_item_windows_directories(self):
        for d in ['Windows', 'Program Files', 'Program Files (x86)', 'Users']:
            issues = _check_dangerous_commands(
                f'Remove-Item -Recurse -Force C:/{d}')
            assert len(issues) >= 1, f'should flag critical dir {d!r}'

    def test_format_volume(self):
        issues = _check_dangerous_commands('Format-Volume -DriveLetter C')
        assert len(issues) >= 1
        assert any('format' in i['message'].lower() for i in issues)

    def test_clear_disk(self):
        issues = _check_dangerous_commands('Clear-Disk -Number 0 -RemoveData')
        assert len(issues) >= 1
        assert any('clear' in i['message'].lower() for i in issues)

    def test_remove_partition(self):
        issues = _check_dangerous_commands(
            'Remove-Partition -DriveLetter D -Confirm:$false')
        assert len(issues) >= 1
        assert any('partition' in i['message'].lower() for i in issues)

    def test_rmdir_drive_root(self):
        issues = _check_dangerous_commands('rmdir /s /q C:\\')
        assert len(issues) >= 1

    def test_safe_powershell_commands_pass(self):
        safe = [
            'Get-Item C:/',
            'Remove-Item C:/Users/me/build',  # specific subdir, not root
            'Format-Table',                   # not Format-Volume
            'Get-ChildItem -Recurse -Force',  # no destructive verb
            'Clear-Host',                     # not Clear-Disk
            'New-Item -ItemType Directory C:/temp/build',
        ]
        for cmd in safe:
            issues = _check_dangerous_commands(cmd)
            assert len(issues) == 0, f'safe PS command flagged: {cmd!r}'


class TestPowerShellToolName:
    """The hook must route PowerShell tool invocations through the same
    dangerous-command pipeline. Previously the tool-name allowlist contained
    only bash-like aliases, so `TOOL_NAME=PowerShell` skipped the check
    entirely even when the payload was a destructive command.
    """

    def _run(self, tool_name, command):
        tool_input = json.dumps({'command': command})
        return subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': tool_name, 'TOOL_INPUT': tool_input},
        )

    def test_powershell_destructive_blocked(self):
        result = self._run('PowerShell', 'Remove-Item -Recurse -Force C:/')
        assert result.returncode == BLOCKING_EXIT, \
            'PowerShell tool name must route to dangerous-command check'
        assert 'Refusing' in result.stderr

    def test_pwsh_destructive_blocked(self):
        result = self._run('pwsh', 'Format-Volume -DriveLetter C')
        assert result.returncode == BLOCKING_EXIT
        assert 'Refusing' in result.stderr

    def test_cmd_destructive_blocked(self):
        result = self._run('cmd', 'rmdir /s /q C:\\')
        assert result.returncode == BLOCKING_EXIT
        assert 'Refusing' in result.stderr

    def test_bash_destructive_still_blocked(self):
        # Regression guard: PowerShell additions must not have weakened the
        # existing bash branch.
        result = self._run('Bash', 'rm -rf /')
        assert result.returncode == BLOCKING_EXIT
        assert 'Refusing' in result.stderr

    def test_rm_rf_root_cli(self):
        """Test via CLI that rm -rf / is blocked."""
        tool_input = json.dumps({'command': 'rm -rf /'})
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Bash', 'TOOL_INPUT': tool_input},
        )
        assert result.returncode == BLOCKING_EXIT
        # The reason must be on stderr, the channel Claude Code reads for a
        # blocking exit, and stdout must stay empty: unrecognized JSON
        # alongside exit 2 is what can turn a block back into a warning.
        assert 'root filesystem' in result.stderr
        assert result.stdout.strip() == ''


class TestValidateHookCLI:
    """Test the PreToolUse hook as a CLI command."""

    def test_no_input_passes(self):
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': '', 'TOOL_INPUT': ''},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'

    def test_write_clean_code_passes(self):
        tool_input = json.dumps({
            'file_path': 'test.py',
            'content': 'import rclpy\nclass MyNode: pass\n',
        })
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Write', 'TOOL_INPUT': tool_input},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'

    def test_write_antipattern_warns(self):
        tool_input = json.dumps({
            'file_path': 'test.py',
            'content': 'time.sleep(5)\n',
        })
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Write', 'TOOL_INPUT': tool_input},
        )
        assert result.returncode == 0  # Warnings don't block
        data = json.loads(result.stdout)
        assert data['issues_count'] >= 1

    def test_dangerous_bash_command_fails(self):
        tool_input = json.dumps({
            'command': 'rm -rf /opt/ros',
        })
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Bash', 'TOOL_INPUT': tool_input},
        )
        assert result.returncode == BLOCKING_EXIT
        assert 'ROS installation' in result.stderr
        assert result.stdout.strip() == ''

    def test_output_is_valid_json(self):
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': '', 'TOOL_INPUT': ''},
        )
        data = json.loads(result.stdout)
        assert 'hook' in data
        assert 'version' in data
        assert 'issues_count' in data
        assert 'issues' in data
        assert 'status' in data

    def test_edit_tool_with_antipattern(self):
        tool_input = json.dumps({
            'file_path': 'test.py',
            'new_string': 'global node_state\n',
        })
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Edit', 'TOOL_INPUT': tool_input},
        )
        assert result.returncode == 0  # Warnings don't block
        data = json.loads(result.stdout)
        assert data['issues_count'] >= 1

    def test_invalid_json_input_passes(self):
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Write', 'TOOL_INPUT': 'not json'},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'


class TestStopHookMainDirect:
    """Test skill_stop_hook.main() directly for coverage."""

    def test_main_clean_workspace(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from skill_stop_hook import main
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path))
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_with_valid_launch(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from skill_stop_hook import main
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'ok.launch.py').write_text(
            'from launch import LaunchDescription\n'
            'def generate_launch_description():\n'
            '    return LaunchDescription([])\n'
        )
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path))
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_with_error_launch(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from skill_stop_hook import main
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'bad.launch.py').write_text(
            'from launch import LaunchDescription\n'
            'def wrong_name():\n'
            '    return LaunchDescription([])\n'
        )
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path))
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_with_package_xml(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from skill_stop_hook import main
        (tmp_path / 'package.xml').write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3"><name>t</name>'
            '<license>Apache-2.0</license></package>\n'
        )
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path))
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_missing_name_in_pkg_xml(self, tmp_path, monkeypatch):
        import pytest as _pytest
        from skill_stop_hook import main
        (tmp_path / 'package.xml').write_text(
            '<?xml version="1.0"?>\n'
            '<package format="3"><license>Apache-2.0</license></package>\n'
        )
        monkeypatch.setenv('SKILL_WORKSPACE', str(tmp_path))
        with _pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestValidateHookMainDirect:
    """Test skill_validate_hook.main() directly for coverage."""

    def test_main_no_input(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', '')
        monkeypatch.setenv('TOOL_INPUT', '')
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_write_clean(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Write')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'file_path': 'test.py',
            'content': 'import rclpy\n',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_write_antipattern(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Write')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'file_path': 'test.py',
            'content': 'time.sleep(5)\n',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0  # Warnings don't block

    def test_main_edit_antipattern(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Edit')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'file_path': 'test.py',
            'new_string': 'global node_ref\n',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_bash_dangerous(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Bash')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'command': 'rm -rf /opt/ros',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == BLOCKING_EXIT

    def test_main_bash_safe(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Bash')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'command': 'colcon build',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_bash_invalid_json(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Bash')
        monkeypatch.setenv('TOOL_INPUT', 'not json')
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_write_no_content(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Write')
        monkeypatch.setenv('TOOL_INPUT', json.dumps({
            'file_path': 'test.py',
        }))
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0

    def test_main_invalid_json_write(self, monkeypatch):
        import pytest as _pytest
        from skill_validate_hook import main
        monkeypatch.setenv('TOOL_NAME', 'Write')
        monkeypatch.setenv('TOOL_INPUT', '{bad json')
        with _pytest.raises(SystemExit) as exc_info:
            main(argv=[])
        assert exc_info.value.code == 0


class TestClaudeCodeStdinPayload:
    """Real Claude Code sends the hook payload as JSON on STDIN, not via
    env vars. Schema verified 2026-05-21 by capturing actual fires::

        {"session_id":..., "cwd":..., "hook_event_name":"PreToolUse",
         "tool_name":"Bash", "tool_input": {"command": "..."}, ...}

    Note `tool_input` is already a dict, not a JSON string.
    """

    def _run_stdin(self, payload):
        return subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            input=json.dumps(payload),
            capture_output=True, text=True,
            # Strip env vars that could short-circuit the stdin path
            env={k: v for k, v in os.environ.items()
                 if k not in ('TOOL_NAME', 'TOOL_INPUT')},
        )

    def test_stdin_bash_destructive_blocks(self):
        r = self._run_stdin({
            'session_id': 'test', 'cwd': '/tmp',
            'hook_event_name': 'PreToolUse',
            'tool_name': 'Bash',
            'tool_input': {'command': 'rm -rf /'},
        })
        assert r.returncode == BLOCKING_EXIT
        assert 'root filesystem' in r.stderr
        assert r.stdout.strip() == ''

    def test_stdin_powershell_destructive_blocks(self):
        r = self._run_stdin({
            'session_id': 'test', 'cwd': '/tmp',
            'hook_event_name': 'PreToolUse',
            'tool_name': 'Bash',  # Claude Code surfaces shell calls as Bash
            'tool_input': {'command': 'Remove-Item -Recurse -Force C:/'},
        })
        assert r.returncode == BLOCKING_EXIT

    def test_stdin_edit_antipattern_warns_passes(self):
        r = self._run_stdin({
            'session_id': 'test', 'cwd': '/tmp',
            'hook_event_name': 'PreToolUse',
            'tool_name': 'Edit',
            'tool_input': {
                'file_path': '/tmp/x.py',
                'old_string': 'pass',
                'new_string': 'import time\ntime.sleep(1)',
            },
        })
        assert r.returncode == 0  # warning, not blocking
        data = json.loads(r.stdout)
        assert data['issues_count'] == 1
        assert 'time.sleep' in data['issues'][0]['message']

    def test_stdin_multiedit_flattens_all_edits(self):
        r = self._run_stdin({
            'session_id': 'test', 'cwd': '/tmp',
            'hook_event_name': 'PreToolUse',
            'tool_name': 'MultiEdit',
            'tool_input': {
                'file_path': '/tmp/x.py',
                'edits': [
                    {'old_string': 'a', 'new_string': 'clean = 1'},
                    {'old_string': 'b', 'new_string': 'time.sleep(2)'},
                ],
            },
        })
        # MultiEdit must scan the concatenated new_strings for antipatterns.
        data = json.loads(r.stdout)
        assert any('time.sleep' in i['message']
                   for i in data['issues']), data

    def test_stdin_tool_input_as_dict_not_string(self):
        # Real Claude Code sends tool_input as object, not a JSON string.
        # Hook must NOT try to json.loads it a second time.
        r = self._run_stdin({
            'tool_name': 'Write',
            'tool_input': {
                'file_path': '/tmp/x.py',
                'content': 'def main(): pass\n',
            },
        })
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data['status'] == 'pass'

    def test_stdin_empty_input_does_not_crash(self):
        r = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            input='',
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if k not in ('TOOL_NAME', 'TOOL_INPUT')},
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data['status'] == 'pass'
        assert data['issues_count'] == 0

    def test_stdin_malformed_json_falls_back_safely(self):
        r = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            input='{not valid json',
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if k not in ('TOOL_NAME', 'TOOL_INPUT')},
        )
        # Should not crash; falls through to env (which we cleared) so no
        # tool_name -> no checks run -> pass.
        assert r.returncode == 0

    def test_stdin_takes_precedence_over_env_for_real_payload(self):
        r = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')],
            input=json.dumps({
                'tool_name': 'Bash',
                'tool_input': {'command': 'rm -rf /'},
            }),
            capture_output=True, text=True,
            env={**os.environ,
                 'TOOL_NAME': 'Write',  # would be safe
                 'TOOL_INPUT': '{"file_path":"/tmp/x.py","content":"clean"}'},
        )
        # Stdin has destructive Bash -> must block, even though env says Write.
        assert r.returncode == BLOCKING_EXIT

    def test_debug_mode_emits_debug_info(self):
        r = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py'),
             '--debug'],
            input=json.dumps({
                'tool_name': 'Bash',
                'tool_input': {'command': 'colcon build'},
            }),
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if k not in ('TOOL_NAME', 'TOOL_INPUT')},
        )
        data = json.loads(r.stdout)
        assert 'debug' in data
        assert data['debug']['source'] == 'stdin'
        assert data['debug']['tool_name'] == 'Bash'


class TestStopHookWorkspaceResolution:
    """Stop hook must pick the workspace from real Claude Code's stdin
    payload (cwd field) or CLAUDE_PROJECT_DIR env, not just cwd().
    """

    def _run_stop(self, payload=None, env_overrides=None, cwd=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ('SKILL_WORKSPACE', 'CLAUDE_PROJECT_DIR')}
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')],
            input=json.dumps(payload) if payload else '',
            capture_output=True, text=True,
            env=env, cwd=cwd,
        )

    def test_stdin_cwd_used_when_no_env(self, tmp_path):
        # Create a fake pkg.xml in tmp_path; stop hook should find it
        # because it walks the cwd from stdin payload.
        pkg = tmp_path / 'package.xml'
        pkg.write_text(
            '<?xml version="1.0"?><package format="3">'
            '<name>x</name><license>Apache-2.0</license>'
            '</package>',
            encoding='utf-8')
        r = self._run_stop({'cwd': str(tmp_path),
                            'hook_event_name': 'Stop'})
        data = json.loads(r.stdout)
        # Pass means it scanned + found a clean package.xml.
        assert data['status'] == 'pass'
        assert data['issues_count'] == 0

    def test_claude_project_dir_env_used_when_no_stdin(self, tmp_path):
        # No stdin payload, but CLAUDE_PROJECT_DIR env -> use that.
        pkg = tmp_path / 'package.xml'
        pkg.write_text(
            '<?xml version="1.0"?><package format="3">'
            '<name>x</name><license>Apache-2.0</license>'
            '</package>',
            encoding='utf-8')
        r = self._run_stop(
            env_overrides={'CLAUDE_PROJECT_DIR': str(tmp_path)},
        )
        data = json.loads(r.stdout)
        assert data['status'] == 'pass'

    def test_skill_workspace_env_overrides_everything(self, tmp_path):
        # Even if stdin payload says a different cwd, SKILL_WORKSPACE wins
        # (used by pytest for hermetic test workspaces).
        other = tmp_path / 'other'
        other.mkdir()
        target = tmp_path / 'target'
        target.mkdir()
        r = self._run_stop(
            payload={'cwd': str(other), 'hook_event_name': 'Stop'},
            env_overrides={'SKILL_WORKSPACE': str(target)},
        )
        # Both dirs are empty (no package.xml/launch) -> clean pass.
        assert r.returncode == 0


class TestReadToolContextDirect:
    """Cover _read_tool_context branches directly so we can assert the
    parsing precisely rather than only through CLI surface."""

    def test_env_fallback_with_invalid_json(self, monkeypatch):
        # Reading the source module directly is required so monkeypatch on
        # sys.stdin and os.environ takes effect inside the call.
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_validate_hook import _read_tool_context
        import io
        # Empty stdin -> falls through to env
        monkeypatch.setattr('sys.stdin', io.StringIO(''))
        monkeypatch.setenv('TOOL_NAME', 'Bash')
        monkeypatch.setenv('TOOL_INPUT', '{not valid json')
        name, data, debug = _read_tool_context()
        assert name == 'Bash'
        assert data == {}  # malformed env JSON -> empty dict
        assert debug['source'] == 'env'
        assert 'env_parse_error' in debug

    def test_stdin_non_dict_payload_falls_through(self, monkeypatch):
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_validate_hook import _read_tool_context
        import io
        # Top-level JSON array (not dict) -> ignored, fall through to env
        monkeypatch.setattr('sys.stdin', io.StringIO('[1, 2, 3]'))
        monkeypatch.delenv('TOOL_NAME', raising=False)
        monkeypatch.delenv('TOOL_INPUT', raising=False)
        name, data, debug = _read_tool_context()
        assert name == ''
        assert data == {}
        # Source falls through to env since stdin payload was unusable.
        assert debug['source'] == 'env'

    def test_stdin_tool_input_non_dict_normalized_to_empty(self, monkeypatch):
        # Real Claude Code always sends tool_input as object, but defensively
        # if a future schema version wraps it (e.g. as string), we must not
        # crash - we normalize to empty dict.
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_validate_hook import _read_tool_context
        import io
        monkeypatch.setattr('sys.stdin',
                            io.StringIO(json.dumps({
                                'tool_name': 'Bash',
                                'tool_input': 'should-be-an-object-not-string',
                            })))
        monkeypatch.delenv('TOOL_NAME', raising=False)
        name, data, debug = _read_tool_context()
        assert name == 'Bash'
        assert data == {}  # non-dict tool_input normalized
        assert debug['source'] == 'stdin'


class TestResolveWorkspaceDirect:
    """Cover _resolve_workspace branches directly."""

    def test_falls_back_to_cwd_when_nothing_else(self, monkeypatch, tmp_path):
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_stop_hook import _resolve_workspace
        import io
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
        monkeypatch.setattr('sys.stdin', io.StringIO(''))
        monkeypatch.chdir(str(tmp_path))
        ws = _resolve_workspace()
        # Resolve via realpath comparison (tmp_path on Windows may have
        # different drive-letter casing than os.getcwd()).
        assert os.path.realpath(ws) == os.path.realpath(str(tmp_path))

    def test_stdin_cwd_ignored_if_not_a_directory(self, monkeypatch, tmp_path):
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_stop_hook import _resolve_workspace
        import io
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
        # Path that does not exist -> falls through.
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({
            'cwd': r'C:\definitely\not\a\real\directory\xyz',
        })))
        monkeypatch.chdir(str(tmp_path))
        ws = _resolve_workspace()
        # Did not honor the bogus cwd; fell through to os.getcwd().
        assert os.path.realpath(ws) == os.path.realpath(str(tmp_path))

    def test_claude_project_dir_used_when_no_stdin(self, monkeypatch, tmp_path):
        sys.path.insert(0, SCRIPTS_DIR)
        from skill_stop_hook import _resolve_workspace
        import io
        monkeypatch.delenv('SKILL_WORKSPACE', raising=False)
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
        monkeypatch.setattr('sys.stdin', io.StringIO(''))
        assert os.path.realpath(_resolve_workspace()) == os.path.realpath(
            str(tmp_path))


class TestStopHookRunsLogOptIn:
    """.skill-runs.log is opt-in via SKILL_RUNS_LOG.

    Without the opt-in the Stop hook must never write into the workspace —
    a read-only session that triggers the hook must leave the working tree
    exactly as it found it.
    """

    HOOK = os.path.join(SCRIPTS_DIR, 'skill_stop_hook.py')

    def _run(self, workspace, env_extra=None):
        env = os.environ.copy()
        env.pop('SKILL_RUNS_LOG', None)
        env['SKILL_WORKSPACE'] = str(workspace)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, self.HOOK],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL)

    def test_no_log_written_by_default(self, tmp_path):
        result = self._run(tmp_path)
        assert result.returncode == 0
        assert not (tmp_path / '.skill-runs.log').exists()

    def test_default_run_does_not_dirty_git_worktree(self, tmp_path):
        subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)

        def porcelain():
            return subprocess.run(
                ['git', '-C', str(tmp_path), 'status', '--porcelain'],
                capture_output=True, text=True).stdout

        before = porcelain()
        result = self._run(tmp_path)
        assert result.returncode == 0
        assert porcelain() == before

    def test_log_written_when_opted_in(self, tmp_path):
        result = self._run(tmp_path, {'SKILL_RUNS_LOG': '1'})
        assert result.returncode == 0
        log = tmp_path / '.skill-runs.log'
        assert log.exists()
        entry = json.loads(log.read_text(encoding='utf-8').splitlines()[0])
        assert entry['status'] == 'pass'
        assert 'yaml_files_checked' in entry

    def test_log_written_to_custom_absolute_path(self, tmp_path):
        state_dir = tmp_path / 'agent-state'
        state_dir.mkdir()
        log = state_dir / 'runs.log'
        result = self._run(tmp_path, {'SKILL_RUNS_LOG': str(log)})
        assert result.returncode == 0
        assert log.exists()
        # Nothing landed in the workspace itself.
        assert not (tmp_path / '.skill-runs.log').exists()

    def test_missing_parent_directory_never_fails_the_hook(self, tmp_path):
        bogus = tmp_path / 'no' / 'such' / 'dir' / 'runs.log'
        result = self._run(tmp_path, {'SKILL_RUNS_LOG': str(bogus)})
        # Logging is best-effort: a bad destination must not fail validation.
        assert result.returncode == 0
        assert not bogus.exists()

    def test_resolve_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv('SKILL_RUNS_LOG', raising=False)
        assert _resolve_log_path('/ws') is None

    def test_resolve_blank_returns_none(self, monkeypatch):
        monkeypatch.setenv('SKILL_RUNS_LOG', '   ')
        assert _resolve_log_path('/ws') is None

    def test_resolve_truthy_uses_workspace_default(self, monkeypatch):
        for truthy in ('1', 'true', 'YES'):
            monkeypatch.setenv('SKILL_RUNS_LOG', truthy)
            assert _resolve_log_path('/ws') == os.path.join(
                '/ws', '.skill-runs.log')

    def test_resolve_relative_path_joins_workspace(self, monkeypatch):
        monkeypatch.setenv('SKILL_RUNS_LOG', os.path.join('logs', 'r.log'))
        assert _resolve_log_path('/ws') == os.path.join(
            '/ws', 'logs', 'r.log')


class TestDistroOrdering:
    """_distro_at_least uses the explicit order table, never string compare."""

    def test_equal_boundary(self):
        assert _distro_at_least('humble', 'humble') is True

    def test_newer_than_boundary(self):
        assert _distro_at_least('jazzy', 'humble') is True
        assert _distro_at_least('iron', 'humble') is True

    def test_older_than_boundary(self):
        assert _distro_at_least('galactic', 'humble') is False
        assert _distro_at_least('foxy', 'galactic') is False

    def test_unknown_distro_is_none(self):
        assert _distro_at_least('rolling', 'humble') is None
        assert _distro_at_least('quixotic', 'humble') is None

    def test_unset_is_none(self):
        assert _distro_at_least(None, 'humble') is None
        assert _distro_at_least('', 'humble') is None

    def test_case_and_whitespace_tolerant(self):
        assert _distro_at_least(' Humble ', 'humble') is True


class TestStopHookNav2YamlLint:
    """Distro-aware lint for Nav2 parameter YAML: syntax + legacy names.

    The two legacy identifiers have DIFFERENT distro boundaries and must be
    classified separately: recovery naming changed in Humble, the BT
    navigator parameter changed in Galactic.
    """

    _LEGACY_RECOVERY = (
        'recoveries_server:\n'
        '  ros__parameters:\n'
        '    recovery_plugins: ["spin"]\n'
        '    spin:\n'
        '      plugin: "nav2_recoveries/Spin"\n'
    )
    _LEGACY_BT_PARAM = (
        'bt_navigator:\n'
        '  ros__parameters:\n'
        '    default_bt_xml_filename: "my_bt.xml"\n'
    )

    def test_legacy_recovery_naming_flagged_on_humble(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_RECOVERY, encoding='utf-8')
        issues = validate_nav2_yaml(str(f))
        assert len(issues) == 1
        assert issues[0]['severity'] == 'warning'
        assert 'pre-Humble recovery naming' in issues[0]['message']

    def test_legacy_recovery_naming_valid_on_galactic(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'galactic')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_RECOVERY, encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_pre_galactic_bt_param_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_BT_PARAM, encoding='utf-8')
        issues = validate_nav2_yaml(str(f))
        assert len(issues) == 1
        assert issues[0]['severity'] == 'warning'
        assert 'pre-Galactic BT navigator parameter' in issues[0]['message']

    def test_pre_galactic_bt_param_valid_on_foxy(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'foxy')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_BT_PARAM, encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_unknown_distro_adds_confirm_note(self, tmp_path, monkeypatch):
        monkeypatch.delenv('ROS_DISTRO', raising=False)
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_RECOVERY, encoding='utf-8')
        issues = validate_nav2_yaml(str(f))
        assert len(issues) == 1
        assert 'confirm the target distro' in issues[0]['message']

    def test_boundaries_classified_separately(self, tmp_path, monkeypatch):
        """On Galactic the recovery naming is fine but the BT parameter is
        already legacy — the two advisories must not share a boundary."""
        monkeypatch.setenv('ROS_DISTRO', 'galactic')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(self._LEGACY_RECOVERY + self._LEGACY_BT_PARAM,
                     encoding='utf-8')
        issues = validate_nav2_yaml(str(f))
        assert len(issues) == 1
        assert 'pre-Galactic BT navigator parameter' in issues[0]['message']

    def test_syntax_error_flagged_for_nav2_named_file(self, tmp_path):
        f = tmp_path / 'nav2_params.yaml'
        f.write_text('foo: [unclosed\n', encoding='utf-8')
        issues = validate_nav2_yaml(str(f))
        assert len(issues) == 1
        assert issues[0]['severity'] == 'error'
        assert 'YAML syntax error' in issues[0]['message']

    def test_syntax_error_skipped_for_unrelated_file(self, tmp_path):
        f = tmp_path / 'random_config.yaml'
        f.write_text('foo: [unclosed\n', encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_non_nav2_yaml_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        f = tmp_path / 'ci.yaml'
        f.write_text('jobs:\n  build:\n    steps: []\n', encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_commented_legacy_names_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(
            '# recoveries_server / nav2_recoveries/ was pre-Humble naming\n'
            'behavior_server:\n'
            '  ros__parameters:\n'
            '    behavior_plugins: ["wait"]\n'
            '    wait:\n'
            '      plugin: "nav2_behaviors/Wait"\n',
            encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_modern_humble_config_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        f = tmp_path / 'nav2_params.yaml'
        f.write_text(
            'bt_navigator:\n'
            '  ros__parameters:\n'
            '    default_nav_to_pose_bt_xml: "my_bt.xml"\n'
            'behavior_server:\n'
            '  ros__parameters:\n'
            '    behavior_plugins: ["wait", "spin", "backup"]\n'
            '    wait:\n'
            '      plugin: "nav2_behaviors/Wait"\n'
            '    spin:\n'
            '      plugin: "nav2_behaviors/Spin"\n'
            '    backup:\n'
            '      plugin: "nav2_behaviors/BackUp"\n',
            encoding='utf-8')
        assert validate_nav2_yaml(str(f)) == []

    def test_nonexistent_file_returns_no_issues(self):
        assert validate_nav2_yaml('/nonexistent/nav2_params.yaml') == []

    def test_find_yaml_files(self, tmp_path):
        (tmp_path / 'a.yaml').write_text('x: 1\n', encoding='utf-8')
        (tmp_path / 'b.yml').write_text('y: 2\n', encoding='utf-8')
        (tmp_path / 'c.txt').write_text('z\n', encoding='utf-8')
        build = tmp_path / 'build'
        build.mkdir()
        (build / 'skip.yaml').write_text('n: 0\n', encoding='utf-8')
        found = find_yaml_files(str(tmp_path))
        names = sorted(os.path.basename(p) for p in found)
        assert names == ['a.yaml', 'b.yml']


class TestValidateHookManualCLI:
    """--file/--command manual mode: no event payload needed, and named
    inputs must never produce a false pass (missing/unreadable files are
    errors, not silent skips)."""

    HOOK = os.path.join(SCRIPTS_DIR, 'skill_validate_hook.py')

    def _run(self, *args):
        env = os.environ.copy()
        env.pop('TOOL_NAME', None)
        env.pop('TOOL_INPUT', None)
        return subprocess.run(
            [sys.executable, self.HOOK, *args],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=10)

    def test_missing_file_is_error_not_skip(self, tmp_path):
        # A nonexistent .txt path must be reported missing, not
        # misclassified as an unsupported-extension skip.
        missing = tmp_path / 'missing.txt'
        result = self._run('--file', str(missing))
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data['mode'] == 'manual'
        assert data['status'] == 'fail'
        assert any('File not found' in i['message'] for i in data['issues'])
        assert data['checks_skipped'] == []

    def test_directory_is_error(self, tmp_path):
        result = self._run('--file', str(tmp_path))
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert any('regular file' in i['message'] for i in data['issues'])

    def test_unsupported_extension_is_skipped_not_failed(self, tmp_path):
        f = tmp_path / 'notes.md'
        f.write_text('time.sleep(1)\n', encoding='utf-8')
        result = self._run('--file', str(f))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'
        assert data['issues'] == []
        assert len(data['checks_skipped']) == 1
        assert 'unsupported extension' in data['checks_skipped'][0]

    def test_antipattern_file_warns_but_passes(self, tmp_path):
        f = tmp_path / 'node.py'
        f.write_text('import time\ntime.sleep(1)\n', encoding='utf-8')
        result = self._run('--file', str(f))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'
        assert any('time.sleep' in i['message'] for i in data['issues'])
        assert all(i['severity'] == 'warning' for i in data['issues'])

    def test_undecodable_file_is_error(self, tmp_path):
        f = tmp_path / 'binary.py'
        f.write_bytes(b'\xff\xfe\x00\x01binary')
        result = self._run('--file', str(f))
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert any('Cannot read file' in i['message'] for i in data['issues'])

    def test_dangerous_command_fails(self):
        result = self._run('--command', 'rm -rf /')
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data['mode'] == 'manual'
        assert data['status'] == 'fail'
        assert any(i['severity'] == 'error' for i in data['issues'])

    def test_safe_command_passes(self):
        result = self._run('--command', 'ros2 topic list')
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['status'] == 'pass'
        assert data['issues'] == []

    def test_file_and_command_are_aggregated(self, tmp_path):
        # One bad file must not stop the scan: the remaining file and the
        # command are still checked and everything lands in one report.
        good = tmp_path / 'node.py'
        good.write_text('time.sleep(1)\n', encoding='utf-8')
        missing = tmp_path / 'gone.py'
        result = self._run('--file', str(missing), str(good),
                           '--command', 'rm -rf /')
        assert result.returncode == 1
        data = json.loads(result.stdout)
        messages = [i['message'] for i in data['issues']]
        assert any('File not found' in m for m in messages)
        assert any('time.sleep' in m for m in messages)
        assert any('Refusing' in m for m in messages)

    def test_no_args_stays_in_event_mode(self):
        result = self._run()
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['mode'] == 'event'
        assert data['status'] == 'pass'
        assert data['checks_skipped'] == []

    def test_manual_and_event_modes_use_different_blocking_exit_codes(self):
        """The two modes answer to different contracts, deliberately.

        Manual mode is a plain CLI: non-zero means "issues found", it
        reports on stdout, and README documents `--command 'rm -rf /'` as
        exit 1. Event mode speaks Claude Code's hook protocol, where only
        exit 2 refuses the tool call, the reason has to be on stderr, and
        stdout stays empty. Collapsing the two back into one shape
        silently breaks whichever contract loses.
        """
        same_command = 'rm -rf /'

        manual = self._run('--command', same_command)
        assert manual.returncode == 1
        assert json.loads(manual.stdout)['mode'] == 'manual'
        assert manual.stderr.strip() == ''

        event = subprocess.run(
            [sys.executable, self.HOOK],
            input=json.dumps({
                'hook_event_name': 'PreToolUse',
                'tool_name': 'Bash',
                'tool_input': {'command': same_command},
            }),
            capture_output=True, text=True, timeout=10,
            env={k: v for k, v in os.environ.items()
                 if k not in ('TOOL_NAME', 'TOOL_INPUT')},
        )
        assert event.returncode == BLOCKING_EXIT
        assert 'Refusing' in event.stderr
        assert event.stdout.strip() == ''


class TestDistroOrderingLyrical:
    """Lyrical is a known release and must order, not fall to 'unknown'."""

    def test_lyrical_is_newer_than_humble(self):
        assert _distro_at_least('lyrical', 'humble') is True

    def test_lyrical_is_newer_than_galactic(self):
        assert _distro_at_least('lyrical', 'galactic') is True


class TestStopHookDiagnostics:
    """checks_skipped and severity-tagged log summaries."""

    def _run_main(self, monkeypatch, capsys, workspace):
        import pytest as _pytest
        import skill_stop_hook
        monkeypatch.setenv('SKILL_WORKSPACE', str(workspace))
        with _pytest.raises(SystemExit) as exc_info:
            skill_stop_hook.main()
        return exc_info.value.code, json.loads(capsys.readouterr().out)

    def test_checks_skipped_key_always_present(self, tmp_path,
                                               monkeypatch, capsys):
        monkeypatch.delenv('SKILL_RUNS_LOG', raising=False)
        code, data = self._run_main(monkeypatch, capsys, tmp_path)
        assert code == 0
        assert data['checks_skipped'] == []

    def test_pyyaml_skip_reported_only_with_yaml_in_scope(
            self, tmp_path, monkeypatch, capsys):
        import skill_stop_hook
        monkeypatch.delenv('SKILL_RUNS_LOG', raising=False)
        monkeypatch.setattr(skill_stop_hook, '_HAVE_YAML', False)
        (tmp_path / 'nav2_params.yaml').write_text(
            'bt_navigator:\n  ros__parameters: {}\n', encoding='utf-8')
        code, data = self._run_main(monkeypatch, capsys, tmp_path)
        assert code == 0
        assert data['checks_skipped'] == [
            'nav2_yaml: PyYAML is not installed']

    def test_no_pyyaml_skip_on_empty_workspace(self, tmp_path,
                                               monkeypatch, capsys):
        import skill_stop_hook
        monkeypatch.delenv('SKILL_RUNS_LOG', raising=False)
        monkeypatch.setattr(skill_stop_hook, '_HAVE_YAML', False)
        code, data = self._run_main(monkeypatch, capsys, tmp_path)
        assert code == 0
        # No YAML was in scope, so the lint had nothing to skip.
        assert data['checks_skipped'] == []

    def test_warning_only_run_logs_issue_summaries(self, tmp_path,
                                                   monkeypatch, capsys):
        monkeypatch.setenv('SKILL_RUNS_LOG', '1')
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        (tmp_path / 'nav2_params.yaml').write_text(
            'recoveries_server:\n'
            '  ros__parameters:\n'
            '    recovery_plugins: ["spin"]\n'
            '    spin:\n'
            '      plugin: "nav2_recoveries/Spin"\n',
            encoding='utf-8')
        code, data = self._run_main(monkeypatch, capsys, tmp_path)
        assert code == 0  # warnings never fail the hook
        assert data['issues_count'] == 1
        entry = json.loads((tmp_path / '.skill-runs.log').read_text(
            encoding='utf-8').splitlines()[0])
        # Pre-1.2 field kept for compatibility, empty on warning-only runs;
        # the new field carries the detail.
        assert entry['error_summaries'] == []
        assert len(entry['issue_summaries']) == 1
        assert entry['issue_summaries'][0].startswith('[warning] ')
        assert 'nav2_params.yaml' in entry['issue_summaries'][0]

    def test_issue_summaries_sort_errors_first(self, tmp_path,
                                               monkeypatch, capsys):
        monkeypatch.setenv('SKILL_RUNS_LOG', '1')
        monkeypatch.setenv('ROS_DISTRO', 'humble')
        (tmp_path / 'nav2_params.yaml').write_text(
            'bt_navigator:\n'
            '  ros__parameters:\n'
            '    default_bt_xml_filename: "x.xml"\n',
            encoding='utf-8')
        (tmp_path / 'launch').mkdir()
        (tmp_path / 'launch' / 'bad.launch.py').write_text(
            'def wrong_name():\n    pass\n', encoding='utf-8')
        code, data = self._run_main(monkeypatch, capsys, tmp_path)
        assert code == 1
        entry = json.loads((tmp_path / '.skill-runs.log').read_text(
            encoding='utf-8').splitlines()[0])
        assert entry['issue_summaries'][0].startswith('[error] ')
        assert any(s.startswith('[warning] ')
                   for s in entry['issue_summaries'])
