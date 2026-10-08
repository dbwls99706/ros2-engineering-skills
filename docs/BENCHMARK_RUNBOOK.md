# Benchmark runbook: plugin ON/OFF paired capture

This runbook turns the preregistered experiment in `evals/benchmark_suite.json`
into a real capture with Claude Code, then into blinded human grades. It adds
no model call and produces no numbers by itself. The verifier checks file
integrity; humans grade answers; `scripts/benchmark_capture.py` connects the two.

The treatment is the `ros2-engineering` plugin as a whole: its skill body plus
its `PreToolUse` and `Stop` hooks, available and enabled in the ON arm and
absent in the OFF arm. A difference between arms is therefore a plugin effect,
not a skill-body-only effect; `skill_loaded` is a per-session diagnostic, not
the treatment. The protocol is: 7 cases × 3 trials × {plugin on, plugin off} =
42 fresh sessions, run in a frozen seeded order generated before the first
session, recorded as a schema 2 manifest
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
| `skill_revision` | The skill source revision contained in the evaluated plugin bundle | `HEAD` of the checkout the ON arm installs as a local marketplace |
| `harness_revision` | The tooling that ran the experiment | `HEAD` of the checkout providing `benchmark_capture.py` and the suite |
| `suite_sha256` | The exact rubric and protocol content | Hash of `evals/benchmark_suite.json` at `init` |

`init` refuses a dirty harness checkout, because `harness_revision` would then
not describe the code that ran, and every later helper command refuses a
harness checkout that is dirty or whose `HEAD` differs from the recorded
`harness_revision`. Pass `--skill-checkout /path/skill-src` so that
`skill_revision` is taken from the checkout the ON arm actually installs
(`skill_revision_binding: verified_checkout`). Passing only `--skill-revision`
records the value as `asserted`. The first published Claude Code benchmark uses
`verified_checkout` binding; `asserted` binding is not used for an
exact-revision comparison claim.

`plugin version` is the plugin's release version string; this repository uses
semver-style versions. `skill_revision` is the exact Git commit SHA. They are
different values and neither substitutes for the other.

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

Install the ON arm from a clean checkout of the exact revision under test,
registered as a local marketplace (the marketplace manifest points its plugin
source at `./`, so the checkout itself is the plugin source):

```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills /path/skill-src
git -C /path/skill-src checkout --detach <skill_revision>
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin marketplace add /path/skill-src
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin install ros2-engineering@ros2-engineering-skills
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin details ros2-engineering
CLAUDE_CONFIG_DIR=/path/bench-config-on claude plugin list --json
```

Verify the ON installation once, in this order: the checkout is clean
(`git -C /path/skill-src status --porcelain` prints nothing); its `HEAD` equals
`skill_revision`; its `.claude-plugin/marketplace.json` names marketplace
`ros2-engineering-skills` with plugin `ros2-engineering` from source `./`, and
its `.claude-plugin/plugin.json` names `ros2-engineering`; the local marketplace
is registered; `claude plugin details ros2-engineering` lists the skill and the
hooks; `claude plugin list --json` shows the plugin enabled. `init` performs the
first three checks itself when given `--skill-checkout /path/skill-src`.
`claude plugin validate /path/skill-src` is a useful extra check where the
installed CLI provides it; it is not part of the protocol's guarantee.

Never install the plugin under `bench-config-off/`. Before every session, save
the state of the arm you are about to run:

```bash
CLAUDE_CONFIG_DIR=/path/bench-config-<arm> claude plugin list --json > preflight.json
# ON arm only:
git -C /path/skill-src status --porcelain >> preflight.json   # must print nothing
git -C /path/skill-src rev-parse HEAD >> preflight.json       # must equal skill_revision
```

The ON preflight must show `ros2-engineering` enabled, a clean skill checkout,
and a `HEAD` equal to `skill_revision`; if either git check differs, do not run
the session and restore the environment first. A session that already ran
against a changed checkout keeps its slot with `--contaminated <reason>`; it is
not replaced silently. The OFF preflight must show no
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
  --skill-checkout /path/skill-src
