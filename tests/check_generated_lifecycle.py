#!/usr/bin/env python3
"""Exercise an installed generated LifecycleNode with real rclpy and timers.

Run in an isolated ROS environment after building the named Python package.
No mocks, actuator commands, or model-quality claims are involved.
"""

import argparse
import importlib
import json
import re
import sys
import time
import uuid


def exercise(node_class, scenario, rate=50.0):
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.lifecycle import TransitionCallbackReturn as Return
    from rclpy.parameter import Parameter

    context = Context()
    executor = node = None
    trace = []
    ticks = [0]
    try:
        rclpy.init(context=context)
        node = node_class(context=context, namespace='/lifecycle_probe_' + uuid.uuid4().hex,
                          parameter_overrides=[Parameter('publish_rate', value=rate)])
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        def receive_tick():
            ticks[0] += 1

        node.timer_callback = receive_tick

        def spin_for(duration):
            end = time.monotonic() + duration
            while time.monotonic() < end:
                executor.spin_once(timeout_sec=0.01)

        def require_ticks():
            before = ticks[0]
            end = time.monotonic() + 3.0
            while ticks[0] < before + 3 and time.monotonic() < end:
                executor.spin_once(timeout_sec=0.02)
            if ticks[0] < before + 3:
                raise RuntimeError('Active timer did not produce the positive control')

        def change(name, expected=Return.SUCCESS):
            result = getattr(node, 'trigger_' + name)()
            # This is observed rclpy state, not a label inferred from our command.
            state = node._state_machine.current_state
            trace.append({'transition': name, 'result': str(result), 'state': state,
                          'timers': len(list(node.timers)), 'ticks': ticks[0]})
            if result != expected:
                raise RuntimeError('Unexpected transition result: ' + str(trace[-1]))

        def require_stopped():
            before = ticks[0]
            spin_for(0.15)
            if ticks[0] != before or list(node.timers):
                raise RuntimeError(f'Timer survived transition: {before} -> {ticks[0]}')

        if scenario == 'reconfigure':
            for _ in range(3):
                change('configure')
                change('activate')
                require_ticks()
                change('deactivate')
                require_stopped()
                change('cleanup')
        elif scenario == 'reactivate':
            change('configure')
            for _ in range(3):
                change('activate')
                require_ticks()
                change('deactivate')
                require_stopped()
            change('cleanup')
        elif scenario == 'active-shutdown':
            change('configure')
            change('activate')
            require_ticks()
            change('shutdown')
            require_stopped()
        elif scenario == 'managed-publisher':
            from std_msgs.msg import String
            publisher = node.create_lifecycle_publisher(String, 'managed_probe', 10)
            received = []
            observer = rclpy.create_node('managed_observer_' + uuid.uuid4().hex, context=context)
            executor.add_node(observer)
            observer.create_subscription(String, node.get_namespace() + '/managed_probe',
                                         lambda msg: received.append(msg.data), 10)
            try:
                change('configure')
                if publisher.is_activated:
                    raise RuntimeError('Publisher was active before activation')
                change('activate')
                if not publisher.is_activated:
                    raise RuntimeError('Active node did not activate its managed publisher')
                deadline = time.monotonic() + 5.0
                while not received and time.monotonic() < deadline:
                    publisher.publish(String(data='positive-control'))
                    executor.spin_once(timeout_sec=0.02)
                if not received:
                    raise RuntimeError('Active lifecycle publisher did not deliver data')
                change('deactivate')
                if publisher.is_activated:
                    raise RuntimeError('Publisher survived deactivation')
                require_stopped()
                change('cleanup')
            finally:
                executor.remove_node(observer)
                observer.destroy_node()
        elif scenario == 'managed-activation-failure':
            from rclpy.lifecycle import ManagedEntity

            class RefusesActivation(ManagedEntity):
                def on_activate(self, state):
                    return Return.FAILURE

            node.add_managed_entity(RefusesActivation())
            change('configure')
            change('activate', Return.FAILURE)
            if node._state_machine.current_state[1] != 'inactive':
                raise RuntimeError('Failed managed activation did not stay inactive')
            require_stopped()
            change('cleanup')
        elif scenario == 'invalid-rate':
            change('configure', Return.FAILURE)
            if node._state_machine.current_state[1] != 'unconfigured' or list(node.timers):
                raise RuntimeError('Rejected configuration retained state or resources')
            results = node.set_parameters([Parameter('publish_rate', value=50.0)])
            if not all(result.successful for result in results):
                raise RuntimeError('Could not repair the rejected parameter')
            change('configure')
            change('activate')
            require_ticks()
            change('shutdown')
            require_stopped()
        else:
            raise ValueError('Unknown scenario: ' + scenario)
        return {'case': scenario, 'rate': repr(rate), 'status': 'pass', 'trace': trace}
    except Exception as exc:
        return {'case': scenario, 'rate': repr(rate), 'status': 'fail',
                'error': str(exc), 'trace': trace}
    finally:
        try:
            if executor is not None:
                executor.shutdown(timeout_sec=2.0)
        finally:
            try:
                if node is not None:
                    node.destroy_node()
            finally:
                context.try_shutdown()


def check_plain_rates(package):
    import rclpy
    from rclpy.context import Context
    from rclpy.parameter import Parameter

    module = importlib.import_module(f'{package}.{package}_node')
    name = ''.join(part.capitalize() for part in package.split('_')) + 'Node'
    node_class = getattr(module, name)
    records = []
    for rate in (0.0, -1.0, float('nan'), float('inf'), 1e-300, 1e300, 17.0):
        context = Context()
        rclpy.init(context=context)
        node = None
        try:
            rejected = False
            try:
                node = node_class(context=context,
                                  parameter_overrides=[Parameter('publish_rate', value=rate)])
            except ValueError:
                rejected = True
            if rate == 17.0:
                if rejected or node.timer.timer_period_ns != int(1e9 / rate):
                    raise RuntimeError('Valid plain-node timer was not created correctly')
            elif not rejected:
                raise RuntimeError('Invalid plain-node rate reached timer construction')
            records.append({'case': 'plain-rate', 'rate': repr(rate), 'status': 'pass'})
        except Exception as exc:
            records.append({'case': 'plain-rate', 'rate': repr(rate), 'status': 'fail',
                            'error': str(exc)})
        finally:
            try:
                if node is not None:
                    node.destroy_node()
            finally:
                context.try_shutdown()
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', help='Installed generated Python lifecycle package')
    parser.add_argument('--plain-package', help='Also check the generated plain Python node')
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r'[a-z][a-z0-9_]*', name)
           for name in (args.package, args.plain_package) if name is not None):
        parser.error('Expected a ROS package name')
    module = importlib.import_module(f'{args.package}.{args.package}_node')
    class_name = ''.join(part.capitalize() for part in args.package.split('_')) + 'Node'
    node_class = getattr(module, class_name)
    results = [exercise(node_class, name) for name in
               ('reconfigure', 'reactivate', 'active-shutdown',
                'managed-publisher', 'managed-activation-failure')]
    results.extend(exercise(node_class, 'invalid-rate', value) for value in
                   (0.0, -1.0, float('nan'), float('inf'), 1e-300, 1e300))
    if args.plain_package:
        results.extend(check_plain_rates(args.plain_package))
    import rclpy
    print(json.dumps({'rmw': rclpy.get_rmw_implementation_identifier(),
                      'scope': 'Real generated lifecycle and timer behavior',
                      'results': results}, indent=2))
    return int(any(result['status'] != 'pass' for result in results))


if __name__ == '__main__':
    sys.exit(main())
