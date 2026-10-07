---
status: accepted
date: 2026-09-29
decision-makers: Waqar Ahmad
---

# Enforce rules with hooks, deny rules and the sandbox

## Context and Problem Statement

CLAUDE.md, rules and skills are advice. Claude can miss them, most often late in a long session or after compaction. The audits of three earlier projects found rules that were written down but not followed. How do we make the rules that matter actually hold?

## Considered Options

* Advice only: CLAUDE.md, rules and skills
* Advice plus CI checks after the fact
* Bind every rule that matters to a permission rule, a hook or the sandbox, and keep the advice as the explanation

## Decision Outcome

Chosen option: bind each rule to a mechanism, because only permission rules, hooks and the sandbox are enforced by Claude Code itself. `docs/architecture/overview.md` holds the enforcement map. Hooks fail closed, and gates are ratchets that fail only on new violations, so they work in legacy repos from day one.

### Consequences

* Good, because the rules hold in every session, whatever the model remembers.
* Good, because ratchets let the kit run on messy repos without a cleanup first.
* Bad, because the hook code is ours to maintain and test.
* Bad, because some checks are best effort, such as the Bash guard for protected files, with the sandbox as the backstop.
* Bad, because every hook adds a little latency to tool calls.
