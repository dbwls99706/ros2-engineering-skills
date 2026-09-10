# Eval Workflow

`scripts/eval_runner.py` performs lexical checks, not model inference or semantic
judging. Structural fixtures, real captured outputs, and paired comparisons are
separate evidence sources. None establishes robot safety.

## Modes

| Mode | Input | Meaning |
|---|---|---|
| `structural` (default) | Configured expected-answer fixtures | Fixture smoke check; no model is invoked |
| `--mode=judge` | Recorded model responses | Lexical coverage only; manually review critical criteria |
| `--parity` | Skill-on and skill-off captures | Difference between valid paired lexical scores |

All modes use the morphology-tolerant keyword matcher. It cannot reliably detect
negation, fabricated facts, unsafe advice, or whether a tool actually executed.
A criterion marked `critical: true` must pass even when the weighted total passes;
that flag does not turn the matcher into a semantic safety checker.

Every JSON report, case result, and parity-history record exposes
`scoring_method: lexical_coverage`, `quality_verdict: not_assessed`, and
`semantic_review_required: true`. The review requirement applies to model-quality
claims, not to completing packaging checks or ordinary code changes.
`pass_rate` and delta fields are lexical
metrics. Matching captured answers have `lexical_status: pass` but
`status: needs_review`; only fixture checks can produce a `pass` status.
Even 100% coverage is not a quality pass:
an answer can negate or quote every criterion and still match all its words.
Text reports label this scope before showing scores. Exit 0 and
`--require-complete` establish neither semantic correctness nor model improvement.
Keep independently reviewed semantic outcomes alongside the authenticated captures
described in [EVIDENCE_CAPTURE.md](EVIDENCE_CAPTURE.md).

## Files and validation

```text
evals/
  eval.yaml                     # manifest: names, paths, criteria, weights
  prompts/...                   # common prompt fixtures
  expected/...                  # common expected-answer fixtures
  progression/prompts/...       # nested fixtures are supported
  progression/expected/...
  outputs/<eval-name>.md         # real skill-on capture
  outputs_baseline/<eval-name>.md # real skill-off capture
  history/<YYYY-MM>.jsonl        # append-only parity records
```

Manifest `prompt` and `expected` paths are relative to `evals/`. Nested paths are
supported, but absolute paths, Windows paths, and symlink escapes are rejected.
Capture names are unique portable filenames: letters, digits, `.`, `_`, `-`, with
no separators, trailing dot, or Windows device names. Case-only duplicates are
rejected too. Empty suites, duplicate YAML keys, malformed criteria, and nonfinite
or negative weights are errors, not passing empty evaluations.

Inputs must be regular UTF-8 text files no larger than 20 MiB. These are snapshot
checks, not a sandbox against concurrent hostile filesystem changes. Create capture
directories when collecting responses. Only deliberately shared, sanitized captures
should be committed; their existence or hashes do not authenticate them.

## Structural smoke check

```bash
python3 scripts/eval_runner.py
python3 scripts/eval_runner.py --min-coverage 0.5 --min-pass-rate 90
```

The defaults are 0.30 per-criterion keyword coverage and 80% weighted pass rate.
They catch absent or empty fixtures and rubric drift; they do not measure model
behavior. An empty or unreadable fixture is an error.

## Captured-output review

Run each manifest-declared prompt in the actual client/model with the skill loaded.
Save the full response at `outputs/<eval-name>.md`. Keep the client/model version,
source revision, prompt, transcript, tool permissions, and first failed attempts.

```bash
# Exploratory review permits incomplete collection, but reports it distinctly.
python3 scripts/eval_runner.py --mode=judge

# Release checks require every selected capture.
python3 scripts/eval_runner.py --mode=judge --require-complete
python3 scripts/eval_runner.py --mode=judge --require-complete --eval-name gate-policy-review
```

Absent captures are `[SKIP]`. No captured cases produce `[NODATA]`; a matching subset
with missing cases produces `[PARTIAL]`. A complete matching set produces
`[REVIEW]`, never `[PASS]`, because its meaning has not been assessed. Exploratory missing-data
states retain exit 0. A damaged, empty, or non-UTF-8 file is an error, not a scored
zero; errors take precedence over missing data. `--require-complete` exits 1 for
any missing selected capture.

A genuine empty model response needs an explicit execution record. A zero-byte
file alone cannot distinguish that outcome from a damaged capture. The separate
capture protocol (`verify_eval_capture.py`) can retain empty responses and failed
attempts without inventing output. Require that protocol for comparative claims,
and independently assess each criterion against the response and execution trace.
Completeness and integrity checks do not authenticate a model session.

## Paired comparisons and history

Capture the same selected prompts in fresh isolated sessions with the skill off
and on. Save both sets before running:

```bash
python3 scripts/eval_runner.py --parity
python3 scripts/eval_runner.py --parity --require-complete --json
```

Parity runs the configured suite; `--eval-name` is not supported with `--parity`.
Only pairs with valid scored files on both sides contribute to
`delta = on_pass_rate - off_pass_rate`. A broken baseline is not 0%.
`data_status` is `complete`, `partial`, `no_data`, or `error`. With no scored pairs,
`avg_delta` is `null`, not an invented zero. Errors exit 1; strict completeness
also rejects partial or absent pairs.

Every attempt appends a history-schema-2 record when storage succeeds. Storage
failures are reported and exit 1. A deprecation review signal requires complete,
distinct capture sets in the same manifest, fixtures, bundle version, runner
version, and scoring-threshold scope. Unscoped legacy history is not evidence.
Rerunning unchanged files cannot manufacture trials or move old failures ahead of
a newer success: first observations determine the ordering.

This is a lexical review signal, not proof of independent experiments or model
quality. Distinct hashes can reflect formatting changes, not distinct sessions;
identical valid responses can also occur in independent sessions. Use traceable
captures and human review rather than treating this conservative deduplication as
a statistical independence test.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No scored failure; inspect status because exploratory missing data is not PASS |
| 1 | Scored failure, invalid input, history-write failure, or required capture missing |
| 2 | Configuration/CLI error, or lexical deprecation review signal in parity mode |

## Recommended use

Run structural checks on every push. For release claims about model behavior,
collect the required real captures, use `--require-complete`, and manually review
the critical criteria. Periodically refresh paired captures for regression review.
Do not report that judge/parity validation ran successfully when its captures were
absent. Changes to the evidence workflow do not themselves establish that a model
has passed the new behavioral scenarios.
