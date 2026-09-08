"""Compile the emitted deactivation body in isolation; no actuator-safety claim."""

import shutil
import subprocess

import pytest

from scripts.create_package import create_hardware_interface_package


@pytest.mark.parametrize('positions', [[], [2.5], [-1.25, 0.0, 3.75]])
def test_emitted_position_deactivation_holds_measurements_not_zero(tmp_path, positions):
    compiler = shutil.which('g++') or shutil.which('clang++')
    if compiler is None:
        pytest.skip('A C++ compiler is required for the emitted-body regression')
    create_hardware_interface_package('hold_probe', tmp_path)
    source = (tmp_path / 'hold_probe/src/hold_probe_hardware.cpp').read_text()
    start = source.index('hardware_interface::CallbackReturn HoldProbeHardware::on_deactivate(')
    end = source.index('hardware_interface::CallbackReturn HoldProbeHardware::on_cleanup(', start)
    method = source[start:end]
    assert '#include <algorithm>' in source
    assert 'position targets' in method and 'not a stop' in method
    values = ', '.join(repr(value) for value in positions)
    fixture = tmp_path / 'position_hold.cpp'
    fixture.write_text('''#include <algorithm>
#include <vector>
#define RCLCPP_INFO(...) ((void)0)
namespace hardware_interface { enum class CallbackReturn { SUCCESS }; }
namespace rclcpp_lifecycle { struct State {}; }
class HoldProbeHardware {
public:
  std::vector<double> hw_positions_;
  std::vector<double> hw_commands_;
  hardware_interface::CallbackReturn on_deactivate(const rclcpp_lifecycle::State &);
};
''' + method + '''
int main() {
  HoldProbeHardware hw;
  const std::vector<double> observed = {''' + values + '''};
  hw.hw_positions_ = observed;
  hw.hw_commands_.assign(observed.size(), 100.0);
  if (hw.on_deactivate(rclcpp_lifecycle::State{}) !=
      hardware_interface::CallbackReturn::SUCCESS) return 1;
  if (hw.hw_positions_ != observed) return 2;
  if (hw.hw_commands_ != observed) return 3;
  return 0;
}
''', encoding='utf-8')
    executable = tmp_path / 'position_hold'
    built = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(fixture), '-o', str(executable)],
                           capture_output=True, text=True, timeout=20)
    assert built.returncode == 0, built.stdout + built.stderr
    executed = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
    assert executed.returncode == 0, executed.stdout + executed.stderr
