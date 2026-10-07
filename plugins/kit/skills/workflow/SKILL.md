---
name: workflow
description: The playbook for a coding task from start to done, sized small, normal or big. Use when starting any feature, bug fix or refactor, when sizing or planning a task, and when briefing a builder or reviewer agent.
---

# Task workflow

The workflow rule sets the sizes, steps and approval points. This skill has the detail: how to size, what each step checks, how to brief the agents, and what done means.

## Size the task
Say the size and the reason in one line before you start.

- **Small** when all of these hold: one file or one function, the cause and the fix are known, nothing to decide, no change to an API, schema, permission, money flow or dependency.
- **Big** when any of these hold: a new module; changes in more than one module's invariants; a migration or a data change; auth, permissions or billing; an API a shipped client uses; a new dependency or integration; unclear requirements, or two sound approaches that give different products; more than about 600 changed lines.
- **Normal** is everything between.

Size up when unsure. When a task grows past its size mid-way, stop, say so, and pick up the missing steps.

## Models
| Task kind | Model |
|---|---|
| Planning | Opus |
| Normal building, testing, research | Sonnet (agent default) |
| Complex or hard task | Opus |
| Small-but-risky change: auth, permissions, money, migrations, deleting data, a shipped client's API, security | Opus |
| `kit:builder` retried after failing the same task twice | Opus (escalate) |
| `kit:reviewer` | Opus, always (its agent default) |
| Quick read-only status check | Haiku |

To get a builder call on Opus, pass `model: opus` on that Agent call; it overrides the agent's `sonnet` frontmatter default for that invocation only.

## The steps
### Look
- [ ] Read the module skill (`.claude/skills/<module>/SKILL.md`) and the FLOWS, LESSONS and INTERLINKING entries for what the task touches.
- [ ] Read the code and its callers. List the other paths that compute or save the same thing.
- [ ] For a bug, find the failing path and how to reproduce it.
- [ ] Check the PM board for a card on this task.

In the main chat, looking goes through Explore agents; keep only their conclusions.

### Clarify (the question limits by size are in the workflow rule's "Asking questions")
- [ ] After looking, ask only what changes what gets built and can't be found by looking or settled with a sensible default.
- [ ] One round, most important first: plain words, multiple choice, the recommended option first with a one-line reason, through the question tool.
- [ ] State assumptions for everything else; don't re-ask what's already answered. Record answers in the plan's "Assumptions and open questions" part.
- [ ] Mid-build, a new unclear point that changes the result gets one more round (2 rounds total); otherwise take the sensible default and report it.
- [ ] A subagent (`kit:builder`, `kit:researcher`) can't ask Waqar: it stops that part and returns the question to the main chat instead of guessing.

