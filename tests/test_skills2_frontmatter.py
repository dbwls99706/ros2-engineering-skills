"""Validate Agent Skills metadata and Claude Code plugin packaging."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / 'SKILL.md'
PLUGIN_JSON = ROOT / '.claude-plugin' / 'plugin.json'
MARKETPLACE_JSON = ROOT / '.claude-plugin' / 'marketplace.json'
HOOKS_JSON = ROOT / 'hooks' / 'hooks.json'

ALLOWED_FIELDS = {
    'name', 'description', 'license', 'allowed-tools', 'metadata',
    'compatibility',
}
FORBIDDEN_LEGACY_FIELDS = {
    'context', 'classification', 'category', 'version',
    'deprecation-risk', 'hooks', 'evals',
}


def _parse_frontmatter():
    content = SKILL_MD.read_text(encoding='utf-8')
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    assert match is not None, 'SKILL.md must start with YAML frontmatter'
    metadata = yaml.safe_load(match.group(1))
    assert isinstance(metadata, dict)
    return metadata


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f'Duplicate JSON key: {key}'
        result[key] = value
    return result


def _load_json(path):
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle, object_pairs_hook=_reject_duplicate_keys)


class TestAgentSkillsMetadata:
    def test_only_standard_fields_are_top_level(self):
        metadata = _parse_frontmatter()
        assert set(metadata) <= ALLOWED_FIELDS
        assert not (set(metadata) & FORBIDDEN_LEGACY_FIELDS)

    def test_required_fields(self):
        metadata = _parse_frontmatter()
        assert metadata['name'] == 'ros2-engineering-skills'
        assert isinstance(metadata['description'], str)
        assert 1 <= len(metadata['description']) <= 1024
        assert metadata['license'] == 'Apache-2.0'

    def test_directory_matches_skill_name(self):
        assert ROOT.name == _parse_frontmatter()['name']

    def test_compatibility_is_bounded(self):
        compatibility = _parse_frontmatter()['compatibility']
        assert isinstance(compatibility, str)
        assert len(compatibility) <= 500
        assert 'Python 3.10' in compatibility

    def test_custom_metadata_is_string_mapping(self):
        metadata = _parse_frontmatter()['metadata']
        assert isinstance(metadata, dict)
        assert metadata['author'] == 'dbwls99706'
        assert re.fullmatch(r'\d+\.\d+\.\d+', metadata['version'])
        assert all(isinstance(key, str) for key in metadata)
        assert all(isinstance(value, str) for value in metadata.values())


class TestPluginPackaging:
    def test_manifests_and_hooks_exist(self):
        for path in (PLUGIN_JSON, MARKETPLACE_JSON, HOOKS_JSON):
            assert path.is_file(), f'Missing packaging file: {path}'

    def test_plugin_manifest(self):
        plugin = _load_json(PLUGIN_JSON)
        assert plugin['name'] == 'ros2-engineering'
        assert plugin['displayName'] == 'ROS 2 Engineering'
        assert plugin['license'] == 'Apache-2.0'
        assert plugin['repository'].endswith('/ros2-engineering-skills')
        assert plugin['author']['email'] == 'yujinhong3@gmail.com'

    def test_root_skill_uses_default_plugin_discovery(self):
        plugin = _load_json(PLUGIN_JSON)
        assert 'skills' not in plugin
        assert SKILL_MD.is_file()
        assert not (ROOT / 'skills').exists()

    def test_marketplace_points_at_plugin_root(self):
        marketplace = _load_json(MARKETPLACE_JSON)
        assert marketplace['name'] == 'ros2-engineering-skills'
        assert len(marketplace['plugins']) == 1
        entry = marketplace['plugins'][0]
        assert entry['name'] == 'ros2-engineering'
        assert entry['source'] == './'
        assert entry['category'] == 'development'

    def test_hook_schema_and_scripts(self):
        config = _load_json(HOOKS_JSON)['hooks']
        assert set(config) == {'PreToolUse', 'Stop'}
        for event, groups in config.items():
            assert isinstance(groups, list) and groups
            for group in groups:
                assert isinstance(group.get('hooks'), list)
                for hook in group['hooks']:
                    assert hook['type'] == 'command'
                    assert 0 < hook['timeout'] <= 600
                    command = hook['command']
                    assert '${CLAUDE_PLUGIN_ROOT}' in command
                    script = command.split('/scripts/', 1)[1].split('.py', 1)[0]
                    assert (ROOT / 'scripts' / f'{script}.py').is_file(), (
                        f'{event} references a missing script: {script}.py'
                    )


class TestVersionConsistency:
    def _hook_version(self, script, tmp_path, extra_args=()):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / script), *extra_args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, 'SKILL_WORKSPACE': str(tmp_path)},
            timeout=10,
        )
        assert result.stdout, result.stderr
        return str(json.loads(result.stdout)['version'])

    def test_all_release_surfaces_match(self, tmp_path):
        skill_version = str(_parse_frontmatter()['metadata']['version'])
        with (ROOT / 'evals' / 'eval.yaml').open(
                'r', encoding='utf-8') as handle:
            eval_version = str(yaml.safe_load(handle)['version'])
        plugin_version = str(_load_json(PLUGIN_JSON)['version'])
        marketplace_version = str(
            _load_json(MARKETPLACE_JSON)['plugins'][0]['version'])
        versions = {
            skill_version,
            eval_version,
            plugin_version,
            marketplace_version,
            self._hook_version('skill_stop_hook.py', tmp_path),
            self._hook_version(
                'skill_validate_hook.py', tmp_path,
                extra_args=('--command', 'ros2 topic list')),
        }
        assert versions == {'1.4.0'}
        changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        release = re.search(r'^## (\d+\.\d+\.\d+) - (\d{4}-\d{2}-\d{2})$',
                            changelog, re.MULTILINE)
        assert release is not None
        assert release.group(1) == skill_version
        assert f'Source version: **{skill_version}**' in (
            ROOT / 'README.md').read_text(encoding='utf-8')


class TestSkillSizeBudget:
    def test_skill_md_under_500_lines(self):
        line_count = len(SKILL_MD.read_text(encoding='utf-8').splitlines())
        assert line_count <= 500

    def test_quick_cli_reference_remains_external(self):
        skill = SKILL_MD.read_text(encoding='utf-8')
        debugging = (ROOT / 'references' / 'debugging.md').read_text(
            encoding='utf-8')
        skill_commands = (
            'ros2 node list', 'ros2 topic info', 'ros2 service list',
            'ros2 action list', 'ros2 param list', 'ros2 interface show',
            'ros2 control list_controllers', 'ros2 lifecycle list',
            'ros2 bag record', 'ros2 bag play',
        )
        assert sum(command in skill for command in skill_commands) <= 2
        assert 'Quick CLI reference' in debugging
        required = (
            'ros2 node list', 'ros2 topic info /topic_name -v',
            'ros2 lifecycle list', 'ros2 bag play my_bag --clock',
        )
        for command in required:
            assert command in debugging
