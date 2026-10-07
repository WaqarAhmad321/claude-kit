# Verification and testing

## Before changing code
- Read the code you are changing and its callers. Trust the code over docs, memory or skill files; when they disagree, the code wins and the doc gets fixed.
- In a project with module skills (`.claude/skills/<module>/`), read that module's SKILL.md before editing its files.
- List the other code paths that compute or save the same thing. They are your divergence checklist for the re-read.

## How to test
- Test the way the client uses the system: real HTTP requests against a running local stack, in the order the frontend calls them (log in, call, read the result back). Passing unit tests are not evidence that a feature works.
- Every endpoint you add or change is covered for: the happy path, each role that can reach it (anonymous, owner, other user, admin), and its error paths (400 validation, 401, 403, 404, 405, 409, 429 where relevant). No response may be a 5xx.
- Check object-level access: user A can't read or change user B's records. Single-user tests don't catch this.
- A bug fix starts with a regression test that fails on the current code, then passes after the fix.
- Unit tests are for pure logic (calculations, parsers, state machines). Don't mock the ORM, the HTTP layer or your own services to make a test pass; mock only external services at the process edge.
- Fix the code, not the test. Never weaken an assertion, hard-code an expected output, or skip a test to get green.
- Tests never call paid or real third-party services and never depend on ambient environment variables.

## Critical re-read
After every task that changed code, before running journeys and before saying done. For a one-line fix, re-read just that function and its callers.
1. Re-read the changed files as they are now, not from memory, plus their callers and any copies of the same logic.
2. Hunt in this order:
   - Edge cases: empty, null, single, duplicate or reversed input; too little of a resource; ambiguous input; which value wins on an override; failures left unhandled.
   - Divergence: paths that must agree (preview and save, list and detail, a module and its copy) still agree. Fix the rule in one shared helper, not in each copy.
   - Shown versus accepted: the write path accepts everything the UI or API offers, and what is saved is what gets shown back.
   - Scope: queries still filter by owner, tenant or state.
3. Fix what you find, then rerun the checks and journeys from the start.

## Before saying done
- Run the project's checks: lint, type check, and tests including the HTTP journeys for what you touched. A run passed only if its summary line says so.
- Re-check the flows that share what you changed: same model, endpoint, serializer or component.
- Non-trivial changes get an independent review from the `kit:reviewer` agent, per the workflow rule.
- If you couldn't verify something, say so. Say "unknown" rather than guess.

## Production safety
- Treat anything that can reach production data as a write: list its side effects and ask before running it. Never run migrations against a remote database without asking.
- Once a client depends on an API (a shipped app build, a frontend, a partner), changing its shape, status codes or auth is a breaking change: call it out and keep a compatible path until the client has moved.
