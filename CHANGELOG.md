# Changelog

All notable changes are documented here.

## Unreleased

## 1.5.0 - 2026-09-08

- Retire unanswered lifecycle state reads within the existing startup deadline
  and retry only the idempotent query; activation remains one-shot. Preserve
  lost-response and persistent-failure regression cases.

- Make environment discovery, reference reading, and local validation proportional
  to the task while retaining mandatory CI and physical-safety boundaries. Verify
  remote branch state before claiming an authorized commit/push is complete.
- Add task-scope and completion cases to the paired capture suite, expand routing
  negatives, and document controlled model/skill-upgrade comparisons.
- Reject FIFOs and other non-regular capture inputs without blocking; bound reads
  and verify artifact hashes and text from one snapshot.
- Keep missing, partial, and damaged model captures separate from scored results;
  require valid ON/OFF pairs before reporting a parity delta. Confine evaluation
  inputs and history to their declared directories and reject malformed manifests.
- Scope parity history to the runner, suite, fixtures, version, and scoring rules;
  do not count absent data or repeated captures as new deprecation evidence.
  Add explicit capture-completeness gates and critical lexical criteria while
  retaining the requirement for independent semantic review.
- Bound physical-test approval by its attempt budget as well as its session and
  envelope. Recheck readiness before execution; a failed or ambiguous command is
  not an automatic retry, and reviewing a gate does not authorize bypassing it.
- Align controlled-motion verification levels and clarify independent measurements;
  fix the container CI example's shell and remove its stale ROS-prefix cache.

- Add evidence-driven progression rules for acceptance gates: review threshold
  provenance, measured quantity, uncertainty, error-budget relevance, and clearing
  conditions instead of treating existing code as automatic authority or tuning
  criteria merely to make a failed run pass.
- Separate user authorization, authorization validity, execution authority,
  supervised-test readiness, L0-L6 evidence, and operational readiness. Preserve
  unchanged, unexpired, unrevoked approvals without turning permission into proof
  or overriding client, product, site, or safety execution policy.
- Give L5 and L6 distinct execution preconditions, retain operator-only execution
  for high-risk physical fault injection, and repair stale numbered-principle links
  so detailed references point to the engineering-principles source of truth.
- Turn repeated blockers into concrete resolution plans with independent variables,
  required evidence, pass/fail criteria, and stop conditions; distinguish current
  healthy observations from latched historical failures and prevent stale-command
  replay during recovery.
- Add canonical progression behavior fixtures for gate-policy review, supervised
  test authorization, latched localization recovery, and sensor-metric separation.
  Public calibration examples are explicitly synthetic, and the structural runner
  remains clearly separated from real model-output judging.
- Correct perception and deployment reference guidance: qualify image transport
  and copy-avoidance claims, check timestamp/clock evidence before queue tuning,
  complete the health-monitor example, and distinguish real device
  acknowledgements from fixed shutdown sleeps.
- Stabilize generated-fleet readiness acceptance by retrying idempotent read
  services within bounds, requiring a brief stable-active window, and keeping
  sibling transition churn out of the transition-event-loss negative control.

## 1.4.0 - 2026-09-08

- Make generated lifecycle startup independent of transition-event delivery: query
  the named node's actual state, request activation once after configuration, and
  fail with its last observed state if startup cannot complete within the deadline.
  Apply the same behavior to single-node and multi-robot launch files. See the
  [startup regression](docs/LIFECYCLE_STARTUP.md).
- Exercise real fleet startup with transition-event callbacks deliberately
  discarded, while retaining service, parameter, sibling-isolation, and child-exit
  assertions. Keep the original runtime checks and time limits.
- Align skill, plugin, marketplace, eval configuration, and manual report versions
  at 1.4.0. Standalone tool-interface versions and newly scaffolded user package
  versions retain their separate version schemes.

- Repair Python fleet launch installation, required lifecycle namespaces, scoped
  startup transitions, and parameter-file matching after fleet remapping.
- Declare the generated C++ configuration parameter and delegate generated
  C++/Python lifecycle transitions to managed entities, preserving their failures.
- Reject nonfinite/out-of-range plain-node timer rates, unsafe package symlinks,
  and metadata newlines before they can produce invalid code or overwrite files.
- Detect a missing literal lifecycle namespace in the launch validator without
  guessing the contents of dynamic keyword arguments.
- Add live three-variant fleet checks, actual managed-publisher tests, and
  structured child start/exit evidence; a zero launcher exit alone is not a pass.

- Isolate the never-activated runtime control from later active inputs so queued
  pre-activation samples are not mistaken for inactive publication.

- Execute the documented lifecycle examples against real ROS: delegate C++
  publisher activation/deactivation to the base callbacks and unregister Python
  lifecycle publishers on cleanup, shutdown, and transition error recovery.
