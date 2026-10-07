# Task workflow

Size every task before starting, then follow the steps for its size. The `kit:workflow` skill has the playbook, the brief formats and the plan template.

## Sizes
- **Small**: one obvious change in one place (a typo, a config value, a one-line fix with a known cause), with nothing to decide and no change to an API, schema, permission or dependency. Steps: look → change → check → commit. No clarify step; ask only if it turns out to be truly ambiguous.
- **Normal**: a feature or a bug fix across a few files in one or two modules, with a clear approach. Steps: look → clarify → short plan → build → test → critical re-read → docs → review → commit → short report.
- **Big**: a new module, a change across modules, anything risky (auth, permissions, money, migrations, deleting data, an API a shipped client uses), or anything ambiguous. Steps: look → clarify → research → written plan (with a short "Assumptions and open questions" part Waqar answers at approval) → Waqar approves → build in small slices, testing each → docs → review → commit → handoff note.
- When unsure, size up. When a task grows past its size, stop, say so, and take on the missing steps.

## Asking questions
- Ask only when the answer changes what gets built AND it can't be found in the code, docs, module skills or memory, or settled by a sensible default.
- Worth asking: what the user sees or can do; scope edges; business rules; risky or hard-to-undo choices (data, money, auth, production, APIs a shipped client uses); conflicting requirements; tradeoffs that need his judgement.
- Don't ask: things you can look up; technical choices with a clear best practice (pick it, say why); style (the rules cover it); "should I go ahead" on agreed work.
- How: look first, then ask everything in one round, the most important first. Small: none, unless the request turns out to be truly ambiguous. Normal: 0–3 questions. Big: up to 4 questions per round, at most 2 rounds. Plain words, multiple choice, the recommended option first with a one-line reason, through the question tool. State assumptions for everything else so he can correct them.
- Don't re-ask what he has answered. Record answers in the plan; durable product rules go into the module skill.
- Mid-build: a new unclear point that changes the result means stop and ask (a big task's second round counts against its 2-round limit); otherwise take the sensible default and list it in the report.
- Subagents can't ask Waqar: builders and researchers stop that part and return the question to the main chat instead of guessing.

## Approval points
- A big task builds nothing until Waqar approves its written plan.
- A normal task's short plan goes in the chat and work goes ahead, unless it needs a product decision from Waqar.
- Stop and ask when the approved plan or scope has to change, and before anything that can reach production.

## Who does what
- Main chat: looks (through Explore agents) and plans with Waqar, sends research to `kit:researcher`, briefs `kit:builder`, runs `kit:reviewer` on the result, and reports back in product terms.
- Builders build, test, do the critical re-read and the docs, then commit when told. They don't review their own work.
- The reviewer is independent and read-only. The main chat starts it, not the builder, and passes the base commit, the plan and the checkout to review, which the reviewer names on its `Reviewed:` line. Its last line is `VERDICT: PASS` or `VERDICT: FIX`. On FIX, the builder fixes the findings and the review runs again.

## Models
- Sonnet: normal building, testing and research.
- Opus: planning; complex or hard tasks; small-but-risky changes (auth, permissions, money, migrations, deleting data, an API a shipped client uses, security); the reviewer, always.
- Haiku: quick read-only status checks only.
- Escalation: a Sonnet builder that fails the same task twice gets retried on Opus.
- Pass `model: opus` when briefing the builder for a hard or risky task.

## Done means
- The change does what the plan says, and the checks in verification.md pass.
- Every acceptance criterion in the plan has its evidence in the report: the command and its summary line, or the HTTP journey result. Nothing is marked done on a claim alone; anything not verified is reported as not verified.
- The docs the change touched are updated, per the docs rule.
- Normal and big: the latest review, covering the code and the docs together, ends in `VERDICT: PASS` before the change lands on the working branch.
- The work is in small conventional commits.
- Waqar has a short report in product terms. Open items go to the PM board, not into repo files. A big task also leaves `.claude/HANDOFF.md` (`/kit:handoff`).
