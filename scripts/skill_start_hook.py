#!/usr/bin/env python3
"""SessionStart check: read the ROS 2 workspace before any work begins.

The PreToolUse hook sees what is about to be written and the Stop hook sees
what was changed. Neither looks at what is already there, and a defect that
predates the session reaches the robot unless the model happens to open the
right two files. This hook reads the workspace once, when the session starts,
and reports where its files disagree, so the same list is available whatever
the model's effort level and whether or not it loads the skill.

Input: the hook payload on stdin (`cwd`), or SKILL_WORKSPACE.
Output: one JSON report on stdout, in the schema of the other hooks. The exit
status is always 0: this is context, never a gate.

Nothing is built, imported or run. A workspace without a package.xml, a home
directory and the filesystem root produce an empty report.
"""

import json
import os
import sys

HOOK = 'ros2-engineering-skills:start'
VERSION = '1.6.0'
MAX_ISSUES = 40
# The launch validator's warnings are mostly style advice (a missing argument
# description, a missing output= setting). At session start only what fails
# is worth the model's attention.
LAUNCH_SEVERITIES = ('error',)


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


def _launch_issues(packages, workspace_checks, launch_validator):
    issues = []
    for package in packages:
        for path in workspace_checks.launch_files(package):
            for issue in launch_validator.validate_file(str(path)):
                if issue.severity not in LAUNCH_SEVERITIES:
                    continue
                issues.append({
                    'file': str(path.relative_to(package.root.parent)), 'line': issue.line,
                    'severity': issue.severity, 'check': 'launch', 'message': issue.message})
    return issues


def collect(workspace):
    """(issues, checks_skipped, package count) for *workspace*."""
    if _too_broad(workspace):
        return [], [], 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import launch_validator
        import workspace_checks
    finally:
        sys.path.pop(0)
    issues, skipped, packages = workspace_checks.check_workspace(workspace)
    if not packages:
        return [], [], 0
    issues = issues + _launch_issues(packages, workspace_checks, launch_validator)
    order = {'error': 0, 'warning': 1, 'info': 2}
    issues.sort(key=lambda issue: (order.get(issue['severity'], 3), issue['file'], issue['line']))
    return issues, skipped, len(packages)


def main():
    issues, skipped, packages = collect(_resolve_workspace())
    if len(issues) > MAX_ISSUES:
        skipped = skipped + [f'{len(issues) - MAX_ISSUES} further findings are not listed']
        issues = issues[:MAX_ISSUES]
    print(json.dumps({
        'hook': HOOK,
        'version': VERSION,
        'packages': packages,
        'issues_count': len(issues),
        'issues': issues,
        'checks_skipped': skipped,
        'status': 'fail' if any(issue['severity'] == 'error' for issue in issues) else 'pass',
    }, indent=2))


if __name__ == '__main__':
    main()
