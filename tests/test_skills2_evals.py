"""Tests for Skills 2.0 eval system — validates eval runner and eval definitions.

These tests ensure:
1. eval.yaml is valid and complete
2. All prompt/expected file pairs exist and are well-formed
3. Eval runner produces correct structured output
4. Parity test configuration is valid
5. Eval runner CLI works correctly
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

SKILL_ROOT = os.path.join(os.path.dirname(__file__), '..')
EVALS_DIR = os.path.join(SKILL_ROOT, 'evals')
EVAL_RUNNER = os.path.join(SKILL_ROOT, 'scripts', 'eval_runner.py')

sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
from eval_runner import (
    load_eval_config,
    validate_eval_entry,
    load_file_content,
    extract_criteria_text,
    evaluate_criteria,
    run_eval,
    run_all_evals,
    run_parity_test,
    _term_matches,
    _extract_criteria_with_weights,
    _content_path_for_source,
    _check_deprecation_streak,
    _print_parity_report,
    print_report,
    main as eval_runner_main,
    __version__ as EVAL_RUNNER_VERSION,
)


class TestEvalYamlStructure:
    """Validate eval.yaml structure and completeness."""

    def setup_method(self):
        with open(os.path.join(EVALS_DIR, 'eval.yaml'), 'r', encoding='utf-8') as fh:
            self.config = yaml.safe_load(fh)

    def test_has_skill_name(self):
        assert 'skill' in self.config
        assert self.config['skill'] == 'ros2-engineering-skills'

    def test_has_version(self):
        assert 'version' in self.config

    def test_has_classification(self):
        assert 'classification' in self.config
        assert self.config['classification'] in ('workflow', 'capability', 'hybrid')

    def test_has_deprecation_risk(self):
        assert 'deprecation-risk' in self.config

    def test_has_evals_list(self):
        assert 'evals' in self.config
        assert isinstance(self.config['evals'], list)
        assert len(self.config['evals']) >= 3

    def test_each_eval_has_required_fields(self):
        for ev in self.config['evals']:
            assert 'name' in ev
            assert 'description' in ev
            assert 'prompt' in ev
            assert 'expected' in ev
            assert 'criteria' in ev
            assert 'timeout' in ev

    def test_each_eval_has_tags(self):
        for ev in self.config['evals']:
            assert 'tags' in ev
            assert isinstance(ev['tags'], list)
            assert len(ev['tags']) > 0

    def test_eval_criteria_have_ids(self):
        for ev in self.config['evals']:
            for criterion in ev['criteria']:
                assert isinstance(criterion, dict)
                assert 'id' in criterion
                assert 'description' in criterion
                assert 'weight' in criterion
                assert isinstance(criterion['weight'], (int, float))
                assert 0.0 <= criterion['weight'] <= 1.0

    def test_eval_names_unique(self):
        names = [ev['name'] for ev in self.config['evals']]
        assert len(names) == len(set(names))

    def test_eval_prompt_files_exist(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['prompt'])
            assert os.path.isfile(path), f'Missing: {path}'

    def test_eval_expected_files_exist(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['expected'])
            assert os.path.isfile(path), f'Missing: {path}'

    def test_has_parity_test(self):
        assert 'parity_test' in self.config
        pt = self.config['parity_test']
        assert 'enabled' in pt
        assert 'threshold' in pt
        assert 'consecutive_failures_for_deprecation' in pt
        assert 'metrics' in pt

    def test_parity_test_metrics(self):
        for metric in self.config['parity_test']['metrics']:
            assert 'name' in metric
            assert 'weight' in metric
            assert isinstance(metric['weight'], (int, float))


class TestEvalPromptQuality:
    """Validate that prompt files meet quality standards."""

    def setup_method(self):
        with open(os.path.join(EVALS_DIR, 'eval.yaml'), 'r', encoding='utf-8') as fh:
            self.config = yaml.safe_load(fh)

    def test_prompts_have_scenario(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['prompt'])
            content = load_file_content(path)
            assert '## Scenario' in content or '## scenario' in content.lower(), (
                f'Prompt "{ev["name"]}" should have a Scenario section'
            )

    def test_prompts_have_question(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['prompt'])
            content = load_file_content(path)
            assert '## Question' in content or '## question' in content.lower(), (
                f'Prompt "{ev["name"]}" should have a Question section'
            )

    def test_expected_have_required_elements(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['expected'])
            content = load_file_content(path)
            assert '## Required' in content or '### ' in content, (
                f'Expected "{ev["name"]}" should have Required sections'
            )

    def test_prompts_minimum_length(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['prompt'])
            content = load_file_content(path)
            assert len(content) >= 100, (
                f'Prompt "{ev["name"]}" too short ({len(content)} chars)'
            )

    def test_expected_minimum_length(self):
        for ev in self.config['evals']:
            path = os.path.join(EVALS_DIR, ev['expected'])
            content = load_file_content(path)
            assert len(content) >= 100, (
                f'Expected "{ev["name"]}" too short ({len(content)} chars)'
            )


class TestEvalRunnerFunctions:
    """Test eval runner internal functions."""

    def test_load_eval_config(self):
        config = load_eval_config(EVALS_DIR)
        assert 'evals' in config

    def test_load_eval_config_missing_dir(self, tmp_path):
        import pytest
        with pytest.raises(SystemExit):
            load_eval_config(str(tmp_path))

    def test_load_eval_config_no_evals_key(self, tmp_path):
        import pytest
        (tmp_path / 'eval.yaml').write_text('skill: test\n')
        with pytest.raises(SystemExit):
            load_eval_config(str(tmp_path))

    def test_load_eval_config_evals_not_list(self, tmp_path):
        import pytest
        (tmp_path / 'eval.yaml').write_text('evals: not_a_list\n')
        with pytest.raises(SystemExit):
            load_eval_config(str(tmp_path))

    def test_load_eval_config_invalid_yaml(self, tmp_path):
        import pytest
        (tmp_path / 'eval.yaml').write_text('not: valid: yaml: [')
        with pytest.raises(SystemExit):
            load_eval_config(str(tmp_path))

    def test_validate_eval_entry_valid(self):
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': ['Must do X'],
            'timeout': 60000,
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert len(errors) == 0

    def test_validate_eval_entry_missing_fields(self):
        entry = {'name': 'test'}
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert len(errors) >= 2  # Missing prompt, expected, criteria

    def test_validate_eval_entry_missing_prompt_file(self, tmp_path):
        entry = {
            'name': 'test',
            'prompt': 'nonexistent.md',
            'expected': 'nonexistent.md',
            'criteria': ['Must do X'],
        }
        errors = validate_eval_entry(entry, str(tmp_path))
        assert any('not found' in e for e in errors)

    def test_validate_eval_entry_bad_timeout(self):
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': ['Must do X'],
            'timeout': -1,
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert any('positive' in e for e in errors)

    def test_validate_eval_entry_criteria_not_list(self):
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': 'not a list',
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert any('list' in e for e in errors)

    def test_extract_criteria_text_strings(self):
        criteria = ['Must do X', 'Should do Y']
        texts = extract_criteria_text(criteria)
        assert texts == ['Must do X', 'Should do Y']

    def test_extract_criteria_text_dicts(self):
        criteria = [
            {'id': 'a', 'description': 'Must do X', 'weight': 1.0},
            {'id': 'b', 'description': 'Should do Y', 'weight': 0.8},
        ]
        texts = extract_criteria_text(criteria)
        assert texts == ['Must do X', 'Should do Y']

    def test_evaluate_criteria_all_pass(self):
        expected = 'This document covers QoS incompatibility and DDS semantics.'
        criteria = ['Must mention QoS incompatibility']
        results = evaluate_criteria(expected, criteria)
        assert len(results) == 1
        assert results[0]['passed'] is True

    def test_evaluate_criteria_failure(self):
        expected = 'This document covers basic topics.'
        criteria = ['Must mention QoS incompatibility and DDS RxO semantics']
        results = evaluate_criteria(expected, criteria)
        assert len(results) == 1
        # May or may not pass depending on term matching

    def test_load_file_content(self):
        path = os.path.join(EVALS_DIR, 'prompts', 'qos-compatibility.md')
        content = load_file_content(path)
        assert len(content) > 0
        assert 'QoS' in content

    def test_load_file_content_nonexistent(self):
        content = load_file_content('/nonexistent/file.md')
        assert content == ''


class TestEvalRunnerExecution:
    """Test eval runner end-to-end execution."""

    def test_run_single_eval(self):
        config = load_eval_config(EVALS_DIR)
        entry = config['evals'][0]
        result = run_eval(entry, EVALS_DIR)
        assert 'name' in result
        assert 'status' in result
        assert 'pass_rate' in result
        assert 'execution_time_ms' in result
        assert result['status'] in ('pass', 'fail', 'error')

    def test_run_all_evals(self):
        config = load_eval_config(EVALS_DIR)
        report = run_all_evals(config, EVALS_DIR)
        assert 'skill' in report
        assert 'version' in report
        assert 'summary' in report
        assert 'evals' in report
        assert report['summary']['total_evals'] >= 3
        assert report['summary']['overall_status'] in ('pass', 'fail')

    def test_run_specific_eval(self):
        config = load_eval_config(EVALS_DIR)
        report = run_all_evals(
            config, EVALS_DIR, eval_name='qos-compatibility-analysis')
        assert report['summary']['total_evals'] == 1

    def test_run_nonexistent_eval(self):
        import pytest
        config = load_eval_config(EVALS_DIR)
        with pytest.raises(SystemExit):
            run_all_evals(config, EVALS_DIR, eval_name='nonexistent')

    def test_run_eval_verbose(self):
        config = load_eval_config(EVALS_DIR)
        entry = config['evals'][0]
        result = run_eval(entry, EVALS_DIR, verbose=True)
        assert 'prompt_path' in result
        assert 'expected_path' in result

    def test_run_eval_with_empty_prompt(self, tmp_path):
        (tmp_path / 'prompts').mkdir()
        (tmp_path / 'expected').mkdir()
        (tmp_path / 'prompts' / 'empty.md').write_text('')
        (tmp_path / 'expected' / 'empty.md').write_text('some content here')
        entry = {
            'name': 'empty-test',
            'prompt': 'prompts/empty.md',
            'expected': 'expected/empty.md',
            'criteria': ['Must do X'],
            'timeout': 1000,
        }
        result = run_eval(entry, str(tmp_path))
        assert result['status'] == 'error'

    def test_run_eval_with_empty_expected(self, tmp_path):
        (tmp_path / 'prompts').mkdir()
        (tmp_path / 'expected').mkdir()
        (tmp_path / 'prompts' / 'test.md').write_text('# Test\nSome prompt content here')
        (tmp_path / 'expected' / 'test.md').write_text('')
        entry = {
            'name': 'empty-expected-test',
            'prompt': 'prompts/test.md',
            'expected': 'expected/test.md',
            'criteria': ['Must do X'],
            'timeout': 1000,
        }
        result = run_eval(entry, str(tmp_path))
        assert result['status'] == 'error'


class TestEvalRunnerCLI:
    """Test eval runner CLI interface."""

    def test_cli_default_run(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR],
            capture_output=True, text=True,
        )
        assert 'Lexical Eval Report' in result.stdout
        assert 'ros2-engineering-skills' in result.stdout

    def test_cli_json_output(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR, '--json'],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert 'skill' in data
        assert 'summary' in data
        assert 'evals' in data

    def test_cli_specific_eval(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR,
             '--eval-name', 'qos-compatibility-analysis', '--json'],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data['summary']['total_evals'] == 1

    def test_cli_verbose(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR,
             '--verbose', '--json'],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        for ev in data['evals']:
            assert 'prompt_path' in ev

    def test_cli_version(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--version'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert EVAL_RUNNER_VERSION in result.stdout

    def test_cli_nonexistent_eval_dir(self, tmp_path):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', str(tmp_path / 'nonexistent')],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_cli_nonexistent_eval_name(self):
        result = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR,
             '--eval-name', 'nonexistent'],
            capture_output=True, text=True,
        )
        assert result.returncode == 2


class TestEvalRunnerCoverage:
    """Additional tests to cover all code paths in eval_runner.py."""

    def test_load_eval_config_non_dict_yaml(self, tmp_path):
        """Cover line 49-50: yaml that parses to non-dict."""
        import pytest
        (tmp_path / 'eval.yaml').write_text('- just\n- a\n- list\n')
        with pytest.raises(SystemExit):
            load_eval_config(str(tmp_path))

    def test_validate_criterion_dict_missing_description(self):
        """Cover line 92: criterion dict without description."""
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': [{'id': 'no-desc', 'weight': 1.0}],
            'timeout': 1000,
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert any('description' in e for e in errors)

    def test_validate_criterion_bad_type(self):
        """Cover line 95: criterion that is neither str nor dict."""
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': [42],
            'timeout': 1000,
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert any('string or dict' in e for e in errors)

    def test_validate_timeout_not_number(self):
        """Cover line 100: timeout that is not a number."""
        entry = {
            'name': 'test',
            'prompt': 'prompts/qos-compatibility.md',
            'expected': 'expected/qos-compatibility.md',
            'criteria': ['Must do X'],
            'timeout': 'fast',
        }
        errors = validate_eval_entry(entry, EVALS_DIR)
        assert any('number' in e for e in errors)

    def test_run_eval_with_validation_errors(self):
        """Cover line 179-180: run_eval with invalid entry."""
        entry = {'name': 'broken'}  # Missing required fields
        result = run_eval(entry, EVALS_DIR)
        assert result['status'] == 'error'
        assert len(result['errors']) > 0
        assert result['pass_rate'] == 0.0

    def test_print_report_pass(self, capsys):
        """Cover lines 303-346: print_report with passing results."""
        from eval_runner import print_report
        report = {
            'skill': 'test-skill',
            'version': '1.0.0',
            'classification': 'capability',
            'deprecation_risk': 'medium',
            'summary': {
                'total_evals': 1,
                'passed': 1,
                'failed': 0,
                'errors': 0,
                'average_pass_rate': 100.0,
                'total_execution_time_ms': 5.0,
                'overall_status': 'pass',
            },
            'evals': [{
                'name': 'test-eval',
                'status': 'pass',
                'pass_rate': 100.0,
                'passed_criteria': 2,
                'total_criteria': 2,
                'execution_time_ms': 5.0,
                'criteria_results': [
                    {'criterion': 'Check A', 'passed': True,
                     'coverage': 1.0, 'matched_terms': [], 'total_terms': 2},
                    {'criterion': 'Check B', 'passed': True,
                     'coverage': 1.0, 'matched_terms': [], 'total_terms': 2},
                ],
            }],
            'parity_test': {
                'enabled': True,
                'threshold': 5.0,
                'consecutive_failures_for_deprecation': 3,
            },
        }
        print_report(report)
        captured = capsys.readouterr()
        assert 'PASS' in captured.out
        assert 'test-skill' in captured.out
        assert 'Parity Test' in captured.out

    def test_print_report_fail_with_errors(self, capsys):
        """Cover print_report with failing and error results."""
        from eval_runner import print_report
        report = {
            'skill': 'test-skill',
            'version': '1.0.0',
            'classification': 'capability',
            'deprecation_risk': 'medium',
            'summary': {
                'total_evals': 2,
                'passed': 0,
                'failed': 1,
                'errors': 1,
                'average_pass_rate': 25.0,
                'total_execution_time_ms': 10.0,
                'overall_status': 'fail',
            },
            'evals': [
                {
                    'name': 'fail-eval',
                    'status': 'fail',
                    'pass_rate': 50.0,
                    'passed_criteria': 1,
                    'total_criteria': 2,
                    'execution_time_ms': 5.0,
                    'criteria_results': [
                        {'criterion': 'Short', 'passed': True,
                         'coverage': 1.0, 'matched_terms': [],
                         'total_terms': 1},
                        {'criterion': 'A very long criterion that exceeds '
                         'sixty characters in total length to test truncation '
                         'behavior', 'passed': False,
                         'coverage': 0.1, 'matched_terms': [],
                         'total_terms': 10},
                    ],
                },
                {
                    'name': 'error-eval',
                    'status': 'error',
                    'pass_rate': 0.0,
                    'passed_criteria': 0,
                    'total_criteria': 0,
                    'execution_time_ms': 1.0,
                    'errors': ['Missing prompt file'],
                    'criteria_results': [],
                },
            ],
            'parity_test': None,
        }
        print_report(report)
        captured = capsys.readouterr()
        assert 'FAIL' in captured.out
        assert 'ERR' in captured.out
        assert 'ERROR' in captured.out

    def test_print_report_no_parity_test(self, capsys):
        """Cover print_report branch when parity_test is disabled."""
        from eval_runner import print_report
        report = {
            'skill': 'test',
            'version': '0.1.0',
            'classification': 'workflow',
            'deprecation_risk': 'none',
            'summary': {
                'total_evals': 0,
                'passed': 0,
                'failed': 0,
                'errors': 0,
                'average_pass_rate': 0.0,
                'total_execution_time_ms': 0.0,
                'overall_status': 'pass',
            },
            'evals': [],
            'parity_test': {'enabled': False},
        }
        print_report(report)
        captured = capsys.readouterr()
        assert 'Parity Test' not in captured.out

    def test_main_text_output(self, monkeypatch):
        """Cover lines 350-388: main() with text output."""
        import pytest
        from eval_runner import main
        monkeypatch.setattr(
            'sys.argv',
            ['eval_runner.py', '--eval-dir', EVALS_DIR])
        with pytest.raises(SystemExit) as exc_info:
            main()
        # May be 0 or 1 depending on eval pass rate
        assert exc_info.value.code in (0, 1)

    def test_main_json_output(self, monkeypatch):
        """Cover main() with --json flag."""
        import pytest
        from eval_runner import main
        monkeypatch.setattr(
            'sys.argv',
            ['eval_runner.py', '--eval-dir', EVALS_DIR, '--json'])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code in (0, 1)

    def test_main_default_eval_dir(self, monkeypatch):
        """Cover main() without --eval-dir (uses default path)."""
        import pytest
        from eval_runner import main
        monkeypatch.setattr(
            'sys.argv', ['eval_runner.py'])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code in (0, 1)

    def test_main_specific_eval(self, monkeypatch):
        """Cover main() with --eval-name."""
        import pytest
        from eval_runner import main
        monkeypatch.setattr(
            'sys.argv',
            ['eval_runner.py', '--eval-dir', EVALS_DIR,
             '--eval-name', 'qos-compatibility-analysis'])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code in (0, 1)


class TestPrefixMatching:
    """Morphology-tolerant term matching via _term_matches.

    The previous exact word-boundary matcher failed on basic inflections:
    a criterion saying 'Must warn about hardcoded paths' did not match an
    expected file containing 'Hardcoded path' (singular) or 'warning' (no
    'warn' exact word). The prefix matcher fixes that without an external
    stemmer, while still avoiding obvious over-matches.
    """

    def test_exact_match_still_works(self):
        assert _term_matches('hardcoded', 'avoid hardcoded paths') is True

    def test_plural_to_singular(self):
        assert _term_matches('paths', 'fix the hardcoded path here') is True

    def test_singular_to_plural(self):
        assert _term_matches('path', 'fix the hardcoded paths here') is True

    def test_warn_matches_warning_and_warnings(self):
        assert _term_matches('warn', 'emits a warning when stale') is True
        assert _term_matches('warn', 'collect warnings from the node') is True
        assert _term_matches('warnings', 'emits a warning when stale') is True

    def test_warned_to_warn(self):
        assert _term_matches('warned', 'we warn on every drop') is True

    def test_short_term_uses_exact_match(self):
        # Below 4 chars: exact word boundary only, no prefix expansion.
        # Otherwise 'qos' would match 'qoss' or 'qos-thing' over-eagerly.
        assert _term_matches('qos', 'check qos profile') is True
        assert _term_matches('qos', 'check qos-profile') is True  # boundary -
        assert _term_matches('qos', 'check qosprofile') is False  # no bound

    def test_unrelated_words_with_shared_4char_prefix_do_not_match(self):
        # 'pati' shares 4 chars with 'paths' and 'patient' but neither is
        # a prefix of the other, so prefix matcher rejects.
        assert _term_matches('paths', 'this is a patient record') is False

    def test_process_does_not_falsely_match_proces(self):
        # Common stemmer failure: 'process' minus 's' -> 'proces' (wrong).
        # Prefix matcher uses the full term, so 'proces' (not a real English
        # word in our expected texts) is never matched against 'process'.
        # 'process' itself matches 'processes' correctly.
        assert _term_matches('process', 'multi-process node') is True
        assert _term_matches('process', 'spawn three processes') is True

    def test_launch_file_review_eval_now_passes(self):
        """End-to-end regression for Gemini's exact failure case.

        Before the prefix matcher: 'Must warn about hardcoded paths' had
        coverage 0.25 because expected text said 'Hardcoded path' (singular)
        and 'warning' (no 'warn' exact word). After: coverage rises and the
        eval passes 80% threshold.
        """
        config = load_eval_config(EVALS_DIR)
        result = run_all_evals(
            config, EVALS_DIR, eval_name='launch-file-review')
        ev = result['evals'][0]
        assert ev['status'] == 'pass', \
            f'launch-file-review must pass post-fix: {ev}'
        assert ev['pass_rate'] >= 80.0


class TestWeightActivation:
    """eval.yaml `weight` field was previously dead config. After fix:
    pass rate is weighted, so a single high-weight failure can sink an eval
    while several low-weight failures may not. Backwards compatible: string
    criteria default to weight 1.0.
    """

    def test_extract_weights_from_dicts(self):
        criteria = [
            {'description': 'A', 'weight': 2.0},
            {'description': 'B', 'weight': 0.5},
            {'description': 'C'},  # missing weight -> default 1.0
        ]
        pairs = _extract_criteria_with_weights(criteria)
        assert pairs == [('A', 2.0), ('B', 0.5), ('C', 1.0)]

    def test_extract_weights_from_strings(self):
        pairs = _extract_criteria_with_weights(['X', 'Y'])
        assert pairs == [('X', 1.0), ('Y', 1.0)]

    def test_extract_weights_negative_is_rejected(self):
        with pytest.raises(ValueError, match='weight'):
            _extract_criteria_with_weights([{'description': 'A', 'weight': -1.0}])

    def test_extract_weights_non_numeric_is_rejected(self):
        with pytest.raises(ValueError, match='weight'):
            _extract_criteria_with_weights([{'description': 'A', 'weight': 'high'}])

    def test_weighted_pass_rate_high_weight_fail_dominates(self, tmp_path):
        """A weight=10 criterion that fails should drop pass rate below
        a weight=1 criterion that passes."""
        prompt = tmp_path / 'p.md'
        expected = tmp_path / 'e.md'
        prompt.write_text('prompt content here for size check', encoding='utf-8')
        # Expected text only addresses the small-weight criterion.
        expected.write_text(
            'this expected file describes the small low priority topic '
            'with extra words to satisfy the three-term minimum',
            encoding='utf-8')
        entry = {
            'name': 'weight_test',
            'prompt': prompt.name,
            'expected': expected.name,
            'criteria': [
                {'description': 'must address small priority topic words',
                 'weight': 1.0},
                {'description': 'must describe critical safety mechanism',
                 'weight': 10.0},
            ],
            'timeout': 1000,
        }
        result = run_eval(entry, str(tmp_path))
        # Weighted: 1.0 passes (small/priority/topic match), 10.0 fails.
        # weighted_pass_rate = 1/11 ~= 9.1%, far below 80% -> overall fail.
        assert result['status'] == 'fail'
        assert result['weighted_total'] == 11.0
        # passed_criteria counts non-weighted heads for human readability
        assert result['passed_criteria'] == 1
        assert result['total_criteria'] == 2


class TestCliThresholds:
    """--min-coverage and --min-pass-rate must be honored end-to-end."""

    def _run_cli(self, *extra_args):
        return subprocess.run(
            [sys.executable, EVAL_RUNNER, '--eval-dir', EVALS_DIR,
             '--json', *extra_args],
            capture_output=True, text=True,
        )

    def test_help_lists_thresholds(self):
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER, '--help'],
            capture_output=True, text=True,
        )
        assert '--min-coverage' in r.stdout
        assert '--min-pass-rate' in r.stdout
        assert 'default: 0.30' in r.stdout
        assert 'default: 80.0' in r.stdout

    def test_default_thresholds_pass_all(self):
        r = self._run_cli()
        assert r.returncode == 0, r.stdout + r.stderr
        d = json.loads(r.stdout)
        assert d['summary']['overall_status'] == 'pass'

    def test_strict_pass_rate_can_fail(self):
        # 100% required: any eval with one off-by-keyword criterion will fail.
        r = self._run_cli('--min-pass-rate', '100.0')
        d = json.loads(r.stdout)
        # Don't assert returncode here — depends on real eval content;
        # just verify the threshold reached the runner.
        for ev in d['evals']:
            if ev['pass_rate'] < 100.0:
                assert ev['status'] == 'fail', \
                    f'{ev["name"]} should fail at --min-pass-rate 100'

    def test_invalid_coverage_rejected(self):
        r = self._run_cli('--min-coverage', '1.5')
        assert r.returncode == 2
        assert 'min-coverage' in r.stderr

    def test_invalid_pass_rate_rejected(self):
        r = self._run_cli('--min-pass-rate', '150')
        assert r.returncode == 2
        assert 'min-pass-rate' in r.stderr


# Shared fixture helpers for judge / parity tests.


def _make_temp_eval_setup(tmp_path, eval_name, prompt_text,
                          output_text=None, baseline_text=None,
                          criteria=None, parity_cfg=None):
    """Create a self-contained eval directory under tmp_path.

    Returns (eval_dir, eval_yaml_path). Caller may invoke run_all_evals
    / run_parity_test directly against this directory.
    """
    eval_dir = tmp_path / 'evals'
    for sub in ('prompts', 'expected', 'outputs',
                'outputs_baseline', 'history'):
        (eval_dir / sub).mkdir(parents=True, exist_ok=True)
    (eval_dir / 'prompts' / f'{eval_name}.md').write_text(
        prompt_text, encoding='utf-8')
    # An expected file is always required (eval.yaml schema requires it).
    (eval_dir / 'expected' / f'{eval_name}.md').write_text(
        prompt_text, encoding='utf-8')
    if output_text is not None:
        (eval_dir / 'outputs' / f'{eval_name}.md').write_text(
            output_text, encoding='utf-8')
    if baseline_text is not None:
        (eval_dir / 'outputs_baseline' / f'{eval_name}.md').write_text(
            baseline_text, encoding='utf-8')

    cfg = {
        'skill': 'test',
        'version': '0.0.1',
        'classification': 'capability',
        'deprecation-risk': 'medium',
        'evals': [{
            'name': eval_name,
            'prompt': f'prompts/{eval_name}.md',
            'expected': f'expected/{eval_name}.md',
            'criteria': criteria or ['Must describe lifecycle node states '
                                     'including configure activate deactivate'],
            'timeout': 1000,
        }],
    }
    if parity_cfg is not None:
        cfg['parity_test'] = parity_cfg

    eval_yaml = eval_dir / 'eval.yaml'
    eval_yaml.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return str(eval_dir), str(eval_yaml)


class TestNav2SafetyPolicyFixtures:
    """Deterministic policy checks for the revised nav2-configuration eval.

    These run the LOCAL structural evaluator (run_eval with
    content_source='output' against synthetic captured outputs) — no model
    calls, no network, fully deterministic in CI. They pin the eval's
    *policy*: an answer that follows the safety guidance passes, while a
    legacy-style answer (recoveries_server on Humble, unconditional
    Spin/BackUp, hardware maximum used as the operational limit) fails.

    Note on thresholds: the default 0.30 coverage threshold is a permissive
    structural smoke check and keyword matching cannot detect negation, so
    the policy discrimination below is asserted at the stricter local
    threshold (0.55) plus direct matched-term checks on the safety
    vocabulary.
    """

    _GOOD_OUTPUT = """
