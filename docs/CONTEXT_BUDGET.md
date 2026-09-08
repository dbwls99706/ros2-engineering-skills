# Selected-context budget

`SKILL.md` is loaded on activation. A line-count limit alone can hide a long body
inside wide tables, so this repository tests both size and reachability.

The root contains the operating contract, task router, high-impact gotchas,
verification ladder, and tool entry points. The detailed engineering principles,
distribution matrix, and 22 pitfalls remain in
`references/engineering-principles.md`. The root says when to read that reference;
it does not require loading it for every task. All reference files have a direct
root route, and the existing factual regressions continue to inspect the moved
content. Short-body tests independently retain the permission, provenance,
non-actuation, and verification boundaries.

## Deterministic local checks

The selected body must be at most 12,000 UTF-8 bytes and fewer than 220 lines.
These are repository policy limits, not tokenizer estimates. The generic package
validator retains its separate 500-line portability check.

```bash
python -m pytest tests/test_selected_body.py tests/test_doc_factuality.py
```

## Measured reference tokenizers

In a development environment with permission to install dependencies:

```bash
python -m pip install 'tiktoken>=0.12,<1'
python scripts/measure_context.py
```

The command measures the full selected body using both `o200k_base` and
`cl100k_base`, recording the tiktoken version, encoding names, file/body hashes,
UTF-8 bytes, lines, and actual token counts. The limit is 5,000 body tokens per
encoding. It returns nonzero for missing input, unavailable tokenizer/data,
invalid configuration, or a measured over-budget result. The package test suite
uses synthetic encoder fixtures only to test this reporting logic; those fixture
counts are never published as actual tokenizer measurements.

CI installs the reference tokenizer in the Python 3.12 packaging job, executes
the command, and retains `context-budget.json`. An artifact reporting
`unavailable` is not a pass. Other jobs keep their existing interpreter matrix.

These are **named reference encodings**, not an assertion about every model used
by Claude, Codex, Cursor, or Gemini. The count excludes host instructions, client
wrappers, conversation history, tool schemas, and references read at runtime.
Measure those in an authenticated target-client trace before making an end-to-end
context-cost claim. A smaller body alone does not prove improved answers.

Sources: [Agent Skills specification](https://agentskills.io/specification),
[skill authoring practices](https://agentskills.io/skill-creation/best-practices),
and [tiktoken](https://github.com/openai/tiktoken).