Good: "Soft delete the property or hard delete it? Recommend soft delete, since two reports still read deleted properties." (changes the data model; no default is safe to guess)
Bad: "Should I use async/await or .then() here?" (a style choice the rules already settle; look it up, don't ask)

### Research (big; normal when a library or API question comes up)
- [ ] Send the questions to `kit:researcher`: library behavior for the project's version, config, limits, and existing helpers to reuse.
- [ ] Carry only sourced facts into the plan. Treat UNVERIFIED items as open questions.

### Plan
- Normal: a short plan in the chat, five to ten lines: what changes, the tests that prove it, the docs that change, the main risk. Go ahead unless it needs a product decision from Waqar.
- Big: fill in [templates/plan.md](templates/plan.md) and show it to Waqar. Build nothing until he approves it. Keep the plan in the chat or in `~/.claude/plans/`, never as a file in the repo. A decision worth keeping becomes an ADR.

### Build
- [ ] Brief `kit:builder` (below). One brief, one outcome.
- [ ] Big tasks go in slices. Each slice leaves the app working and is tested before the next starts.
- [ ] Builders run in parallel only on separate files. Give each its own worktree when they share a repo.
- [ ] Lint feedback arrives on each edit; the builder fixes it as it goes.

### Test
- [ ] For a bug, the regression test comes first and is seen to fail.
- [ ] Real HTTP journeys in the frontend's call order, with each role and the error paths (verification.md).
- [ ] The project's checks pass, read from their summary lines.

### Re-read
- [ ] The builder does the critical re-read from verification.md before it reports, then reruns the checks.

### Docs
- [ ] Update only what the change touched, per the docs rule: the plan's docs list.
- [ ] No per-task summary, plan or notes files.

### Review (normal and big)
- [ ] The main chat briefs `kit:reviewer` (below), once the docs step has run: one review covers the code and the docs together. The builder never reviews its own work.
- [ ] On `VERDICT: FIX`, send the findings to the same builder (SendMessage keeps its context), then review again with the same base and the earlier findings listed.
- [ ] If you think a finding is wrong, say why in the next review brief; don't drop it silently. After two rounds on the same finding, take it to Waqar.

### Commit
- [ ] Normal and big: commit only after `VERDICT: PASS`. Small: commit when the checks pass.
- [ ] Small Conventional Commits, one concern each, files added by explicit path. A `fix:` commit includes its regression test.

### Report
- [ ] A few sentences in product terms: what works now, the evidence for each acceptance criterion in the plan (the command and its summary line, or the HTTP journey result), what isn't verified, and anything Waqar must decide or know.
- [ ] Open items go on the PM board. Ask before creating or changing cards.
- [ ] Big tasks: write the handoff note with `/kit:handoff`.

## Brief a builder
The builder starts fresh. It doesn't see the chat, so the brief carries everything. Use [templates/brief-builder.md](templates/brief-builder.md). It covers:
- Goal: the outcome in product terms.
- Context: why, decisions made (the answers to any clarifying questions), what's done, research facts with sources, the size, the base commit if known, and the model it's built by (see Models above).
- Module skill to read first, and the other files or symbols that matter.
- Constraints: what not to touch, the rules that matter here, and whether to commit.
- Files: each file and its expected change.
- Tests that prove it, including the regression test for a bug.
- Docs to update, or "none".
- Report format: the builder's standard report, plus anything extra you need.

For normal and big tasks, tell the builder not to commit. After the review passes, send it the go-ahead to commit, and to push only if the brief calls for it (in a client repo, pushing is Waqar's call).

The builder never spawns a fork subagent; for read-only help it uses an Explore or `kit:researcher` agent, told not to edit, commit or push. Only the builder commits and pushes its own work, in its own worktree when it has one; that branch merges into the working branch only after the review that covers it passes.

## Brief the reviewer
Give `kit:reviewer`:
- Checkout: the absolute path of the one checkout to review, such as the builder's worktree. The reviewer names it on its `Reviewed:` line, and its PASS counts only for that checkout as it is when the review ends. So review a worktree before merging it. Merging one reviewed branch into an unchanged checkout needs no second review when the worktree was made before any other work of this session was committed there; otherwise, and after merging two or more, the merged checkout needs its own review.
- Base: the builder's reported base commit.
- The plan, or for a normal task the goal and what done looks like.
- Builder model: the model that built the change (from the brief's "Built by" line). Sonnet, Haiku or unknown gets extra scrutiny (see the reviewer's "Be critical" section).
- The modules touched, so it reads their skills.
- The check and journeys commands, and what the builder already ran.
- Trade-offs Waqar accepted, so they aren't reported as findings.
- On a re-review: the round number and the earlier findings.

Don't pass on the builder's opinion of its own work. The reviewer judges the diff against its ten-area checklist, from correctness and design fit to operations. Its findings are blocker, should-fix or nit; only nits don't block.

## Done
- [ ] The change does what the plan says.
- [ ] Tests prove it: journeys, roles, error paths, and a regression test for each fix, all green.
- [ ] Lint, types and tests pass, read from their summary lines.
- [ ] The docs the change touched are updated.
- [ ] Normal and big: the latest review says `VERDICT: PASS`.
- [ ] Committed in small conventional commits.
- [ ] Waqar has a short report; open items are on the PM board; big tasks have a handoff note.
- [ ] Every acceptance criterion has its evidence in the report; nothing is done on a claim alone, and anything not verified is named as not verified.
