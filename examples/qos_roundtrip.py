#!/usr/bin/env python3
"""Controlled DDS QoS mismatch and repair; no hardware or motion commands.

Run only in an isolated ROS environment. Missing dependencies or timed-out
positive controls fail the experiment rather than count as a demonstrated fix.
"""

import json
import os
import time
import uuid


def run():
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    context = Context()
    rclpy.init(context=context)
    executor = SingleThreadedExecutor(context=context)
    sender = receiver = None
    try:
        token = uuid.uuid4().hex
        topic = '/skill_qos_probe_' + token
        sender = Node('skill_qos_sender_' + token, context=context)
        receiver = Node('skill_qos_receiver_' + token, context=context)
        executor.add_node(sender)
        executor.add_node(receiver)
        offered = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        requested = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)
        pub = sender.create_publisher(String, topic, offered)
        counts = {'control': 0, 'incompatible': 0, 'repaired': 0}

        def callback(key):
            def receive(message):
                if message.data == token:
                    counts[key] += 1
            return receive

        receiver.create_subscription(String, topic, callback('control'), offered)
        bad = receiver.create_subscription(String, topic, callback('incompatible'), requested)
        sender.create_timer(0.02, lambda: pub.publish(String(data=token)))
        # A simultaneous compatible subscriber is the positive control. Merely
        # seeing zero messages on a mismatched endpoint would be inconclusive.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.05)
            if counts['control'] >= 25:
                break
        if counts['control'] < 25:
            raise RuntimeError('Positive control did not establish live delivery')
        observation_end = time.monotonic() + 1.0
        while time.monotonic() < observation_end:
            executor.spin_once(timeout_sec=0.05)
        if counts['incompatible'] != 0:
            raise RuntimeError('Expected incompatible subscriber received data')
        before = dict(counts)
        receiver.destroy_subscription(bad)
        receiver.create_subscription(String, topic, callback('repaired'), offered)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.05)
            if counts['repaired'] >= 25:
                break
        if counts['repaired'] < 25:
            raise RuntimeError('Repaired subscriber did not establish live delivery')
        return {'status': 'pass', 'verification_level': 'L3',
                'ros_distro': os.environ.get('ROS_DISTRO', 'unset'),
                'rmw': rclpy.get_rmw_implementation_identifier(),
                'topic': topic, 'offered': 'BEST_EFFORT',
                'requested_before': 'RELIABLE', 'requested_after': 'BEST_EFFORT',
                'before': before, 'after': counts,
                'boundary': 'Controlled DDS behavior; not a model benchmark or hardware test.'}
    finally:
        executor.shutdown()
        for node in (receiver, sender):
            if node is not None:
                node.destroy_node()
        context.shutdown()


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
