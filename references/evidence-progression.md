# Evidence-Driven Progression

Use this reference when a ROS 2 task is blocked by an acceptance threshold,
latched failure, approval rule, commissioning policy, or a dispute about what
must be measured next. The goal is not to make a blocked system pass. The goal
is to preserve evidence while turning an unexplained stop into a reviewable
engineering decision and, where authorized, a bounded next test.

## Table of contents

1. Gate provenance
2. Authorization and readiness are separate
3. Repeated blocker to resolution plan
4. Current observation vs latched failure
5. Recovery without stale-command replay
6. Measurement and metric separation
7. User observations as evidence
8. Reporting patterns
9. Common failures and fixes

---

## 1. Gate provenance

A value in code is evidence that a gate exists, not evidence that the gate is
correct for the current purpose. Before treating a threshold, latch, count, or
readiness flag as authoritative, identify its provenance.

Record at least:

```text
Gate:
Purpose:
Source:
Measured quantity:
Measurement uncertainty:
System/error-budget relationship:
Clear or transition condition:
Change owner / version:
```

Classify the gate before deciding how changeable it is:

| Gate class | Typical source | Default treatment |
|---|---|---|
| External hard requirement | Vendor limit, interface contract, regulation | Do not change without authoritative source or requirement change |
| Safety requirement | Hazard analysis, safety architecture | Change only with explicit rationale, hazard review, and regression evidence |
| Operational limit | Measured platform performance plus margin | Recalculate from evidence when platform or operating envelope changes |
| Diagnostic heuristic | Internal warning threshold, commissioning diagnostic | Useful evidence, but not automatically a safety or field-readiness limit |
| Commissioning threshold | Initial calibration/bringup acceptance rule | Record scope, uncertainty, and when it may be revised or retired |
| Legacy/unknown | Unexplained constant or inherited policy | Find provenance before treating it as authoritative |

Two opposite mistakes are both failures:

- **Outcome-driven weakening:** a run failed, so increase the threshold until it
  passes and hide the reason.
- **Code-as-authority:** a threshold exists, so refuse to examine whether it
  measures the intended physical quantity or fits the current uncertainty.

A justified revision states the old criterion, the new criterion, the evidence
or model that motivated the change, and regression cases that fail when the new
criterion is violated. Keep the old result in the record; changing the criterion
does not retroactively make an earlier run a pass.

An unknown or disputed gate remains enforced while its provenance is reviewed.
Review is not permission to bypass an interlock or enable motion. Use read-only
inspection, simulation, isolated measurements, or the authorized change process.
Freeze revised acceptance criteria before collecting confirmation data; retain the
failed run and test the revised rule on new data rather than fitting it to that run.

## 2. Authorization and readiness are separate

Do not compress all of the following into one `approved` Boolean:

```text
Authorization:
  Has the user/operator authorized this specific action or test?

Authorization validity:
  What session/time window and attempt/retry budget does the approval cover?
  Has it expired, been revoked, or been consumed?

Authorized envelope:
  Location, motion extent, speed/torque limit, duration, operator presence,
  restraint/containment, independent stop path, and objective.

Execution authority:
  Does the active client, product, site, or safety policy permit the agent to
  execute the physical action, or is the action operator-executed?

Observed technical state:
  What is currently verified, stale, failed, or unknown?

Supervised-test readiness:
  Are the preconditions for this bounded test satisfied?

Operational readiness:
  Has the system been validated for ordinary use in the stated duty cycle?
```

These dimensions are orthogonal to L0-L6 verification. Authorization grants
permission to attempt an action inside the stated envelope; it does not prove the
action is safe, successful, or field-ready, and it does not override execution
policy. Authorization remains valid only for its stated session/time window and
until revoked. Expired, revoked, or materially changed authorization requires
renewal. Conversely, once an unchanged, unexpired, and unrevoked envelope is
established, do not repeatedly ask for the same permission. Track the allowed
attempt count too: a one-test approval is consumed when the physical test command
is issued. A read-only preflight that blocks before any command is issued does not
consume that attempt. Failure, abort, or an ambiguous command outcome is not a free
retry. Reconcile the command outcome and use only an explicitly approved remaining
retry budget; otherwise renew authorization for a new attempt. Never expand one
authorized test into an unlimited loop.

