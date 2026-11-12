#!/usr/bin/env python3
"""Execute the QoS event example that create_package.py writes into Python nodes.

The example is a commented block in the generated node source. This check does
not keep its own copy: it reads the block from a generated package, removes the
comment markers and runs that code, so a wrong module, class or argument in the
generator fails here.

    check_qos_event_callbacks.py PACKAGE_DIR            imports and signatures
    check_qos_event_callbacks.py PACKAGE_DIR --runtime  also creates the endpoints

The default mode never calls rclpy.init(), so it also runs on a distribution
whose node-creation gates are excluded. --runtime creates a real subscription
and publisher with the callbacks attached and destroys them again.
"""

import argparse
import ast
import inspect
from pathlib import Path
import sys

BEGIN = '# --- qos event example: begin ---'
END = '# --- qos event example: end ---'
FACTORIES = ('create_subscription', 'create_publisher')


def extract_example(source):
    """Return the example between the markers with its comment prefix removed."""
    lines = source.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [index for index, line in enumerate(lines) if line.strip() == END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0] + 1:
        raise ValueError('expected exactly one non-empty QoS event example block')
    code = []
    for line in lines[starts[0] + 1:ends[0]]:
        text = line.strip()
        if not text.startswith('#'):
            raise ValueError('example line is not a comment: ' + line)
        code.append(text[2:] if text.startswith('# ') else text[1:])
    return '\n'.join(code) + '\n'


def node_source(package_dir):
    paths = sorted(Path(package_dir).glob('*/*_node.py'))
    if len(paths) != 1:
        raise ValueError('expected one generated node module under ' + str(package_dir))
    return paths[0].read_text(encoding='utf-8')


def example_calls(tree):
    """The endpoint factory calls the example makes, by factory name."""
    calls = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in FACTORIES):
            calls[node.func.attr] = node
    return calls


def check_static(code):
    """Imports resolve, the callback containers accept the argument, and the
    factories take every keyword the example passes. No ROS context is used."""
    tree = ast.parse(code)
    imports = ast.Module(body=[node for node in tree.body if isinstance(
        node, (ast.Try, ast.Import, ast.ImportFrom))], type_ignores=[])
    namespace = {}
    exec(compile(imports, '<qos event example: imports>', 'exec'), namespace)

    def callback(event):
        return None

    for name in ('SubscriptionEventCallbacks', 'PublisherEventCallbacks'):
        container = namespace[name](incompatible_qos=callback)
        if container.incompatible_qos is not callback:
            raise AssertionError(name + ' did not keep the incompatible_qos callback')

    from rclpy.node import Node
    calls = example_calls(tree)
    if set(calls) != set(FACTORIES):
        raise AssertionError('example must create a subscription and a publisher')
    for name, call in calls.items():
        parameters = inspect.signature(getattr(Node, name)).parameters
        keywords = [keyword.arg for keyword in call.keywords]
        if 'event_callbacks' not in keywords:
            raise AssertionError(name + ' call does not pass event_callbacks')
        missing = [keyword for keyword in keywords if keyword not in parameters]
        if missing:
            raise AssertionError('%s has no parameter %s' % (name, ', '.join(missing)))
    return sorted(namespace[name].__module__ for name in (
        'SubscriptionEventCallbacks', 'PublisherEventCallbacks'))


def check_runtime(code):
    """Run the whole example inside a real node's __init__."""
    import rclpy
    from rclpy.node import Node

    class Probe(Node):
        def __init__(self):
            super().__init__('qos_event_example_probe')
            exec(compile(code, '<qos event example>', 'exec'), {'self': self})

        def on_qos_event(self, event):
            return None

    rclpy.init()
    try:
        node = Probe()
        try:
            created = (node.example_sub, node.example_pub)
            if any(endpoint is None for endpoint in created):
                raise AssertionError('example did not create both endpoints')
            if not node.example_sub.event_handlers or not node.example_pub.event_handlers:
                raise AssertionError('no QoS event handler was attached')
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('package_dir')
    parser.add_argument('--runtime', action='store_true',
                        help='also create the endpoints; needs a working ROS context')
    args = parser.parse_args(argv)
    code = extract_example(node_source(args.package_dir))
    modules = check_static(code)
    print('qos event example: imports from %s, containers and signatures ok'
          % ', '.join(sorted(set(modules))))
    if args.runtime:
        check_runtime(code)
        print('qos event example: subscription and publisher created with callbacks attached')
    return 0


if __name__ == '__main__':
    sys.exit(main())
