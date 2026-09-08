#!/usr/bin/env python3
"""Measure the selected skill body with named reference BPE tokenizers.

Requires tiktoken (development only). Missing tokenizer/data is not a budget pass.
The counts exclude host prompts, runtime wrappers, and on-demand reference files;
they are not claimed to be Claude, Gemini, Cursor, or every Codex model's counts.
"""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys

ENCODINGS = ('o200k_base', 'cl100k_base')
MAX_FILE_BYTES = 1024 * 1024


class TokenizerUnavailable(RuntimeError):
    """The requested tokenizer or its data could not be loaded."""


def load_encoding(name):
    try:
        import tiktoken
        return tiktoken.get_encoding(name), version('tiktoken')
    except (ImportError, OSError, ValueError) as exc:
        raise TokenizerUnavailable(type(exc).__name__) from exc


def selected_body(path):
    path = Path(path)
    with path.open('rb') as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('SKILL.md exceeds the 1 MiB input limit')
    text = raw.decode('utf-8')
    match = re.match(r'\A---\r?\n.*?\r?\n---(?:\r?\n|\Z)', text, re.S)
    if match is None or not text[match.end():].strip():
        raise ValueError('Expected frontmatter and nonempty skill instructions')
    return raw, text[match.end():]


def measure(path, max_tokens=5000, encodings=ENCODINGS):
    report = {'status': 'invalid', 'scope': 'selected body / named reference tokenizer only',
              'max_tokens': max_tokens, 'measurements': [], 'errors': []}
    try:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError('max_tokens must be a positive integer')
        if not encodings or len(set(encodings)) != len(encodings) or any(e not in ENCODINGS for e in encodings):
            raise ValueError('Specify unique supported reference encodings')
        raw, body = selected_body(path)
        report.update(skill_sha256=hashlib.sha256(raw).hexdigest(),
                      body_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest(),
                      body_bytes=len(body.encode('utf-8')), body_lines=len(body.splitlines()))
        for name in encodings:
            encoding, package_version = load_encoding(name)
            # Literal special-token spellings in Markdown are ordinary input text.
            count = len(encoding.encode_ordinary(body))
            report['measurements'].append({'encoding': name, 'tokenizer': 'tiktoken',
                                           'tokenizer_version': package_version, 'tokens': count})
        report['status'] = ('over_budget' if any(m['tokens'] > max_tokens for m in report['measurements'])
                            else 'measured_within_budget')
    except TokenizerUnavailable as exc:
        report['status'] = 'unavailable'
        report['errors'].append('Tokenizer/data unavailable: ' + str(exc))
    except (OSError, ValueError, TypeError) as exc:
        report['errors'].append(str(exc))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill', type=Path, default=Path(__file__).resolve().parents[1] / 'SKILL.md')
    parser.add_argument('--max-tokens', type=int, default=5000)
    parser.add_argument('--encoding', choices=ENCODINGS, action='append')
    args = parser.parse_args(argv)
    report = measure(args.skill, args.max_tokens, args.encoding or ENCODINGS)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'measured_within_budget' else 1


if __name__ == '__main__':
    sys.exit(main())
