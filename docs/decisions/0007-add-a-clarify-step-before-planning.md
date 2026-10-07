---
status: accepted
date: 2026-10-02
decision-makers: Waqar Ahmad
---

# Add a clarify step before planning

## Context and Problem Statement

Claude either builds on a guess when a request is ambiguous, which can mean redoing risky work, or interrogates Waqar with questions he could have answered himself by reading the code or the rules. How should Claude decide when a request needs his judgement and when it doesn't?

## Considered Options

* Never ask; always take the most reasonable default and flag it in the report
* Ask whenever anything is unclear, for every size of task
* Ask only when the answer changes what gets built and can't be found by looking, scaled to the task's size

## Decision Outcome

Chosen option: ask only when it matters, scaled by size. `home/rules/workflow.md` adds a clarify step between "look" and "plan" for normal and big tasks, skipped for small ones unless a request turns out to be truly ambiguous. Its "Asking questions" section sets the bar (the answer must change the build and be unfindable by looking or a sensible default) and the shape: normal gets 0–3 questions in one round, big gets up to 4 questions per round for at most 2 rounds, most important first, the recommended option named, through the question tool. A big task's written plan carries an "Assumptions and open questions" part Waqar answers at approval. A subagent (`kit:builder`, `kit:researcher`) can't reach Waqar directly, so it stops that part and returns the question to the main chat instead of guessing.

### Consequences

* Good, because Waqar is asked only what genuinely needs his judgement, not what Claude could have looked up.
* Good, because a stated default lets him correct course without a round trip when he has no time to answer.
* Bad, because judging what's "worth asking" stays a subjective call Claude makes about its own request.
* Bad, because a subagent that hits a blocking question stalls until the main chat relays an answer back to it.
