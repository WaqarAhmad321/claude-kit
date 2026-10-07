# claude-kit

Waqar's Claude Code setup in one repo: the global CLAUDE.md, rules and output style that `install.sh` links into `~/.claude`, the `kit` plugin whose hooks enforce those rules, the task workflow's skills and agents, and the templates that start or adopt projects. The [README](README.md) covers installing and using the kit; this file covers changing it.

## Layout
- `home/`: what `install.sh` links into `~/.claude`: `CLAUDE.md`, `rules/`, `output-styles/`, and `hooks/` (linked as `~/.claude/kit-hooks`; the orchestrator guard).
- `plugins/kit/`: the plugin. `hooks/hooks.json` and the stdlib scripts in `hooks/bin/`, `skills/`, `agents/`, `scripts/` (the onboarding and AGENTS.md tools) and `tests/`. Its [README](plugins/kit/README.md) documents every hook, its state and its failure policy.
- `settings/`: the settings `install.sh` merges into `~/.claude/settings.json` with `scripts/merge-settings.py`. Private entries such as client names go in `${XDG_CONFIG_HOME:-~/.config}/claude-kit/settings.local.json`, outside the repo, which `install.sh` merges last if it exists.
- `templates/`: starter files for projects: `common/`, one pack per stack, and `agents/` adapters. See [templates/README.md](templates/README.md).
- `docs/`: the [docs index](docs/README.md), ADRs, the install runbook, guides and [the architecture doc](docs/architecture/overview.md), which holds the principles, the enforcement map and the known limits.
- `.githooks/post-commit`: pushes every commit to the branch of the same name on GitHub, from worktrees too (`install.sh` sets `core.hooksPath`).

## Main is live: work in a worktree
`~/.claude/CLAUDE.md`, `~/.claude/rules`, `~/.claude/output-styles` and `~/.claude/kit-hooks` (the orchestrator guard) are symlinks into this checkout's `home/`, so whatever main's working tree holds there is what every Claude Code session on this machine reads at once. The `kit` plugin (`plugins/kit`'s hooks, skills, agents and scripts) doesn't load this way: Claude Code runs it from a cache copy pinned to whichever commit was last installed with `claude plugin update` or `claude plugin install`, not main's current tree, so a plugin change on main needs that update step before a session sees it (see [the deploy runbook](docs/runbooks/deploy.md)). Either way, a half-done hook or rule on main breaks or weakens enforcement everywhere once it does take effect, and a session developing the kit directly in the main checkout bypasses its own file guards (see below), so main has to stay whole in the meantime.
- Develop in `.worktrees/<name>` on its own branch: `git worktree add .worktrees/<name> -b <branch>`. `.worktrees/` is git-ignored.
- Never edit main's working tree, not even for a one-line fix. A session started in the main checkout may change the kit's own hook files without the guards asking, because developing the kit is then the task; this rule is what keeps main whole.
- Land a finished, reviewed branch in one fast-forward step: bring it up to date with main in the worktree, run the tests there, then `git merge --ff-only <branch>` on main. Main only moves forward; never force-push it.
- Run that merge in a terminal, outside the sandbox: `install.sh` denies sandboxed writes to main's live files, and a sandboxed merge stops halfway (ADR 0010).

## Tests
```bash
uv run --no-project --with pytest -- pytest plugins/kit/tests -q
```
Run it from the worktree's root. Hook tests run each script through `run_hook.py` with the hook input on stdin, in temporary git repos. Cases that need ruff, mypy or betterleaks skip when the tool isn't installed. The [install runbook](docs/runbooks/deploy.md)'s Verify steps check a change end to end in Claude Code.

## Changing hooks
- Hooks are Python 3 stdlib scripts. Each declares `FAIL_CLOSED`, and `run_hook.py` keeps the same list for scripts it can't import; a test keeps the two in sync.
- Some logic is shared on purpose: the gate-file check in `guard_protected_files.py` also drives the Bash guard and the stop gate's gate-config check, `shellwords.command_steps` is the one command parser both Bash guards (protected files and docs location) read redirections with, and `docnames.py` holds the docs naming rules the docs guard and `docs-check.py` share (`templates/common/scripts/docnames.py` links to it). Change each once, there.
- A behaviour change updates the plugin README in the same change, and a bug fix starts with a test that fails on the current code.

## Commits
- Conventional Commits with a lowercase subject of at most 72 characters; the body says why. One concern per commit, about 600 changed lines at most, lockfiles aside.
- A `fix:` commit includes its regression test.
- No AI attribution of any kind: no `Co-Authored-By` lines and no "Generated with" notes.
- Stage by explicit path. Never force-add an ignored file, and never commit `.env*` (except `.env.example`) or `settings.local.json`.

## Docs
- The docs follow [the docs rule](home/rules/docs.md): present tense, one topic per file, no status ledgers or dated notes. Open work and status go on the PM board.
- `.claude/kit.json` lets new Markdown files live only where the kit keeps them: `home/CLAUDE.md`, `home/rules/`, `home/output-styles/` and the plugin's agents and skills. `templates/` is starter content the docs guard and check skip. Other docs go where the docs layout puts them, so a scratch name such as `plugins/kit/FINAL_REPORT.md` is still blocked.
- A new decision gets an ADR in `docs/decisions/`, and `docs/README.md` links every doc.
