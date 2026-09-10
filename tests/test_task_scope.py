"""Scope contracts and wired capture cases, not semantic model evaluations."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def flat(path):
    return ' '.join((ROOT / path).read_text(encoding='utf-8').split())


def test_discovery_frontloads_ros_and_keeps_negative_boundary():
    text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    metadata = yaml.safe_load(text.split('---', 2)[1])
    description = metadata['description']
    assert description.startswith('ROS 2 engineering:')
    assert len(description) <= 360
    assert 'Not for general C++/Python' in description
    assert all(model not in description for model in ('Astra', 'Fable', 'GPT-6'))
    client = yaml.safe_load((ROOT / 'agents/openai.yaml').read_text(encoding='utf-8'))
    assert client['policy']['allow_implicit_invocation'] is True
    assert '$ros2-engineering-skills' in client['interface']['default_prompt']
    assert 'Load only needed references' in client['interface']['default_prompt']


def test_proportionate_scope_does_not_weaken_required_checks():
    body = flat('SKILL.md')
    assert 'not a checklist to execute on every request' in body
    assert 'A prose-only edit does not require ROS inventory' in body
    assert 'read a matching reference only when it resolves a task-specific uncertainty' in body
    assert 'affected behavior and risk, not diff size' in body
    assert 'Preserve mandatory project/CI gates' in body


def test_scope_cases_use_the_existing_paired_capture_pipeline():
    suite = json.loads((ROOT / 'evals/benchmark_suite.json').read_text(encoding='utf-8'))
    cases = {case['id']: case for case in suite['cases']}
    for name in ('scoped-doc-edit', 'scoped-safety-change'):
        case = cases[name]
        prompt = (ROOT / 'evals' / case['prompt']).read_text(encoding='utf-8')
        assert '## Scenario' in prompt and '## Question' in prompt
        assert 'synthetic' in prompt.lower()
        assert len(case['criteria']) >= 3
        assert 'result' not in case
    protocol = flat('docs/EVIDENCE_CAPTURE.md')
    assert '--suite evals/benchmark_suite.json' in protocol
    assert 'compare the previous and candidate skill revisions' in protocol
    assert 'including competing skills' in protocol
    assert 'whether they were shortened' in protocol
    assert 'An unavailable metric is unknown, not zero' in protocol


def test_routing_cases_include_shorthand_and_context_only_negatives():
    suite = json.loads((ROOT / 'evals/trigger_cases.json').read_text(encoding='utf-8'))
    cases = {case['id']: case for case in suite['cases']}
    for name in ('nav2-shorthand', 'control-shorthand', 'simulation', 'realtime',
                 'sros2', 'micro-ros', 'multi-robot'):
        assert cases[name]['should_trigger'] is True
    for name in ('generic-in-ros-repo', 'ros-mentioned-css'):
        assert cases[name]['should_trigger'] is False


def test_portable_bundle_does_not_inherit_client_permission_or_model_overrides():
    text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    metadata = yaml.safe_load(text.split('---', 2)[1])
    client_controls = {
        'allowed-tools', 'disallowed-tools', 'disable-model-invocation',
        'user-invocable', 'model', 'effort', 'context', 'agent', 'hooks',
    }
    assert not client_controls.intersection(metadata)
    codex = yaml.safe_load((ROOT / 'agents/openai.yaml').read_text(encoding='utf-8'))
    assert set(codex['policy']) == {'allow_implicit_invocation'}
    doc = flat('docs/CLIENT_COMPATIBILITY.md')
    assert 'share intent, not client settings' in doc
    assert 'invocation is not permission to actuate' in doc
    assert 'not a sandbox or a denylist' in doc
    assert "Inherit the user's selected model/context" in doc
    assert 'A model upgrade does not authorize changing' in doc