- Keep repeatable runtime regressions for actual filtered output and released
  publisher objects; a successful lifecycle state transition alone is not a pass.

- Fix generated C++ component target linkage and exercise component builds in
  the ROS distribution matrix, alongside Python lifecycle variants.
- Make generated lifecycle configuration repeatable, reject invalid timer rates,
  and release ordinary timers on deactivation, cleanup, shutdown, and errors.
- Add real generated-node lifecycle acceptance checks, including positive timer
  controls, repeated transitions, rejected rates, and recovery after rejection.
- Provide a transferable Humble runtime for local tests; isolate modern developer
  pytest dependencies from the distribution's ROS testing plugins.

- Preserve measured position targets on hardware-template deactivation instead
  of confusing a zero position command with stopping an actuator.

- Handle already-shut-down contexts and external shutdown in generated Python
  entry points; clean up after constructor or node-destruction failures too.
- Require generated smoke-test processes to exit cleanly after discovery instead
  of accepting an exception or forced termination during shutdown.

- Keep the ROS smoke observer and its executor on the same initialized context;
  verify cleanup after initialization, discovery, and shutdown failures.

- Resolve generated package manifest schemas through a pinned, hash-checked local
  XML catalog so network-isolated ROS tests retain real xmllint validation.
  Require positive and negative schema controls instead of disabling the linter.

- Reduce eagerly selected instructions while retaining detailed principles and all
  pitfall entries; preserve factual regressions and test every direct route.
- Add explicit selected-body byte/line limits and named BPE-tokenizer measurements
  that fail visibly when tokenizer data is unavailable or the budget is exceeded.
- Add capture schema 2 to retain missed activations, failed/timed-out attempts,
  absent responses, and actual empty outputs without inventing benchmark results.
- Enforce hook input limits in UTF-8 bytes and reject ambiguous JSON, invalid
  working-directory types, and non-Boolean Stop continuation flags.
- Bound unit-test execution and retain environment, JUnit, coverage, and timeout
  diagnostics; use concise parameter IDs for oversized-input regressions.

- Separate dependency-image builds from uncached ROS test execution; use isolated
  BuildKit and bounded, named runtime containers with retained diagnostics.
- Replace daemon-based smoke discovery with unique graph names and owned process
  groups; preserve all stable ROS gates and explicit Rolling runtime exclusions.
- Require local ROS runners to execute tests, not merely build an image, and add
  a CI summary gate that rejects failed, cancelled, skipped, or missing jobs.
- Preserve recoverable installation backups even when both replacement and
  rollback fail; serialize portable installers and report retained backups.
- Align portable Agent Skills metadata with the public specification.
- Add a standard Claude Code plugin manifest and hook location.
- Qualify unsupported ROS 2 generalizations in the core guidance.
- Replace the speculative README diagnosis with an evidence-first example.
- Add contribution, security, conduct, and roadmap documents.
- Make dependency vulnerability auditing a required CI gate.
- Clarify validator Python coverage and refresh roadmap items.
- Add a documented Claude hook protocol adapter with bounded execution,
  NotebookEdit handling, warning context, and non-blocking Stop notices.
- Add a read-only skill/package/source-date validator and Codex display metadata.
- Add staged knowledge-only installation for Codex, Claude Code, Cursor, and Gemini.
- Document current primary-source client rules and separate verified gates from
  uncollected authenticated activation and model-quality evidence.
- Preregister activation and paired quality cases; reject missing, reused, or
  tampered capture artifacts without inventing model results.
- Add regression tests, individual validator coverage gates, Python 3.13/3.14
  matrix targets, portable Windows smoke checks, and a controlled QoS experiment.
- Preserve existing ROS distro builds and explicitly retain Rolling runtime limits.
- Add isolated live Codex/Gemini discovery probes with pinned versions, negative
  controls, resolved installation paths, and retained CI evidence.

### Known limitations

- One local Cyclone DDS immediate-shutdown attempt timed out and was not
  reproduced in subsequent diagnostic trials. This is separate from the repaired
  startup path; its root cause remains unconfirmed. See [local evidence](docs/LOCAL_RUNTIME.md).
- Rolling's documented runtime exclusions remain in place; successful build jobs
  do not establish the excluded runtime behavior.
- Client discovery and isolated ROS software tests are not authenticated
  multi-client quality benchmarks or physical robot safety certification.

## 1.3.0

- Added runtime provenance and cross-layer system diagnosis references.
- Added explicit L0-L6 verification levels.
- Expanded end-to-end stop-path and field-diagnosis guidance.
- Updated the documented ROS 2 distribution matrix.

## 1.2.0

- Corrected Nav2 distribution naming and recovery-safety guidance.
- Added manual validation modes and factual regression tests.
- Reduced duplicated always-loaded eval metadata.

## 1.1.0

- Hardened path handling and clarified validation boundaries.
- Added dependency vulnerability awareness in CI.
