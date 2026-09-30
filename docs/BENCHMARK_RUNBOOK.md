# Benchmark runbook: skill-on/off paired capture

This runbook turns the preregistered experiment in `evals/benchmark_suite.json`
into a real capture with Claude Code, then into blinded human grades. It adds
no model call and produces no numbers by itself. The verifier checks file
integrity; humans grade answers; `scripts/benchmark_capture.py` connects the two.

The protocol is: 7 cases × 3 trials × {skill on, skill off} = 42 fresh sessions,
run in a preregistered order, recorded as a schema 2 manifest
(`docs/EVIDENCE_CAPTURE.md`), verified with `scripts/verify_eval_capture.py`,
graded blind, and reported per pair and per criterion.

`claude plugin eval` is not used as the execution backend in this first
benchmark. The repository's preregistered suite and blinded human-review
protocol remain the source of truth. It may be used later as an independent
cross-check.

## 1. Fix the revisions and the environment

Three identifiers are recorded separately and must not be confused:

| Field | Meaning | Source |
|---|---|---|
| `skill_revision` | The plugin source being evaluated | The commit the ON arm installs, e.g. the released tag |
| `harness_revision` | The tooling that ran the experiment | `HEAD` of the checkout providing `benchmark_capture.py` and the suite |
| `suite_sha256` | The exact rubric and protocol content | Hash of `evals/benchmark_suite.json` at `init` |

`init` refuses a dirty harness checkout, because `harness_revision` would then
not describe the code that ran. Pass `--skill-revision` explicitly whenever the
evaluated plugin is not the harness `HEAD` (it usually is not).

Record the client and model exactly as installed (`claude --version`, the model
id shown by the client), and the ROS distribution, RMW, and OS. When ROS is not
installed in the neutral workspace, record `not installed`; do not invent a value.

Create two neutral workspaces outside this repository, `bench-on/` and
`bench-off/`, with identical contents (an empty git repository is enough; the
seven prompts are self-contained and reference no workspace files). Record the
identifier you give them as `--workspace-revision`, for example
`neutral-empty-v1`. Neither workspace may contain this repository, its
`references/`, expected answers, or any earlier experiment output.

## 2. Isolate the two arms with `CLAUDE_CONFIG_DIR`

Use two configuration directories so that the plugin state never has to be
toggled during the 42 sessions:

```text
bench-config-on/    plugin installed and enabled
bench-config-off/   plugin never installed
```

Set up the ON directory with the README install commands, run under the ON
configuration:

```bash
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin marketplace add dbwls99706/ros2-engineering-skills
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin install ros2-engineering@ros2-engineering-skills
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin list --json
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin details ros2-engineering
```

Never install the plugin under `bench-config-off/`. Before every session, save
the state of the arm you are about to run:

```bash
CLAUDE_CONFIG_DIR=/path/bench-config-<arm> claude plugin list --json > preflight.json
```

The ON preflight must show `ros2-engineering` enabled with its `installPath`
and a version matching `skill_revision`. The OFF preflight must show no
`ros2-engineering` entry of any kind, including skills-directory or synced
plugins, and neither `~/.claude/skills` (under the OFF config) nor
`bench-off/.claude/skills` may contain this skill. Prepend the preflight output
to the trace file of that run.

Both configuration directories must use the same account, model, and settings.
Confirm this once before the first session and record it in the experiment
README.

These commands were confirmed on Claude Code 2.1.285 (`claude plugin
list|details|enable|disable|uninstall`). On another version, check
`claude plugin --help` first and record the version you used.

## 3. Initialize the experiment

```bash
python3 scripts/benchmark_capture.py init /path/exp-2026-10 \
  --suite evals/benchmark_suite.json \
  --client claude-code --client-version "$(claude --version)" \
  --model <model id> --seed 20261001 \
  --workspace-revision neutral-empty-v1 --tool-permissions default \
  --skill-revision <40-char SHA of the installed plugin source>
```

This writes `capture.json` (schema 2, no runs yet), `order.json`, `runs/`, and
a README stating that the experiment is incomplete. `order.json` is the
preregistered sequence: the 21 `(case, trial)` blocks are shuffled with the
seed, each block runs its two arms adjacently, and on-first versus off-first is
balanced 11:10 or 10:11. Follow it exactly; `status` prints the next runs.

## 4. Run one session

For each entry in the order:

1. Start a fresh session in the matching workspace and configuration:
   `cd bench-<arm> && CLAUDE_CONFIG_DIR=/path/bench-config-<arm> claude`.
2. Paste the prompt file named by the suite (`evals/prompts/<file>`) verbatim.
   Do not add instructions, hints, or the rubric.
