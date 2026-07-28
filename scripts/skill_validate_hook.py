#!/usr/bin/env python3
"""Skills 2.0 PreToolUse Hook — Pre-execution validation for ros2-engineering-skills.

This hook runs before tool invocations during skill execution. It provides
context-aware warnings when the user's actions may conflict with ROS 2
engineering best practices defined in the skill.

Modes:

* **Event mode** (default, no arguments): consumes a Claude Code PreToolUse
  payload from stdin (or the ``TOOL_NAME``/``TOOL_INPUT`` env fallback) and
  checks the tool input. Without a payload there is nothing to check and the
  result is an empty pass — this mode is NOT a workspace scanner.
* **Manual mode** (``--file`` and/or ``--command``): for platforms without
  Claude Code hooks. ``--file`` checks the named source files for
  anti-patterns; ``--command`` inspects a shell command string for dangerous
  patterns. The command string is analyzed only — it is NEVER executed.
  Both options may be combined; all issues are aggregated into one report.

Scope and limits — read before relying on this:

* The Bash dangerous-command checks below are best-effort sanity guards
  intended to catch obvious accidents like a literal ``rm -rf /``. They are
  *not* a security boundary. Variable expansion (``rm -rf $X``), command
  substitution (``rm -rf $(echo /)``), aliasing, here-docs, and minor
  whitespace variations all defeat plain regex matching. Treat any code
  path that depends on these checks for safety as broken.
* The anti-pattern checks scan source for common ROS 2 mistakes. They skip
  single-line comments (``#`` in Python, ``//`` in C/C++, both for unknown
  extensions) but do *not* skip Python triple-quoted strings, so a
  docstring mentioning ``time.sleep()`` will produce a warning.
  This is intentional: the hook errs on the side of false positives over
  false negatives.

Exit codes differ by mode, because the two modes answer to different
contracts:

* **Event mode** speaks Claude Code's hook protocol. Only exit code **2**
  blocks a tool call — the docs are explicit that "Claude Code treats exit
  code 1 as a non-blocking error and proceeds with the action". On exit 2
  Claude Code ignores stdout entirely and feeds **stderr** back as the
  error message, so the reason is written to stderr; the JSON report still
  goes to stdout for anyone capturing it.

    0 — No blocking issues found
    2 — Blocking issue detected; the tool call is refused

* **Manual mode** is an ordinary CLI, where a non-zero exit means "issues
  found" in the usual Unix sense.

    0 — No blocking issues found
    1 — Blocking issue detected
"""

import argparse
import json
import os
import re
import sys


# Patterns that indicate potential ROS 2 anti-patterns in code being written.
#
# Optional fields per entry:
#   pattern_flags  - extra `re` flags (e.g. re.MULTILINE). Default 0.
#   file_filter    - callable(filename) -> bool; if present, regex only runs
#                    when the filter returns True. Default: applies to all
#                    checkable files. Use to scope launch-only or
#                    ROS-specific patterns to where they are real signals.
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
        # Python `global` is a statement, only valid at the start of a line
        # inside a function body. Anchoring at line-start eliminates false
        # positives from identifiers/strings/class attrs containing "global"
        # (e.g. `dict["global_state"]`, `global_var = 1`).
        'pattern': r'^[ \t]*global\s+\w+',
        'pattern_flags': re.MULTILINE,
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
        # Deprecated launch_ros kwarg — only meaningful inside a launch file.
        # Other Python code may legitimately have an attribute/kwarg of the
        # same name (e.g. a dataclass `node_executable: str = ...`).
        'file_filter': lambda fp: _is_launch_file(fp),
    },
    {
        'pattern': r'node_name\s*=',
        'message': 'node_name is deprecated — use name instead',
        'severity': 'warning',
        'file_filter': lambda fp: _is_launch_file(fp),
    },
    {
        'pattern': r'node_namespace\s*=',
        'message': 'node_namespace is deprecated — use namespace instead',
        'severity': 'warning',
        'file_filter': lambda fp: _is_launch_file(fp),
    },
]