Configure AMCL with the correct robot model type
(robot_model_type: nav2_amcl::DifferentialMotionModel) and laser model
(laser_model_type: likelihood_field).

Configure the global and local costmaps with the correct layers
(static, obstacle, inflation) and the 0.5 x 0.4 m robot footprint.

Set the DWB controller velocity limits from the operational speed limits
for the site (max_vel_x: 0.5, max_vel_theta: 1.0), not the hardware
maximum (1.2 m/s, 2.0 rad/s) — the SDK range is a clamp, not a target.

Configure the behavior server recoveries with the Jazzy plugin types
nav2_behaviors::Wait, nav2_behaviors::Spin and nav2_behaviors::BackUp.
Motion behaviors (spin, backup) are gated on validated robot geometry and
clearance at the stuck locations, preferring actuation-free recovery
first (wait, targeted costmap clearing) when unvalidated. Costmap
clearing can erase real obstacles and requires re-observation before
traversal. The recovery timeout values are set per behavior.

Provide a launch file with a map argument and the use_sim_time parameter.
"""

    _BAD_OUTPUT = """
Configure AMCL with the correct robot model type
(robot_model_type: nav2_amcl::DifferentialMotionModel) and laser model
(laser_model_type: likelihood_field).

Configure the global and local costmaps with the correct layers
(static, obstacle, inflation) and the 0.5 x 0.4 m robot footprint.

