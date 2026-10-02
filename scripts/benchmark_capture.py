#!/usr/bin/env python3
"""Orchestrate a preregistered plugin-on/off capture and its blinded grading.

This helper does not run a model, grade an answer, or validate a bundle by
itself. It fills the schema 2 manifest that ``verify_eval_capture.py`` checks,
fixes the run order before any session starts, builds blinded grading sheets
for completed answers, and turns human verdicts into paired outcomes.

Subcommands:
    init         Create an experiment directory with a manifest skeleton and order
    add-run      Record one finished session (output, trace, status) into a slot
    status       Show filled slots and the next runs in the frozen run order
    grade-sheet  Write blinded sheets and a grades.json template for reviewers
    score        Combine grades and the manifest into per-pair, per-criterion outcomes

The treatment is the whole plugin (skill body plus its hooks) being available
and enabled; ``skill_loaded`` is a per-session diagnostic of whether the skill
itself was observed to activate. ``skill_revision`` is the skill source revision
contained in the evaluated plugin bundle. Failed, timed-out, and contaminated
runs stay in the inventory and are never re-run silently. Every command after
``init`` refuses to run from a harness checkout that is dirty or differs from
the recorded ``harness_revision``.
"""

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
import platform
import random
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_eval_capture as vec  # noqa: E402  (sibling script, no package)

__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / 'evals' / 'benchmark_suite.json'
CONDITIONS = ('on', 'off')
STATUSES = ('completed', 'failed', 'timed_out')
GRADES = ('pass', 'fail', 'abstain')
# Files grade-sheet writes into its output directory; --force replaces only these.
GENERATED_OUTPUTS = ('sheet-*.md', 'grades.json', 'key.json')
# Version of the payload behind grading_sha256 in grades.json and key.json.
BINDING_SCHEMA = 1
CRITERION_LABEL = re.compile(r'C([1-9][0-9]*)\Z')
ORDER_ALGORITHM = ('Shuffle the (case, trial) blocks with the seed; inside each block '
                   'assign on-first or off-first from a seeded, balanced list so the '
                   'counts differ by at most one; run each pair adjacently.')
SCORE_NOTE = ('plugin ON/OFF paired benchmark; blinded human grading; single experiment; '
              'a case study, not a general performance estimate')
BINDINGS = ('verified_checkout', 'asserted')
PLUGIN_NAME = 'ros2-engineering'
MARKETPLACE_NAME = 'ros2-engineering-skills'


class UsageError(ValueError):
    """A caller mistake that maps to exit code 2."""


def fail(message):
    raise UsageError(message)


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(moment):
    return moment.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_output(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                            check=False, timeout=30)
    if result.returncode != 0:
        fail('git ' + ' '.join(args) + ' failed in ' + str(repo) + ': ' + result.stderr.strip())
    return result.stdout


def git_head(repo):
    return git_output(repo, 'rev-parse', 'HEAD').strip()


def git_is_dirty(repo):
    return bool(git_output(repo, 'status', '--porcelain').strip())


def load_suite(path):
    data = vec.read_regular_bytes(path)
    suite = vec.parse_object(data)
    if suite.get('schema_version') != 1:
        fail('Unsupported suite schema')
    trials = suite.get('trials')
    cases = suite.get('cases')
    if type(trials) is not int or trials < 1 or not isinstance(cases, list) or not cases:
        fail('Suite must declare trials and cases')
    ids = [case.get('id') for case in cases]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        fail('Suite case ids must be unique non-empty strings')
    return suite, data


def critical_labels(case):
    """Return the preregistered critical criterion labels for a case."""
    if 'critical_criteria' not in case:
        fail('Case %s has no preregistered critical_criteria field' % case['id'])
    labels = case['critical_criteria']
    count = len(case['criteria'])
    if not isinstance(labels, list) or len(set(labels)) != len(labels):
        fail('critical_criteria must be a list without duplicates: ' + case['id'])
    for label in labels:
        match = CRITERION_LABEL.fullmatch(label) if isinstance(label, str) else None
        if not match or int(match.group(1)) > count:
            fail('critical_criteria label out of range for %s: %r' % (case['id'], label))
    return list(labels)


