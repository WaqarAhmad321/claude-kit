---
status: accepted
date: 2026-09-29
decision-makers: Waqar Ahmad
---

# Keep module knowledge in guarded module skills

## Context and Problem Statement

Each project module has invariants, edges to other modules, client journeys and lessons from past bugs. Claude edited modules without that knowledge in context. Putting it all in CLAUDE.md makes the always-loaded context too big. Where should module knowledge live, and how do we make sure it's read?

## Considered Options

* One large CLAUDE.md per project
* A docs folder per module, read when someone remembers to
* Module skills (v2 of an earlier project's system): `.claude/skills/<module>/` with `paths`, loaded on demand and guarded before edits

## Decision Outcome

Chosen option: module skills v2, because they load only when needed and a hook can check they were read. Each module has `SKILL.md` (invariants, settled decisions, what to read next, verify commands), `INTERLINKING.md`, `FLOWS.md`, `LESSONS.md`, and generated `CONTRACTS.md` and `SCHEMA.md`. The `paths` frontmatter makes Claude Code load the skill for matching files, and the kit's pre-edit guard denies an edit until the agent has loaded it. The format is in `home/rules/module-skills.md`.

### Consequences

* Good, because always-loaded context stays small and the knowledge sits next to the code it governs.
* Good, because the guard and the stop check make reading the skill a requirement, not a hope.
* Bad, because each module's skill has to be kept current in the same change that alters the module.
* Bad, because the generated files need a generator per stack.
