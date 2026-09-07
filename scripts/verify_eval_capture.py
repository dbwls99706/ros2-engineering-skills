#!/usr/bin/env python3
"""Check the integrity and completeness of externally collected A/B captures.

Does not call a model, grade answers, or establish that a claimed model/client
produced the files. A valid bundle is a prerequisite for human review, not a
performance claim. No data, missing pairs, and changed files are failures.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

SHA256 = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
MAX_FILE_BYTES = 20 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_object(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, 'Duplicate JSON key: ' + key)
            out[key] = value
        return out
    require(path.stat().st_size <= MAX_FILE_BYTES, 'JSON file exceeds 20 MiB')
    result = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    require(isinstance(result, dict), 'JSON root must be an object')
    return result


def local_file(root, relative):
    require(isinstance(relative, str) and bool(relative), 'Artifact path is empty')
    path = PurePosixPath(relative)
    require(not path.is_absolute() and '..' not in path.parts
            and '\\' not in relative and ':' not in relative,
            'Artifact paths must stay inside their bundle')
    target = (root / relative).resolve()
    require(target.is_relative_to(root.resolve()) and target.is_file(),
            'Artifact is missing or escapes the bundle: ' + relative)
    require(0 < target.stat().st_size <= MAX_FILE_BYTES,
            'Artifact is empty or exceeds 20 MiB: ' + relative)
    return target


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root, spec):
    require(isinstance(spec, dict), 'Artifact must contain path and sha256')
    sha = spec.get('sha256')
    require(isinstance(sha, str) and SHA256.fullmatch(sha), 'Invalid artifact SHA-256')
    path = local_file(root, spec.get('path'))
    require(digest(path) == sha, 'Artifact hash mismatch: ' + spec['path'])
    require(bool(path.read_text(encoding='utf-8').strip()), 'Blank text artifact')
    return path


def nonempty(value, field):
    require(isinstance(value, str) and bool(value.strip()), field + ' must be a string')


def validate(manifest_path, suite_path, now=None):
    errors = []
    pairs_checked = 0
    try:
        manifest_path, suite_path = Path(manifest_path), Path(suite_path)
        manifest, suite = load_object(manifest_path), load_object(suite_path)
        require(type(manifest.get('schema_version')) is int
                and manifest['schema_version'] == 1, 'Unsupported capture schema')
        require(type(suite.get('schema_version')) is int
                and suite['schema_version'] == 1, 'Unsupported suite schema')
        revision = manifest.get('skill_revision')
        require(isinstance(revision, str) and REVISION.fullmatch(revision),
                'skill_revision must be a full commit SHA')
        require(manifest.get('suite_sha256') == digest(suite_path), 'Suite hash mismatch')
        for field in ('model', 'client', 'client_version'):
            nonempty(manifest.get(field), field)
        environment = manifest.get('environment')
        require(isinstance(environment, dict) and bool(environment),
                'A reproducible environment description is required')
        for field in ('os', 'ros_distro', 'rmw', 'workspace_revision', 'tool_permissions'):
            nonempty(environment.get(field), 'environment.' + field)
        require(isinstance(manifest.get('generation_parameters'), dict),
                'generation_parameters must be recorded, even when empty')
        trials = suite.get('trials')
        require(type(trials) is int and 1 <= trials <= 100, 'Suite trials must be an integer between 1 and 100')
        cases = suite.get('cases')
        require(isinstance(cases, list) and 1 <= len(cases) <= 100,
                'Suite must declare between 1 and 100 cases before capture')
        case_map = {}
        for case in cases:
            require(isinstance(case, dict), 'Case must be an object')
            case_id = case.get('id')
            nonempty(case_id, 'case.id')
            require(case_id not in case_map, 'Duplicate suite case')
            criteria = case.get('criteria')
            require(isinstance(criteria, list) and bool(criteria)
                    and all(isinstance(c, str) and c.strip() for c in criteria),
                    'Each case needs a preregistered rubric')
            prompt = local_file(suite_path.parent, case.get('prompt'))
            case_map[case_id] = digest(prompt)
        runs = manifest.get('runs')
        require(isinstance(runs, list) and 1 <= len(runs) <= 20000,
                'No captured runs or too many runs')
        expected = {(case, trial, condition) for case in case_map
                    for trial in range(1, trials + 1) for condition in ('on', 'off')}
        observed, sessions, trace_paths, output_paths = set(), set(), set(), set()
        now = now or datetime.now(timezone.utc)
        for run in runs:
            require(isinstance(run, dict), 'Run must be an object')
            case, trial, condition = run.get('case_id'), run.get('trial'), run.get('condition')
            require(isinstance(case, str) and type(trial) is int
                    and isinstance(condition, str), 'Invalid run key types')
            key = (case, trial, condition)
            require(key in expected and key not in observed, 'Unexpected or duplicate run: ' + str(key))
            observed.add(key)
            nonempty(run.get('session_id'), 'session_id')
            require(run['session_id'] not in sessions, 'Sessions must be isolated across runs')
            sessions.add(run['session_id'])
            require(type(run.get('skill_loaded')) is bool
                    and run['skill_loaded'] == (condition == 'on'),
                    'skill_loaded contradicts the run condition')
            nonempty(run.get('captured_at'), 'captured_at')
            captured = datetime.fromisoformat(run['captured_at'].replace('Z', '+00:00'))
            require(captured.tzinfo is not None and captured <= now,
                    'captured_at needs a timezone and cannot be in the future')
            require(run.get('prompt_sha256') == case_map[case], 'Prompt differs from the suite')
            output = artifact(manifest_path.parent, run.get('output'))
            trace = artifact(manifest_path.parent, run.get('trace'))
            require(output != trace, 'Answer and execution trace must be separate files')
            require(trace not in trace_paths and output not in output_paths
                    and trace not in output_paths and output not in trace_paths,
                    'Each run needs its own output and trace artifacts')
            trace_paths.add(trace)
            output_paths.add(output)
        require(observed == expected, 'Missing paired runs: ' + str(sorted(expected - observed)))
        pairs_checked = len(expected) // 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return {'status': 'invalid' if errors else 'integrity_valid',
            'pairs_checked': pairs_checked, 'errors': errors,
            'boundary': 'File integrity only; model authenticity, trigger behavior, '
                        'answer quality, and safety require separate review.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--suite', type=Path, required=True)
    args = parser.parse_args(argv)
    result = validate(args.manifest, args.suite)
    print(json.dumps(result, indent=2))
    return int(bool(result['errors']))


if __name__ == '__main__':
    sys.exit(main())
