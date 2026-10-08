"""Both ros2_control API paths of the generated hardware package, checked without ROS.

The generator emits one source with two preprocessor paths: the manual
``export_*_interfaces()`` path for Humble 2.x and the framework-managed path
for Jazzy 4.x and newer (ros2_control 6.12 removed the manual methods). These
tests evaluate each path separately and run the framework-managed control
loop against a stub of the upstream handle API. No ROS installation, model, or
hardware is involved; the five-distro CI job compiles the real thing.
"""

import re
import shutil
import subprocess

import pytest

from scripts.create_package import create_hardware_interface_package

MACRO = 'HARDWARE_INTERFACE_HAS_ON_EXPORT_INTERFACES'
LEGACY_ONLY = ('export_state_interfaces', 'export_command_interfaces',
               '&hw_positions_[', '&hw_velocities_[', '&hw_commands_[')
MODERN_ONLY = ('get_state_interface_handle(', 'get_command_interface_handle(',
               'std::isfinite(', 'position_commands_', 'missed_state_updates_')


def preprocess(text, defined):
    """Resolve #ifdef/#ifndef/#else/#endif for the given macro set; keep everything else."""
    kept, stack = [], []  # stack entries: (currently_emitting, any_branch_taken)
    for line in text.splitlines():
        directive = line.strip()
        emitting = all(frame[0] for frame in stack)
        if directive.startswith(('#ifdef ', '#ifndef ')):
            macro = directive.split()[1]
            take = (macro in defined) == directive.startswith('#ifdef ')
            stack.append((emitting and take, take))
        elif directive == '#else':
            parent = all(frame[0] for frame in stack[:-1])
            _, taken = stack.pop()
            stack.append((parent and not taken, True))
        elif directive == '#endif':
            stack.pop()
        elif emitting:
            kept.append(line)
    assert not stack, 'unbalanced preprocessor conditionals'
    return '\n'.join(kept) + '\n'


def generated(tmp_path, name='api_probe'):
    create_hardware_interface_package(name, tmp_path)
    pkg = tmp_path / name
    header = (pkg / 'include' / name / (name + '_hardware.hpp')).read_text(encoding='utf-8')
    source = (pkg / 'src' / (name + '_hardware.cpp')).read_text(encoding='utf-8')
    cmake = (pkg / 'CMakeLists.txt').read_text(encoding='utf-8')
    return header, source, cmake


def method_body(source, name):
    start = source.index('ApiProbeHardware::%s(' % name)
    end = source.index('\n}\n', start)
    return source[start:end]


def test_cmake_probes_the_api_instead_of_comparing_versions(tmp_path):
    _, _, cmake = generated(tmp_path)
    probe_dir = tmp_path / 'api_probe' / 'cmake' / 'hw_api_probe'
    probe_cmake = (probe_dir / 'CMakeLists.txt').read_text(encoding='utf-8')
    probe_source = (probe_dir / 'main.cpp').read_text(encoding='utf-8')
    # A separate C and C++ project: the ROS imported targets need both languages, and a
    # C++-only check_cxx_source_compiles() scratch project fails on CMake 4.x.
    assert 'try_compile(HW_API_PROBE_COMPILED' in cmake
    assert '${CMAKE_CURRENT_SOURCE_DIR}/cmake/hw_api_probe' in cmake
    assert 'check_cxx_source_compiles' not in cmake
    assert 'project(HardwareApiProbe LANGUAGES C CXX)' in probe_cmake
    assert 'hardware_interface::hardware_interface' in probe_cmake
    assert 'on_export_state_interfaces() override' in probe_source
    # The probe exercises every framework-managed accessor the source relies on.
    for accessor in ('get_state_interface_handle(', 'get_command_interface_handle(',
                     'set_state(state, value, false)', 'get_command(command, value, false)',
                     'set_command(command, value, false)'):
        assert accessor in probe_source, accessor
    assert probe_source.startswith('// Copyright')
    # No C++ inside the CMake text, and every CMake line is 2-space indented and short.
    for path, text in (('CMakeLists.txt', cmake), ('cmake/hw_api_probe/CMakeLists.txt', probe_cmake)):
        for number, line in enumerate(text.splitlines(), 1):
            indent = len(line) - len(line.lstrip(' '))
            assert indent % 2 == 0 and len(line) <= 100, '%s:%d: %r' % (path, number, line)
    # The cache contract the container gates assert per distro.
    assert 'set(%s 1 CACHE INTERNAL' % MACRO in cmake
    assert 'set(%s "" CACHE INTERNAL' % MACRO in cmake
    assert 'add_definitions(-D%s)' % MACRO in cmake
    assert 'set(CMAKE_CXX_STANDARD 17)' in cmake
    assert 'hardware_interface_VERSION VERSION_GREATER_EQUAL' not in cmake.split('try_compile(')[1]