Unknown execution authority means no actuation until that boundary is resolved.
Recheck technical preconditions immediately before execution and monitor the
stated abort conditions throughout. A valid approval can coexist with lost
readiness; operator departure, stale observations, or a changed stop path blocks
the attempt without implying that the original approval never existed.

If a product, client, site, safety policy, or tool-permission boundary reserves
physical actuation to an operator, preserve the authorization state and provide
the exact bounded operator-ready next action. Name the execution-policy boundary
once; do not misreport it as missing user authorization or fall back to repeating
the same blocker.

A material envelope change needs a new authorization decision. Examples include
moving from a stand to the floor, increasing speed or travel, changing the stop
path, moving to a different site, or changing the test objective from observation
to fault injection.

Example status:

```text
Authorization: granted for this session; not revoked
Execution authority: operator-executed on physical hardware under current policy
Envelope: restrained bench, <=0.10 m/s equivalent wheel command, <=2 s, operator at e-stop
Evidence: L4; live localization and command ownership verified, no motion tested
Supervised-test readiness: ready for the stated L5 trial
Operational readiness: not established
```

## 3. Repeated blocker to resolution plan

Repeating the same blocker without new evidence is not progress. If the next
answer would merely restate the same reason, switch to a resolution plan.

Separate the work into three buckets:

1. **Code/config:** a defect, missing recovery transition, stale latch, wrong
   threshold source, or instrumentation gap that can be changed and regressed.
2. **Measurement:** evidence that must be collected from the real system, a bag,
   a calibrated target, a simulation, or an independent observer.
3. **Operator decision:** a test envelope, site constraint, risk acceptance, or
   product-policy choice that engineering evidence alone cannot decide.

Define the next test before running it:

```text
Question:
Independent variable:
Controlled variables:
Data to collect:
Pass/fail criterion and provenance:
Stop/abort condition:
What state may advance if it passes:
What remains unproven even if it passes:
```

A numeric count alone does not establish independence. Three frames from one
unchanged pose are not automatically three independent pose validations. Consider
robot position, body attitude, target geometry, range, viewing angle, time,
sensor generation/session, and environmental conditions according to the failure
hypothesis.

Distinguish statistical independence from coverage of failure modes. Repeated
measurements at a fixed pose can estimate repeatability when their correlation is
accounted for, but do not remove a common calibration bias or establish global
accuracy. Vary geometry or use an independent reference when the hypothesis needs
it. Do not demand arbitrary novelty or count adjacent frames as independent proof.

## 4. Current observation vs latched failure

A current healthy sample and a latched historical failure can both be true.
Report them separately so an operator does not read an old failure reason as a
claim that the sensor is still failing now.

Use this shape for latched or state-machine failures:

```text
Current observation:
First/last failure:
Latched state/reason:
Why the latch remains active:
Clear or transition condition:
Previous-command disposition:
```

Examples:

- A vendor pose can be fresh now while a `LOST` state remains latched because
  continuity proof was invalidated earlier.
- A link can reconnect while a lifecycle node remains in error.
- A stop trigger can clear while an e-stop correctly remains latched pending a
  deliberate reset.

Do not overwrite failure history when current data recovers. Do not describe a
latched historical reason as a fresh observation unless it was observed again.

## 5. Recovery without stale-command replay

Recovery is not the inverse of failure. A data source becoming fresh again does
not prove continuity, current pose, command ownership, or that an old command is
still valid.

For a motion-capable recovery, explicitly decide:

1. What failed: age/freshness, position jump, generation/session change,
   transport loss, transform discontinuity, or another cause.
2. Whether command output is stopped and who currently owns the command path.
3. Whether the robot is physically stationary when the recovery procedure
   assumes it is.
4. What independent observation re-establishes the state that was invalidated.
5. Whether old proof is preserved as history but excluded from the new decision.
6. Whether the previous command is invalidated. Default to **no automatic replay**
   after a stop, localization discontinuity, or lost-control episode; require a
   fresh command or goal after recovery unless the product has a separately
   validated resume protocol.

A recovery design may end in "manual restart required" when continuity cannot be
re-established safely. The skill should explain why and what evidence would be
needed to design a narrower recovery path instead of silently weakening the latch.

## 6. Measurement and metric separation

Do not substitute one convenient sensor metric for another physical quantity.
A metric can constrain another only through an explicit model with stated
uncertainty.

