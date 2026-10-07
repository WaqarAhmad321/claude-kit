---
name: adopt
description: Bring the kit into an existing repo without breaking it. A read-only audit first, then grouped changes, each applied only after Waqar agrees. Runs only as /kit:adopt.
argument-hint: "[repo directory]"
disable-model-invocation: true
---

# Adopt the kit

Audit an existing repo against the kit, then bring the kit's gates, docs standard and Claude files in one approved group at a time. Nothing is overwritten, reformatted, deleted or rewritten in history without a yes for that group.

Given: $ARGUMENTS

## Who does what
The audit and every applied group are hands-on, so in the main chat send them to subagents: the audit to an Explore agent (it can run read-only commands), each approved group to a `kit:builder` with the group's file list and this skill's rules. The proposals, questions and decisions stay in the main chat. In a subagent, or in direct mode, do the work yourself.

`<templates>` is the directory `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/apply_template.py --list-stacks` prints. `apply_template.py --dry-run --json --only <glob>` shows what a group would add without writing; files it classes `differs` are never written, and `.gitignore` is only ever appended to.

## 1. Detect the stack
From the repo root: `manage.py` or Django in `pyproject.toml`/`requirements*.txt` is `django`; `next` in `package.json` is `nextjs`; `vite` and `react` is `react-vite`; `express` with `mongoose` is `mern`. A monorepo can hold several; record each with its directory. If none fits, say so and adopt only the common pack. Note the package manager, the Python and Node versions, and how it deploys (a workflow or host that deploys on every push to `main` is a risk to state up front).

## 2. Audit (read-only)
Run each check, keep the numbers, and never print a secret value.
- **Lint, types, format, tests, CI**: which configs exist (ruff, black, isort, flake8, mypy, oxlint, eslint, biome, prettier, `tsconfig` strict flags), what each workflow in `.github/workflows/` runs and whether one deploys, the test count and runner, and whether journeys, a role matrix or a test stack exist. Run the existing lint and type commands once and record the error counts.
- **Docs sprawl**: `python3 <templates>/common/scripts/docs-check.py --root .` lists every gap against the docs standard. Group its output by kind (missing required docs, files outside the layout, scratch or dated names, dates and history words, missing ADR shape, unindexed docs, env vars missing from `.env.example`) and count each.
- **Old Claude systems**: CLAUDE.md's line count and whether it reads as a journal; `.claude/skills/*` without `paths` frontmatter (v1 skills) and their files (`USER_FLOWS`, `BUG_LOG`, `API_CONTRACTS`, `DB_SCHEMA`, underscore files like `_CODING_RULES.md`); `.claude/memory/`; `.claude/commands/`, `.claude/agents/`, `AGENTS.md`; instructions that tell Claude to test against production.
- **Committed secret-risk files**: `git ls-files` for `.env*` other than `.env.example`, `.claude/settings.local.json`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, database dumps and `*.sqlite3`. List paths only.
- **Secrets in history**: `betterleaks git --redact --no-banner -f json -r <scratchpad>/betterleaks.json`, then summarise it with a script that reads only each finding's `RuleID`, `File` and `Commit`: counts by rule, by file and by commit. Never show a matched value, and leave the report in the scratchpad. If betterleaks is missing, say so and give the install command.
- **Kit opt-out**: `.claude/settings.json` or `.claude/settings.local.json` holding `"kit@claude-kit": false`, and whether that file is tracked, ignored or untracked.

