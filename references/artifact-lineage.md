# Offline ROS Artifacts and Lineage

Use this reference for saved ROS maps, point clouds, bag-derived data, and cleanup
backends. It concerns what produced an artifact and what was saved or selected,
not a general data-platform architecture. For a running node's installed prefix,
parameters, or publishers, use `references/runtime-provenance.md` instead.

## Table of contents

1. Preserve inputs and identify the pipeline
2. Backend execution is not artifact adoption
3. Compare identities, not only counts
4. Establish state semantics before a sweep
5. Bound the finding and the decision

---

## 1. Preserve inputs and identify the pipeline

Start from supplied artifacts and relevant code. An offline review need not start
a live ROS graph and does not authorize changes to an operational map. Keep
original inputs and installed presets intact. Authorized experiments write to a separate
output directory; promotion or deployment remains a separate action.

For each relevant processing edge, capture what is available and mark missing
provenance explicitly:

```text
Input: <path, content hash, session and scan/time interval>
Representation: <schema, frame, units, timestamps, point/record identity>
Transform/preprocessing: <pose source, calibration, filters, grid and resolution>
Implementation: <source revision, actual binary/module identity, dependencies>
Invocation: <command, effective configuration, backend and seed if relevant>
Candidate: <output path/hash and comparison scope>
Selection: <accept/reject/fallback decision and criterion>
Saved output: <writer path, resulting hash, reload/comparison result>
```

Capture inclusion boundaries explicitly, including whether an end scan/time is
inclusive. A matching filename, timestamp, or point count is not proof of lineage.
Matching coordinates can support content equivalence but do not prove ancestry:
independent runs or a common parent may produce the same content. Without a
processing record linking inputs to outputs, report equivalence or a lineage
hypothesis rather than inventing a historical chain.

## 2. Backend execution is not artifact adoption

Trace the caller through backend selection, candidate return, acceptance checks,
fallback, writer, and reload. A success or `applied` flag may describe only the
backend invocation; read its actual contract. It does not alone establish that
the caller selected that result or that the saved map contains it.

Compare the saved artifact with the selected candidate under the declared
comparison rule. Retain fallback reasons and candidate outputs so a successful
helper is not mistaken for a deployed change. A viewer or in-memory preview is
not a saved-output check.

When rebuilding a helper for experiments, first compare the unmodified rebuild
with the installed baseline on identical inputs and configuration. Different
binary hashes establish different bytes, not necessarily different behavior.
Matching output on one corpus supports equivalence on that corpus, not every
input or bit-identical builds. Keep implementation identity and behavioral
equivalence as separate claims.

## 3. Compare identities, not only counts

Choose the comparison relation before interpreting a result:

- **Byte equality:** hash the original serialized files. This tests byte identity,
  not semantic correctness, provenance, or tolerance-based geometric agreement.
- **Exact record/point identity:** use stable input IDs where possible. Otherwise
  declare the fields and canonicalization that define identity. Preserve duplicate
  multiplicity with a multiset when duplicates matter; a set drops that evidence.
- **Geometric equivalence:** state frames, transforms, units, precision, tolerance,
  and correspondence rules. Voxelization or radius matching can merge distinct
  points; do not label approximate agreement as exact reproduction.

For removal comparisons, fix the original input universe and compare the actual
removed identities. Report both `A - B` and `B - A`, not just `len(A) == len(B)`.
Both empty establishes equality under the chosen identity rule. Only `A - B`
empty establishes subset inclusion, not equality or a proper subset; a proper
subset also needs a nonempty reverse difference. Subset inclusion alone says
nothing about whether the extra removals are correct.

Declare handling of duplicates, nonfinite values, missing fields, and float
rounding. When a stage changes coordinates or resamples points, do not subtract
raw coordinate sets as though original identities survived. Preserve a mapping
back to input IDs or report a separately validated approximate comparison.

For example, removing IDs `{1, 2}` and `{1, 3}` gives the same count but different
results. A comparator is also a diagnostic: test equal, unequal-but-equal-count,
duplicate, and tolerance-boundary controls relevant to its claim. See
`references/evidence-progression.md` section 6.

## 4. Establish state semantics before a sweep

Inspect the pinned implementation and caller, not just a flag name. Distinguish
monotone accumulation, idempotence, and order independence; none is a synonym for
the others. A flag that never clears within one run does not prove the output is
monotone across different runs with different inputs.

A sufficient condition for subset dominance is a fixed input/output universe and
fixed per-input contributions `C_i`, accumulated as `U(S) = union(C_i for i in S)`.
For `S` contained in `T`, `U(S)` is contained in `U(T)`. The final result must also
be a monotone function of that state for the same query points. Only then can a
subset not exceed the full result for that property. Union also ignores order
and duplicate contributions under these conditions.

Check whether selecting or reordering inputs changes poses, registration,
calibration, adaptive thresholds, grid origin/resolution, preprocessing, per-scan
contributions, reset/decay/pruning, early exit, or the final query. A set-only flag
inside such a pipeline is insufficient to skip the experiments. Prove the needed
property for the whole path or retain a bounded distinguishing test.

This synthetic example uses nonempty integer sequences. Its set only grows within
a run, but the first input changes how later contributions are selected:

```python
def accumulate_hits(samples):
    anchor = samples[0]
    hits = set()
    for sample in samples:
        if abs(sample - anchor) <= 1:
            hits.add(sample)
    return hits


assert accumulate_hits([0, 4, 5]) == {0}
assert accumulate_hits([4, 5]) == {4, 5}
assert accumulate_hits([4, 0, 5]) == {4, 5}
```

Even when dominance is established, prefix tests may still locate when new
contributions appear, identify a minimal sufficient prefix, or measure cost.
Marginal contribution of zero in that run does not prove those inputs are useless
under other geometry or settings. Do not label that attribution experiment a
search for a subset that beats a proven full-run bound.

## 5. Bound the finding and the decision

Report input scope, comparison relation, diagnostic/control results, target
quality metrics and preservation constraints, selection/writer evidence, and
remaining uncertainty. Keep candidate evaluation distinct from promotion.
For cleanup, measure intended removals and static-structure damage separately,
with denominators and region/object definitions; aggregate removal count is not
a quality score. Preserve both accepted and rejected decisions with their metrics.

Do not create a second L0-L6 ladder for offline analysis or promote exact artifact
reproduction to physical correctness. Retract invalidated conclusions explicitly
without discarding their records. The canonical scope and reporting guidance is
in `references/testing.md` and `references/evidence-progression.md` section 8.
