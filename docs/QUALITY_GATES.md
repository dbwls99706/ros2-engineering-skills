# Quality and release gates

A passing gate has a bounded meaning. Do not collapse this table into a single
score or interpret an untested cell as a pass.

| Gate | Evidence | Does not prove |
|---|---|---|
| Format and packaging | `validate_skill.py`, metadata/link/version tests | Actual client activation |
| Portable installation | Staging, replacement, rollback, per-client path tests | Remote client discovery or hooks |
| Claude plugin discovery | Pinned CLI strict validation and component listing | Model-selected invocation or task quality |
| Hook protocol | Envelope, stderr/exit, advisory, timeout, notebook tests | Sandbox security or physical safety |
| Utility behavior | Full unit suite and module coverage floors | All unseen workspaces work |
| Evaluation fixtures | Declared prompts and semantic criteria exist | Any model was called |
| Captures | Complete pairs, hashes, session isolation metadata | Authenticity or semantic correctness |
| ROS builds | Existing distro container builds and generated-package tests | Field behavior or every reference snippet |
| QoS case study | Controlled DDS delivery before/after mismatch repair | General skill effectiveness or hardware behavior |
| Source freshness | Reviewed source registry and date gate | Automatic verification of linked web contents |

## Before creating a release

Run all existing tests as well as new contract tests; do not weaken or remove
regression tests to make a badge green. Keep total script coverage at least 90%
and enforce individual floors on the hook validators and new tooling. Dependency
auditing must fail visibly on findings. Keep the five existing ROS distribution
jobs; Rolling exclusions must remain explicit until their underlying runtime is
actually repaired and exercised.

Run `python3 scripts/validate_skill.py --check-sources`, inspect the actual client
versions in CI, and check a fresh installed copy outside the repository. Record
its discovery and explicit invocation in each supported client before advertising
end-to-end support. When authentic captures are available, validate the full
preregistered paired suite and review semantic outcomes, including failures.

Choose a version only after deciding the release scope. Update skill, plugin,
marketplace, eval configuration, manual report versions, version assertions,
and changelog together. Tag the reviewed commit; never tag a moving branch name
without resolving its commit. Creating a branch or passing CI is not a release.
No workflow in this repository should automatically publish a release or change
the user's source tree as a side effect of a validator run.

## Outstanding evidence requirements

Authenticated multi-client trigger/quality captures, independent technical review
of safety-sensitive guidance, and physical-hardware measurements remain distinct
requirements. They cannot be replaced by synthetic fixtures, README examples, a
successful installer, or a promised future test. Review the selected skill body's
context cost with the actual client tokenizer before claiming a token budget.
