"""Packaging contracts tested with isolated synthetic bundles and bad inputs."""

from datetime import date
import json
from pathlib import Path

import pytest

from scripts import validate_skill as contract

TODAY = date(2026, 9, 7)
ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'ros2-engineering-skills'
    for directory in ('scripts', 'references', '.claude-plugin', 'hooks', 'agents', 'docs'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / 'SKILL.md').write_text('''---
name: ros2-engineering-skills
description: Use for ROS 2 node development; not web applications.
license: Apache-2.0
compatibility: Python 3.10 or newer
metadata:
  version: "1.3.0"
---
# ROS 2
Read `references/test.md` and [a reference](references/test.md#example).
''', encoding='utf-8')
    (root / 'LICENSE').write_text('Synthetic license fixture', encoding='utf-8')
    (root / 'references/test.md').write_text('# Example\n', encoding='utf-8')
    for name in ('claude_hook.py', 'skill_validate_hook.py', 'skill_stop_hook.py'):
        (root / 'scripts' / name).write_text('pass\n', encoding='utf-8')
    save(root / '.claude-plugin/plugin.json', {'name': 'ros2-engineering', 'version': '1.3.0'})
    save(root / '.claude-plugin/marketplace.json', {'plugins': [{
        'name': 'ros2-engineering', 'version': '1.3.0', 'source': './'}]})
    save(root / 'hooks/hooks.json', {'hooks': {event: [{'hooks': [{
        'type': 'command', 'timeout': 10,
        'command': 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py" ' + event,
    }]}] for event in ('PreToolUse', 'Stop')}})
    (root / 'agents/openai.yaml').write_text('''interface:
  display_name: "ROS 2 Engineering"
  short_description: "ROS 2 development"
  default_prompt: "Use $ros2-engineering-skills for this task"
policy:
  allow_implicit_invocation: true
''', encoding='utf-8')
    save(root / 'docs/sources.json', {'sources': [{
        'id': 'spec', 'url': 'https://agentskills.io/specification',
        'reviewed_on': '2026-09-07', 'review_after': '2026-12-06',
        'covers': ['SKILL.md'],
    }]})
    return root


def change(root, name, update):
    path = root / name
    value = json.loads(path.read_text(encoding='utf-8'))
    update(value)
    save(path, value)


def test_valid_bundle(bundle):
    result = contract.validate(bundle, installed=True, sources=True, today=TODAY)
    assert result['status'] == 'pass' and result['errors'] == []
    assert result['metrics']['sources_reviewed'] == 1
    assert result['scope'] == 'static packaging only'


@pytest.mark.parametrize('text', ['no frontmatter', '\ufeff---\nname: test\n---\n',
                                  '---\n[]\n---\nbody', '---\nname: a\nname: b\n---\nbody',
                                  '---\n? [a,b]\n: c\n---\nbody', '---\nname: [\n---\nbody'])
def test_bad_frontmatter(bundle, text):
    (bundle / 'SKILL.md').write_text(text, encoding='utf-8')
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('replacement', ['Bad-Name', '-bad', 'bad--name', 'bad-', 'a' * 65, '123: []'])
def test_bad_names(bundle, replacement):
    path = bundle / 'SKILL.md'
    path.write_text(path.read_text().replace('name: ros2-engineering-skills', 'name: ' + replacement))
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('field,value', [('description', '""'), ('description', '4'),
                                         ('description', 'a' * 1025), ('compatibility', 'a' * 501),
                                         ('compatibility', '[]'), ('allowed-tools', '[]'),
                                         ('license', 'true'), ('hooks', '{}'), ('context', 'fork')])
def test_metadata_types_and_portability(bundle, field, value):
    path = bundle / 'SKILL.md'
    lines = [line for line in path.read_text().splitlines() if not line.startswith(field + ':')]
    lines.insert(1, field + ': ' + value)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert contract.validate(bundle)['status'] == 'fail'


def test_custom_metadata_values_must_be_strings(bundle):
    path = bundle / 'SKILL.md'
    path.write_text(path.read_text().replace('version: "1.3.0"', 'version: 3'))
    assert contract.validate(bundle)['status'] == 'fail'


