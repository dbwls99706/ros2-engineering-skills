"""Synthetic transport fixtures only; these are not model benchmark captures."""

import json
from datetime import datetime, timezone

import pytest

from scripts import verify_eval_capture as capture

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def save(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / 'prompt.md').write_text('Diagnose QoS using evidence.', encoding='utf-8')
    suite = {'schema_version': 1, 'trials': 1, 'cases': [
        {'id': 'qos', 'prompt': 'prompt.md', 'criteria': ['Check live endpoints.']}]}
    suite_path = tmp_path / 'suite.json'
    save(suite_path, suite)
    manifest = {'schema_version': 1, 'skill_revision': 'a' * 40,
                'suite_sha256': capture.digest(suite_path), 'client': 'synthetic-test',
                'client_version': 'test', 'model': 'not-a-model',
                'generation_parameters': {},
                'environment': dict.fromkeys(('os', 'ros_distro', 'rmw', 'workspace_revision',
                                              'tool_permissions'), 'test'), 'runs': []}
    for condition in ('on', 'off'):
        run = {'case_id': 'qos', 'trial': 1, 'condition': condition,
               'session_id': condition, 'skill_loaded': condition == 'on',
               'captured_at': '2026-09-06T00:00:00Z',
               'prompt_sha256': capture.digest(tmp_path / 'prompt.md')}
        for kind in ('output', 'trace'):
            path = tmp_path / (condition + '-' + kind + '.txt')
            path.write_text('Synthetic ' + kind, encoding='utf-8')
            run[kind] = {'path': path.name, 'sha256': capture.digest(path)}
        manifest['runs'].append(run)
    manifest_path = tmp_path / 'capture.json'
    save(manifest_path, manifest)
    return manifest_path, suite_path, manifest, suite


def check(bundle):
    manifest_path, suite_path, manifest, _ = bundle
    save(manifest_path, manifest)
    return capture.validate(manifest_path, suite_path, now=NOW)


def test_complete_pair_is_integrity_only(bundle):
    report = check(bundle)
    assert report['status'] == 'integrity_valid' and report['pairs_checked'] == 1
    assert 'authenticity' in report['boundary']
    assert 'score' not in report


@pytest.mark.parametrize('field,value', [('runs', []), ('runs', None), ('schema_version', True),
                                       ('schema_version', 2), ('skill_revision', 'main'),
                                       ('suite_sha256', '0' * 64), ('client', ''),
                                       ('model', None), ('environment', {}),
                                       ('generation_parameters', [])])
def test_bad_manifest_is_failure(bundle, field, value):
    bundle[2][field] = value
    assert check(bundle)['status'] == 'invalid'


@pytest.mark.parametrize('field,value', [('case_id', []), ('case_id', 'unknown'),
                                       ('trial', True), ('trial', 2), ('condition', []),
                                       ('condition', 'baseline'), ('skill_loaded', False),
                                       ('skill_loaded', 'true'), ('session_id', ''),
                                       ('captured_at', '2026-10-01T00:00:00Z'),
                                       ('captured_at', '2026-09-06'), ('captured_at', 'bad'),
                                       ('prompt_sha256', '0' * 64), ('output', []),
                                       ('trace', {'path': 'missing', 'sha256': '0' * 64})])
def test_bad_run_is_failure(bundle, field, value):
    bundle[2]['runs'][0][field] = value
    assert check(bundle)['status'] == 'invalid'


def test_no_partial_pairs(bundle):
    bundle[2]['runs'].pop()
    assert 'Missing paired runs' in check(bundle)['errors'][0]


def test_duplicate_session_rejected(bundle):
    bundle[2]['runs'][1]['session_id'] = 'on'
    assert 'isolated' in check(bundle)['errors'][0]


def test_duplicate_run_rejected(bundle):
    bundle[2]['runs'].append(bundle[2]['runs'][0])
    assert 'duplicate' in check(bundle)['errors'][0]


def test_modified_artifact_rejected(bundle):
    (bundle[0].parent / 'on-output.txt').write_text('tampered', encoding='utf-8')
    assert 'hash mismatch' in check(bundle)['errors'][0]


def test_shared_artifact_path_rejected(bundle):
    bundle[2]['runs'][1]['trace'] = bundle[2]['runs'][0]['trace']
    assert 'own output and trace' in check(bundle)['errors'][0]


def test_answer_cannot_be_its_own_trace(bundle):
    bundle[2]['runs'][0]['trace'] = bundle[2]['runs'][0]['output']
    assert 'separate' in check(bundle)['errors'][0]


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', 'C:/secret', 'a\\b', '', None])
def test_unsafe_paths_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        capture.local_file(tmp_path, path)


def test_symlink_escape_rejected(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir()
    outside = tmp_path / 'secret.txt'
    outside.write_text('data', encoding='utf-8')
    (root / 'escape').symlink_to(outside)
    with pytest.raises(ValueError):
        capture.local_file(root, 'escape')


def test_blank_artifact_rejected(bundle):
    file = bundle[0].parent / 'on-output.txt'
    file.write_text('   ', encoding='utf-8')
    bundle[2]['runs'][0]['output']['sha256'] = capture.digest(file)
    assert 'Blank text' in check(bundle)['errors'][0]


@pytest.mark.parametrize('key,value', [('trials', 0), ('trials', True), ('cases', []),
                                     ('schema_version', 3), ('cases', [None]),
                                     ('cases', [{'id': 'qos', 'criteria': []}])])
def test_invalid_suite(bundle, key, value):
    bundle[3][key] = value
    save(bundle[1], bundle[3])
    bundle[2]['suite_sha256'] = capture.digest(bundle[1])
    assert check(bundle)['status'] == 'invalid'


def test_duplicate_suite_case(bundle):
    bundle[3]['cases'] *= 2
    save(bundle[1], bundle[3])
    bundle[2]['suite_sha256'] = capture.digest(bundle[1])
    assert 'Duplicate suite case' in check(bundle)['errors'][0]


def test_duplicate_json_keys(tmp_path):
    file = tmp_path / 'duplicate.json'
    file.write_text('{"a":1,"a":2}', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        capture.load_object(file)


def test_missing_capture_is_nonzero(bundle, capsys):
    assert capture.main([str(bundle[0].with_name('missing.json')), '--suite', str(bundle[1])]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'invalid'


def test_cli_valid_capture(bundle, capsys):
    assert capture.main([str(bundle[0]), '--suite', str(bundle[1])]) == 0
    assert json.loads(capsys.readouterr().out)['pairs_checked'] == 1
