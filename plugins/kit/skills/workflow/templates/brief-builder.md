Goal: <the outcome in one or two sentences, in product terms>

Context:
- Why: <the problem, and what Waqar decided and why>
- Decisions made: <answers to any clarifying questions asked, plain and short, or "none asked">
- Done so far: <earlier slices, findings, relevant facts with sources>
- Size: <small | normal | big, slice N of M>
- Base commit: <sha, or "record it with git rev-parse HEAD before your first change">
- Built by: <sonnet (default) | opus (hard, complex or small-but-risky; or escalated after two same-task failures)>

Read first:
- Module skill: `.claude/skills/<module>/SKILL.md`, plus <FLOWS.md | LESSONS.md | INTERLINKING.md> for <reason>
- <other files or symbols that matter>

Constraints:
- Don't touch: <files, APIs or behavior outside the scope>
- Keep: <invariants, client contracts, or decisions that apply here>
- Commit: <"commit when the checks pass" for small | "don't commit; I'll send the go-ahead after review">

Files:
- <path>: <expected change>

Tests that prove it:
- <journey or test id>: <what it checks, including roles and error paths>
- Regression (bug): <the test to write first and see fail>

Checks to run: <lint, type, test and journeys commands>

Docs to update before you report: <file: what changes, or "none">

Report: your standard report<, plus anything extra>.