3. Let the session finish or time out. Do not intervene.
4. Save the final answer verbatim as the output file and the full transcript
   or export, tool calls included, as the trace file (with the preflight output
   prepended).

**Rubric leakage rule.** During benchmark sessions, the agent must not read
`evals/benchmark_suite.json`, grading sheets, expected-answer fixtures, prior
captures, or experiment outputs. Any such access is recorded as protocol
contamination and reported rather than silently discarded. In the ON arm the
installed plugin checkout contains `evals/`, so inspect the trace for reads of
those paths. A contaminated run keeps its slot with `--contaminated <reason>`;
it is never re-run and replaced.

**Availability is not activation.** The ON arm makes the skill available;
`--skill-loaded` records what the trace shows for that session: `true` when
activation is visible (resolved skill path, plugin hook output, or the client's
activation record), `false` when the trace shows it did not activate, and
`unknown` when the trace cannot tell. A missed activation is part of the
skill's measured effect and stays in the inventory.

Record the run:

```bash
python3 scripts/benchmark_capture.py add-run /path/exp-2026-10 \
  --case hardware-stop --trial 1 --condition on \
  --trace run.trace.txt --output run.answer.md \
  --skill-loaded true --duration 412
```

A failed or timed-out session keeps its slot without an output:

```bash
python3 scripts/benchmark_capture.py add-run /path/exp-2026-10 \
  --case hardware-stop --trial 1 --condition off \
  --trace run.trace.txt --status timed_out --error "client timeout after 600 s" \
  --skill-loaded false
```

The helper copies the files into `runs/`, hashes them, stamps `captured_at`,
and refuses duplicate slots, reused session ids, an OFF run that claims
`skill_loaded=true`, a completed run without an output, and a failed run
without an error.

## 5. Verify integrity

```bash
python3 scripts/benchmark_capture.py status /path/exp-2026-10
python3 scripts/verify_eval_capture.py /path/exp-2026-10/capture.json --suite evals/benchmark_suite.json
```

`integrity_valid` means every slot is filled with hashed, isolated artifacts.
It does not mean the answers are good, that activation was observed, or that
the transcripts are authentic; a reviewer must still inspect the traces
(`docs/EVIDENCE_CAPTURE.md`, "What the verifier does not do").

## 6. Grade blind

```bash
python3 scripts/benchmark_capture.py grade-sheet /path/exp-2026-10 --out /path/exp-2026-10-grading
```

Give the grader only `sheet-<case>.md` and `grades.json`. Keep `key.json`
away from them; it unblinds the sheets. Sheets contain only completed answers
without contamination, shuffled per case under blinded ids. Failed, timed-out,
and contaminated runs are not graded by a human; the scorer counts them from
the manifest.

The grader records `pass`, `fail`, or `abstain` for every criterion of every
answer in `grades.json`. Abstain when the answer does not allow a decision.
Critical failure is decided only by the `critical_criteria` preregistered in
the suite; no other sentence is promoted to critical after the fact.

## 7. Score

```bash
python3 scripts/benchmark_capture.py score /path/exp-2026-10 --grades /path/exp-2026-10-grading/grades.json
```

For each `(case, trial)` pair and each criterion the report gives one of
`on_better`, `off_better`, `tie`, or `unknown`:

| ON | OFF | Outcome |
|---|---|---|
| pass | fail | `on_better` |
| fail | pass | `off_better` |
| pass | pass | `tie` |
| fail | fail | `tie` |
| anything else | | `unknown` |

A criterion is `unknown` whenever either arm failed, timed out, was
contaminated, or was graded `abstain` or left empty. `ON completed/pass` against
`OFF timed_out` is not `on_better`. Execution stability is reported per arm in
`execution_status` and never mixed into answer quality. `critical_failure` is
`true`, `false`, or `null` per arm; `null` means undecidable.

## 8. Report

Publish the complete run inventory, the per-pair and per-criterion outcomes,
the execution failures and contaminated runs, `skill_revision`,
`harness_revision`, `suite_sha256`, client and model versions, the exact rubric,
and cost or latency only where measured. Keep the original transcripts; if you
redact them for publication, say so and rehash the published copies.

One experiment is a case study, not a general performance estimate
(`docs/EVIDENCE_CAPTURE.md`). Report unfavorable pairs with the same prominence
as favorable ones.

## 9. What this runbook does not establish

It does not prove transcript authenticity, that the plugin activated in every
ON session, hardware safety, or an effect size beyond the captured pairs. The
lexical `eval_runner.py` pipeline is separate and is not used here.
