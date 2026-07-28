"""Tests for Skills 2.0 frontmatter — validates SKILL.md metadata completeness.

These tests ensure the skill conforms to Skills 2.0 requirements:
1. Frontmatter has all required fields
2. context: fork is declared
3. classification is valid
4. version follows semver
5. hooks are properly declared
6. evals are properly structured
7. every release-version surface carries the same version
"""

import json
import os
import re
import subprocess
import sys

import yaml


SKILL_ROOT = os.path.join(os.path.dirname(__file__), '..')
SKILL_MD = os.path.join(SKILL_ROOT, 'SKILL.md')


def _parse_frontmatter(filepath):
    """Extract YAML frontmatter from a markdown file."""
    with open(filepath, 'r', encoding='utf-8') as fh:
        content = fh.read()
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    assert match is not None, 'SKILL.md must start with YAML frontmatter (---)'
    return yaml.safe_load(match.group(1))


class TestSkills2FrontmatterStructure:
    """Validate that SKILL.md frontmatter contains all Skills 2.0 fields."""

    def setup_method(self):
        self.fm = _parse_frontmatter(SKILL_MD)

    def test_has_name(self):
        assert 'name' in self.fm
        assert isinstance(self.fm['name'], str)
        assert len(self.fm['name']) > 0

    def test_has_description(self):
        assert 'description' in self.fm
        assert isinstance(self.fm['description'], str)
        assert len(self.fm['description']) > 0

    def test_has_context_fork(self):
        assert 'context' in self.fm, 'Skills 2.0 requires context field'
        assert self.fm['context'] == 'fork', 'context must be "fork" for isolated execution'

    def test_has_classification(self):
        assert 'classification' in self.fm, 'Skills 2.0 requires classification field'
        valid_types = {'workflow', 'capability', 'hybrid'}
        assert self.fm['classification'] in valid_types, (
            f'classification must be one of {valid_types}, '
            f'got: {self.fm["classification"]}'
        )

    def test_has_version(self):
        assert 'version' in self.fm, 'Skills 2.0 requires version field'
        version = self.fm['version']
        assert re.match(r'^\d+\.\d+\.\d+$', str(version)), (
            f'version must follow semver (X.Y.Z), got: {version}'
        )

    def test_has_deprecation_risk(self):
        assert 'deprecation-risk' in self.fm, (
            'Skills 2.0 requires deprecation-risk field'
        )
        valid_levels = {'none', 'low', 'medium', 'high'}
        assert self.fm['deprecation-risk'] in valid_levels, (
            f'deprecation-risk must be one of {valid_levels}, '
            f'got: {self.fm["deprecation-risk"]}'
        )


