"""ROS 2 commands that can move hardware: what is detected, and what is not.

The analysis is advisory and name-based. These tests pin the difference
between a command the shell would run and the same text as data, the syntax
that is reported as not evaluated instead of clean, and the three delivery
modes of the Claude adapter.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import claude_hook as adapter  # noqa: E402
import skill_validate_hook as hook  # noqa: E402

TWIST = 'geometry_msgs/msg/Twist'
PUB = 'ros2 topic pub /cmd_vel ' + TWIST + " '{}'"
NAV = "ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose '{}'"
# The command string is produced when the command runs: nothing literal to read.
BUILT = 'bash -c "$(printf \'ros2 topic pub /cmd_vel ' + TWIST + ' {}\')"'


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv(hook.ACTUATION_ENV, raising=False)


def analyze(command, tool='Bash'):
    return hook.analyze_actuation(command, tool)


def judged(command, tool='Bash'):
    result = analyze(command, tool)
    return result['status'], result['confidence']


HIGH = ('detected', 'high')
LOW = ('detected', 'low')
NONE = ('none', None)
UNREAD = ('not_evaluated', None)


@pytest.mark.parametrize('command', [
    PUB,
    'ros2 topic pub -r 10 /robot1/cmd_vel ' + TWIST + ' "{linear: {x: 2.0}}"',
    'ros2 topic pub --once cmd_vel ' + TWIST + ' {}',
    'ros2 topic pub --rate=10 /cmd_vel ' + TWIST + ' {}',
    'ros2 topic pub -t 3 --qos-reliability best_effort /base/cmd_vel_unstamped ' + TWIST + ' {}',
    'ros2 topic pub /drive geometry_msgs/msg/TwistStamped {}',
    'ros2 topic pub /arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory {}',
    'ros2 topic pub /forward_position_controller/commands std_msgs/msg/Float64MultiArray {}',
    'ros2 topic pub /ackermann_cmd ackermann_msgs/msg/AckermannDriveStamped {}',
    '/opt/ros/jazzy/bin/ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    NAV,
    'ros2 action send_goal -f /robot/follow_joint_trajectory '
    'control_msgs/action/FollowJointTrajectory {}',
    'ros2 action send_goal /spin nav2_msgs/action/Spin "{target_yaw: 1.57}"',
    'ros2 action send_goal /custom my_pkg/action/DockRobot {}',
    'ros2 service call /emergency_stop/reset std_srvs/srv/Trigger',
    'ros2 service call /robot/clear_faults std_srvs/srv/Trigger',
    'ros2 service call /left_motor/enable std_srvs/srv/SetBool "{data: true}"',
    'ros2 service call /mavros/cmd/arming mavros_msgs/srv/CommandBool "{value: true}"',
    'ros2 service call /controller_manager/switch_controller '
    'controller_manager_msgs/srv/SwitchController {}',
    'ros2 service call -r 1 /brake/release std_srvs/srv/Trigger',
    'ros2 control switch_controllers --activate joint_trajectory_controller',
    'ros2 control set_controller_state jtc active',
    'ros2 control set_hardware_component_state arm active',
    'ros2 control load_controller --set-state active jtc',
    'ros2 control load_controller jtc --set-state=active',
    'ros2 param set /controller_server FollowPath.max_vel_x 5.0',
    'ros2 param set /velocity_smoother max_accel "[2.5, 0.0, 3.2]"',
    'ros2 param set /driver command_timeout 30.0',
    'ros2 param set /safety_node e_stop_enabled false',
    'ros2 run teleop_twist_keyboard teleop_twist_keyboard',
])
def test_commands_that_can_move_hardware_are_high_confidence(command):
    result = analyze(command)
    assert (result['status'], result['confidence']) == HIGH, command
    assert all(issue['severity'] == 'warning' and issue['category'] == 'actuation'
               for issue in result['issues'])
    assert 'Not blocked' in result['issues'][0]['message']


@pytest.mark.parametrize('command', [
    'ros2 action send_goal /do_thing my_pkg/action/Thing {}',
    'ros2 lifecycle set /motor_driver activate',
])
def test_commands_that_may_actuate_are_low_confidence(command):
    assert judged(command) == LOW
    assert analyze(command)['issues'][0]['message'].startswith('Possible actuation')


@pytest.mark.parametrize('command', [
    'ros2 topic list',
    'ros2 topic echo /cmd_vel',
    'ros2 topic info /cmd_vel -v',
    'ros2 topic hz /cmd_vel',
    'ros2 topic pub /chatter std_msgs/msg/String "data: hi"',
    'ros2 topic pub /turtle1/cmd_vel ' + TWIST + ' "{linear: {x: 2.0}}"',
    'ros2 action list',
    'ros2 action send_goal /fibonacci action_tutorials_interfaces/action/Fibonacci "{order: 5}"',
    'ros2 action send_goal /turtle1/rotate_absolute turtlesim/action/RotateAbsolute "{theta: 1}"',
    'ros2 service list',
    'ros2 service call /add_two_ints example_interfaces/srv/AddTwoInts "{a: 1, b: 2}"',
    'ros2 service call /reset std_srvs/srv/Empty',
    'ros2 service call /camera/set_exposure my_msgs/srv/SetExposure {}',
    'ros2 control list_controllers',
    'ros2 control load_controller jtc',
    'ros2 control load_controller --set-state inactive jtc',
    'ros2 lifecycle get /motor_driver',
    'ros2 lifecycle set /motor_driver deactivate',
    'ros2 param get /controller_server FollowPath.max_vel_x',
    'ros2 param set /camera exposure 10',
    'ros2 param list',
    'ros2 run demo_nodes_cpp talker',
    'ros2 launch my_robot bringup.launch.py',
    'ros2 bag play run1 --loop',
    'ros2',
    'ros2 doctor',
    'colcon build --symlink-install',
])
def test_commands_outside_the_declared_scope_report_nothing(command):
    assert judged(command) == NONE, command
    assert analyze(command)['reasons'] == []


@pytest.mark.parametrize('command', [
    'grep "ros2 topic pub /cmd_vel" notes.md',
    "grep -rn 'ros2 action send_goal /navigate_to_pose' docs/",
    'git commit -m "ros2 topic pub /cmd_vel ' + TWIST + '"',
    "echo 'ros2 topic pub /cmd_vel " + TWIST + "'",
    'printf "%s\\n" "never run ros2 topic pub /cmd_vel here" >> NOTES.md',
    'python3 -c "print(\'ros2 topic pub /cmd_vel\')"',
    'man ros2',
    'cat notes.md  # ros2 topic pub /cmd_vel ' + TWIST,
    'which ros2 && ros2 topic list',
    'command -v ros2',
    'rg "ros2 control switch_controllers" -g "*.md" > /tmp/hits.txt 2>&1',
])
def test_the_same_text_as_data_is_not_a_command(command):
    assert judged(command) == NONE, command


def test_quoting_decides_whether_a_substitution_runs():
    assert judged('echo "$(' + PUB + ')"') == HIGH
    assert judged("echo '$(" + PUB.replace("'", '') + ")'") == NONE
    assert judged('x=$(ros2 topic pub --once /cmd_vel ' + TWIST + ' {})') == HIGH
    assert judged('echo `ros2 topic pub /cmd_vel ' + TWIST + ' {}`') == HIGH
    assert judged("echo '`ros2 topic pub /cmd_vel " + TWIST + " {}`'") == NONE
    assert judged('diff <(ros2 topic pub -1 /cmd_vel ' + TWIST + ' {}) expected.txt') == HIGH
    assert judged('echo $((1 + 2)); ros2 topic list') == NONE
    assert judged('echo "${ROS_DISTRO}"; ros2 topic list') == NONE
    assert judged("echo $'ros2 topic pub /cmd_vel'") == NONE


@pytest.mark.parametrize('command', [
    'true;' + PUB,
    'true&&' + PUB,
    'false || ' + PUB,
    'ros2 topic list | head\n' + PUB,
    'sleep 1 & ' + PUB,
    '( ' + PUB + ' )',
    '{ ' + PUB + '; }',
    'if true; then ' + PUB + '; fi',
    'for i in 1 2; do ' + PUB + '; done',
    '! ' + PUB,
    'ros2 topic pub \\\n  /cmd_vel ' + TWIST + ' {}',
    PUB + ' > /tmp/out.log 2>&1',
    PUB + ' &> /dev/null',
    '2>/dev/null ' + PUB,
    PUB + ' <<< ignored',
])
def test_separators_grouping_and_redirections(command):
    assert judged(command) == HIGH, command


@pytest.mark.parametrize('command', [
    'sudo -E ' + PUB,
    'sudo -u robot -- ' + PUB,
    'ROS_DOMAIN_ID=3 ' + PUB,
    'env ROS_DOMAIN_ID=3 RMW_IMPLEMENTATION=rmw_zenoh_cpp ' + PUB,
    'timeout -s INT 5 ' + PUB,
    'nice -n 5 nohup ' + PUB,
    'stdbuf -oL ' + PUB,
    'chrt 50 ' + PUB,
    'taskset 0x1 ' + PUB,
    'time ' + PUB,
    'exec ' + PUB,
    'x=1; y=2 ' + PUB,
])
def test_wrappers_and_assignments_are_looked_through(command):
    assert judged(command) == HIGH, command


@pytest.mark.parametrize('command', [
    'bash -c "' + PUB.replace("'", '') + '"',
    "bash -lc 'source /opt/ros/humble/setup.bash && " + PUB.replace("'", '') + "'",
    "bash --login -c '" + NAV.replace("'", '') + "'",
    "sh -xec '" + PUB.replace("'", '') + "'",
    "bash --norc --noprofile -c '" + PUB.replace("'", '') + "'",
    "bash -o pipefail -c '" + PUB.replace("'", '') + "'",
    'ssh robot "' + PUB.replace("'", '') + '"',
    'ssh -p 2222 -i key user@robot ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    'docker exec -it nav ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    "docker exec -e ROS_DOMAIN_ID=3 nav bash -lc '" + PUB.replace("'", '') + "'",
    'docker run --rm -it ros:jazzy ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    "su robot -c '" + PUB.replace("'", '') + "'",
    "eval '" + PUB.replace("'", '') + "'",
    'tmux send-keys "' + PUB.replace("'", '') + '" Enter',
    'watch -n 1 "' + PUB.replace("'", '') + '"',
    'xargs ros2 topic pub /cmd_vel ' + TWIST,
])
def test_strings_handed_to_another_shell_are_scanned(command):
    assert judged(command) == HIGH, command


def test_shell_script_arguments_are_not_command_strings():
    # A script file's contents are not visible; its name is not a command.
    assert judged('bash ./drive.sh ros2') == NONE
    assert judged('bash -c "echo hi" ros2') == NONE


def test_here_documents_follow_the_receiver_and_the_delimiter():
    body = '\n' + NAV + '\nEOF'
    # A shell runs the body whether or not the delimiter is quoted.
    assert judged("bash <<'EOF'" + body) == HIGH
    assert judged('bash <<EOF' + body) == HIGH
    assert judged('bash -s <<EOF' + body) == HIGH
    assert judged('ssh robot <<EOF' + body) == HIGH
    assert judged('docker exec -i nav bash <<EOF' + body) == HIGH
    assert judged("sudo -u robot sh <<-'EOF'\n\t" + NAV + '\n\tEOF') == HIGH
    # Anything else receives data...
    assert judged("cat <<'EOF'" + body) == NONE
    assert judged('cat <<EOF' + body) == NONE
    assert judged("cat > drive.sh <<'EOF'" + body) == NONE
    assert judged("tee notes.md <<\\EOF" + body) == NONE
    # ...except that an unquoted delimiter still expands command substitutions.
    substituted = '\n$(' + PUB + ')\nEOF'
    assert judged('cat <<EOF' + substituted) == HIGH
    assert judged("cat <<'EOF'" + substituted) == NONE
    assert judged('cat <<"EOF"' + substituted) == NONE
    assert judged('cat <<EOF\n`ros2 topic pub /cmd_vel ' + TWIST + ' {}`\nEOF') == HIGH
    assert judged('cat <<EOF\n\\$(ros2 topic pub /cmd_vel x)\n$((1+1))\nEOF') == NONE
    # A body handed to a shell with -c is that command's input, not a script.
    assert judged('bash -c "cat" <<EOF' + body) == NONE
    # The command after the here-document is still read.
    assert judged('cat <<EOF\ntext\nEOF\n' + PUB) == HIGH
    assert judged('cat <<A <<B\nfirst\nA\nsecond\nB\n' + PUB) == HIGH


@pytest.mark.parametrize('command,reason', [
    ('eval "echo $CMD"; ros2 topic list', 'eval builds its command at run time'),
    ('eval "$CMD"; ros2 topic list', 'eval builds its command at run time'),
    (BUILT, 'the command name is a variable or substitution'),
    ('bash -c "$CMD"; ros2 topic list', 'the command name is a variable or substitution'),
    ('ssh robot "$CMD"; ros2 topic list', 'the command name is a variable or substitution'),
    ('su robot -c "$CMD"; ros2 topic list', 'the command name is a variable or substitution'),
    ('watch -n 1 "$CMD"; ros2 topic list', 'the command name is a variable or substitution'),
    ('$TOOL x; ros2 topic list', 'the command name is a variable or substitution'),
    ('"$(which tool)" x; ros2 topic list', 'the command name is a variable or substitution'),
    ('tmux send-keys "$CMD" Enter; ros2 topic list',
     'a command string built at run time is not inspected'),
    ("ros2 topic pub /drive $TYPE '{}'", 'ros2 topic pub target is not literal'),
    ('ros2 topic pub $TOPIC std_msgs/msg/String {}', 'ros2 topic pub target is not literal'),
    ('ros2 action send_goal /do $TYPE {}', 'ros2 action send_goal target is not literal'),
    ('ros2 service call /do $TYPE', 'ros2 service call target is not literal'),
    ('ros2 lifecycle set /driver $TRANSITION', 'ros2 lifecycle set target is not literal'),
    ('docker run --some-new-flag ros:jazzy ros2 topic pub /cmd_vel ' + TWIST,
     'an unrecognized docker option hides the command position'),
    ('docker --unknown exec nav ros2 topic list',
     'an unrecognized docker option hides the command position'),
    ('docker compose --unknown exec nav ros2 topic list',
     'an unrecognized docker option hides the command position'),
    ('docker compose exec -it nav ros2 topic pub /cmd_vel ' + TWIST,
     'an unrecognized docker option hides the command position'),
    ('xargs --unknown ros2 topic pub /cmd_vel ' + TWIST,
     'an unrecognized xargs option hides the command position'),
    ('watch --unknown "ros2 topic pub /cmd_vel x"',
     'an unrecognized watch option hides the command position'),
    ('tmux --unknown send-keys "ros2 topic pub /cmd_vel x"',
     'an unrecognized tmux option hides the command position'),
    ('kubectl exec pod ros2 topic pub /cmd_vel ' + TWIST,
     'an unrecognized kubectl option hides the command position'),
    ('parallel ros2 topic pub ::: /cmd_vel', 'arguments of parallel are not inspected'),
    ('screen -dmS drive ros2 topic pub /cmd_vel ' + TWIST,
     'arguments of screen are not inspected'),
    ('flock /tmp/l bash -c "ros2 topic list"', 'arguments of flock are not inspected'),
    ("ros2 topic pub /cmd_vel 'unterminated", 'unbalanced quote'),
    ('ros2 topic pub /cmd_vel "unterminated', 'unbalanced quote'),
    ('echo `ros2 topic list', 'unbalanced backtick'),
    ('echo $(ros2 topic list', 'unbalanced quote or parenthesis'),
    ('echo ${ROS_DISTRO; ros2 topic list', 'unbalanced parameter expansion'),
    ("echo $'ros2 topic list", 'unbalanced quote'),
    ('cat <<EOF\n`ros2 topic list\nEOF', 'unbalanced backtick'),
    ('bash <<EOF\nros2 topic list', 'a here-document is not terminated'),
    ('cat <<EOF\nros2 topic list', 'a here-document is not terminated'),
    ('ros2 topic list; cat <<EOF', 'a here-document is not terminated'),
    ('echo "' + PUB + '" | bash', 'commands fed to a shell on standard input are not inspected'),
    ('$PREFIX ros2 topic pub /cmd_vel ' + TWIST, 'the command name is a variable or substitution'),
    ('ros2 $VERB pub /cmd_vel ' + TWIST, 'ros2 verb is not literal'),
    ('ros2 topic pub $TOPIC $TYPE {}', 'ros2 topic pub target is not literal'),
    ('ros2 action send_goal $NAME my/action/T {}', 'ros2 action send_goal target is not literal'),
    ('ros2 service call "$SERVICE" std_srvs/srv/Trigger', 'ros2 service call target is not literal'),
    ('ros2 lifecycle set $NODE activate', 'ros2 lifecycle set target is not literal'),
    ('ros2 param set /node $NAME 1', 'ros2 param set target is not literal'),
    ('bash -c "bash -c \\"bash -c \\\\\\"bash -c ros2\\\\\\"\\""',
     'command nesting is deeper than the analysis follows'),
])
def test_uninterpreted_syntax_is_reported_not_passed(command, reason):
    result = analyze(command)
    assert result['status'] == 'not_evaluated' and result['issues'] == []
    assert reason in result['reasons'], result['reasons']


def test_a_finding_and_an_unread_part_are_both_reported():
    result = analyze(PUB + '; eval "ros2 topic pub /cmd_vel $MSG"')
    assert (result['status'], result['confidence']) == HIGH
    assert result['reasons'] == ['eval builds its command at run time']
    # The literal part classifies first, whichever half is dynamic.
    assert judged('ros2 topic pub $TOPIC ' + TWIST + ' {}') == HIGH
    assert judged('ros2 topic pub /$NS/cmd_vel $TYPE {}') == HIGH
    assert judged('ros2 action send_goal /navigate_to_pose $TYPE {}') == HIGH
    assert judged('ros2 action send_goal $NAME nav2_msgs/action/Spin {}') == HIGH
    assert judged('ros2 service call /emergency_stop/reset $TYPE') == HIGH
    assert judged('ros2 param set $NODE FollowPath.max_vel_x 5.0') == HIGH
    assert judged('ros2 param set /node max_vel_$AXIS 5.0') == HIGH
    # A type that looks harmless does not settle an unknown topic: controller
    # command topics carry plain std_msgs arrays.
    assert judged('ros2 topic pub $TOPIC std_msgs/msg/String {}') == UNREAD
    assert judged('ros2 topic pub /turtle1/cmd_vel $TYPE {}') == NONE


def test_eval_reports_the_literal_part_and_that_the_rest_is_unknown():
    # The topic is literal, so the finding stands; $MSG can still expand to a
    # separator and another command, so the result is also marked incomplete.
    result = analyze('eval "ros2 topic pub /cmd_vel $MSG"')
    assert (result['status'], result['confidence']) == HIGH
    assert result['reasons'] == ['eval builds its command at run time']
    literal = analyze("eval '" + PUB.replace("'", '') + "'")
    assert (literal['status'], literal['reasons']) == ('detected', [])
    assert judged('eval "echo $CMD"; ros2 topic list') == UNREAD
    # Without the literal text `ros2` the analysis does not start.
    assert judged('eval "$CMD"') == NONE


@pytest.mark.parametrize('command', [
    'ROS_DOMAIN_ID=$DOMAIN ' + PUB,
    'ROS_DOMAIN_ID="$DOMAIN" RMW_IMPLEMENTATION=$RMW ' + PUB,
    'A="$(date)" B=2 ' + PUB,
    'env ROS_DOMAIN_ID=$DOMAIN ' + PUB,
    'sudo -u $USER ' + PUB,
    'sudo -u "$(id -un)" -E ' + PUB,
    'timeout $LIMIT ' + PUB,
    'timeout -s INT "$LIMIT" ' + PUB,
    '$HOME/ros2_ws/install/bin/ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    '"${ROS_BIN}"/ros2 topic pub /cmd_vel ' + TWIST + ' {}',
])
def test_dynamic_values_around_a_literal_program_do_not_hide_it(command):
    result = analyze(command)
    assert (result['status'], result['confidence'], result['reasons']) == ('detected', 'high', [])


@pytest.mark.parametrize('command', [
    'bash -c "cd $DIR && make"; ros2 topic list',
    'bash -c "ros2 topic list $X"',
    'source "$WS/install/setup.bash" && ros2 topic list',
    '. $WS/install/setup.bash; ros2 topic echo /cmd_vel',
    'cd "$WS" && colcon build && ros2 topic list',
    'ROS_DOMAIN_ID=$DOMAIN ros2 topic list',
    'echo "$CMD" ros2',
])
def test_dynamic_arguments_of_literal_commands_are_not_unread(command):
    assert judged(command) == NONE, command
    assert analyze(command)['reasons'] == []


@pytest.mark.parametrize('command', [
    "docker exec nav echo ros2 topic pub /cmd_vel " + TWIST + " '{}'",
    'docker exec nav grep -r "ros2 topic pub /cmd_vel" /ws',
    'docker exec -it nav cat /ws/ros2_notes.txt',
    'docker run --rm ros:jazzy echo ros2 topic pub /cmd_vel',
    'docker run --rm --entrypoint echo img ros2 topic pub /cmd_vel ' + TWIST,
    'docker logs ros2',
    'docker ps --filter name=ros2',
    'docker compose logs ros2',
    'docker compose -f ros2.yml up -d',
    'kubectl logs ros2 topic pub /cmd_vel',
    'kubectl get pods -l app=ros2',
    'kubectl exec pod -- echo ros2 topic pub /cmd_vel',
    'xargs echo ros2 topic pub /cmd_vel ' + TWIST,
    'xargs -n 1 grep ros2',
    'find . -name ros2 -newer bash',
    'find . -name "*.py" -exec grep -l ros2 {} +',
    'watch -n 1 "grep ros2 notes.md"',
    'watch ros2 topic list',
    'tmux send-keys "grep ros2 notes.md" Enter',
    'tmux send-keys -t "$PANE" "ros2 topic list" Enter',
    'tmux new-session -d -s ros2 htop',
    'tmux kill-session -t ros2',
    'tmux ls',
    'docker run --some-new-flag img ls; ros2 topic list',
])
def test_arguments_of_a_program_that_is_not_ros2_are_data(command):
    assert judged(command) == NONE, command
    assert analyze(command)['reasons'] == []


@pytest.mark.parametrize('command', [
    'docker run --read-only ros:jazzy ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    'docker run --rm -it -v /ws:/ws --network host ros:jazzy ros2 topic pub /cmd_vel ' + TWIST,
    'docker run --network=host -e A=1 -eB=2 --name nav ros:jazzy ros2 topic pub /cmd_vel ' + TWIST,
    'docker run -d --privileged --init -P -w /ws -u robot ros:jazzy ros2 topic pub /cmd_vel '
    + TWIST,
    'docker run --rm --entrypoint ros2 img topic pub /cmd_vel ' + TWIST,
    'docker run --rm --entrypoint=ros2 img topic pub /cmd_vel ' + TWIST,
    'docker --context robot exec nav ros2 topic pub /cmd_vel ' + TWIST,
    'docker exec -e ROS_DOMAIN_ID=$DOMAIN "$CONTAINER" ros2 topic pub /cmd_vel ' + TWIST,
    'podman exec -it nav ros2 topic pub /cmd_vel ' + TWIST,
    'docker compose exec nav ros2 topic pub /cmd_vel ' + TWIST,
    'docker compose -f nav.yml exec -T nav ros2 topic pub /cmd_vel ' + TWIST,
    'docker compose run --rm nav ros2 topic pub /cmd_vel ' + TWIST,
    'docker-compose exec nav ros2 topic pub /cmd_vel ' + TWIST,
    'kubectl exec pod -- ros2 topic pub /cmd_vel ' + TWIST,
    'kubectl -n robots exec -it pod -c nav -- ros2 topic pub /cmd_vel ' + TWIST,
    'xargs -n 1 ros2 topic pub /cmd_vel ' + TWIST,
    'xargs -0 -I{} ros2 topic pub /cmd_vel ' + TWIST + ' {}',
    'find . -name "*.yaml" -exec ros2 topic pub /cmd_vel ' + TWIST + ' {} \\;',
    'find . -execdir ros2 control switch_controllers --activate jtc +',
    'watch -n 1 ros2 topic pub -1 /cmd_vel ' + TWIST,
    'watch -d -n 1 "ros2 topic pub -1 /cmd_vel ' + TWIST + '"',
    'tmux new-window ros2 topic pub /cmd_vel ' + TWIST,
    'tmux -L robot send-keys -t nav "ros2 topic pub /cmd_vel ' + TWIST + '" Enter',
])
def test_the_program_a_launcher_runs_is_located_and_judged(command):
    assert judged(command) == HIGH, command


def test_documented_limits_are_not_detected():
    # No literal `ros2`: the analysis does not start, by design.
    assert judged('$CMD /cmd_vel') == NONE
    assert judged('./drive_forward.sh') == NONE
    assert judged('python3 teleop.py') == NONE
    # The Python string is data to the shell, whatever Python does with it.
    assert judged('python3 -c "import subprocess; subprocess.run([\'ros2\', \'topic\', \'pub\'])"') == NONE
    assert judged(None) == NONE and judged('') == NONE


@pytest.mark.parametrize('tool', ['PowerShell', 'pwsh', 'cmd', 'Write'])
def test_only_posix_shell_tools_are_parsed(tool):
    result = analyze('ros2 topic pub /cmd_vel ' + TWIST + ' {}', tool)
    assert result['status'] == 'not_evaluated'
    assert 'only POSIX shell commands are analyzed' in result['reasons'][0]
    assert analyze('Get-ChildItem', tool)['status'] == 'none'


@pytest.mark.parametrize('tool', ['Bash', 'bash', 'shell', 'mcp__server__bash', 'terminal'])
def test_posix_shell_tool_aliases_are_parsed(tool):
    assert judged(PUB, tool) == HIGH


def test_off_mode_disables_the_analysis(monkeypatch):
    monkeypatch.setenv(hook.ACTUATION_ENV, ' OFF ')
    assert analyze(PUB) == {'status': 'off', 'confidence': None, 'reasons': [], 'issues': []}


def test_long_and_deep_input_stays_bounded():
    start = time.monotonic()
    long_command = 'ros2 topic list; ' + 'echo x; ' * 8000
    assert len(long_command) < hook._ACTUATION_MAX_CHARS
    assert judged(long_command) == NONE
    assert judged('echo ' + '$(' * 500 + 'ros2' + ')' * 500) == UNREAD
    too_long = analyze('ros2 topic list ' + 'x' * hook._ACTUATION_MAX_CHARS)
    assert too_long['reasons'] == ['the command is longer than the analysis reads']
    assert time.monotonic() - start < 5.0


def test_repeated_findings_are_reported_once():
    result = analyze(PUB + '; ' + PUB)
    assert len(result['issues']) == 1


# --- validator output ---------------------------------------------------------

def _validator(*args, payload=None, env=None):
    environ = {key: value for key, value in os.environ.items()
               if key not in (hook.ACTUATION_ENV, 'TOOL_NAME', 'TOOL_INPUT')}
    environ.update(env or {})
    return subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/skill_validate_hook.py'), *args],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True, text=True, timeout=15, env=environ)


def _bash(command, tool='Bash'):
    return {'hook_event_name': 'PreToolUse', 'tool_name': tool,
            'tool_input': {'command': command}, 'cwd': '/ws'}


def test_report_carries_the_structured_result_in_both_modes():
    manual = _validator('--command', PUB)
    event = _validator(payload=_bash(PUB))
    assert manual.returncode == 0 and event.returncode == 0
    for result, mode in ((manual, 'manual'), (event, 'event')):
        report = json.loads(result.stdout)
        assert report['mode'] == mode and report['version'] == '1.5.0'
        assert report['status'] == 'pass'
        assert report['actuation'] == {'status': 'detected', 'confidence': 'high', 'reasons': []}
        assert report['issues'][0]['category'] == 'actuation'
        assert report['checks_skipped'] == []
    assert (json.loads(manual.stdout)['issues'] == json.loads(event.stdout)['issues'])


def test_unread_command_reaches_the_report_as_skipped():
    for result in (_validator('--command', BUILT), _validator(payload=_bash(BUILT))):
        report = json.loads(result.stdout)
        assert result.returncode == 0 and report['issues'] == []
        assert report['checks_skipped'] == [
            'actuation: the command name is a variable or substitution']
        assert report['actuation']['status'] == 'not_evaluated'


def test_finding_and_unread_part_both_reach_the_report():
    command = 'eval "ros2 topic pub /cmd_vel $MSG"'
    for result in (_validator('--command', command), _validator(payload=_bash(command))):
        report = json.loads(result.stdout)
        assert result.returncode == 0
        assert [issue['confidence'] for issue in report['issues']] == ['high']
        assert report['checks_skipped'] == ['actuation: eval builds its command at run time']
        assert report['actuation'] == {
            'status': 'detected', 'confidence': 'high',
            'reasons': ['eval builds its command at run time']}


def test_report_schema_is_stable_without_a_command():
    for result in (_validator(payload=_bash('ls')),
                   _validator(payload={'tool_name': 'Write', 'tool_input': {
                       'file_path': 'a.md', 'content': 'ros2 topic pub /cmd_vel'}})):
        report = json.loads(result.stdout)
        assert report['actuation'] == {'status': 'none', 'confidence': None, 'reasons': []}
    assert json.loads(_validator('--command', PUB, env={hook.ACTUATION_ENV: 'off'}).stdout)[
        'actuation']['status'] == 'off'


def test_destructive_commands_keep_their_exit_codes():
    both = PUB + '; rm -rf /'
    manual = _validator('--command', both)
    assert manual.returncode == 1
    report = json.loads(manual.stdout)
    assert report['status'] == 'fail'
    assert {issue['severity'] for issue in report['issues']} == {'error', 'warning'}
    event = _validator(payload=_bash(both))
    assert event.returncode == 2 and not event.stdout and 'rm -rf' in event.stderr
    # Actuation alone never produces a failing or blocking exit.
    assert _validator('--command', PUB).returncode == 0
    assert _validator(payload=_bash(PUB)).returncode == 0


def test_powershell_destructive_checks_are_unchanged():
    result = _validator(payload=_bash('Remove-Item -Recurse -Force C:/', 'PowerShell'))
    assert result.returncode == 2
    report = json.loads(_validator(payload=_bash('ros2 topic pub /cmd_vel x', 'pwsh')).stdout)
    assert report['actuation']['status'] == 'not_evaluated' and report['issues'] == []


# --- Claude adapter -----------------------------------------------------------

def _adapter(command, mode=None, tool='Bash'):
    environ = {key: value for key, value in os.environ.items() if key != hook.ACTUATION_ENV}
    if mode is not None:
        environ[hook.ACTUATION_ENV] = mode
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/claude_hook.py'), 'PreToolUse'],
        input=json.dumps(_bash(command, tool)), capture_output=True, text=True,
        timeout=15, env=environ)
    return result


@pytest.mark.parametrize('mode', [None, 'advise', 'unknown-value', ''])
def test_default_mode_adds_context_and_makes_no_permission_decision(mode):
    result = _adapter(PUB, mode)
    assert result.returncode == 0 and 'permissionDecision' not in result.stdout
    context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    assert 'Actuation command' in context and '/cmd_vel' in context


def test_default_mode_tells_the_model_what_was_not_evaluated():
    result = _adapter('eval "ros2 topic pub /cmd_vel $MSG"')
    context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    assert 'Actuation command' in context
    assert 'Skipped: actuation: eval builds its command at run time' in context
    assert 'permissionDecision' not in result.stdout
    unread = _adapter(BUILT)
    context = json.loads(unread.stdout)['hookSpecificOutput']['additionalContext']
    assert context.endswith('Skipped: actuation: the command name is a variable or substitution')
    assert 'permissionDecision' not in unread.stdout


def test_low_confidence_finding_beside_an_unread_part():
    # Advisory by default; in ask mode the unread part is reason enough.
    assert analyze(LOW_AND_UNREAD)['confidence'] == 'low'
    assert analyze(LOW_AND_UNREAD)['reasons'] == [
        'the command name is a variable or substitution']
    default = json.loads(_adapter(LOW_AND_UNREAD).stdout)['hookSpecificOutput']
    assert 'permissionDecision' not in default
    assert 'Possible actuation' in default['additionalContext']
    assert 'Skipped: actuation:' in default['additionalContext']
    asked = json.loads(_adapter(LOW_AND_UNREAD, 'ask').stdout)['hookSpecificOutput']
    assert asked['permissionDecision'] == 'ask'


LOW_AND_UNREAD = "ros2 action send_goal /do_thing my_pkg/action/Thing '{}'; bash -c \"$CMD\""


@pytest.mark.parametrize('command', [PUB, NAV, 'eval "ros2 topic pub /cmd_vel $MSG"',
                                     "bash -lc '" + PUB.replace("'", '') + "'", BUILT,
                                     LOW_AND_UNREAD])
def test_ask_mode_requests_confirmation_and_never_grants(command):
    result = _adapter(command, 'ask')
    assert result.returncode == 0
    output = json.loads(result.stdout)['hookSpecificOutput']
    assert output['permissionDecision'] == 'ask'
    assert 'not a safety function' in output['permissionDecisionReason']
    assert set(output) == {'hookEventName', 'permissionDecision',
                           'permissionDecisionReason', 'additionalContext'}
    assert output['additionalContext'].endswith(
        output['permissionDecisionReason'].split('\n', 1)[1])
    assert 'allow' not in result.stdout


@pytest.mark.parametrize('command', [
    'ros2 lifecycle set /driver activate',                     # low confidence
    'ros2 action send_goal /do_thing my_pkg/action/Thing {}',  # low confidence
])
def test_ask_mode_keeps_low_confidence_findings_advisory(command):
    result = _adapter(command, 'ask')
    assert 'permissionDecision' not in result.stdout
    assert 'Possible actuation' in json.loads(result.stdout)[
        'hookSpecificOutput']['additionalContext']


@pytest.mark.parametrize('command', [
    'ros2 topic list', 'grep "ros2 topic pub /cmd_vel" notes.md', 'ls -la',
    'ros2 topic pub /chatter std_msgs/msg/String "data: hi"'])
@pytest.mark.parametrize('mode', [None, 'ask'])
def test_clean_commands_stay_silent_in_every_mode(command, mode):
    result = _adapter(command, mode)
    assert result.returncode == 0 and json.loads(result.stdout) == {}


def test_off_mode_is_silent():
    result = _adapter(PUB, 'off')
    assert result.returncode == 0 and json.loads(result.stdout) == {}


def test_blocking_exit_takes_precedence_over_ask():
    result = _adapter(PUB + '; rm -rf /', 'ask')
    assert result.returncode == 2 and not result.stdout and 'rm -rf' in result.stderr


def test_unparsed_powershell_command_asks_instead_of_passing():
    result = _adapter('ros2 topic pub /cmd_vel x', 'ask', tool='PowerShell')
    output = json.loads(result.stdout)['hookSpecificOutput']
    assert output['permissionDecision'] == 'ask'
    assert 'only POSIX shell commands are analyzed' in output['permissionDecisionReason']


def test_adapter_reads_the_structured_field_not_the_message(monkeypatch):
    monkeypatch.setenv(adapter.ACTUATION_ENV, 'ask')
    base = {'status': 'pass', 'issues': [], 'checks_skipped': []}
    # A message that merely mentions actuation does not ask.
    worded = {**base, 'issues': [{'severity': 'warning', 'message': 'Actuation command: x'}]}
    assert not adapter.needs_confirmation(worded)
    assert not adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'none', 'confidence': None, 'reasons': []}})
    assert not adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'detected', 'confidence': 'low', 'reasons': []}})
    assert adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'detected', 'confidence': 'low', 'reasons': ['r']}})
    assert adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'detected', 'confidence': 'high', 'reasons': ['r']}})
    assert not adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'off', 'confidence': None, 'reasons': []}})
    assert adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'detected', 'confidence': 'high', 'reasons': []}})
    assert adapter.needs_confirmation(
        {**base, 'actuation': {'status': 'not_evaluated', 'confidence': None, 'reasons': ['r']}})


@pytest.mark.parametrize('actuation', [
    'detected', [], {'status': 'unknown', 'confidence': None, 'reasons': []},
    {'status': 'detected', 'confidence': 'certain', 'reasons': []},
    {'status': 'detected', 'confidence': 'high', 'reasons': 'text'},
    {'confidence': 'high', 'reasons': []}])
def test_malformed_actuation_result_is_reported_as_skipped(monkeypatch, actuation):
    report = {'status': 'pass', 'issues': [], 'checks_skipped': [], 'actuation': actuation}
    with pytest.raises(ValueError):
        adapter.needs_confirmation(report)
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(
        args, 0, stdout=json.dumps(report), stderr=''))
    output, error, status = adapter.run_hook('PreToolUse', _bash(PUB))
    assert status == 0 and not error
    assert 'skipped: invalid validator output' in output['systemMessage']


def test_actuation_mode_parsing(monkeypatch):
    for raw, expected in (('ask', 'ask'), (' ASK ', 'ask'), ('off', 'off'),
                          ('advise', 'advise'), ('', 'advise'), ('yes', 'advise')):
        monkeypatch.setenv(adapter.ACTUATION_ENV, raw)
        assert adapter.actuation_mode() == expected
    monkeypatch.delenv(adapter.ACTUATION_ENV)
    assert adapter.actuation_mode() == 'advise'
    assert adapter.ACTUATION_ENV == hook.ACTUATION_ENV


def test_arbitrary_shell_text_never_crashes_the_analysis():
    hypothesis = pytest.importorskip('hypothesis')
    strategies = pytest.importorskip('hypothesis.strategies')
    atoms = strategies.sampled_from([
        'ros2', ' ', 'topic', 'pub', '/cmd_vel', "'", '"', '$(', ')', '`', '\\', '\n', ';',
        '&&', '|', '&', '<<', '<<-', 'EOF', '<', '>', '(', '{', '}', '$', '${', '$((', "$'",
        '#', 'bash', '-c', '-lc', 'ssh', 'eval', 'sudo', 'docker', 'exec', 'cat', '<<<',
        '2>', '&>', 'x=1', TWIST, 'action', 'send_goal', 'tmux', '--', '<(', '||', '!'])

    @hypothesis.settings(max_examples=400, deadline=None)
    @hypothesis.given(strategies.lists(atoms, max_size=40), strategies.text(max_size=20))
    def check(parts, noise):
        result = analyze('ros2 ' + ''.join(parts) + noise)
        assert result['status'] in ('none', 'detected', 'not_evaluated')
        assert (result['status'] == 'detected') == bool(result['issues'])
        assert all(isinstance(reason, str) for reason in result['reasons'])
        if result['status'] == 'not_evaluated':
            assert result['reasons']

    check()
