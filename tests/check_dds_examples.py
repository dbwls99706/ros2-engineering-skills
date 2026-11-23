#!/usr/bin/env python3
"""Load every DDS configuration example of the references with the installed middleware.

The references carry CycloneDDS and Fast DDS configuration files. A file that
CycloneDDS rejects stops every node of the process, and one that Fast DDS
rejects is ignored without stopping anything, so a wrong example costs a
reader a bring-up. This check does not keep copies: it reads the fenced blocks
from references/*.md, writes each to a file and starts one rclpy node with it.

    check_dds_examples.py                 load the examples, print one line each
    check_dds_examples.py --list          only list what would be loaded

What depends on the machine is replaced before loading: interface names become
`lo`, trace files go to a temporary directory and shared memory is switched off
(it waits for the iceoryx daemon). An example whose comment names another
generation of the middleware ("Fast DDS 2.x", "Fast DDS 3.x", "CycloneDDS
0.10") is skipped where that generation is not installed.

Exit status 1 when an example is rejected on Jazzy or Kilted, where the
examples were loaded before they were written down. On other distributions
the result is printed and the status stays 0.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ENFORCED = ('jazzy', 'kilted')
PROBE = (
    "import rclpy\n"
    "from std_msgs.msg import String\n"
    "rclpy.init()\n"
    "node = rclpy.create_node('dds_example_probe')\n"
    "node.create_publisher(String, 'dds_example_probe', 10)\n"
    "rclpy.spin_once(node, timeout_sec=0.3)\n"
    "print('NODE_OK')\n"
    "rclpy.shutdown()\n")
NOT_PROFILES = re.compile(r'<policy|governance|domain_access_rules|<enclave|permissions')
INLINE = re.compile(r"CYCLONEDDS_URI='(<CycloneDDS>.*?</CycloneDDS>)'")
GENERATION = re.compile(r'(Fast DDS \d\.x|CycloneDDS\s+\d+\.\d+)')


def examples(references=None):
    """[(source, kind, text)] for every CycloneDDS or Fast DDS configuration in the references."""
    found = []
    for path in sorted(Path(references or ROOT / 'references').glob('*.md')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'```(\w*)\n(.*?)```', text, re.S):
            language, body = match.group(1), match.group(2)
            source = '%s:%d' % (path.name, text[:match.start()].count('\n') + 1)
            if language in ('xml', '') and '<CycloneDDS' in body:
                found.append((source, 'cyclonedds', body))
            elif (language in ('xml', '') and re.search(r'<profiles|<dds[ >]', body)
                  and not NOT_PROFILES.search(body)):
                found.append((source, 'fastdds', body))
            for inline in INLINE.finditer(body):
                found.append((source, 'cyclonedds', inline.group(1)))
    return found


def localised(kind, text, scratch):
    """The example with what depends on this machine replaced; nothing else is changed."""
    if kind != 'cyclonedds':
        return text
    text = re.sub(r'(<NetworkInterface\s+name=")[^"]+(")', r'\1lo\2', text)
    text = re.sub(r'<OutputFile>[^<]*</OutputFile>', '<OutputFile>%s/trace.log</OutputFile>' % scratch, text)
    # a peer given by host name resolves only on the network the example is written for
    text = re.sub(r'(<Peer\s+address=")(?![\d.:\[\]]+")[^"]+(")', r'\g<1>127.0.0.1\2', text)
    return re.sub(r'(<SharedMemory>\s*<Enable>)true(</Enable>)', r'\1false\2', text)


def classify(kind, started, output):
    """(verdict, lines): loaded, rejected, precondition (kernel buffer limit), unavailable or failed."""
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not started and any('failed to load shared library' in line for line in lines):
        return 'unavailable', ['the middleware library cannot be loaded in this environment']
    if kind == 'cyclonedds':
        rejected = [line.split('config: ', 1)[1] for line in lines if 'config: ' in line]
        if rejected:
            return 'rejected', rejected
        limit = [line for line in lines if 'failed to increase socket receive buffer size' in line]
        if limit:
            return 'precondition', ['net.core.rmem_max is too small for the buffer size the example requires']
    else:
        rejected = [line.split('Error] ', 1)[1] for line in lines if 'XMLPARSER Error]' in line]
        if rejected:
            return 'rejected', rejected
    if started:
        return 'loaded', []
    # what the middleware itself printed comes before the Python traceback
    own = [line for line in lines if re.match(r'\d+\.\d+ \[\d+\]', line) or line.startswith('[ERROR]')]
    return 'failed', (own or lines)[-6:]


def installed(distro):
    """{'fastdds': '2.x' or '3.x' or None, 'cyclonedds': 'major.minor' or None}."""
    prefix = Path('/opt/ros') / distro

    def version(*patterns):
        for pattern in patterns:
            for file in sorted(prefix.glob(pattern)):
                match = re.search(r'PACKAGE_VERSION "(\d+)\.(\d+)', file.read_text(errors='replace'))
                if match:
                    return match.groups()
        return None

    fast = version('share/fastdds/cmake/fastdds-config-version.cmake',
                   'share/fastrtps/cmake/fastrtps-config-version.cmake')
    cyclone = version('lib/*/cmake/CycloneDDS/CycloneDDSConfigVersion.cmake',
                      'lib/cmake/CycloneDDS/CycloneDDSConfigVersion.cmake')
    rmw = {name: (prefix / 'share' / name).is_dir() for name in ('rmw_fastrtps_cpp', 'rmw_cyclonedds_cpp')}
    return {'fastdds': '%s.x' % fast[0] if fast and rmw['rmw_fastrtps_cpp'] else None,
            'cyclonedds': '%s.%s' % cyclone if cyclone and rmw['rmw_cyclonedds_cpp'] else None}


def other_generation(kind, text, have):
    """The generation the example names when it is not the installed one, else None."""
    named = GENERATION.search(text)
    if not named:
        return None
    label = re.sub(r'\s+', ' ', named.group(1))
    wanted = label.split(' ')[-1]
    if kind == 'fastdds':
        return None if wanted == have else label
    return None if have.startswith(wanted) else label


def load(kind, path, generation):
    env = dict(os.environ, ROS_DOMAIN_ID=os.environ.get('ROS_DOMAIN_ID', '97'))
    if kind == 'cyclonedds':
        env.update(RMW_IMPLEMENTATION='rmw_cyclonedds_cpp', CYCLONEDDS_URI='file://%s' % path)
    else:
        variable = 'FASTRTPS_DEFAULT_PROFILES_FILE' if generation == '2.x' else 'FASTDDS_DEFAULT_PROFILES_FILE'
        env.update({'RMW_IMPLEMENTATION': 'rmw_fastrtps_cpp', variable: str(path)})
    try:
        done = subprocess.run([sys.executable, '-c', PROBE], capture_output=True, text=True, env=env,
                              timeout=30)
    except subprocess.TimeoutExpired:
        return 'failed', ['no answer in 30 s']
    return classify(kind, 'NODE_OK' in done.stdout, done.stdout + done.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--list', action='store_true', help='list the examples and exit')
    args = parser.parse_args(argv)
    found = examples()
    if args.list:
        for source, kind, _text in found:
            print('%-24s %s' % (source, kind))
        return 0
    distro = os.environ.get('ROS_DISTRO', '')
    have = installed(distro)
    print('ROS_DISTRO=%s  Fast DDS %s  CycloneDDS %s' % (
        distro or '(unset)', have['fastdds'] or 'not installed', have['cyclonedds'] or 'not installed'))
    bad = 0
    with tempfile.TemporaryDirectory() as scratch:
        for index, (source, kind, text) in enumerate(found):
            if have[kind] is None:
                print('%-24s %-10s not checked: middleware not installed' % (source, kind))
                continue
            other = other_generation(kind, text, have[kind])
            if other:
                print('%-24s %-10s skipped: written for %s' % (source, kind, other))
                continue
            path = Path(scratch) / ('example-%02d.xml' % index)
            body = localised(kind, text, scratch)
            if kind == 'cyclonedds' and '<CycloneDDS' not in body:
                body = '<CycloneDDS><Domain>%s</Domain></CycloneDDS>' % body
            path.write_text(body, encoding='utf-8')
            verdict, lines = load(kind, path, have[kind])
            bad += verdict in ('rejected', 'failed')
            print('%-24s %-10s %s%s' % (source, kind, verdict, ''.join('\n    ' + line[:300] for line in lines)))
    if bad and distro not in ENFORCED:
        print('%d example(s) not loaded; reported only, %s is not one of %s' % (bad, distro or 'this', ENFORCED))
        return 0
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
