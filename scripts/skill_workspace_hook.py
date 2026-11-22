#!/usr/bin/env python3
"""Workspace report: where the files of a ROS 2 workspace disagree.

The PreToolUse hook sees what is about to be written and the changed-file Stop
check sees what was changed. Neither looks at the workspace as a whole. This
script reads it and reports where its files disagree. The adapter runs it
twice: when a conversation starts, to remember what was already there, and
when the model is about to finish, to hand over what is still reported.

Input: the hook payload on stdin (`cwd`), or SKILL_WORKSPACE.
Output: one JSON report on stdout, in the schema of the other hooks. The exit
status is always 0: this is a report, never a gate.

Nothing is built, imported or run. A workspace without a package.xml, a home
directory and the filesystem root produce an empty report.
"""

import hashlib
import json
import os
import sys
import time

HOOK = 'ros2-engineering-skills:workspace'
VERSION = '1.7.0'
MAX_ISSUES = 40
# The launch validator's warnings are mostly style advice (a missing argument
# description, a missing output= setting). Only what fails is reported here.
LAUNCH_SEVERITIES = ('error',)
# The client gives this hook 15 seconds and the adapter 12. Past this limit the
# checks that have not started are skipped and the report says so.
TIME_LIMIT = 8.0
# Test files that every generated package carries and that say nothing about its code.
LINT_TESTS = {'test_copyright.py', 'test_flake8.py', 'test_pep257.py', 'test_xmllint.py',
              'test_mypy.py'}
MAX_PACKAGE_STATES = 200


def _resolve_workspace():
    """SKILL_WORKSPACE, then the payload's cwd, then CLAUDE_PROJECT_DIR, then cwd."""
    explicit = os.environ.get('SKILL_WORKSPACE')
    if explicit:
        return explicit
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else None
        except (OSError, ValueError, RecursionError):
            payload = None
        if isinstance(payload, dict):
            cwd = payload.get('cwd')
            if isinstance(cwd, str) and os.path.isdir(cwd):
                return cwd
    project_dir = os.environ.get('CLAUDE_PROJECT_DIR')
    if project_dir and os.path.isdir(project_dir):
        return project_dir
    return os.getcwd()


def _too_broad(workspace):
    """A home directory or the filesystem root is not a workspace to walk."""
    real = os.path.realpath(workspace)
    return real in (os.path.realpath(os.path.expanduser('~')), os.path.abspath(os.sep))


def _is_launch_description(path):
    """A file named *.launch.py, or one that mentions generate_launch_description.

    A directory called launch also holds helper modules, node scripts and
    Python packages of that name. Those are not launch descriptions, and
    "generate_launch_description is missing" would be a guess about them.
    """
    if path.name.endswith('.launch.py'):
        return True
    try:
        return 'generate_launch_description' in path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return False


def _launch_issues(packages, workspace_checks, launch_validator, deadline=None):
    """(issues, complete). *complete* is False when the time limit cut the loop short."""
    issues = []
    for package in packages:
        for path in workspace_checks.launch_files(package):
            if deadline is not None and time.monotonic() >= deadline:
                return issues, False
            if not _is_launch_description(path):
                continue
            for issue in launch_validator.validate_file(str(path)):
                if issue.severity not in LAUNCH_SEVERITIES:
                    continue
                issues.append({
                    'file': str(path.relative_to(package.root.parent)), 'line': issue.line,
                    'severity': issue.severity, 'check': 'launch', 'message': issue.message})
    return issues, True


def package_states(workspace):
    """What the adapter needs to tell which packages a session changed and how to test them.

    A fingerprint of the files (path, size, modification time; nothing is
    read) and the directory of the package's own tests, relative to the
    workspace, or None when it has none besides the lint tests.
    """
    if _too_broad(workspace):
        return []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import workspace_checks
    finally:
        sys.path.pop(0)
    workspace = os.path.realpath(workspace)
    states = []
    for package in workspace_checks.find_packages(workspace)[:MAX_PACKAGE_STATES]:
        digest = hashlib.sha256()
        tests = None
        for path in package.files:
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.relative_to(package.root)
            digest.update(repr((str(relative), stat.st_size, stat.st_mtime_ns)).encode('utf-8'))
            if (tests is None and len(relative.parts) == 2 and relative.parts[0] in ('test', 'tests')
                    and path.suffix == '.py' and path.name not in LINT_TESTS
                    and (path.name.startswith('test_') or path.name.endswith('_test.py'))):
                tests = os.path.relpath(path.parent, workspace)
        states.append({'package': package.name,
                       'path': os.path.relpath(package.root, workspace),
                       'fingerprint': digest.hexdigest()[:24], 'tests': tests})
    return states


def collect(workspace, deadline=None):
    """(issues, checks_skipped, package count) for *workspace*.

    *deadline* is a time.monotonic() value; None means no limit.
    """
    issues, skipped, names = survey(workspace, deadline)
    return issues, skipped, len(names)


def survey(workspace, deadline=None):
    """(issues, checks_skipped, package directory names) for *workspace*."""
    if _too_broad(workspace):
        return [], [], []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import launch_validator
        import workspace_checks
    finally:
        sys.path.pop(0)
    issues, skipped, packages = workspace_checks.check_workspace(workspace, deadline)
    if not packages:
        # Nothing is claimed about a directory with no package in it, but a
        # search that was cut short is still recorded as cut short.
        return [], skipped, []
    found, complete = _launch_issues(packages, workspace_checks, launch_validator, deadline)
    issues = issues + found
    if not complete:
        skipped = skipped + ['time limit reached: not every launch file was validated']
    order = {'error': 0, 'warning': 1, 'info': 2}
    issues.sort(key=lambda issue: (order.get(issue['severity'], 3), issue['file'], issue['line']))
    return issues, skipped, [package.root.name for package in packages]


def main():
    workspace = _resolve_workspace()
    deadline = time.monotonic() + TIME_LIMIT
    issues, skipped, names = survey(workspace, deadline)
    # Past the time limit the adapter is told nothing about the packages, and
    # asks for no test: an incomplete list would read as "these did not change".
    states = package_states(workspace) if names and time.monotonic() < deadline else []
    if len(issues) > MAX_ISSUES:
        skipped = skipped + [f'{len(issues) - MAX_ISSUES} further findings are not listed']
        issues = issues[:MAX_ISSUES]
    print(json.dumps({
        'hook': HOOK,
        'version': VERSION,
        'packages': len(names),
        'issues_count': len(issues),
        'issues': issues,
        'checks_skipped': skipped,
        'package_state': states,
        'status': 'fail' if any(issue['severity'] == 'error' for issue in issues) else 'pass',
    }, indent=2))


if __name__ == '__main__':
    main()