```

This writes `capture.json` (schema 2, no runs yet), `order.json`, `runs/`, and
a README stating that the experiment is incomplete. `order.json` is the frozen
seeded sequence: the 21 `(case, trial)` blocks are shuffled with the seed, each
block runs its two arms adjacently, and on-first versus off-first is balanced
11:10 or 10:11. Follow it exactly: `add-run` accepts only the next pending slot,
and every command re-derives the order from the seed and refuses an edited
`order.json`. A deliberate deviation must be declared with
`--out-of-order <reason>`; it is recorded as protocol contamination and scores
as `unknown`. `status` prints the next runs.

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

**Availability is not activation.** The ON arm makes the plugin available;
`--skill-loaded` records what the trace shows for that session: `true` only
when the client trace contains an explicit Skill invocation or load event for
this skill; `false` when the trace records activation events and none is for
this skill; `unknown` when only plugin hook activity is visible, when only a
generic read of `SKILL.md` is visible, or when the trace cannot decide. Plugin
hook output is evidence that the plugin was available and its `PreToolUse` and
`Stop` hooks ran; those hooks fire regardless of whether `SKILL.md` was loaded.
A missed activation is part of the plugin's measured effect and stays in the
inventory.

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
and refuses duplicate slots, slots out of the frozen run order, reused
session ids, an OFF run that claims `skill_loaded=true`, a completed run
without an output, and a failed run without an error. Every input check,
including reading and decoding both artifacts, runs before any file is copied,
so a validation refusal leaves `runs/` and `capture.json` unchanged.

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

`grade-sheet` runs the integrity verifier first and refuses a capture that is
incomplete, has an artifact whose hash no longer matches the manifest, or whose
manifest points outside the experiment directory; every answer is read through
the same hash-checked path, so an output edited after capture is never shown to
a grader. It also refuses a non-empty output directory, because re-running it
would overwrite the sheets and any grades already recorded in `grades.json`.
`--force` replaces only the generated files (`sheet-*.md`, `grades.json`,
`key.json`) and leaves unrelated files in place; it refuses to run when the
output directory or any generated path is a symbolic link or not a regular
file, and it refuses before removing anything. JSON files are written through
unpredictable temporary names, so a planted `grades.json.tmp` link is never
followed.

`grades.json` and `key.json` carry `grading_sha256`, a digest of the suite
(its criteria) and the exact blinded answer set: the assignments plus the
SHA-256 of every answer the grader saw. `score` refuses a grades file whose
digest does not match the experiment it is scoring, so grades from another
run, another suite revision, or a run that reused session ids cannot be
applied by mistake.

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

`score` re-runs the integrity verifier and re-derives the blinded assignments
from the manifest and seed. `key.json` is never trusted on its own: a key whose
arms were swapped, that belongs to another experiment, or that is missing an
assignment is rejected, so the key cannot flip `on_better` and `off_better`.
It then recomputes `grading_sha256` from the verified manifest and rejects a
`grades.json` or `key.json` whose digest differs, so only grades recorded
against this experiment's own sheets are ever combined with its key. `score`
also checks the structure of `grades.json`: every suite case, every blinded
id, and every criterion label must be present, and a value must be `pass`,
`fail`, `abstain`, or `null`. Leave an ungraded cell `null`; a deleted row or
criterion is a structural error, not an unknown, so `ungraded_cells` in the
report is exact.

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

All post-init helper commands refuse a dirty harness checkout or a `HEAD` that
differs from `capture.json.harness_revision`. Before every ON session, record
the skill checkout's clean status and `HEAD` in the trace; it must remain equal
to `skill_revision`. The first published Claude Code benchmark uses
`verified_checkout` binding; `asserted` binding is not used for an
exact-revision comparison claim.

## 9. What this runbook does not establish

It does not prove transcript authenticity, that the skill activated in every
ON session, that any difference comes from the skill body rather than the
plugin's hooks, hardware safety, or an effect size beyond the captured pairs. The
lexical `eval_runner.py` pipeline is separate and is not used here.
