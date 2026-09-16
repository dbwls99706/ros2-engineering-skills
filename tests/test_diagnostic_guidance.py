"""Guidance regressions and a synthetic counterexample, not model evaluation."""

import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def test_self_built_diagnostics_have_control_and_claim_boundaries():
    body = read('SKILL.md')
    detail = read('references/evidence-progression.md')
    assert 'Validate self-built diagnostics with positive controls' in body
    for required in ('positive controls', 'negative controls',
                     'not independent physical ground truth',
                     'Missing or failing controls',
                     'not "no observation exists."',
                     'does not by itself prove the opposite'):
        assert required in detail


def test_rejected_changes_retain_requirement_and_preservation_metrics():
    detail = read('references/evidence-progression.md')
    decision = detail.split('### Accepted and rejected changes', 1)[1].split('## 2.', 1)[0]
    for required in ('denominator', 'preservation constraints', 'wrong quantity',
                     'independent confirmation data', 'earlier result'):
        assert required in decision


def test_offline_route_is_scoped_and_compares_actual_artifacts():
    assert '| Offline ROS map/bag post-processing, saved artifact lineage |' in read('SKILL.md')
    detail = read('references/artifact-lineage.md')
    for required in ('not a general data-platform architecture',
                     'Backend execution is not artifact adoption',
                     '`A - B` and `B - A`', 'multiset',
                     'do not prove ancestry', 'not every',
                     'do not label approximate agreement as exact reproduction'):
        assert required.lower() in detail.lower()


def test_offline_claims_and_retractions_do_not_change_reporting_contract():
    body = read('SKILL.md')
    detail = read('references/evidence-progression.md')
    assert 'an optional report shape' in body
    assert 'Do not force those claims onto a hardware-readiness ladder' in body
    for required in ('Retracted:', 'Affected conclusions:', 'Remaining:',
                     'not a mandatory output schema', 'not a hook JSON change'):
        assert required in detail


def test_subset_proof_requires_more_than_a_set_only_flag():
    detail = read('references/artifact-lineage.md')
    for required in ('fixed per-input contributions',
                     'monotone function of that state',
                     'whole path', 'prefix tests may still'):
        assert required in detail
    blocks = re.findall(r'```python\n(.*?)\n```', detail, re.DOTALL)
    assert len(blocks) == 1
    namespace = {}
    exec(compile(blocks[0], 'artifact-lineage.md:counterexample', 'exec'), namespace)
    accumulate = namespace['accumulate_hits']
    assert accumulate([0, 4, 5]) == {0}
    assert accumulate([4, 5]) == {4, 5}
    assert accumulate([4, 0, 5]) == {4, 5}


@pytest.mark.parametrize('case_id', [
    'diagnostic-controls', 'artifact-adoption', 'subset-semantics', 'rejected-metric',
])
def test_preregistered_review_cases_are_complete_without_fabricated_results(case_id):
    suite = json.loads(read('evals/diagnostic_review_suite.json'))
    assert suite['schema_version'] == 1 and suite['trials'] == 3
    assert 'not model results' in suite['purpose']
    cases = suite['cases']
    assert len({case['id'] for case in cases}) == len(cases) == 4
    case = next(case for case in cases if case['id'] == case_id)
    assert len(case['criteria']) >= 4
    assert all(isinstance(item, str) and item.strip() for item in case['criteria'])
    assert not ({'result', 'score', 'pass_rate', 'captures'} & set(case))
    prompt = (ROOT / 'evals' / case['prompt']).resolve()
    assert prompt.is_relative_to(ROOT / 'evals' / 'diagnostics' / 'prompts')
    assert prompt.is_file() and 'synthetic' in prompt.read_text(encoding='utf-8')
