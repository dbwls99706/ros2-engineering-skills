#!/usr/bin/env python3
"""PreToolUse guard: keep tool-attribution trailers out of commit messages.

CLAUDE.md requires that commits carry only a description of the change —
no ``Co-Authored-By:`` trailers, no "Generated with" footers, no session
links. The repository-local ``.git/hooks/commit-msg`` enforces that for
anyone who has run it, but git hooks are not cloned, so this checks the
same rule at the point where the commit is composed.

Scope is deliberately narrow: only Bash commands that invoke ``git commit``
are inspected, and any internal error falls through to "allow". A guard on
the write path must never be the reason a session cannot make progress.

Exit codes follow Claude Code's hook protocol:
    0 — allow
    2 — block the tool call; stderr is fed back as the reason
"""

import json
import re
import sys


# A trailer is a *line* in the message, so these anchor to a line start
# rather than matching anywhere. Without that, a commit whose body
# explains the rule ("no Co-Authored-By trailers") blocks itself — which
# is exactly what happened the first time this guard ran.
#
# `\\n` covers the literal two-character escape, since a message written
# as `git commit -m "subject\n\nTrailer: x"` reaches the hook with the
# newline unexpanded. Leading punctuation is allowed so a trailer cannot
# slip through as a list item.
_LINE_START = r'(?:^|\\n)[ \t>*+-]*'

FORBIDDEN_PATTERNS = [
    (_LINE_START + r'co-authored-by\s*:', 'Co-Authored-By trailer'),
    (_LINE_START + r'(?:\U0001F916\s*)?generated\s+with\b',
     '"Generated with" footer'),
    (_LINE_START + r'(?:\U0001F916\s*)?created\s+with\b',
     '"Created with" footer'),
    (_LINE_START + r'claude-session\s*:', 'Claude-Session trailer'),
    # These two are traces wherever they appear, not just as trailers.
    (r'https?://claude\.ai/\S*', 'claude.ai session link'),
    (r'\U0001F916', 'robot emoji'),
]

_GIT_COMMIT = re.compile(r'\bgit\b[^\n|;&]*\bcommit\b')


def find_violations(command):
    """Return human-readable descriptions of any forbidden trailers."""
    if not _GIT_COMMIT.search(command):
        return []
    return [
        label for pattern, label in FORBIDDEN_PATTERNS
        if re.search(pattern, command, re.IGNORECASE | re.MULTILINE)
    ]


def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
        if payload.get('tool_name') != 'Bash':
            sys.exit(0)
        command = (payload.get('tool_input') or {}).get('command', '')
        violations = find_violations(command)
    except Exception:  # noqa: BLE001 - fail open, never wedge the session
        sys.exit(0)

    if not violations:
        sys.exit(0)

    print(
        'Blocked: this commit message contains ' + ', '.join(violations) +
        '. See CLAUDE.md "Commit messages" — commits must describe only the '
        'change, with no tool-attribution trailers. Rewrite the message '
        'without it.',
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == '__main__':
    main()
