---
paths:
  - "**/.claude/skills/**"
---
# Module skills

Projects keep per-module knowledge in `.claude/skills/<module>/`:
- `SKILL.md`: frontmatter (`name`, `description`, `paths`), invariants, settled decisions, a "read next" table and verify commands. About 80 lines at most. Links to `ARCHITECTURE.md`.
- `ARCHITECTURE.md`: the module explained for a human (Waqar, a teammate, a client dev), not an agent: what it does, who uses it, a diagram and walk-through of how it works, its data, its connections, the key decisions, its rules and limits, how it's tested, and where the code lives. 120 lines at most, plain English. Links `SCHEMA.md`, `INTERLINKING.md`, `FLOWS.md` and ADRs instead of repeating them; never copies a schema or a contract.
- `INTERLINKING.md`: every edge to other modules, one per line: FK/M2M, signal → receiver → effect, cache keys, cross-module imports, limits set by shipped clients.
- `FLOWS.md`: user journeys in the order the client calls the API, each naming the journey test that proves it.
- `LESSONS.md`: `- **L-<MOD>-NN** <rule>. *Prevents:* <failure>. *Test:* <test id or MISSING>`
- `CONTRACTS.md`, `SCHEMA.md`: generated from the OpenAPI schema and the models. Never edit them by hand; regenerate.

Writing them:
- State current facts and rules in the present tense. No dates, ticket or QA numbers, commit hashes, incident stories, history wording, open work, or praise.
- Update a hand-written file in the same change that alters what it describes: a new cross-module edge goes in INTERLINKING, a changed invariant or single-source helper in SKILL, a changed journey in FLOWS, a bug fix that teaches a rule in LESSONS, a changed behaviour, structure, data, permission or connection goes in ARCHITECTURE.
- Keep entries short and actionable. Cite symbols (`services.resolve_bounds`), not line numbers.