def build_order(suite, seed):
    rng = random.Random(seed)
    blocks = [(case['id'], trial) for case in suite['cases']
              for trial in range(1, suite['trials'] + 1)]
    rng.shuffle(blocks)
    half = len(blocks) // 2
    first = ['on'] * half + ['off'] * half
    if len(blocks) % 2:
        first.append(rng.choice(CONDITIONS))
    rng.shuffle(first)
    sequence = []
    for (case_id, trial), lead in zip(blocks, first):
        for condition in (lead, 'off' if lead == 'on' else 'on'):
            sequence.append({'sequence': len(sequence) + 1, 'case_id': case_id,
                             'trial': trial, 'condition': condition})
    return sequence


def manifest_path(exp_dir):
    return Path(exp_dir) / 'capture.json'


def load_manifest(exp_dir):
    path = manifest_path(exp_dir)
    if not path.is_file():
        fail('No capture.json in ' + str(exp_dir) + '; run init first')
    return vec.parse_object(vec.read_regular_bytes(path))


def save_json(path, data):
    """Write JSON through an unpredictable temporary name, then rename it into place.

    mkstemp creates a fresh regular file exclusively, so a symbolic link planted
    at a guessable name such as ``grades.json.tmp`` is never followed; the
    target is only ever replaced by a complete file.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        mask = os.umask(0)
        os.umask(mask)
        os.fchmod(fd, 0o666 & ~mask)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=False) + '\n')
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def suite_for(manifest):
    suite_path = Path(manifest['suite_file'])
    suite, data = load_suite(suite_path)
    if sha256_bytes(data) != manifest['suite_sha256']:
        fail('Suite file changed since init: ' + str(suite_path))
    return suite, suite_path


def expected_slots(suite):
    return [(case['id'], trial, condition) for case in suite['cases']
            for trial in range(1, suite['trials'] + 1) for condition in CONDITIONS]


def slot_key(run):
    return (run['case_id'], run['trial'], run['condition'])


def verify_harness(manifest):
    """The running helper must be the clean checkout recorded at init."""
    if git_is_dirty(ROOT):
        fail('Harness checkout is dirty (untracked files count); restore it before continuing: '
             + str(ROOT))
    head = git_head(ROOT)
    if head != manifest.get('harness_revision'):
        fail('Harness HEAD %s differs from capture.json.harness_revision %s; check out the '
             'recorded revision to continue this experiment' % (head, manifest.get('harness_revision')))


def load_order(exp_dir, manifest, suite):
    """Re-derive the order from the seed and refuse an edited order.json."""
    path = Path(exp_dir) / 'order.json'
    if not path.is_file():
        fail('No order.json in ' + str(exp_dir))
    recorded = vec.parse_object(vec.read_regular_bytes(path))
    expected = {'seed': manifest['order_seed'], 'algorithm': ORDER_ALGORITHM,
                'sequence': build_order(suite, manifest['order_seed'])}
    if recorded != expected:
        fail('order.json does not match the order derived from the recorded seed; '
             'the frozen run order was edited')
    return expected['sequence']


def next_pending(order, manifest):
    done = {slot_key(run) for run in manifest['runs']}
    for entry in order:
        if (entry['case_id'], entry['trial'], entry['condition']) not in done:
            return entry
    return None


def verify_skill_checkout(path, expected_revision=None):
    """Bind skill_revision to a clean checkout whose manifests name this plugin."""
    checkout = Path(path).resolve()
    if git_is_dirty(checkout):
        fail('Skill checkout is dirty; restore it before recording its revision: ' + str(checkout))
    head = git_head(checkout)
    if expected_revision and expected_revision != head:
        fail('--skill-revision %s does not match the checkout HEAD %s' % (expected_revision, head))
    marketplace = vec.parse_object(vec.read_regular_bytes(checkout / '.claude-plugin' / 'marketplace.json'))
    plugin = vec.parse_object(vec.read_regular_bytes(checkout / '.claude-plugin' / 'plugin.json'))
    entries = marketplace.get('plugins') if isinstance(marketplace.get('plugins'), list) else []
    listed = next((e for e in entries if isinstance(e, dict) and e.get('name') == PLUGIN_NAME), None)
    if marketplace.get('name') != MARKETPLACE_NAME or listed is None or listed.get('source') != './':
        fail('Checkout marketplace.json must name marketplace %s with plugin %s from source "./"'
             % (MARKETPLACE_NAME, PLUGIN_NAME))
    if plugin.get('name') != PLUGIN_NAME:
        fail('Checkout plugin.json must name plugin ' + PLUGIN_NAME)
    return head


def clean_reason(value, option):
    if value is None:
        return None
    reason = value.strip()
    if not reason:
        fail(option + ' needs a non-empty reason')
    return reason


# --------------------------------------------------------------------------- init

def cmd_init(args):
    exp_dir = Path(args.exp_dir)
    if exp_dir.exists() and any(exp_dir.iterdir()):
        fail('Experiment directory must be empty or absent: ' + str(exp_dir))
    suite_path = Path(args.suite).resolve()
    suite, suite_bytes = load_suite(suite_path)
    for case in suite['cases']:
        critical_labels(case)
        vec.local_file(suite_path.parent, case.get('prompt'))
    if git_is_dirty(ROOT):
        fail('Harness checkout is dirty (untracked files count); commit or stash before starting '
             'an experiment: ' + str(ROOT))
    harness_revision = git_head(ROOT)
    if args.skill_revision and not vec.REVISION.fullmatch(args.skill_revision):
        fail('skill_revision must be a full 40-character commit SHA')
    if args.skill_checkout:
        skill_revision = verify_skill_checkout(args.skill_checkout, args.skill_revision)
        binding = 'verified_checkout'
    else:
        skill_revision = args.skill_revision or harness_revision
        binding = 'asserted'
    environment = {'os': args.os, 'ros_distro': args.ros_distro, 'rmw': args.rmw,
                   'workspace_revision': args.workspace_revision,
                   'tool_permissions': args.tool_permissions}
    for field, value in environment.items():
        if not value or not value.strip():
            fail('environment.' + field + ' must be non-empty')
    manifest = {
        'schema_version': 2,
        'skill_revision': skill_revision,
        'skill_revision_binding': binding,
        'harness_revision': harness_revision,
        'harness_version': __version__,
        'suite_file': str(suite_path),
        'suite_sha256': sha256_bytes(suite_bytes),
        'client': args.client,
        'client_version': args.client_version,
        'model': args.model,
        'generation_parameters': {},
        'environment': environment,
        'order_seed': args.seed,
        'created_at': iso_utc(utc_now()),
        'runs': [],
    }
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / 'runs').mkdir()
    save_json(manifest_path(exp_dir), manifest)
    order = {'seed': args.seed, 'algorithm': ORDER_ALGORITHM,
             'sequence': build_order(suite, args.seed)}
    save_json(exp_dir / 'order.json', order)
    (exp_dir / 'README.md').write_text(
        '# Benchmark experiment (in progress)\n\n'
        'This directory is an incomplete plugin ON/OFF capture. It holds no results until\n'
        'every slot in `order.json` is filled and `verify_eval_capture.py` reports\n'
        '`integrity_valid`. Integrity is not answer quality; see docs/BENCHMARK_RUNBOOK.md.\n\n'
        '- skill_revision (skill source in the evaluated plugin bundle): ' + skill_revision + '\n'
        '- skill_revision_binding: ' + binding
        + ('\n' if binding == 'verified_checkout' else
           ' (not bound to an installed checkout; not for an exact-revision comparison claim)\n')
        + '- harness_revision (benchmark tooling): ' + harness_revision + '\n'
        '- suite_sha256: ' + manifest['suite_sha256'] + '\n', encoding='utf-8')
    print(json.dumps({'experiment': str(exp_dir), 'slots': len(expected_slots(suite)),
                      'skill_revision': skill_revision, 'skill_revision_binding': binding,
                      'harness_revision': harness_revision,
                      'suite_sha256': manifest['suite_sha256']}, indent=2))
    return 0


# ------------------------------------------------------------------------ add-run

def parse_loaded(value):
    return {'true': True, 'false': False, 'unknown': None}[value]


def prepare_artifact(exp_dir, source, destination, allow_empty):
    """Read and validate an artifact without writing anything.

    Returns (destination, data, manifest_entry). Every input check for a run
    happens through this function before any file is copied, so a validation
    refusal leaves runs/ and the manifest unchanged.
    """
    data = vec.read_regular_bytes(Path(source))
    text = data.decode('utf-8')
    if not allow_empty and not text.strip():
        fail('Artifact must not be blank: ' + str(source))
    if destination.exists():
        fail('Artifact already recorded: ' + str(destination))
    entry = {'path': destination.relative_to(exp_dir).as_posix(), 'sha256': sha256_bytes(data)}
    return destination, data, entry


def cmd_add_run(args):
    exp_dir = Path(args.exp_dir).resolve()
    manifest = load_manifest(exp_dir)
    verify_harness(manifest)
    suite, suite_path = suite_for(manifest)
    order = load_order(exp_dir, manifest, suite)
    case = next((c for c in suite['cases'] if c['id'] == args.case), None)
    if case is None:
        fail('Unknown case id: ' + args.case)
    if not 1 <= args.trial <= suite['trials']:
        fail('trial must be between 1 and %d' % suite['trials'])
    key = (args.case, args.trial, args.condition)
    if any(slot_key(run) == key for run in manifest['runs']):
        fail('Slot already recorded: ' + str(key))
    loaded = parse_loaded(args.skill_loaded)
    if args.condition == 'off' and loaded is True:
        fail('A control (off) run cannot report skill_loaded=true')
    if args.status == 'completed':
        if args.output is None:
            fail('--output is required for a completed run')
        if args.error:
            fail('--error is only for failed or timed_out runs')
    else:
        if not args.error or not args.error.strip():
            fail('--error is required for a failed or timed_out run')
    if args.duration is not None and not (math.isfinite(args.duration) and args.duration >= 0):
        fail('--duration must be a finite nonnegative number')
    session_id = args.session_id or str(uuid.uuid4())
    if any(run['session_id'] == session_id for run in manifest['runs']):
        fail('session_id already used; each run needs a fresh session')
    out_of_order = clean_reason(args.out_of_order, '--out-of-order')
    contaminated = clean_reason(args.contaminated, '--contaminated')
    expected = next_pending(order, manifest)
    expected_key = (expected['case_id'], expected['trial'], expected['condition']) if expected else None
    if expected_key != key and out_of_order is None:
        fail('Out of frozen run order: next slot is %s, got %s. Record a deliberate deviation '
             'with --out-of-order <reason>; it is kept as protocol contamination'
             % (expected_key, key))
    if expected_key == key and out_of_order is not None:
        fail('--out-of-order given but %s is the next slot in order' % (key,))
    stem = '%s-t%d-%s' % (args.case, args.trial, args.condition)
    runs_dir = exp_dir / 'runs'
    pending = [prepare_artifact(exp_dir, args.trace, runs_dir / (stem + '.trace.txt'), allow_empty=False)]
    if args.output is not None:
        pending.append(prepare_artifact(exp_dir, args.output, runs_dir / (stem + '.output.md'),
                                        allow_empty=True))
    for destination, data, _ in pending:
        destination.write_bytes(data)
    trace = pending[0][2]
    output = pending[1][2] if len(pending) == 2 else None
    run = {
        'case_id': args.case, 'trial': args.trial, 'condition': args.condition,
        'session_id': session_id, 'skill_loaded': loaded,
        'execution_status': args.status, 'captured_at': iso_utc(utc_now()),
        'prompt_sha256': vec.digest(vec.local_file(suite_path.parent, case['prompt'])),
        'output': output, 'trace': trace,
    }
    if args.status != 'completed':
        run['error'] = args.error.strip()
    if args.duration is not None:
        run['duration_seconds'] = args.duration
    reasons = []
    if out_of_order is not None:
        reasons.append('out-of-order: ' + out_of_order)
    if contaminated is not None:
        reasons.append(contaminated)
    if reasons:
        run['protocol_contamination'] = '; '.join(reasons)
    manifest['runs'].append(run)
    save_json(manifest_path(exp_dir), manifest)
    remaining = len(expected_slots(suite)) - len(manifest['runs'])
    print(json.dumps({'recorded': list(key), 'session_id': session_id,
                      'execution_status': args.status, 'skill_loaded': loaded,
                      'protocol_contamination': run.get('protocol_contamination'),
                      'remaining_slots': remaining}, indent=2))
    return 0


# ------------------------------------------------------------------------- status

def status_report(exp_dir):
    exp_dir = Path(exp_dir).resolve()
    manifest = load_manifest(exp_dir)
    verify_harness(manifest)
    suite, _ = suite_for(manifest)
    order = load_order(exp_dir, manifest, suite)
    done = {slot_key(run) for run in manifest['runs']}
    pending = [entry for entry in order
               if (entry['case_id'], entry['trial'], entry['condition']) not in done]
    outcomes = {status: 0 for status in STATUSES}
    contaminated = 0
    for run in manifest['runs']:
        outcomes[run['execution_status']] += 1
        contaminated += int('protocol_contamination' in run)
    return {'recorded': len(done), 'total': len(expected_slots(suite)),
            'remaining': len(pending), 'execution_outcomes': outcomes,
            'contaminated_runs': contaminated, 'next': pending[:5],
            'note': 'Integrity and quality are checked separately; see verify_eval_capture.py.'}


def cmd_status(args):
    print(json.dumps(status_report(args.exp_dir), indent=2))
    return 0


# -------------------------------------------------------------------- grade-sheet

def gradable(run):
    return (run['execution_status'] == 'completed' and run.get('output') is not None
            and 'protocol_contamination' not in run)


def blind_assignments(manifest, suite):
    """Map every gradable run to a blinded id; the order is seeded and case-local."""
    key, counter = {}, 0
    for case in suite['cases']:
        runs = [run for run in manifest['runs'] if run['case_id'] == case['id'] and gradable(run)]
        rng = random.Random('%s:%s:%s' % (manifest['order_seed'], manifest['suite_sha256'], case['id']))
        rng.shuffle(runs)
        for run in runs:
            counter += 1
            key['R%03d' % counter] = run
    return key


def verified_capture(exp_dir, suite_path):
    """Refuse to grade or score anything the integrity verifier does not accept.

    The verifier checks that every artifact path stays inside the experiment,
    that every artifact is a regular file whose SHA-256 matches the manifest,
    and that every preregistered slot is present.
    """
    report = vec.validate(manifest_path(exp_dir), suite_path)
    if report['status'] != 'integrity_valid':
        fail('Capture failed integrity verification: ' + '; '.join(report['errors']))
    return report


def key_entries(key):
    return {rid: {'case_id': run['case_id'], 'trial': run['trial'],
                  'condition': run['condition'], 'session_id': run['session_id']}
            for rid, run in key.items()}


def grading_entries(key):
    """key_entries plus the hash of the answer shown under each blinded id."""
    entries = key_entries(key)
    for rid, run in key.items():
        entries[rid]['output_sha256'] = run['output']['sha256']
    return entries


def grading_digest(manifest, key):
    """Digest binding a grades file to one suite and one exact blinded answer set.

    The payload names the suite (criteria) and every answer's bytes, so grades
    recorded against another experiment, another suite revision, or a run that
    reused session ids never match. One SHA-256 reveals nothing about which arm
    produced an answer.
    """
    payload = {'binding_schema': BINDING_SCHEMA, 'suite_sha256': manifest['suite_sha256'],
               'assignments': grading_entries(key)}
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def generated_outputs(out):
    """List the generated files in ``out``; refuse before touching anything if one is not a regular file.

    A symbolic link or directory under a generated name would otherwise be
    skipped by the cleanup and then followed by the write that comes after it.
    """
    found = []
    with os.scandir(out) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if not any(fnmatch.fnmatchcase(entry.name, pattern) for pattern in GENERATED_OUTPUTS):
                continue
            if entry.is_symlink() or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                fail('Refusing to replace generated output %s: it is a symbolic link or not a '
                     'regular file; remove it manually' % entry.name)
            found.append(Path(entry.path))
    return found


def cmd_grade_sheet(args):
    exp_dir = Path(args.exp_dir).resolve()
    manifest = load_manifest(exp_dir)
    verify_harness(manifest)
    suite, suite_path = suite_for(manifest)
    load_order(exp_dir, manifest, suite)
    verified_capture(exp_dir, suite_path)
    out = Path(args.out)
    if out.is_symlink():
        fail('Output directory must not be a symbolic link: ' + str(out))
    if out.exists() and not out.is_dir():
        fail('Output path must be a directory: ' + str(out))
    if out.is_dir() and any(out.iterdir()):
        if not args.force:
            fail('Output directory is not empty: %s. Re-running would overwrite the sheets and any '
                 'grades already recorded in grades.json; pass --force to replace them' % out)
        # Every generated path is checked before any is removed; unrelated files stay.
        for stale in generated_outputs(out):
            stale.unlink()
    out.mkdir(parents=True, exist_ok=True)
    key = blind_assignments(manifest, suite)
    binding = grading_digest(manifest, key)
    grades = {'schema_version': 1, 'grading_sha256': binding, 'values': list(GRADES), 'cases': {}}
    for case in suite['cases']:
        ids = [rid for rid, run in key.items() if run['case_id'] == case['id']]
        labels = ['C%d' % (i + 1) for i in range(len(case['criteria']))]
        lines = ['# Blind grading sheet: ' + case['id'], '',
                 'Grade each answer on every criterion as pass, fail, or abstain in',
                 '`grades.json`. Do not guess which arm produced an answer. Abstain when',
                 'the answer does not let you decide.', '', '## Criteria', '']
        lines += ['- %s: %s' % (label, text) for label, text in zip(labels, case['criteria'])]
        lines += ['', '## Answers', '']
        for rid in ids:
            # Re-verify path containment and the recorded hash at read time.
            body = vec.artifact(exp_dir, key[rid]['output'], allow_empty=True).read_text(encoding='utf-8')
            lines += ['### ' + rid, '', body.rstrip('\n') or '(empty answer)', '', '---', '']
        (out / ('sheet-' + case['id'] + '.md')).write_text('\n'.join(lines), encoding='utf-8')
        grades['cases'][case['id']] = {rid: {label: None for label in labels} for rid in ids}
    save_json(out / 'grades.json', grades)
    save_json(out / 'key.json', {
        'warning': 'Do not give this file to graders; it unblinds the sheets.',
        'grading_sha256': binding,
        'assignments': key_entries(key)})
    excluded = [slot_key(run) for run in manifest['runs'] if not gradable(run)]
    print(json.dumps({'sheets': len(suite['cases']), 'gradable_runs': len(key),
                      'excluded_runs': [list(k) for k in excluded],
                      'note': 'Excluded runs are execution failures or contaminated sessions; '
                              'score reports them separately.'}, indent=2))
    return 0


# -------------------------------------------------------------------------- score

def criterion_outcome(on_grade, off_grade):
    if on_grade == 'pass' and off_grade == 'fail':
        return 'on_better'
    if on_grade == 'fail' and off_grade == 'pass':
        return 'off_better'
    if on_grade in ('pass', 'fail') and on_grade == off_grade:
        return 'tie'
    return 'unknown'


def critical_failure(grade_row, critical):
    """True/False when decidable, None when any critical grade is missing or abstain."""
    if grade_row is None:
        return None
    verdicts = [grade_row.get(label) for label in critical]
    if any(v not in ('pass', 'fail') for v in verdicts):
        return None
    return any(v == 'fail' for v in verdicts)


def cmd_score(args):
    exp_dir = Path(args.exp_dir).resolve()
    manifest = load_manifest(exp_dir)
    verify_harness(manifest)
    suite, suite_path = suite_for(manifest)
    load_order(exp_dir, manifest, suite)
    verified_capture(exp_dir, suite_path)
    grades_path = Path(args.grades)
    grades = vec.parse_object(vec.read_regular_bytes(grades_path))
    if grades.get('schema_version') != 1:
        fail('grades.json schema_version must be 1')
    key_path = Path(args.key) if args.key else grades_path.with_name('key.json')
    key_document = vec.parse_object(vec.read_regular_bytes(key_path))
    key = key_document.get('assignments')
    # The key is never trusted: it must equal the assignments re-derived from
    # this experiment's manifest and seed, so a swapped or foreign key cannot
    # flip ON and OFF.
    derived = blind_assignments(manifest, suite)
    if key != key_entries(derived):
        fail('key.json does not match the blinded assignments derived from this experiment; '
             'regenerate it with grade-sheet or pass the original with --key')
    # The grades are bound to the suite and the exact answers the grader saw, so
    # a grades file from another experiment is refused even with a valid key.
    expected = grading_digest(manifest, derived)
    if grades.get('grading_sha256') != expected:
        fail("grades.json does not belong to this experiment: its grading_sha256 "
             "does not match the blinded answer set derived here; "
             "grade this experiment's own sheets")
    if key_document.get('grading_sha256') != expected:
        fail('key.json grading_sha256 does not match this experiment; regenerate it with grade-sheet')
    by_slot = {slot_key(run): run for run in manifest['runs']}
    rid_by_slot = {(a['case_id'], a['trial'], a['condition']): rid for rid, a in key.items()}
    pairs = []
    totals = {'on_better': 0, 'off_better': 0, 'tie': 0, 'unknown': 0}
    execution_failures = {'on': 0, 'off': 0}
    contaminated = {'on': 0, 'off': 0}
    ungraded_cells = 0
    for case in suite['cases']:
        critical = critical_labels(case)
        labels = ['C%d' % (i + 1) for i in range(len(case['criteria']))]
        case_grades = grades.get('cases', {}).get(case['id'], {})
        for trial in range(1, suite['trials'] + 1):
            entry = {'case_id': case['id'], 'trial': trial, 'criteria': {},
                     'execution_status': {}, 'protocol_contamination': {},
                     'critical_failure': {}}
            rows = {}
            for condition in CONDITIONS:
                run = by_slot.get((case['id'], trial, condition))
                if run is None:
                    entry['execution_status'][condition] = 'missing'
                    entry['protocol_contamination'][condition] = None
                    rows[condition] = None
                    continue
                entry['execution_status'][condition] = run['execution_status']
                entry['protocol_contamination'][condition] = run.get('protocol_contamination')
                if run['execution_status'] != 'completed':
                    execution_failures[condition] += 1
                if 'protocol_contamination' in run:
                    contaminated[condition] += 1
                rid = rid_by_slot.get((case['id'], trial, condition))
                row = case_grades.get(rid) if rid and gradable(run) else None
                if row is not None:
                    for label in labels:
                        value = row.get(label)
                        if value not in GRADES and value is not None:
                            fail('Invalid grade %r for %s/%s' % (value, rid, label))
                        ungraded_cells += int(value is None)
                rows[condition] = row
            for label in labels:
                on_grade = rows['on'].get(label) if rows['on'] else None
                off_grade = rows['off'].get(label) if rows['off'] else None
                outcome = criterion_outcome(on_grade, off_grade)
                entry['criteria'][label] = outcome
                totals[outcome] += 1
            for condition in CONDITIONS:
                entry['critical_failure'][condition] = critical_failure(rows[condition], critical)
            pairs.append(entry)
    report = {'note': SCORE_NOTE, 'skill_revision': manifest['skill_revision'],
              'skill_revision_binding': manifest.get('skill_revision_binding', 'asserted'),
              'harness_revision': manifest.get('harness_revision'),
              'suite_sha256': manifest['suite_sha256'],
              'summary': {'pairs': len(pairs), 'criterion_outcomes': totals,
                          'execution_failures': execution_failures,
                          'contaminated_runs': contaminated, 'ungraded_cells': ungraded_cells},
              'pairs': pairs}
    print(json.dumps(report, indent=2))
    return 0


# ---------------------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True)

    init = sub.add_parser('init', help='create an experiment directory')
    init.add_argument('exp_dir')
    init.add_argument('--suite', default=str(DEFAULT_SUITE))
    init.add_argument('--client', required=True)
    init.add_argument('--client-version', required=True)
    init.add_argument('--model', required=True)
    init.add_argument('--seed', type=int, required=True)
    init.add_argument('--workspace-revision', required=True,
                      help='identifier of the neutral workspace used by both arms')
    init.add_argument('--tool-permissions', required=True,
                      help='tool permission mode shared by both arms')
    init.add_argument('--skill-revision', default=None,
                      help='40-char SHA of the skill source in the evaluated plugin bundle; '
                           'without --skill-checkout it is recorded as asserted')
    init.add_argument('--skill-checkout', default=None,
                      help='clean checkout that the ON arm installs as a local marketplace; '
                           'its HEAD becomes skill_revision (binding: verified_checkout)')
    init.add_argument('--os', default=platform.platform())
    init.add_argument('--ros-distro', default=os.environ.get('ROS_DISTRO') or 'not installed')
    init.add_argument('--rmw', default=os.environ.get('RMW_IMPLEMENTATION') or 'not installed')
    init.set_defaults(func=cmd_init)

    add = sub.add_parser('add-run', help='record one finished session')
    add.add_argument('exp_dir')
    add.add_argument('--case', required=True)
    add.add_argument('--trial', type=int, required=True)
    add.add_argument('--condition', choices=CONDITIONS, required=True)
    add.add_argument('--trace', required=True, help='transcript/tool log; always required')
    add.add_argument('--output', default=None, help='final answer; required when completed')
    add.add_argument('--status', choices=STATUSES, default='completed')
    add.add_argument('--error', default=None)
    add.add_argument('--duration', type=float, default=None)
    add.add_argument('--skill-loaded', choices=('true', 'false', 'unknown'), default='unknown')
    add.add_argument('--session-id', default=None)
    add.add_argument('--contaminated', default=None,
                     help='reason the session broke protocol (e.g. read the rubric)')
    add.add_argument('--out-of-order', default=None,
                     help='reason this slot is recorded ahead of the frozen seeded order; '
                          'kept as protocol contamination')
    add.set_defaults(func=cmd_add_run)

    status = sub.add_parser('status', help='show progress and the next runs')
    status.add_argument('exp_dir')
    status.set_defaults(func=cmd_status)

    sheet = sub.add_parser('grade-sheet', help='write blinded grading sheets')
    sheet.add_argument('exp_dir')
    sheet.add_argument('--out', required=True)
    sheet.add_argument('--force', action='store_true',
                       help='replace the generated files (sheet-*.md, grades.json, key.json) in a '
                            'non-empty output directory, discarding recorded grades; unrelated '
                            'files are left in place')
    sheet.set_defaults(func=cmd_grade_sheet)

    score = sub.add_parser('score', help='combine grades into paired outcomes')
    score.add_argument('exp_dir')
    score.add_argument('--grades', required=True)
    score.add_argument('--key', default=None, help='defaults to key.json beside grades')
    score.set_defaults(func=cmd_score)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
