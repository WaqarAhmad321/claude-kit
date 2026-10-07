# API overview

<!-- fill: who calls this API (the web app, a mobile app and its oldest supported build, partners) and where each environment's base URL is. -->

## The contract
- The schema is generated from the code into `docs/api/openapi.yaml`. Never edit it by hand. CI regenerates it and fails when the committed copy is stale.
- Each feature a client builds has a handoff in `handoffs/<feature>.md`, organised by operationId and edited in place.

## Conventions
- Errors are RFC 9457 `application/problem+json` with an `errors` extension for field errors.
- JSON fields and query parameters are snake_case. Timestamps are RFC 3339 in UTC.
- Authenticated by default; anonymous endpoints are listed in the schema with no security requirement.
- Collections are paginated, with a server-side maximum page size.
- PATCH follows JSON Merge Patch: an absent field is unchanged, and `null` clears it.

## Changing the API
- Changes are additive. CI runs `oasdiff breaking` against the main branch's schema and fails on a breaking change.
- A breaking change for a shipped client gets a compatible path first: a new version path or an opt-in header, kept until the client has moved. It also gets an ADR.
- Update `openapi.yaml` and the affected handoffs in the same change.
