# <!-- fill: project name --> docs

Every doc in this folder is listed here, grouped by who reads it. Add a line when you add a doc.

## Working on the code
- [README](../README.md): run and check it locally.
- [Deploy, verify and roll back](runbooks/deploy.md)

## API consumers
- [API overview](api/README.md): base URL, auth, errors, pagination and how the API changes.
- [Handoff template](api/handoffs/_template.md): copy it to `api/handoffs/<feature>.md` for each feature a client builds.

## Modules
Each module's `ARCHITECTURE.md`, under `.claude/skills/<module>/`, explains how it's built end to end, for a human reader. Add a line per module here as it's drafted with `/kit:skills-bootstrap`, such as `- [<module>](../.claude/skills/<module>/ARCHITECTURE.md)`.

## Decisions
1. [Record architecture decisions](decisions/0001-record-architecture-decisions.md)
2. [Choose the stack and hosting](decisions/0002-choose-the-stack-and-hosting.md)
