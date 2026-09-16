# Offline scan sweep

These two pipelines are synthetic models for an offline ROS 2 point-cloud study.

Pipeline A has a fixed point universe, fixed poses and grid, and fixed per-scan
removed-ID sets. It unions those sets, and the final query returns that union.
Repeating or reordering inputs does not change the per-scan sets.

Pipeline B processes a nonempty sequence of scalar samples with this code:

```python
def accumulate_hits(samples):
    anchor = samples[0]
    hits = set()
    for sample in samples:
        if abs(sample - anchor) <= 1:
            hits.add(sample)
    return hits
```

An engineer proposes skipping all subset and ordering experiments for both
pipelines because neither clears a hit. For B, compare [0,4,5], its subset [4,5],
and its reordering [4,0,5]. For A, the team also wants to know when useful scan
contributions stop arriving and whether a shorter prefix is sufficient.

Evaluate which experiments can be eliminated and which still answer a useful
question. State the scope of any proof rather than assuming another backend has
these properties.