def _is_launch_file(filepath):
    """Both official Python launch naming conventions."""
    return filepath.endswith('.launch.py') or filepath.endswith('_launch.py')


# File extensions that should be checked
CHECKABLE_EXTENSIONS = {'.py', '.cpp', '.hpp', '.h', '.cc', '.cxx'}


_CPP_EXTENSIONS = ('.cpp', '.hpp', '.h', '.cc', '.cxx')


def _comment_markers(filename):
    """Pick single-line comment markers appropriate for the file's language.

    Treating ``//`` as a comment in Python would make floor division
    (``total // 2``) suppress real matches later on the same line, so
    Python files only honour ``#``. C/C++ files only honour ``//``
    (``#`` starts a preprocessor directive there, which is code).
    Unknown extensions keep the conservative both-markers behaviour.
    """
    if filename.endswith('.py'):
        return ('#',)
    if filename.endswith(_CPP_EXTENSIONS):
        return ('//',)
    return ('#', '//')


def _is_in_comment(content, pos, markers=('#', '//')):
    """Heuristically check if a position falls inside a single-line comment.

    This reduces false positives from anti-pattern regex checks that match
    inside inline comments or docstring-style comment lines.  The heuristic
    is intentionally conservative: it only skips matches clearly inside
    single-line comments (see ``_comment_markers`` for which markers apply
    per language).  It does NOT skip string literals, because code like
    ``os.environ["ROS_LOCALHOST_ONLY"]`` is real usage even though the
    variable name appears adjacent to quotes.
    """
    line_start = content.rfind('\n', 0, pos) + 1
    line_end = content.find('\n', pos)
    if line_end == -1:
        line_end = len(content)
    line = content[line_start:line_end]
    col = pos - line_start

    # Single-line comment: if the first marker on the line is
    # before the match position, the match is in a comment.
    for marker in markers:
        idx = line.find(marker)
        if idx != -1 and col > idx:
            # Make sure the '#' isn't inside a string on that line
            # by checking that there's no odd number of quotes before it
            prefix = line[:idx]
            in_str = False
            for q in ('"', "'"):
                if len(re.findall(r'(?<!\\)' + q, prefix)) % 2 == 1:
                    in_str = True
                    break
            if not in_str:
                return True

    return False


def check_content(content, filename='<input>'):
    """Check content for ROS 2 anti-patterns.

    Matches inside single-line ``#``/``//`` comments are skipped to reduce
    false positives (e.g. a docstring mentioning ``time.sleep()``). Patterns
    with a ``file_filter`` entry only run for files the filter approves —
    used to scope deprecated-launch-kwarg checks to ``*.launch.py`` only,
    so that a regular Python module with an attribute named ``node_name``
    is not falsely flagged.
    """
    issues = []
    markers = _comment_markers(filename)
    for check in ANTIPATTERN_CHECKS:
        file_filter = check.get('file_filter')
        if file_filter and not file_filter(filename):
            continue
        flags = check.get('pattern_flags', 0)
        matches = list(re.finditer(check['pattern'], content, flags))
        for match in matches:
            if _is_in_comment(content, match.start(), markers):
                continue
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
        with open(filepath, 'r', encoding='utf-8') as fh:
            content = fh.read()
        return check_content(content, filepath)
    except OSError:
        return []


