"""Execute the documented permit decision; no DDS or hardware-stop claim."""

from pathlib import Path
import fnmatch
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest


@pytest.mark.parametrize('initially_revoked, readings, expected', [
    (True, [True, True], [False, False]),
    (False, [True, True], [True, True]),
    (False, [True, False, True, True], [True, False, False, False]),
])
def test_supervisor_retains_revocation_through_healthy_readings(
        tmp_path, initially_revoked, readings, expected):
    compiler = shutil.which('g++') or shutil.which('clang++')
    if compiler is None:
        pytest.skip('A C++ compiler is required for the documented-body regression')
    text = (Path(__file__).resolve().parents[1]
            / 'references/safety-estop.md').read_text(encoding='utf-8')
    block = next(code for code in re.findall(r'```cpp\n(.*?)```', text, re.S)
                 if 'class SafetySupervisor' in code)
    match = re.search(r'timer_ = create_wall_timer\(200ms, \[this\] \{(.*?)\n    \}\);',
                      block, re.S)
    assert match is not None, 'No supervisor decision callback was extracted'
    body = match.group(1)

    def cpp(values):
        return ', '.join('true' if value else 'false' for value in values)

    # The initial false latch models a previously authorized rearm. The callback
    # itself must never rearm. A later healthy decision must retain revocation
    # even when a consumer did not take the earlier revoked sample.
    fixture = tmp_path / 'permit_decision.cpp'
    fixture.write_text('''#include <vector>
namespace std_msgs { namespace msg { struct Bool { bool data; }; } }
struct Publisher {
  std::vector<bool> decisions;
  void publish(const std_msgs::msg::Bool & message) { decisions.push_back(message.data); }
};
int main() {
  Publisher publisher;
  auto * permit_pub_ = &publisher;
  bool revoked_ = ''' + cpp([initially_revoked]) + ''';
  (void)revoked_;
  bool healthy = true;
  auto checks_pass = [&healthy] { return healthy; };
  auto decision = [&] {
''' + body + '''
  };
  const std::vector<bool> readings = {''' + cpp(readings) + '''};
  for (bool value : readings) { healthy = value; decision(); }
  const std::vector<bool> expected = {''' + cpp(expected) + '''};
  if (publisher.decisions != expected) { return 1; }
}
''', encoding='utf-8')
    executable = tmp_path / 'permit_decision'
    built = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(fixture), '-o', str(executable)],
                           capture_output=True, text=True, timeout=20)
    assert built.returncode == 0, built.stdout + built.stderr
    executed = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
    assert executed.returncode == 0, executed.stdout + executed.stderr


def safety_policy():
    text = (Path(__file__).resolve().parents[1]
            / 'references/safety-estop.md').read_text(encoding='utf-8')
    source = next(block for block in re.findall(r'```xml\n(.*?)```', text, re.S)
                  if '<policy ' in block)
    return ET.fromstring(source)


def allowed_enclaves(policy, kind, operation, name):
    """Resolve the shipped policy's namespace-scoped ALLOW expressions."""
    allowed = set()
    for enclave in policy.findall('./enclaves/enclave'):
        for profile in enclave.findall('./profiles/profile'):
            prefix = profile.attrib['ns'].rstrip('/')
            for rules in profile.findall(kind):
                if rules.get(operation) != 'ALLOW':
                    continue
                for expression in rules:
                    pattern = expression.text or ''
                    resolved = pattern if pattern.startswith('/') else prefix + '/' + pattern
                    if fnmatch.fnmatchcase(name, resolved):
                        allowed.add(enclave.attrib['path'])
    return allowed


@pytest.mark.parametrize('topic, owner', [
    ('/cmd_vel', '/estop_gate'),
    ('/cmd_vel_selected', '/twist_mux'),
    ('/safety/motion_permit', '/safety_supervisor'),
    ('/safety/stop_ack', '/estop_gate'),
])
def test_policy_preserves_each_protected_writer(topic, owner):
    # Broad publish grants count too; an extra writer is a bypass, not a match.
    assert allowed_enclaves(safety_policy(), 'topics', 'publish', topic) == {owner}


def test_policy_allows_ack_and_reset_without_granting_other_reset_callers():
    policy = safety_policy()
    assert '/safety_supervisor' in allowed_enclaves(
        policy, 'topics', 'subscribe', '/safety/stop_ack')
    assert '/estop_gate' in allowed_enclaves(
        policy, 'topics', 'subscribe', '/safety/motion_permit')
    assert allowed_enclaves(policy, 'services', 'request', '/estop_gate/reset') == {
        '/safety_supervisor'}
    assert allowed_enclaves(policy, 'services', 'reply', '/estop_gate/reset') == {
        '/estop_gate'}
