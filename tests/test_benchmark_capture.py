"""Synthetic experiment fixtures only; nothing here is a model benchmark capture."""

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
    """Record every slot in the preregistered order; ``special`` overrides a slot."""
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
        assert 'preregistered order was edited' in capsys.readouterr().err
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
        assert 'Out of preregistered order' in err and '--out-of-order' in err
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
        assert bench.main(['grade-sheet', str(experiment), '--out', str(tmp_path / 'g')]) == 0
        sheet = (tmp_path / 'g' / ('sheet-' + case + '.md')).read_text(encoding='utf-8')
        assert '(empty answer)' in sheet

    def test_score_uses_explicit_key_and_reports_missing_slots(self, experiment, tmp_path, capsys):
        case, trial, condition = next_slot(experiment)
        loaded = 'true' if condition == 'on' else 'false'
        assert add_next(experiment, tmp_path, extra=['--skill-loaded', loaded], capsys=capsys) == 0
        out = tmp_path / 'g'
        assert bench.main(['grade-sheet', str(experiment), '--out', str(out)]) == 0
        capsys.readouterr()
        moved_key = tmp_path / 'elsewhere.json'
        moved_key.write_bytes((out / 'key.json').read_bytes())
        assert bench.main(['score', str(experiment), '--grades', str(out / 'grades.json'),
                           '--key', str(moved_key)]) == 0
        report = json.loads(capsys.readouterr().out)
        pair = next(p for p in report['pairs'] if p['case_id'] == case and p['trial'] == trial)
        other = 'off' if condition == 'on' else 'on'
        assert pair['execution_status'][condition] == 'completed'
        assert pair['execution_status'][other] == 'missing'
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
