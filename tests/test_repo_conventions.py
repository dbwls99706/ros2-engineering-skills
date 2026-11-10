"""Repository-level packaging and workflow regression tests."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_project_local_settings_are_not_committed():
    assert not (ROOT / '.claude' / 'settings.json').exists()
    assert not (ROOT / '.claude' / 'hooks' / 'no_ai_attribution.py').exists()


def test_gitignore_keeps_local_client_state_out():
    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    assert '.claude/' in gitignore.splitlines()


def test_plugin_hooks_use_repository_scripts():
    path = ROOT / 'hooks' / 'hooks.json'
    with path.open('r', encoding='utf-8') as handle:
        hooks = json.load(handle)['hooks']
    for event, groups in hooks.items():
        for group in groups:
            for hook in group['hooks']:
                # -B keeps __pycache__ out of a provenance-tracked installation.
                assert hook['command'] == (
                    'python3 -B "${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py" ' + event)
    adapter = (ROOT / 'scripts/claude_hook.py').read_text(encoding='utf-8')
    for script in ('skill_validate_hook.py', 'skill_stop_hook.py'):
        assert script in adapter and (ROOT / 'scripts' / script).is_file()


def test_ci_uses_standard_plugin_locations():
    github_dir = ROOT / '.github'
    if not github_dir.is_dir():
        pytest.skip('not a full checkout (.github absent)')
    workflow = (github_dir / 'workflows' / 'test.yml').read_text(encoding='utf-8')
    assert '.claude-plugin/plugin.json' in workflow
    assert 'hooks/hooks.json' in workflow
    assert '.claude/hooks/' not in workflow


def test_readme_preserves_before_after_examples():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '## Before / After' in readme
    assert '<th width="50%">Without this skill</th>' in readme
    assert '<th width="50%">With this skill loaded</th>' in readme
    prompts = (
        "My ROS 2 subscriber isn't receiving any sensor messages.",
        'Create a C++ driver package for my LiDAR sensor.',
    )
    for prompt in prompts:
        assert prompt in readme
    assert 'ros2 topic info /camera/image_raw -v' in readme


def _check_sources_commands(name):
    github_dir = ROOT / '.github'
    if not github_dir.is_dir():
        pytest.skip('not a full checkout (.github absent)')
    text = (github_dir / 'workflows' / name).read_text(encoding='utf-8')
    commands = [line.strip() for line in text.splitlines()
                if 'validate_skill.py' in line and '--check-sources' in line
                and not line.lstrip().startswith('#')]
    assert commands, name + ' must check source review dates'
    return commands


@pytest.mark.parametrize('name', ['source-review.yml', 'release-gate.yml'])
def test_review_and_release_gates_fail_on_overdue_sources(name):
    # The default mode is the strict one; these gates must not opt out of it.
    for command in _check_sources_commands(name):
        assert '--overdue' not in command, command


def test_pull_request_gate_only_reports_overdue_sources():
    for command in _check_sources_commands('test.yml'):
        assert '--overdue warn' in command, command


def test_release_gate_runs_before_a_tag_exists():
    github_dir = ROOT / '.github'
    if not github_dir.is_dir():
        pytest.skip('not a full checkout (.github absent)')
    yaml = pytest.importorskip('yaml')
    workflow = yaml.safe_load(
        (github_dir / 'workflows' / 'release-gate.yml').read_text(encoding='utf-8'))
    triggers = workflow.get('on') or workflow.get(True)
    assert 'main' in triggers['push']['branches']
    assert triggers['push']['tags'] == ['v*']
    assert 'workflow_dispatch' in triggers
    # The gate reports; it must never publish or write to the repository.
    assert workflow['permissions'] == {'contents': 'read'}
