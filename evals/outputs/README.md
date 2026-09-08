# evals/outputs/

Paste captured model responses HERE, one file per eval name, with the skill loaded.
Used by `eval_runner.py --mode=judge` and `--parity`.

For each eval in `eval.yaml`, run the prompt path declared by that entry. Prompt
fixtures may live in nested directories such as `progression/prompts/`; the saved
capture is always flat by eval name: `evals/outputs/<eval-name>.md`.

Then run: `python3 scripts/eval_runner.py --mode=judge`

See `docs/EVAL_WORKFLOW.md` for the full procedure, including the paired
`evals/outputs_baseline/` skill-off captures used by `--parity`.
