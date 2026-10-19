#!/usr/bin/env python3
"""Check stage 0 evidence: a deliberate SIGSEGV must leave a core that gdb reads with Fast DDS
debug information (diagnostic branch only).

Runs inside the container right after the probe and again on the runner after the artifact has
been downloaded, so the upload and download are part of what is checked. Every check must pass
before the comparison matrix may start.
"""

import argparse
import gzip
import json
from pathlib import Path
import re

EXPECTED_PATTERN = '/var/crash-cores/core.%e.%p.%t'


def read(directory, name):
    try:
        return (directory / name).read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return ''


def gzip_intact(path):
    try:
        with gzip.open(path, 'rb') as stream:
            while stream.read(1 << 20):
                pass
    except (OSError, EOFError):
        return False
    return True


def check_stage0(directory):
    gdb = read(directory, 'gdb.txt')
    fastrtps = [line for line in gdb.splitlines()
                if re.match(r'0x[0-9a-f]+\s+0x[0-9a-f]+\s+(Yes|No)', line) and 'libfastrtps.so' in line]
    packages = dict(line.split('=', 1) for line in read(directory, 'packages.txt').split() if '=' in line)
    library = packages.get('ros-humble-fastrtps')
    return {
        'core_pattern': read(directory, 'core_pattern.txt').strip() == EXPECTED_PATTERN,
        'core_limit_unlimited': read(directory, 'ulimit_core.txt').strip() == 'unlimited',
        'fastrtps_needed': bool(re.search(r'\(NEEDED\).*\[libfastrtps\.so\.2\.6\]',
                                          read(directory, 'readelf-dynamic.txt'))),
        'fastrtps_mapped_at_run_time': bool(re.search(r'^mapped: .*/libfastrtps\.so',
                                                      read(directory, 'probe-output.txt'), re.M)),
        'killed_by_sigsegv': read(directory, 'exit-status.txt').strip() == '139',
        'one_core_for_pid': len(read(directory, 'cores.txt').split()) == 1,
        'gdb_reports_sigsegv': 'Program terminated with signal SIGSEGV' in gdb,
        'gdb_crash_frame_in_probe': bool(re.search(
            r'^#0\s+(0x[0-9a-f]+ in )?main \(\) at \S*probe\.cpp:\d+', gdb, re.M)),
        'fastrtps_debug_info_loaded': bool(fastrtps) and all(
            'Yes' in line and '(*)' not in line for line in fastrtps),
        'fastrtps_source_line': bool(re.search(r'^Line \d+ of "[^"]*StatefulReader\.cpp"', gdb, re.M)),
        'inspect_script_completed': '[diag_gdb_inspect] done' in gdb,
        'dbgsym_matches_library': library is not None
        and packages.get('ros-humble-fastrtps-dbgsym') == library,
        'core_archive_intact': gzip_intact(directory / 'core.gz'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('directory', type=Path, help='stage 0 evidence directory')
    parser.add_argument('--write', action='store_true', help='also write verdict.json there')
    args = parser.parse_args(argv)
    checks = check_stage0(args.directory)
    verdict = {'status': 'pass' if all(checks.values()) else 'fail', 'checks': checks}
    text = json.dumps(verdict, indent=2)
    print(text, flush=True)
    if args.write:
        (args.directory / 'verdict.json').write_text(text + '\n', encoding='utf-8')
    return 0 if verdict['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
