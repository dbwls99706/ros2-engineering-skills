"""Regression checks for evidence-driven progression rules."""

from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'SKILL.md'
PROGRESSION = ROOT / 'references' / 'evidence-progression.md'
TESTING = ROOT / 'references' / 'testing.md'
SAFETY_ESTOP = ROOT / 'references' / 'safety-estop.md'
HARDWARE = ROOT / 'references' / 'hardware-interface.md'
ROOT_EVALS = ROOT / 'evals' / 'eval.yaml'
PROGRESSION_DIR = ROOT / 'evals' / 'progression'
ESTOP_EXPECTED = ROOT / 'evals' / 'expected' / 'estop-safety.md'


def read(path):
    return path.read_text(encoding='utf-8')


def flat(path):
    """Collapse Markdown wrapping so prose regressions test meaning, not layout."""
    return ' '.join(read(path).split())


def bundle_version():
    frontmatter = read(SKILL).split('---', 2)[1]
    return str(yaml.safe_load(frontmatter)['metadata']['version'])


def test_selected_contract_reviews_gate_provenance():
    body = flat(SKILL)
    assert 'Validate gates, not only outcomes' in body
    assert 'do not assume a gate is valid merely because it already exists in code' in body
    assert 'Turn blockers into a resolution plan' in body
    assert 'Separate permission from proof' in body
    assert 'unchanged, unexpired, and unrevoked' in body
    assert '`references/evidence-progression.md`' in body


def test_authorization_and_execution_authority_are_separate():
    body = flat(SKILL)
    assert 'Execution authority is separate' in body
    assert 'does not override product, client, site, or safety policy' in body
    assert 'client tool permissions' in body
    assert 'physical actuation is reserved to an operator' in body
    assert 'operator-ready bounded next action' in body
    assert 'pretending authorization is missing' in body


def test_progression_reference_keeps_both_gate_failure_modes():
    text = flat(PROGRESSION)
    assert 'Outcome-driven weakening' in text
    assert 'Code-as-authority' in text
    assert 'does not retroactively make an earlier run a pass' in text
    assert 'A numeric count alone does not establish independence' in text


def test_authorization_validity_and_verification_are_orthogonal():
    text = flat(PROGRESSION)
    for marker in (
        'Authorization:', 'Authorization validity:', 'Authorized envelope:',
        'Execution authority:', 'Observed technical state:',
        'Supervised-test readiness:', 'Operational readiness:',
    ):
        assert marker in text
    assert 'Authorization grants permission to attempt an action' in text
    assert 'does not override execution policy' in text
    assert 'Expired, revoked, or materially changed authorization requires renewal' in text
    assert 'do not repeatedly ask for the same permission' in text


def test_latched_recovery_never_replays_stale_commands_by_default():
    text = flat(PROGRESSION)
    assert 'Current observation vs latched failure' in text
    assert 'Previous-command disposition' in text
    assert 'no automatic replay' in text
    assert 'fresh command or goal after recovery' in text


def test_sensor_metrics_are_not_substituted():
    text = flat(PROGRESSION)
    for marker in (
        'Individual LiDAR range precision', 'Plane-normal fit in a narrow ROI',
        'Marker/PnP pose uncertainty', 'Camera-LiDAR extrinsic error',
        'Point-cloud/map residual', 'Robot pose error',
    ):
        assert marker in text
    assert 'Do not substitute one convenient sensor metric for another physical quantity.' in text


def test_verification_levels_keep_distinct_execution_preconditions():
    testing = flat(TESTING)
    assert 'L5 preconditions are test-specific' in testing
    assert 'L6 preconditions are field-specific' in testing
    assert 'Authorization and execution authority are separate' in testing
    assert 'high-risk physical fault-injection checks' in testing
    assert 'operator-executed only' in testing
    assert 'L6 is never an unattended CI step' in testing


def test_high_risk_fault_injection_stays_operator_executed():
    safety = flat(SAFETY_ESTOP)
    hardware = flat(HARDWARE)
    assert 'reserves their execution to the operator' in safety
    assert 'must not execute these physical fault injections itself' in safety
    assert 'never an automated step for CI or an AI agent to run on hardware' in hardware


