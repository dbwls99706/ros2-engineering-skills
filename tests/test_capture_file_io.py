"""File-type and snapshot regressions; fixtures are not model captures."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import verify_eval_capture as capture

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX FIFO test')
@pytest.mark.parametrize('slot', ['manifest', 'suite'])
def test_json_fifo_is_rejected_without_waiting_for_a_writer(tmp_path, slot):
    manifest, suite = tmp_path / 'capture.json', tmp_path / 'suite.json'
    manifest.write_text('{}', encoding='utf-8')
    suite.write_text('{}', encoding='utf-8')
    target = {'manifest': manifest, 'suite': suite}[slot]
    target.unlink()
    os.mkfifo(target)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/verify_eval_capture.py'),
         str(manifest), '--suite', str(suite)],
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report['status'] == 'invalid'
    assert 'regular file' in report['errors'][0]


@pytest.mark.parametrize('kind', ['directory', 'oversize', 'missing'])
def test_bounded_reader_rejects_invalid_files(tmp_path, monkeypatch, kind):
    path = tmp_path / 'input'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'oversize':
        path.write_bytes(b'12345')
        monkeypatch.setattr(capture, 'MAX_FILE_BYTES', 4)
    with pytest.raises((OSError, ValueError)):
        capture.read_regular_bytes(path)


def test_reader_accepts_exact_limit_and_empty_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'input'
    monkeypatch.setattr(capture, 'MAX_FILE_BYTES', 4)
    path.write_bytes(b'1234')
    assert capture.read_regular_bytes(path) == b'1234'
    path.write_bytes(b'')
    assert capture.read_regular_bytes(path) == b''


def test_growth_after_fstat_cannot_escape_byte_limit(tmp_path, monkeypatch):
    path = tmp_path / 'input'
    path.write_bytes(b'1234')
    monkeypatch.setattr(capture, 'MAX_FILE_BYTES', 4)
    original = capture.os.fstat

    def grow(fd):
        info = original(fd)
        with path.open('ab') as handle:
            handle.write(b'5')
        return info

    monkeypatch.setattr(capture.os, 'fstat', grow)
    with pytest.raises(ValueError, match='20 MiB'):
        capture.read_regular_bytes(path)


def test_artifact_hash_and_utf8_check_share_one_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'trace.txt'
    raw = b'valid trace'
    path.write_bytes(raw)
    calls = []
    original = capture.read_regular_bytes

    def change_after_read(target):
        calls.append(target)
        data = original(target)
        path.write_bytes(b'\xff')
        return data

    monkeypatch.setattr(capture, 'read_regular_bytes', change_after_read)
    assert capture.artifact(tmp_path, {
        'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
    }) == path
    assert calls == [path]


def test_same_hash_does_not_make_non_utf8_artifact_valid(tmp_path):
    path = tmp_path / 'trace.txt'
    raw = b'\xff'
    path.write_bytes(raw)
    with pytest.raises(UnicodeError):
        capture.artifact(tmp_path, {
            'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
        })


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX FIFO test')
def test_artifact_replaced_by_fifo_before_open_is_rejected(tmp_path):
    path = tmp_path / 'trace.txt'
    path.write_bytes(b'trace')
    # A subprocess bounds this regression even if a later edit reintroduces a hang.
    code = '''
import os, sys
from pathlib import Path
from scripts import verify_eval_capture as c
root = Path(sys.argv[1])
original = c.local_file
def replace(root, relative, allow_empty=False):
    path = original(root, relative, allow_empty)
    path.unlink()
    os.mkfifo(path)
    return path
c.local_file = replace
try:
    c.artifact(root, {'path': 'trace.txt', 'sha256': '0' * 64})
except ValueError as exc:
    assert 'regular file' in str(exc)
else:
    raise AssertionError('FIFO accepted')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            cwd=ROOT, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
