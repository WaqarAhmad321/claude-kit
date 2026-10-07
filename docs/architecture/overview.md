# Architecture overview

claude-kit's principles, how each rule is enforced, and its known limits.

## Principles
- Only permission rules, hooks and the sandbox enforce anything. CLAUDE.md, rules and skills are advisory, so every rule that matters is bound to one of the three.
- Always-loaded context stays small. Stack rules load by path; module knowledge loads through skills; nothing is pre-read wholesale.
- Existing, maintained tools first. Custom code only where nothing fits (the hook glue).
- Gates are ratchets: they fail on new violations only, so they run in legacy repos from day one.
- Hooks fail closed.

## Enforcement map
| Rule | Mechanism |
|---|---|
| Code style, best practices | PostToolUse lint + format of each edited file; Stop ratchet on lint and types |
| HTTP testing the way the frontend calls | Stop gate: source changed since the last green journey run blocks finishing |
| Module skill read before editing a module | PreToolUse guard keyed per session and agent |
| Regression test per bug fix | Commit guard on `fix:` commits without test changes; CI proves red then green |
| No secrets | PreToolUse secret scan; Betterleaks pre-commit and CI; commit guard for settings.local.json and .env |
| No destructive or production actions | cc-safety-net, deny rules, sandbox |
| Types, architecture | LSP plugins; mypy/tsc and import-linter in the ratchet |
| API contracts | Generated OpenAPI plus oasdiff breaking-change check in CI |

## Testing
Hurl journeys in the frontend's call order with error paths (about 40% of test effort), a role matrix over real HTTP (20%), Schemathesis per role (10%), unit tests for pure logic (20%), Playwright UI flows (10%). All against a local Docker stack with paid API keys forced empty. Every bug fix gets a regression journey that fails before the fix.

## Module skills (v2 of an earlier project's system)
Per module: `SKILL.md` (router, invariants, settled decisions), `ARCHITECTURE.md` (a human-readable explanation, ratcheted in by the Stop check), `INTERLINKING.md` (CI-checked edges), `FLOWS.md` (journeys naming their tests), `LESSONS.md` (rule, what it prevents, test id), and generated `CONTRACTS.md` / `SCHEMA.md`. Loaded by description, `paths`, and the pre-edit guard.

## Decisions
- Push on every commit, with real commit messages.
- Review: the independent `kit:reviewer` agent checks the diff against its ten-area checklist and must end in `VERDICT: PASS` before a normal or big change can finish (ADR 0006).
- Sandbox on, with docker, herdr, gh and git remote commands excluded, and the work folder (`~/Work`) open to Claude and to sandboxed commands (ADR 0010).
- API conventions for new and existing projects: RFC 9457 errors, snake_case, JSON Merge Patch, authenticated by default, page-number pagination, HttpOnly cookies on the web. Existing APIs with shipped mobile builds get a compatibility path.
- mattpocock-skills for structure; cc-safety-net for destructive commands.

## Delivery
`install.sh` turns the sandbox on, registers this repo as the `claude-kit` marketplace, installs `kit@claude-kit` at user scope, and (on a re-run) refreshes it with `claude plugin update`. A commit here reaches a running session's rules, CLAUDE.md, output styles and orchestrator guard at once, since those are symlinks; the plugin itself (hooks, skills, agents, scripts) needs that refresh and then `/reload-plugins` — see [the deploy runbook](../runbooks/deploy.md).

## Headless runs
One of Waqar's personal tools makes headless `claude -p` runs that start with `KIT_DIRECT_MODE=1`, so the orchestrator guard lets them use web tools in the main thread. The plugin and the sandbox don't affect these runs, for three reasons:
- they use only WebSearch and WebFetch, and the sandbox covers only shell commands
- they write no files
- they run outside git, where the kit's session context and stop checks stay silent

## Known limits
- The Bash guard for protected files is best effort. It can't follow a variable it can't see, or a script written and run in the same command. The sandbox backs it up for the kit's live checkout, whose code and git hooks `install.sh` puts on the sandbox's `denyWrite` list, but not for other repos: sandboxed commands can write anything else under `~/Work` that isn't one of Claude Code's own protected paths, other repos' `.git/hooks` and `.git/config` included (ADR 0010).
- The stop checks don't see Bash-only changes in a repo other than the start repo. The review check sees them in a worktree of the start repo made during the session, but not in one that was there at the start. Bash edits by a running background subagent aren't deferred. A commit or stash with a forged older `GIT_COMMITTER_DATE` isn't reported as set-aside work.
- The journeys match trusts the command's words, so a shadowed `make` still counts. A change to a makefile variable isn't seen as a gate-config change.
- Inside the sandbox:
  - `uvx` needs `UV_TOOL_DIR="$TMPDIR/uv-tools"`, because by default it writes to `~/.local/share/uv/tools`, which isn't on the allow list.
  - `claude` runs only as a command of its own, because the mise wrapper can't write inside the sandbox.
  - In a compound command, git lists Claude Code's `/dev/null` mask files (`.bashrc`, `.gitconfig`, `.mcp.json` and others) as untracked. On its own, `git status` or `git add` is excluded from the sandbox and sees the real tree.
