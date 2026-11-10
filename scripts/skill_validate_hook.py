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
* ROS 2 commands that can move hardware or change a motion gate
  (``ros2 topic pub /cmd_vel``, a navigation goal, a controller switch, an
  e-stop reset, a velocity limit) are reported as warnings with
  ``category: actuation``. They never block and are not a safety function:
  the check matches names, reads POSIX shell syntax only, and ignores any
  command that does not contain the literal text ``ros2``. Syntax it cannot
  interpret is listed in ``checks_skipped`` rather than treated as clean.
  ``ROS2_SKILL_ACTUATION=off`` disables it; ``ask`` is handled by the Claude
  adapter (``claude_hook.py``), not here.
* In event mode the anti-pattern checks run only for a file with confirmed
  ROS context (see ``_has_ros_context``); manual ``--file`` checks whatever
  it is given.
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
  the reason is written to **stderr**, which is the channel Claude Code
  reads, and nothing is written to stdout: this module's JSON report is
  its own shape rather than the documented hook-output schema, and pairing
  unrecognized JSON with a blocking exit is the combination that can be
  downgraded to non-blocking.

    0 — No blocking issues found (JSON report on stdout)
    2 — Blocking issue detected; the tool call is refused (reason on stderr)

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
import xml.etree.ElementTree as ET


# Patterns that indicate potential ROS 2 anti-patterns in code being written.
#
# Optional fields per entry:
#   pattern_flags  - extra `re` flags (e.g. re.MULTILINE). Default 0.
#   file_filter    - callable(filename) -> bool; if present, regex only runs
#                    when the filter returns True. Default: applies to all
#                    checkable files. Use to scope launch-only or
#                    ROS-specific patterns to where they are real signals.
#   distro_filter  - callable(ros_distro) -> bool, where ros_distro is the
#                    lower-cased ROS_DISTRO of the environment ('' when
#                    unset); if present, regex only runs when the filter
#                    returns True. Use for advice that is wrong on older
#                    distributions.
#
# `message` is a string, or a callable(ros_distro) -> str for advice whose
# wording depends on whether the distribution is known.

# ROS_AUTOMATIC_DISCOVERY_RANGE arrived in Iron, which also deprecated
# ROS_LOCALHOST_ONLY. On these earlier distributions the deprecated variable is
# the only way to confine discovery, so recommending its replacement there
# would make a follower open the robot's DDS traffic to the network.
_DISTROS_WITHOUT_DISCOVERY_RANGE = frozenset(('foxy', 'galactic', 'humble'))


def _ros_distro():
    return os.environ.get('ROS_DISTRO', '').strip().lower()


