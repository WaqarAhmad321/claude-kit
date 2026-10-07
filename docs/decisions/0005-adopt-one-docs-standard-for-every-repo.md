---
status: accepted
date: 2026-09-30
decision-makers: Waqar Ahmad
---

# Adopt one docs standard for every repo

## Context and Problem Statement

Project repos collected dated journals, status ledgers, per-task summaries and `_V2` or `_FINAL` copies. Nobody could tell which file was current, and Claude read stale files as the truth. What docs should a repo have, and what should never be in it?

## Considered Options

* No standard; each project decides
* Keep docs outside the repo, in a wiki
* A small fixed set of docs in each repo, a list of banned file types with where their content goes, and update triggers

## Decision Outcome

Chosen option: the fixed set, written down in `home/rules/docs.md`. Every repo has a README, a CLAUDE.md of 150 lines or fewer, `docs/README.md`, ADRs in `docs/decisions/` in MADR 4 minimal style, and `docs/runbooks/deploy.md`. Other docs are added by project type. History lives in git and ADRs, and status and open work live on the PM board.

### Consequences

* Good, because each topic has one current file and Claude stops reading stale ones.
* Good, because the update triggers tie docs to the changes that make them stale.
* Bad, because existing repos need a cleanup pass.
* Bad, because the rule loads only when Claude works on docs files, so keeping banned files out needs a hook as well.