# Best-effort regex guards against common destructive commands.
# NOT a security boundary — see module docstring. These exist to catch
# accidental literal commands like `rm -rf /`, not to defend against an
# adversary or an LLM that knows about $IFS, $(...), or `eval`.
DANGEROUS_COMMAND_PATTERNS = [
    # `(?=\s|$|[;&|])` terminates a target path at whitespace, end of input,
    # or a shell separator, so compound commands (`rm -rf /; echo done`,
    # `rm -rf / && true`) are still caught, while longer paths under the
    # same root (`rm -rf /var/lib/apt/lists/*`) are not.
    {
        'pattern': r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|(-[a-zA-Z]+\s+)*)/(?=\s|$|[;&|])',
        'message': 'Refusing to remove root filesystem (rm -rf /)',
    },
    {
        'pattern': r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|(-[a-zA-Z]+\s+)*)/\*',
        'message': 'Refusing to remove all files from root (rm -rf /*)',
    },
    {
        'pattern': r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)/opt/ros\b',
        'message': 'Refusing to remove ROS installation directory',
    },
    {
        # Matches removal of the critical directory itself (optionally with
        # a trailing / or /*), NOT of arbitrary subpaths beneath it.
        'pattern': (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)'
                    r'/(usr|bin|sbin|etc|var|boot|lib|lib64)'
                    r'(?:/\*)?/?(?=\s|$|[;&|])'),
        'message': 'Refusing to remove critical system directory',
    },
    {
        'pattern': (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)'
                    r'(~|\$HOME)/?(?=\s|$|[;&|])'),
        'message': 'Refusing to remove home directory',
    },
    {
        'pattern': r'\bmkfs\b',
        'message': 'Refusing to format filesystem (mkfs)',
    },
    {
        'pattern': r'\bdd\s+.*\bof\s*=\s*/dev/(sd|nvme|vd|hd)',
        'message': 'Refusing to write directly to block device (dd)',
    },
    {
        # Only the filesystem root itself (`/` or `/*`), not any absolute path.
        'pattern': r'\bchmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)777\s+/\*?(?=\s|$|[;&|])',
        'message': 'Refusing to recursively chmod 777 on root filesystem',
    },
    {
        'pattern': r':\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:',
        'message': 'Refusing to execute fork bomb',
    },
    {
        'pattern': r'>\s*/dev/(sd|nvme|vd|hd)',
        'message': 'Refusing to overwrite block device',
    },
    # PowerShell / Windows equivalents. Maintainer works on Windows and the
    # harness may forward `pwsh`/`powershell` commands under TOOL_NAME=Bash
    # (the agent's logical tool name), so the bash branch can carry PS syntax.
    # Same best-effort caveat as above: these are not a security boundary.
    #
    # Implementation notes:
    #   * (?i) inline flag — PowerShell cmdlets are case-insensitive
    #     (`remove-item` == `Remove-Item`).
    #   * `\b` only attaches to alphanumeric ends; on a hyphen-prefixed flag
    #     like `-Recurse` the leading `\b` would fail (space → `-` is not a
    #     word boundary). Lookaheads avoid that trap and also let `-Recurse`
    #     and `-Force` appear in any order.
    #   * Drive-root regex requires `:` then `/` or `\` then nothing else
    #     meaningful, so `C:/Users/me/build` (a real subdir) does NOT match.
    {
        'pattern': (r'(?i)\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*[A-Za-z]:[\\/]\s*(?:["\']?\s*)?(?:[;\n]|$)'),
        'message': 'Refusing to recursively force-remove a drive root (Remove-Item)',
    },
    {
        'pattern': (r'(?i)\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*(?:\$HOME|\$env:USERPROFILE|~)\s*'
                    r'(?:["\']?\s*)?(?:[;\n]|$)'),
        'message': 'Refusing to recursively force-remove the home directory (Remove-Item)',
    },
    {
        'pattern': (r'(?i)\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*[A-Za-z]:[\\/](?:Windows|Program Files|Program Files \(x86\)|Users)\b'),
        'message': 'Refusing to recursively force-remove a critical Windows directory',
    },
    {
        'pattern': r'(?i)\bFormat-Volume\b',
        'message': 'Refusing to format volume (Format-Volume)',
    },
    {
        'pattern': r'(?i)\bClear-Disk\b',
        'message': 'Refusing to clear disk (Clear-Disk)',
    },
    {
        'pattern': r'(?i)\bRemove-Partition\b',
        'message': 'Refusing to remove partition (Remove-Partition)',
    },
    {
        'pattern': r'(?i)\brmdir\b\s+/s\s+/q\s+[A-Za-z]:[\\/]?\s*$',
        'message': 'Refusing to silently recursively remove drive root (rmdir /s /q)',
    },
]