def test_empty_body(bundle):
    path = bundle / 'SKILL.md'
    path.write_text(path.read_text().split('# ROS 2')[0])
    assert contract.validate(bundle)['status'] == 'fail'


def test_line_budget_is_enforced(bundle):
    path = bundle / 'SKILL.md'
    path.write_text(path.read_text() + '\n' * 501)
    assert contract.validate(bundle)['status'] == 'fail'


def test_byte_budget_is_not_claimed_as_token_count(bundle):
    path = bundle / 'SKILL.md'
    path.write_text(path.read_text() + 'text ' * 6000)
    report = contract.validate(bundle)
    assert report['status'] == 'pass'
    assert 'bytes are not tokens' in report['warnings'][0]


def test_installation_name_not_checkout_alias(bundle):
    moved = bundle.with_name('arbitrary-checkout')
    bundle.rename(moved)
    assert contract.validate(moved)['status'] == 'pass'
    assert contract.validate(moved, installed=True)['status'] == 'fail'


@pytest.mark.parametrize('relative', ['../secret', '/etc/passwd', 'C:/secret', 'file:///tmp/a',
                                      'references\\x', '%2e%2e/secret', 'missing.md', ''])
def test_unsafe_reference(bundle, relative):
    with pytest.raises(ValueError):
        contract.bundled_file(bundle, relative)


def test_escaping_symlink(bundle):
    outside = bundle.parent / 'secret'
    outside.write_text('text')
    (bundle / 'references/link.md').symlink_to(outside)
    with pytest.raises(ValueError):
        contract.bundled_file(bundle, 'references/link.md')


def test_broken_router_link(bundle):
    (bundle / 'references/test.md').unlink()
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('field,value', [('name', 'wrong'), ('version', '9.0.0'),
                                         ('skills', './'), ('hooks', './hooks/hooks.json')])
def test_bad_plugin_contract(bundle, field, value):
    change(bundle, '.claude-plugin/plugin.json', lambda d: d.update({field: value}))
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('plugins', [[], None, [None], [{}, {}], [{}]])
def test_bad_marketplace(bundle, plugins):
    change(bundle, '.claude-plugin/marketplace.json', lambda d: d.update(plugins=plugins))
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('groups', [None, [], [{}], [{'hooks': []}], [{'hooks': [None]}],
                                    [{'hooks': [{'type': 'http'}]}]])
def test_bad_hook_groups(bundle, groups):
    change(bundle, 'hooks/hooks.json', lambda d: d['hooks'].update(PreToolUse=groups))
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('value', [0, 601, True, '10'])
def test_bad_hook_timeout(bundle, value):
    change(bundle, 'hooks/hooks.json', lambda d: d['hooks']['Stop'][0]['hooks'][0].update(timeout=value))
    assert contract.validate(bundle)['status'] == 'fail'


def test_duplicate_json_rejected(bundle):
    (bundle / '.claude-plugin/plugin.json').write_text('{"name":1,"name":2}')
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('yaml', ['[]', 'policy: {}', 'interface: {}',
                                  'interface:\n  display_name: x\n  short_description: x\n  default_prompt: wrong'])
def test_bad_codex_metadata(bundle, yaml):
    (bundle / 'agents/openai.yaml').write_text(yaml)
    assert contract.validate(bundle)['status'] == 'fail'


@pytest.mark.parametrize('field,value', [('review_after', '2026-09-06'),
                                         ('reviewed_on', '2026-09-08'), ('reviewed_on', 'bad'),
                                         ('id', ''), ('url', 'http://example.com'), ('covers', [])])
def test_bad_source_registry(bundle, field, value):
    change(bundle, 'docs/sources.json', lambda d: d['sources'][0].update({field: value}))
    assert contract.validate(bundle, sources=True, today=TODAY)['status'] == 'fail'


def test_duplicate_source_ids(bundle):
    change(bundle, 'docs/sources.json', lambda d: d['sources'].append(d['sources'][0]))
    assert contract.validate(bundle, sources=True, today=TODAY)['status'] == 'fail'


def test_cli_report(bundle, capsys):
    assert contract.main(['--root', str(bundle), '--installed']) == 0
    assert json.loads(capsys.readouterr().out)['scope'] == 'static packaging only'


def test_cli_missing_bundle(tmp_path, capsys):
    assert contract.main(['--root', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['errors']
