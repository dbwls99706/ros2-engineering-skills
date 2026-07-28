"""Tests for the repository's own tooling (.claude/hooks), not the skill.

The commit-attribution guard is a write-path gate: a false negative lets a
tool-attribution trailer into permanent history, and a false positive
blocks legitimate commits. It rejected its own introducing commit the
first time it ran — the message described the banned trailers, and the
patterns matched anywhere rather than at a line start — so both directions
are pinned here.
"""

import importlib.util
import os

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(
    REPO_ROOT, '.claude', 'hooks', 'no_ai_attribution.py')


def _load_hook():
    spec = importlib.util.spec_from_file_location('no_ai_attribution',
                                                  HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


BLOCKED = [
    ('git commit -m "fix: x\n\nCo-Authored-By: A <a@b.c>"',
     'trailer on its own line'),
    ('git commit -m "fix: x\\n\\nCo-Authored-By: A <a@b.c>"',
     'trailer with an unexpanded \\n escape'),
    ('git commit -m "fix: x\n\n- Co-Authored-By: A <a@b.c>"',
     'trailer disguised as a list item'),
    ('git commit -m "fix: x\n\nco-authored-by: a <a@b.c>"',
     'lowercase trailer'),
    ('git commit -m "x\n\n\U0001F916 Generated with Some Tool"',
     'generated-with footer'),
    ('git commit -m "x\n\nCreated with Some Tool"',
     'created-with footer'),
    ('git commit -m "x\n\nClaude-Session: abc"',
     'session trailer'),
    ('git commit -m "x\n\nhttps://claude.ai/code/session_01"',
     'session link anywhere'),
    ('git commit -am "x" && git commit --amend -m "y\n\nCo-Authored-By: a"',
     'second commit in a compound command'),
]

ALLOWED = [
    ('git commit -m "fix: correct the QoS default"',
     'ordinary commit'),
    ('git commit -m "chore: forbid Co-Authored-By trailers in messages"',
     'subject naming the banned trailer'),
    ('git commit -m "docs: explain why Generated with footers are banned"',
     'subject naming the banned footer'),
    ('git log --format=%B | grep Co-Authored-By',
     'reading history, not committing'),
    ('git show HEAD | grep -i "generated with"',
     'inspecting an existing commit'),
    ('colcon build --packages-select my_pkg',
     'unrelated command'),
]


@pytest.mark.parametrize('command,label',
                         [(c, l) for c, l in BLOCKED],
                         ids=[label for _, label in BLOCKED])
def test_attribution_is_blocked(command, label):
    assert hook.find_violations(command), (
        f'{label}: should have been rejected'
    )


@pytest.mark.parametrize('command,label',
                         [(c, l) for c, l in ALLOWED],
                         ids=[label for _, label in ALLOWED])
def test_legitimate_commands_pass(command, label):
    assert hook.find_violations(command) == [], (
        f'{label}: should have been allowed'
    )


# Message forms the hook cannot see, because it only ever receives the
# Bash command string. These are not bugs to fix at this layer — the
# message simply is not in the string — but they are the reason CLAUDE.md
# calls the hook best-effort and points at the CI job as the real gate.
# Pinned so nobody later reads a passing suite as proof of coverage.
KNOWN_BLIND_SPOTS = [
    ('git commit -F message.txt', 'message read from a file'),
    ('git commit', 'message typed in the editor'),
    ('git commit -m "$MESSAGE"', 'message in a shell variable'),
    ('git commit --amend --no-edit', 'message inherited by an amend'),
]


@pytest.mark.parametrize('command,label',
                         [(c, l) for c, l in KNOWN_BLIND_SPOTS],
                         ids=[label for _, label in KNOWN_BLIND_SPOTS])
def test_documented_blind_spots_are_not_caught(command, label):
    assert hook.find_violations(command) == [], (
        f'{label}: if this now reports a violation the hook has started '
        f'guessing at message content it cannot actually see'
    )


def test_ci_enforces_what_the_hook_cannot():
    """The authoritative check must exist, since the hook is partial."""
    path = os.path.join(REPO_ROOT, '.github', 'workflows', 'test.yml')
    with open(path, 'r', encoding='utf-8') as fh:
        workflow = fh.read()
    assert 'commit-messages:' in workflow
    assert 'co-authored-by' in workflow.lower()


def test_settings_wires_the_hook():
    """The script only runs if .claude/settings.json points at it."""
    import json
    path = os.path.join(REPO_ROOT, '.claude', 'settings.json')
    with open(path, 'r', encoding='utf-8') as fh:
        settings = json.load(fh)
    entries = [
        entry
        for group in settings['hooks']['PreToolUse']
        for entry in group['hooks']
    ]
    assert any('no_ai_attribution.py' in e['command'] for e in entries)
    for entry in entries:
        # Seconds, not milliseconds — same trap as the skill's own hooks.
        assert 0 < entry['timeout'] <= 600