class TestSkills2FrontmatterHooks:
    """Validate that hooks are properly declared in frontmatter."""

    def setup_method(self):
        self.fm = _parse_frontmatter(SKILL_MD)

    def test_has_hooks(self):
        assert 'hooks' in self.fm, 'Skills 2.0 requires hooks field'
        assert isinstance(self.fm['hooks'], dict)

    def test_hooks_have_valid_events(self):
        hooks = self.fm['hooks']
        valid_events = {
            'PreToolUse', 'PostToolUse', 'Stop',
            'NotificationArrived', 'SubagentStop',
        }
        for event in hooks:
            assert event in valid_events, (
                f'Hook event "{event}" is not a valid Skills 2.0 hook event. '
                f'Valid events: {valid_events}'
            )

    def test_stop_hook_exists(self):
        hooks = self.fm['hooks']
        assert 'Stop' in hooks, 'Must have a Stop hook for post-execution validation'
        stop_hooks = hooks['Stop']
        assert isinstance(stop_hooks, list)
        assert len(stop_hooks) > 0

    def test_hook_entries_match_claude_code_matcher_group_schema(self):
        """Claude Code hook frontmatter schema (verified 2026-05-21 against
        official docs https://code.claude.com/docs/en/hooks):

            hooks:
              <EventName>:
                - matcher: "<regex>"   # optional; omit/"*" = all
                  hooks:
                    - type: command
                      command: "..."
                      timeout: <seconds>

        Flat list of {type, command} directly under the event (which earlier
        revisions of this skill used) is NOT a valid schema and silently
        fails to register hooks - they become no-op in real Claude Code.
        """
        hooks = self.fm['hooks']
        for event, groups in hooks.items():
            assert isinstance(groups, list), (
                f'{event} must be a list of matcher groups, got {type(groups)}'
            )
            for i, group in enumerate(groups):
                assert isinstance(group, dict), (
                    f'{event}[{i}] must be a matcher-group dict'
                )
                # 'matcher' is optional. 'hooks' (the inner list of handlers)
                # is required.
                assert 'hooks' in group, (
                    f'{event}[{i}] missing inner "hooks" list - '
                    f'looks like flat schema, will silently no-op'
                )
                assert isinstance(group['hooks'], list)
                for j, entry in enumerate(group['hooks']):
                    assert 'type' in entry, (
                        f'{event}[{i}].hooks[{j}] missing "type"'
                    )
                    assert entry['type'] in ('command', 'script'), (
                        f'{event}[{i}].hooks[{j}] type must be '
                        f'"command" or "script"'
                    )
                    assert 'command' in entry, (
                        f'{event}[{i}].hooks[{j}] missing "command"'
                    )
                    assert isinstance(entry['command'], str)

    def test_hook_commands_use_valid_path_variables_only(self):
        """Claude Code documents exactly 3 path placeholders for hook
        commands: ${CLAUDE_PROJECT_DIR}, ${CLAUDE_PLUGIN_ROOT}, and
        ${CLAUDE_PLUGIN_DATA}. ${SKILL_ROOT} does NOT exist and is left
        literal at exec time, making the path invalid.
        """
        invalid_vars = ['${SKILL_ROOT}']
        valid_vars = ['${CLAUDE_PROJECT_DIR}', '${CLAUDE_PLUGIN_ROOT}',
                      '${CLAUDE_PLUGIN_DATA}']
        hooks = self.fm['hooks']
        for event, groups in hooks.items():
            for i, group in enumerate(groups):
                for j, entry in enumerate(group.get('hooks', [])):
                    cmd = entry.get('command', '')
                    for bad in invalid_vars:
                        assert bad not in cmd, (
                            f'{event}[{i}].hooks[{j}] command uses '
                            f'unrecognized variable {bad!r} - Claude Code '
                            f'leaves it literal -> file not found at runtime. '
                            f'Use one of {valid_vars}.'
                        )

    def test_hook_commands_reference_existing_scripts(self):
        hooks = self.fm['hooks']
        for event, groups in hooks.items():
            for i, group in enumerate(groups):
                for j, entry in enumerate(group.get('hooks', [])):
                    cmd = entry['command']
                    # Resolve documented placeholders against the local repo
                    # (CLAUDE_PLUGIN_ROOT == skill dir == SKILL_ROOT for our
                    # purposes during pytest).
                    script_path = cmd.replace(
                        '${CLAUDE_PLUGIN_ROOT}', SKILL_ROOT
                    ).replace(
                        '${CLAUDE_PROJECT_DIR}', SKILL_ROOT
                    )
                    parts = script_path.split()
                    if len(parts) >= 2:
                        script_file = parts[1]
                        assert os.path.isfile(script_file), (
                            f'{event}[{i}].hooks[{j}] references non-existent '
                            f'script: {script_file}'
                        )

    def test_hooks_have_timeout(self):
        """Hook timeouts are expressed in SECONDS.

        Claude Code documents `timeout` as "Seconds before canceling", with
        a 600 s default for command hooks. Writing a millisecond value here
        (10000 for "10 s") does not fail loudly - it silently installs a
        ~2.8 hour timeout, so a wedged validator hangs the session instead
        of being killed. The upper bound below is what makes that mistake
        fail in CI; keep it at or under the documented default.
        """
        max_seconds = 600
        hooks = self.fm['hooks']
        for event, groups in hooks.items():
            for i, group in enumerate(groups):
                for j, entry in enumerate(group.get('hooks', [])):
                    assert 'timeout' in entry, (
                        f'{event}[{i}].hooks[{j}] should have a timeout'
                    )
                    assert isinstance(entry['timeout'], (int, float))
                    assert entry['timeout'] > 0
                    assert entry['timeout'] <= max_seconds, (
                        f'{event}[{i}].hooks[{j}] timeout '
                        f'{entry["timeout"]} exceeds the {max_seconds}s '
                        f'command-hook default - timeout is in SECONDS, '
                        f'not milliseconds'
                    )

    def test_pretooluse_has_matcher(self):
        """PreToolUse without a matcher fires on EVERY tool call, which is
        excessive cost (hook runs on Read, Glob, etc. that have nothing to
        do with ROS 2). Scope it to file-mutation + shell tools.
        """
        hooks = self.fm['hooks']
        if 'PreToolUse' in hooks:
            for i, group in enumerate(hooks['PreToolUse']):
                assert 'matcher' in group, (
                    f'PreToolUse[{i}] missing matcher - hook will fire on '
                    f'every tool call. Add matcher: "Edit|Write|Bash|MultiEdit".'
                )


