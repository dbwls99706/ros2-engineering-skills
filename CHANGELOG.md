# Changelog

All notable changes are documented here.

## Unreleased

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
