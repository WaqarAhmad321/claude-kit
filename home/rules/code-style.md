# Code style

Applies to every language and project. Stack-specific rules load when you work in matching files.

## Scope
- Change only what the task needs. No speculative features, options, abstractions, compatibility shims or unrequested improvements. A bug fix doesn't need the surrounding code cleaned up.
- Search for an existing helper before writing one. Extract a shared helper once the same logic exists twice; never for a one-off.
- When a bug follows a pattern, grep for its siblings and fix them in the same change.
- Validate at system boundaries (user input, external APIs, environment). Trust internal code and framework guarantees; don't add fallbacks for cases that can't happen.
- Delete dead code, unused imports and unused parameters. Never keep code around in case someone wants it back; git has it.

## Comments
- Default to no comments. Names, types and structure carry the meaning.
- Comment only a non-obvious why: an external constraint, a trap, a deliberate oddity, a workaround and its reason. Present tense, one or two lines.
- Never write narration of what the code does, history (`used to`, `previously`, `changed from`), dates, ticket/QA/bug-log/review references, warning-emoji or ASCII banners, section dividers, commented-out code, or notes addressed to future readers. History belongs in commit messages.
- If an explanation is longer than the code it describes, it belongs in the module skill or docs.
- No docstrings that restate the signature. Write one only where the contract isn't evident from the code.

## Say it in code
- Magic numbers and strings become named constants. Arithmetic that needs explaining becomes a named function. A block that needs a heading becomes a function. A comment about a data shape becomes a type.
- Names are specific, greppable, and carry units (`timeout_seconds`, `price_cents`).
- Guard clauses and early returns over nesting. One job per function; high complexity means split it. No boolean parameters that switch behaviour: write two functions or take an enum.
- Prefer pure functions and immutable data. No mutable globals or mutable default arguments.

## Errors
- Catch only what you can handle, as narrowly as possible. Never swallow an exception (`except: pass`, empty `catch`); everything else reaches one global handler.
- Users see actionable messages. Stack traces, paths, SQL and config keys go to logs only.
- Error messages say what happened and what to do next.

## Config, secrets, logs, dependencies
- Configuration comes from environment variables, validated at startup. No insecure defaults: debug off, no wildcard hosts, no fallback secret keys.
- Clients for external services are built lazily from validated config. An unconfigured integration reports itself unavailable instead of crashing at import.
- Never write a secret, token or password into code, tests, docs, settings files or shell commands. Reference it through an environment variable (`"$API_TOKEN"`).
- Structured logs to stdout. Never log passwords, tokens, session IDs or personal data.
- Every dependency earns its place: prefer the standard library and existing dependencies, commit lockfiles, remove what's unused.
