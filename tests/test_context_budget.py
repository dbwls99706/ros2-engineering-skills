"""Synthetic tokenizer tests; only a real installed tokenizer can measure a budget."""

import json
from types import SimpleNamespace
import sys

import pytest

from scripts import measure_context as context


@pytest.fixture
def skill(tmp_path):
    path = tmp_path / 'SKILL.md'
    path.write_text('---\nname: test\ndescription: Test\n---\nUse this skill.\n', encoding='utf-8')
    return path


@pytest.fixture
def encoder(monkeypatch):
    value = SimpleNamespace(encode_ordinary=lambda text: list(text))
    monkeypatch.setattr(context, 'load_encoding', lambda name: (value, 'synthetic-test-only'))
    return value


def test_measures_body_not_metadata(skill, encoder):
    report = context.measure(skill)
    assert report['status'] == 'measured_within_budget'
    assert report['body_bytes'] == len('Use this skill.\n')
    assert {row['encoding'] for row in report['measurements']} == set(context.ENCODINGS)
    assert all(row['tokens'] == len('Use this skill.\n') for row in report['measurements'])
    assert 'reference tokenizer only' in report['scope']
    assert len(report['skill_sha256']) == len(report['body_sha256']) == 64


def test_ordinary_encoder_handles_literal_special_tokens(skill, encoder):
    seen = []
    encoder.encode_ordinary = lambda text: seen.append(text) or [0, 1]
    skill.write_text('---\nname: test\n---\n<|endoftext|>\n')
    assert context.measure(skill)['status'] == 'measured_within_budget'
    assert seen == ['<|endoftext|>\n'] * 2


def test_over_budget_does_not_pass(skill, encoder):
    assert context.measure(skill, max_tokens=1)['status'] == 'over_budget'


@pytest.mark.parametrize('limit', [True, 0, -1, 1.5, '5000'])
def test_invalid_limits(skill, encoder, limit):
    assert context.measure(skill, max_tokens=limit)['status'] == 'invalid'


@pytest.mark.parametrize('encodings', [[], ['wrong'], ['o200k_base', 'o200k_base']])
def test_invalid_encodings(skill, encoder, encodings):
    assert context.measure(skill, encodings=encodings)['status'] == 'invalid'


@pytest.mark.parametrize('raw', [b'no frontmatter', b'---\nname: test\n---\n', b'\xff', b'x' * (1024 * 1024 + 1)],
                         ids=['missing-frontmatter', 'empty-body', 'invalid-utf8', 'too-large'])
def test_invalid_input(skill, raw):
    skill.write_bytes(raw)
    assert context.measure(skill)['status'] == 'invalid'


def test_missing_file_is_not_a_pass(tmp_path):
    assert context.measure(tmp_path / 'missing')['status'] == 'invalid'


def test_missing_tokenizer_is_not_a_byte_approximation(skill, monkeypatch):
    def unavailable(name):
        raise context.TokenizerUnavailable('synthetic offline test')
    monkeypatch.setattr(context, 'load_encoding', unavailable)
    report = context.measure(skill)
    assert report['status'] == 'unavailable' and report['measurements'] == []
    assert report['body_bytes'] > 0


@pytest.mark.parametrize('failure', [ImportError, OSError, ValueError])
def test_loader_failures_are_explicit(monkeypatch, failure):
    def broken(name):
        raise failure('synthetic load failure')
    monkeypatch.setitem(sys.modules, 'tiktoken', SimpleNamespace(get_encoding=broken))
    with pytest.raises(context.TokenizerUnavailable):
        context.load_encoding('o200k_base')


def test_loader_records_real_package_version(monkeypatch):
    encoding = object()
    monkeypatch.setitem(sys.modules, 'tiktoken', SimpleNamespace(get_encoding=lambda name: encoding))
    monkeypatch.setattr(context, 'version', lambda name: 'synthetic-version')
    assert context.load_encoding('cl100k_base') == (encoding, 'synthetic-version')


def test_cli_success_and_failure(skill, encoder, capsys):
    assert context.main(['--skill', str(skill)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'measured_within_budget'
    assert context.main(['--skill', str(skill), '--max-tokens', '1']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'over_budget'


def test_crlf_frontmatter_and_unicode(skill, encoder):
    skill.write_bytes('---\r\nname: test\r\n---\r\n로봇\n'.encode())
    report = context.measure(skill)
    assert report['body_bytes'] == len('로봇\n'.encode())
    assert report['body_lines'] == 1
