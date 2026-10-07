# <!-- fill: feature --> handoff

<!-- fill: what a client builds with this, in one or two sentences, and which clients use it. -->

The schema in `docs/api/openapi.yaml` is the source of truth for every field. This handoff adds what a schema can't say: the order of the calls, the rules behind the fields, and what the client must do with each error.

## Flow
1. <!-- fill: the calls in the order the client makes them, by operationId. -->

## Operations

### `<!-- fill: operationId -->`
- **Who**: <!-- fill: the roles that may call it, and what other roles get (401, 403 or 404) -->
- **Rules**: <!-- fill: validation and business rules the client should show before it calls -->
- **Errors**: <!-- fill: each status, what it means for the user, and what the client shows -->
- **Side effects**: <!-- fill: emails, notifications, cached data the client should refresh -->

## Limits
<!-- fill: rate limits, page sizes, upload sizes and timeouts the client must respect. -->
