---
paths:
  - "**/views*.py"
  - "**/views/**/*.py"
  - "**/serializers*.py"
  - "**/serializers/**/*.py"
  - "**/urls.py"
  - "**/permissions.py"
  - "**/docs.py"
  - "**/api/**/*.py"
  - "**/app/**/route.ts"
  - "**/actions.ts"
  - "**/openapi*.yaml"
  - "**/openapi*.json"
---
# API design and security

## Contract
- Errors are RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`) with an `errors` extension for field errors, produced by one global exception handler. Never return stack traces or raw exception text.
- Paths are plural kebab-case nouns (`/trip-stops/`). JSON fields and query parameters are snake_case end to end.
- GET is safe; PUT and DELETE are idempotent; a create returns 201 with a `Location` header.
- Use the most specific status. Return 404 instead of 403 when a 403 would reveal that a record exists. 429 carries rate-limit headers.
- PATCH follows JSON Merge Patch: an absent field is unchanged, `null` clears it.
- Every collection is paginated from day one with a server-side maximum: page numbers for bounded admin lists, cursors for feeds. Filter and ordering fields are allowlisted.
- Side-effecting POSTs that clients may retry (payments, submissions, imports) accept an `Idempotency-Key`.
- Evolve additively. Timestamps are RFC 3339 in UTC. The OpenAPI schema is generated from code and checked for breaking changes; a breaking change needs a new version path and a migration plan for existing clients.
- Keep the schema accurate for each endpoint you touch: the request body matches what the view accepts, every response and error status is listed, and descriptions cover behaviour and side effects rather than implementation.

## Security
- The default permission is authenticated. Anonymous access is an explicit, deliberate exception per endpoint, and privileged actions check the role on the server.
- Scope every queryset to the requesting user in `get_queryset`. Object-level permission checks don't run on list and create, so they are not enough alone.
- Allowlist readable and writable fields. Clients never set ownership, role, status, verification or billing fields.
- An anonymous or low-privilege request never modifies another account (password, role, email, type).
- Bound everything: page size, string and array lengths, upload size, outbound request timeouts, and rate limits on login, OTP and other abuse-prone endpoints.
- Parameterized SQL only. Server-side fetches of user-supplied URLs go through a host allowlist, and third-party responses are validated before use.
