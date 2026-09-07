#!/usr/bin/env python3
"""Execute the trusted repository's documented lifecycle examples on real ROS.

Requires a sourced ROS environment, sensor_msgs, and CMake/C++ build tools.
No user-supplied source, model invocation, or physical hardware is involved.
"""

import gc
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import weakref


ROOT = Path(__file__).resolve().parents[1]


def example(text, heading, language):
    """Select exactly the first fenced example under an unambiguous heading."""
    marker = '## ' + heading + '\n'
    if text.count(marker) != 1:
        raise ValueError('Missing or ambiguous example heading: ' + heading)
    section = text.split(marker, 1)[1].split('\n## ', 1)[0]
    fence = '```' + language + '\n'
    if fence not in section:
        raise ValueError('Missing example fence: ' + heading)
    remainder = section.split(fence, 1)[1]
    if '\n```' not in remainder:
        raise ValueError('Unclosed example fence: ' + heading)
    source = remainder.split('\n```', 1)[0] + '\n'
    if not source.strip():
        raise ValueError('Empty example: ' + heading)
    return source


def check_python(source):
    import rclpy
    from rclpy.context import Context
    from rclpy.lifecycle import TransitionCallbackReturn as Return

    namespace = {'__name__': 'lifecycle_reference_under_test'}
    exec(compile(source, str(ROOT / 'references/lifecycle-components.md'), 'exec'), namespace)
    node_class = namespace['LidarProcessor']
    context = Context()
    rclpy.init(context=context)
    nodes, records, retired = [], [], []

    def make(cls):
        node = cls(context=context, namespace='/reference_' + uuid.uuid4().hex)
        nodes.append(node)
        return node

    def require_released(node):
        gc.collect()
        if node._scan_pub is not None or node._scan_sub is not None:
            raise RuntimeError('Example retained its publisher/subscription fields')
        if any(ref() is not None for ref in retired):
            raise RuntimeError('Destroyed lifecycle publisher is still retained')

    try:
        node = make(node_class)
        for trial in range(3):
            for name in ('configure', 'activate', 'deactivate'):
                if getattr(node, 'trigger_' + name)() != Return.SUCCESS:
                    raise RuntimeError('Example transition failed: ' + name)
            retired.append(weakref.ref(node._scan_pub))
            if node.trigger_cleanup() != Return.SUCCESS:
                raise RuntimeError('Example cleanup failed')
            require_released(node)
            records.append({'case': 'cleanup-reconfigure', 'trial': trial + 1,
                            'retained_publishers': 0})
        if node.trigger_configure() != Return.SUCCESS or node.trigger_activate() != Return.SUCCESS:
            raise RuntimeError('Example failed active-shutdown setup')
        retired.append(weakref.ref(node._scan_pub))
        if node.trigger_shutdown() != Return.SUCCESS:
            raise RuntimeError('Example shutdown failed')
        require_released(node)
        records.append({'case': 'active-shutdown', 'retained_publishers': 0})

        class ConfigurationError(node_class):
            def on_configure(self, state):
                super().on_configure(state)
                retired.append(weakref.ref(self._scan_pub))
                return Return.ERROR

        failing = make(ConfigurationError)
        if failing.trigger_configure() != Return.ERROR:
            raise RuntimeError('Deliberate configuration ERROR was not observed')
        require_released(failing)
        records.append({'case': 'configuration-error-cleanup', 'retained_publishers': 0})
        return records
    finally:
        try:
            for node in nodes:
                node.destroy_node()
        finally:
            context.try_shutdown()


def check_cpp(source):
    with tempfile.TemporaryDirectory(prefix='lifecycle-reference-') as temporary:
        work = Path(temporary)
        for name in ('CMakeLists.txt', 'main.cpp'):
            shutil.copyfile(ROOT / 'tests/lifecycle_reference' / name, work / name)
        (work / 'reference_node.hpp').write_text(source, encoding='utf-8')
        subprocess.run(['cmake', '-S', str(work), '-B', str(work / 'build')],
                       check=True, timeout=40)
        subprocess.run(['cmake', '--build', str(work / 'build'), '--parallel', '2'],
                       check=True, timeout=90)
        subprocess.run([str(work / 'build/reference_probe'), '--ros-args', '-r',
                        '__ns:=/reference_' + uuid.uuid4().hex], check=True, timeout=20)
    return {'case': 'cpp-filtered-output', 'status': 'pass'}


def main():
    text = (ROOT / 'references/lifecycle-components.md').read_text(encoding='utf-8')
    python_source = example(text, '3. Implementing lifecycle transitions (rclpy)', 'python')
    cpp_source = example(text, '2. Implementing lifecycle transitions (rclcpp)', 'cpp')
    python_results = check_python(python_source)
    cpp_result = check_cpp(cpp_source)
    import rclpy
    print(json.dumps({'status': 'pass', 'rmw': rclpy.get_rmw_implementation_identifier(),
                      'python': python_results, 'cpp': cpp_result,
                      'scope': 'Actual documented examples; no hardware or model-quality claim'},
                     indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
