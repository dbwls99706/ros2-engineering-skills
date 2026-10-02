# evals/history/

Auto-generated. `eval_runner.py --parity` appends one JSON-line per run
to `<YYYY-MM>.jsonl` here. The `*.jsonl` files are gitignored
(per-run dev noise); only this README is committed so the directory
survives.

The streak check for deprecation candidacy reads the most recent N
entries across these files. Deleting the .jsonl files resets the streak
during development, but history used as comparison evidence cannot be
recovered once deleted; keep it when a deprecation decision depends on it
(see docs/EVAL_WORKFLOW.md, "Paired comparisons and history").
