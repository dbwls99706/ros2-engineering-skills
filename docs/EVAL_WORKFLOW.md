# Eval Workflow

`scripts/eval_runner.py` supports three operating modes. This document explains
each, the data files they read, and how to wire them into a manual or CI workflow.

## TL;DR

| Mode | What it scores | When to run | Cost |
|------|----------------|-------------|------|
| `structural` (default) | Expected-answer fixtures vs criteria keywords | Every CI run | 0 model calls |
| `--mode=judge` | `evals/outputs/*.md` real model captures | After capturing model output | 0 model calls (you bring the output) |
| `--parity` | Skill-on captures vs skill-off baselines | Periodically | 0 model calls (you bring both outputs) |

All three share the same morphology-tolerant keyword matcher (see
`scripts/eval_runner.py::_term_matches`); they differ only in which file backs the
comparison.

## Directory layout and path rules

```text
evals/
  eval.yaml                     # eval definitions, criteria, weights, parity config
  prompts/...                   # common prompt fixtures
  expected/...                  # common expected-answer fixtures
  progression/prompts/...       # nested prompt fixtures are allowed
  progression/expected/...      # nested expected fixtures are allowed
  outputs/<eval-name>.md         # USER FILL: real model output with skill loaded
  outputs_baseline/<eval-name>.md # USER FILL: output WITHOUT skill loaded
  history/<YYYY-MM>.jsonl        # parity history, gitignored
```

The `prompt` and `expected` fields in `eval.yaml` are paths relative to `evals/`.
They may use nested directories. `eval_runner.py` resolves them inside the eval
root and rejects path escapes. Judge and parity captures stay flat and use the
eval `name`, so a nested prompt still maps to `outputs/<eval-name>.md`.

`outputs/`, `outputs_baseline/`, and `history/` are created on first run. Only
captured outputs should be committed when sharing real baselines with collaborators.

## Mode 1 - structural

```bash
python3 scripts/eval_runner.py
```

Scores each configured expected-answer fixture against its eval criteria. This is
a structural smoke check: it catches missing fixtures, accidentally emptied
expected files, or criteria that no longer have matching content. It does **not**
invoke a model and does not score model behavior.

The default thresholds (per-criterion coverage 0.30, overall pass rate 80%) are
deliberately permissive.

### When it fails

- Expected file deleted or emptied: `Empty expected file`.
- Criteria reworded with too little keyword overlap: coverage drops below 0.30.
- Adjust strictness with `--min-coverage 0.5 --min-pass-rate 90`.

## Mode 2 - judge (real model output)

```bash
# 1. Read the eval entry in evals/eval.yaml.
# 2. Open an agent WITH the skill loaded.
# 3. Run the prompt path declared by that entry.
# 4. Save the full response as evals/outputs/<eval-name>.md.
# 5. Score it:
python3 scripts/eval_runner.py --mode=judge
```

This scores captured model output. It answers whether the model response addressed
the declared lexical criteria, but lexical scoring still is not semantic proof.
For release review, inspect critical safety/workflow criteria manually as well.

Missing capture files surface as `[SKIP]`, not `[FAIL]`. The overall status reports
`[NODATA]` if nothing was captured; the exit code stays 0 so judge mode can be
present without fabricating results.

## Mode 3 - parity (skill ON vs OFF)

```bash
# 1. Capture each selected eval in a fresh session WITHOUT the skill.
# 2. Save it as evals/outputs_baseline/<eval-name>.md.
# 3. Capture the same eval in a fresh session WITH the skill.
# 4. Save it as evals/outputs/<eval-name>.md.
# 5. Run:
python3 scripts/eval_runner.py --parity
```

Parity reports `delta = on_pass_rate - off_pass_rate`. The aggregate average delta
is compared with `parity_test.threshold` in `eval.yaml`.

Every run appends one JSON-lines entry to `evals/history/<YYYY-MM>.jsonl`. If the
most recent `consecutive_failures_for_deprecation` runs all sit below threshold,
the report marks the skill as a deprecation candidate.

### Why parity matters

Models change. A skill can become redundant or regress even while its static tests
remain green. Parity is an early-warning signal; it is not a substitute for
semantic review or real ROS execution.

## CLI cheat sheet

```bash
# Structural smoke check
eval_runner.py
eval_runner.py --min-coverage 0.5 --min-pass-rate 90

# Score captured model output
eval_runner.py --mode=judge
eval_runner.py --mode=judge --eval-name gate-policy-review

# Skill ON vs OFF
eval_runner.py --parity

# Common
eval_runner.py --json
eval_runner.py --verbose
```

## Recommended cadence

| Trigger | Run |
|---------|-----|
| Every PR/push gate | `eval_runner.py` structural smoke |
| Each release candidate | Refresh the critical `outputs/` captures and run `--mode=judge` |
| Periodically | Refresh selected ON/OFF captures and run `--parity` |

The structural gate is cheap and catches fixture rot. Judge and parity require
real captures and should never be described as having run when the files are absent.

## FAQ

**Q: Do I have to capture every configured output to run parity?**  
No. Capture the cases relevant to the experiment. Missing outputs are `[SKIP]` and
excluded from the aggregate delta. For a release claim about a specific scenario,
that scenario must have a real capture; structural fixture success is not enough.

**Q: How do I add an LLM-as-judge?**  
Out of scope for the current runner. The built-in matcher is deliberately a cheap
lexical check. A semantic judge would require a separate model call and its own
reproducibility/cost policy.

**Q: Why JSON-lines for history?**  
Append-only records parse incrementally and survive partial writes.
