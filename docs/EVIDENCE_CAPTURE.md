# Reproducible skill evaluations

## What exists and what it proves

`evals/eval.yaml` and its expected answers are regression fixtures.
`evals/benchmark_suite.json` preregisters a paired experiment using existing
prompts and semantic criteria. `evals/trigger_cases.json` preregisters positive,
negative, explicit, and implicit loading cases. Neither file is a model result.
No synthetic output in a unit test may be published as a real capture.

The existing `eval_runner.py` has a lexical scoring heuristic. Word overlap can
reward a wrong answer that mentions the right concepts. Treat that score as a
triage aid, not proof of correctness, safety, or an improvement attributable to
this skill. Use blinded human review or an independently validated semantic
judge for published quality comparisons. Critical safety failures fail a case
even when its total weighted score is high.

## Capture design

Use the same exact client, model, generation settings, prompt, workspace commit,
ROS distribution, RMW, and tool permissions in both conditions. Run at least the
suite's three trials per case. Alternate or randomize condition order and record
it in the trace. Each run starts in a fresh session; the control must not see the
skill, its references, expected answers, or earlier experimental outputs.
Record unedited output and an independently inspectable tool/activation trace.
Record failed, timed-out, and unfavorable trials; do not silently drop them.

The trace must identify the resolved skill path and commit in the on condition,
and absence of skill loading in the off condition. Record actual tool results,
not a model's statement that it used a tool. Keep sensitive raw transcripts local;
publish only with authorization. Redaction changes artifact bytes: document it,
rehash the published copy, and retain the original securely for verification.

## Re-evaluate on model or instruction upgrades

Use the installed model/client identifiers, not a claimed capability from a release
announcement. Preregister a comparison before editing instructions. In addition to
skill-on/off pairs, compare the previous and candidate skill revisions under the
same model and workspace; keep their manifests separate. A keyword score or a
smaller prompt alone is not evidence that the candidate helps.

The benchmark suite includes a prose-only edit, a one-line watchdog-limit change,
and an authorized commit/push task. They distinguish unnecessary ritual from
necessary ROS-specific investigation and completed work. Run the existing safety,
QoS, lifecycle, and provenance cases as well; speed gains do not excuse regressions.
For progression changes, also capture the four named progression scenarios in
`evals/eval.yaml` and manually inspect their critical criteria.

For routing, exercise `evals/trigger_cases.json` in fresh sessions with the normal
installed skill inventory, including competing skills. Record names/descriptions
actually exposed by the host, whether they were shortened, selected skill paths,
and traces of loaded references. Do not force activation when measuring implicit
routing. Explicit invocation, discovery, and correct task completion are separate.

Retain security, irreversible-action boundaries, stable project facts and mandatory
CI. Re-evaluate blanket repository scans, repeated permission requests, forced
reference reads and repeated full test runs. Compare task success, safety errors,
files read, tools/tests executed, latency and measured tokens/cost. An unavailable
metric is unknown, not zero. Stop once the preregistered criteria and required gates
are met; the purpose is measurable task value, not making the skill unavoidable.

## Manifest format

New experiments should use schema 2 below. Schema 1 remains readable for existing
complete captures; do not use it for a trial that failed or did not activate.

Use one directory per experiment, with `capture.json` and the referenced output
and trace files. All hashes below are lowercase SHA-256 of the exact bytes.
Commit revisions are full 40-character SHAs. This abbreviated shape is a schema
illustration, not an executable or completed experiment:

