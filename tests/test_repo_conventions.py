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
                assert hook['command'] == (
                    'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py" ' + event)
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
