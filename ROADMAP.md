# Roadmap

Prioritize measured usefulness and correctness over additional prose.

## Implemented verification infrastructure

- Portable metadata, link, packaging, and source-review-date checks.
- Knowledge-only staged installation with client-specific discovery paths.
- Claude protocol adapter, notebook normalization, bounded calls, and advisory Stop.
- Positive/negative/explicit trigger cases and preregistered paired quality suite.
- Capture completeness, prompt/output/trace hashes, and session-isolation checks.
- Validator-specific coverage gates and controlled ROS QoS failure/repair example.
- Contribution, security, conduct, issue templates, and release-gate documentation.

## Next evidence to collect

- Authenticated loading and invocation traces for exact Claude, Codex, Cursor,
  and Gemini versions, including negative cases and remote execution environments.
- Complete immutable skill-on/off captures, repeated trials, semantic grading,
  failures, latency, and cost rather than fixture-derived improvement claims.
- Context-cost measurement with each target tokenizer and a smaller selected
  body that retains factual coverage and demonstrably useful routing.
- Distribution-sensitive audits backed by installed package versions and tests.
- More controlled workspaces for callback groups, lifecycle, launch, and provenance.

## Longer-term validation

- Restore Rolling runtime gates after reproducing and resolving the actual stack
  mismatch; do not remove an exclusion merely because an image builds.
- Independent review of safety-sensitive guidance and authorized hardware studies.
- Package standalone validators and establish a versioned release process after
  the relevant gates pass. Branch pushes do not publish releases.
