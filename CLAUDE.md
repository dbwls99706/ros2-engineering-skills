# Repository conventions

## Git identity

**External contributors: use your own name and address.** Authorship should
reflect who actually wrote the change, and a pull request committed under
someone else's identity misattributes it on GitHub. Nothing below applies
to you.

The rest of this section is for the repository owner and for agent sessions
acting on the owner's behalf. Those must commit as the owner's own identity
rather than whatever a tool has configured globally — an assistant default
like `Claude <noreply@anthropic.com>` is not a person who can be asked about
the change later:

```bash
git config --local user.name  "dbwls99706"
git config --local user.email "yujinhong3@gmail.com"
```

Set it per-repository. A global identity is what leaks the wrong author
into a repo you did not intend it for.

## Commit messages

Commits must contain only the description of the change.

Do **not** add:

- `Co-Authored-By:` trailers of any kind
- `Generated with ...` / `Created with ...` footers
- Tool, assistant, or session links and identifiers
- Emoji or badges that identify the authoring tool

Two checks back this up, and it is worth being precise about what each one
actually covers:

- **CI (authoritative).** The `commit-messages` job reads the real commit
  objects on the branch and fails if any message carries one of those
  trailers. It does not care how the commit was made, so this is the check
  that actually gates a merge.
- **`.claude/hooks/no_ai_attribution.py` (best-effort, early warning).**
  A `PreToolUse` hook wired in `.claude/settings.json`. It inspects the
  *Bash command string* Claude Code is about to run, so it catches
  `git commit -m "..."` and nothing else — a message supplied via `-F`,
  typed in the editor, expanded from a shell variable, or written from an
  IDE or plain terminal never passes through it. Treat it as a fast
  reminder, not a gate. It fails open on any internal error and ignores
  non-commit commands, so it cannot block unrelated work.

Format: Conventional Commits, matching the existing history.

```
<type>(<scope>): <imperative summary>

<optional body explaining why, wrapped at 72 columns>
```

Types in use: `feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`.

## Commit signing

Sign with your own key or do not sign — a signature attributes the commit
to whoever holds the key, so signing someone else's work with a borrowed or
tool-provided key is a false claim about who stands behind it. This is the
one rule here; signing is otherwise optional and no workflow depends on it.

Agent sessions in particular should set `commit.gpgsign=false` locally,
since the ambient signing key belongs to the tooling rather than to the
repository owner.

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
