---
paths:
  - "server/**/*.{ts,mts,js,mjs}"
---
# Node / Express

- The API contract and security rules in `~/.claude/rules/api-design.md` apply here too: problem+json, snake_case, 201 with `Location`, bounded pagination, authenticated by default.
- Layers: routes → controllers → services → data access. Route files only wire paths to middleware and controllers. Controllers parse input, call one service and shape the response. Business rules live in services. Only the data-access layer (models, repositories, `db/`) imports Mongoose or the driver.
- Parse `req.body`, `req.params` and `req.query` with Zod in the controller and use only the parsed value. Responses go through an explicit DTO, never a raw document.
- Express 5 passes rejected promises to the error handler, so no try/catch-and-`next(err)` wrappers or `express-async-handler`. Services throw typed errors (`NotFoundError`, `ConflictError`).
- One error middleware, registered last, turns every error into RFC 9457 `application/problem+json`: Zod issues become a 400 with the `errors` extension, typed errors keep their status, anything else is logged and returned as a generic 500. A catch-all 404 sits just before it.
- Order: `helmet()`, `cors()` with an explicit origin allowlist and `credentials: true` (never `*`), `express.json({ limit })`, then routes. Behind a load balancer set `trust proxy` to the real hop count, or `req.ip` and rate limits see the proxy.
- Rate-limit login, signup, password reset and OTP routes with express-rate-limit (a shared store once there's more than one instance). Over the limit is a 429 with rate-limit headers.
- Web auth is an HttpOnly, Secure, SameSite=Lax cookie, never a token in the body for localStorage. State-changing routes reject a foreign `Origin`. Hash passwords with argon2id or bcrypt.
- Services receive the requester and scope every query to their user or tenant id. Load by `{ _id, owner_id }` and answer 404 when it doesn't match.
- One config module parses `process.env` with Zod at startup and exits on failure. No defaults for secrets, URLs or credentials. Nothing else reads `process.env`.
- Log with pino and pino-http (request ids) to stdout. `redact` the authorization and cookie headers, passwords and tokens. Never log request bodies or personal data.
- Graceful shutdown: on SIGTERM and SIGINT call `server.close()`, let in-flight requests finish, close the Mongo connection, then exit, with a hard timeout.
- TypeScript strict with `noUncheckedIndexedAccess`. No `any`, `@ts-ignore` or non-null `!`. Type-aware lint (`no-unsafe-type-assertion`, `no-floating-promises`, `no-misused-promises`) catches casts, unawaited writes and async callbacks.
- Don't cast untrusted data with `as`: parse bodies, env, JSON and YAML with Zod, narrow caught errors with `instanceof` or `in`, and keep `as const` for literals. Put the session user on an augmented `Express.Request` and read it through one `requireUser(req)` that throws a 401.
- Await every promise. Start up with top-level `await` in `main.ts`. Signal and event callbacks stay synchronous and end the chain with `.then(onDone, onError)`; don't pass an async function where a callback returns void. Tests run on Vitest and `await` every `expect(...).resolves` or `.rejects`; `node:test`'s `test()` returns a promise the lint flags.