def test_numbered_principle_references_point_to_engineering_reference():
    stale = re.compile(
        r'`?SKILL\.md`?\s+Principle\s+\d+'
        r'|pitfall\s+\d+\s+in\s+`?SKILL\.md`?', re.IGNORECASE)
    paths = [ROOT / 'README.md', ROOT / 'SKILL.md']
    for directory in ('references', 'docs', 'scripts', 'tests'):
        paths.extend(path for path in (ROOT / directory).rglob('*')
                     if path.suffix in ('.md', '.py'))
    offenders = [str(path.relative_to(ROOT)) for path in paths if stale.search(read(path))]
    assert offenders == [], f'Stale numbered-principle references: {offenders}'


def test_progression_fixtures_are_explicitly_synthetic():
    for name in ('gate-policy-review', 'sensor-metric-separation'):
        prompt_path = PROGRESSION_DIR / 'prompts' / f'{name}.md'
        expected_path = PROGRESSION_DIR / 'expected' / f'{name}.md'
        prompt = flat(prompt_path)
        expected = flat(expected_path)
        assert 'synthetic' in prompt.lower()
        assert 'synthetic fixture' in expected.lower()
        assert '1.8' in prompt and '2.6' in prompt


def test_supervised_fixture_preserves_valid_current_session_authorization():
    prompt_path = PROGRESSION_DIR / 'prompts' / 'supervised-test-authorization.md'
    expected_path = PROGRESSION_DIR / 'expected' / 'supervised-test-authorization.md'
    prompt = flat(prompt_path)
    expected = flat(expected_path)
    assert 'current test session' in prompt
    assert 'restrained test stand' in prompt
    assert 'has not expired or been revoked' in prompt
    assert 'operator-ready bounded test' in expected
    assert 'does not override client, product, site, or safety policy' in expected


def test_primary_eval_pipeline_registers_progression_scenarios():
    config = yaml.safe_load(read(ROOT_EVALS))
    assert str(config['version']) == bundle_version()
    by_name = {entry['name']: entry for entry in config['evals']}
    expected = {
        'gate-policy-review',
        'supervised-test-authorization',
        'latched-localization-recovery',
        'sensor-metric-separation',
    }
    assert expected <= set(by_name)
    for name in expected:
        entry = by_name[name]
        assert (ROOT / 'evals' / entry['prompt']).is_file()
        assert (ROOT / 'evals' / entry['expected']).is_file()
        assert len(entry['criteria']) >= 5
        assert all(item['weight'] >= 0.8 for item in entry['criteria'])


def test_no_dead_secondary_progression_manifest():
    assert not (PROGRESSION_DIR / 'eval.yaml').exists()


def test_existing_estop_contract_keeps_fresh_command_semantics():
    expected = flat(ESTOP_EXPECTED)
    assert 'Reset restores permission, not motion' in expected
    assert 'no replay of the pre-stop command' in expected


def test_gate_review_does_not_authorize_bypassing_unknown_interlocks():
    text = flat(PROGRESSION)
    assert 'unknown or disputed gate remains enforced' in text
    assert 'not permission to bypass an interlock or enable motion' in text
    assert 'Freeze revised acceptance criteria before collecting confirmation data' in text


def test_authorization_attempts_and_readiness_are_not_permanent():
    text = flat(PROGRESSION)
    assert 'one-test approval is consumed when the physical test command is issued' in text
    assert 'read-only preflight that blocks before any command is issued does not consume' in text
    assert 'Unknown execution authority means no actuation' in text
    assert 'Recheck technical preconditions immediately before execution' in text
    assert 'unlimited loop' in text


def test_repeatability_is_not_confused_with_independent_accuracy():
    text = flat(PROGRESSION)
    assert 'Distinguish statistical independence from coverage' in text
    assert 'correlation is accounted for' in text
    assert 'do not remove a common calibration bias' in text


def test_l5_description_covers_controlled_trials_without_reclassifying_all_as_field_use():
    for path in (SKILL, ROOT / 'references/engineering-principles.md', TESTING):
        assert 'Controlled motion / fault injection' in read(path)
    assert 'software geofence alone is not equivalent to physical restraint' in flat(TESTING)


def test_container_ci_example_selects_bash_without_restoring_ros_install_prefix():
    text = read(TESTING)
    example = text.split('# .github/workflows/ci.yaml\n', 1)[1].split('```', 1)[0]
    job = yaml.safe_load(example)['jobs']['build-and-test']
    assert job['defaults']['run']['shell'] == 'bash'
    for step in job['steps']:
        if 'actions/cache' in step.get('uses', ''):
            assert '/opt/ros' not in step['with']['path']
