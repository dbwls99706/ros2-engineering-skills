# Client compatibility and installation

Reviewed against the primary sources below on 2026-09-07. A documented client
feature is not a recorded successful run of this repository on that client.

## Two different products

The portable skill is `SKILL.md`, references, scripts, and optional client
metadata. The Claude Code plugin additionally registers session hooks. Copying
a full checkout into a skill directory is not necessarily knowledge-only:
Claude Code recognizes `.claude-plugin/plugin.json` there as a plugin after
applicable trust checks. Use the copy installer below for an intentionally
hook-free installation. It never changes settings or tool permissions.

```bash
python3 -m pip install -r requirements.txt
python3 scripts/install_skill.py --client codex --dry-run
python3 scripts/install_skill.py --client codex
python3 scripts/install_skill.py --client claude --project /path/to/project
python3 scripts/install_skill.py --client cursor
python3 scripts/install_skill.py --client gemini
```

All commands start in a trusted checkout. Use a virtual environment when the
host Python requires one. Python 3.10+ and PyYAML are required by this installer.
On Windows use `py -3` instead of `python3` where appropriate. Existing targets
need `--force`; replacement is staged and validated before the old skill moves.
`--target` names the complete final skill directory, not its parent. Nested
symlinks and replacing arbitrary non-skill directories are deliberately refused.
The legacy `install.sh` and `install.ps1` remain full-checkout copy/link tools.

## Discovery and explicit invocation

| Client | Personal installation | Project installation | Explicit invocation |
|---|---|---|---|
| Claude Code, knowledge-only | `~/.claude/skills/ros2-engineering-skills` | `.claude/skills/ros2-engineering-skills` | `/ros2-engineering-skills` |
| Claude Code, plugin | Client-managed plugin cache | Client-managed plugin scope | `/ros2-engineering:ros2-engineering-skills` |
| Codex CLI / IDE | `~/.agents/skills/ros2-engineering-skills` | `.agents/skills/ros2-engineering-skills` | `$ros2-engineering-skills`; inspect `/skills` |
| Cursor | `~/.cursor/skills/ros2-engineering-skills` | `.cursor/skills/ros2-engineering-skills` | Select the skill from the slash menu |
| Gemini CLI | `~/.gemini/skills/ros2-engineering-skills` | `.gemini/skills/ros2-engineering-skills` | Inspect `/skills list`; request the skill explicitly |

The shared `.agents/skills` directory is also documented by Cursor and Gemini.
Gemini gives `.agents` precedence over `.gemini` within the same scope. Avoid
multiple copies with the same name; inspect the resolved path, not just the name.
Codex discovers project skills along the path from the working directory to the
repository root. Host policies and trust settings can still disable discovery.

Cursor's personal skills are not automatically copied to every remote/cloud
execution environment. A skill installed on a laptop is not proof that a remote
agent sees it. Install or commit the project-scoped bundle in the actual execution
environment, and capture the selected path and revision there.

## Claude Code plugin

```bash
claude plugin marketplace add dbwls99706/ros2-engineering-skills
claude plugin install ros2-engineering@ros2-engineering-skills
claude plugin validate . --strict
claude --plugin-dir . plugin details ros2-engineering@inline
```

Run the last two commands from a trusted checkout for local development, not as
proof that every marketplace cache is current. CI pins the tested client version;
newer client behavior needs a separate recorded check. This repository uses the
supported root `SKILL.md` discovery path, not a duplicated nested copy. Hooks stay
in `hooks/hooks.json`, outside portable frontmatter. After editing plugin hooks,
reload plugins or restart the client. Skill-text live reload is a different path.

The plugin matcher registers PreToolUse and advisory Stop. `Bash` is the primary
shell tool; extra matcher names are compatibility aliases, not a claim that every
client exposes PowerShell or `cmd` as a native tool. A working `python3` command
is required by this hook configuration. A Windows copy-installer test does not
establish Windows hook interpreter availability.

## Codex metadata and permission boundary

`agents/openai.yaml` supplies a display name, short description, explicit default
prompt, and `policy.allow_implicit_invocation: true`. This advertises the skill;
it does not grant shell, network, hardware, or write access. Explicit invocation
and automatic selection must be evaluated separately. This repository does not
ship a Codex hook adapter: use the portable validators manually. Do not rename
Claude's hook JSON and assume that another client's event protocol is identical.

## Acceptance procedure

For each exact client version, test a fresh positive implicit prompt, a fresh
negative prompt, and explicit invocation. Use `evals/trigger_cases.json`. Record
whether the skill was selected, the resolved path/revision, which references were
opened, and any actual tool calls. A skill mentioning its own name in an answer
is not loading evidence. Test hooks independently with known transport payloads;
then test a real client event. Do not compare a warmed-up skill-on session with
an unrelated or contaminated control session.

CI verifies the pinned Claude plugin's structure and component discovery, Python
adapter behavior, and portable installation layout. Authenticated end-to-end
model runs for all clients are a separate evidence requirement; see
[EVIDENCE_CAPTURE.md](EVIDENCE_CAPTURE.md). No cross-client model benchmark is
claimed merely because these package and protocol tests pass.

## Primary sources

- [Agent Skills specification](https://agentskills.io/specification)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Claude plugin reference](https://code.claude.com/docs/en/plugins-reference)
- [Claude hooks reference](https://code.claude.com/docs/en/hooks)
- [OpenAI skill authoring and Codex installation](https://learn.chatgpt.com/docs/build-skills)
- [Cursor skills](https://cursor.com/docs/skills)
- [Gemini CLI skills](https://geminicli.com/docs/cli/skills/)