def _check_dangerous_commands(command):
    """Check a bash command string for dangerous patterns."""
    issues = []
    for check in DANGEROUS_COMMAND_PATTERNS:
        if re.search(check['pattern'], command):
            issues.append({
                'file': '<bash>',
                'line': 0,
                'severity': 'error',
                'message': check['message'],
            })
    return issues


def _read_tool_context():
    """Resolve (tool_name, tool_input_dict, debug_info) from the harness.

    Real Claude Code (verified 2026-05-21 by capturing actual hook fires)
    sends the full hook payload as a JSON object on STDIN, NOT via env vars.
    The payload shape is::

        {
          "session_id": "...",
          "transcript_path": "...",
          "cwd": "...",
          "hook_event_name": "PreToolUse",
          "tool_name": "Bash",
          "tool_input": { "command": "...", "description": "..." },
          "tool_use_id": "toolu_..."
        }

    Note `tool_input` is already a parsed object, NOT a JSON string.

    For backwards-compatible mock testing (the existing pytest suite uses
    env vars), we fall back to `TOOL_NAME` and `TOOL_INPUT` (where
    `TOOL_INPUT` is a JSON-encoded string) if stdin is empty or absent.
    """
    debug = {'source': None}

    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except Exception:  # noqa: BLE001 - stdin read can fail many ways
            raw = ''
        if raw.strip():
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    debug['source'] = 'stdin'
                    debug['payload_keys'] = sorted(payload.keys())
                    tool_name = str(payload.get('tool_name') or '')
                    tool_input = payload.get('tool_input') or {}
                    if not isinstance(tool_input, dict):
                        # Defensive: future schema versions might wrap it.
                        tool_input = {}
                    return tool_name, tool_input, debug
            except json.JSONDecodeError as e:
                debug['stdin_parse_error'] = str(e)

    debug['source'] = 'env'
    tool_name = os.environ.get('TOOL_NAME', '')
    raw_env = os.environ.get('TOOL_INPUT', '')
    tool_input: dict = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                tool_input = parsed
        except json.JSONDecodeError as e:
            debug['env_parse_error'] = str(e)
    return tool_name, tool_input, debug


def _manual_check_files(paths):
    """Check files named on the command line, without the event hook's
    silent-skip behavior.

    The event-mode ``check_file`` swallows read errors because a file can
    legitimately vanish mid-edit; in manual mode a path the user explicitly
    named must never produce a false pass. Check order matters: existence
    before the extension filter, so a missing ``missing.txt`` is reported
    as an error rather than misclassified as an unsupported-extension skip.

    Returns (issues, checks_skipped). Never aborts early — every path is
    examined so one bad file does not hide findings in the others.
    """
    issues = []
    skipped = []
    for path in paths:
        if not os.path.exists(path):
            issues.append({
                'file': path,
                'line': 0,
                'severity': 'error',
                'message': 'File not found',
            })
            continue
        if not os.path.isfile(path):
            issues.append({
                'file': path,
                'line': 0,
                'severity': 'error',
                'message': 'Not a regular file (directories are not scanned)',
            })
            continue
        ext = os.path.splitext(path)[1]
        if ext not in CHECKABLE_EXTENSIONS:
            skipped.append(
                f'{path}: unsupported extension {ext or "(none)"} — '
                f'checked extensions: '
                f'{", ".join(sorted(CHECKABLE_EXTENSIONS))}')
            continue
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                content = fh.read()
        except (OSError, UnicodeError) as exc:
            issues.append({
                'file': path,
                'line': 0,
                'severity': 'error',
                'message': f'Cannot read file: {exc}',
            })
            continue
        issues.extend(check_content(content, path))
    return issues, skipped


