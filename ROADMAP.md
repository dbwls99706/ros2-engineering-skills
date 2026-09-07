# Roadmap

The roadmap prioritizes evidence and usability over adding more prose.

## Near term

- Publish positive and negative trigger cases for each supported client.
- Capture reproducible skill-on and skill-off outputs with immutable metadata.
- Add an installation verifier for portable Agent Skills and Claude Code plugin
  layouts.
- Audit claims that vary by ROS 2 distribution or package major version.
- Raise validator-specific coverage for command and edit validation paths.

## Medium term

- Add small runnable workspaces for QoS, callback-group, lifecycle, and launch
  failure modes.
- Publish real case studies showing which defect was found, what evidence
  isolated it, and which verification level was reached.
- Package user-facing validators independently of the full reference corpus.
- Track reference freshness and require review when a source passes its review
  date.

## Long term

- Maintain client-neutral conformance tests for Agent Skills loading and
  progressive disclosure.
- Build distribution-specific verification fixtures from installed package
  versions rather than manually maintained assumptions.
- Establish an external technical review process for safety-sensitive guidance.
