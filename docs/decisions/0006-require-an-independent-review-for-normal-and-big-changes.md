---
status: accepted
date: 2026-09-30
decision-makers: Waqar Ahmad
---

# Require an independent review for normal and big changes

## Context and Problem Statement

The agent that wrote a change is a weak judge of it, because it shares the assumptions that produced it. Reviewing every change costs an extra agent run, which is pure overhead on a one-line fix. Which changes need a review, and how do we make sure it happens?

## Considered Options

* No required review; rely on the builder's own re-read
* An independent review of every change
* An independent review of normal and big changes only, checked by a hook (the review gate)

## Decision Outcome

Chosen option: review normal and big changes, checked by the review gate. The workflow (`home/rules/workflow.md`) sizes every task and runs the docs step before the review, so one review covers the code and the docs together. For normal and big tasks, the main chat runs `kit:reviewer`, a read-only agent on Opus, against the diff, the plan and the module skills. Its last line is `VERDICT: PASS` or `VERDICT: FIX`; the review gate reads that line, and a session can't finish a non-trivial change without a PASS. Small changes skip the review.

### Consequences

* Good, because every non-trivial change gets a second, independent read against the plan, the module invariants, security and the tests.
* Good, because a finding must describe a failure scenario, which keeps the reviews short and useful.
* Bad, because each normal or big task costs an extra Opus run, and more when a FIX needs another round.
* Bad, because sizing is judgment, so an undersized task can skip the review unless the gate catches it.
* Bad, because the verdict line is a contract between the agent's prompt and the hook, and both must change together.
