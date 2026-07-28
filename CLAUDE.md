# Repository conventions

## Git identity

All commits in this repository are authored and committed as:

```
user.name  = dbwls99706
user.email = yujinhong3@gmail.com
```

Configure it locally before committing:

```bash
git config --local user.name  "dbwls99706"
git config --local user.email "yujinhong3@gmail.com"
```

Never commit under any other name or address, and never rely on a globally
configured identity — set it per-repository.

## Commit messages

Commits must contain only the description of the change.

Do **not** add:

- `Co-Authored-By:` trailers of any kind
- `Generated with ...` / `Created with ...` footers
- Tool, assistant, or session links and identifiers
- Emoji or badges that identify the authoring tool

Format: Conventional Commits, matching the existing history.

```
<type>(<scope>): <imperative summary>

<optional body explaining why, wrapped at 72 columns>
```

Types in use: `feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`.

## Commit signing

Signing is disabled for this repository (`commit.gpgsign=false`). Do not
re-enable it with a key that is not the repository owner's — a signature
attributes the commit to its key holder.

## Branch names

Branches are named `<type>/<short-kebab-summary>` and describe the change,
never the process or the tooling that produced it.

Good: `fix/qos-durability-check`, `docs/nav2-humble-review`,
`chore/git-identity-conventions`

Not allowed: any prefix or suffix naming a tool, assistant, or agent
(`claude/...`, `ai/...`, `bot/...`), and generated-looking random suffixes.

## Pull requests

Titles and bodies follow the same rules: describe the change, its rationale,
and how it was verified. No tool attribution footers, no generated-by lines.