Set max_vel_x: 1.2 and max_vel_theta: 2.0 to match the hardware maximum
from the spec sheet.

Configure recoveries_server with recovery_plugins [spin, backup, wait]
using nav2_recoveries/Spin, nav2_recoveries/BackUp and
nav2_recoveries/Wait. Spin and backup run automatically whenever the
robot is stuck.

Provide a launch file with a map argument and the use_sim_time parameter.
"""

    _SAFETY_TERMS = ('operational', 'gated', 'clearance', 'actuation-free')

    def _nav2_entry(self):
        with open(os.path.join(EVALS_DIR, 'eval.yaml'),
                  encoding='utf-8') as fh:
            cfg = yaml.safe_load(fh)
        return next(ev for ev in cfg['evals']
                    if ev['name'] == 'nav2-stack-configuration')

    def _run_with_output(self, tmp_path, output_text, **kwargs):
        entry = dict(self._nav2_entry())
        eval_dir = tmp_path / 'evals'
        for sub in ('prompts', 'expected', 'outputs'):
            (eval_dir / sub).mkdir(parents=True, exist_ok=True)
        for key in ('prompt', 'expected'):
            src = os.path.join(EVALS_DIR, entry[key])
            with open(src, encoding='utf-8') as fh:
                (eval_dir / entry[key]).write_text(
                    fh.read(), encoding='utf-8')
        (eval_dir / 'outputs' / f'{entry["name"]}.md').write_text(
            output_text, encoding='utf-8')
        return run_eval(entry, str(eval_dir),
                        content_source='output', **kwargs)

    def test_safety_following_output_passes_default(self, tmp_path):
        result = self._run_with_output(tmp_path, self._GOOD_OUTPUT)
        assert result['status'] == 'pass', result

    def test_safety_following_output_passes_strict(self, tmp_path):
        result = self._run_with_output(
            tmp_path, self._GOOD_OUTPUT, coverage_threshold=0.55)
        assert result['status'] == 'pass', result

    def test_legacy_output_fails_strict(self, tmp_path):
        """Humble + recoveries_server, unconditional Spin/BackUp, and
        hardware-max-as-operational-limit must NOT satisfy the eval."""
        result = self._run_with_output(
            tmp_path, self._BAD_OUTPUT, coverage_threshold=0.55)
        assert result['status'] == 'fail', result
        failed = {r['criterion'] for r in result['criteria_results']
                  if not r['passed']}
        assert any('operational' in c for c in failed), failed
        assert any('actuation-free' in c for c in failed), failed

    def test_safety_vocabulary_discriminates(self):
        """The safety-policy terms must match the compliant output and
        stay unmatched in the legacy output — this is what actually
        separates the two fixtures under keyword matching."""
        entry = self._nav2_entry()
        texts = extract_criteria_text(entry['criteria'])
        for content, should_match in ((self._GOOD_OUTPUT, True),
                                      (self._BAD_OUTPUT, False)):
            results = evaluate_criteria(content, texts)
            matched = {t for r in results for t in r['matched_terms']}
            for term in self._SAFETY_TERMS:
                assert (term in matched) is should_match, (
                    f'{term!r} matched={term in matched}, '
                    f'expected {should_match}')


class TestContentSourceResolution:
    """_content_path_for_source maps logical source names to file paths."""

    def test_expected_returns_none(self):
        # 'expected' is handled by entry['_resolved_expected'], so this
        # helper returns None for it (and callers use the resolved path).
        assert _content_path_for_source('/eval', 'qos', 'expected') is None

    def test_output_path(self):
        p = _content_path_for_source('/eval', 'qos', 'output')
        assert p.endswith(os.path.join('outputs', 'qos.md'))

    def test_baseline_path(self):
        p = _content_path_for_source('/eval', 'qos', 'baseline')
        assert p.endswith(os.path.join('outputs_baseline', 'qos.md'))


class TestJudgeMode:
    """--mode=judge scores user-pasted real model outputs."""

    def test_judge_pass_when_output_addresses_criteria(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Design a lifecycle node prompt',
            output_text=(
                'The lifecycle node should configure, activate, and '
                'deactivate cleanly with proper state transitions and '
                'safety guarantees during shutdown.'),
        )
        config = load_eval_config(eval_dir)
        report = run_all_evals(config, eval_dir, content_source='output')
        assert report['summary']['overall_status'] == 'pass'
        assert report['content_source'] == 'output'

    def test_judge_skipped_when_output_missing(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Design a lifecycle node prompt',
            output_text=None,  # explicitly not captured
        )
        config = load_eval_config(eval_dir)
        report = run_all_evals(config, eval_dir, content_source='output')
        ev = report['evals'][0]
        assert ev['status'] == 'skipped'
        # No-data overall status: not a failure but not a pass either.
        assert report['summary']['overall_status'] == 'no_data'

    def test_judge_fail_when_output_misses_criteria(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Design a lifecycle node prompt',
            output_text='This response is entirely unrelated to the task.',
            criteria=['Must describe lifecycle node configure activate '
                      'deactivate transitions with safety'],
        )
        config = load_eval_config(eval_dir)
        report = run_all_evals(config, eval_dir, content_source='output')
        ev = report['evals'][0]
        assert ev['status'] == 'fail'
        assert report['summary']['overall_status'] == 'fail'

    def test_judge_cli_mode_flag(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=None,
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--mode=judge', '--json'],
            capture_output=True, text=True,
        )
        # Exit 0 because 'no_data' is not a CI failure.
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data['content_source'] == 'output'
        assert data['summary']['overall_status'] == 'no_data'
        assert data['summary']['skipped'] == 1


class TestParityMode:
    """--parity scores skill ON vs OFF and tracks history."""

    _STRONG_OUTPUT = (
        'The lifecycle node should configure, activate, and deactivate '
        'cleanly with proper state transitions, error handling, and '
        'safety guarantees during shutdown.')
    _WEAK_BASELINE = (
        'You should consider lifecycle. The transitions need to be '
        'reasonable. Make sure you handle errors.')

    def test_parity_delta_positive_when_skill_helps(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=self._STRONG_OUTPUT,
            baseline_text=self._WEAK_BASELINE,
            criteria=['Must describe lifecycle node states configure '
                      'activate deactivate safety',
                      'Must include error handling on transitions'],
            parity_cfg={'enabled': True, 'threshold': 5.0,
                        'consecutive_failures_for_deprecation': 3,
                        'metrics': []},
        )
        config = load_eval_config(eval_dir)
        report = run_parity_test(config, eval_dir)
        assert report['mode'] == 'parity'
        assert report['scored_evals'] == 1
        # ON should score >= OFF; delta non-negative.
        per = report['per_eval'][0]
        assert per['status'] == 'scored'
        assert per['skill_on_pass_rate'] >= per['skill_off_pass_rate']

    def test_parity_skips_eval_when_one_capture_missing(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=self._STRONG_OUTPUT,
            baseline_text=None,  # missing baseline
        )
        config = load_eval_config(eval_dir)
        report = run_parity_test(config, eval_dir)
        assert report['scored_evals'] == 0
        assert report['skipped_evals'] == 1
        assert report['per_eval'][0]['status'] == 'skipped'

    def test_parity_writes_history(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=self._STRONG_OUTPUT,
            baseline_text=self._WEAK_BASELINE,
        )
        config = load_eval_config(eval_dir)
        run_parity_test(config, eval_dir)
        history_dir = os.path.join(eval_dir, 'history')
        files = [f for f in os.listdir(history_dir) if f.endswith('.jsonl')]
        assert len(files) == 1
        with open(os.path.join(history_dir, files[0]), encoding='utf-8') as fh:
            entry = json.loads(fh.readline())
        assert 'avg_delta' in entry
        assert 'timestamp_utc' in entry
        assert entry['scored_evals'] == 1

    def test_parity_history_appends_not_overwrites(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=self._STRONG_OUTPUT,
            baseline_text=self._WEAK_BASELINE,
        )
        config = load_eval_config(eval_dir)
        run_parity_test(config, eval_dir)
        run_parity_test(config, eval_dir)
        history_dir = os.path.join(eval_dir, 'history')
        files = [f for f in os.listdir(history_dir) if f.endswith('.jsonl')]
        with open(os.path.join(history_dir, files[0]), encoding='utf-8') as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        assert len(lines) == 2

    def test_parity_cli_exits_zero_on_no_data(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc',
            prompt_text='Lifecycle prompt',
            output_text=None,
            baseline_text=None,
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--parity', '--json'],
            capture_output=True, text=True,
        )
        # All skipped, no deprecation possible -> exit 0.
        assert r.returncode == 0, r.stdout + r.stderr
        data = json.loads(r.stdout)
        assert data['mode'] == 'parity'
        assert data['deprecation_candidate'] is False

    def test_parity_rejects_combined_with_eval_name(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='y', baseline_text='z',
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--parity', '--eval-name', 'lc'],
            capture_output=True, text=True,
        )
        assert r.returncode == 2
        assert 'parity' in r.stderr.lower()


class TestDeprecationStreak:
    """_check_deprecation_streak flags a skill as a deprecation candidate
    when the most recent N runs all sit below threshold."""

    def _write_history(self, history_dir, results):
        """results: list of (timestamp_iso, threshold_met_bool) tuples."""
        path = os.path.join(history_dir, 'test.jsonl')
        with open(path, 'w', encoding='utf-8') as fh:
            for ts, met in results:
                fh.write(json.dumps({
                    'history_schema': 2, 'scope_sha256': 'fixture-scope',
                    'capture_sha256': hashlib.sha256(ts.encode()).hexdigest(),
                    'data_status': 'complete', 'avg_delta': 10.0 if met else 0.0,
                    'threshold': 5.0, 'scored_evals': 1, 'total_evals': 1,
                    'skipped_evals': 0, 'error_evals': 0,
                    'timestamp_utc': ts,
                    'threshold_met': met,
                }) + '\n')

    def test_three_consecutive_misses_flags_deprecation(self, tmp_path):
        self._write_history(str(tmp_path), [
            ('2026-05-01T00:00:00Z', False),
            ('2026-05-08T00:00:00Z', False),
            ('2026-05-15T00:00:00Z', False),
        ])
        assert _check_deprecation_streak(str(tmp_path), 5.0, 3, 'fixture-scope') is True

    def test_recent_pass_breaks_streak(self, tmp_path):
        self._write_history(str(tmp_path), [
            ('2026-05-01T00:00:00Z', False),
            ('2026-05-08T00:00:00Z', False),
            ('2026-05-15T00:00:00Z', True),
        ])
        assert _check_deprecation_streak(str(tmp_path), 5.0, 3, 'fixture-scope') is False

    def test_insufficient_history_does_not_flag(self, tmp_path):
        self._write_history(str(tmp_path), [
            ('2026-05-15T00:00:00Z', False),
        ])
        # Only 1 entry, need 3 -> not enough data to declare deprecation.
        assert _check_deprecation_streak(str(tmp_path), 5.0, 3, 'fixture-scope') is False

    def test_empty_history_does_not_flag(self, tmp_path):
        assert _check_deprecation_streak(str(tmp_path), 5.0, 3, 'fixture-scope') is False

    def test_uses_most_recent_by_timestamp(self, tmp_path):
        # File order may not match timestamp order; check_deprecation_streak
        # must sort by timestamp before slicing.
        self._write_history(str(tmp_path), [
            ('2026-05-15T00:00:00Z', True),   # newest, passes -> no streak
            ('2026-04-15T00:00:00Z', False),
            ('2026-04-08T00:00:00Z', False),
            ('2026-04-01T00:00:00Z', False),
        ])
        assert _check_deprecation_streak(str(tmp_path), 5.0, 3, 'fixture-scope') is False


class TestPrintReportText:
    """Cover the human-readable report printers (NODATA, skipped, parity)."""

    def test_judge_cli_text_output_shows_nodata(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x', output_text=None,
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--mode=judge'],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert '[NODATA]' in r.stdout
        assert '[SKIP]' in r.stdout
        assert 'Content source: output' in r.stdout

    def test_parity_cli_text_output(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='good answer with configure activate deactivate '
                        'safety transitions',
            baseline_text='weak vague hand-wavy non-specific',
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--parity'],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert 'Parity Test' in r.stdout
        assert 'Average delta' in r.stdout
        assert 'History file' in r.stdout

    def test_parity_rejects_explicit_mode(self, tmp_path):
        # Help documents --parity and --mode as mutually exclusive;
        # enforce it instead of silently ignoring --mode
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='captured', baseline_text='baseline',
        )
        for mode in ('structural', 'judge'):
            r = subprocess.run(
                [sys.executable, EVAL_RUNNER,
                 '--eval-dir', eval_dir, '--parity', f'--mode={mode}'],
                capture_output=True, text=True,
            )
            assert r.returncode == 2
            assert 'mutually exclusive' in r.stderr

    def test_parity_cli_skipped_eval_text(self, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='captured', baseline_text=None,
        )
        r = subprocess.run(
            [sys.executable, EVAL_RUNNER,
             '--eval-dir', eval_dir, '--parity'],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert '[SKIP]' in r.stdout


class TestPrintParityReportDirect:
    """Cover _print_parity_report directly (subprocess CLI tests do not
    increment line coverage for the parent process)."""

    def _base_report(self):
        return {
            'skill': 'test', 'version': '0.0.1', 'mode': 'parity',
            'threshold': 5.0, 'consecutive_failures_for_deprecation': 3,
            'avg_delta': 7.5, 'threshold_met': True,
            'deprecation_candidate': False,
            'scored_evals': 2, 'skipped_evals': 0,
            'history_file': '/tmp/hist.jsonl',
            'per_eval': [
                {'name': 'a', 'status': 'scored',
                 'skill_on_pass_rate': 90.0, 'skill_off_pass_rate': 80.0,
                 'delta': 10.0, 'meets_threshold': True},
                {'name': 'b', 'status': 'scored',
                 'skill_on_pass_rate': 70.0, 'skill_off_pass_rate': 65.0,
                 'delta': 5.0, 'meets_threshold': True},
            ],
        }

    def test_print_parity_report_threshold_met(self, capsys):
        _print_parity_report(self._base_report())
        out = capsys.readouterr().out
        assert 'Parity Test' in out
        assert '+7.50%' in out
        assert '[MET]' in out
        assert 'a' in out and 'b' in out

    def test_print_parity_report_threshold_miss(self, capsys):
        r = self._base_report()
        r['avg_delta'] = 2.0
        r['threshold_met'] = False
        r['per_eval'][0]['meets_threshold'] = False
        _print_parity_report(r)
        out = capsys.readouterr().out
        assert '[MISS]' in out
        assert '+2.00%' in out

    def test_print_parity_report_deprecation_candidate(self, capsys):
        r = self._base_report()
        r['deprecation_candidate'] = True
        r['threshold_met'] = False
        _print_parity_report(r)
        out = capsys.readouterr().out
        assert 'DEPRECATION CANDIDATE' in out
        assert 'most recent 3 runs' in out

    def test_print_parity_report_with_skipped(self, capsys):
        r = self._base_report()
        r['per_eval'].append({
            'name': 'c', 'status': 'skipped',
            'reason': 'no output captured yet',
        })
        r['skipped_evals'] = 1
        _print_parity_report(r)
        out = capsys.readouterr().out
        assert '[SKIP]' in out
        assert 'no output captured yet' in out


class TestPrintReportDirect:
    """Cover print_report's NODATA + skipped + parity_test footer branches."""

    def test_print_report_with_skipped_evals(self, capsys):
        report = {
            'skill': 's', 'version': '0', 'classification': 'capability',
            'deprecation_risk': 'medium', 'content_source': 'output',
            'summary': {
                'total_evals': 1, 'passed': 0, 'failed': 0,
                'errors': 0, 'skipped': 1,
                'average_pass_rate': 0.0,
                'total_execution_time_ms': 0.0,
                'overall_status': 'no_data',
            },
            'evals': [{
                'name': 'x', 'status': 'skipped',
                'reason': 'capture missing',
                'pass_rate': 0.0, 'execution_time_ms': 0,
            }],
            'parity_test': None,
        }
        print_report(report)
        out = capsys.readouterr().out
        assert '[NODATA]' in out
        assert '[SKIP] x' in out
        assert 'capture missing' in out
        assert 'Content source: output' in out

    def test_print_report_with_parity_footer(self, capsys):
        report = {
            'skill': 's', 'version': '0', 'classification': 'capability',
            'deprecation_risk': 'low', 'content_source': 'expected',
            'summary': {
                'total_evals': 1, 'passed': 1, 'failed': 0,
                'errors': 0, 'skipped': 0,
                'average_pass_rate': 100.0,
                'total_execution_time_ms': 12.3,
                'overall_status': 'pass',
            },
            'evals': [{
                'name': 'x', 'status': 'pass',
                'pass_rate': 100.0, 'passed_criteria': 3,
                'total_criteria': 3, 'execution_time_ms': 12.3,
                'criteria_results': [
                    {'criterion': 'short', 'passed': True},
                    {'criterion': 'a' * 80, 'passed': False},  # long, truncated
                ],
            }],
            'parity_test': {
                'enabled': True, 'threshold': 5.0,
                'consecutive_failures_for_deprecation': 3,
            },
        }
        print_report(report)
        out = capsys.readouterr().out
        assert '[PASS]' in out
        assert 'Parity Test: enabled' in out
        assert 'threshold: 5.0' in out
        # Long criterion should be truncated with ... marker
        assert '...' in out


