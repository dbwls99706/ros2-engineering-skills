#!/usr/bin/env python3
"""Read-only checks for this repository's portable skill and client adapters.

These are packaging checks, not a claim that any client invoked the skill.
Requires PyYAML for YAML parsing; --help works without third-party packages.
"""

import argparse
from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit

FIELDS = {'name', 'description', 'license', 'compatibility', 'metadata',
          'allowed-tools'}
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def yaml_object(text):
    try:
        import yaml
    except ImportError as exc:
        raise ValueError('PyYAML is required: python -m pip install -r '
                         'requirements.txt') from exc

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError('YAML keys must be unique strings')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError('Invalid YAML: ' + str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError('Expected a YAML object')
    return value


def json_object(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    result = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object: ' + str(path))
    return result


def bundled_file(root, relative):
    """Reject absolute, traversal, and escaping symlink references."""
    if not isinstance(relative, str):
        raise ValueError('Bundle reference must be a string')
    relative = unquote(relative.split('#', 1)[0])
    path = PurePosixPath(relative)
    if (not relative or '\\' in relative or path.is_absolute()
            or '..' in path.parts or urlsplit(relative).scheme):
        raise ValueError('Unsafe bundle reference: ' + relative)
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError('Missing or escaping bundle file: ' + relative)
    return candidate


def check_sources(root, today):
    registry = json_object(bundled_file(root, 'docs/sources.json'))
    entries = registry.get('sources')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Source registry is empty')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Source entry must be an object')
        key = entry.get('id')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Source ids must be nonempty and unique')
        seen.add(key)
        if not isinstance(entry.get('url'), str):
            raise ValueError('Source URL must be a string: ' + key)
        url = urlsplit(entry['url'])
        if url.scheme != 'https' or not url.netloc:
            raise ValueError('Source must use an HTTPS URL: ' + key)
        reviewed = date.fromisoformat(entry['reviewed_on'])
        due = date.fromisoformat(entry['review_after'])
        if not reviewed <= today <= due or due <= reviewed:
            raise ValueError('Source review is future-dated or overdue: ' + key)
        covers = entry.get('covers')
        if not isinstance(covers, list) or not covers:
            raise ValueError('Source must identify covered files: ' + key)
        for target in covers:
            bundled_file(root, target)
    return len(entries)


def validate(root, installed=False, sources=False, today=None, portable=False):
    root = Path(root).absolute()
    errors, warnings = [], []
    metrics = {}
    try:
        text = bundled_file(root, 'SKILL.md').read_text(encoding='utf-8')
        match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', text, re.S)
        if not match:
            raise ValueError('SKILL.md must start with YAML frontmatter')
        meta = yaml_object(match.group(1))
        if set(meta) - FIELDS:
            raise ValueError('Nonportable frontmatter fields: '
                             + ', '.join(sorted(set(meta) - FIELDS)))
        name = meta.get('name')
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or not NAME.fullmatch(name):
            raise ValueError('Skill name must be 1-64 lowercase kebab-case characters')
        if root.name != name:
            (errors if installed else warnings).append(
                'Installed directory name must match skill name: ' + name)
        for field, limit, required in (('description', 1024, True),
                                       ('compatibility', 500, False)):
            value = meta.get(field)
            if (required or field in meta) and (
                not isinstance(value, str) or not value.strip() or len(value) > limit
            ):
                raise ValueError(field + ' must be a nonempty string <= ' + str(limit))
        for field in ('license', 'allowed-tools'):
            if field in meta and (not isinstance(meta[field], str) or not meta[field].strip()):
                raise ValueError(field + ' must be a nonempty string')
        custom = meta.get('metadata', {})
        if not isinstance(custom, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in custom.items()
        ):
            raise ValueError('metadata must map strings to strings')
        body = text[match.end():]
        if not body.strip():
            raise ValueError('Skill instructions are empty')
        metrics.update(lines=len(text.splitlines()), body_bytes=len(body.encode('utf-8')))
        if metrics['lines'] > 500:
            errors.append('Repository policy: SKILL.md exceeds 500 lines')
        if metrics['body_bytes'] > 24000:
            warnings.append('Large skill body; review against the recommended 5000-token '
                            'budget with the target tokenizer (bytes are not tokens)')
        refs = set(re.findall(r'`((?:references|scripts|docs)/[^`\s]+\.(?:md|py|json))`', body))
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', body):
            if not urlsplit(link).scheme and not link.startswith('#'):
                refs.add(link)
        for relative in refs:
            bundled_file(root, relative)
        metrics['referenced_files'] = len(refs)
        bundled_file(root, 'LICENSE')
        if not portable:
            plugin = json_object(bundled_file(root, '.claude-plugin/plugin.json'))
            market = json_object(bundled_file(root, '.claude-plugin/marketplace.json'))
            if plugin.get('name') != 'ros2-engineering':
                errors.append('Unexpected Claude plugin name')
            if plugin.get('version') != custom.get('version'):
                errors.append('Plugin and skill versions differ')
            # Root SKILL.md and hooks/hooks.json use default plugin discovery.
            if 'skills' in plugin or 'hooks' in plugin:
                errors.append('Do not duplicate default root skill or hooks registration')
            entries = market.get('plugins')
            if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
                raise ValueError('Marketplace must contain exactly one plugin entry')
            if (entries[0].get('name') != plugin.get('name')
                    or entries[0].get('version') != plugin.get('version')
                    or entries[0].get('source') != './'):
                errors.append('Marketplace entry does not match the local plugin')
            hooks = json_object(bundled_file(root, 'hooks/hooks.json')).get('hooks')
            if not isinstance(hooks, dict) or set(hooks) != {'PreToolUse', 'Stop'}:
                raise ValueError('Expected PreToolUse and Stop hook groups')
            for event, groups in hooks.items():
                if not isinstance(groups, list) or not groups:
                    raise ValueError('Missing hook groups: ' + event)
                for group in groups:
                    if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                        raise ValueError('Invalid hook group: ' + event)
                    if not group['hooks']:
                        raise ValueError('Empty hook group: ' + event)
                    for hook in group['hooks']:
                        expected = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py" ' + event
                        if (not isinstance(hook, dict) or hook.get('type') != 'command'
                                or hook.get('command') != expected):
                            raise ValueError('Unexpected hook command: ' + event)
                        timeout = hook.get('timeout')
                        if type(timeout) not in (int, float) or not 0 < timeout <= 600:
                            raise ValueError('Invalid hook timeout: ' + event)
            for script in ('claude_hook.py', 'skill_validate_hook.py', 'skill_stop_hook.py'):
                bundled_file(root, 'scripts/' + script)
        config = yaml_object(bundled_file(root, 'agents/openai.yaml').read_text(encoding='utf-8'))
        interface = config.get('interface')
        if not isinstance(interface, dict):
            raise ValueError('Codex interface metadata is missing')
        for field in ('display_name', 'short_description', 'default_prompt'):
            if not isinstance(interface.get(field), str) or not interface[field].strip():
                raise ValueError('Codex interface.' + field + ' is empty')
        if '$' + name not in interface['default_prompt']:
            errors.append('Codex default_prompt must mention the actual skill name')
        policy = config.get('policy')
        if not isinstance(policy, dict) or type(policy.get('allow_implicit_invocation')) is not bool:
            errors.append('Codex invocation policy must use a Boolean')
        if sources:
            metrics['sources_reviewed'] = check_sources(root, today or date.today())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return {'status': 'fail' if errors else 'pass', 'scope': 'static packaging only',
            'errors': errors, 'warnings': warnings, 'metrics': metrics}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--installed', action='store_true', help='enforce the installation directory name')
    parser.add_argument('--check-sources', action='store_true', help='check source review dates, not remote contents')
    parser.add_argument('--portable', action='store_true',
                        help='validate knowledge-only installation without Claude plugin files')
    args = parser.parse_args(argv)
    report = validate(args.root, args.installed, args.check_sources, portable=args.portable)
    print(json.dumps(report, indent=2))
    return int(report['status'] == 'fail')


if __name__ == '__main__':
    sys.exit(main())
