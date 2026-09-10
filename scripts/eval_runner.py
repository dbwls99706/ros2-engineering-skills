#!/usr/bin/env python3
"""Skills 2.0 Eval Runner — Structural smoke check for skill evals.

Loads eval definitions from eval.yaml, validates that prompt/expected files
exist, and reports whether each expected file's text addresses the keywords
declared in its criteria. This is a *structural* check: it does not invoke
Claude, does not execute the skill, and does not measure model accuracy.

A criterion "passes" when at least 30% of its non-stop-word tokens appear
(case-insensitively) in the expected file. The threshold and matching are
deliberately permissive — the goal is to catch missing or empty fixtures,
not to score model output. Treat the pass rate as a fixture-quality
indicator, not a benchmark.

Usage:
    python eval_runner.py [--eval-dir DIR] [--eval-name NAME] [--json] [--verbose]

Exit codes:
    0 - No scored failures; exploratory missing-data states are not a pass
    1 - Scored failure, invalid data, history failure, or required capture missing
    2 - Configuration error or lexical deprecation review signal (parity)
"""

__version__ = '1.2.0'

import argparse
from datetime import datetime, timezone
import hashlib
import math
import ntpath
import json
import os
import re
import stat
import sys
import time


def _import_yaml():
    """Import PyYAML lazily, only on the execution path that needs it.

    Importing (or asking for --help from) this module must not require
    PyYAML: the module is imported by the test suite and the CLI should
    print usage on a bare environment. Only actually running evals needs
    the dependency, so the friendly failure lives here, not at module
    import time.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        print('Error: PyYAML is required to run evals — install the '
              'development dependencies first: '
              'pip install -r requirements-dev.txt', file=sys.stderr)
        sys.exit(2)
    return yaml


MAX_TEXT_BYTES = 20 * 1024 * 1024
NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
DEVICE_PATTERN = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def _assessment_scope():
    """Lexical matches, including critical ones, never certify answer quality."""
    return {
        'scoring_method': 'lexical_coverage',
        'quality_verdict': 'not_assessed',
        'semantic_review_required': True,
    }


def _valid_name(value):
    return (isinstance(value, str) and NAME_PATTERN.fullmatch(value) is not None
            and not value.endswith('.') and DEVICE_PATTERN.match(value) is None)


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _resolve_within(base_dir, rel_path):
    """Resolve a portable relative path, rejecting escapes and absolute paths.

    This is a snapshot check, not a sandbox against concurrent hostile writes.
    """
    if (not isinstance(rel_path, str) or not rel_path.strip()
            or '\x00' in rel_path or '\\' in rel_path
            or ntpath.splitdrive(rel_path)[0] or os.path.isabs(rel_path)):
        return None
    try:
        base = os.path.realpath(base_dir)
        candidate = os.path.realpath(os.path.join(base, rel_path))
        if os.path.commonpath([base, candidate]) != base:
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return candidate


def _read_text(filepath):
    """Read bounded regular UTF-8 text; never turn an I/O error into a score."""
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(filepath, flags)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Not a regular file: {filepath}')
        if info.st_size > MAX_TEXT_BYTES:
            raise ValueError(f'File exceeds {MAX_TEXT_BYTES} bytes: {filepath}')
        data = handle.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f'File exceeds {MAX_TEXT_BYTES} bytes: {filepath}')
    return data.decode('utf-8')


def load_eval_config(eval_dir):
    """Reject malformed manifests, including duplicate keys and ambiguous names."""
    yaml = _import_yaml()

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ValueError(f'Duplicate YAML key: {key}')
                result[key] = loader.construct_object(value_node, deep=deep)
            except TypeError as exc:
                raise ValueError('YAML mapping keys must be scalar') from exc
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    # YAML implicitly types ISO dates/timestamps. Keep manifest metadata JSON
    # compatible, with the same spelling and hash as an explicitly quoted value.
    UniqueLoader.add_constructor('tag:yaml.org,2002:timestamp', UniqueLoader.construct_scalar)
    config_path = _resolve_within(eval_dir, 'eval.yaml')
    try:
        if config_path is None:
            raise ValueError('eval.yaml escapes eval directory')
        config = yaml.load(_read_text(config_path), Loader=UniqueLoader)
        _validate_config_shape(config)
    except (OSError, UnicodeError, ValueError, RecursionError, yaml.YAMLError) as exc:
        print(f'Error: invalid eval.yaml: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
    return config


def _validate_config_shape(config):
    if not isinstance(config, dict):
        raise ValueError('eval.yaml must be a YAML mapping')
    entries = config.get('evals')
    if not isinstance(entries, list) or not entries:
        raise ValueError('"evals" must be a non-empty list')
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or not _valid_name(entry.get('name')):
            raise ValueError('Each eval must have a portable, non-empty name')
        name = entry['name'].casefold()
        if name in names:
            raise ValueError(f'Duplicate eval name: {entry["name"]}')
        names.add(name)
    try:
        json.dumps(config, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Manifest must contain finite JSON-compatible values') from exc
    parity = config.get('parity_test')
    if parity is not None:
        if not isinstance(parity, dict):
            raise ValueError('parity_test must be a mapping')
        threshold = parity.get('threshold', 5.0)
        count = parity.get('consecutive_failures_for_deprecation', 3)
        if not _finite_number(threshold) or not -100 <= threshold <= 100:
            raise ValueError('parity threshold must be finite, in [-100, 100]')
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError('consecutive_failures_for_deprecation must be an integer in [1, 1000]')


def validate_eval_entry(entry, eval_dir):
    """Validate fields and fixture paths without mutating the manifest."""
    if not isinstance(entry, dict):
        return ['Eval entry must be a mapping']
    errors = []
    for field in ('name', 'prompt', 'expected', 'criteria'):
        if field not in entry:
            errors.append(f'Missing required field: {field}')
    if not _valid_name(entry.get('name')):
        errors.append('Eval name must be a portable filename, not a path')
    for field in ('prompt', 'expected'):
        if field not in entry:
            continue
        path = _resolve_within(eval_dir, entry[field])
        if path is None:
            errors.append(f'{field.title()} path escapes eval dir or is not relative: {entry[field]!r}')
        elif not os.path.isfile(path):
            errors.append(f'{field.title()} file not found or not regular: {path}')
    criteria = entry.get('criteria')
    if not isinstance(criteria, list) or not criteria:
        errors.append('"criteria" must be a non-empty list')
    else:
        ids = set()
        for i, criterion in enumerate(criteria):
            if isinstance(criterion, dict):
                description = criterion.get('description')
                if not isinstance(description, str) or not description.strip():
                    errors.append(f'Criterion {i} missing "description" or empty text')
                if 'critical' in criterion and type(criterion['critical']) is not bool:
                    errors.append(f'Criterion {i} critical must be Boolean')
                if 'id' in criterion:
                    identifier = criterion['id']
                    if not _valid_name(identifier) or identifier in ids:
                        errors.append(f'Criterion {i} id must be unique and portable')
                    else:
                        ids.add(identifier)
            elif not isinstance(criterion, str) or not criterion.strip():
                errors.append(f'Criterion {i} must be a non-empty string or dict')
        try:
            pairs = _extract_criteria_with_weights(criteria)
            total = sum(weight for _, weight in pairs)
            if not _finite_number(total) or total <= 0:
                errors.append('Criteria must have a positive finite total weight')
        except ValueError as exc:
            errors.append(str(exc))
    if 'timeout' in entry:
        if not _finite_number(entry['timeout']):
            errors.append('"timeout" must be a finite number')
        elif entry['timeout'] <= 0:
            errors.append('"timeout" must be positive')
    return errors


def load_file_content(filepath):
    """Compatibility helper. Scoring uses _read_text to retain actual errors."""
    try:
        return _read_text(filepath)
    except (OSError, UnicodeError, ValueError):
        return ''


def extract_criteria_text(criteria):
    """Extract text descriptions from criteria list (supports str and dict)."""
    texts = []
    for c in criteria:
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, dict):
            texts.append(c.get('description', str(c)))
    return texts


def _term_matches(term, expected_lower):
    """Return True if *term* matches any word in *expected_lower*.

    Matching is morphology-tolerant: a 4+ char term matches any word whose
    word boundary shares the first 4 chars AND where either side is a prefix
    of the other. This handles common inflections without a full stemmer:

        criterion 'paths'    -> matches 'path' / 'paths' / 'pathway' (no — 'paths' is not a prefix of 'pathway')
        criterion 'warnings' -> matches 'warn' / 'warning' / 'warnings' / 'warned'
        criterion 'service'  -> matches 'services' / 'serviced'
        criterion 'process'  -> 'proces' false-positive avoided (no 'proces' in real text)

    Words under 4 chars fall back to exact word-boundary matching so short
    common tokens (e.g. 'qos', 'tf') do not over-match.
    """
    if len(term) < 4:
        return re.search(r'\b' + re.escape(term) + r'\b',
                         expected_lower) is not None
    # 4-char prefix anchor: cheap pre-filter so we only run the prefix test
    # on candidate words, not on every word in the file.
    prefix = re.escape(term[:4])
    pattern = r'\b' + prefix + r'\w*\b'
    for match in re.finditer(pattern, expected_lower):
        word = match.group(0)
        # Bidirectional prefix check: morphological variant must share a stem.
        # 'paths' / 'path': 'path' (4) is prefix of both -> the shorter side
        # is always a prefix of the longer. Reject if neither is a prefix
        # of the other (e.g. 'paths' vs 'patient' — share 'pat' only).
        if word.startswith(term) or term.startswith(word):
            return True
    return False


def evaluate_criteria(expected_content, criteria_texts,
                      coverage_threshold=0.30, min_terms=3):
    """Evaluate criteria against expected content.

    This is a structural validation — it checks that the expected file
    contains content that addresses each criterion via morphology-tolerant
    keyword matching (see _term_matches).

    Args:
        expected_content: Text of the expected file.
        criteria_texts: List of criterion description strings.
        coverage_threshold: Fraction of key terms that must match for the
            criterion to pass. Default 0.30 (a permissive structural check,
            not a benchmark). Configurable via --min-coverage CLI flag.
        min_terms: Minimum number of meaningful key terms a criterion must
            have to be evaluated; shorter criteria are not informative enough
            to score and fail by default. Default 3.

    Returns:
        list[dict]: Results for each criterion with pass/fail and details.
    """
    results = []
    for criterion_text in criteria_texts:
        # Extract key terms from the criterion for matching
        key_terms = []
        words = criterion_text.lower().split()
        # Filter out common words to find meaningful key terms
        stop_words = {
            'must', 'should', 'the', 'a', 'an', 'is', 'are', 'for',
            'and', 'or', 'of', 'in', 'to', 'with', 'that', 'this',
            'be', 'have', 'has', 'not', 'from', 'at', 'by', 'on',
        }
        for word in words:
            cleaned = word.strip('"\'.,;:!?()[]{}')
            if cleaned and cleaned not in stop_words and len(cleaned) > 2:
                key_terms.append(cleaned)

        expected_lower = expected_content.lower()
        matched_terms = [t for t in key_terms
                         if _term_matches(t, expected_lower)]
        coverage = len(matched_terms) / max(len(key_terms), 1)
        passed = (len(key_terms) >= min_terms
                  and coverage >= coverage_threshold)

        results.append({
            'criterion': criterion_text,
            'passed': passed,
            'coverage': round(coverage, 2),
            'matched_terms': matched_terms,
            'total_terms': len(key_terms),
        })

    return results


def _extract_criteria_with_weights(criteria_entries):
    """Return declared weights; malformed values must never silently default."""
    pairs = []
    for criterion in criteria_entries:
        if isinstance(criterion, str):
            pairs.append((criterion, 1.0))
        elif isinstance(criterion, dict):
            text = criterion.get('description')
            weight = criterion.get('weight', 1.0)
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Criterion must have a non-empty description')
            if not _finite_number(weight) or weight < 0:
                raise ValueError('Criterion weight must be a finite non-negative number')
            pairs.append((text, float(weight)))
        else:
            raise ValueError('Criterion must be a string or dict')
    return pairs


def _content_path_for_source(eval_dir, eval_name, source):
    if source == 'expected':
        return None
    if not _valid_name(eval_name) or source not in ('output', 'baseline'):
        raise ValueError('Invalid capture name or source')
    sub = {'output': 'outputs', 'baseline': 'outputs_baseline'}[source]
    logical_root = os.path.join(eval_dir, sub)
    if os.path.lexists(logical_root) and not os.path.isdir(logical_root):
        raise ValueError(f'{sub} is not a readable directory')
    root = _resolve_within(eval_dir, sub)
    if root is None:
        raise ValueError(f'{sub} escapes eval directory')
    candidate = _resolve_within(root, f'{eval_name}.md')
    if candidate is None:
        raise ValueError(f'Capture escapes {sub} directory')
    return candidate


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def run_eval(entry, eval_dir, verbose=False,
             coverage_threshold=0.30, pass_rate_threshold=80.0,
             content_source='expected'):
    """Score valid text only; missing captures and damaged files are distinct."""
    start = time.monotonic()
    name = entry.get('name', '<unknown>') if isinstance(entry, dict) else '<unknown>'

    def failure(errors, status='error'):
        return {**_assessment_scope(), 'name': name, 'status': status, 'errors': errors,
                'execution_time_ms': round((time.monotonic() - start) * 1000, 1),
                'criteria_results': [], 'pass_rate': 0.0,
                'content_source': content_source}

    errors = validate_eval_entry(entry, eval_dir)
    if content_source not in ('expected', 'output', 'baseline'):
        errors.append('Unknown content source')
    if (not _finite_number(coverage_threshold) or not 0 <= coverage_threshold <= 1
            or not _finite_number(pass_rate_threshold) or not 0 <= pass_rate_threshold <= 100):
        errors.append('Invalid scoring thresholds')
    if errors:
        return failure(errors)
    try:
        prompt_path = _resolve_within(eval_dir, entry['prompt'])
        expected_path = _resolve_within(eval_dir, entry['expected'])
        if prompt_path is None or expected_path is None:
            raise ValueError('Fixture path escapes eval directory')
        prompt_content = _read_text(prompt_path)
        fixture = _read_text(expected_path)
        if not prompt_content.strip():
            return failure([f'Empty prompt file: {prompt_path}'])
        if not fixture.strip():
            return failure([f'Empty expected file: {expected_path}'])
        content_path = expected_path
        if content_source == 'expected':
            content = fixture
        else:
            content_path = _content_path_for_source(eval_dir, name, content_source)
            # A dangling link is damaged input, not an absent capture.
            logical = os.path.join(eval_dir, {'output': 'outputs', 'baseline': 'outputs_baseline'}[content_source],
                                   f'{name}.md')
            if not os.path.exists(content_path) and not os.path.lexists(logical):
                result = failure([], 'skipped')
                result['reason'] = f'No {content_source} captured yet: {content_path}'
                return result
            content = _read_text(content_path)
        if not content.strip():
            return failure([f'Empty {content_source} file: {content_path}'])
    except (OSError, UnicodeError, ValueError) as exc:
        return failure([f'Cannot read eval input: {exc}'])

    pairs = _extract_criteria_with_weights(entry['criteria'])
    criteria_results = evaluate_criteria(content, [text for text, _ in pairs],
                                         coverage_threshold=coverage_threshold)
    critical_failures = []
    for i, (result, (_, weight), criterion) in enumerate(zip(criteria_results, pairs, entry['criteria'])):
        result['weight'] = weight
        critical = isinstance(criterion, dict) and criterion.get('critical', False)
        result['critical'] = critical
        if critical and not result['passed']:
            critical_failures.append(criterion.get('id', f'criterion_{i}'))
    total = sum(weight for _, weight in pairs)
    passed_weight = sum(result['weight'] for result in criteria_results if result['passed'])
    pass_rate = passed_weight / total * 100
    lexical_status = 'pass' if pass_rate >= pass_rate_threshold and not critical_failures else 'fail'
    # A matching response still needs an independent review of its meaning.
    # Keep fixture smoke checks usable as CI gates without promoting an
    # unreviewed model response (including a negated rubric) to a passing answer.
    status = ('needs_review' if content_source != 'expected' and lexical_status == 'pass'
              else lexical_status)
    result = {
        **_assessment_scope(),
        'name': name, 'status': status, 'lexical_status': lexical_status,
        'pass_rate': round(pass_rate, 1),
        'passed_criteria': sum(result['passed'] for result in criteria_results),
        'total_criteria': len(criteria_results),
        'weighted_passed': round(passed_weight, 2), 'weighted_total': round(total, 2),
        'execution_time_ms': round((time.monotonic() - start) * 1000, 1),
        'token_estimate': (len(prompt_content) + len(content)) // 4,
        'criteria_results': criteria_results, 'critical_failures': critical_failures,
        'tags': entry.get('tags', []), 'content_source': content_source,
        'content_sha256': _digest(content), 'prompt_sha256': _digest(prompt_content),
        'fixture_sha256': _digest(fixture),
    }
    if verbose:
        result.update(prompt_path=prompt_path, expected_path=content_path,
                      prompt_length=len(prompt_content), expected_length=len(content))
    return result


def run_all_evals(config, eval_dir, eval_name=None, verbose=False,
                  coverage_threshold=0.30, pass_rate_threshold=80.0,
                  content_source='expected'):
    """Run all evals (or a specific one) and return aggregate results.

    Returns:
        dict: Aggregate results including per-eval and summary data.
    """
    _validate_config_shape(config)
    evals = config['evals']
    if eval_name:
        evals = [e for e in evals if e.get('name') == eval_name]
        if not evals:
            print(f'Error: eval "{eval_name}" not found', file=sys.stderr)
            sys.exit(2)

    results = []
    for entry in evals:
        result = run_eval(
            entry, eval_dir, verbose=verbose,
            coverage_threshold=coverage_threshold,
            pass_rate_threshold=pass_rate_threshold,
            content_source=content_source)
        results.append(result)

    total_evals = len(results)
    passed_evals = sum(1 for r in results if r.get('lexical_status') == 'pass')
    review_evals = sum(1 for r in results if r['status'] == 'needs_review')
    failed_evals = sum(1 for r in results if r['status'] == 'fail')
    error_evals = sum(1 for r in results if r['status'] == 'error')
    skipped_evals = sum(1 for r in results if r['status'] == 'skipped')
    scored_count = passed_evals + failed_evals
    avg_pass_rate = (
        sum(r['pass_rate'] for r in results
            if r['status'] in ('pass', 'fail', 'needs_review')) / scored_count
        if scored_count > 0 else 0.0
    )
    total_time = sum(r['execution_time_ms'] for r in results)

    # Overall: 'no_data' if everything skipped (no model outputs captured),
    # 'fail' if any scored eval failed or errored, otherwise 'pass'. The
    # 'no_data' state matters for judge mode in CI - we do not want a clean
    # green when the user simply has not pasted anything yet.
    if failed_evals == 0 and error_evals == 0:
        overall = ('no_data' if scored_count == 0 else 'partial') if skipped_evals else 'pass'
        if overall == 'pass' and review_evals:
            overall = 'needs_review'
    else:
        overall = 'fail'

    return {
        **_assessment_scope(),
        'skill': config.get('skill', '<unknown>'),
        'version': config.get('version', '<unknown>'),
        'classification': config.get('classification', '<unknown>'),
        'deprecation_risk': config.get('deprecation-risk', '<unknown>'),
        'content_source': content_source,
        'summary': {
            'total_evals': total_evals,
            'passed': passed_evals,
            'failed': failed_evals,
            'errors': error_evals,
            'skipped': skipped_evals,
            'needs_review': review_evals,
            'average_pass_rate': round(avg_pass_rate, 1),
            'total_execution_time_ms': round(total_time, 1),
            'overall_status': overall,
        },
        'evals': results,
        'parity_test': config.get('parity_test', None),
    }


def run_parity_test(config, eval_dir, verbose=False,
                    coverage_threshold=0.30, pass_rate_threshold=80.0):
    """Compare valid ON/OFF pairs, retaining absent and invalid evidence."""
    _validate_config_shape(config)
    parity_cfg = config.get('parity_test') or {}
    threshold = float(parity_cfg.get('threshold', 5.0))
    consec = parity_cfg.get('consecutive_failures_for_deprecation', 3)
    reports = [run_all_evals(config, eval_dir, verbose=verbose,
                             coverage_threshold=coverage_threshold,
                             pass_rate_threshold=pass_rate_threshold, content_source=source)
               for source in ('output', 'baseline')]
    deltas, per_eval, captures, fixtures = [], [], [], []
    for on, off in zip(reports[0]['evals'], reports[1]['evals']):
        item = {'name': on['name']}
        if 'error' in (on['status'], off['status']):
            item.update(status='error', errors=on.get('errors', []) + off.get('errors', []))
        elif 'skipped' in (on['status'], off['status']):
            item.update(status='skipped', reason=on.get('reason') or off.get('reason'))
        elif (on['prompt_sha256'], on['fixture_sha256']) != (off['prompt_sha256'], off['fixture_sha256']):
            item.update(status='error', errors=['Fixtures changed between ON and OFF scoring'])
        else:
            delta = on['pass_rate'] - off['pass_rate']
            deltas.append(delta)
            item.update(status='scored', skill_on_pass_rate=on['pass_rate'],
                        skill_off_pass_rate=off['pass_rate'], delta=round(delta, 2),
                        meets_threshold=delta >= threshold)
            captures.append([on['name'], on['content_sha256'], off['content_sha256']])
            fixtures.append([on['name'], on['prompt_sha256'], on['fixture_sha256']])
        per_eval.append(item)
    errors = sum(item['status'] == 'error' for item in per_eval)
    skipped = sum(item['status'] == 'skipped' for item in per_eval)
    data_status = ('error' if errors else 'no_data' if not deltas
                   else 'partial' if skipped else 'complete')
    average = sum(deltas) / len(deltas) if deltas else None
    threshold_met = average is not None and average >= threshold
    manifest = {key: value for key, value in config.items() if not key.startswith('_')}
    # Do not incorporate cached paths from older callers into the experiment ID.
    manifest['evals'] = [{key: value for key, value in entry.items() if not key.startswith('_')}
                         for entry in config['evals']]
    scope = _digest(json.dumps([__version__, manifest, fixtures, coverage_threshold,
                               pass_rate_threshold], sort_keys=True, allow_nan=False))
    capture_id = _digest(json.dumps(captures, sort_keys=True)) if deltas else None
    record = {
        **_assessment_scope(),
        'history_schema': 2, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'scope_sha256': scope, 'capture_sha256': capture_id, 'data_status': data_status,
        'avg_delta': round(average, 2) if average is not None else None,
        'threshold': threshold, 'threshold_met': threshold_met,
        'scored_evals': len(deltas), 'skipped_evals': skipped, 'error_evals': errors,
        'total_evals': len(per_eval), 'per_eval': per_eval,
    }
    history_path = None
    history_error = None
    deprecation = False
    try:
        history_dir = _resolve_within(eval_dir, 'history')
        if history_dir is None:
            raise ValueError('History directory escapes eval directory')
        os.makedirs(history_dir, exist_ok=True)
        history_path = _resolve_within(history_dir, time.strftime('%Y-%m', time.gmtime()) + '.jsonl')
        if history_path is None:
            raise ValueError('History file escapes history directory')
        # O_NONBLOCK avoids hanging on an accidentally supplied FIFO.
        fd = os.open(history_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NONBLOCK', 0), 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError('History destination is not a regular file')
            handle.write(json.dumps(record, allow_nan=False) + '\n')
        if data_status == 'complete':
            deprecation = _check_deprecation_streak(history_dir, threshold, consec, scope)
    except (OSError, UnicodeError, ValueError) as exc:
        history_error = str(exc)
    result = {**record, 'skill': config.get('skill', '<unknown>'),
              'version': config.get('version', '<unknown>'), 'mode': 'parity',
              'consecutive_failures_for_deprecation': consec,
              'deprecation_candidate': deprecation, 'history_file': history_path}
    if history_error:
        result['history_error'] = history_error
    return result


def _check_deprecation_streak(history_dir, threshold, consec_required, scope=None):
    """Require complete, distinct, same-scope captures. Legacy rows are not evidence."""
    if scope is None or type(consec_required) is not int or consec_required <= 0:
        return False
    try:
        entries = []
        for name in sorted(os.listdir(history_dir)):
            if not name.endswith('.jsonl'):
                continue
            path = _resolve_within(history_dir, name)
            if path is None:
                return False
            for line in _read_text(path).splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                if entry.get('history_schema') != 2 or entry.get('scope_sha256') != scope:
                    continue
                if entry.get('data_status') != 'complete':
                    continue
                if (not isinstance(entry.get('timestamp_utc'), str)
                        or not isinstance(entry.get('capture_sha256'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', entry['capture_sha256'])
                        or not _finite_number(entry.get('avg_delta'))
                        or entry.get('threshold') != threshold
                        or type(entry.get('scored_evals')) is not int
                        or entry['scored_evals'] <= 0
                        or entry['scored_evals'] != entry.get('total_evals')
                        or entry.get('skipped_evals') != 0 or entry.get('error_evals') != 0):
                    return False
                entries.append(entry)
    except (OSError, UnicodeError, ValueError):
        return False
    # Re-scoring old files must not move them ahead of a newer successful trial.
    first_seen = {}
    for entry in sorted(entries, key=lambda item: item['timestamp_utc']):
        first_seen.setdefault(entry['capture_sha256'], entry)
    recent = sorted(first_seen.values(), key=lambda item: item['timestamp_utc'],
                    reverse=True)[:consec_required]
    return len(recent) == consec_required and all(entry['avg_delta'] < threshold for entry in recent)


def print_report(report):
    """Print a human-readable eval report."""
    print('=' * 70)
    print(f'  Lexical Eval Report: {report["skill"]} v{report["version"]}')
    print(f'  Classification: {report["classification"]}  |  '
          f'Deprecation Risk: {report["deprecation_risk"]}')
    print('=' * 70)
    print('  Quality verdict: NOT ASSESSED; semantic review needed for quality claims.')
    print('  Percentages measure keyword coverage, not accuracy or safety.')
    print()

    summary = report['summary']
    skipped = summary.get('skipped', 0)
    status_icon = {'pass': 'PASS', 'partial': 'PARTIAL', 'no_data': 'NODATA',
                   'needs_review': 'REVIEW'}.get(
        summary['overall_status'], 'FAIL')
    parts = [
        f'  Lexical checks: [{status_icon}]',
        f'{summary["passed"]}/{summary["total_evals"]} matched',
        f'({summary["average_pass_rate"]}% avg lexical score)',
        f'{summary["total_execution_time_ms"]:.0f}ms total',
    ]
    if skipped:
        parts.insert(2, f'skipped: {skipped}')
    print('  '.join(parts))
    source = report.get('content_source', 'expected')
    if source == 'expected':
        print('  Content source: expected fixtures; no model was invoked.')
    else:
        print(f'  Content source: {source} '
              f'(use --mode=structural for the fixture-only smoke check)')
    print()

    for ev in report['evals']:
        if ev['status'] == 'skipped':
            print(f'  [SKIP] {ev["name"]}')
            reason = ev.get('reason', 'no content available')
            print(f'         {reason}')
            print()
            continue
        icon = {'pass': 'PASS', 'fail': 'FAIL', 'needs_review': 'REVIEW'}.get(ev['status'], 'ERR ')
        print(f'  [{icon}] {ev["name"]}')
        print(f'         Lexical score: {ev["pass_rate"]}%  '
              f'({ev.get("passed_criteria", 0)}/{ev.get("total_criteria", 0)} criteria)  '
              f'{ev["execution_time_ms"]:.1f}ms')

        if ev.get('errors'):
            for err in ev['errors']:
                print(f'         ERROR: {err}')

        if ev.get('criteria_results'):
            for cr in ev['criteria_results']:
                cr_icon = '+' if cr['passed'] else '-'
                print(f'         [{cr_icon}] {cr["criterion"][:60]}...'
                      if len(cr['criterion']) > 60
                      else f'         [{cr_icon}] {cr["criterion"]}')
        print()

    if report.get('parity_test') and report['parity_test'].get('enabled'):
        pt = report['parity_test']
        print('-' * 70)
        print(f'  Parity Test: enabled (threshold: {pt.get("threshold", 5.0)}%)')
        print(f'  Consecutive failures for deprecation: '
              f'{pt.get("consecutive_failures_for_deprecation", 3)}')
        print()

    print('=' * 70)


def _print_parity_report(report):
    """Human-readable parity report (skill ON vs OFF)."""
    print('=' * 70)
    print(f'  Lexical Parity Test: {report["skill"]} v{report["version"]}')
    print(f'  Threshold: avg_delta >= {report["threshold"]}%  |  '
          f'Deprecation streak: {report["consecutive_failures_for_deprecation"]}')
    print('=' * 70)
    print('  Quality verdict: NOT ASSESSED; semantic review needed for quality claims.')
    print('  Deltas compare keyword coverage, not measured model improvement.')
    print()
    threshold_icon = 'MET' if report['threshold_met'] else 'MISS'
    delta_text = f'{report["avg_delta"]:+.2f}%' if report['avg_delta'] is not None else 'NODATA'
    print(f'  Average delta:        {delta_text}  [{threshold_icon}]')
    print(f'  Data status:          {report.get("data_status", "complete")}')
    if report.get('history_error'):
        print(f'  History ERROR:        {report["history_error"]}')
    print(f'  Scored evals:         {report["scored_evals"]}  '
          f'(skipped: {report["skipped_evals"]})')
    if report['deprecation_candidate']:
        print(f'  *** DEPRECATION CANDIDATE: most recent '
              f'{report["consecutive_failures_for_deprecation"]} runs all '
              f'below threshold ***')
    print(f'  History file:         {report["history_file"]}')
    print()
    print('-' * 70)
    for ev in report['per_eval']:
        if ev['status'] == 'skipped':
            print(f'  [SKIP] {ev["name"]:40s}  {ev["reason"]}')
        elif ev['status'] == 'error':
            print(f'  [ERR] {ev["name"]}: {ev.get("errors", [])}')
        else:
            icon = '+' if ev['meets_threshold'] else '-'
            print(f'  [{icon}]    {ev["name"]:40s}  '
                  f'on={ev["skill_on_pass_rate"]:5.1f}%  '
                  f'off={ev["skill_off_pass_rate"]:5.1f}%  '
                  f'delta={ev["delta"]:+.1f}%')
    print('=' * 70)


def main():
    parser = argparse.ArgumentParser(
        description='Skills 2.0 Eval Runner - Lexical fixture and capture checks')
    parser.add_argument(
        '--eval-dir', default=None,
        help='Directory containing eval.yaml (default: evals/ relative to skill root)')
    parser.add_argument(
        '--eval-name', default=None,
        help='Run a specific eval by name')
    parser.add_argument(
        '--json', action='store_true', dest='json_output',
        help='Output results as JSON')
    parser.add_argument(
        '--verbose', action='store_true',
        help='Include additional details in output')
    parser.add_argument(
        '--min-coverage', type=float, default=0.30,
        metavar='FRAC',
        help=('Per-criterion key-term coverage required to pass '
              '(default: 0.30 - permissive structural smoke check)'))
    parser.add_argument(
        '--min-pass-rate', type=float, default=80.0,
        metavar='PCT',
        help=('Overall weighted pass rate (0-100) required for an eval to '
              'be considered passing (default: 80.0)'))
    parser.add_argument(
        '--mode',
        choices=['structural', 'judge'],
        default=None,  # resolved to 'structural' below; None distinguishes
                       # an explicit --mode from the default for the
                       # --parity exclusivity check
        help=('structural (default): check expected fixtures. judge: check '
              'keyword coverage in captured outputs under evals/outputs/. '
              'Neither mode assesses answer quality; see docs/EVAL_WORKFLOW.md'))
    parser.add_argument(
        '--parity', action='store_true', default=False,
        help=('Run parity test: score skill ON (evals/outputs/) vs OFF '
              '(evals/outputs_baseline/), append delta to evals/history/, '
              'and flag deprecation candidacy if recent runs miss threshold. '
              'Mutually exclusive with --mode.'))
    parser.add_argument(
        '--require-complete', action='store_true',
        help='Fail judge/parity mode if any selected capture is missing')
    parser.add_argument(
        '--version', action='version',
        version=f'%(prog)s {__version__}')

    args = parser.parse_args()
    if not 0.0 <= args.min_coverage <= 1.0:
        print(
            f'Error: --min-coverage must be in [0.0, 1.0], '
            f'got: {args.min_coverage}', file=sys.stderr)
        sys.exit(2)
    if not 0.0 <= args.min_pass_rate <= 100.0:
        print(
            f'Error: --min-pass-rate must be in [0.0, 100.0], '
            f'got: {args.min_pass_rate}', file=sys.stderr)
        sys.exit(2)

    # Determine eval directory
    if args.eval_dir:
        eval_dir = args.eval_dir
    else:
        skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        eval_dir = os.path.join(skill_root, 'evals')

    config = load_eval_config(eval_dir)

    if args.parity:
        if args.mode is not None:
            print('Error: --parity and --mode are mutually exclusive; '
                  'parity is its own scoring mode.', file=sys.stderr)
            sys.exit(2)
        if args.eval_name:
            print('Error: --parity does not support --eval-name; runs all '
                  'evals to compute aggregate delta.', file=sys.stderr)
            sys.exit(2)
        report = run_parity_test(
            config, eval_dir, verbose=args.verbose,
            coverage_threshold=args.min_coverage,
            pass_rate_threshold=args.min_pass_rate)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            _print_parity_report(report)
        # Missing a lexical threshold once is informational. Damaged data or
        # history is an error; release mode also requires complete captures.
        if (report['data_status'] == 'error' or report.get('history_error')
                or (args.require_complete and report['data_status'] != 'complete')):
            sys.exit(1)
        sys.exit(2 if report['deprecation_candidate'] else 0)

    mode = args.mode or 'structural'
    content_source = 'output' if mode == 'judge' else 'expected'
    report = run_all_evals(config, eval_dir,
                           eval_name=args.eval_name,
                           verbose=args.verbose,
                           coverage_threshold=args.min_coverage,
                           pass_rate_threshold=args.min_pass_rate,
                           content_source=content_source)

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    status = report['summary']['overall_status']
    # Exploratory missing data stays distinct from PASS. Release checks must
    # request complete captures explicitly; invalid data always fails.
    incomplete = report['summary']['skipped'] > 0
    sys.exit(1 if status == 'fail' or (args.require_complete and incomplete) else 0)


if __name__ == '__main__':
    main()
