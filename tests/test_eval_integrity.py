"""Adversarial inputs must not become evaluation evidence or escape the bundle."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eval_runner as runner


@pytest.fixture
def suite(tmp_path):
    root = tmp_path / 'evals'
    root.mkdir()
    for name in ('prompt.md', 'expected.md'):
        (root / name).write_text('alpha beta gamma', encoding='utf-8')
    for name in ('outputs', 'outputs_baseline'):
        (root / name).mkdir()
    config = {
        'skill': 'fixture', 'version': '1.5.0',
        'evals': [{
            'name': 'case', 'prompt': 'prompt.md', 'expected': 'expected.md',
            'criteria': [{'id': 'criterion', 'description': 'alpha beta gamma'}],
        }],
    }
    (root / 'eval.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    return root, config


def capture(root, on='alpha beta gamma', off='different lexical output'):
    (root / 'outputs/case.md').write_text(on, encoding='utf-8')
    (root / 'outputs_baseline/case.md').write_text(off, encoding='utf-8')


def cli(root, *args):
    return subprocess.run(
        [sys.executable, runner.__file__, '--eval-dir', str(root), *args],
        capture_output=True, text=True, timeout=10,
    )


def test_no_captures_never_imply_deprecation(suite):
    root, config = suite
    for _ in range(4):
        report = runner.run_parity_test(config, str(root))
        assert report['scored_evals'] == 0
        assert not report['deprecation_candidate']


def test_empty_baseline_is_error_not_zero_point_comparison(suite):
    root, config = suite
    capture(root, off='')
    report = runner.run_parity_test(config, str(root))
    assert report['scored_evals'] == 0
    assert report['per_eval'][0]['status'] == 'error'
    assert not report['threshold_met']


@pytest.mark.parametrize('name', ['../../outside', '/outside', r'..\outside', 'C:outside', 'CON'])
def test_capture_name_cannot_select_arbitrary_files(suite, name):
    root, config = suite
    (root.parent / 'outside.md').write_text('alpha beta gamma', encoding='utf-8')
    entry = {**config['evals'][0], 'name': name}
    assert runner.run_eval(entry, str(root), content_source='output')['status'] == 'error'


@pytest.mark.parametrize('target', ['output', 'baseline', 'history'])
def test_capture_and_history_symlinks_cannot_escape(suite, target):
    root, config = suite
    outside = root.parent / 'outside'
    outside.mkdir()
    (outside / 'case.md').write_text('alpha beta gamma', encoding='utf-8')
    subdir = {'output': 'outputs', 'baseline': 'outputs_baseline', 'history': 'history'}[target]
    path = root / subdir
    if path.exists():
        path.rmdir()
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlinks unavailable in this environment')
    if target == 'history':
        capture(root)
        report = runner.run_parity_test(config, str(root))
        assert report.get('history_error')
        assert not list(outside.glob('*.jsonl'))
    else:
        assert runner.run_eval(config['evals'][0], str(root), content_source=target)['status'] == 'error'


def test_empty_suite_cannot_pass(suite):
    root, _ = suite
    (root / 'eval.yaml').write_text('evals: []\n', encoding='utf-8')
    result = cli(root, '--json')
    assert result.returncode == 2
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('payload', [
    'evals: [null]',
    'evals: [42]',
    'evals: []\nevals: []',
])
def test_malformed_yaml_fails_without_traceback(suite, payload):
    root, _ = suite
    (root / 'eval.yaml').write_text(payload, encoding='utf-8')
    result = cli(root)
    assert result.returncode == 2
    assert 'Traceback' not in result.stderr


def test_duplicate_eval_names_are_rejected(suite):
    root, config = suite
    config['evals'].append(copy.deepcopy(config['evals'][0]))
    (root / 'eval.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    assert cli(root, '--json').returncode == 2


@pytest.mark.parametrize('value', [None, [], 17, '', '../prompt.md', r'C:\prompt.md'])
def test_invalid_fixture_paths_are_reported(suite, value):
    root, config = suite
    entry = {**config['evals'][0], 'prompt': value}
    assert runner.run_eval(entry, str(root))['status'] == 'error'


@pytest.mark.parametrize('weight', [float('nan'), float('inf'), -1, True, 'not-a-number'])
def test_invalid_weights_are_errors_not_silent_defaults(suite, weight):
    root, config = suite
    config['evals'][0]['criteria'][0]['weight'] = weight
    assert runner.run_eval(config['evals'][0], str(root))['status'] == 'error'


def test_non_utf8_capture_remains_error(suite):
    root, config = suite
    (root / 'outputs/case.md').write_bytes(b'\xff\xfe\x00')
    report = runner.run_eval(config['evals'][0], str(root), content_source='output')
    assert report['status'] == 'error'


def test_partial_judge_capture_is_not_full_pass(suite):
    root, config = suite
    capture(root)
    config['evals'].append({**config['evals'][0], 'name': 'uncaptured'})
    report = runner.run_all_evals(config, str(root), content_source='output')
    assert report['summary']['overall_status'] == 'partial'


def test_require_complete_fails_on_missing_capture(suite):
    root, _ = suite
    result = cli(root, '--mode=judge', '--require-complete', '--json')
    assert result.returncode == 1
    assert json.loads(result.stdout)['summary']['overall_status'] == 'no_data'


def test_require_complete_accepts_present_capture(suite):
    root, _ = suite
    capture(root)
    assert cli(root, '--mode=judge', '--require-complete', '--json').returncode == 0


def test_replaying_same_captures_does_not_make_independent_trials(suite):
    root, config = suite
    capture(root, off='alpha beta gamma')
    for _ in range(4):
        report = runner.run_parity_test(config, str(root))
    assert report['scored_evals'] == 1
    assert not report['deprecation_candidate']


def test_changed_suite_does_not_inherit_deprecation_streak(suite):
    root, config = suite
    for i in range(3):
        capture(root, on=f'alpha beta gamma trial{i}', off=f'alpha beta gamma trial{i}')
        report = runner.run_parity_test(config, str(root))
    assert report['deprecation_candidate']
    config['version'] = '1.6.0'
    capture(root, on='alpha beta gamma new version', off='alpha beta gamma new version')
    assert not runner.run_parity_test(config, str(root))['deprecation_candidate']


def test_malformed_history_does_not_crash(suite):
    root, config = suite
    history = root / 'history'
    history.mkdir()
    (history / '2026-01.jsonl').write_text('null\n42\n[]\n{"bad":true}\n', encoding='utf-8')
    capture(root)
    assert not runner.run_parity_test(config, str(root))['deprecation_candidate']


def test_failed_critical_criterion_is_not_weighted_away(suite):
    root, config = suite
    entry = config['evals'][0]
    entry['criteria'] = [
        {'description': 'alpha beta gamma', 'weight': 100},
        {'id': 'safety', 'description': 'stop latch recovery', 'weight': 1, 'critical': True},
    ]
    report = runner.run_eval(entry, str(root))
    assert report['pass_rate'] > 80
    assert report['status'] == 'fail'
    assert report['critical_failures'] == ['safety']


def test_invalid_baseline_fails_cli_even_without_strict_mode(suite):
    root, _ = suite
    capture(root, off='')
    result = cli(root, '--parity', '--json')
    assert result.returncode == 1
    assert json.loads(result.stdout)['data_status'] == 'error'


def test_broken_capture_symlink_is_error_not_missing_capture(suite):
    root, config = suite
    try:
        (root / 'outputs/case.md').symlink_to(root / 'outputs/missing.md')
    except OSError:
        pytest.skip('Symlinks unavailable in this environment')
    assert runner.run_eval(config['evals'][0], str(root), content_source='output')['status'] == 'error'


def test_all_zero_weights_and_empty_criteria_are_rejected(suite):
    root, config = suite
    entry = config['evals'][0]
    entry['criteria'][0]['weight'] = 0
    assert runner.run_eval(entry, str(root))['status'] == 'error'
    entry['criteria'] = []
    assert runner.run_eval(entry, str(root))['status'] == 'error'


def test_revised_rubric_resets_history_scope(suite):
    root, config = suite
    for i in range(3):
        capture(root, on=f'alpha beta gamma {i}', off=f'alpha beta gamma {i}')
        report = runner.run_parity_test(config, str(root))
    assert report['deprecation_candidate']
    config['evals'][0]['criteria'][0]['description'] = 'alpha beta gamma changed rubric'
    assert not runner.run_parity_test(config, str(root))['deprecation_candidate']


def test_legacy_unscoped_history_is_not_quality_evidence(suite):
    root, config = suite
    history = root / 'history'
    history.mkdir()
    (history / '2026-01.jsonl').write_text(''.join(
        json.dumps({'timestamp_utc': f'2026-01-0{i}T00:00:00Z', 'threshold_met': False}) + '\n'
        for i in range(1, 4)), encoding='utf-8')
    capture(root, off='alpha beta gamma')
    assert not runner.run_parity_test(config, str(root))['deprecation_candidate']


def test_partial_parity_retains_only_valid_pairs_and_cannot_deprecate(suite):
    root, config = suite
    capture(root, off='alpha beta gamma')
    config['evals'].append({**config['evals'][0], 'name': 'uncaptured'})
    for _ in range(4):
        report = runner.run_parity_test(config, str(root))
    assert report['data_status'] == 'partial'
    assert report['scored_evals'] == 1
    assert report['skipped_evals'] == 1
    assert not report['deprecation_candidate']


def test_empty_and_invalid_capture_is_not_printed_as_nodata(suite):
    root, config = suite
    (root / 'outputs/case.md').write_text('', encoding='utf-8')
    config['evals'].append({**config['evals'][0], 'name': 'missing'})
    (root / 'eval.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    result = cli(root, '--mode=judge')
    assert result.returncode == 1
    assert 'Overall: [FAIL]' in result.stdout


def test_replaying_old_failures_cannot_reorder_a_newer_success(suite):
    root, config = suite
    for label in ('first', 'second'):
        capture(root, on=f'alpha beta gamma {label}', off=f'alpha beta gamma {label}')
        runner.run_parity_test(config, str(root))
    capture(root, on='alpha beta gamma newer', off='unrelated baseline')
    runner.run_parity_test(config, str(root))
    for label in ('first', 'second'):
        capture(root, on=f'alpha beta gamma {label}', off=f'alpha beta gamma {label}')
        report = runner.run_parity_test(config, str(root))
        assert not report['deprecation_candidate']


@pytest.mark.parametrize('kind', ['file', 'dangling'])
def test_broken_output_directory_is_error(suite, kind):
    root, config = suite
    output = root / 'outputs'
    output.rmdir()
    if kind == 'file':
        output.write_text('not a directory', encoding='utf-8')
    else:
        try:
            output.symlink_to(root / 'missing', target_is_directory=True)
        except OSError:
            pytest.skip('Symlinks unavailable')
    assert runner.run_eval(config['evals'][0], str(root), content_source='output')['status'] == 'error'


@pytest.mark.parametrize('payload', [
    'parity_test: {threshold: .nan}',
    'parity_test: {consecutive_failures_for_deprecation: 0}',
    'parity_test: []',
    'other: &loop [*loop]',
])
def test_invalid_parity_config_is_bounded_configuration_error(suite, payload):
    root, config = suite
    (root / 'eval.yaml').write_text(yaml.safe_dump(config) + payload, encoding='utf-8')
    result = cli(root, '--parity')
    assert result.returncode == 2
    assert 'Traceback' not in result.stderr


def test_require_complete_partial_parity_fails_without_inventing_delta(suite):
    root, config = suite
    config['evals'].append({**config['evals'][0], 'name': 'missing'})
    (root / 'eval.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    capture(root)
    result = cli(root, '--parity', '--require-complete', '--json')
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report['scored_evals'] == 1 and report['data_status'] == 'partial'
    assert not report['deprecation_candidate']


def test_large_finite_weight_cannot_overflow_pass_rate(suite):
    root, config = suite
    entry = config['evals'][0]
    entry['criteria'][0]['weight'] = 1e308
    report = runner.run_eval(entry, str(root))
    assert report['pass_rate'] == 100.0
    json.dumps(report, allow_nan=False)


def test_enabled_parity_defaults_are_printable(suite, capsys):
    root, config = suite
    config['parity_test'] = {'enabled': True}
    report = runner.run_all_evals(config, str(root))
    runner.print_report(report)
    output = capsys.readouterr().out
    assert 'threshold: 5.0%' in output
    assert 'Consecutive failures for deprecation: 3' in output
