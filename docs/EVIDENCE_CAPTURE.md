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

## Manifest format

Use one directory per experiment, with `capture.json` and the referenced output
and trace files. All hashes below are lowercase SHA-256 of the exact bytes.
Commit revisions are full 40-character SHAs. This abbreviated shape is a schema
illustration, not an executable or completed experiment:

```json
{
  "schema_version": 1,
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
and escaping paths. It does not invoke a model, validate transcript authenticity,
verify that `skill_loaded` is true in the real client, or grade an answer.
Hashes protect integrity relative to a declared record; they do not establish
who produced that record. A reviewer must inspect the traces.

## Report the experiment, not a marketing score

Publish the complete run inventory, per-case paired outcomes, uncertainty,
failed trials, client/model versions, hashes, costs and latency when measured,
and the exact grading rubric. Separate trigger precision/recall from output
quality and executable correctness. Report abstentions and safety violations.
A single successful example is a case study, not a general performance estimate.
There is no honest 100% conformance claim across untested future client versions.

Sources: [trigger evaluation](https://agentskills.io/skill-creation/optimizing-descriptions),
[skill iteration](https://agentskills.io/skill-creation/best-practices), and
[Claude skill evaluation](https://code.claude.com/docs/en/skills#evaluate-and-iterate-on-a-skill).
