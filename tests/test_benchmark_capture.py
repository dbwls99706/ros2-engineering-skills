"""Synthetic experiment fixtures only; nothing here is a model benchmark capture."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import benchmark_capture as bench
from scripts import verify_eval_capture as capture

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'evals' / 'benchmark_suite.json'
RUNBOOK = ROOT / 'docs' / 'BENCHMARK_RUNBOOK.md'
SKILL_SHA = 'a' * 40
HARNESS_SHA = 'b' * 40
FIXTURE_LINE = 'synthetic transport fixture, not a model capture\n'


@pytest.fixture(autouse=True)
def fake_git(monkeypatch):
    """Do not depend on the state of the developer checkout.

    ``heads`` maps a resolved repository path to its fake HEAD; ``dirty`` marks
    repositories that should look modified. The harness root defaults to clean
    at HARNESS_SHA.
    """
    state = {'heads': {}, 'dirty': set()}
    monkeypatch.setattr(bench, 'git_head',
                        lambda repo: state['heads'].get(Path(repo).resolve(), HARNESS_SHA))
    monkeypatch.setattr(bench, 'git_is_dirty',
                        lambda repo: Path(repo).resolve() in state['dirty'])
    return state


def init_args(exp_dir, seed=1, extra=()):
    return ['init', str(exp_dir), '--suite', str(SUITE), '--client', 'synthetic-test',
            '--client-version', 'test', '--model', 'not-a-model', '--seed', str(seed),
            '--workspace-revision', 'neutral-empty-v1', '--tool-permissions', 'default',
            '--skill-revision', SKILL_SHA, *extra]


@pytest.fixture
def experiment(tmp_path, capsys):
    exp = tmp_path / 'exp'
    assert bench.main(init_args(exp)) == 0
    capsys.readouterr()
    return exp


def manifest(exp):
    return json.loads((exp / 'capture.json').read_text(encoding='utf-8'))


def order_of(exp):
    return json.loads((exp / 'order.json').read_text(encoding='utf-8'))['sequence']


def next_slot(exp):
    done = {(r['case_id'], r['trial'], r['condition']) for r in manifest(exp)['runs']}
    for entry in order_of(exp):
        slot = (entry['case_id'], entry['trial'], entry['condition'])
        if slot not in done:
            return slot
    return None


def artifacts(tmp_path, stem, output=True):
    trace = tmp_path / (stem + '.trace')
    trace.write_text(FIXTURE_LINE + 'tool log for ' + stem + '\n', encoding='utf-8')
    if not output:
        return trace, None
    answer = tmp_path / (stem + '.answer')
    answer.write_text(FIXTURE_LINE + 'answer text for ' + stem + '\n', encoding='utf-8')
    return trace, answer


def add(exp, tmp_path, case, trial, condition, extra=(), output=True, capsys=None):
    stem = '%s-%d-%s-%d' % (case, trial, condition, len(list(tmp_path.glob('*.trace'))))
    trace, answer = artifacts(tmp_path, stem, output=output)
    argv = ['add-run', str(exp), '--case', case, '--trial', str(trial), '--condition', condition,
            '--trace', str(trace), *extra]
    if answer is not None:
        argv += ['--output', str(answer)]
    code = bench.main(argv)
    if capsys is not None:
        capsys.readouterr()
    return code


def add_next(exp, tmp_path, extra=(), output=True, capsys=None):
    case, trial, condition = next_slot(exp)
    return add(exp, tmp_path, case, trial, condition, extra=extra, output=output, capsys=capsys)


def fill_all(exp, tmp_path, capsys, special=None):
    """Record every slot in the frozen run order; ``special`` overrides a slot."""
    special = special or {}
    while next_slot(exp) is not None:
        slot = next_slot(exp)
        case, trial, condition = slot
        if slot in special:
            kwargs = special[slot]
            assert add(exp, tmp_path, case, trial, condition, capsys=capsys, **kwargs) == 0
            continue
        loaded = 'true' if condition == 'on' else 'false'
        assert add(exp, tmp_path, case, trial, condition,
                   extra=['--skill-loaded', loaded], capsys=capsys) == 0


class TestInit:
    def test_records_separate_skill_and_harness_revisions(self, experiment):
        data = manifest(experiment)
        assert data['schema_version'] == 2
        assert data['skill_revision'] == SKILL_SHA
        assert data['skill_revision_binding'] == 'asserted'
        assert data['harness_revision'] == HARNESS_SHA
        assert data['suite_sha256'] == capture.digest(SUITE)
        assert data['runs'] == []
        assert (experiment / 'runs').is_dir()
        readme = (experiment / 'README.md').read_text(encoding='utf-8')
        assert 'in progress' in readme and 'plugin ON/OFF' in readme
        assert 'not for an exact-revision comparison claim' in readme
        assert '/' not in data['skill_revision'] and 'skill_source_path' not in data

    def test_empty_experiment_is_not_integrity_valid(self, experiment, capsys):
        report = capture.validate(experiment / 'capture.json', SUITE)
        assert report['status'] == 'invalid'
        assert 'No captured runs' in report['errors'][0]
        status = bench.status_report(experiment)
        assert status['recorded'] == 0 and status['remaining'] == 42 and status['total'] == 42

    def test_refuses_dirty_harness(self, tmp_path, fake_git, capsys):
        fake_git['dirty'].add(bench.ROOT.resolve())
        assert bench.main(init_args(tmp_path / 'exp')) == 2
        assert 'dirty' in capsys.readouterr().err
        assert not (tmp_path / 'exp' / 'capture.json').exists()

    def test_refuses_short_skill_revision(self, tmp_path, capsys):
        argv = init_args(tmp_path / 'exp')
        argv[argv.index('--skill-revision') + 1] = 'main'
        assert bench.main(argv) == 2

    def test_requires_nonempty_environment(self, tmp_path, capsys):
        argv = init_args(tmp_path / 'exp')
        argv[argv.index('--workspace-revision') + 1] = ' '
        assert bench.main(argv) == 2

    def test_no_harness_root_option(self):
        with pytest.raises(SystemExit):
            bench.build_parser().parse_args(['init', 'x', '--harness-root', '/tmp'])


def skill_checkout(tmp_path, fake_git, sha=SKILL_SHA, marketplace=None, plugin=None):
    src = tmp_path / 'skill-src'
    (src / '.claude-plugin').mkdir(parents=True)
    marketplace = marketplace if marketplace is not None else {
        'name': 'ros2-engineering-skills',
        'plugins': [{'name': 'ros2-engineering', 'source': './'}]}
    plugin = plugin if plugin is not None else {'name': 'ros2-engineering'}
    (src / '.claude-plugin' / 'marketplace.json').write_text(json.dumps(marketplace), encoding='utf-8')
    (src / '.claude-plugin' / 'plugin.json').write_text(json.dumps(plugin), encoding='utf-8')
    fake_git['heads'][src.resolve()] = sha
    return src


class TestSkillCheckoutBinding:
    def test_checkout_head_becomes_verified_revision(self, tmp_path, fake_git, capsys):
        src = skill_checkout(tmp_path, fake_git, sha='c' * 40)
        argv = init_args(tmp_path / 'exp', extra=['--skill-checkout', str(src)])
        del argv[argv.index('--skill-revision'):argv.index('--skill-revision') + 2]
        assert bench.main(argv) == 0
        data = manifest(tmp_path / 'exp')
        assert data['skill_revision'] == 'c' * 40
        assert data['skill_revision_binding'] == 'verified_checkout'
        assert str(src) not in json.dumps(data)

    def test_mismatching_explicit_revision_is_rejected(self, tmp_path, fake_git, capsys):
        src = skill_checkout(tmp_path, fake_git, sha='c' * 40)
        assert bench.main(init_args(tmp_path / 'exp', extra=['--skill-checkout', str(src)])) == 2
        assert 'does not match the checkout HEAD' in capsys.readouterr().err

    def test_dirty_checkout_is_rejected(self, tmp_path, fake_git, capsys):
        src = skill_checkout(tmp_path, fake_git)
        fake_git['dirty'].add(src.resolve())
        assert bench.main(init_args(tmp_path / 'exp', extra=['--skill-checkout', str(src)])) == 2

    @pytest.mark.parametrize('marketplace,plugin', [
        ({'name': 'other', 'plugins': [{'name': 'ros2-engineering', 'source': './'}]}, None),
        ({'name': 'ros2-engineering-skills', 'plugins': [{'name': 'ros2-engineering', 'source': 'x'}]}, None),
        ({'name': 'ros2-engineering-skills', 'plugins': []}, None),
        (None, {'name': 'something-else'}),
    ])
    def test_identity_mismatch_is_rejected(self, tmp_path, fake_git, capsys, marketplace, plugin):
        src = skill_checkout(tmp_path, fake_git, marketplace=marketplace, plugin=plugin)
        assert bench.main(init_args(tmp_path / 'exp', extra=['--skill-checkout', str(src)])) == 2


class TestOrder:
    def test_seed_is_deterministic_and_pairs_are_adjacent(self, tmp_path, capsys):
        exp_a, exp_b, exp_c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
        assert bench.main(init_args(exp_a, seed=7)) == 0
        assert bench.main(init_args(exp_b, seed=7)) == 0
        assert bench.main(init_args(exp_c, seed=8)) == 0
        seq = order_of(exp_a)
        assert seq == order_of(exp_b) and seq != order_of(exp_c)
        assert [e['sequence'] for e in seq] == list(range(1, 43))
        for first, second in zip(seq[0::2], seq[1::2]):
            assert (first['case_id'], first['trial']) == (second['case_id'], second['trial'])
            assert {first['condition'], second['condition']} == {'on', 'off'}
        assert sum(e['condition'] == 'on' for e in seq[0::2]) in (10, 11)
        assert len({(e['case_id'], e['trial'], e['condition']) for e in seq}) == 42

    def test_edited_order_is_refused_everywhere(self, experiment, tmp_path, capsys):
        order = json.loads((experiment / 'order.json').read_text(encoding='utf-8'))
        a, b = order['sequence'][0], order['sequence'][1]
        a['condition'], b['condition'] = b['condition'], a['condition']
        (experiment / 'order.json').write_text(json.dumps(order), encoding='utf-8')
        assert bench.main(['status', str(experiment)]) == 2
        assert 'frozen run order was edited' in capsys.readouterr().err
        case, trial, condition = a['case_id'], a['trial'], a['condition']
        assert add(experiment, tmp_path, case, trial, condition) == 2
        assert manifest(experiment)['runs'] == []
        assert list((experiment / 'runs').iterdir()) == []
        assert bench.main(['grade-sheet', str(experiment), '--out', str(tmp_path / 'g')]) == 2

    def test_out_of_order_slot_is_refused_without_a_trace_left_behind(self, experiment, tmp_path, capsys):
        seq = order_of(experiment)
        later = seq[5]
        assert add(experiment, tmp_path, later['case_id'], later['trial'], later['condition']) == 2
        err = capsys.readouterr().err
        assert 'Out of frozen run order' in err and '--out-of-order' in err
        assert manifest(experiment)['runs'] == []
        assert list((experiment / 'runs').iterdir()) == []

    def test_declared_deviation_is_kept_as_contamination(self, experiment, tmp_path, capsys):
        later = order_of(experiment)[5]
        assert add(experiment, tmp_path, later['case_id'], later['trial'], later['condition'],
                   extra=['--out-of-order', '  ']) == 2
        assert add(experiment, tmp_path, later['case_id'], later['trial'], later['condition'],
                   extra=['--out-of-order', 'operator started the wrong arm',
                          '--contaminated', 'read grading sheet'], capsys=capsys) == 0
        run = manifest(experiment)['runs'][0]
        assert run['protocol_contamination'] == \
            'out-of-order: operator started the wrong arm; read grading sheet'

    def test_out_of_order_flag_on_the_next_slot_is_refused(self, experiment, tmp_path, capsys):
        assert add_next(experiment, tmp_path, extra=['--out-of-order', 'x']) == 2
        assert 'is the next slot' in capsys.readouterr().err


class TestHarnessEnforcement:
    @pytest.mark.parametrize('argv_tail', [['status'], ['add-run'], ['grade-sheet'], ['score']])
    @pytest.mark.parametrize('problem', ['dirty', 'moved'])
    def test_post_init_commands_refuse_a_changed_harness(self, experiment, tmp_path, fake_git,
                                                         capsys, argv_tail, problem):
        if problem == 'dirty':
            fake_git['dirty'].add(bench.ROOT.resolve())
        else:
            fake_git['heads'][bench.ROOT.resolve()] = 'd' * 40
        argv = [argv_tail[0], str(experiment)]
        if argv_tail[0] == 'add-run':
            case, trial, condition = next_slot(experiment)
            trace, answer = artifacts(tmp_path, 'h')
            argv += ['--case', case, '--trial', str(trial), '--condition', condition,
                     '--trace', str(trace), '--output', str(answer)]
        elif argv_tail[0] == 'grade-sheet':
            argv += ['--out', str(tmp_path / 'g')]
        elif argv_tail[0] == 'score':
            (tmp_path / 'grades.json').write_text('{}', encoding='utf-8')
            (tmp_path / 'key.json').write_text('{"assignments": {}}', encoding='utf-8')
            argv += ['--grades', str(tmp_path / 'grades.json')]
        assert bench.main(argv) == 2
        assert 'Harness' in capsys.readouterr().err
        assert manifest(experiment)['runs'] == []


class TestAddRun:
    def test_full_synthetic_bundle_is_integrity_valid(self, experiment, tmp_path, capsys):
        fill_all(experiment, tmp_path, capsys)
        report = capture.validate(experiment / 'capture.json', SUITE)
        assert report['status'] == 'integrity_valid', report['errors']
        assert report['pairs_checked'] == 21
        assert bench.status_report(experiment)['remaining'] == 0

    def test_off_run_cannot_claim_loading(self, experiment, tmp_path, capsys):
        first = order_of(experiment)[0]
        if first['condition'] == 'on':
            assert add_next(experiment, tmp_path, extra=['--skill-loaded', 'true'], capsys=capsys) == 0
        case, trial, condition = next_slot(experiment)
        assert condition == 'off'
        assert add(experiment, tmp_path, case, trial, 'off', extra=['--skill-loaded', 'true']) == 2
        assert 'control' in capsys.readouterr().err

    def test_duplicate_slot_is_rejected(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        assert add(experiment, tmp_path, case, trial, condition, capsys=capsys) == 0
        assert add(experiment, tmp_path, case, trial, condition) == 2
        assert 'already recorded' in capsys.readouterr().err
        assert len(manifest(experiment)['runs']) == 1

    @pytest.mark.parametrize('broken', ['missing', 'not_utf8'])
    def test_invalid_output_leaves_runs_and_manifest_unchanged(self, experiment, tmp_path, capsys, broken):
        """A valid trace with an invalid output is refused before any file is written."""
        case, trial, condition = next_slot(experiment)
        trace, _ = artifacts(tmp_path, 'prevalidate', output=False)
        answer = tmp_path / 'prevalidate.answer'
        if broken == 'not_utf8':
            answer.write_bytes(b'\xff\xfe not text')
        before = (experiment / 'capture.json').read_bytes()
        argv = ['add-run', str(experiment), '--case', case, '--trial', str(trial), '--condition', condition,
                '--trace', str(trace), '--output', str(answer)]
        assert bench.main(argv) != 0
        capsys.readouterr()
        assert (experiment / 'capture.json').read_bytes() == before
        assert list((experiment / 'runs').iterdir()) == []
        assert manifest(experiment)['runs'] == []

    @pytest.mark.parametrize('value', ['inf', 'nan'])
    def test_duration_must_be_finite(self, experiment, tmp_path, capsys, value):
        assert add_next(experiment, tmp_path, extra=['--duration', value]) == 2
        assert 'finite' in capsys.readouterr().err
        assert manifest(experiment)['runs'] == []

    def test_failed_run_needs_error_and_keeps_null_output(self, experiment, tmp_path, capsys):
        assert add_next(experiment, tmp_path, output=False, extra=['--status', 'timed_out']) == 2
        assert add_next(experiment, tmp_path, output=False,
                        extra=['--status', 'timed_out', '--error', 'client timeout after 600 s'],
                        capsys=capsys) == 0
        run = manifest(experiment)['runs'][0]
        assert run['output'] is None and run['execution_status'] == 'timed_out'
        assert run['error'] == 'client timeout after 600 s'
        assert run['skill_loaded'] is None

    def test_completed_run_requires_output(self, experiment, tmp_path, capsys):
        assert add_next(experiment, tmp_path, output=False) == 2

    def test_contamination_is_recorded_not_dropped(self, experiment, tmp_path, capsys):
        assert add_next(experiment, tmp_path,
                        extra=['--contaminated', 'read evals/benchmark_suite.json'], capsys=capsys) == 0
        assert manifest(experiment)['runs'][0]['protocol_contamination'] == 'read evals/benchmark_suite.json'
        assert add_next(experiment, tmp_path, capsys=capsys) == 0
        assert 'protocol_contamination' not in manifest(experiment)['runs'][1]


class TestGradeSheetAndScore:
    FAILED = ('launch-review', 2, 'off')
    CONTAMINATED = ('bag-playback', 3, 'on')

    def graded_experiment(self, experiment, tmp_path, capsys):
        special = {
            self.FAILED: dict(output=False, extra=['--status', 'failed', '--error', 'synthetic crash']),
            self.CONTAMINATED: dict(extra=['--skill-loaded', 'true', '--contaminated', 'read grading sheet']),
        }
        fill_all(experiment, tmp_path, capsys, special=special)
        out = tmp_path / 'sheets'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 0
        capsys.readouterr()
        return out

    def test_grade_sheet_refuses_an_incomplete_capture(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        loaded = 'true' if condition == 'on' else 'false'
        assert add(experiment, tmp_path, case, trial, condition,
                   extra=['--skill-loaded', loaded], capsys=capsys) == 0
        out = tmp_path / 'sheets'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        err = capsys.readouterr().err
        assert 'integrity verification' in err and 'Missing paired runs' in err
        assert not out.exists()

    def test_grade_sheet_refuses_an_output_edited_after_capture(self, experiment, tmp_path, capsys):
        fill_all(experiment, tmp_path, capsys)
        edited = sorted((experiment / 'runs').glob('*.output.md'))[0]
        edited.write_text('edited after capture\n', encoding='utf-8')
        out = tmp_path / 'sheets'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        assert 'hash mismatch' in capsys.readouterr().err
        assert not out.exists()

    @pytest.mark.parametrize('escape', ['relative', 'absolute'])
    def test_grade_sheet_never_reads_outside_the_experiment(self, experiment, tmp_path, capsys, escape):
        fill_all(experiment, tmp_path, capsys)
        outside = tmp_path / 'outside.md'
        outside.write_text('OUTSIDE SECRET TEXT\n', encoding='utf-8')
        data = manifest(experiment)
        run = next(r for r in data['runs'] if r['output'] is not None)
        # A matching hash isolates the path check from the hash check.
        run['output'] = {'path': '../outside.md' if escape == 'relative' else str(outside),
                         'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}
        (experiment / 'capture.json').write_text(json.dumps(data), encoding='utf-8')
        out = tmp_path / 'sheets'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        err = capsys.readouterr().err
        assert 'integrity verification' in err and 'inside their bundle' in err
        assert not out.exists()

    def test_grade_sheet_does_not_overwrite_recorded_grades(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades_path = out / 'grades.json'
        grades = json.loads(grades_path.read_text(encoding='utf-8'))
        case_id, rows = next(iter(grades['cases'].items()))
        rid = next(iter(rows))
        grades['cases'][case_id][rid]['C1'] = 'pass'
        grades_path.write_text(json.dumps(grades), encoding='utf-8')
        recorded = grades_path.read_bytes()
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        assert 'not empty' in capsys.readouterr().err
        assert grades_path.read_bytes() == recorded
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 0
        capsys.readouterr()
        fresh = json.loads(grades_path.read_text(encoding='utf-8'))
        assert fresh['cases'][case_id][rid]['C1'] is None

    def test_score_rejects_a_key_that_does_not_match_the_experiment(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        key_path = out / 'key.json'
        original = json.loads(key_path.read_text(encoding='utf-8'))
        grades = str(out / 'grades.json')

        def score():
            code = bench.main(['score', str(experiment), '--grades', grades])
            return code, capsys.readouterr()

        assert score()[0] == 0
        # Swap the arms of one pair.
        swapped = json.loads(json.dumps(original))
        by_slot = {(a['case_id'], a['trial'], a['condition']): rid for rid, a in swapped['assignments'].items()}
        on_rid, off_rid = by_slot[('qos-compatibility', 1, 'on')], by_slot[('qos-compatibility', 1, 'off')]
        swapped['assignments'][on_rid]['condition'] = 'off'
        swapped['assignments'][off_rid]['condition'] = 'on'
        key_path.write_text(json.dumps(swapped), encoding='utf-8')
        code, captured = score()
        assert code == 2 and 'does not match the blinded assignments' in captured.err
        # A key from a different experiment (one session id differs).
        foreign = json.loads(json.dumps(original))
        foreign['assignments'][on_rid]['session_id'] = 'other-experiment'
        key_path.write_text(json.dumps(foreign), encoding='utf-8')
        assert score()[0] == 2
        # A key missing an assignment.
        partial = json.loads(json.dumps(original))
        del partial['assignments'][on_rid]
        key_path.write_text(json.dumps(partial), encoding='utf-8')
        assert score()[0] == 2
        key_path.write_text(json.dumps(original), encoding='utf-8')
        assert score()[0] == 0

    def second_experiment(self, tmp_path, capsys, name='exp-b', seed=1):
        exp = tmp_path / name
        assert bench.main(init_args(exp, seed=seed)) == 0
        capsys.readouterr()
        return exp

    def test_score_refuses_grades_from_another_experiment(self, experiment, tmp_path, capsys):
        out_a = self.graded_experiment(experiment, tmp_path, capsys)
        exp_b = self.second_experiment(tmp_path, capsys)
        fill_all(exp_b, tmp_path, capsys)
        out_b = tmp_path / 'sheets-b'
        assert bench.main(['grade-sheet', str(exp_b), '--out', str(out_b)]) == 0
        capsys.readouterr()
        # Same suite, same seed, both complete: the blinded ids coincide, the grades must not.
        assert bench.main(['score', str(exp_b), '--grades', str(out_a / 'grades.json'),
                           '--key', str(out_b / 'key.json')]) == 2
        assert 'does not belong to this experiment' in capsys.readouterr().err
        assert bench.main(['score', str(exp_b), '--grades', str(out_b / 'grades.json')]) == 0
        capsys.readouterr()

    def test_grades_are_bound_to_answer_bytes_not_session_ids(self, tmp_path, capsys):
        """Reused --session-id values and identical answers except one still separate the digests."""
        workspaces = {}
        for name in ('a', 'b'):
            workspace = tmp_path / name
            workspace.mkdir()
            exp = self.second_experiment(workspace, capsys, name='exp', seed=1)
            workspaces[name] = (workspace, exp)
            while next_slot(exp) is not None:
                case, trial, condition = next_slot(exp)
                extra = ['--skill-loaded', 'true' if condition == 'on' else 'false',
                         '--session-id', 'sess-%s-%d-%s' % (case, trial, condition)]
                if name == 'b' and (case, trial, condition) == ('qos-compatibility', 1, 'on'):
                    trace, _ = artifacts(workspace, 'different', output=False)
                    answer = workspace / 'different.answer'
                    answer.write_text(FIXTURE_LINE + 'a different answer\n', encoding='utf-8')
                    assert bench.main(['add-run', str(exp), '--case', case, '--trial', str(trial),
                                       '--condition', condition, '--trace', str(trace),
                                       '--output', str(answer), *extra]) == 0
                    capsys.readouterr()
                    continue
                assert add(exp, workspace, case, trial, condition, extra=extra, capsys=capsys) == 0
        outs = {}
        for name, (workspace, exp) in workspaces.items():
            outs[name] = workspace / 'sheets'
            assert bench.main(['grade-sheet', str(exp), '--out', str(outs[name])]) == 0
            capsys.readouterr()
        key_a = json.loads((outs['a'] / 'key.json').read_text(encoding='utf-8'))
        key_b = json.loads((outs['b'] / 'key.json').read_text(encoding='utf-8'))
        assert key_a['assignments'] == key_b['assignments']
        assert key_a['grading_sha256'] != key_b['grading_sha256']
        exp_b = workspaces['b'][1]
        assert bench.main(['score', str(exp_b), '--grades', str(outs['a'] / 'grades.json'),
                           '--key', str(outs['b'] / 'key.json')]) == 2
        assert 'does not belong to this experiment' in capsys.readouterr().err
        assert bench.main(['score', str(exp_b), '--grades', str(outs['b'] / 'grades.json')]) == 0
        capsys.readouterr()

    def test_grading_digest_depends_on_the_suite(self, experiment, tmp_path, capsys):
        fill_all(experiment, tmp_path, capsys)
        data = manifest(experiment)
        suite, _ = bench.load_suite(SUITE)
        key = bench.blind_assignments(data, suite)
        digest = bench.grading_digest(data, key)
        assert len(digest) == 64 and int(digest, 16) >= 0
        other_suite = dict(data, suite_sha256='0' * 64)
        assert bench.grading_digest(other_suite, key) != digest
        for rid in key:
            assert 'output_sha256' in bench.grading_entries(key)[rid]
            assert 'output_sha256' not in bench.key_entries(key)[rid]

    def test_grades_and_key_share_the_digest(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades = json.loads((out / 'grades.json').read_text(encoding='utf-8'))
        key = json.loads((out / 'key.json').read_text(encoding='utf-8'))
        assert grades['grading_sha256'] == key['grading_sha256']
        assert len(grades['grading_sha256']) == 64
        data = manifest(experiment)
        suite, _ = bench.load_suite(SUITE)
        assert grades['grading_sha256'] == bench.grading_digest(data, bench.blind_assignments(data, suite))

    @pytest.mark.parametrize('mutation', ['missing', 'altered', 'schema'])
    def test_score_rejects_an_unbound_grades_file(self, experiment, tmp_path, capsys, mutation):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades_path = out / 'grades.json'
        grades = json.loads(grades_path.read_text(encoding='utf-8'))
        if mutation == 'missing':
            del grades['grading_sha256']
        elif mutation == 'altered':
            digest = grades['grading_sha256']
            grades['grading_sha256'] = ('0' if digest[0] != '0' else '1') + digest[1:]
        else:
            grades['schema_version'] = 2
        grades_path.write_text(json.dumps(grades), encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(grades_path)]) == 2
        err = capsys.readouterr().err
        assert ('schema_version' if mutation == 'schema' else 'does not belong') in err

    @pytest.mark.parametrize('mutation', ['missing', 'altered'])
    def test_score_rejects_a_key_whose_digest_was_edited(self, experiment, tmp_path, capsys, mutation):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        key_path = out / 'key.json'
        key = json.loads(key_path.read_text(encoding='utf-8'))
        if mutation == 'missing':
            del key['grading_sha256']
        else:
            key['grading_sha256'] = 'f' * 64
        key_path.write_text(json.dumps(key), encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json')]) == 2
        assert 'key.json grading_sha256' in capsys.readouterr().err

    def test_grade_sheet_refuses_a_symlinked_output_directory(self, experiment, tmp_path, capsys):
        fill_all(experiment, tmp_path, capsys)
        external = tmp_path / 'external-dir'
        external.mkdir()
        (external / 'marker.txt').write_text('keep\n', encoding='utf-8')
        out = tmp_path / 'grading'
        out.symlink_to(external, target_is_directory=True)
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        assert 'must not be a symbolic link' in capsys.readouterr().err
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 2
        capsys.readouterr()
        assert [p.name for p in external.iterdir()] == ['marker.txt']
        assert out.is_symlink()

    def test_grade_sheet_refuses_a_file_as_output_directory(self, experiment, tmp_path, capsys):
        fill_all(experiment, tmp_path, capsys)
        out = tmp_path / 'grading'
        out.write_text('not a directory\n', encoding='utf-8')
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 2
        assert 'must be a directory' in capsys.readouterr().err
        assert out.read_text(encoding='utf-8') == 'not a directory\n'

    def test_force_replaces_only_generated_files(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        (out / 'sheet-old-case.md').write_text('from an earlier suite\n', encoding='utf-8')
        (out / 'notes.txt').write_text('grader notes\n', encoding='utf-8')
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 0
        capsys.readouterr()
        assert not (out / 'sheet-old-case.md').exists()
        assert (out / 'notes.txt').read_text(encoding='utf-8') == 'grader notes\n'
        assert (out / 'grades.json').is_file() and (out / 'key.json').is_file()
        assert (out / 'sheet-qos-compatibility.md').is_file()

    @pytest.mark.parametrize('kind', ['symlink', 'directory'])
    def test_force_refuses_non_regular_generated_paths_before_removing_anything(
            self, experiment, tmp_path, capsys, kind):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades_path = out / 'grades.json'
        grades = json.loads(grades_path.read_text(encoding='utf-8'))
        case_id, rows = next(iter(grades['cases'].items()))
        grades['cases'][case_id][next(iter(rows))]['C1'] = 'pass'
        grades_path.write_text(json.dumps(grades), encoding='utf-8')
        recorded = grades_path.read_bytes()
        outside = tmp_path / 'outside.md'
        outside.write_text('untouched\n', encoding='utf-8')
        if kind == 'symlink':
            planted = out / 'sheet-qos-compatibility.md'
            planted.unlink()
            planted.symlink_to(outside)
        else:
            planted = out / 'sheet-planted.md'
            planted.mkdir()
        before = sorted(p.name for p in out.iterdir())
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 2
        err = capsys.readouterr().err
        assert 'not a regular file' in err and planted.name in err
        assert outside.read_text(encoding='utf-8') == 'untouched\n'
        assert grades_path.read_bytes() == recorded
        assert sorted(p.name for p in out.iterdir()) == before
        if kind == 'symlink':
            assert planted.is_symlink()

    def test_json_writes_never_follow_a_planted_temporary_symlink(self, experiment, tmp_path, capsys):
        outside = tmp_path / 'outside.txt'
        outside.write_text('untouched\n', encoding='utf-8')
        (experiment / 'capture.json.tmp').symlink_to(outside)
        assert add_next(experiment, tmp_path, extra=['--skill-loaded', 'true'], capsys=capsys) == 0
        assert outside.read_text(encoding='utf-8') == 'untouched\n'
        assert not (experiment / 'capture.json').is_symlink()
        fill_all(experiment, tmp_path, capsys)
        out = tmp_path / 'sheets'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 0
        capsys.readouterr()
        (out / 'grades.json.tmp').symlink_to(outside)
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 0
        capsys.readouterr()
        assert outside.read_text(encoding='utf-8') == 'untouched\n'
        assert (out / 'grades.json').is_file() and not (out / 'grades.json').is_symlink()

    def test_save_json_leaves_the_target_and_no_temporary_file_when_rename_fails(self, tmp_path, monkeypatch):
        target = tmp_path / 'data.json'
        target.write_text('{"kept": true}\n', encoding='utf-8')

        def refuse(src, dst):
            raise OSError('synthetic rename failure')

        monkeypatch.setattr(bench.os, 'replace', refuse)
        with pytest.raises(OSError, match='synthetic rename failure'):
            bench.save_json(target, {'kept': False})
        assert target.read_text(encoding='utf-8') == '{"kept": true}\n'
        assert [p.name for p in tmp_path.iterdir()] == ['data.json']

    def test_score_refuses_a_capture_edited_after_grading(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        edited = sorted((experiment / 'runs').glob('*.output.md'))[0]
        edited.write_text('edited after grading\n', encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json')]) == 2
        assert 'hash mismatch' in capsys.readouterr().err

    def test_sheets_hide_condition_filenames_and_sessions(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        sessions = {run['session_id'] for run in manifest(experiment)['runs']}
        blind_text = ''.join(p.read_text(encoding='utf-8') for p in out.glob('sheet-*.md'))
        blind_text += (out / 'grades.json').read_text(encoding='utf-8')
        for leak in ('"on"', '"off"', '-on.', '-off.', 'condition', 'runs/', '.output.md',
                     '.trace.txt', 'skill_loaded'):
            assert leak not in blind_text, leak
        for session in sessions:
            assert session not in blind_text
        key = json.loads((out / 'key.json').read_text(encoding='utf-8'))
        assert 'Do not give this file to graders' in key['warning']

    def test_failed_and_contaminated_runs_are_not_graded(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades = json.loads((out / 'grades.json').read_text(encoding='utf-8'))
        key = json.loads((out / 'key.json').read_text(encoding='utf-8'))['assignments']
        assert len(key) == 40
        assert sum(len(rows) for rows in grades['cases'].values()) == 40
        slots = {(a['case_id'], a['trial'], a['condition']) for a in key.values()}
        assert self.FAILED not in slots and self.CONTAMINATED not in slots
        assert 'synthetic crash' not in (out / 'sheet-launch-review.md').read_text(encoding='utf-8')

    def test_score_keeps_unknowns_and_separates_execution_failures(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades = json.loads((out / 'grades.json').read_text(encoding='utf-8'))
        key = json.loads((out / 'key.json').read_text(encoding='utf-8'))['assignments']
        rid_for = {(a['case_id'], a['trial'], a['condition']): rid for rid, a in key.items()}
        grades['cases']['hardware-stop'][rid_for[('hardware-stop', 1, 'on')]] = {
            'C1': 'pass', 'C2': 'pass', 'C3': 'pass'}
        grades['cases']['hardware-stop'][rid_for[('hardware-stop', 1, 'off')]] = {
            'C1': 'fail', 'C2': 'pass', 'C3': 'pass'}
        grades['cases']['hardware-stop'][rid_for[('hardware-stop', 2, 'on')]] = {
            'C1': 'pass', 'C2': 'abstain', 'C3': 'pass'}
        grades['cases']['hardware-stop'][rid_for[('hardware-stop', 2, 'off')]] = {
            'C1': 'pass', 'C2': 'fail', 'C3': 'pass'}
        (out / 'grades.json').write_text(json.dumps(grades), encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json')]) == 0
        report = json.loads(capsys.readouterr().out)
        assert 'plugin ON/OFF' in report['note'] and 'single experiment' in report['note']
        assert report['harness_revision'] == HARNESS_SHA
        assert report['skill_revision_binding'] == 'asserted'
        by_key = {(p['case_id'], p['trial']): p for p in report['pairs']}
        t1 = by_key[('hardware-stop', 1)]
        assert t1['criteria'] == {'C1': 'on_better', 'C2': 'tie', 'C3': 'tie'}
        assert t1['critical_failure'] == {'on': False, 'off': False}
        t2 = by_key[('hardware-stop', 2)]
        assert t2['criteria']['C2'] == 'unknown'
        assert t2['critical_failure'] == {'on': None, 'off': True}
        failed = by_key[('launch-review', 2)]
        assert failed['execution_status'] == {'on': 'completed', 'off': 'failed'}
        assert set(failed['criteria'].values()) == {'unknown'}
        assert failed['critical_failure'] == {'on': None, 'off': None}
        contaminated = by_key[('bag-playback', 3)]
        assert contaminated['protocol_contamination']['on'] == 'read grading sheet'
        assert set(contaminated['criteria'].values()) == {'unknown'}
        summary = report['summary']
        assert summary['execution_failures'] == {'on': 0, 'off': 1}
        assert summary['contaminated_runs'] == {'on': 1, 'off': 0}
        assert summary['criterion_outcomes']['on_better'] == 1

    @pytest.mark.parametrize('mutation', [
        'cases_list', 'case_rows_list', 'row_deleted', 'criterion_deleted', 'criterion_added',
        'row_list', 'values_changed', 'case_deleted', 'row_moved'])
    def test_score_rejects_malformed_grades(self, experiment, tmp_path, capsys, mutation):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades_path = out / 'grades.json'
        grades = json.loads(grades_path.read_text(encoding='utf-8'))
        case_id, rows = next(iter(grades['cases'].items()))
        other_case = next(c for c in grades['cases'] if c != case_id)
        rid = next(iter(rows))
        expected = {
            'cases_list': 'cases must be an object',
            'case_rows_list': 'rows for %s must be an object' % case_id,
            'row_deleted': 'do not match its blinded ids',
            'criterion_deleted': 'criteria for %s/%s do not match' % (case_id, rid),
            'criterion_added': 'criteria for %s/%s do not match' % (case_id, rid),
            'row_list': 'row %s/%s must be an object' % (case_id, rid),
            'values_changed': 'values must be',
            'case_deleted': 'cases do not match the suite',
            'row_moved': 'do not match its blinded ids',
        }[mutation]
        if mutation == 'cases_list':
            grades['cases'] = []
        elif mutation == 'case_rows_list':
            grades['cases'][case_id] = []
        elif mutation == 'row_deleted':
            del grades['cases'][case_id][rid]
        elif mutation == 'criterion_deleted':
            del grades['cases'][case_id][rid]['C2']
        elif mutation == 'criterion_added':
            grades['cases'][case_id][rid]['C99'] = 'pass'
        elif mutation == 'row_list':
            grades['cases'][case_id][rid] = []
        elif mutation == 'values_changed':
            grades['values'] = ['pass', 'fail']
        elif mutation == 'case_deleted':
            del grades['cases'][case_id]
        else:
            grades['cases'][other_case][rid] = grades['cases'][case_id].pop(rid)
        grades_path.write_text(json.dumps(grades), encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(grades_path)]) == 2
        err = capsys.readouterr().err
        assert expected in err, err
        if mutation == 'row_deleted':
            assert 'leave an ungraded cell null' in err

    def test_null_cells_are_counted_as_ungraded(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades_path = out / 'grades.json'
        grades = json.loads(grades_path.read_text(encoding='utf-8'))
        for rows in grades['cases'].values():
            for row in rows.values():
                for label in row:
                    row[label] = 'pass'

        def score():
            grades_path.write_text(json.dumps(grades), encoding='utf-8')
            assert bench.main(['score', str(experiment), '--grades', str(grades_path)]) == 0
            return json.loads(capsys.readouterr().out)['summary']['ungraded_cells']

        assert score() == 0
        case_id, rows = next(iter(grades['cases'].items()))
        grades['cases'][case_id][next(iter(rows))]['C1'] = None
        assert score() == 1

    def test_score_rejects_invalid_grade_value(self, experiment, tmp_path, capsys):
        out = self.graded_experiment(experiment, tmp_path, capsys)
        grades = json.loads((out / 'grades.json').read_text(encoding='utf-8'))
        case, rows = next(iter(grades['cases'].items()))
        rid = next(iter(rows))
        grades['cases'][case][rid]['C1'] = 'maybe'
        (out / 'grades.json').write_text(json.dumps(grades), encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json')]) == 2


class TestErrorPaths:
    def test_git_helpers_use_a_real_repository(self, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.undo()
        repo = tmp_path / 'repo'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        assert bench.git_is_dirty(repo) is False
        assert len(bench.git_head(repo)) == 40
        (repo / 'untracked.log').write_text('x', encoding='utf-8')
        assert bench.git_is_dirty(repo) is True
        with pytest.raises(bench.UsageError):
            bench.git_head(tmp_path / 'not-a-repo')

    def test_load_suite_rejects_bad_shapes(self, tmp_path):
        path = tmp_path / 'suite.json'
        path.write_text(json.dumps({'schema_version': 2}), encoding='utf-8')
        with pytest.raises(bench.UsageError):
            bench.load_suite(path)
        path.write_text(json.dumps({'schema_version': 1, 'trials': 0, 'cases': []}), encoding='utf-8')
        with pytest.raises(bench.UsageError):
            bench.load_suite(path)
        path.write_text(json.dumps({'schema_version': 1, 'trials': 1,
                                    'cases': [{'id': 'a'}, {'id': 'a'}]}), encoding='utf-8')
        with pytest.raises(bench.UsageError):
            bench.load_suite(path)

    @pytest.mark.parametrize('case', [
        {'id': 'x', 'criteria': ['a', 'b']},
        {'id': 'x', 'criteria': ['a', 'b'], 'critical_criteria': ['C1', 'C1']},
        {'id': 'x', 'criteria': ['a', 'b'], 'critical_criteria': ['C3']},
        {'id': 'x', 'criteria': ['a', 'b'], 'critical_criteria': [1]},
    ])
    def test_critical_labels_are_validated(self, case):
        with pytest.raises(bench.UsageError):
            bench.critical_labels(case)

    def test_status_requires_an_initialized_directory(self, tmp_path, capsys):
        assert bench.main(['status', str(tmp_path)]) == 2
        assert 'run init first' in capsys.readouterr().err

    def test_init_refuses_nonempty_directory(self, tmp_path, capsys):
        target = tmp_path / 'exp'
        target.mkdir()
        (target / 'stale').write_text('x', encoding='utf-8')
        assert bench.main(init_args(target)) == 2

    def test_suite_change_after_init_is_detected(self, experiment, tmp_path, capsys):
        data = manifest(experiment)
        data['suite_sha256'] = '0' * 64
        (experiment / 'capture.json').write_text(json.dumps(data), encoding='utf-8')
        assert bench.main(['status', str(experiment)]) == 2
        assert 'changed since init' in capsys.readouterr().err

    def test_add_run_rejects_unknown_case_trial_and_session_reuse(self, experiment, tmp_path, capsys):
        assert add(experiment, tmp_path, 'no-such-case', 1, 'on') == 2
        assert add(experiment, tmp_path, 'qos-compatibility', 9, 'on') == 2
        assert add_next(experiment, tmp_path, extra=['--session-id', 'S1'], capsys=capsys) == 0
        assert add_next(experiment, tmp_path, extra=['--session-id', 'S1']) == 2
        assert 'fresh session' in capsys.readouterr().err

    def test_add_run_rejects_error_on_completed_and_negative_duration(self, experiment, tmp_path, capsys):
        assert add_next(experiment, tmp_path, extra=['--error', 'x']) == 2
        assert add_next(experiment, tmp_path, extra=['--duration', '-1']) == 2

    def test_add_run_rejects_blank_trace_and_existing_artifact(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        blank = tmp_path / 'blank.trace'
        blank.write_text('   \n', encoding='utf-8')
        answer = tmp_path / 'a.md'
        answer.write_text('x', encoding='utf-8')
        code = bench.main(['add-run', str(experiment), '--case', case, '--trial', str(trial),
                           '--condition', condition, '--trace', str(blank), '--output', str(answer)])
        assert code == 2
        assert 'blank' in capsys.readouterr().err
        stale = experiment / 'runs' / ('%s-t%d-%s.trace.txt' % (case, trial, condition))
        stale.write_text('stale', encoding='utf-8')
        assert add(experiment, tmp_path, case, trial, condition) == 2

    def test_status_cli_and_empty_answer_sheet(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        trace = tmp_path / 't.trace'
        trace.write_text(FIXTURE_LINE, encoding='utf-8')
        empty = tmp_path / 'empty.md'
        empty.write_text('', encoding='utf-8')
        loaded = 'true' if condition == 'on' else 'false'
        assert bench.main(['add-run', str(experiment), '--case', case, '--trial', str(trial),
                           '--condition', condition, '--trace', str(trace), '--output', str(empty),
                           '--skill-loaded', loaded]) == 0
        assert bench.main(['status', str(experiment)]) == 0
        assert '"recorded": 1' in capsys.readouterr().out
        # An empty completed answer is a valid schema-2 artifact; grading needs the full capture.
        fill_all(experiment, tmp_path, capsys)
        assert bench.main(['grade-sheet', str(experiment), '--out', str(tmp_path / 'g')]) == 0
        sheet = (tmp_path / 'g' / ('sheet-' + case + '.md')).read_text(encoding='utf-8')
        assert '(empty answer)' in sheet

    def test_score_refuses_an_incomplete_capture_and_accepts_an_explicit_key(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        loaded = 'true' if condition == 'on' else 'false'
        assert add_next(experiment, tmp_path, extra=['--skill-loaded', loaded], capsys=capsys) == 0
        out = tmp_path / 'g'
        (out).mkdir()
        (out / 'grades.json').write_text('{"schema_version": 1, "cases": {}}', encoding='utf-8')
        (out / 'key.json').write_text('{"assignments": {}}', encoding='utf-8')
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json')]) == 2
        assert 'Missing paired runs' in capsys.readouterr().err
        fill_all(experiment, tmp_path, capsys)
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out), '--force']) == 0
        capsys.readouterr()
        moved_key = tmp_path / 'elsewhere.json'
        moved_key.write_bytes((out / 'key.json').read_bytes())
        (out / 'key.json').unlink()
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json'),
                           '--key', str(moved_key)]) == 0
        report = json.loads(capsys.readouterr().out)
        pair = next(p for p in report['pairs'] if p['case_id'] == case and p['trial'] == trial)
        assert pair['execution_status'] == {'on': 'completed', 'off': 'completed'}
        assert set(pair['criteria'].values()) == {'unknown'}

    def test_unreadable_manifest_exits_one(self, experiment, capsys):
        (experiment / 'capture.json').write_text('{bad json', encoding='utf-8')
        assert bench.main(['status', str(experiment)]) == 1
        assert 'error:' in capsys.readouterr().err


class TestRunbookWording:
    def test_activation_evidence_excludes_hook_output(self):
        text = RUNBOOK.read_text(encoding='utf-8')
        assert 'plugin hook output, or' not in text
        assert 'those hooks fire regardless of whether `SKILL.md` was loaded' in text
        assert 'explicit Skill invocation or load event' in text

    def test_claim_is_plugin_on_off_with_bound_revision(self):
        text = RUNBOOK.read_text(encoding='utf-8')
        assert text.startswith('# Benchmark runbook: plugin ON/OFF paired capture')
        assert 'version matching `skill_revision`' not in text
        assert 'checkout --detach <skill_revision>' in text
        assert '--skill-checkout /path/skill-src' in text
        assert 'frozen seeded' in text
        assert '`asserted` binding is not used for an' in text
        assert 'refuse a dirty harness checkout' in text


def test_cli_version_matches_module(capsys):
    with pytest.raises(SystemExit) as exc:
        bench.main(['--version'])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == bench.__version__
