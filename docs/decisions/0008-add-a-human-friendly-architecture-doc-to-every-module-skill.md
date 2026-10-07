---
status: accepted
date: 2026-10-02
decision-makers: Waqar Ahmad
---

# Add a human-friendly architecture doc to every module skill

## Context and Problem Statement

Module skills (`.claude/skills/<module>/`) are written for agents: terse invariants, path globs and generated schemas. A human reviewer, Waqar, a teammate or a client dev, has nowhere to read in a couple of minutes how a module is actually built and how its pieces connect, so staying in the loop means reading the code or asking Claude to explain it again each time. How do we give humans a doc of their own without duplicating what the agent files already say?

## Considered Options

* Rely on SKILL.md, read by humans too
* A project-wide `docs/architecture/overview.md` only, with no per-module detail
* One `ARCHITECTURE.md` per module skill, written for humans, linked from SKILL.md

## Decision Outcome

Chosen option: one `ARCHITECTURE.md` per module, 120 lines or fewer, plain English: what it does, who uses it, a diagram and walk-through of how it works, its data, its connections, the key decisions, its rules and limits, how it's tested, and where the code lives. It links `SCHEMA.md`, `INTERLINKING.md`, `FLOWS.md` and ADRs instead of repeating them, and `SKILL.md` links to it. `/kit:skills-bootstrap` drafts it from the code for new and existing modules. The module-skills Stop check blocks finishing a change that creates a module without one, and reminds without blocking when a module the change only touches still lacks one, so existing projects grow coverage without a blocking migration. `docs-check.py` checks its length and headings when the file exists.

### Consequences

* Good, because a reviewer gets one short, current doc per module instead of reading agent-oriented files or asking Claude to re-explain the module.
* Good, because the ratchet grows coverage over existing modules without a one-time migration that blocks everyone's work.
* Bad, because it's one more file to keep current in the same change that alters a module's behaviour, data or connections.
* Bad, because a diagram can drift from the code faster than prose does, if nobody re-checks it.
