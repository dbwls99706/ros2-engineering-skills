# Releasing the skill bundle

Read the source bundle version from `SKILL.md` metadata and verify all surfaces
below. A source version or changelog heading is not proof that a Git tag or GitHub
Release has already been published. `X.Y.Z` below means that verified version.

## Version surfaces

Update these together for a bundle release: `SKILL.md` metadata,
`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `evals/eval.yaml`,
the README source version, and the first versioned release heading in
`CHANGELOG.md`. `TestVersionConsistency` checks those surfaces.

The JSON `version` emitted by `skill_validate_hook.py` and `skill_stop_hook.py`
describes the hook-report contract and is intentionally independent of bundle
semver. Bump it only when that report contract changes. Standalone utility
`--version` values likewise describe their own interfaces; generated user packages
start at 0.1.0. Third-party versions must never be changed as part of a bundle bump.

## Finalize one release change

1. Finalize the changelog date and pending status in the release change, then
   verify the exact release-branch head. Require successful Test and Client
   discovery workflows on that SHA; earlier runs are not substitutes. If a release
   claim depends on model behavior, capture each named scenario in the actual
   client/model, run judge mode with `--require-complete`, and manually review each
   critical criterion. Keep traces and first failures. Lexical PASS, hash integrity,
   and client discovery are not semantic quality or physical safety evidence.
2. Merge only after review. For a squash merge, use a one-line release title such as
   `chore: release vX.Y.Z` and avoid carrying intermediate commit bodies or footers
   into the mainline history.
3. Resolve the new commit on `main` and check its workflows before creating tag
   `vX.Y.Z` on that exact commit. Use the corresponding changelog section as the
   release notes, including known limitations. Publishing is a separate action.
4. Start later work from updated `main`, not from an already-squashed release branch.

Keep historical verification records tied to their original commit IDs. Squash
merging creates a new mainline commit; do not relabel prior runs as having tested
that new SHA. Nothing in the validators automatically tags, merges, or publishes.

Source: [GitHub squash and merge](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges).