| Metric | Directly describes | Does not by itself prove |
|---|---|---|
| Individual LiDAR range precision | Point/range noise under stated conditions | Plane orientation or global robot yaw |
| Plane-normal fit in a narrow ROI | Orientation of the fitted local surface | Camera-LiDAR extrinsic accuracy or map localization |
| Marker/PnP pose uncertainty | Camera-to-marker estimate for that observation | LiDAR-map registration accuracy |
| Camera-LiDAR extrinsic error | Relative sensor mounting transform | Global map alignment or navigation clearance |
| Point-cloud/map residual | Fit of a registration hypothesis | Uniqueness/observability of the pose |
| Robot pose error | Actual position/orientation error under the reference used | Any one upstream metric without an error model |

Small targets, short baselines, limited fields of view, weak geometry, timestamp
skew, or poor conditioning can make orientation estimates noisier than individual
range precision suggests. State what the acceptance threshold is actually testing
before comparing a measured number with it.

## 7. User observations as evidence

Do not promote an operator observation directly to ground truth, but do not throw
it away as "anecdotal" when it can be captured reproducibly.

For a report such as "the wall still aligns from another position," preserve:

- timestamp and session/generation;
- robot pose or location label;
- target/scene and viewing geometry;
- relevant camera/LiDAR/map data or screenshot/bag when available;
- whether the observation was independent of the calibration data;
- what claim it supports: repeatability, continuity, qualitative alignment, or a
  stronger measured result.

Phrase the evidence at its actual strength. "Qualitative repeatability observed at
three poses" is useful; it is not an independent survey-accuracy measurement.

## 8. Reporting patterns

### Blocked task

```text
Blocker: <specific gate/state>
Current evidence: <what is true now>
Gate provenance: <source/purpose or unknown>
Resolution path:
  code/config: <action or none>
  measurement: <next test and independent variable>
  operator decision: <needed choice or already granted>
Advance condition: <what permits the next state>
Still unproven: <limits>
```

### Authorized supervised test

```text
Authorization: granted / not granted
Authorization validity: <session/time window; active / expired / revoked>
Execution authority: agent-executable / operator-executed / blocked by named policy
Envelope: <bounded conditions>
Preconditions: <verified / missing>
Verification before test: Lx
Test readiness: ready / blocked and why
Operational readiness: not established unless separately demonstrated
```

### Latched recovery

```text
Current observation: <fresh/current data>
Historical failure: <time/cause>
Latch: <state and persistence rule>
Recovery evidence: <what re-establishes continuity/state>
Previous command: invalidated / explicitly governed by tested resume protocol
```

## 9. Common failures and fixes

| Failure | Why it is wrong | Better approach |
|---|---|---|
| "The threshold is in code, so it is correct" | Existence is not provenance | Trace purpose, source, uncertainty, and error budget |
| "It failed, so raise the threshold" | Outcome-driven acceptance | Justify any new criterion independently and keep the failed result |
| Asking for the same permission repeatedly | Authorization state is forgotten or conflated with evidence | Preserve the valid granted envelope and check technical preconditions separately |
| Treating authorization as verification | Permission does not produce evidence | Keep authorization and L0-L6 independent |
| Treating authorization as execution authority | User permission does not override tool/site/safety policy | Preserve approval, name the execution boundary, and provide the operator-ready next action |
| Reusing expired or revoked approval | Authorization has a validity window and can be withdrawn | Renew authorization after expiry, revocation, or material envelope change |
| "One more sample" with unchanged conditions | Count is mistaken for independence | Choose a variable that tests a distinct failure mode |
| Current data is fresh, so clear LOST | Freshness is not continuity or pose proof | Separate current observation from latch-clearing evidence |
| Reconnect means resume old goal | Stale commands may no longer be valid | Invalidate old command; require fresh command unless resume was explicitly validated |
| Small-plane normal error is called robot yaw error | Different metrics are substituted | State the measured quantity and an explicit uncertainty/error model |
| Operator observation is discarded or promoted to truth | Evidence strength is misclassified | Preserve it with provenance and label the claim strength |

---

**See also:** `references/testing.md` for L0-L6 evidence levels,
`references/safety-estop.md` for stop/reset semantics,
`references/system-diagnostics.md` for cross-layer failure timelines,
and `references/sensor-integration.md` for calibration and timestamp mechanics.
