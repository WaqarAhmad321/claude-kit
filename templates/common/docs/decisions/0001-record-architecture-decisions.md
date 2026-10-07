---
status: accepted
date: YYYY-MM-DD
decision-makers: <!-- fill: who decided -->
---

# Record architecture decisions

## Context and Problem Statement

Decisions about this project's architecture, dependencies and conventions get made in chats, calls and pull requests, and the reasons get lost. Later, nobody can tell whether a choice was deliberate or an accident, and stale notes get read as the truth. How should we record the decisions that matter?

## Considered Options

* Don't record them; rely on memory and the commit history
* Keep a running decisions log in one file
* One Architecture Decision Record (ADR) per decision, in MADR 4 minimal style

## Decision Outcome

Chosen option: one ADR per decision, because each decision gets its own reviewable file that stays true once accepted. ADRs live in `docs/decisions/NNNN-kebab-title.md` and are listed in `docs/README.md`. An accepted ADR doesn't change: a new ADR supersedes it, and the old one's status points to the new one. A new big dependency or integration, a change to the stack or hosting, and a deliberate break from the kit's rules each get an ADR.

### Consequences

* Good, because the reason behind each choice survives the people and chats that made it.
* Good, because a changed decision leaves a trail instead of an edited file.
* Bad, because writing an ADR takes time in the change that makes the decision.
