"""Selected instructions stay short; detailed regressions must remain reachable."""

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
BODY = (ROOT / 'SKILL.md').read_text(encoding='utf-8').split('\n---\n', 1)[1]
DETAILS = (ROOT / 'references/engineering-principles.md').read_text(encoding='utf-8')


def test_selected_body_has_a_separate_measurable_size_budget():
    # This is an explicit byte budget, not a claim about a model's tokenizer.
    assert len(BODY.encode('utf-8')) <= 12000
    assert len(BODY.splitlines()) < 220


@pytest.mark.parametrize('path', sorted((ROOT / 'references').glob('*.md')), ids=lambda p: p.name)
def test_every_reference_is_directly_reachable_from_selected_instructions(path):
    assert f'`references/{path.name}`' in BODY
    assert path.stat().st_size > 0


def test_large_tables_are_not_eagerly_loaded():
    assert '| Feature ' not in BODY
    assert '| # | Pitfall |' not in BODY
    assert '| # | Pitfall |' in DETAILS
    assert 'Do not preload that entire' in BODY


def test_verification_ladder_is_identical_in_short_and_detailed_contracts():
    def rows(text):
        return [line for line in text.splitlines() if re.match(r'^\| L[0-6] \|', line)]
    actual = rows(BODY)
    assert len(actual) == 7
    assert actual == rows(DETAILS)
    assert 'Hardware powered, no actuation' in actual[4]
    assert 'motors disabled/isolated' in actual[4]
    assert 'operator present' in actual[5]
    assert 'Never write an L0–L2 result in L4+ language' in BODY


@pytest.mark.parametrize('required', [
    'A review stays read-only', 'preserve user', 'not permission to execute commands',
    'Leave `SKILL_RUNS_LOG` unset', 'not automatic selection',
    'Report conflicting', 'greenfield', 'Missing dependencies, cancelled commands',
    'not passes', 'absolute paths under the discovered skill root',
    'does not authorize execution', 'does not prove that an actuator stopped',
    'not a crash-safety mechanism', 'Do not enable Nav2 Spin/BackUp',
    'not zero-overhead', 'separate claims',
])
def test_high_impact_boundaries_still_live_in_selected_body(required):
    assert required in BODY


def test_task_and_client_routes_are_not_conflated():
    assert '`docs/CLIENT_COMPATIBILITY.md`' in BODY
    assert '`docs/SKILL_CONTRACT.md`' in BODY
    assert 'Claude hooks are optional integration' in BODY
    assert 'other clients use manual validators' in BODY
