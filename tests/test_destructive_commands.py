"""Destructive-command check: text passed as data is not a command, and a
command inside quotes, a subshell or a pipe into a shell still is.

The existing pattern tests in test_skills2_hooks.py stay as they are. These
cases pin the difference between the two, in four groups that state what each
input did before this change and what it must do now.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

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
