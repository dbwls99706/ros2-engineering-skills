# Roadmap

Prioritize measured usefulness and correctness over additional prose.

## Implemented verification infrastructure

- Portable metadata, link, packaging, and source-review-date checks.
- Short selected body, preserved detailed references, direct-route regressions,
  and a named-reference-tokenizer measurement gate.
- Versioned capture records that retain unsuccessful and unactivated attempts.
- Knowledge-only staged installation with client-specific discovery paths.
- Claude protocol adapter, notebook normalization, bounded calls, and advisory Stop.
- Positive/negative/explicit trigger cases and preregistered paired quality suite.
- Capture completeness, prompt/output/trace hashes, and session-isolation checks.
- Validator-specific coverage gates and controlled ROS QoS failure/repair example.
- Contribution, security, conduct, issue templates, and release-gate documentation.

Local Humble acceptance now also covers component/lifecycle generator variants,
repeated lifecycle transitions, dynamic component loading, parameter provenance,
and controlled QoS/callback behavior. See [the record](docs/LOCAL_RUNTIME.md).

## Next evidence to collect

- Authenticated loading and invocation traces for exact Claude, Codex, Cursor,
  and Gemini versions, including negative cases and remote execution environments.
- Complete immutable skill-on/off captures, repeated trials, semantic grading,
  failures, latency, and cost rather than fixture-derived improvement claims.
- Actual-provider context-cost and task-trace measurements beyond the named
  reference tokenizer gate; verify that the smaller body improves routing.
- Distribution-sensitive audits backed by installed package versions and tests.
- More controlled workspaces for callback groups, lifecycle, launch, and provenance.

## Longer-term validation

- Restore Rolling runtime gates after reproducing and resolving the actual stack
  mismatch; do not remove an exclusion merely because an image builds.
- Independent review of safety-sensitive guidance and authorized hardware studies.
- Package standalone validators and establish a versioned release process after
  the relevant gates pass. Branch pushes do not publish releases.