ANTIPATTERN_CHECKS = [
    {
        'pattern': r'time\.sleep\s*\(',
        'message': ('time.sleep() inside a callback blocks its executor thread; '
                    'prefer a node timer there. A sleep outside callbacks, such as '
                    'in main() or a test, is not inherently an anti-pattern'),
        'severity': 'warning',
    },
    {
        'pattern': r'spin_until_future_complete\s*\(',
        'message': ('Calling spin_until_future_complete() inside a callback can '
                    'deadlock depending on executor and callback-group '
                    'configuration. Check the execution context; a call from '
                    'main() is not inherently an anti-pattern'),
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
        # Unknown distro: keep warning, but do not prescribe the replacement.
        # An unset ROS_DISTRO is common in editors and CI, and telling a
        # Humble or Foxy project to switch would open its discovery.
        'message': lambda distro: (
            'ROS_LOCALHOST_ONLY is deprecated since Iron. '
            'Use ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST instead'
            if distro else
            'ROS_LOCALHOST_ONLY is deprecated since Iron in favor of '
            'ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST, but on Humble and earlier '
            'it is the only way to confine discovery. ROS_DISTRO is not set: '
            'confirm the target distribution before changing it'),
        'severity': 'warning',
        'distro_filter': lambda distro: distro not in _DISTROS_WITHOUT_DISCOVERY_RANGE,
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


# --- ROS context -----------------------------------------------------------
#
# The plugin hook runs on every source file a session writes, in every
# project. The anti-pattern advice is only true of ROS 2 code, so event mode
# applies it only where ROS context is established by one of:
#
#   1. an unambiguous ROS import or include in the text being written;
#   2. a valid ROS package manifest in the file's directory or above it;
#   3. such an import or include in the first _CONTEXT_HEAD_BYTES of the file
#      already on disk (an Edit fragment rarely repeats the imports).
#
# Anything else is skipped: an unconfirmed context gets no advice rather than
# advice that may be wrong. `from launch import ...` and names starting with
# `ament_` are deliberately not evidence on their own - neither is unique to
# ROS. A launch-named file may additionally use a `launch` import.
_ROS_PY_MARKER = re.compile(
    r'^[ \t]*(?:import|from)[ \t]+'
    r'(?:rclpy|launch_ros|ament_index_python|rcl_interfaces|rosbag2_py|rosidl_runtime_py)\b'
    r'|^[ \t]*from[ \t]+\w+_(?:msgs|interfaces)\.(?:msg|srv|action)[ \t]+import\b',
    re.MULTILINE)
_ROS_CPP_MARKER = re.compile(
    r'^[ \t]*#[ \t]*include[ \t]*[<"]'
    r'(?:(?:rclcpp(?:_lifecycle|_action|_components)?|rclc(?:_lifecycle|_parameter)?'
    r'|rcl|rcutils|rmw|ament_index_cpp)/'
    r'|\w+_(?:msgs|interfaces)/(?:msg|srv|action)/)',
    re.MULTILINE)
_LAUNCH_PY_MARKER = re.compile(
    r'^[ \t]*(?:import|from)[ \t]+launch(?:_ros|_xml|_yaml|_testing)?\b', re.MULTILINE)
_CONTEXT_HEAD_BYTES = 64 * 1024
_MANIFEST_MAX_BYTES = 256 * 1024
_ANCESTOR_LIMIT = 8


def _has_ros_marker(text, filename=''):
    """True when *text* carries an unambiguous ROS 2 import or include."""
    if not text:
        return False
    ext = os.path.splitext(filename)[1] if filename else ''
    if ext == '.py':
        return bool(_ROS_PY_MARKER.search(text)
                    or (_is_launch_file(filename) and _LAUNCH_PY_MARKER.search(text)))
    if ext in _CPP_EXTENSIONS:
        return bool(_ROS_CPP_MARKER.search(text))
    return bool(_ROS_PY_MARKER.search(text) or _ROS_CPP_MARKER.search(text))


def _is_ros_package_manifest(path):
    """True for a package.xml that declares an ament build, not merely the name."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _MANIFEST_MAX_BYTES:
            return False
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError, ValueError, LookupError):
        return False
    if root.tag != 'package':
        return False
    declared = [node.text for tag in ('buildtool_depend', 'build_type')
                for node in root.iter(tag)]
    return any(isinstance(text, str) and text.strip().startswith('ament_')
               for text in declared)


def _ros_package_above(path, stop_dir=''):
    """True when the file sits in, or below, a valid ROS package directory.

    The manifest is looked up on disk from the file's own directory upwards,
    so it is found whether or not this session touched it. The walk ends at
    *stop_dir* when that is an ancestor, and after _ANCESTOR_LIMIT levels
    otherwise.
    """
    current = os.path.dirname(os.path.abspath(path))
    stop = os.path.abspath(stop_dir) if stop_dir else ''
    if stop:
        try:
            if os.path.commonpath([current, stop]) != stop:
                stop = ''
        except ValueError:  # different drives
            stop = ''
    for _ in range(_ANCESTOR_LIMIT):
        if _is_ros_package_manifest(os.path.join(current, 'package.xml')):
            return True
        parent = os.path.dirname(current)
        if current == stop or parent == current:
            break
        current = parent
    return False


def _read_head(path):
    """First _CONTEXT_HEAD_BYTES of an existing regular file, else ''."""
    try:
        if not os.path.isfile(path):
            return ''
        with open(path, 'rb') as handle:
            return handle.read(_CONTEXT_HEAD_BYTES).decode('utf-8', errors='replace')
    except OSError:
        return ''


def _has_ros_context(filepath, content, cwd=''):
    """Decide whether automatic anti-pattern advice applies to this write."""
    if _has_ros_marker(content, filepath):
        return True
    if not filepath:
        return False
    path = filepath
    if cwd and not os.path.isabs(path):
        path = os.path.join(cwd, path)
    if _ros_package_above(path, cwd):
        return True
    return _has_ros_marker(_read_head(path), filepath)


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
    distro = _ros_distro()
    for check in ANTIPATTERN_CHECKS:
        file_filter = check.get('file_filter')
        if file_filter and not file_filter(filename):
            continue
        distro_filter = check.get('distro_filter')
        if distro_filter and not distro_filter(distro):
            continue
        flags = check.get('pattern_flags', 0)
        matches = list(re.finditer(check['pattern'], content, flags))
        for match in matches:
            if _is_in_comment(content, match.start(), markers):
                continue
            line_num = content[:match.start()].count('\n') + 1
            message = check['message']
            issues.append({
                'file': filename,
                'line': line_num,
                'severity': check['severity'],
                'message': message(distro) if callable(message) else message,
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
#
# Two kinds of pattern, marked by `position`:
#   'command'  - the dangerous word is a program name, so it only counts in
#                command position: start of the input or of a line, after a
#                shell separator / grouping character / `$(`, or right after
#                the opening quote of an argument (`bash -c '...'`,
#                `ssh host '...'`), optionally behind variable assignments and
#                wrappers (`sudo -u root`, `env A=1`, `xargs`, `timeout 5`).
#                The same word as a mere argument (`grep mkfs`, `man
#                mkfs.ext4`, a commit message, a sentence in a here-doc) is
#                data, and refusing it with the blocking exit code is a false
#                positive that stops read-only work.
#   'anywhere' - the pattern is a shell operator or a structure (`> /dev/sda`,
#                a fork bomb, `Remove-Item ... -Recurse -Force <root>`) that is
#                dangerous wherever it appears, so it keeps matching anywhere.
# `flags` carries `re` flags; an inline `(?i)` cannot follow the position prefix.
_ASSIGNMENTS = r'(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*'
_WRAPPER = (r'(?:(?:sudo|doas|exec|nohup|time|command|builtin|xargs|env|nice|ionice|stdbuf)'
            r'(?:\s+-\S+(?:\s+[^\s;&|-][^\s;&|]*)?)*'
            r'|timeout(?:\s+-\S+)*\s+\S+)\s+')
_CMD = (r'(?:^|(?<=[\n;&|(){}`])|(?<=\$\()|(?<=[\s=]["\']))\s*' + _ASSIGNMENTS
        + r'(?:' + _WRAPPER + _ASSIGNMENTS + r')*')

DANGEROUS_COMMAND_PATTERNS = [
    # `(?=\s|$|[;&|])` terminates a target path at whitespace, end of input,
    # or a shell separator, so compound commands (`rm -rf /; echo done`,
    # `rm -rf / && true`) are still caught, while longer paths under the
    # same root (`rm -rf /var/lib/apt/lists/*`) are not.
    {
        'pattern': _CMD + r'rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|(-[a-zA-Z]+\s+)*)/(?=\s|$|[;&|])',
        'position': 'command',
        'message': 'Refusing to remove root filesystem (rm -rf /)',
    },
    {
        'pattern': _CMD + r'rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|(-[a-zA-Z]+\s+)*)/\*',
        'position': 'command',
        'message': 'Refusing to remove all files from root (rm -rf /*)',
    },
    {
        'pattern': _CMD + r'rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)/opt/ros\b',
        'position': 'command',
        'message': 'Refusing to remove ROS installation directory',
    },
    {
        # Matches removal of the critical directory itself (optionally with
        # a trailing / or /*), NOT of arbitrary subpaths beneath it.
        'pattern': (_CMD + r'rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)'
                    r'/(usr|bin|sbin|etc|var|boot|lib|lib64)'
                    r'(?:/\*)?/?(?=\s|$|[;&|])'),
        'position': 'command',
        'message': 'Refusing to remove critical system directory',
    },
    {
        'pattern': (_CMD + r'rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*)'
                    r'(~|\$HOME)/?(?=\s|$|[;&|])'),
        'position': 'command',
        'message': 'Refusing to remove home directory',
    },
    {
        'pattern': _CMD + r'mkfs\b',
        'position': 'command',
        'message': 'Refusing to format filesystem (mkfs)',
    },
    {
        'pattern': _CMD + r'dd\s+.*\bof\s*=\s*/dev/(sd|nvme|vd|hd)',
        'position': 'command',
        'message': 'Refusing to write directly to block device (dd)',
    },
    {
        # Only the filesystem root itself (`/` or `/*`), not any absolute path.
        'pattern': _CMD + r'chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)777\s+/\*?(?=\s|$|[;&|])',
        'position': 'command',
        'message': 'Refusing to recursively chmod 777 on root filesystem',
    },
    {
        'pattern': r':\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:',
        'position': 'anywhere',
        'message': 'Refusing to execute fork bomb',
    },
    {
        'pattern': r'>\s*/dev/(sd|nvme|vd|hd)',
        'position': 'anywhere',
        'message': 'Refusing to overwrite block device',
    },
    # PowerShell / Windows equivalents. Maintainer works on Windows and the
    # harness may forward `pwsh`/`powershell` commands under TOOL_NAME=Bash
    # (the agent's logical tool name), so the bash branch can carry PS syntax.
    # Same best-effort caveat as above: these are not a security boundary.
    #
    # Implementation notes:
    #   * `flags: re.IGNORECASE` — PowerShell cmdlets are case-insensitive
    #     (`remove-item` == `Remove-Item`).
    #   * `\b` only attaches to alphanumeric ends; on a hyphen-prefixed flag
    #     like `-Recurse` the leading `\b` would fail (space → `-` is not a
    #     word boundary). Lookaheads avoid that trap and also let `-Recurse`
    #     and `-Force` appear in any order.
    #   * Drive-root regex requires `:` then `/` or `\` then nothing else
    #     meaningful, so `C:/Users/me/build` (a real subdir) does NOT match.
    {
        'pattern': (r'\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*[A-Za-z]:[\\/]\s*(?:["\']?\s*)?(?:[;\n]|$)'),
        'position': 'anywhere',
        'flags': re.IGNORECASE,
        'message': 'Refusing to recursively force-remove a drive root (Remove-Item)',
    },
    {
        'pattern': (r'\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*(?:\$HOME|\$env:USERPROFILE|~)\s*'
                    r'(?:["\']?\s*)?(?:[;\n]|$)'),
        'position': 'anywhere',
        'flags': re.IGNORECASE,
        'message': 'Refusing to recursively force-remove the home directory (Remove-Item)',
    },
    {
        'pattern': (r'\bRemove-Item\b'
                    r'(?=[^;\n]*\s-Recurse\b)'
                    r'(?=[^;\n]*\s-Force\b)'
                    r'[^;\n]*[A-Za-z]:[\\/](?:Windows|Program Files|Program Files \(x86\)|Users)\b'),
        'position': 'anywhere',
        'flags': re.IGNORECASE,
        'message': 'Refusing to recursively force-remove a critical Windows directory',
    },
    {
        'pattern': _CMD + r'Format-Volume\b',
        'position': 'command',
        'flags': re.IGNORECASE,
        'message': 'Refusing to format volume (Format-Volume)',
    },
    {
        'pattern': _CMD + r'Clear-Disk\b',
        'position': 'command',
        'flags': re.IGNORECASE,
        'message': 'Refusing to clear disk (Clear-Disk)',
    },
    {
        'pattern': _CMD + r'Remove-Partition\b',
        'position': 'command',
        'flags': re.IGNORECASE,
        'message': 'Refusing to remove partition (Remove-Partition)',
    },
    {
        'pattern': r'\brmdir\b\s+/s\s+/q\s+[A-Za-z]:[\\/]?\s*$',
        'position': 'anywhere',
        'flags': re.IGNORECASE,
        'message': 'Refusing to silently recursively remove drive root (rmdir /s /q)',
    },
]


def _check_dangerous_commands(command):
    """Check a bash command string for dangerous patterns."""
    issues = []
    for check in DANGEROUS_COMMAND_PATTERNS:
        if re.search(check['pattern'], command, check.get('flags', 0)):
            issues.append({
                'file': '<bash>',
                'line': 0,
                'severity': 'error',
                'message': check['message'],
            })
    return issues


# --- Actuation commands -----------------------------------------------------
#
# A `ros2` command that publishes a velocity, sends a motion goal, switches a
# controller, resets a stop or changes a limit is the moment the skill's
# guidance is about, so the hook says so. These findings are warnings: they
# never block (exit 2 is reserved for the destructive patterns above), and
# they are not a safety function. Names are matched, not behavior.
#
# The destructive patterns deliberately match inside quotes. That is wrong
# here: `grep "ros2 topic pub /cmd_vel" notes.md` moves nothing. So this
# analysis reads the command with a small quote-aware scanner and only judges
# what it can interpret:
#
#   * simple commands split on ; && || | & newlines and grouping;
#   * single quotes are data; double quotes and unquoted text execute `$(...)`
#     and backticks, which are scanned in turn;
#   * leading assignments, whose values may be dynamic, and wrappers (sudo,
#     env, timeout, nice, ...);
#   * strings handed to a shell or a remote shell: `bash -c`, `bash -lc`,
#     `bash --login -c`, `ssh host '...'`, `su -c`, `eval`, `watch`, and the
#     tmux subcommands that type or run a command;
#   * the program run by `docker|podman exec|run` (and compose), `kubectl
#     exec ... --`, `xargs`, and `find -exec`, located from each program's own
#     option table so that `docker exec c echo ros2 ...` stays data;
#   * here-documents: the body is a script when it feeds a shell, and
#     otherwise only its `$(...)` is executed, and only when the delimiter is
#     unquoted.
#
# What it cannot interpret is reported in `checks_skipped`, never as a pass:
# unbalanced quoting, an unterminated here-document, nesting beyond
# _ACTUATION_MAX_DEPTH, a command name that is a variable or substitution
# (which covers `bash -c "$CMD"` and `ssh host "$CMD"`), a dynamic `eval`,
# a shell reading commands from a pipe, a `ros2` target that is not literal
# and matches no known pattern, an option of docker, kubectl, xargs, watch or
# tmux outside the table, and the arguments of parallel, script, flock,
# chroot and screen. An unrelated dynamic command in the same call is
# reported too: an unread command is not assumed to be harmless. A command
# without the literal text `ros2` is not analyzed at all, so `$CMD /cmd_vel`,
# an alias, or a script that publishes is not detected. PowerShell and cmd
# are not parsed.
ACTUATION_ENV = 'ROS2_SKILL_ACTUATION'
ACTUATION_MODES = ('advise', 'ask', 'off')
_ACTUATION_MAX_CHARS = 64 * 1024
_ACTUATION_MAX_DEPTH = 3
_POSIX_TOOLS = frozenset(('bash', 'shell', 'command', 'terminal'))
_SHELLS = frozenset(('bash', 'sh', 'zsh', 'dash', 'ksh'))
_RESERVED = frozenset(('if', 'then', 'elif', 'else', 'fi', 'do', 'done', 'while',
                       'until', '{', '}', '!'))
_ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\+?=')
_VARIABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-]')
# Options that consume the following word, per wrapper; `positionals` are
# dropped after the options (the duration of `timeout`, the mask of taskset).
_WRAPPERS = {
    'sudo': (('-u', '-g', '-h', '-p', '-C', '-D', '-R', '-T', '-U', '-r', '-t',
              '--user', '--group', '--host', '--prompt', '--chdir', '--role', '--type'), 0),
    'doas': (('-u', '-C'), 0),
    'env': (('-u', '--unset', '-C', '--chdir', '-S', '--split-string'), 0),
    'nice': (('-n', '--adjustment'), 0),
    'ionice': (('-c', '-n', '--class', '--classdata'), 0),
    'timeout': (('-s', '--signal', '-k', '--kill-after'), 1),
    'stdbuf': (('-i', '-o', '-e', '--input', '--output', '--error'), 0),
    'chrt': ((), 1),
    'taskset': ((), 1),
    'nohup': ((), 0), 'setsid': ((), 0), 'time': ((), 0),
    'exec': (('-a',), 0), 'command': ((), 0), 'builtin': ((), 0),
}
_SSH_VALUE_OPTIONS = frozenset(
    '-' + letter for letter in 'BbcDEeFIiJLlmOopQRSWw')
# Programs that run another program given as arguments. The executed argv is
# located from each program's own option table: (options that take a value,
# flags, letters that may be bundled as `-it`). An option outside the table
# is never guessed at - see _skip_options.
_CONTAINER_TOOLS = frozenset(('docker', 'podman', 'nerdctl', 'docker-compose'))
_DOCKER_GLOBAL = (('--context', '-c', '-H', '--host', '--config', '-l', '--log-level'),
                  ('-D', '--debug', '--tls', '--tlsverify'), '')
_COMPOSE_GLOBAL = (('-f', '--file', '-p', '--project-name', '--profile', '--env-file',
                    '--project-directory', '--ansi', '--progress', '--parallel'),
                   ('--compatibility', '--dry-run'), '')
_CONTAINER_VERBS = {
    'exec': (('-e', '--env', '--env-file', '-u', '--user', '-w', '--workdir',
              '--detach-keys'),
             ('-i', '-t', '-d', '--interactive', '--tty', '--detach', '--privileged'), 'itd'),
    'run': (('-e', '--env', '--env-file', '-v', '--volume', '--mount', '-p', '--publish',
             '-w', '--workdir', '-u', '--user', '--name', '--network', '--net', '-h',
             '--hostname', '-l', '--label', '--gpus', '--ipc', '--pid', '--cap-add',
             '--cap-drop', '--device', '--add-host', '--restart', '--runtime',
             '--shm-size', '-m', '--memory', '--cpus', '--ulimit', '--security-opt',
             '--platform', '--pull', '--entrypoint'),
            ('--rm', '-i', '-t', '-d', '--interactive', '--tty', '--detach',
             '--privileged', '--init', '--read-only', '-P', '--publish-all'), 'itdP'),
    'compose exec': (('-e', '--env', '-u', '--user', '-w', '--workdir', '--index'),
                     ('-T', '--no-TTY', '-d', '--detach', '--privileged'), 'Td'),
    'compose run': (('-e', '--env', '-u', '--user', '-w', '--workdir', '-v', '--volume',
                     '-p', '--publish', '--name', '--entrypoint', '-l', '--label',
                     '--cap-add', '--cap-drop', '--pull'),
                    ('--rm', '-T', '--no-TTY', '-d', '--detach', '--no-deps',
                     '--service-ports', '--use-aliases', '--build', '--quiet-pull',
                     '-i', '--interactive', '-t', '--tty'), 'Tdit'),
}
_XARGS_OPTIONS = (('-I', '-n', '-P', '-L', '-d', '-E', '-s', '-a', '--max-args',
                   '--max-procs', '--max-lines', '--delimiter', '--arg-file',
                   '--max-chars'),
                  ('-0', '--null', '-r', '--no-run-if-empty', '-t', '--verbose', '-p',
                   '--interactive', '-x', '--exit', '-o', '--open-tty', '--replace'),
                  '0rtpxo')
_WATCH_OPTIONS = (('-n', '--interval'),
                  ('-d', '--differences', '-t', '--no-title', '-b', '--beep', '-e',
                   '--errexit', '-g', '--chgexit', '-c', '--color', '-x', '--exec',
                   '-p', '--precise', '-w', '--no-wrap'), 'dtbegcxpw')
_TMUX_GLOBAL = (('-L', '-S', '-f', '-c', '-T'), ('-2', '-u', '-v', '-C', '-D', '-l', '-N'),
                '2uvCDlN')
# tmux subcommands whose arguments are typed into, or run by, a shell.
_TMUX_COMMAND_VERBS = frozenset((
    'send-keys', 'send', 'new-session', 'new', 'new-window', 'neww', 'split-window',
    'splitw', 'respawn-pane', 'respawnp', 'respawn-window', 'respawnw', 'run-shell',
    'run', 'display-popup', 'popup'))
_TMUX_VALUE_OPTIONS = frozenset(('-t', '-s', '-n', '-c', '-e', '-F', '-x', '-y'))
_FIND_EXEC = frozenset(('-exec', '-execdir', '-ok', '-okdir'))
_LAUNCHERS = _CONTAINER_TOOLS | frozenset(('kubectl', 'xargs', 'watch', 'find', 'tmux'))
# Programs that also run their arguments but are not interpreted here.
_UNREAD_LAUNCHERS = frozenset(('parallel', 'script', 'flock', 'chroot', 'screen'))
_TUTORIAL_PACKAGES = frozenset(('example_interfaces', 'action_tutorials_interfaces',
                                'turtlesim', 'test_msgs'))
_TURTLE_SEGMENT = re.compile(r'turtle\d*\Z')
_MOTION_TOPIC = re.compile(
    r'(?:cmd_vel\w*|\w+_cmd_vel|cmd_ackermann|ackermann_cmd\w*|joint_trajectory'
    r'|joint_commands?|joint_jog|(?:position|velocity|effort)_commands?'
    r'|delta_twist_cmds|delta_joint_cmds)\Z')
_MOTION_MESSAGES = frozenset(('Twist', 'TwistStamped', 'JointTrajectory',
                              'MultiDOFJointTrajectory', 'AckermannDrive',
                              'AckermannDriveStamped', 'JointJog'))
_MOTION_ACTIONS = frozenset((
    'NavigateToPose', 'NavigateThroughPoses', 'FollowPath', 'FollowWaypoints',
    'FollowGPSWaypoints', 'Spin', 'BackUp', 'DriveOnHeading', 'AssistedTeleop',
    'FollowJointTrajectory', 'MoveGroup', 'MoveGroupSequence', 'ExecuteTrajectory',
    'GripperCommand', 'ParallelGripperCommand', 'DockRobot', 'UndockRobot'))
_MOTION_ACTION_NAME = re.compile(
    r'(?:navigate_to_pose|navigate_through_poses|follow_path|follow_waypoints|spin'
    r'|backup|drive_on_heading|follow_joint_trajectory|move_action|execute_trajectory'
    r'|gripper_cmd|gripper_action|dock_robot|undock_robot)\Z')
_STOP_PATH_SERVICE = re.compile(
    r'e_?stop|emergency|clear_faults?|clear_errors?|reset_faults?|reset_errors?'
    r'|brake|motor|torque|servo|actuator|arming|disarm|takeoff'
    r'|(?<![a-z])arm(?![a-z])|(?<![a-z])land(?![a-z])')
_CONTROLLER_SERVICE = re.compile(
    r'(?:switch_controller|load_controller|unload_controller|configure_controller'
    r'|set_hardware_component_state)\Z')
_GATE_PARAMETER = re.compile(
    r'(?:max|min)[_.]?\w*(?:vel|speed|acc|decel|jerk|effort|torque|current)'
    r'|limit|timeout|watchdog|deadman|safety|e_?stop|collision', re.IGNORECASE)
_TOPIC_PUB_VALUE_OPTIONS = frozenset((
    '-r', '--rate', '-p', '--print', '-t', '--times', '-w',
    '--wait-matching-subscriptions', '--keep-alive', '-n', '--node-name',
    '--max-wait-time-secs', '--yaml-file', '--spin-time'))
_SEND_GOAL_VALUE_OPTIONS = frozenset(('-t', '--timeout', '--spin-time'))
_SERVICE_CALL_VALUE_OPTIONS = frozenset(('-r', '--rate', '--timeout', '--spin-time'))
_CONTROL_VALUE_OPTIONS = frozenset((
    '-c', '--controller-manager', '--activate', '--deactivate', '--start', '--stop',
    '--set-state', '--switch-timeout', '--spin-time', '--param-file', '-p'))
_ACTUATION_ADVICE = (
    ' Not blocked. Confirm explicit authorization, an operator, and an independent '
    'stop path before running it; see references/safety-estop.md and '
    'references/evidence-progression.md section 2')


class _Unparsed(Exception):
    """The scanner met shell syntax it does not interpret."""


class _Analysis:
    def __init__(self):
        self.commands = []  # (words, here-documents, depth); a word is (text, dynamic)
        self.issues = []
        self.reasons = []

    def skip(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)

    def add(self, confidence, what):
        message = (('Actuation command: ' if confidence == 'high' else 'Possible actuation: ')
                   + what[:200] + ' can move hardware or change a motion gate.'
                   + _ACTUATION_ADVICE)
        if not any(issue['message'] == message for issue in self.issues):
            self.issues.append({'file': '<bash>', 'line': 0, 'severity': 'warning',
                                'message': message, 'category': 'actuation',
                                'confidence': confidence})


def _balanced(text, start):
    """Index just past the parenthesis matching the one at *start*."""
    depth, i, n = 0, start, len(text)
    while i < n:
        char = text[i]
        if char == '\\':
            i += 2
            continue
        if char == "'":
            end = text.find("'", i + 1)
            if end < 0:
                break
            i = end + 1
            continue
        if char == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    i += 1
                elif text.startswith('$(', i):
                    i = _balanced(text, i + 1) - 1
                i += 1
            if i >= n:
                break
            i += 1
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise _Unparsed('unbalanced quote or parenthesis')


def _scan_commands(text, depth, out):
    """Split shell text into simple commands, keeping what quoting meant."""
    if depth > _ACTUATION_MAX_DEPTH:
        out.skip('command nesting is deeper than the analysis follows')
        return
    n = len(text)
    words, heredocs, pending = [], [], []
    state = {'buf': [], 'word': False, 'dynamic': False, 'skip': False,
             'delimiter': None, 'quoted': False}

    def literal(chunk):
        state['buf'].append(chunk)
        state['word'] = True

    def end_word():
        if not state['word']:
            return
        value = ''.join(state['buf'])
        if state['delimiter'] is not None:
            doc = {'delimiter': value, 'strip_tabs': state['delimiter'],
                   'quoted': state['quoted'], 'body': None}
            heredocs.append(doc)
            pending.append(doc)
            state['delimiter'] = None
        elif state['skip']:
            state['skip'] = False
        else:
            words.append((value, state['dynamic']))
        state.update(buf=[], word=False, dynamic=False, quoted=False)

    def end_command():
        nonlocal words, heredocs
        end_word()
        if words or heredocs:
            out.commands.append((words, heredocs, depth))
        words, heredocs = [], []
        state.update(skip=False, delimiter=None)

    def substitution(start, placeholder):
        """`$(`, `<(` or `>(` at *start*: its commands run; return the next index."""
        end = _balanced(text, start + 1)
        _scan_commands(text[start + 2:end - 1], depth + 1, out)
        literal(placeholder)
        state['dynamic'] = True
        return end

    def dollar(i, in_double):
        if text.startswith('$((', i):
            end = _balanced(text, i + 1)
            literal(text[i:end])
            state['dynamic'] = True
            return end
        if text.startswith('$(', i):
            return substitution(i, '$()')
        if text.startswith('${', i):
            end = text.find('}', i)
            if end < 0:
                raise _Unparsed('unbalanced parameter expansion')
            literal(text[i:end + 1])
            state['dynamic'] = True
            return end + 1
        if not in_double and text.startswith("$'", i):
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == '\\' else 1
            if j >= n:
                raise _Unparsed('unbalanced quote')
            literal(text[i + 2:j])
            return j + 1
        match = _VARIABLE.match(text, i + 1)
        if not match:
            literal('$')
            return i + 1
        literal(text[i:match.end()])
        state['dynamic'] = True
        return match.end()

    def backtick(i):
        j, inner = i + 1, []
        while j < n and text[j] != '`':
            if text[j] == '\\' and j + 1 < n:
                j += 1
            inner.append(text[j])
            j += 1
        if j >= n:
            raise _Unparsed('unbalanced backtick')
        _scan_commands(''.join(inner), depth + 1, out)
        literal('$()')
        state['dynamic'] = True
        return j + 1

    def heredoc_bodies(i):
        for doc in pending:
            lines = []
            while True:
                if i >= n:
                    out.skip('a here-document is not terminated')
                    del pending[:]
                    return n
                end = text.find('\n', i)
                end = n if end < 0 else end
                line = text[i:end]
                i = min(end + 1, n)
                if (line.lstrip('\t') if doc['strip_tabs'] else line) == doc['delimiter']:
                    break
                lines.append(line)
            doc['body'] = '\n'.join(lines)
        del pending[:]
        return i

    i = 0
    while i < n:
        char = text[i]
        if char == '\\':
            if text.startswith('\\\n', i):
                i += 2
                continue
            literal(text[i + 1:i + 2])
            if state['delimiter'] is not None:
                state['quoted'] = True
            i += 2
        elif char == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise _Unparsed('unbalanced quote')
            literal(text[i + 1:end])
            if state['delimiter'] is not None:
                state['quoted'] = True
            i = end + 1
        elif char == '"':
            literal('')
            if state['delimiter'] is not None:
                state['quoted'] = True
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n:
                    if text[i + 1] == '\n':
                        i += 2
                    elif text[i + 1] in '"\\$`':
                        literal(text[i + 1])
                        i += 2
                    else:
                        literal('\\')
                        i += 1
                elif text[i] == '$':
                    i = dollar(i, True)
                elif text[i] == '`':
                    i = backtick(i)
                else:
                    literal(text[i])
                    i += 1
            if i >= n:
                raise _Unparsed('unbalanced quote')
            i += 1
        elif char == '$':
            i = dollar(i, False)
        elif char == '`':
            i = backtick(i)
        elif char in ' \t\r':
            end_word()
            i += 1
        elif char == '\n':
            end_command()
            i = heredoc_bodies(i + 1) if pending else i + 1
        elif char == ';':
            end_command()
            i += 1
        elif char == '&':
            if text.startswith('&&', i):
                end_command()
                i += 2
            elif text.startswith('&>', i):
                end_word()
                i += 3 if text.startswith('&>>', i) else 2
                state['skip'] = True
            else:
                end_command()
                i += 1
        elif char == '|':
            end_command()
            i += 2 if text[i + 1:i + 2] in ('|', '&') else 1
        elif char in '()':
            end_command()
            i += 1
        elif char in '<>':
            if text.startswith(char + '(', i):
                i = substitution(i, char + '()')
                continue
            if state['word'] and ''.join(state['buf']).isdigit() and not state['dynamic']:
                state.update(buf=[], word=False)  # a file descriptor, not a word
            else:
                end_word()
            if text.startswith('<<<', i):
                i += 3
                state['skip'] = True
            elif text.startswith('<<', i):
                i += 2
                strip = text[i:i + 1] == '-'
                i += 1 if strip else 0
                state['delimiter'] = strip
            else:
                i += 1
                if text[i:i + 1] in ('>', '|'):
                    i += 1
                if text[i:i + 1] == '&':
                    i += 1
                state['skip'] = True
        elif char == '#' and not state['word']:
            end = text.find('\n', i)
            i = n if end < 0 else end
        else:
            literal(char)
            i += 1
    end_command()
    if pending:
        out.skip('a here-document is not terminated')


def _scan_heredoc_substitutions(body, depth, out):
    """An unquoted here-document expands `$(...)` and backticks before use."""
    i, n = 0, len(body)
    while i < n:
        if body[i] == '\\':
            i += 2
        elif body.startswith('$((', i):
            i = _balanced(body, i + 1)
        elif body.startswith('$(', i):
            end = _balanced(body, i + 1)
            _scan_commands(body[i + 2:end - 1], depth + 1, out)
            i = end
        elif body[i] == '`':
            end = body.find('`', i + 1)
            if end < 0:
                raise _Unparsed('unbalanced backtick')
            _scan_commands(body[i + 1:end], depth + 1, out)
            i = end + 1
        else:
            i += 1


def _strip_wrapper(name, rest):
    """Words after a wrapper's own options: `sudo -u $USER`, `timeout $T`."""
    value_options, positionals = _WRAPPERS[name]
    i = 0
    while i < len(rest):
        text = rest[i][0]
        if text == '--':
            i += 1
            break
        if name == 'env' and _ASSIGNMENT.match(text):
            i += 1
        elif text.startswith('-') and len(text) > 1:
            # A value is consumed whether or not it is literal.
            i += 2 if text in value_options else 1
        else:
            break
    return rest[i + positionals:]


def _skip_options(words, value_options, flags, bundle=''):
    """Index of the first word that is not an option, or None.

    None means an option outside the table was met. Whether it takes a value
    cannot be known, so the position of the executed program is unknown too;
    the caller reports that instead of assuming either answer.
    """
    i = 0
    while i < len(words):
        text, dynamic = words[i]
        if dynamic or not text.startswith('-') or len(text) == 1:
            return i
        if text == '--':
            return i + 1
        if text in value_options:
            i += 2
        elif (text in flags or (text.startswith('--') and '=' in text)
              or (not text.startswith('--') and text[:2] in value_options)  # -eA=1
              or (bundle and re.fullmatch('-[' + bundle + ']+', text))):
            i += 1
        else:
            return None
    return i


def _mentions_ros2(words):
    return any(not dynamic and ('ros2' in text or os.path.basename(text) in _SHELLS)
               for text, dynamic in words)


def _launched_argv(name, rest):
    """The argv a launcher runs: a list (possibly empty), or None when unknown."""
    if name in _CONTAINER_TOOLS:
        verb, args = '', rest
        if name != 'docker-compose':
            i = _skip_options(rest, *_DOCKER_GLOBAL)
            if i is None:
                return None
            if i >= len(rest) or rest[i][1]:
                return []
            verb, args = rest[i][0], rest[i + 1:]
        if name == 'docker-compose' or verb == 'compose':
            i = _skip_options(args, *_COMPOSE_GLOBAL)
            if i is None:
                return None
            if i >= len(args) or args[i][1]:
                return []
            verb, args = 'compose ' + args[i][0], args[i + 1:]
        if verb not in _CONTAINER_VERBS:
            return []  # logs, ps, build, ...: nothing is executed
        i = _skip_options(args, *_CONTAINER_VERBS[verb])
        if i is None:
            return None
        argv = args[i + 1:]  # after the container, image or service name
        options = [text for text, _dynamic in args[:i]]
        for index, text in enumerate(options):
            # The image arguments become the arguments of the entry point.
            if text == '--entrypoint' and index + 1 < i:
                return [args[index + 1]] + argv
            if text.startswith('--entrypoint='):
                return [(text.split('=', 1)[1], args[index][1])] + argv
        return argv
    if name == 'kubectl':
        texts = [text for text, dynamic in rest if not dynamic]
        if 'exec' not in texts:
            return []
        for index, (text, dynamic) in enumerate(rest):
            if text == '--' and not dynamic:
                return rest[index + 1:]
        return None  # `kubectl exec pod cmd` without `--`: the split is ambiguous
    if name == 'xargs':
        i = _skip_options(rest, *_XARGS_OPTIONS)
        return None if i is None else rest[i:]
    return []


def _inspect_launcher(name, rest, heredocs, depth, out):
    """A program that runs other programs; True when here-documents were consumed."""
    if name == 'find':
        for index, (text, dynamic) in enumerate(rest):
            if not dynamic and text in _FIND_EXEC:
                argv = []
                for word in rest[index + 1:]:
                    if not word[1] and word[0] in (';', '+'):
                        break
                    argv.append(word)
                _inspect_command(argv, [], depth, out)
        return False
    if name == 'watch':
        i = _skip_options(rest, *_WATCH_OPTIONS)
        if i is None:
            if _mentions_ros2(rest):
                out.skip('an unrecognized watch option hides the command position')
        elif i < len(rest):
            # watch hands its arguments, joined, to `sh -c`.
            _scan_commands(' '.join(text for text, _dynamic in rest[i:]), depth + 1, out)
        return False
    if name == 'tmux':
        i = _skip_options(rest, *_TMUX_GLOBAL)
        if i is None:
            if _mentions_ros2(rest):
                out.skip('an unrecognized tmux option hides the command position')
            return False
        if i >= len(rest) or rest[i][1] or rest[i][0] not in _TMUX_COMMAND_VERBS:
            return False
        args, index = rest[i + 1:], 0
        while index < len(args):
            text, dynamic = args[index]
            if not dynamic and text in _TMUX_VALUE_OPTIONS:
                index += 2  # `-t "$PANE"`: a target, not a command
                continue
            if dynamic:
                out.skip('a command string built at run time is not inspected')
            elif text == 'ros2':
                _scan_commands(' '.join(word for word, _dynamic in args[index:]),
                               depth + 1, out)
                break
            elif 'ros2' in text:
                _scan_commands(text, depth + 1, out)
            index += 1
        return False
    argv = _launched_argv(name, rest)
    if argv is None:
        if _mentions_ros2(rest):
            out.skip('an unrecognized ' + name + ' option hides the command position')
        return False
    return _inspect_command(argv, heredocs, depth, out)


def _positionals(args, value_options):
    """Positional words of a ros2 verb, skipping options and their values."""
    result, i = [], 0
    while i < len(args):
        text, dynamic = args[i]
        if not dynamic and text.startswith('-') and len(text) > 1:
            takes_value = '=' not in text and (
                text in value_options or text.startswith('--qos-'))
            i += 2 if takes_value else 1
            continue
        result.append(args[i])
        i += 1
    return result


def _segments(name):
    return [part for part in name.lower().strip('/').split('/') if part]


def _interface(type_name):
    """('pkg', 'Name') from pkg/msg/Name, pkg/Name or a bare Name."""
    parts = [part for part in type_name.split('/') if part]
    return (parts[0] if len(parts) > 1 else '', parts[-1] if parts else '')


def _is_tutorial(name, type_name):
    return (_interface(type_name)[0] in _TUTORIAL_PACKAGES
            or any(_TURTLE_SEGMENT.match(part) for part in _segments(name)))


def _classify_ros2(args, out):
    """Judge one `ros2 ...` invocation; *args* excludes the program name."""
    if len(args) < 2:
        return
    if args[0][1] or args[1][1]:
        out.skip('ros2 verb is not literal')
        return
    verb, sub, rest = args[0][0], args[1][0], args[2:]

    def named(words, count):
        """(texts, any dynamic) of the first *count* positionals; missing ones are ''."""
        chosen = list(words[:count])
        return ([text for text, _dynamic in chosen] + [''] * (count - len(chosen)),
                any(dynamic for _text, dynamic in chosen))

    # In every branch the literal part is classified first: `/$NS/cmd_vel` ends
    # in a motion topic whatever the namespace expands to. Only when nothing
    # matches and a name is not literal is the command reported as unread.
    if (verb, sub) == ('topic', 'pub'):
        (topic, type_name), dynamic = named(_positionals(rest, _TOPIC_PUB_VALUE_OPTIONS), 2)
        if _is_tutorial(topic, type_name):
            return
        parts = _segments(topic)
        controller_command = (len(parts) >= 2 and parts[-2].endswith('controller')
                              and parts[-1] in ('commands', 'command', 'reference'))
        if (controller_command or (parts and _MOTION_TOPIC.match(parts[-1]))
                or _interface(type_name)[1] in _MOTION_MESSAGES):
            out.add('high', '`ros2 topic pub` to ' + (topic or 'a ' + type_name + ' topic'))
        elif dynamic:
            # A harmless-looking type does not settle it: controller command
            # topics carry plain std_msgs arrays.
            out.skip('ros2 topic pub target is not literal')
    elif (verb, sub) == ('action', 'send_goal'):
        (name, type_name), dynamic = named(_positionals(rest, _SEND_GOAL_VALUE_OPTIONS), 2)
        if _is_tutorial(name, type_name):
            return
        parts = _segments(name)
        if (_interface(type_name)[1] in _MOTION_ACTIONS
                or (parts and _MOTION_ACTION_NAME.match(parts[-1]))):
            out.add('high', '`ros2 action send_goal` to ' + (name or type_name))
        elif dynamic:
            out.skip('ros2 action send_goal target is not literal')
        else:
            out.add('low', '`ros2 action send_goal` to ' + (name or 'an action')
                    + ' may command motion, and if it does it')
    elif (verb, sub) == ('service', 'call'):
        (name, type_name), dynamic = named(_positionals(rest, _SERVICE_CALL_VALUE_OPTIONS), 2)
        if _is_tutorial(name, type_name):
            return
        parts = _segments(name)
        if (_STOP_PATH_SERVICE.search(name.lower())
                or (parts and _CONTROLLER_SERVICE.match(parts[-1]))):
            out.add('high', '`ros2 service call` to ' + name)
        elif dynamic:
            out.skip('ros2 service call target is not literal')
    elif verb == 'control':
        if sub in ('switch_controllers', 'set_controller_state',
                   'set_hardware_component_state'):
            out.add('high', '`ros2 control ' + sub + '`')
        elif sub == 'load_controller':
            texts = [text for text, _dynamic in rest]
            if any(text in ('--set-state=active', '--set-state=start') for text in texts) or any(
                    text == '--set-state' and following in ('active', 'start')
                    for text, following in zip(texts, texts[1:])):
                out.add('high', '`ros2 control load_controller --set-state active`')
    elif (verb, sub) == ('lifecycle', 'set'):
        (node, transition), dynamic = named(
            _positionals(rest, frozenset(('--spin-time',))), 2)
        if dynamic:
            out.skip('ros2 lifecycle set target is not literal')
        elif transition == 'activate':
            out.add('low', '`ros2 lifecycle set ' + node + ' activate` may enable '
                    'a driver or controller, and if it does it')
    elif (verb, sub) == ('param', 'set'):
        (node, parameter), dynamic = named(
            _positionals(rest, frozenset(('--spin-time',))), 2)
        if _GATE_PARAMETER.search(parameter):
            out.add('high', '`ros2 param set` of ' + parameter + ' on ' + node)
        elif dynamic:
            out.skip('ros2 param set target is not literal')
    elif verb == 'run':
        words = [sub] + [text for text, dynamic in rest[:1] if not dynamic]
        if any('teleop' in word.lower() for word in words):
            out.add('high', '`ros2 run` of a teleoperation node')


def _inspect_shell(rest, heredocs, depth, out):
    """A shell invocation; True when its here-documents were read as scripts."""
    i, saw_c, reads_stdin = 0, False, False
    while i < len(rest):
        text, dynamic = rest[i]
        if dynamic:
            break
        if text == '--':
            i += 1
            break
        if text.startswith('--'):
            i += 2 if text in ('--rcfile', '--init-file') else 1
        elif text in ('-o', '+o', '-O', '+O'):
            i += 2
        elif re.fullmatch(r'[-+][A-Za-z]+', text):
            if text[0] == '-':
                saw_c = saw_c or 'c' in text
                reads_stdin = reads_stdin or 's' in text
            i += 1
        else:
            break
    if saw_c:
        if i < len(rest):
            _scan_commands(rest[i][0], depth + 1, out)
        return False
    if i < len(rest) and not reads_stdin:
        return False  # a script file: its contents are not visible here
    bodies = [doc['body'] for doc in heredocs if doc['body'] is not None]
    for body in bodies:
        _scan_commands(body, depth + 1, out)
    if not bodies:
        out.skip('commands fed to a shell on standard input are not inspected')
    return True


def _inspect_command(words, heredocs, depth, out):
    """Judge one simple command; True when here-documents were consumed."""
    args = list(words)
    while args:
        text, dynamic = args[0]
        if _ASSIGNMENT.match(text):
            # `ROS_DOMAIN_ID=$DOMAIN ros2 ...`: the value may be dynamic, the
            # program that runs is still the next word.
            args.pop(0)
        elif dynamic:
            break
        elif text in _RESERVED:
            args.pop(0)
        elif os.path.basename(text) in _WRAPPERS:
            args = _strip_wrapper(os.path.basename(text), args[1:])
        else:
            break
    if not args:
        return False
    head, dynamic = args[0]
    rest = args[1:]
    name = os.path.basename(head)
    if dynamic and ('$' in name or not name):
        # What runs is decided at run time. `$HOME/bin/ros2` is still ros2;
        # `$CMD`, `$(which tool)` and `"$PREFIX"` are not known.
        out.skip('the command name is a variable or substitution')
        return False
    if name == 'ros2':
        _classify_ros2(rest, out)
    elif name in _SHELLS:
        return _inspect_shell(rest, heredocs, depth, out)
    elif name == 'ssh':
        i = 0
        while i < len(rest) and not rest[i][1] and rest[i][0].startswith('-'):
            i += 2 if rest[i][0] in _SSH_VALUE_OPTIONS else 1
        remote = rest[i + 1:]
        if remote:
            _scan_commands(' '.join(text for text, _dynamic in remote), depth + 1, out)
        else:
            for doc in heredocs:
                if doc['body'] is not None:
                    _scan_commands(doc['body'], depth + 1, out)
            return True
    elif name == 'su':
        for index, (text, _dynamic) in enumerate(rest[:-1]):
            if text in ('-c', '--command'):
                _scan_commands(rest[index + 1][0], depth + 1, out)
    elif name == 'eval':
        # The literal part is judged. A dynamic part can expand to separators
        # and further commands, so the result is also marked as incomplete.
        _scan_commands(' '.join(text for text, _dynamic in rest), depth + 1, out)
        if any(dyn for _text, dyn in rest):
            out.skip('eval builds its command at run time')
    elif name in _LAUNCHERS:
        return _inspect_launcher(name, rest, heredocs, depth, out)
    elif name in _UNREAD_LAUNCHERS and _mentions_ros2(rest):
        out.skip('arguments of ' + name + ' are not inspected')
    return False


def analyze_actuation(command, tool_name='bash'):
    """Classify what a shell command would do to a robot.

    Returns {'status', 'confidence', 'reasons', 'issues'}. status is 'detected'
    when at least one finding exists, 'not_evaluated' when the command names
    ros2 but could not be judged, 'off' when disabled, otherwise 'none'.
    """
    result = {'status': 'none', 'confidence': None, 'reasons': [], 'issues': []}
    if os.environ.get(ACTUATION_ENV, '').strip().lower() == 'off':
        result['status'] = 'off'
        return result
    if not isinstance(command, str) or 'ros2' not in command:
        return result
    out = _Analysis()
    family = tool_name.lower().rstrip('_').split('__')[-1]
    if family not in _POSIX_TOOLS:
        out.skip('only POSIX shell commands are analyzed, not ' + (family or 'this tool'))
    elif len(command) > _ACTUATION_MAX_CHARS:
        out.skip('the command is longer than the analysis reads')
    else:
        try:
            _scan_commands(command, 0, out)
            index = 0
            while index < len(out.commands):
                words, heredocs, depth = out.commands[index]
                index += 1
                if not _inspect_command(words, heredocs, depth, out):
                    for doc in heredocs:
                        if doc['body'] is not None and not doc['quoted']:
                            _scan_heredoc_substitutions(doc['body'], depth, out)
        except _Unparsed as exc:
            out.skip(str(exc))
        except RecursionError:
            out.skip('command nesting is deeper than the analysis follows')
    result['issues'] = out.issues
    result['reasons'] = out.reasons
    if out.issues:
        result['status'] = 'detected'
        result['confidence'] = ('high' if any(
            issue['confidence'] == 'high' for issue in out.issues) else 'low')
    elif out.reasons:
        result['status'] = 'not_evaluated'
    return result


def _actuation_report(analysis):
    """The part of an analysis that goes into the JSON report."""
    return {key: analysis[key] for key in ('status', 'confidence', 'reasons')}


def _read_hook_payload():
    """Resolve (tool_name, tool_input_dict, cwd, debug_info) from the harness.

    Stdin is consumed here, once. A caller that also needs `cwd` must use this
    function instead of calling _read_tool_context() a second time.

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
                    cwd = payload.get('cwd')
                    if not isinstance(cwd, str) or not cwd.strip():
                        cwd = ''
                    return tool_name, tool_input, cwd, debug
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
    return tool_name, tool_input, '', debug


def _read_tool_context():
    """(tool_name, tool_input_dict, debug_info): _read_hook_payload() without cwd."""
    tool_name, tool_input, _cwd, debug = _read_hook_payload()
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
    """Event-mode checks; returns (issues, tool_name, tool_input, debug,
    actuation analysis)."""
    tool_name, tool_input, cwd, debug = _read_hook_payload()

    issues = []
    actuation = analyze_actuation('')

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
        # Same file types as check_file() and manual --file: these are
        # source-code patterns. Prose (README.md, notes.txt), shell scripts and
        # parameter files that mention time.sleep() or ROS_LOCALHOST_ONLY are
        # not ROS 2 nodes. Unlike manual --file, where the user named the
        # file, this runs unasked in every project, so it also requires
        # confirmed ROS context; a payload without a path needs it in its text.
        if not isinstance(filepath, str):
            filepath = ''
        ext = os.path.splitext(filepath)[1]
        if (content and (not filepath or ext in CHECKABLE_EXTENSIONS)
                and _has_ros_context(filepath, content, cwd)):
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
            # The scanner reads POSIX shell syntax only; for the PowerShell
            # and cmd aliases it reports the command as not evaluated.
            actuation = analyze_actuation(command, tool_name_lower)
            issues.extend(actuation['issues'])

    return issues, tool_name, tool_input, debug, actuation


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
        help=('inspect a shell command string for dangerous patterns and for '
              'ROS 2 commands that can move hardware; the string is analyzed '
              'only and never executed'))
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
        actuation = analyze_actuation('')
        if args.command is not None:
            issues.extend(_check_dangerous_commands(args.command))
            # Same analysis as event mode, so a manual check and the hook
            # agree on what a command would do.
            actuation = analyze_actuation(args.command)
            issues.extend(actuation['issues'])
    else:
        issues, tool_name, tool_input, debug, actuation = _event_mode_issues()

    # A command that names ros2 but could not be judged is not a clean pass.
    checks_skipped = list(checks_skipped) + [
        'actuation: ' + reason for reason in actuation['reasons']]

    result = {
        'hook': 'ros2-engineering-skills:pre-tool-use',
        'version': '1.5.0',
        'mode': 'manual' if manual_mode else 'event',
        'issues_count': len(issues),
        'issues': issues,
        'checks_skipped': checks_skipped,
        'actuation': _actuation_report(actuation),
        'status': 'fail' if any(
            i['severity'] == 'error' for i in issues
        ) else 'pass',
    }
    if args.debug and not manual_mode:
        result['debug'] = {**debug, 'tool_name': tool_name,
                           'tool_input_keys': sorted(tool_input.keys())}

    has_errors = any(i['severity'] == 'error' for i in issues)

    if has_errors and not manual_mode:
        # Event mode, blocking: exit 2 is the only code Claude Code treats
        # as a refusal, and it takes the reason from stderr. The report is
        # deliberately NOT written to stdout here — this JSON is our own
        # shape, not the documented hook-output schema, and emitting
        # unrecognized JSON alongside a blocking exit has historically been
        # the case where a block degrades into a warning. Saying nothing on
        # stdout leaves only the one channel that is specified.
        for issue in issues:
            if issue['severity'] == 'error':
                print(f"{issue['file']}: {issue['message']}", file=sys.stderr)
        sys.exit(2)

    # Non-blocking paths keep the machine-readable report: manual mode is a
    # CLI whose output is the point, and a passing event-mode run has no
    # exit-code contract to conflict with.
    print(json.dumps(result, indent=2))
    sys.exit(1 if has_errors else 0)


if __name__ == '__main__':
    main()
