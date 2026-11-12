"""Destructive-command check: text passed as data is not a command, and a
command inside quotes, a subshell or a pipe into a shell still is.

The existing pattern tests in test_skills2_hooks.py stay as they are. These
cases pin the difference between the two, in four groups that state what each
input did before this change and what it must do now.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import skill_validate_hook as hook  # noqa: E402
from skill_validate_hook import _check_dangerous_commands  # noqa: E402

# Passed before and must keep passing.
STILL_ALLOWED = [
    "rg 'chmod -R 777 /' .",
    "grep 'rm -rf /' notes.md | head",
    'grep -rn mkfs tests/*.py',
    'man mkfs.ext4 | head',
    "git commit -m 'docs: never run mkfs on the robot SD card'",
    "printf '%s\\n' 'never run rm -rf / on the robot' >> NOTES.md",
    "cat > SAFETY.md <<'EOF'\nDo not run rm -rf / or dd if=x of=/dev/sda here.\nEOF",
    'rm -rf build install log',
    'rm -rf ./tmp /tmp/scratch',
    'rm -rf -- ./build',
    'rm -rf "$BUILD_DIR"/',
    'rm -rf "/ tmp"',
    "echo 'cleaning' && rm -rf build",
    'ls /; echo done',
]

# Blocked before although nothing destructive would run; must pass now.
FALSE_POSITIVES = [
    "grep 'rm -rf / --no-preserve-root' README.md",
    'printf "%s\\n" "rm -rf / is dangerous"',
    "echo 'mkfs.ext4 /dev/sda1'",
    'grep -rn "dd if=/dev/zero of=/dev/sda" docs/',
    "grep 'mkfs.ext4 /dev/sda1' f | sort | uniq | wc -l",
    "rg -n 'mkfs.ext4 /dev/sda1' docs/ | head -5",
    "echo 'mkfs.ext4 /dev/sda1' >&2",
    "echo 'mkfs.ext4 /dev/sda1' > /dev/null",
    "fgrep 'dd if=/dev/zero of=/dev/sda' notes.md && echo found",
]

# Blocked before and must stay blocked: something runs, or the text is written
# where it can be run.
STILL_BLOCKED = [
    'echo "$(mkfs.ext4 /dev/sda1)"',
    'grep "$(dd if=/dev/zero of=/dev/sda)" f',
    'echo `mkfs.ext4 /dev/sda1`',
    'grep -f <(mkfs.ext4 /dev/sda1) x',
    "echo 'mkfs.ext4 /dev/sda1' | cat | sh",
    "printf -v cmd 'mkfs.ext4 /dev/sda1'; eval \"$cmd\"",
    "echo 'mkfs.ext4 /dev/sda1' | bash",
    "echo 'mkfs.ext4 /dev/sda1' > wipe.sh",
    "echo 'mkfs.ext4 /dev/sda1' | tee run.sh",
    "echo 'mkfs.ext4 /dev/sda1' | cat > run.sh",
    "echo 'mkfs.ext4 /dev/sda1' | sort -o run.sh",
    "echo 'mkfs.ext4 /dev/sda1' | xargs -I{} sh -c '{}'",
    "rg --pre 'mkfs.ext4 /dev/sda1' x .",
    "grep x f; rm -rf /",
    "grep 'mkfs.ext4 /dev/sda1' f; mkfs.ext4 /dev/sda1",
    "sudo echo 'mkfs.ext4 /dev/sda1'",
    "./grep 'mkfs.ext4 /dev/sda1'",
    "/tmp/echo 'mkfs.ext4 /dev/sda1'",
    "echo 'mkfs.ext4 /dev/sda1' | ./head",
    "echo 'mkfs.ext4 /dev/sda1' | /bin/sh",
    "bash -c \"echo 'mkfs.ext4 /dev/sda1'\"",
    "X='mkfs.ext4 /dev/sda1' echo done",
    "echo 'mkfs.ext4 /dev/sda1' 'unterminated",
    'sudo mkfs.ext4 /dev/sdb1',
    'timeout 5 dd if=/dev/zero of=/dev/sda',
    'echo y | sudo mkfs /dev/sdb',
    "ssh robot 'sudo mkfs.ext4 /dev/mmcblk0p1'",
    'bash -c "rm -rf / && echo gone"',
    "cat > wipe.sh <<'EOF'\nrm -rf /\nEOF",
    'rm -rf / --no-preserve-root',
    "bash -lc 'cd /tmp && rm -rf /*'",
]

# Passed before although the command would run; must be blocked now.
NEWLY_BLOCKED = [
    "bash -c 'rm -rf /'",
    'sh -c "rm -rf /"',
    "ssh h 'rm -rf /'",
    '(rm -rf /)',
    'x=$(rm -rf /)',
    "sudo sh -c 'rm -rf /'",
    "bash -c \"grep 'rm -rf /' f; rm -rf /\"",
    'echo "$(rm -rf /)"',
    "find . -maxdepth 0 -exec rm -rf / \\;",
    "echo 'rm -rf /' | bash",
    "printf 'rm -rf /\\n' | sh",
    "echo 'rm -rf /' > wipe.sh",
    "echo 'rm -rf /' | cat | sh",
    "printf '%s\\n' 'rm -rf /' >> cleanup.sh",
    "echo 'rm -rf /' | sudo bash",
    "echo 'rm -rf /' | tee wipe.sh",
    "echo 'rm -rf /' | cat > wipe.sh",
    'rm -rf --no-preserve-root /',
    'rm -rf -- /',
    'rm --recursive --force /',
    'sudo rm -rf --no-preserve-root /',
    'rm -rf /tmp/x /',
    'rm -rf -- "/*"',
]

# Not interpreted: git runs `!...` aliases through a shell. Recorded so the
# gap is visible; git is also not on the list of data-only programs.
NOT_DETECTED = [
    "git -c alias.x='!rm -rf /' x",
]


def blocked(command):
    return bool(_check_dangerous_commands(command))


@pytest.mark.parametrize('command', STILL_ALLOWED)
def test_harmless_commands_stay_allowed(command):
    assert not blocked(command), command


@pytest.mark.parametrize('command', FALSE_POSITIVES)
def test_text_given_as_data_to_grep_echo_printf_is_not_blocked(command):
    assert not blocked(command), command


@pytest.mark.parametrize('command', STILL_BLOCKED)
def test_commands_that_run_or_get_written_stay_blocked(command):
    assert blocked(command), command


@pytest.mark.parametrize('command', NEWLY_BLOCKED)
def test_root_removal_inside_quotes_groups_and_pipes_is_blocked(command):
    issues = _check_dangerous_commands(command)
    assert issues and all(issue['severity'] == 'error' for issue in issues), command


@pytest.mark.parametrize('command', NOT_DETECTED)
def test_documented_gap_git_shell_alias(command):
    assert not blocked(command), command


def test_only_literal_arguments_are_treated_as_data():
    # The substitution runs before grep or echo sees its argument.
    assert blocked("grep 'x' f \"$(mkfs.ext4 /dev/sda1)\"")
    assert blocked("echo safe $(mkfs.ext4 /dev/sda1)")
    assert not blocked("echo safe 'mkfs.ext4 /dev/sda1'")
    # printf -v stores the text; eval then runs it.
    assert blocked("printf -v cmd 'mkfs.ext4 /dev/sda1'")
    # A program outside the short list keeps the old, conservative verdict.
    assert blocked("awk '{ print }' 'mkfs.ext4 /dev/sda1'")
    assert blocked("sed -n 'mkfs.ext4 /dev/sda1' f")


def test_the_rest_of_a_pipeline_decides():
    assert not blocked("echo 'mkfs.ext4 /dev/sda1' | head -1")
    assert not blocked("echo 'mkfs.ext4 /dev/sda1' | cat | wc -c")
    assert blocked("echo 'mkfs.ext4 /dev/sda1' | head -1 | sh")
    assert blocked("echo 'mkfs.ext4 /dev/sda1' | cat | cat | bash -s")
    assert blocked("echo 'mkfs.ext4 /dev/sda1' | python3 -")


def test_non_posix_tools_keep_the_pattern_verdict():
    # PowerShell quoting is not parsed, so nothing is masked and nothing added.
    command = "echo 'mkfs.ext4 /dev/sda1'"
    assert _check_dangerous_commands(command, 'powershell')
    assert not _check_dangerous_commands(command, 'bash')
    assert not _check_dangerous_commands("bash -c 'rm -rf /'", 'pwsh')
    assert _check_dangerous_commands('Remove-Item -Recurse -Force C:/', 'powershell')


def _hook(command, tool='Bash'):
    environ = {key: value for key, value in os.environ.items()
               if key not in ('TOOL_NAME', 'TOOL_INPUT', 'ROS2_SKILL_ACTUATION')}
    return subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/claude_hook.py'), 'PreToolUse'],
        input=json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': tool,
                          'tool_input': {'command': command}, 'cwd': '/ws'}),
        capture_output=True, text=True, timeout=15, env=environ)


@pytest.mark.parametrize('command', FALSE_POSITIVES[:4] + STILL_ALLOWED[:2])
def test_hook_lets_data_commands_through(command):
    result = _hook(command)
    assert result.returncode == 0 and json.loads(result.stdout) == {}, command


@pytest.mark.parametrize('command', NEWLY_BLOCKED[:4] + NEWLY_BLOCKED[10:13] + STILL_BLOCKED[:2])
def test_hook_refuses_commands_that_would_run(command):
    result = _hook(command)
    assert result.returncode == 2 and not result.stdout and result.stderr, command


def test_manual_mode_matches_the_hook():
    script = str(ROOT / 'scripts/skill_validate_hook.py')
    for command, code in (("grep 'rm -rf / --no-preserve-root' README.md", 0),
                          ("bash -c 'rm -rf /'", 1), ("echo 'rm -rf /' | bash", 1)):
        result = subprocess.run([sys.executable, '-B', script, '--command', command],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == code, command


# --- Output of echo and printf, checked as the one string it is -------------

# Passed before although the completed output is a destructive command.
RENDERED_BLOCKED = [
    r"printf '%s' 'rm ' '-rf /' | sh",
    r"printf '%s' 'rm -rf' ' /' > w.sh",
    r"printf '%b' 'rm -rf /\n' | sh",
    r"printf '%b' 'rm\x20-rf\x20/' | sh",
    r"echo -e 'rm\x20-rf\x20/' | sh",
    r"printf '%s' 'r' 'm -rf /' | tee w.sh",
    r"printf '%s' 'r' 'm -rf /' | cat | sh",
    r"printf '%-3s%s' 'rm' '-rf /' | sh",
    r"printf '%.2s %s' 'rmdir' '-rf /' | sh",
]

# The same, with nothing in the raw text that names the program.
NO_HINT_BLOCKED = [
    r"printf '%s%s' 'r' 'm -rf /' | sh",
    r"printf '%b' '\x72\x6d -rf /' | sh",
    r"echo -e '\x72\x6d -rf /' | sh",
    r"echo -E -e '\x72\x6d -rf /' | sh",
    r"echo -en '\0162m -rf /\n' | sh",
    r"printf '\x72m -rf /\n' | bash",
    r"printf '\162\155 -rf /' | sh",
    r"printf '%b' '\0162\0155 -rf /' | sh",
    r"printf '%s%s%s' r m ' -rf /' | sudo sh",
    'printf "%s%s" "r" "m -rf /" | sh',
    r"echo $'\x72m -rf /' | sh",
    r"$'\x72m' -rf /",
    "r''m -rf /",
    r"r\m -rf /",
    '"r"m -rf /',
]

# Blocked before and still blocked: one argument, or a format that completes it.
RENDERED_STILL_BLOCKED = [
    r"printf '%s%s\n' 'rm ' '-rf /' | sh",
    r"printf '%s %s\n' rm '-rf /' | sh",
    r"printf 'rm %s\n' '-rf /' | sh",
    r"printf '%s\n' 'rm -rf /' | sh",
    r"printf 'rm -rf %s\n' / | sh",
    r"printf '%s' 'mkfs.ext4 ' '/dev/sda1' | sh",
    r"printf '%5s' 'rm -rf /' | sh",
]

# Blocked before although the completed output is a sentence, not a command.
RENDERED_FALSE_POSITIVES = [
    r"printf '%s' 'never run ' 'rm -rf /' >> NOTES.md",
    r"printf '%s %s\n' 'do not' 'rm -rf / here' >> NOTES.md",
]

# Passed before and still passes: the output is not that command. bash's echo
# leaves escapes alone unless -e is the last of -e and -E; \c in a format is
# two characters, and in a %b argument it ends the output.
RENDERED_ALLOWED = [
    r"printf '%d\n' 3 >> n.txt",
    r"printf '100%%\n' > p.txt",
    r"printf '%s\n' hello > out.txt",
    r"echo -e -E '\x72\x6d -rf /' | sh",
    r"echo '\x72\x6d -rf /' | sh",
    r"printf '\c%s' 'rm -rf /' | sh",
    r"printf '%b%s' '\c' 'rm -rf /' | sh",
    r"echo -e 'ok\c rm -rf /' | sh",
    r"printf '%.0s%s' 'rm -rf /' 'ls' | sh",
    "echo 'colcon build' | sh",
]

# Not rendered: other conversions, and the joined output of several commands.
# These keep the pattern verdict, which for these inputs is a pass.
NOT_RENDERED = [
    r"printf '%q' 'rm -rf /' | sh",
    r"printf '%c%s' 'r' 'm -rf /' | sh",
    r"printf '%*s' 3 'rm -rf /' | sh",
    r"{ printf 'rm '; printf '-rf /'; } | sh",
    r"printf '%s' 'r' 'm -rf /' | rev | sh",
]


@pytest.mark.parametrize('command', RENDERED_BLOCKED + NO_HINT_BLOCKED + RENDERED_STILL_BLOCKED)
def test_completed_output_that_is_a_command_is_blocked(command):
    issues = _check_dangerous_commands(command)
    assert issues and all(issue['severity'] == 'error' for issue in issues), command


@pytest.mark.parametrize('command', RENDERED_FALSE_POSITIVES + RENDERED_ALLOWED)
def test_completed_output_that_is_not_a_command_passes(command):
    assert not blocked(command), command


@pytest.mark.parametrize('command', NOT_RENDERED)
def test_documented_gap_output_that_is_not_rendered(command):
    assert not blocked(command), command


@pytest.mark.parametrize('command', NO_HINT_BLOCKED[:3] + RENDERED_BLOCKED[:2])
def test_hook_refuses_rendered_commands(command):
    result = _hook(command)
    assert result.returncode == 2 and not result.stdout and result.stderr, command


@pytest.mark.parametrize('command', RENDERED_FALSE_POSITIVES + RENDERED_ALLOWED[3:5])
def test_hook_lets_rendered_text_through(command):
    result = _hook(command)
    assert result.returncode == 0 and json.loads(result.stdout) == {}, command


class TestDecodeEscapes:
    """One row per place where bash reads the same text differently."""

    @pytest.mark.parametrize('text,fmt,percent_b,echo,ansi_c', [
        (r'\0123', '\n3', 'S', 'S', '\n3'),
        (r'\123', 'S', 'S', r'\123', 'S'),
        (r'A\cB', r'A\cB', ('A', True), ('A', True), None),
        (r'\"', '"', r'\"', r'\"', '"'),
        (r"\'", "'", r"\'", r"\'", "'"),
        (r'\?', '?', r'\?', r'\?', '?'),
        (r'\x41\x4a', 'AJ', 'AJ', 'AJ', 'AJ'),
        (r'\x414', 'A4', 'A4', 'A4', 'A4'),
        (r'a\nb\tc\\d', 'a\nb\tc\\d', 'a\nb\tc\\d', 'a\nb\tc\\d', 'a\nb\tc\\d'),
        (r'\e\E', '\x1b\x1b', '\x1b\x1b', '\x1b\x1b', '\x1b\x1b'),
        (r'\q\8\xZ\-', r'\q\8\xZ\-', r'\q\8\xZ\-', r'\q\8\xZ\-', r'\q\8\xZ\-'),
        ('end\\', 'end\\', 'end\\', 'end\\', 'end\\'),
        (r'\u0041', None, None, None, None),
        (r'\U00000041', None, None, None, None),
        (r'\uZ', r'\uZ', r'\uZ', r'\uZ', r'\uZ'),
        (r'\0', None, None, None, None),
        (r'\200', None, None, r'\200', None),
        (r'\x80', None, None, None, None),
        (r'\0101', '\x081', 'A', 'A', '\x081'),
    ])
    def test_modes(self, text, fmt, percent_b, echo, ansi_c):
        for mode, expected in (('format', fmt), ('percent_b', percent_b),
                               ('echo', echo), ('ansi_c', ansi_c)):
            if isinstance(expected, str):
                expected = (expected, False)
            assert hook._decode_escapes(text, mode) == expected, (text, mode)


class TestRenderPrintf:
    @pytest.mark.parametrize('args,expected', [
        (['%s', 'a', 'b', 'c'], 'abc'),
        (['%s-%s\\n', 'a', 'b', 'c'], 'a-b\nc-\n'),
        (['x\\n', 'a', 'b'], 'x\n'),
        (['%s|%s'], '|'),
        (['100%%\\n'], '100%\n'),
        (['%b', 'a\\nb'], 'a\nb'),
        (['%s', 'a\\nb'], 'a\\nb'),
        (['%b|%s', 'a\\cb', 'never'], 'a'),
        (['[%b]', '\\c', 'x'], '['),
        (['\\c%s', 'x'], '\\cx'),
        (['%5s|', 'ab'], '   ab|'),
        (['%-5s|', 'ab'], 'ab   |'),
        (['%.1s|', 'ab'], 'a|'),
        (['%.s|', 'ab'], '|'),
        (['%4.1s|', 'ab'], '   a|'),
        (['\\x25s', 'a'], '%s'),
        (['\\%s', 'a'], '\\a'),
        (['--', '%s\\n', 'a'], 'a\n'),
        (['', 'a'], ''),
    ])
    def test_rendered(self, args, expected):
        assert hook._render_printf(args) == expected, args

    @pytest.mark.parametrize('args', [
        [], ['-v', 'x', '%s', 'a'], ['-vx', 'a'], ['-x'], ['%d', '3'], ['%q', 'a b'], ['%c', 'a'],
        ['%*s', '3', 'a'], ['%05s', 'a'], ['%+s', 'a'], ['%5b', 'a'], ['%.1b', 'a'], ['%'],
        ['%s%', 'a'], ['%5s', 'é'], ['\\u0041'], ['%b', '\\u0041'], ['%b', '\\0'], ['%z', 'a'],
    ])
    def test_not_rendered(self, args):
        assert hook._render_printf(args) is None, args


class TestRenderEcho:
    @pytest.mark.parametrize('args,expected', [
        (['a', 'b'], 'a b\n'),
        (['-n', 'a'], 'a'),
        (['a\\nb'], 'a\\nb\n'),
        (['-e', 'a\\nb'], 'a\nb\n'),
        (['-e', '-E', 'a\\nb'], 'a\\nb\n'),
        (['-E', '-e', 'a\\nb'], 'a\nb\n'),
        (['-eE', 'a\\nb'], 'a\\nb\n'),
        (['-Ee', 'a\\nb'], 'a\nb\n'),
        (['-ne', 'a\\tb'], 'a\tb'),
        (['-e', 'a\\cb', 'c'], 'a'),
        (['-e', '\\0101', '\\101'], 'A \\101\n'),
        (['-x', 'a'], '-x a\n'),
        (['-e', '-x', '-n', 'a\\nb'], '-x -n a\nb\n'),
        (['a', '-e', 'b\\n'], 'a -e b\\n\n'),
        (['-', 'a'], '- a\n'),
        (['--', 'a'], '-- a\n'),
        ([], '\n'),
    ])
    def test_rendered(self, args, expected):
        assert hook._render_echo(args) == expected, args

    def test_not_rendered(self):
        assert hook._render_echo(['-e', '\\u0041']) is None
        assert hook._render_echo(['-e', '\\0']) is None
        assert hook._render_echo(['\\u0041']) == '\\u0041\n'


def test_ansi_c_quoted_words_are_decoded_or_marked_unknown():
    out = hook._command_shape(r"$'\x65cho' $'a\tb' $'\u0041' plain")
    words = out.commands[0][0]
    assert words == [('echo', False), ('a\tb', False), (r'\u0041', True), ('plain', False)]


def _bash():
    """bash 5 or later, whose builtins the renderers describe."""
    path = shutil.which('bash')
    if sys.platform == 'win32' or not path:
        return None
    try:
        major = subprocess.run([path, '--noprofile', '--norc', '-c', 'echo "${BASH_VERSINFO[0]}"'],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return path if major.isdigit() and int(major) >= 5 else None


# Harmless text only: letters, digits and every escape the decoder reads.
ESCAPED = [r'A\aZ', r'A\bZ', r'A\cZ', r'A\eZ', r'A\EZ', r'A\fZ', r'A\nZ', r'A\rZ', r'A\tZ',
           r'A\vZ', r'A\\Z', r"A\'Z", r'A\"Z', r'A\?Z', r'A\1Z', r'A\7Z', r'A\8Z', r'A\xZ',
           r'A\uZ', r'A\UZ', r'A\qZ', r'A\$Z', r'A\-Z', r'A\ Z', r'\0123Z', r'\123Z', r'\1234',
           r'\101', r'\0101', r'\00101', r'\x4a', r'\x41Z', r'\x414', r'\x', 'A\\', r'\101\c\102',
           r'\cZ', r'\60\061\x32', r'\\n', r'\\\n', r'%%', r'A\x25Z', r'\0777', r'\777', r'\08']
PRINTF_CASES = ([[text] for text in ESCAPED]
                + [['%b', text] for text in ESCAPED]
                + [['%s', text] for text in ESCAPED]
                + [['[%b]%s', text, 'tail'] for text in ESCAPED]
                + [['%s', 'a', 'b', 'c'], ['%s-%s\\n', 'a', 'b', 'c'], ['x\\n', 'a', 'b'],
                   ['%s|%s|%s\\n', 'a'], ['%5s|%-5s|%.2s|%6.2s|%.s|\\n', 'ab', 'cd', 'efgh',
                                          'ijkl', 'mn'],
                   ['%3s', 'abcdef'], ['%-3s|', 'abcdef'], ['%s %b\\n', 'a\\tb', 'a\\tb', 'c'],
                   ['--', '%s\\n', 'a'], ['100%%\\n'], ['', 'a'], ['%b%b', 'A\\c', 'B'],
                   ['\\%s', 'a'], ['%s%%%s', 'a', 'b', 'c']])
ECHO_CASES = ([[text] for text in ESCAPED]
              + [['-e', text] for text in ESCAPED]
              + [['-e', text, 'tail'] for text in ESCAPED]
              + [['-n', 'a'], ['-e', '-E', 'A\\nB'], ['-E', '-e', 'A\\nB'], ['-eE', 'A\\nB'],
                 ['-Ee', 'A\\nB'], ['-ne', 'A\\tB'], ['-en', 'A\\tB'], ['-x', 'a'],
                 ['-e', '-x', '-n', 'A\\nB'], ['a', '-e', 'B\\n'], ['-', 'a'], ['--', 'a'], [],
                 ['-nn', 'a'], ['-n', '-n', 'a'], ['-ex', 'A\\nB'], ['a', 'b', 'c']])


def _real(bash, script, args):
    environ = {'PATH': os.environ.get('PATH', ''), 'LC_ALL': 'C'}
    return subprocess.run([bash, '--noprofile', '--norc', '-c', script, 'bash'] + args,
                          capture_output=True, timeout=10, env=environ).stdout


def test_renderers_agree_with_bash():
    """Whatever a renderer returns is exactly what bash writes.

    Inputs the renderers decline (None) are left out: declining is always
    allowed, a wrong string never is.
    """
    bash = _bash()
    if bash is None:
        pytest.skip('needs bash 5 or later')
    compared = 0
    for args in PRINTF_CASES:
        rendered = hook._render_printf(args)
        if rendered is not None:
            assert _real(bash, 'printf "$@"', args) == rendered.encode(), ('printf', args)
            compared += 1
    for args in ECHO_CASES:
        rendered = hook._render_echo(args)
        if rendered is not None:
            assert _real(bash, 'echo "$@"', args) == rendered.encode(), ('echo', args)
            compared += 1
    for text in ESCAPED:
        decoded = hook._decode_escapes(text, 'ansi_c')
        if decoded is not None and not text.endswith('\\') and "\\'" not in text:
            assert _real(bash, "printf '%s' $'" + text + "'", []) == decoded[0].encode(), text
            compared += 1
    assert compared > 300
