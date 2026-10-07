# kit

Claude Code plugin that binds the kit's rules to hooks. Every hook is a Python 3 stdlib script in `hooks/bin/`, built on the shared `hooks/bin/kitlib.py`. `hooks/hooks.json` never runs a script directly: each entry runs `python3 hooks/bin/run_hook.py <script> [args...]`, which applies the script's failure policy (see [Failure policy](#failure-policy)).

It also ships the task workflow's skills and agents, and the onboarding skills that bring projects onto the kit:

| Name | Kind | What it does |
|---|---|---|
| `kit:workflow` | Skill | The task playbook: sizing, each step's checklist, how to brief the builder and the reviewer, and what done means. Templates in `skills/workflow/templates/` |
| `kit:handoff` | Skill | Writes `.claude/HANDOFF.md` for the next session |
| `kit:new` | Skill, `/kit:new` only | Starts a project: asks the stack (django, react-vite, nextjs, mern), name, API consumers and mobile app, lays down the common and stack templates, writes CLAUDE.md and the README from the answers, installs lefthook and makes a first commit that passes the hooks |
| `kit:adopt` | Skill, `/kit:adopt` only | Brings the kit into an existing repo: a read-only audit (lint, CI, tests, docs sprawl, old Claude files, committed secret-risk files, a Betterleaks history summary, the kit opt-out), then grouped changes, each applied only after Waqar agrees |
| `kit:skills-bootstrap` | Skill, `/kit:skills-bootstrap` only | Drafts module skills v2 into `.claude/skills/_draft/<module>/` from the code, the fix history and v1 skill files, including a human-readable ARCHITECTURE.md, with generated CONTRACTS and SCHEMA; a draft goes live only after review. Templates in `skills/skills-bootstrap/templates/` |
| `kit:builder` | Agent | Builds and tests a change from a brief, and reports in a fixed format. Has user-scope memory |
| `kit:reviewer` | Agent | Independent, read-only review on Opus against a ten-area checklist, with blocker, should-fix and nit findings. Its report names the checkout it reviewed on a `Reviewed:` line, and its last line is `VERDICT: PASS` or `VERDICT: FIX` |
| `kit:researcher` | Agent | Verifies facts from current docs, Context7 first, with sources |

The reviewer has no `memory`: Claude Code gives an agent with memory the Write and Edit tools, and the reviewer must stay read-only.

The onboarding skills set `disable-model-invocation`, so only a typed command starts them. In the main chat they hand their shell and file work to subagents, as the orchestrator guard requires. They use four stdlib scripts in `scripts/`, each with its tests:

| Script | What it does |
|---|---|
| `apply_template.py` | Lays `templates/common/` and one stack pack into a project. Never overwrites: each file is `new`, `identical`, `differs` (left alone), or `append` for `.gitignore`, which gets only the lines it lacks. Merges the packs' `kit.json` into `.claude/kit.json`, lists `CLAUDE.md.template` and `README.md.template` to fill, and `--only <glob>` applies one approved group. Finds the templates in the kit repo (see below the table), or takes `--templates` |
| `openapi_to_contracts.py` | Turns an OpenAPI 3 JSON document into one compact `CONTRACTS.md` per module, grouped by the first path segment after the shared prefix, with a GENERATED header naming the command |
| `django_model_dump.py` | Run through `manage.py shell`, prints the project apps' models (fields, relations, indexes, constraints) as `SCHEMA.md`, one file per app with `KIT_OUT_DIR` |
| `build_agents_md.py` | Writes the project's `AGENTS.md` for Codex, Cursor, Gemini CLI, OpenCode and Copilot: the kit's universal rules plus the `--stack` rules (`django`, `api-design`, `node-express`, `mongodb`, `react`, `nextjs`) between claude-kit markers, without the parts only Claude Code can act on, capped a safety margin under Codex's `project_doc_max_bytes` default. The project's own section outside the markers is kept, and a rerun without `--stack` keeps the recorded stacks. `--check` writes nothing and exits 1 when the file is missing or stale, for CI. `/kit:new` runs it; [Using the kit with other agents](../../docs/guides/using-the-kit-with-other-agents.md) covers the rest |

`apply_template.py` and `build_agents_md.py` find the kit repo with `kit_root.py`, which the protected-files guard also uses to find the live kit: the kit checkout the script sits in, else the repo `~/.claude/rules` links into (`$CLAUDE_CONFIG_DIR/rules` when that is set), else the `claude-kit` marketplace's `installLocation` in `plugins/known_marketplaces.json`. When none holds the kit, they stop with an error that names each place they looked. The first method covers a plugin that's genuinely loading in place from the checkout; when Claude Code is instead running the script from a cache copy under `~/.claude/plugins/cache/`, which holds only `plugins/kit` and not the rest of the checkout (see [the deploy runbook](../../docs/runbooks/deploy.md) for when that happens), the other two cover it.

The templates themselves live in the kit repo's `templates/`: `common/` (the docs skeleton, `scripts/docs-check.py`, lefthook, the base `kit.json`, the docs CI), one folder per stack, and `agents/`, the Copilot and Gemini CLI adapters. Each folder's `README.md` lists its files, what they assume, its setup steps and how it was checked; [templates/README.md](../../templates/README.md) says how a pack is laid out and applied.

## Models
`kit:builder` and `kit:researcher` default to Sonnet (`model: sonnet` in their frontmatter); `kit:reviewer` is always Opus. The main chat briefs a builder on Opus (`model: opus` on that Agent call) for planning, complex or hard tasks, and small-but-risky changes (auth, permissions, money, migrations, deleting data, a shipped client's API, security), and escalates a builder that fails the same task twice. Haiku is for quick read-only status checks only. See `home/rules/workflow.md` and the `kit:workflow` skill for the full routing table.

## Hooks
| Event (matcher) | Script | Fails | What it does |
|---|---|---|---|
| SessionStart (`startup\|resume\|clear\|compact\|fork`) | `session_context.py` | open | Records the repo, its HEAD and the files already dirty at startup (once per session), then gives Claude the branch, module skills, the journeys command and what the kit enforces (at most 1500 chars). Silent outside git |
| SessionStart (`resume\|compact`) | `inject_handoff.py` | open | Gives Claude the project's `.claude/HANDOFF.md` (from `kit:handoff`), at most 6000 chars, with a pointer to the file when cut. Silent when the file is missing or empty |
| SubagentStart | `session_context.py` | open | Records the same for the subagent, so its stop checks cover only its own run |
| PreToolUse (`Edit\|Write\|NotebookEdit`) | `snapshot_before_edit.py` | open | Records the file's lint and format baseline once per file per session, before the first edit. Never blocks |
| PreToolUse (`Edit\|Write\|NotebookEdit`, `Bash`) | `scan_secrets.py` | closed | Blocks secrets in file edits and shell commands |
| PreToolUse (`Edit\|Write\|NotebookEdit`) | `guard_module_skill.py` | closed | Denies editing a file under a module skill's `paths` until this agent has loaded that skill |
| PreToolUse (`Edit\|Write\|NotebookEdit`) | `guard_protected_files.py` | closed | Asks the user before an edit changes quality-gate configuration (see Protected files) |
| PreToolUse (`Bash`) | `guard_protected_bash.py` | closed | Asks the user before a command could write one of the same protected files, or disables or removes a plugin (see Protected files) |
| PreToolUse (`Write`, `Bash`) | `guard_docs_location.py` | closed | Keeps new Markdown files in a repo to its docs layout (see Docs location) |
| PreToolUse (`Bash`) | `guard_commit.py` | closed | Checks commits: `fix:` without test changes, `settings.local.json`, `.env`. It runs on every Bash call and exits at once when the command commits nothing. There's no `if: Bash(git commit*)` filter, because that filter let `git -C . commit`, `git -c core.hooksPath=/dev/null commit` and `/usr/bin/git commit` skip every check |
| PostToolUse (`Edit\|Write\|NotebookEdit`) | `lint_edited_file.py` | open | Tidies the edited file, then feeds back lint problems the edit introduced (see below) |
| PostToolUse (`Read\|Skill`), UserPromptExpansion, PostCompact | `record_skill_seen.py` | open | Records which skills this agent loaded; `--compact` (PostCompact) forgets plain reads |
| PostToolUse (`Bash`) | `record_journey_pass.py` | open | Records a green journey run |
| SubagentStop (`^(reviewer\|kit:reviewer\|kit-reviewer)$`), PostToolUse (`SubagentHandback`) | `record_review.py` | open | Records the reviewer's verdict with the working state of the checkout it reviewed (see Recording the review) |
| PostCompact | `log_compact.py` | open | Appends the compaction summary to `~/.claude/compact-log/<session_id>.md`, so the user can see what compaction kept |
| Stop, SubagentStop | `stop_gate.py` | closed | Refuses to finish while this session's changes add lint or type errors, leave journeys stale, change gate config the user hasn't been told about, or (main Stop only) are non-trivial without a reviewer's `VERDICT: PASS` (see below) |
| Stop, SubagentStop | `stop_module_skills.py` | closed | Refuses to finish while files changed in this session belong to a module skill that wasn't loaded (by this subagent; on the main Stop, by any agent of the session), which catches edits made through Bash. Also ratchets ARCHITECTURE.md: blocks on a module skill created this session without one, and reminds without blocking, once per module per session, on an existing one that's still missing it |

### The lint ratchet
- Linters are detected from the project, nearest config first, so monorepos work: `ruff` for `.py` when `[tool.ruff]` (pyproject.toml), `ruff.toml` or `.ruff.toml` exists; `oxlint` for JS/TS when `node_modules/.bin/oxlint` or `.oxlintrc.json` exists; otherwise `eslint` when an eslint config exists. Formatters: `ruff format` with ruff; `biome`, `oxfmt` or `prettier` by config file. Tools are looked up in `node_modules/.bin`, `.venv/bin`, `venv/bin`, then `PATH` and mise shims. Nothing configured means nothing runs.
- After an edit, the file is autofixed (safe fixes only) if it had no fixable violations before, and formatted if it was formatted before. A legacy dirty file is never rewritten wholesale. The autofix never removes an unused import or variable (ruff `--unfixable F401,F841`, oxlint `-A no-unused-vars`), because Claude often adds an import one edit before the code that uses it; it's reported instead.
- Violations are compared as counts of `(tool, rule, message)` with no line numbers, so moved code doesn't count as new. Suppression comments count too, each kind separately, so adding one, or swapping a line-level one for a file-level one, is a new violation:
  - Python, read from real comments only (tokenized, so `"# noqa"` in a string doesn't count): `# noqa`, `# ruff: noqa`, `# flake8: noqa`, `# ruff: disable[...]`, `# type: ignore`, `# mypy: ignore-errors`, `# pyright: ignore`, `# pragma: no cover`
  - JS/TS, by line: `eslint-disable`, `eslint-disable-next-line`, `eslint-disable-line`, the same three for `oxlint`, `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`
- Anything new goes back to Claude (exit 2) with rule, message and lines.
- Skipped: paths under `.claude/`, `migrations/`, `node_modules/`, `.git/`, `.venv/`, `venv/`, generated `CONTRACTS.md` / `SCHEMA.md`, and [non-project paths](#non-project-paths). A non-project edit isn't snapshotted or recorded either.

### Non-project paths
Scratch work and the kit's starter templates aren't project code. No lint-on-edit, stop check, review check, quality-gate config check or docs location check covers them, and no report lists them:
- anything under the hook input's `scratchpad_dir`, under `$TMPDIR`, or under `/tmp`. A temp directory that holds the session's own project (`$CLAUDE_PROJECT_DIR`, else the session's cwd) doesn't count, so a project checked out under `/tmp` is still checked, and a `TMPDIR` of `/` or `$HOME` exempts nothing.
- `templates/**` in the kit repo (the repo holding `plugins/kit/.claude-plugin/plugin.json`): starter files for other projects, not live config.

The files that could switch the kit off (Claude Code settings, hooks and plugins, the kit's state and its own hook files) stay protected wherever they are.

### Module skills
A module skill is a project skill whose frontmatter has `paths`: the files it governs. Claude Code uses the same field to load the skill automatically.
```markdown
---
name: billing
description: Rules for the billing module. Use before changing invoices, payments or tax code.
paths:
  - "backend/billing/**"
  - "frontend/src/billing/**/*.{ts,tsx}"
---

# Billing
...
```
- Where: `.claude/skills/<dir>/SKILL.md` at the repo root. `name` defaults to `<dir>`. `paths` is a YAML list or a comma-separated string. Quote globs, since YAML reads a leading `*` as an alias.
- Globs are anchored at the repo root: `*` stays inside one path segment, a `**` segment spans zero or more, and `?`, `[abc]`, `[!abc]` and `{ts,tsx}` work. `*.md` matches only root files; use `**/*.md` for any depth.
- Loading a skill means any of:
  - a Read of its whole `SKILL.md`, recorded as `read`: no `offset` past the first line, and no `limit`, or one that covers every line. Without a limit Read returns 2000 lines, so a longer skill needs a covering `limit`.
  - the Skill tool, by name or directory, recorded as `skill`
  - the user typing `/<name>`, recorded as `prompt`

  A namespaced name (`someplugin:billing`) is another plugin's skill and doesn't count. The exception is a directory-qualified name whose directory is the repo root, such as `../..:billing` from a subdirectory. Each subagent has to load the skill itself. Compaction forgets `read` entries, because Claude Code re-attaches invoked skills after compaction but not the content of a plain Read.
- Symlinks work both ways. The edited path is matched as written and with symlinks resolved. Each `paths` glob also matches with its literal leading directories resolved, so when `app/billing` is a symlink to `real_billing`, an edit to `real_billing/x.py` is guarded too. A symlink below a glob's first wildcard isn't resolved.
- If the state directory can't be written at all (see [State](#state)), the guard allows the edit and warns the user, instead of blocking the module for good.
- Never guarded: `.claude/`, `.git/`, `node_modules/`, `migrations/` at any depth, generated `CONTRACTS.md` / `SCHEMA.md`, and lockfiles.
- The stop check looks at what changed since the session, or the subagent, started (see [What the stop hooks check](#what-the-stop-hooks-check)). A subagent needs the skill itself; the main Stop accepts a skill any agent of the session loaded, because the main thread may only orchestrate. It reports like the stop gate (below): three lines on the main thread with the list in `<data dir>/reports/<session>-module-skills.md`, the list itself (at most 40 lines) for a subagent. It follows the same block limits, then lets Claude finish and warns the user once, in one line.
- The ARCHITECTURE.md ratchet treats a module skill as created this session when no `SKILL.md` at the start commit has its `name`, so moving a skill's folder (even with plain `mv`) leaves it an existing module, and changing its `name:` makes it a new one.

### Protected files
`guard_protected_files.py` asks the user to approve edits to the files that could switch the kit off. These are checked everywhere, in a git repo or not:
- Claude Code settings: `.claude/settings.json` and `.claude/settings.local.json` in any directory, including `~/.claude` and `$CLAUDE_CONFIG_DIR`. That covers adding `disableAllHooks` or `KIT_BYPASS`, and removing hooks.
- Claude Code's `hooks/`, `kit-hooks/` and `plugins/` directories under `~/.claude` and `$CLAUDE_CONFIG_DIR`.
- The kit's state (`$CLAUDE_PLUGIN_DATA`, `~/.cache/claude-kit`).
- The kit's own files:
  - the installed plugin (`$CLAUDE_PLUGIN_ROOT`)
  - in the live kit, the checkout Claude Code runs: `plugins/kit/hooks/**`, `settings/*.json`, `home/hooks/**` and `install.sh`. The live kit is found with `kit_root.py`, as the scripts find it: the kit checkout holding `$CLAUDE_PLUGIN_ROOT`, else the repo `~/.claude/rules` links into (`$CLAUDE_CONFIG_DIR/rules` when that is set), else the `claude-kit` marketplace's `installLocation`. Other checkouts and worktrees of the kit are ordinary files.

  These don't ask when the session's own project (`$CLAUDE_PROJECT_DIR`, else the session's cwd) is the live kit, because developing the kit is then the task. Claude Code settings still ask there.

Inside a git repo, it also asks to approve changes to its quality-gate configuration:
- `.claude/kit.json`, `.claude/kit-baseline*`, `.github/workflows/**`, the root `conftest.py`
- at any depth: `ruff.toml`, `.ruff.toml`, `mypy.ini`, `.mypy.ini`, `pytest.ini`, `.flake8`, `tox.ini`, `.oxlintrc.json(c)`, `eslint.config.*`, `.eslintrc*`, `biome.json(c)`, `tsconfig*.json`, `.pre-commit-config.yaml`, every lefthook config name (`lefthook`, `.lefthook`, `lefthook-local` and `.lefthook-local`, each as `.yml`, `.yaml`, `.toml`, `.json` or `.jsonc`, root or `.config/`), and every file under a `.lefthook/` or `.lefthook-local/` directory, the directory itself included: lefthook's default `source_dir` and `source_dir_local`, which hold the scripts it runs (`.lefthook/pre-commit/lint.sh`), and where the kit's stack jobs live (`.lefthook/django.yml`, which `lefthook.yml` extends)
- only when their gate part would change:
  - `pyproject.toml`: `[tool.ruff]`, `[tool.mypy]`, `[tool.importlinter]`, `[tool.pytest]`
  - `setup.cfg`: `[flake8]`, `[mypy]`, `[mypy-*]`
  - `package.json`: `eslintConfig`, and the `lint`, `test`, `typecheck`, `type-check`, `types`, `tsc` and `check` scripts, including `test:unit`-style variants
  - the makefile that `gate.journeys.command` runs, when the command calls `make`: the rules of the targets it names (or the default goal) and their prerequisites. This is best effort; a change to a variable a recipe uses isn't seen.

  The gate part is compared after applying the edit, so an edit inside a table's body counts, and an edit that leaves the file unparseable counts too.

  None of this applies to [non-project paths](#non-project-paths).

A file is checked by its path as written and with symlinks resolved, so `~/.claude/hooks/...` pointing into the kit repo counts.

#### Through Bash
`guard_protected_bash.py` asks the user to approve a command that could write any of the files above:
- files everywhere, with the same kit-repo exception, which never covers Claude Code settings or the kit's state
- whole-file gate configuration inside a repo; the section-compared files are left to the stop gate, which compares them with HEAD

It reads what a command writes to:
- output redirections, including `&>`, and `tee`
- `sed -i` / `yq -i`, `cp`, `mv`, `install`, `ln`, `rsync`, `rm`, `rmdir`, `chmod`/`chown`, `truncate`, `touch`, `curl -o`, `wget -O`, `dd of=`
- `git checkout`/`restore`/`reset`/`stash`/`clean` and other work-tree rewrites, taken as touching the whole repo
- `$(...)`, `bash -c`, `eval`, a heredoc fed to a shell, and a shell script file

A directory that holds protected files counts, for `rm -rf ~/.claude`. For code given to `python`, `node`, `perl` and similar interpreters, including a script file, it asks when the code mentions those paths or `skills_seen` and could write, move or delete a file. For Python, Node and Perl that means a write-mode `open`, `write_text`, `json.dump`, `writeFile`, `appendFile`, `rename`, `unlink`, `rm`, `shutil`, `perl -i`, or a subprocess running a file-writing command; plain reads such as `json.load(open(p))` or `readFileSync` pass. For other interpreters any mention asks. A mention of the kit's hook directories counts unless it points into a checkout other than the live kit.

Whole-file gate configuration (any file name from the list above, such as `kit.json`, `kit-baseline*`, `tsconfig*.json` and every lefthook config name, or the `.github/workflows`, `.lefthook` and `.lefthook-local` directories, built from the same lists the edit guard uses) counts in such code only as a write target. It asks for a write call whose text names one (`open('.claude/kit.json', 'w')`, `Path('.claude', 'kit.json').write_text(...)`, `shutil.copy(src, '.github/workflows/ci.yml')`, a subprocess such as `['cp', src, 'lefthook.yml']`), a write call on a variable the code sets to a path naming one (`p = '.claude/kit.json'` and then `open(p, 'w')`, followed through further assignments but not through a value read from a file), or a gate file passed on the command line to code that could write (`fmt.py lefthook.yml`, `--out=.claude/kit.json`). A script that names `kit.json` only to read it and writes elsewhere, such as `docs-check.py`, passes. So does one whose write target the hook can't trace to a gate file name: `apply_template.py` passes with or without `--dry-run`, though it writes `.claude/kit.json` through a planned-file record, and the stop gate reports that change. For other interpreters any mention asks. The same text check covers a shell write target the hook can't resolve, such as `> "$(git rev-parse --show-toplevel)/.claude/kit.json"`.

It also asks on any command that mentions `disableAllHooks` or `KIT_BYPASS`, and on `claude plugin(s) disable|uninstall|remove` and `claude plugin(s) marketplace remove|rm`. The kit's `settings/settings.kit.json` also denies those plugin commands outright; Waqar can still run them himself with `!`.

This is a best-effort layer above the sandbox, which blocks writes to `~/.claude` at the OS level. It doesn't follow a variable it can't see or a script written and run in the same command, and in code it follows a variable only through plain `name = value` assignments, so a gate path built by a function or stored on an object elsewhere isn't seen; the stop gate still reports a changed gate file. It adds about 25 ms to a Bash call.

### What the stop hooks check
`stopscope.py` works this out for both stop hooks, per repository:
- **The start repo** (the one `session_context` recorded, with `start_root` and `start_head`): every file that differs from the start commit, whether committed since, staged, unstaged, deleted or untracked, minus the files dirty at the start. A rename counts as a change to both paths. Files dirty at the start are checked only when this agent edited them with Edit/Write. `session_context` pins the start commit under a hidden ref, `refs/kit/start/<unix time>-<session id>[-<agent id>]`, so an amend, rebase, `reflog expire` or `gc` can't drop it; pins older than 7 days are deleted at the next session start. If the start commit is gone anyway (the pin was deleted), that is a finding ("session history was rewritten; the baseline was lost"), and every file touched since the session started (Edit/Write, the working tree against HEAD, and files in commits made since `started_at`) is checked against HEAD.
- **Work the working tree no longer shows**: a stash made during the session, and commits made during the session on a local branch that no worktree has checked out (a `git checkout` away from them), are findings, because the checks can't see that work. Commits checked out in another worktree, such as a subagent's, don't count. Work that is discarded (`git reset --hard`, `git stash drop`) is gone, so it isn't a finding.
- **Any other repo** holding a file the agents edited with Edit/Write (a sibling repo, or every repo when the session started outside git): those edited files only. Bash-only changes there aren't seen.
- **Whose work**: the main Stop answers for every agent of the session; a subagent answers for its own run, minus files other agents edited with Edit/Write. On the main Stop, files that a subagent still running in the background edited with Edit/Write (from the Stop input's `background_tasks`, whose subagent `id` is the agent id) are deferred instead of checked, without telling the user; that subagent's own stop checks them, and the next main Stop after it finishes does too. If no task id matches an agent, the subagents whose own stop hasn't let them finish (clean or released) since their last edit are deferred.
- **Left out**: [non-project paths](#non-project-paths), including a whole repo under a temp directory.

### The stop gate
`stop_gate.py` runs on Stop and SubagentStop, on the files above. A subagent that made no Edit/Write call isn't checked, because SubagentStop also fires for Claude Code's internal agents; the main Stop covers it. The lint and type checks skip the lint ratchet's exempt paths and lockfiles. Paths are relative to their repo root; a section about a repo other than the working directory's names that repo in its heading.
- **Lint ratchet**: each file is linted now and compared, as counts of `(tool, rule, message)`, with its content at the start commit, so a violation added through Bash can't become the baseline. Only a file dirty at the start (or in a repo without a start record) uses the snapshot taken before its first edit. ruff and eslint read the old content from stdin under the real path; oxlint lints a hidden `.<stem>.kit-head<ext>` copy next to the file and deletes it. A file that didn't exist has an empty baseline; a renamed file uses its old path.
- **Type ratchet**: runs only for a changed `.py` under a mypy config (`[tool.mypy]`, `mypy.ini`, `.mypy.ini`) with mypy installed, or a changed `.ts`/`.tsx`/`.mts`/`.cts` under a `tsconfig.json` with `node_modules/.bin/tsc`. Commands: `mypy --follow-imports=silent -O json <changed files>` and `tsc -b --noEmit --pretty false`, falling back to `tsc --noEmit -p tsconfig.json` when the build fails on its config. Errors in the changed files are compared, as counts of `(tool, file, code, message)`, with `.claude/kit-baseline.json`. A tool with no baseline isn't run, and the gate says so once per session. A timeout (180 s) is a finding.
- **Journeys**: when `gate.journeys` is set and a changed file matches `watch`, the gate blocks until some agent of the session has run `command` green with the watched files as they are now. The watched state is a hash of the git blob ids of every watched file, so a commit doesn't make the journeys stale.
- **Gate config**: a changed file from the Protected files list (for `pyproject.toml`, `setup.cfg`, `package.json` and the journeys makefile, only their gate parts, compared with the start commit) that wasn't dirty at the start blocks once, asking Claude to tell the user; the next stop tells the user directly.
- **Invalid config**: an unreadable `.claude/kit.json` or `.claude/kit-baseline.json`, or a bad `gate.review` value, is one finding, through the loop guard like any other.
- **Review**: on the main Stop only, and only once no subagent is still running in the background, since a running builder hasn't finished the change. A subagent's stop never needs a review. It checks the repos above and every worktree of the start repo (`git worktree list`). A worktree made during the session, one that wasn't there when `session_context` recorded the start, counts in full from the commit it was made at, its commits and Bash-made changes included; once its HEAD is in the start checkout's history, only its uncommitted part counts, since the start repo's check covers the rest. A worktree that was there at the start counts only for what Edit/Write changed in it. A repo's change is non-trivial when it has more than `gate.review.min_lines` changed lines (added plus deleted, from the session's base to the working state, lockfiles and [non-project paths](#non-project-paths) aside) or changes a file matching `gate.review.always`. The base is the start commit until HEAD moves onto commits made elsewhere: a pull, a fast-forward, a merge's other side, a branch switch, a reset or an `update-ref` onto someone else's commit, read from HEAD's reflog since the session started. Then the latest of those is the base, so a teammate's commits don't count. A commit made in the session (commit, amend, cherry-pick, revert, a rebase step, a merge) never becomes the base, so it keeps counting. Neither does the session's own commit when HEAD moves onto it, such as a branch committed in a worktree and then fast-forwarded or merged into the main checkout, or a `git commit-tree` commit set with `git update-ref`: a commit is the session's own when its committer email is the one git commits with in the repo (`user.email`, or `GIT_COMMITTER_EMAIL` when set) and its committer time is at or after the session started. When HEAD arrives on a commit, the base moves to the first commit along its first parents that isn't the session's own; a merge's other side moves it only when that commit isn't already in the base's history. A move onto the base itself leaves it there, so a worktree made at one of the session's own commits counts from that commit, before and after it commits. When git has no identity there, no commit counts as the session's own this way. The check first counts cheaply, from `git diff --numstat` against the working tree plus every line of each untracked file, a count never below the exact one; only when that could be over `min_lines`, or a changed file matches `always`, does it snapshot the working state and count exactly, with renames detected. A non-trivial change needs the reviewer's `VERDICT: PASS` (see [Recording the review](#recording-the-review)), which counts for the checkout the reviewer named, as that checkout was when the verdict came, and for another checkout with the same session base, whose change starts where the reviewed one did. It passes when such a PASS is the latest verdict recorded for exactly this working state: a reviewed worktree branch merged into a checkout that hasn't moved, by fast-forward or a merge commit, has the reviewed tree and needs no second review. A PASS on the same tree from another base doesn't count, since its reviewer saw a different change, such as only a worktree's own part without the session's commits it was made from. Otherwise it passes when the working state differs by at most `gate.review.follow_up_lines` lines from such a PASS. The result of merging two reviewed branches usually differs from each by more than that, so it needs a review of its own. When the base moved after the review, what arrived is merged into the reviewed state first (`git merge-tree`), so a pull isn't a follow-up; if that merge conflicts, the reviewed state is compared as it was. Files dirty at the start that no agent changed don't count toward that follow-up. A FIX as the latest verdict for this exact working state, in any checkout, keeps the block. So does a FIX as the latest verdict for this checkout, until a PASS recorded after it covers the working state as above; a PASS recorded before it, another checkout's included, no longer counts. No verdict, or a reviewed state git no longer has, keeps the block too. When the session changed `.claude/kit.json`, the check reads `gate.review` from the start commit's kit.json (the defaults when it had none), so a session can't switch off or loosen its own review; the change applies from the next session. The report lists the size and up to 10 paths. If git fails while the check runs (an unreadable untracked file, a nested repository without a commit, a git timeout), that is a finding of its own, `Review check failed`, so the other findings still show and the block limits apply as usual.
- **Output**: every block writes the full report to `<data dir>/reports/<session>[-<agent id>]-stop.md`. On the main thread, which the user watches and which only orchestrates, the block reason is three short lines: what kind of problem and how many files, where the report is, and to delegate the fix to a subagent that reads it. When an unreviewed change is the only finding, the three lines say instead: `Non-trivial change (N lines) without an independent PASS review.`, `Run the kit:reviewer agent on the current diff; fix FIX findings and re-run until PASS.` and `Full report: <path>`. A subagent's block reason holds the findings themselves, capped at 40 lines plus a count of the rest.
- **Block limits**: until a clean stop, the gate blocks at most 3 times in a row on the same findings and at most 5 times in total. Past a limit it lets Claude finish and warns the user in one line that names the report, once until a clean stop starts a new count. Changed findings restart the first count only. A new turn restarts neither, because an orchestrating main thread starts a new turn whenever a background agent reports, and a stop that is clean only because it deferred a running agent's files isn't a clean stop. The review check counts toward the same limits.
- **Known limits**: a commit or stash made with a forged older `GIT_COMMITTER_DATE` looks older than the session, so it isn't reported as set-aside work, and a commit made with a forged date or committer email isn't the session's own, so the base moves past it when it arrives; a session commit that a teammate built on and that then comes back by fast-forward is part of the new base, so the review size no longer counts it; a teammate's commit merged into the session's own branch counts toward the review size, since only first parents are followed; Bash edits by a running background subagent aren't deferred, because they can't be attributed to it; the review check doesn't see Bash-only changes in a worktree that was there when the session started, since that worktree's state then isn't recorded; a PASS stamps the checkout's state when the reviewer finishes, so whatever a builder changes there while the review runs counts as reviewed.

### Matching the journeys command
`record_journey_pass.py` runs after every successful Bash call. PostToolUse only fires when the call exited 0, so a run counts only if its own failure would have failed the call:
- The configured command's words (its last simple command) must be a simple command of their own, word for word: no extra arguments, so `make journeys -n` or `--dry-run` doesn't count, and `echo make journeys`, `: make journeys` or a comment doesn't either. Redirections, `VAR=value`, `env`, `time`, `nohup`, `command`, `exec` and `timeout <duration>` in front are fine.
- Nothing may hide its exit status: no leading `!` or `if`/`while`/`until`, no `||` before or after it, and no `;`, `&` or newline after it (`&&` is fine). In a pipe it must be the last stage, unless the command set `set -o pipefail` first. Claude Code's Bash doesn't set pipefail, so `make journeys | tail -20` alone doesn't count.
- One exception to the `;` rule: the command followed by an echo of its status as the last command, such as `make journeys; echo "exit=$?"` or `; echo $?`, counts when the last line of the output is that echo showing 0. The echo can't redirect or print anything else the hook can't work out.
- A literal `cd <dir>` before it picks the repo whose `kit.json` and watched files apply.
- A background, moved-to-background or interrupted run doesn't count.
- Known limit: the match trusts the command's words, so a shadowed command still counts: `PATH=/fake:$PATH make journeys`, or a shell function or alias named `make`. That takes deliberate intent.

### Recording the review
`record_review.py` runs when the reviewer agent stops, and after the reviewer's `SubagentHandback` call. In auto mode a subagent delivers its report through `SubagentHandback`, and its SubagentStop then carries only closing text, so the report is read from the hand-back's `message`.
- The agent type must be `reviewer`, `kit:reviewer` or `kit-reviewer`. The SubagentStop matcher is the anchored regex `^(reviewer|kit:reviewer|kit-reviewer)$`: the colon puts a matcher on Claude Code's regex path, where an unanchored pattern matches anywhere in the name.
- The verdict is the report's last non-empty line, exactly `VERDICT: PASS` or `VERDICT: FIX` (surrounding spaces aside). Bold, a trailing period or a closing code fence after it means no verdict, and nothing is recorded.
- The verdict counts for one checkout: the one the report names on a `Reviewed: <path>` line, by the absolute path of its root (surrounding backticks aside), among the checkouts the review check knows (see Review under The stop gate). With no `Reviewed:` line, it counts for the reviewer's own checkout, but only when that is the only checkout the session knows, so a one-checkout session needs no line. A line naming another path, a relative one or two different checkouts, or a missing line when the session knows more than one checkout, records nothing and tells the user; such a FIX also drops every PASS recorded so far, since it may be about any of them.
- It records, in the main thread's state, the verdict, that checkout's working state as a git tree id (tracked and untracked files, not ignored ones, built in a copy of the index so the real index is untouched), the session's base tree there and the changed lines. The record replaces the checkout's earlier verdict and any other checkout's verdict on the same working state, so the latest verdict wins for both. If git fails for the checkout, nothing is recorded and the user is told why.

### Docs location
`guard_docs_location.py` checks a new Markdown file (`.md`, `.mdx`) inside a git work tree, when Write or a Bash command would create it. A file that already exists, a path outside any repo, and [non-project paths](#non-project-paths) (scratch work, the kit's `templates/`) pass. For Bash it reads output redirections (`>`, `>>`, `&>` and the rest) and `tee` targets, following a literal `cd`; `cp`, `mv`, `touch` and code run by interpreters aren't followed. It decides in this order:
1. **Pass**: `README.md` at any depth; `CLAUDE.md`, `AGENTS.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` and `LICENSE.md` at the root; anything under the root's `.claude/`, `.github/`, `plugins/**/skills/` and `plugins/**/agents/`, or under a `node_modules`, `fixtures`, `__fixtures__` or `testdata` directory at any depth; and the `docs.allowed` globs.
2. **Docs directories** decide by their naming rule: in `docs/architecture/`, `docs/api/`, `docs/api/handoffs/`, `docs/partners/`, `docs/runbooks/` and `docs/guides/` a kebab-case name passes and any other is denied; in `docs/decisions/` it's `NNNN-kebab-title.md`. A scratch word doesn't count there, so `docs/runbooks/fix-stuck-queue.md` and `docs/decisions/0007-subscription-plan-tiers.md` pass, but a `v2` or `round3` part or a date is still denied.
3. **Deny** a scratch name anywhere else: a name with the word summary, report, notes, plan, fix, status, progress, todo, remaining, findings, analysis, final, latest or reply, a `v2` or `round3` part, or a date. The message points to the reply, the PR or a PM card, and to the session's scratchpad.
4. **Ask** for anything else, such as `docs/overview.md` or `docs/runbooks/db/restore.md`. Approve only a new kind of lasting doc, then add its glob to `docs.allowed`.

It never allows a call itself, so a pass leaves the normal permission flow in charge. A malformed `docs.allowed` fails closed. The naming rules in steps 2 and 3 live in `hooks/bin/docnames.py`, which the docs check (`templates/common/scripts/docs-check.py`) reads too, through a link beside it, so the guard and the check judge a name alike.

### `kit_baseline.py`
```bash
python3 plugins/kit/hooks/bin/kit_baseline.py [--root <repo>]
```
Records the current mypy and tsc errors of every tracked or untracked (not ignored) file in `.claude/kit-baseline.json`: per-file fingerprint counts, tool versions and a timestamp. Commit it. It warns when the new baseline has more errors than the old one. A tool that fails is left out, so its errors stay ungated.

## `.claude/kit.json`
Optional, per project. Every key is optional.
```json
{
  "gate": {
    "journeys": {"command": "hurl --test journeys/*.hurl", "watch": ["backend/**/*.py", "frontend/src/**"]},
    "review": {"min_lines": 80, "always": ["**/auth/**", "**/billing/**"]}
  },
  "docs": {"allowed": ["specs/**/*.md"]}
}
```
- `gate.journeys`: `command` runs the journeys; a change to a file matching `watch` since the last green run makes them stale. `watch` globs are gitignore-style: `**` spans directories, and a glob without a slash matches at any depth.
- `gate.review` (see the stop gate's Review check; a change here applies from the next session):
  - `enabled` (default `true`): `false` turns the review check off for the repo.
  - `min_lines` (default 50): a change of more changed lines than this needs a review.
  - `follow_up_lines` (default 15): how many more lines may change after a PASS before the review is stale.
  - `always` (default `**/auth/**`, `**/*permission*`, `**/payments/**`, `**/billing/**`, `**/*security*`, `**/migrations/**`): a change to a matching file needs a review whatever its size. A list here replaces the defaults. Globs work like `watch`.
- `docs.allowed`: extra globs, like `watch`, where the docs location guard lets new Markdown files pass.
- Type checkers aren't configured here: the stop gate detects mypy and tsc itself.

Module skills are declared in their `SKILL.md`, not here.

## `KIT_BYPASS`
Comma-separated hook names, or `all`, to skip hooks for one launch: `KIT_BYPASS=guard_commit,stop_gate claude`. A hook's name is its script name without `.py`. The launcher checks it before importing the script, so a bypass works even when the script or kitlib is broken.

The bypass only counts when it comes from the shell. If `env.KIT_BYPASS` is in `~/.claude/settings.json` (or under `$CLAUDE_CONFIG_DIR`), or in the project's `.claude/settings.json` or `.claude/settings.local.json`, the launcher ignores it, because Claude can write those files itself. The hooks run as normal, and the user is told once per session.

## Failure policy
Each script declares `FAIL_CLOSED = True|False` and passes it to `@kitlib.hook_main(name, fail_closed=FAIL_CLOSED)`. `run_hook.py` also keeps a static list of the fail-closed hooks (the guards, `scan_secrets` and both stop hooks), because it must know the policy when the script can't even be imported. A test keeps the two in sync.
- **Fail closed**: an unexpected error blocks with `kit: <name> hook failed (<error>). Fix the hook, or relaunch Claude Code with KIT_BYPASS=<name>.` (exit 2). On Stop and SubagentStop it blocks only once: when the input has `stop_hook_active: true`, the error goes to the user as a warning and Claude may stop, so a hook that keeps crashing can't trap Claude.
- **Fail open**: the error is shown to the user as a `systemMessage` warning, and the tool call or session proceeds (exit 0).

This covers errors inside `main()` (handled by `hook_main`) and errors the script can't catch (handled by the launcher): a syntax error in the script or kitlib, a failed import, a missing file, a missing `main()`. A deliberate `sys.exit()` from a script, such as a block or feedback, passes through unchanged.

## State
Per session and agent, in `${CLAUDE_PLUGIN_DATA}/state/<session_id>/<agent_id or main>.json`, under an exclusive `flock`. When `CLAUDE_PLUGIN_DATA` is unset or can't be written, the state goes to `~/.cache/claude-kit` instead. Keys:
- `edits`: absolute path → `{"seq", "before"}`. `seq` 0 means a baseline was recorded but the edit never landed. Baseline lint keys are `json.dumps([tool, rule, message])`.
- `edit_seq`: the number of edits that landed
- `journey_passes`: repo root → hash of the watched files at this agent's last green journey run
- `skills_seen`: skill name → `read`, `skill` or `prompt`
- `initially_dirty`, `start_root`, `start_head`, `start_worktrees`: the repo, its HEAD commit (null before the first commit), the repo-relative paths dirty and the absolute paths of the repo's worktrees when the session (main file) or subagent (its own file) started
- `module_skills_streak`, `module_architecture_streak`, `stop_gate_streak`: the findings each stop hook last saw, how many stops in a row saw them (`same`), how many times it has blocked since the last clean stop (`blocks`), and whether it has released and warned (`released`)
- `checked_edit_seq`: the `edit_seq` at this agent's last stop that let it finish, clean or released
- `architecture_reminded`: the module skills this agent has already reminded the user lack ARCHITECTURE.md; no agent of the session repeats a module another one reminded about
- `verdicts` (main file only): checkout root → `{"verdict", "tree", "base", "lines", "seq"}`, the reviewer's latest verdict for that checkout, the git tree ids of the working state it reviewed and of the session's base there then, the session's changed lines then, and the verdict's place in the order they were recorded. A verdict saved before verdicts were numbered has no `seq` and counts as older than every numbered one, so a PASS whose order against this checkout's FIX is unknown doesn't count
- `started_at`: when the session (or subagent) started, in Unix seconds; `typecheck_notice_shown`; `tamper_blocked` and `tamper_notified`: absolute path → hash of the gate content already raised

The stop hooks read every agent's file in the session (without the lock; files are replaced atomically) and write only their own. Session directories older than 7 days are pruned. The stop hooks' full reports live next to the state, in `reports/`, and are pruned after 7 days too.

## Tests
```bash
uvx pytest plugins/kit/tests
```
Hook tests run the scripts through `run_hook.py` with the hook input on stdin, in temporary git repos. The tests use `ruff` and `mypy` from `PATH`, or fetch them with `uvx`; they skip those cases if neither works.
