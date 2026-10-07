---
paths:
  - "**/*.md"
  - "**/docs/**"
  - "**/README*"
  - "**/CHANGELOG*"
---
# Docs

## Every repo has
- `README.md`: what it is, how to run it, where to read next.
- `CLAUDE.md`: 150 lines or fewer.
- `docs/README.md`: an index of the docs, grouped by reader.
- `docs/decisions/NNNN-kebab-title.md`: ADRs in MADR 4 minimal style. An accepted ADR doesn't change; a new ADR supersedes it, and the old one's status points to the new one.
- `docs/runbooks/deploy.md`: how to deploy, verify and roll back.

## Add by project type
- `.claude/skills/<module>/ARCHITECTURE.md`: a human-readable explanation of the module, per module-skills.md.
- `docs/architecture/overview.md`: arc42-lite, with C4 context and container diagrams.
- `docs/api/README.md`, the generated `docs/api/openapi.yaml`, and `docs/api/handoffs/<feature>.md` by operationId, edited in place.
- `docs/partners/<partner>.md`, `docs/guides/<topic>.md`, and `CHANGELOG.md` only when clients consume releases.

## Never add (and where the content goes)
- Dated journals, and per-task summary, plan, report, notes or fix files: the commit message, the PR or the PM board.
- Status ledgers (TODO, REMAINING, STATUS): the PM board.
- Versioned siblings (`_V2`, `_REPLY_2`, `_FINAL`, dates in names): the one file, edited in place.
- Bug-log chronicles: a LESSONS.md rule plus its regression test.
- Superseded files kept for history: delete them, since git has them; a changed decision gets a superseding ADR.
- Hand-copied schemas and contracts, and duplicates: generate them, or link to the one source.
- Committed PDF exports: build them when needed.

## Update in the same change
- API change: `openapi.yaml` and the affected handoffs.
- New env var: `.env.example`.
- New big dependency or integration: an ADR.
- Deploy change: the deploy runbook.
- Module invariant change: the module skill.
- Module behaviour, structure, data, permissions or connections change: the module's `ARCHITECTURE.md`.

## Style
- Present tense, with dates only in ADRs and the CHANGELOG. One topic per file, named by its subject. Link rather than copy.
- Generated files start with a GENERATED header naming the command that makes them.
