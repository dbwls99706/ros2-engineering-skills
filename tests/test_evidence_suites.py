"""Preregistered cases exist; this does not execute or score a model."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_cases_have_prompts_and_rubrics():
    suite = json.loads((ROOT / 'evals/benchmark_suite.json').read_text(encoding='utf-8'))
    assert suite['trials'] >= 3
    assert len(suite['cases']) >= 5
    ids = set()
    for case in suite['cases']:
        assert case['id'] not in ids
        ids.add(case['id'])
        assert (ROOT / 'evals' / case['prompt']).is_file()
        assert len(case['criteria']) >= 3


def test_trigger_suite_has_positive_negative_and_explicit_cases():
    suite = json.loads((ROOT / 'evals/trigger_cases.json').read_text(encoding='utf-8'))
    cases = suite['cases']
    assert len({case['id'] for case in cases}) == len(cases)
    for outcome in (True, False):
        assert sum(case['mode'] == 'implicit' and case['should_trigger'] is outcome
                   for case in cases) >= 10
    for client in suite['clients']:
        assert any(case.get('client') == client and case['mode'] == 'explicit'
                   for case in cases)
    assert all('result' not in case and 'score' not in case for case in cases)


def test_case_study_has_no_motion_topic_and_uses_positive_control():
    example = (ROOT / 'examples/qos_roundtrip.py').read_text(encoding='utf-8')
    assert '/cmd_vel' not in example
    assert 'Positive control' in example and "counts['repaired']" in example
    assert 'time.monotonic()' in example
