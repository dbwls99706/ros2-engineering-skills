# Diagnostic and artifact review cases

These four synthetic cases preregister failure modes for offline ROS 2 diagnosis:
invalid ray diagnostics, saved-artifact adoption and identity, set-only state
counterexamples, and rejection on an unrelated metric. They contain no production
recording, private map, or measured model result.

Use `evals/diagnostic_review_suite.json` with the existing paired capture contract
in `docs/EVIDENCE_CAPTURE.md`: three trials per case, skill on/off, independent
sessions, unchanged prompts, and retained first failures. Keep the rubric outside
the model input and grade every listed criterion semantically. Twelve complete
pairs would require 24 sessions; none is supplied here.

```bash
python3 scripts/verify_eval_capture.py /path/to/capture.json --suite evals/diagnostic_review_suite.json
```

The command validates supplied capture integrity/completeness, not correctness.
The new pytest checks validate guidance structure, case files, and the executable
synthetic counterexample only. They do not invoke a model. The existing
`evals/eval.yaml` lexical fixtures and their history remain separate and unchanged
apart from the bundle version.
