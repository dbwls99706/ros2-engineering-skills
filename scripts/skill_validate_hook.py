#!/usr/bin/env python3
"""Skills 2.0 PreToolUse Hook — Pre-execution validation for ros2-engineering-skills.

This hook runs before tool invocations during skill execution. It provides
context-aware warnings when the user's actions may conflict with ROS 2
engineering best practices defined in the skill.

Exit codes:
    0 — No blocking issues found
    1 — Blocking issue detected (should halt tool execution)
"""

import json
import os
import re
import sys


# Patterns that indicate potential ROS 2 anti-patterns in code being written
ANTIPATTERN_CHECKS = [
    {
        'pattern': r'time\.sleep\s*\(',
        'message': 'Avoid time.sleep() in ROS 2 nodes — use create_wall_timer() instead',
        'severity': 'warning',
    },
    {
        'pattern': r'spin_until_future_complete\s*\(',
        'message': ('spin_until_future_complete inside a callback causes deadlock. '
                    'Use async_send_request with a callback instead'),
        'severity': 'warning',
    },
    {
        'pattern': r'global\s+\w+',
        'message': 'Global variables break composition — store state as class members',
        'severity': 'warning',
    },
    {
        'pattern': r'ROS_LOCALHOST_ONLY',
        'message': ('ROS_LOCALHOST_ONLY is deprecated in Jazzy+. '
                    'Use ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST instead'),
        'severity': 'warning',
    },
    {
        'pattern': r'node_executable\s*=',
        'message': 'node_executable is deprecated — use executable instead',
        'severity': 'warning',
    },
    {
        'pattern': r'node_name\s*=',
        'message': 'node_name is deprecated — use name instead',
        'severity': 'warning',
    },
    {
        'pattern': r'node_namespace\s*=',
        'message': 'node_namespace is deprecated — use namespace instead',
        'severity': 'warning',
    },
]

# File extensions that should be checked
CHECKABLE_EXTENSIONS = {'.py', '.cpp', '.hpp', '.h', '.cc', '.cxx'}


def check_content(content, filename='<input>'):
    """Check content for ROS 2 anti-patterns."""
    issues = []
    for check in ANTIPATTERN_CHECKS:
        matches = list(re.finditer(check['pattern'], content))
        for match in matches:
            line_num = content[:match.start()].count('\n') + 1
            issues.append({
                'file': filename,
                'line': line_num,
                'severity': check['severity'],
                'message': check['message'],
            })
    return issues


def check_file(filepath):
    """Check a file for ROS 2 anti-patterns."""
    ext = os.path.splitext(filepath)[1]
    if ext not in CHECKABLE_EXTENSIONS:
        return []
    try:
        with open(filepath, 'r') as fh:
            content = fh.read()
        return check_content(content, filepath)
    except OSError:
        return []


def main():
    # Read tool context from environment or stdin
    tool_input = os.environ.get('TOOL_INPUT', '')
    tool_name = os.environ.get('TOOL_NAME', '')

    issues = []

    # If the tool is writing or editing a file, validate the content
    if tool_name in ('Write', 'Edit') and tool_input:
        try:
            data = json.loads(tool_input)
            filepath = data.get('file_path', '')
            content = data.get('content', '') or data.get('new_string', '')
            if content:
                issues = check_content(content, filepath)
        except (json.JSONDecodeError, AttributeError):
            pass

    # For Bash tool, check for dangerous patterns
    if tool_name == 'Bash' and tool_input:
        try:
            data = json.loads(tool_input)
            command = data.get('command', '')
            if 'rm -rf /opt/ros' in command:
                issues.append({
                    'file': '<bash>',
                    'line': 0,
                    'severity': 'error',
                    'message': 'Refusing to remove ROS installation directory',
                })
        except (json.JSONDecodeError, AttributeError):
            pass

    result = {
        'hook': 'ros2-engineering-skills:pre-tool-use',
        'version': '1.0.0',
        'issues_count': len(issues),
        'issues': issues,
        'status': 'fail' if any(
            i['severity'] == 'error' for i in issues
        ) else 'pass',
    }

    print(json.dumps(result, indent=2))

    has_errors = any(i['severity'] == 'error' for i in issues)
    sys.exit(1 if has_errors else 0)


if __name__ == '__main__':
    main()