```json
{
  "schema_version": 2,
  "skill_revision": "<full commit SHA>",
  "suite_sha256": "<benchmark_suite.json SHA-256>",
  "client": "<actual client>",
  "client_version": "<actual version>",
  "model": "<actual model identifier>",
  "generation_parameters": {"temperature": "<actual or client default>"},
  "environment": {
    "os": "<actual OS>",
    "ros_distro": "<actual distro>",
    "rmw": "<actual RMW>",
    "workspace_revision": "<actual revision>",
    "tool_permissions": "<actual policy>"
  },
  "runs": [
    {
      "case_id": "qos-compatibility",
      "trial": 1,
      "condition": "on",
      "session_id": "<unique fresh session>",
      "skill_loaded": true,
      "execution_status": "completed",
      "captured_at": "2026-09-07T10:00:00+09:00",
      "prompt_sha256": "<exact prompt hash>",
      "output": {"path": "qos-on-1.txt", "sha256": "<hash>"},
      "trace": {"path": "qos-on-1.trace.txt", "sha256": "<hash>"}
    }
  ]
}
```

A real manifest needs every case, trial, and both conditions. Verify it with:

```bash
python3 scripts/verify_eval_capture.py /path/to/capture.json --suite evals/benchmark_suite.json
```

The command returns nonzero for missing pairs, absent data, duplicate/reused
sessions or artifacts, wrong prompt hashes, changed output bytes, invalid dates,
and escaping paths. Manifest, suite and artifact reads reject non-regular files
and are byte-bounded; artifact hashes and text checks use the same read snapshot.
These are input checks, not a sandbox against hostile concurrent filesystem edits.
It does not invoke a model, validate transcript authenticity,
verify that `skill_loaded` is true in the real client, or grade an answer.
Hashes protect integrity relative to a declared record; they do not establish
who produced that record. A reviewer must inspect the traces.

## Schema 2: preserve failed and unactivated attempts

`condition` describes skill availability, not a guarantee that the model used
it. Schema 1 required `skill_loaded: true` in the on condition and a nonempty
answer, so it could not honestly represent a missed activation or a timeout
without output. Schema 2 separates the assigned condition from those outcomes.
Keep the same suite, hashes, isolated sessions, timestamps, and complete pair
inventory. Set `schema_version` to `2` and add these fields to **each** run:

```json
{
  "execution_status": "timed_out",
  "skill_loaded": null,
  "error": "The attempt exceeded its recorded execution deadline.",
  "duration_seconds": 120.0,
  "output": null
}
```

This is a field-shape example, not an observed experiment. A real run still
requires all identity fields and its own nonempty hashed trace. Do not invent an
error, duration, session, or transcript to fill the shape.

`execution_status` is `completed`, `failed`, or `timed_out`. Failures/timeouts
require an error explanation and may record `output: null` when no response was
produced. A completed attempt requires an output artifact; an actual empty answer
is a distinct, possibly zero-byte file with its real SHA-256, not an absent file.
`skill_loaded` is `true`, `false`, or explicit `null` when observation was
unavailable. An on run with false/unknown loading stays in the denominator.
An off run with true loading is contamination and remains invalid. Optional
`duration_seconds` must be finite and nonnegative; omitting it is different from
reporting an invented zero cost or duration.

The verifier reports `execution_outcomes` and `unknown_loading_observations` only
after the complete inventory passes integrity checks. `integrity_valid` never
means that all attempts succeeded, all loading was observed, or answer quality
improved. Preserve failures in semantic grading and report unknown activation
separately; hashes still do not authenticate a client or a model.

## Report the experiment, not a marketing score

Publish the complete run inventory, per-case paired outcomes, uncertainty,
failed trials, client/model versions, hashes, costs and latency when measured,
and the exact grading rubric. Separate trigger precision/recall from output
quality and executable correctness. Report abstentions and safety violations.
A single successful example is a case study, not a general performance estimate.
There is no honest 100% conformance claim across untested future client versions.

Sources: [trigger evaluation](https://agentskills.io/skill-creation/optimizing-descriptions),
[skill iteration](https://agentskills.io/skill-creation/best-practices), and
[Claude skill evaluation](https://code.claude.com/docs/en/skills#evaluate-and-iterate-on-a-skill),
[OpenAI skill routing](https://developers.openai.com/codex/skills/),
[OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model),
and [Claude skill authoring](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
The model-upgrade guidance above was reviewed against these official authoring
pages on 2026-09-09; it makes no model-specific performance claim.
