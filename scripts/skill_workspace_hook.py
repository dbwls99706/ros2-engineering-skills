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

import json
import os
import sys
import time

HOOK = 'ros2-engineering-skills:workspace'
VERSION = '1.7.0'
MAX_ISSUES = 40
MAX_SOURCES = 600
# The launch validator's warnings are mostly style advice (a missing argument
# description, a missing output= setting). Only what fails is reported here.
LAUNCH_SEVERITIES = ('error',)
# The client gives this hook 15 seconds and the adapter 12. Past this limit the
# checks that have not started are skipped and the report says so.
TIME_LIMIT = 8.0


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


def collect(workspace, deadline=None):
    """(issues, checks_skipped, package count) for *workspace*.

    *deadline* is a time.monotonic() value; None means no limit.
    """
    issues, skipped, names = survey(workspace, deadline)
    return issues, skipped, len(names)


def sources(workspace):
    """Python files of the packages under *workspace*, files that start something first.

    Each entry is {'path': as findings name it, 'real': resolved path,
    'entry': True for a launch file or the module of a console script}. Test
    code and setup.py are left out. The adapter compares the list with the
    files a session opened.
    """
    if _too_broad(workspace):
        return []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import workspace_checks
    finally:
        sys.path.pop(0)
    found = []
    for package in workspace_checks.find_packages(workspace_checks.Path(workspace).resolve()):
        launch = set(workspace_checks.launch_files(package))
        entries = set()
        for module, _function in (package.console_scripts or {}).values():
            path = workspace_checks._module_file(package, module)
            if path is not None:
                entries.add(path)
        for path in package.files:
            relative = path.relative_to(package.root)
            if path.suffix != '.py' or path.name == 'setup.py' or workspace_checks._is_test(relative):
                continue
            found.append({'path': str(path.relative_to(package.root.parent)),
                          'real': os.path.realpath(path),
                          'entry': path in launch or path in entries})
    found.sort(key=lambda item: (not item['entry'], item['path']))
    return found[:MAX_SOURCES]


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
    issues, skipped, names = survey(workspace, time.monotonic() + TIME_LIMIT)
    if len(issues) > MAX_ISSUES:
        skipped = skipped + [f'{len(issues) - MAX_ISSUES} further findings are not listed']
        issues = issues[:MAX_ISSUES]
    print(json.dumps({
        'hook': HOOK,
        'version': VERSION,
        'packages': len(names),
        'sources': sources(workspace) if names else [],
        'issues_count': len(issues),
        'issues': issues,
        'checks_skipped': skipped,
        'status': 'fail' if any(issue['severity'] == 'error' for issue in issues) else 'pass',
    }, indent=2))


if __name__ == '__main__':
    main()