class TestSkills2FrontmatterEvals:
    """Eval definitions live in evals/eval.yaml — the single source of truth.

    Earlier revisions duplicated all eval definitions in SKILL.md
    frontmatter. The copies drifted (flat string criteria vs weighted
    criteria with ids/tags) and cost ~125 lines of always-loaded context.
    These tests validate the canonical eval.yaml and pin the removal of the
    frontmatter duplicate.
    """

    def setup_method(self):
        eval_yaml = os.path.join(SKILL_ROOT, 'evals', 'eval.yaml')
        with open(eval_yaml, 'r', encoding='utf-8') as fh:
            self.config = yaml.safe_load(fh)
        self.evals_dir = os.path.join(SKILL_ROOT, 'evals')

    def test_skill_md_frontmatter_has_no_evals(self):
        """Regression: eval definitions must not reappear in SKILL.md."""
        fm = _parse_frontmatter(SKILL_MD)
        assert 'evals' not in fm, (
            'SKILL.md frontmatter must not duplicate eval definitions — '
            'evals/eval.yaml is the single source of truth'
        )

    def test_has_evals(self):
        assert 'evals' in self.config, 'eval.yaml requires evals field'
        assert isinstance(self.config['evals'], list)
        assert len(self.config['evals']) > 0

    def test_evals_have_required_fields(self):
        for i, ev in enumerate(self.config['evals']):
            assert 'name' in ev, f'Eval {i} missing "name"'
            assert 'prompt' in ev, f'Eval {i} missing "prompt"'
            assert 'expected' in ev, f'Eval {i} missing "expected"'
            assert 'criteria' in ev, f'Eval {i} missing "criteria"'

    def test_eval_names_are_unique(self):
        names = [ev['name'] for ev in self.config['evals']]
        assert len(names) == len(set(names)), (
            f'Eval names must be unique, found duplicates: '
            f'{[n for n in names if names.count(n) > 1]}'
        )

    def test_eval_prompt_files_exist(self):
        for ev in self.config['evals']:
            prompt_path = os.path.join(self.evals_dir, ev['prompt'])
            assert os.path.isfile(prompt_path), (
                f'Eval "{ev["name"]}" prompt file not found: {prompt_path}'
            )

    def test_eval_expected_files_exist(self):
        for ev in self.config['evals']:
            expected_path = os.path.join(self.evals_dir, ev['expected'])
            assert os.path.isfile(expected_path), (
                f'Eval "{ev["name"]}" expected file not found: {expected_path}'
            )

    def test_eval_criteria_are_non_empty(self):
        for ev in self.config['evals']:
            assert len(ev['criteria']) > 0, (
                f'Eval "{ev["name"]}" must have at least one criterion'
            )

    def test_eval_timeouts_are_positive(self):
        for ev in self.config['evals']:
            assert 'timeout' in ev, (
                f'Eval "{ev["name"]}" should have a timeout'
            )
            assert isinstance(ev['timeout'], (int, float))
            assert ev['timeout'] > 0

    def test_eval_prompt_files_are_non_empty(self):
        for ev in self.config['evals']:
            prompt_path = os.path.join(self.evals_dir, ev['prompt'])
            with open(prompt_path, 'r', encoding='utf-8') as fh:
                content = fh.read().strip()
            assert len(content) > 50, (
                f'Eval "{ev["name"]}" prompt file is too short '
                f'({len(content)} chars)'
            )

    def test_eval_expected_files_are_non_empty(self):
        for ev in self.config['evals']:
            expected_path = os.path.join(self.evals_dir, ev['expected'])
            with open(expected_path, 'r', encoding='utf-8') as fh:
                content = fh.read().strip()
            assert len(content) > 50, (
                f'Eval "{ev["name"]}" expected file is too short '
                f'({len(content)} chars)'
            )


class TestSkills2ClassificationConsistency:
    """Validate that classification and deprecation-risk are consistent."""

    def setup_method(self):
        self.fm = _parse_frontmatter(SKILL_MD)

    def test_workflow_has_no_deprecation_risk(self):
        """Workflow skills should have deprecation-risk: none."""
        if self.fm['classification'] == 'workflow':
            assert self.fm['deprecation-risk'] == 'none', (
                'Workflow skills should have deprecation-risk: none'
            )

    def test_capability_has_deprecation_risk(self):
        """Capability skills should have deprecation-risk: medium or high."""
        if self.fm['classification'] == 'capability':
            assert self.fm['deprecation-risk'] in ('medium', 'high'), (
                'Capability skills should have deprecation-risk: medium or high'
            )

    def test_hybrid_has_low_deprecation_risk(self):
        """Hybrid skills should have deprecation-risk: low."""
        if self.fm['classification'] == 'hybrid':
            assert self.fm['deprecation-risk'] == 'low', (
                'Hybrid skills should have deprecation-risk: low'
            )