class TestMainDirectInvocation:
    """Invoke main() via monkeypatch so coverage tracks the CLI plumbing
    (subprocess invocations do not propagate coverage to the parent run).
    """

    def _argv(self, *args):
        return ['eval_runner.py'] + list(args)

    def test_main_default_text_output(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv', self._argv('--eval-dir', EVALS_DIR))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert 'Lexical Eval Report' in out
        assert '[PASS]' in out

    def test_main_parity_with_temp_eval_dir(self, monkeypatch, capsys, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='configure activate deactivate safety transitions error',
            baseline_text='vague nothing useful',
        )
        monkeypatch.setattr(
            sys, 'argv', self._argv('--eval-dir', eval_dir, '--parity'))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert 'Parity Test' in out

    def test_main_parity_json_output(self, monkeypatch, capsys, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='ok', baseline_text='ok',
        )
        monkeypatch.setattr(
            sys, 'argv',
            self._argv('--eval-dir', eval_dir, '--parity', '--json'))
        with pytest.raises(SystemExit):
            eval_runner_main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data['mode'] == 'parity'

    def test_main_parity_rejects_eval_name(self, monkeypatch, capsys, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='ok', baseline_text='ok',
        )
        monkeypatch.setattr(
            sys, 'argv',
            self._argv('--eval-dir', eval_dir, '--parity',
                       '--eval-name', 'lc'))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert 'parity' in err.lower()

    def test_main_judge_mode_with_outputs(self, monkeypatch, capsys, tmp_path):
        eval_dir, _ = _make_temp_eval_setup(
            tmp_path, 'lc', prompt_text='x',
            output_text='configure activate deactivate safety transitions',
        )
        monkeypatch.setattr(
            sys, 'argv', self._argv('--eval-dir', eval_dir, '--mode=judge'))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        # When user supplies outputs, judge mode should produce a real score
        # (not NODATA).
        assert '[NODATA]' not in out

    def test_main_invalid_coverage_threshold(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, 'argv',
            self._argv('--eval-dir', EVALS_DIR, '--min-coverage', '2.0'))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 2
        assert 'min-coverage' in capsys.readouterr().err

    def test_main_invalid_pass_rate(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, 'argv',
            self._argv('--eval-dir', EVALS_DIR, '--min-pass-rate', '-1'))
        with pytest.raises(SystemExit) as exc:
            eval_runner_main()
        assert exc.value.code == 2
        assert 'min-pass-rate' in capsys.readouterr().err


class TestEvalRunnerWithoutPyYaml:
    """eval_runner must not require PyYAML at import time: the module is
    imported by this test suite and `--help` should work on a bare
    environment. Only actually running evals needs the dependency, and
    that path fails with a friendly message and exit 2."""

    def _run_with_blocked_yaml(self, tmp_path, *args):
        # A stub package that raises on import simulates the missing
        # dependency without touching the real environment.
        (tmp_path / 'yaml').mkdir()
        (tmp_path / 'yaml' / '__init__.py').write_text(
            "raise ImportError('PyYAML blocked for this test')\n",
            encoding='utf-8')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(tmp_path)
        return subprocess.run(
            [sys.executable, EVAL_RUNNER, *args],
            capture_output=True, text=True, env=env, timeout=10)

    def test_no_top_level_yaml_import(self):
        import ast
        with open(EVAL_RUNNER, 'r', encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        top_level_imports = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert 'yaml' not in top_level_imports, (
            'eval_runner.py must import PyYAML lazily, not at module level')

    def test_help_works_without_pyyaml(self, tmp_path):
        result = self._run_with_blocked_yaml(tmp_path, '--help')
        assert result.returncode == 0, result.stderr
        assert '--eval-dir' in result.stdout

    def test_run_without_pyyaml_fails_helpfully(self, tmp_path):
        result = self._run_with_blocked_yaml(
            tmp_path, '--eval-dir', EVALS_DIR)
        assert result.returncode == 2
        assert 'PyYAML is required' in result.stderr
        assert 'requirements-dev.txt' in result.stderr