def probe_block(cmake):
    start = cmake.index('try_compile(HW_API_PROBE_COMPILED')
    end = cmake.index('endif()', start) + len('endif()')
    return cmake[start:end] + '\n'


@pytest.mark.parametrize('outcome', ['compiles', 'fails'])
def test_probe_wiring_records_the_cache_contract(tmp_path, outcome):
    """Run the real try_compile() block with plain CMake; ROS is not needed to prove the wiring."""
    cmake_tool = shutil.which('cmake')
    if cmake_tool is None:
        pytest.skip('cmake is required for the probe wiring check')
    _, _, cmake = generated(tmp_path)
    parent = tmp_path / 'parent'
    (parent / 'cmake' / 'hw_api_probe').mkdir(parents=True)
    (parent / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.8)\nproject(Parent LANGUAGES C CXX)\n' + probe_block(cmake),
        encoding='utf-8')
    if outcome == 'compiles':
        # A probe that builds stands in for a ros2_control that offers the API.
        (parent / 'cmake' / 'hw_api_probe' / 'CMakeLists.txt').write_text(
            'cmake_minimum_required(VERSION 3.8)\nproject(HardwareApiProbe LANGUAGES C CXX)\n'
            'add_executable(hw_api_probe main.cpp)\n', encoding='utf-8')
        (parent / 'cmake' / 'hw_api_probe' / 'main.cpp').write_text('int main() { return 0; }\n', encoding='utf-8')
        expected = MACRO + ':INTERNAL=1'
    else:
        # The generated probe itself: without ROS its find_package() fails, like Humble's API would.
        generated_probe = tmp_path / 'api_probe' / 'cmake' / 'hw_api_probe'
        for name in ('CMakeLists.txt', 'main.cpp'):
            (parent / 'cmake' / 'hw_api_probe' / name).write_bytes((generated_probe / name).read_bytes())
        expected = MACRO + ':INTERNAL='
    build = parent / 'build'
    run = subprocess.run([cmake_tool, '-S', str(parent), '-B', str(build)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    cache = (build / 'CMakeCache.txt').read_text(encoding='utf-8').splitlines()
    assert [line for line in cache if line.startswith(MACRO + ':INTERNAL=')] == [expected]
    assert (build / 'hw_api_probe.log').exists()
    if outcome == 'fails':
        assert 'hardware_interface' in (build / 'hw_api_probe.log').read_text(encoding='utf-8')


def test_modern_path_never_uses_the_removed_api(tmp_path):
    header, source, _ = generated(tmp_path)
    modern = preprocess(header, {MACRO}) + preprocess(source, {MACRO})
    for token in LEGACY_ONLY:
        assert token not in modern, token
    for token in MODERN_ONLY:
        assert token in modern, token
    assert 'HARDWARE_INTERFACE_HAS_PARAMS_API' in header  # the on_init gate is untouched


def test_legacy_path_never_uses_the_framework_managed_api(tmp_path):
    header, source, _ = generated(tmp_path)
    legacy = preprocess(header, set()) + preprocess(source, set())
    for token in ('export_state_interfaces', 'export_command_interfaces', '&hw_positions_['):
        assert token in legacy, token
    for token in MODERN_ONLY + ('set_state(', 'get_command(', 'set_command(', 'on_export'):
        assert token not in legacy, token


def test_control_loop_uses_cached_handles_and_non_blocking_access_only(tmp_path):
    """read()/write() must not look interfaces up by name (upstream: not real-time safe)."""
    _, source, _ = generated(tmp_path)
    modern = preprocess(source, {MACRO})
    for name in ('read', 'write'):
        body = method_body(modern, name)
        assert 'get_state_interface_handle(' not in body and 'get_command_interface_handle(' not in body
        # Name-based helpers take a string first; the handle overloads take a cached handle.
        name_based = r'(set_state|set_command|get_command)\(\s*(info_|"|name\b|position\b|velocity\b)'
        assert not re.search(name_based, body), name
        assert not re.search(r'(set_state|set_command|get_command)\([^;]*,\s*true\)', body), name
    assert 'get_state_interface_handle(' in method_body(modern, 'on_configure')
    assert 'std::isfinite(command)' in method_body(modern, 'write')


STUB = r'''
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#define RCLCPP_INFO(...) ((void)0)
namespace rclcpp {
struct Time {};
struct Duration {};
inline int get_logger(const char *) { return 0; }
}  // namespace rclcpp
namespace rclcpp_lifecycle { struct State {}; }
namespace hardware_interface {
constexpr char HW_IF_POSITION[] = "position";
constexpr char HW_IF_VELOCITY[] = "velocity";
enum class return_type { OK, ERROR };
enum class CallbackReturn { SUCCESS, ERROR };
struct Handle {
  double value = std::numeric_limits<double>::quiet_NaN();
  bool locked = false;  // simulates a contended try_lock
};
struct StateInterface : Handle { using SharedPtr = std::shared_ptr<StateInterface>; };
struct CommandInterface : Handle { using SharedPtr = std::shared_ptr<CommandInterface>; };
}  // namespace hardware_interface

struct Joint { std::string name; };
struct Info { std::vector<Joint> joints; };

class ApiProbeHardware {
public:
  hardware_interface::CallbackReturn on_configure(const rclcpp_lifecycle::State &);
  hardware_interface::CallbackReturn on_activate(const rclcpp_lifecycle::State &);
  hardware_interface::CallbackReturn on_deactivate(const rclcpp_lifecycle::State &);
  hardware_interface::CallbackReturn on_cleanup(const rclcpp_lifecycle::State &);
  hardware_interface::return_type read(const rclcpp::Time &, const rclcpp::Duration &);
  hardware_interface::return_type write(const rclcpp::Time &, const rclcpp::Duration &);

  Info info_;
  std::unordered_map<std::string, int> joint_state_interfaces_;
  std::unordered_map<std::string, int> joint_command_interfaces_;
  std::map<std::string, hardware_interface::StateInterface::SharedPtr> state_handles_;
  std::map<std::string, hardware_interface::CommandInterface::SharedPtr> command_handles_;
  std::size_t name_lookups_ = 0;

  const hardware_interface::StateInterface::SharedPtr & get_state_interface_handle(const std::string & n)
  { ++name_lookups_; return state_handles_.at(n); }
  const hardware_interface::CommandInterface::SharedPtr & get_command_interface_handle(const std::string & n)
  { ++name_lookups_; return command_handles_.at(n); }
  template <typename H, typename T>
  bool set_value(const std::shared_ptr<H> & h, const T & v, bool wait)
  {
    if (!h) throw std::runtime_error("null handle");
    if (h->locked && !wait) return false;
    h->value = v;
    return true;
  }
  template <typename H, typename T>
  bool get_value(const std::shared_ptr<H> & h, T & out, bool wait) const
  {
    if (!h) throw std::runtime_error("null handle");
    if (h->locked && !wait) return false;
    out = h->value;
    return true;
  }
  template <typename T>
  bool set_state(const hardware_interface::StateInterface::SharedPtr & h, const T & v, bool wait)
  { return set_value(h, v, wait); }
  template <typename T>
  bool set_command(const hardware_interface::CommandInterface::SharedPtr & h, const T & v, bool wait)
  { return set_value(h, v, wait); }
  template <typename T>
  bool get_command(const hardware_interface::CommandInterface::SharedPtr & h, T & out, bool wait) const
  { return get_value(h, out, wait); }

  std::vector<double> hw_positions_;
  std::vector<double> hw_velocities_;
  std::vector<double> hw_commands_;
  std::vector<hardware_interface::StateInterface::SharedPtr> position_states_;
  std::vector<hardware_interface::StateInterface::SharedPtr> velocity_states_;
  std::vector<hardware_interface::CommandInterface::SharedPtr> position_commands_;
  std::size_t missed_state_updates_{0};
};
'''

MAIN = r'''
#define CHECK(cond, code) do { if (!(cond)) return (code); } while (0)
int main()
{
  using hardware_interface::CallbackReturn;
  using hardware_interface::return_type;
  const rclcpp_lifecycle::State state{};
  const rclcpp::Time t{};
  const rclcpp::Duration d{};
  ApiProbeHardware hw;
  hw.info_.joints = {{"j1"}, {"j2"}};
  hw.hw_positions_ = {0.0, 0.0};
  hw.hw_velocities_ = {0.0, 0.0};
  hw.hw_commands_ = {0.0, 0.0};
  // j1: position + velocity state, position command. j2: position state only, position command.
  for (const char * n : {"j1/position", "j1/velocity", "j2/position"}) {
    hw.joint_state_interfaces_[n] = 1;
    hw.state_handles_[n] = std::make_shared<hardware_interface::StateInterface>();
  }
  for (const char * n : {"j1/position", "j2/position"}) {
    hw.joint_command_interfaces_[n] = 1;
    hw.command_handles_[n] = std::make_shared<hardware_interface::CommandInterface>();
  }
  CHECK(hw.on_configure(state) == CallbackReturn::SUCCESS, 10);
  CHECK(hw.position_states_[1] && hw.velocity_states_[1] == nullptr && hw.position_commands_[1], 11);
  const std::size_t lookups_after_configure = hw.name_lookups_;
  CHECK(lookups_after_configure == 5, 12);

  // Commands are NaN until a controller writes; activation syncs them to the measured position.
  hw.hw_positions_ = {0.3, -0.7};
  CHECK(std::isnan(hw.command_handles_["j1/position"]->value), 20);
  CHECK(hw.on_activate(state) == CallbackReturn::SUCCESS, 21);
  CHECK(hw.command_handles_["j1/position"]->value == 0.3 && hw.command_handles_["j2/position"]->value == -0.7, 22);
  CHECK(hw.state_handles_["j1/position"]->value == 0.3 && hw.state_handles_["j2/position"]->value == -0.7, 23);
  CHECK(hw.hw_commands_[0] == 0.3 && hw.hw_commands_[1] == -0.7, 24);

  // A NaN command (no controller target yet, or an invalid one) never becomes a target or a state.
  hw.command_handles_["j1/position"]->value = std::numeric_limits<double>::quiet_NaN();
  hw.command_handles_["j2/position"]->value = 1.5;
  CHECK(hw.write(t, d) == return_type::OK, 30);
  CHECK(hw.hw_commands_[0] == 0.3 && hw.hw_commands_[1] == 1.5, 31);
  CHECK(hw.read(t, d) == return_type::OK, 32);
  CHECK(hw.hw_positions_[0] == 0.3 && hw.hw_positions_[1] == 1.5, 33);
  for (const auto & entry : hw.state_handles_) CHECK(std::isfinite(entry.second->value), 34);
  CHECK(hw.state_handles_["j2/position"]->value == 1.5, 35);

  // Contended handles: write keeps the last valid command, read skips and counts, nothing blocks.
  hw.command_handles_["j1/position"]->locked = true;
  hw.command_handles_["j1/position"]->value = 9.9;
  CHECK(hw.write(t, d) == return_type::OK && hw.hw_commands_[0] == 0.3, 40);
  hw.state_handles_["j1/position"]->locked = true;
  hw.hw_commands_[0] = 0.4;  // device buffer moves; the locked state handle must keep 0.3
  CHECK(hw.read(t, d) == return_type::OK, 41);
  CHECK(hw.missed_state_updates_ == 1 && hw.state_handles_["j1/position"]->value == 0.3, 42);
  CHECK(hw.state_handles_["j1/velocity"]->value == 0.0, 43);
  hw.command_handles_["j1/position"]->locked = false;
  hw.state_handles_["j1/position"]->locked = false;

  // The control loop never looked a handle up by name.
  CHECK(hw.name_lookups_ == lookups_after_configure, 50);

  // Deactivation holds the measured positions on the framework side too.
  CHECK(hw.on_deactivate(state) == CallbackReturn::SUCCESS, 60);
  CHECK(hw.command_handles_["j1/position"]->value == hw.hw_positions_[0], 61);
  CHECK(hw.command_handles_["j2/position"]->value == hw.hw_positions_[1], 62);
  CHECK(hw.on_cleanup(state) == CallbackReturn::SUCCESS, 70);
  CHECK(hw.position_states_.empty() && hw.velocity_states_.empty() && hw.position_commands_.empty(), 71);
  return 0;
}
'''


def test_framework_managed_loop_against_a_handle_stub(tmp_path):
    compiler = shutil.which('g++') or shutil.which('clang++')
    if compiler is None:
        pytest.skip('A C++ compiler is required for the emitted-loop regression')
    _, source, _ = generated(tmp_path)
    start = source.index('hardware_interface::CallbackReturn ApiProbeHardware::on_configure(')
    end = source.index('}  // namespace api_probe', start)
    fixture = tmp_path / 'loop_probe.cpp'
    fixture.write_text(STUB + source[start:end] + MAIN, encoding='utf-8')
    executable = tmp_path / 'loop_probe'
    built = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-D' + MACRO, str(fixture), '-o', str(executable)],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    executed = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert executed.returncode == 0, 'scenario check %s failed' % executed.returncode