def _event_mode_issues():
    """Original event-mode checks; returns (issues, tool_name, tool_input,
    debug)."""
    tool_name, tool_input, debug = _read_tool_context()

    issues = []

    # Normalize tool name for resilient matching across runtime variations.
    # This avoids silent bypass if the harness ever changes casing or adds
    # prefixes (e.g. "write" vs "Write", "mcp__server__write", "bash_tool").
    tool_name_lower = tool_name.lower().rstrip('_')
    if tool_name_lower.startswith('mcp__'):
        # MCP tools are named mcp__<server>__<tool>; match on the tool part.
        tool_name_lower = tool_name_lower.split('__')[-1]

    # If the tool is writing or editing a file, validate the content.
    # NOTE: Claude Code uses tool names: Write, Edit, MultiEdit, NotebookEdit.
    if tool_name_lower in (
        'write', 'edit', 'multiedit', 'notebookedit',
        'create_file', 'write_file',
    ) and tool_input:
        filepath = tool_input.get('file_path', '') or tool_input.get('path', '')
        # MultiEdit carries a list of edits — flatten content checks across them.
        if tool_name_lower == 'multiedit':
            content_parts = [
                e.get('new_string', '')
                for e in (tool_input.get('edits') or [])
                if isinstance(e, dict)
            ]
            content = '\n'.join(p for p in content_parts if p)
        else:
            content = (tool_input.get('content', '')
                       or tool_input.get('new_string', ''))
        if content:
            issues = check_content(content, filepath)

    # For shell-style tools, check for dangerous patterns. Includes PowerShell
    # variants so the same regex set protects Windows hosts where the agent's
    # tool name surfaces as `PowerShell`/`pwsh` rather than `Bash`.
    if tool_name_lower in (
        'bash', 'shell', 'command', 'terminal', 'powershell', 'pwsh', 'cmd',
    ) and tool_input:
        command = tool_input.get('command', '')
        if command:
            issues.extend(_check_dangerous_commands(command))

    return issues, tool_name, tool_input, debug


def main(argv=None):
    """Entry point. *argv* defaults to sys.argv[1:]; tests that call main()
    in-process pass an explicit list so the host runner's argv is ignored."""
    parser = argparse.ArgumentParser(
        description=('ROS 2 skill PreToolUse hook. Without arguments it '
                     'consumes a Claude Code hook payload (event mode); '
                     'with --file/--command it validates the named inputs '
                     'directly (manual mode).'))
    parser.add_argument(
        '--file', nargs='+', metavar='PATH', default=None,
        help='check the named source files for ROS 2 anti-patterns')
    parser.add_argument(
        '--command', metavar='CMD', default=None,
        help=('inspect a shell command string for dangerous patterns; '
              'the string is analyzed only and never executed'))
    parser.add_argument(
        '--debug', action='store_true',
        help='include payload-resolution debug info in the JSON result')
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    manual_mode = bool(args.file) or args.command is not None

    checks_skipped = []
    debug = {}
    tool_name = ''
    tool_input = {}

    if manual_mode:
        issues = []
        # Aggregate across ALL inputs — one failing file must not hide
        # findings in the remaining files or in --command.
        if args.file:
            file_issues, checks_skipped = _manual_check_files(args.file)
            issues.extend(file_issues)
        if args.command is not None:
            issues.extend(_check_dangerous_commands(args.command))
    else:
        issues, tool_name, tool_input, debug = _event_mode_issues()

    result = {
        'hook': 'ros2-engineering-skills:pre-tool-use',
        'version': '1.2.0',
        'mode': 'manual' if manual_mode else 'event',
        'issues_count': len(issues),
        'issues': issues,
        'checks_skipped': checks_skipped,
        'status': 'fail' if any(
            i['severity'] == 'error' for i in issues
        ) else 'pass',
    }
    if args.debug and not manual_mode:
        result['debug'] = {**debug, 'tool_name': tool_name,
                           'tool_input_keys': sorted(tool_input.keys())}

    print(json.dumps(result, indent=2))

    has_errors = any(i['severity'] == 'error' for i in issues)
    if not has_errors:
        sys.exit(0)

    if manual_mode:
        # Plain CLI: non-zero means "issues found".
        sys.exit(1)

    # Event mode: exit 2 is the only code Claude Code treats as blocking,
    # and it reads the reason from stderr (stdout is discarded for exit 2).
    # Anything else here would print a refusal and then let the tool run.
    for issue in issues:
        if issue['severity'] == 'error':
            print(f"{issue['file']}: {issue['message']}", file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
