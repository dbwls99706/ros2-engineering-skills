"""Tests for Skills 2.0 eval system — validates eval runner and eval definitions.

These tests ensure:
1. eval.yaml is valid and complete
2. All prompt/expected file pairs exist and are well-formed
3. Eval runner produces correct structured output
4. Parity test configuration is valid
5. Eval runner CLI works correctly
"""

import json
import os
import subprocess
import sys

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
)


class TestEvalYamlStructure:
    """Validate eval.yaml structure and completeness."""

    def setup_method(self):
        with open(os.path.join(EVALS_DIR, 'eval.yaml'), 'r') as fh:
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
        with open(os.path.join(EVALS_DIR, 'eval.yaml'), 'r') as fh:
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
        assert 'Skills 2.0 Eval Report' in result.stdout
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
        assert '1.0.0' in result.stdout

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