Report the audit as one table (area, what's there, what the kit expects, size of the gap), then the risks: committed secrets, deploy-on-push, production testing instructions.

## 3. Propose groups
Offer these groups in order, each with its exact file list (from a dry run) and what it changes for daily work. Apply a group only after Waqar says yes to it. Commit each group on its own, on a branch when a push to `main` deploys: `chore(kit): <what the group adds>`.

1. **Secrets first.** For every committed secret-risk file: `git rm --cached <file>` and the `.gitignore` line. Leaked values need rotating, which Waqar does; history rewrites are his call and never run from here.
2. **Ignore lines and docs skeleton.** `--only .gitignore` appends the missing lines. `--only 'docs/*'` adds `docs/README.md`, the two ADRs and the deploy runbook; existing docs stay where they are until the migration map is done. Fill the markers from what the repo shows.
3. **Claude settings.** A tracked `.claude/settings.json` with deny rules for production hosts and database URLs, such as `Bash(*<prod api host>*)`. If `.claude/` is ignored, add `!.claude/settings.json` (and `!.claude/kit.json`, `!.claude/skills/`) to `.gitignore`.
4. **kit.json and baseline.** Write `.claude/kit.json` by hand rather than with the template tool, whose merged file turns the journeys gate on. It holds `gate.review` only (`min_lines` 80 for a large legacy repo, and `always` for its auth, permissions, migrations and settings paths), `docs.allowed` for doc folders that stay until the migration, and `docs.ignore` for paths that will never be this repo's own docs, such as vendored content. No `gate.journeys` yet: it would block every change until journeys exist. `kit_baseline.py --root .` only where mypy or tsc actually runs.
5. **Lint and type configs, no mass reformat.** Add the stack's configs from `<templates>/<stack>/`, sized for legacy code: for Python, ruff with `E4,E7,E9,F,I,B,UP,DJ,S,BLE,ERA,SIM` and a per-file-ignores entry per app generated once from `ruff check --statistics` (the list only shrinks); mypy with django-stubs pinned to the Django version; for TypeScript, oxlint's `correctness` and `suspicious` categories with an override listing today's offenders of the size rules. A one-shot format of the whole repo is a separate group, run only when Waqar asks and no other branches are open, and its commit goes in `.git-blame-ignore-revs`.
6. **lefthook.** The common `lefthook.yml` and the stack's `.lefthook/<stack>.yml`. If husky, pre-commit or `core.hooksPath` is already in use, say so and ask which wins; never unset it silently.
7. **CI.** The docs workflow and the stack's `ci.yml`, adjusted to the repo's commands. Existing workflows stay untouched; a deploy-on-push workflow is flagged, not changed.
8. **A lean CLAUDE.md, as a proposal.** Write `.claude/CLAUDE.proposed.md` from the common and stack templates and what the audit found, 150 lines or fewer, current facts only. Add the path to `.git/info/exclude`. Waqar reads it and decides when it replaces CLAUDE.md; the journal content goes to git history, ADRs and the PM board. Generate `AGENTS.md` too, with `build_agents_md.py` and the stack's rules (the mapping in `/kit:new`'s step 5); a repo that already has one keeps it as is until Waqar asks to regenerate it, which keeps everything under its markers — or, with no markers yet, the whole existing file — as the project section.
9. **Module skills.** Waqar runs `/kit:skills-bootstrap`, in its v1 migration mode when v1 skills exist.
10. **Docs migration map.** A table in the chat: every doc, its action (keep, merge, move, ADR, PM card, LESSONS, delete) and where its content goes, following the docs rule. Applied later, a few docs per commit, after the frontend team hears about any handoff that moves.
11. **Turn the gates on gradually.** Review gate (group 4), then the lint ratchet (group 5), the type baseline, lefthook, CI as a required check, and last `gate.journeys` in `kit.json` once the first journeys pass on a clean stack.
12. **Remove the opt-out, only when asked.** Waqar deletes the opt-out file himself (`! rm .claude/settings.json`), or group 3's tracked settings file replaces it without an `enabledPlugins` entry. Then a fresh session in the repo should show the kit's context.

## Rules
- Never overwrite: a file that differs gets its diff shown and its own question.
- Never mass-reformat, delete docs or rewrite history inside another group.
- The kit asks before each gate-config write (`kit.json`, lefthook, lint and type configs, `tsconfig`, workflows, `.claude/settings.json`). That's expected; approve per group.
- After each applied group, run the checks it touches and report what passed and what still fails, in numbers.