class TestSkillMdSizeBudget:
    """Enforce SKILL.md's self-declared size budget.

    README.md tells contributors to keep SKILL.md under 500 lines so that the
    always-loaded portion of the skill stays small in the agent context
    window. The file drifted to 527 once before; this test pins the budget so
    a future contributor cannot quietly blow past it. If you must exceed the
    limit, raise it deliberately here (with reasoning) — do not delete the
    assertion.
    """

    SOFT_LIMIT_LINES = 500

    def test_skill_md_under_500_lines(self):
        with open(SKILL_MD, 'r', encoding='utf-8') as fh:
            line_count = sum(1 for _ in fh)
        assert line_count <= self.SOFT_LIMIT_LINES, (
            f'SKILL.md has {line_count} lines, exceeds the '
            f'{self.SOFT_LIMIT_LINES}-line budget declared in README.md. '
            'Move detail into references/*.md or condense.'
        )

    def test_quick_cli_reference_moved_to_debugging(self):
        """Regression: the long CLI cheat sheet must live in debugging.md,
        not in always-loaded SKILL.md, to keep the size budget realistic.
        SKILL.md may still mention "Quick reference" as a pointer, but the
        full command list (>=20 ros2 subcommands) should not be inline."""
        with open(SKILL_MD, 'r', encoding='utf-8') as fh:
            skill = fh.read()
        # Count ros2 introspection commands that the moved section contained.
        ros2_command_count = sum(
            1 for cmd in ('ros2 node list', 'ros2 topic info',
                          'ros2 service list', 'ros2 action list',
                          'ros2 param list', 'ros2 interface show',
                          'ros2 control list_controllers',
                          'ros2 lifecycle list', 'ros2 bag record',
                          'ros2 bag play')
            if cmd in skill
        )
        assert ros2_command_count <= 2, (
            f'SKILL.md inlines {ros2_command_count} ros2 commands from the '
            'cheat sheet; these belong in references/debugging.md §10 to '
            'preserve always-loaded context budget.'
        )
        # And the actual cheat sheet must be reachable.
        debugging_md = os.path.join(
            SKILL_ROOT, 'references', 'debugging.md')
        with open(debugging_md, 'r', encoding='utf-8') as fh:
            debugging = fh.read()
        assert 'Quick CLI reference' in debugging, (
            'Quick CLI reference must live in references/debugging.md'
        )
        for must in ('ros2 node list', 'ros2 topic info /topic_name -v',
                     'ros2 lifecycle list', 'ros2 bag play my_bag --clock'):
            assert must in debugging, (
                f'debugging.md is missing CLI reference command: {must!r}'
            )


class TestVersionConsistency:
    """All release metadata and hook output surfaces represent the skill
    release version and must be updated together. A drifting copy (as
    happened when the hooks reported 1.1.0 after the skill moved to 1.2.0)
    makes logs and bug reports ambiguous about what actually ran — do not
    delete this check; extend it when a new version surface is added.
    """

    def _skill_md_version(self):
        return str(_parse_frontmatter(SKILL_MD)['version'])

    def _eval_yaml_version(self):
        path = os.path.join(SKILL_ROOT, 'evals', 'eval.yaml')
        with open(path, 'r', encoding='utf-8') as fh:
            return str(yaml.safe_load(fh)['version'])

    def _marketplace_version(self):
        path = os.path.join(SKILL_ROOT, '.claude-plugin', 'marketplace.json')
        with open(path, 'r', encoding='utf-8') as fh:
            return str(json.load(fh)['metadata']['version'])

    def _hook_reported_version(self, script, tmp_path, extra_args=()):
        result = subprocess.run(
            [sys.executable, os.path.join(SKILL_ROOT, 'scripts', script),
             *extra_args],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
            timeout=10,
        )
        return str(json.loads(result.stdout)['version'])

    def test_all_version_surfaces_match(self, tmp_path):
        reference = self._skill_md_version()
        surfaces = {
            'SKILL.md frontmatter': reference,
            'evals/eval.yaml': self._eval_yaml_version(),
            '.claude-plugin/marketplace.json': self._marketplace_version(),
            'skill_stop_hook.py output': self._hook_reported_version(
                'skill_stop_hook.py', tmp_path),
            'skill_validate_hook.py output': self._hook_reported_version(
                'skill_validate_hook.py', tmp_path,
                extra_args=('--command', 'ros2 topic list')),
        }
        mismatched = {name: v for name, v in surfaces.items()
                      if v != reference}
        assert not mismatched, (
            f'Version surfaces out of sync with SKILL.md '
            f'({reference}): {mismatched}'
        )
