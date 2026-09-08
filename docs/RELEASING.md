# Releasing the skill bundle

The next bundle version is **1.4.0**. A source version or changelog heading is not
proof that a Git tag or GitHub Release has already been published.

## Version surfaces

Update these together: `SKILL.md` metadata, `.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json`, `evals/eval.yaml`, the manual JSON reports in
both validation hooks, the README source version, and the release changelog.
`TestVersionConsistency` checks those surfaces, including the first versioned
changelog heading. Standalone utility `--version` values describe their own
interfaces; generated user packages start at 0.1.0. Neither value is an installed
skill-bundle version, and third-party versions must not be changed during a bump.

## Finalize one release change

1. Verify the exact PR head and its current merge-test revision. Require successful
   Test and Client discovery workflows; earlier push results are not substitutes
   for a failed or incomplete PR run.
2. For this release branch, use **Squash and merge** with the one-line title
   `chore: release v1.4.0`. Clear the automatically suggested body so intermediate
   commit messages and footers do not become the release commit description.
3. Resolve the new commit on `main` and check its workflows before creating tag
   `v1.4.0` on that exact commit. Use the corresponding changelog section as the
   release notes, including known limitations. Publishing is a separate action.
4. Start later changes from updated `main`, not from the already-squashed branch.

Keep historical verification records tied to their original commit IDs. Squash
merging creates a new mainline commit; do not relabel prior runs as having tested
that new SHA. Nothing in the validators automatically tags, merges, or publishes.

Source: [GitHub squash and merge](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges).
