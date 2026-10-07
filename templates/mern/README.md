# MERN pack

Express 5 API and React + Vite client in one repo, as two pnpm packages: `server/` and `client/`, each with its own lockfile. The pack holds the quality gates, the test stack and example HTTP tests; the app's own code comes from the setup steps below.

## Files
| File | What it does | What it assumes |
|---|---|---|
| `kit.json` | Journeys gate: `docker compose -f compose.test.yml run --build --rm journeys`, stale when the server, migrations, tests or stack change; review gate: `server/migrations/**` and auth, permission, payments, billing and security files always get a review | Docker; nothing else on the host |
| `CLAUDE.md.template` | The stack section of CLAUDE.md: layers, layout, data flow, auth, and the run and check commands | |
| `.lefthook/mern.yml` | Pre-commit: oxlint and Prettier on staged server and client files, each skipped until its package's `node_modules` exists | The common `lefthook.yml` extends it |
| `.github/workflows/ci.yml` | Per package: install, oxlint, `tsc --noEmit`, Prettier, `pnpm test`; knip and jscpd for `client` only. Betterleaks over the full history. The journeys command, with logs on failure | Actions pinned by SHA; Betterleaks by version and checksum |
| `compose.test.yml` | MongoDB 8.0 as a single-node replica set, Mailpit, `migrate` (`migrate-mongo up`), `seed-admin`, the API, two test services under the `tests` profile: `role-matrix`, then `journeys` (Hurl), and Schemathesis per role under the `fuzz` profile | See [Assumptions](#assumptions) |
| `journeys/*.hurl`, `journeys/vars.env` | Example journeys for a notes API: the happy path in the client's call order, object-level access (404 for another user's note), and the error paths (400, 401, 403, 404, 405, 409, never a 5xx) | `base_url` is `http://localhost:3000` |
| `tests/matrix/roles.yaml` | Example role matrix: the expected status for every operation and role, with a fresh fixture per cell | The throwaway admin login matches `seed-admin` in `compose.test.yml` |
| `tests/schemathesis/*.toml` | Schemathesis configs, one per role: anonymous, and the admin through the session cookie | The generated schema declares `sessionAuth` as an apiKey-in-cookie scheme |
| `server/test/role-matrix.ts` | Runs a matrix file against a base URL: `node test/role-matrix.ts <roles.yaml> <base URL>`. Fails on any other status or a 5xx | Node 24 or later (runs the `.ts` file directly), `yaml` and `zod` installed |
| `server/Dockerfile`, `server/.dockerignore` | Builds with pnpm, runs `node dist/main.js` as `node` on `node:24.21.0-alpine`. The `role-matrix` target adds the runner | `pnpm build` writes `dist/`; `migrate-mongo-config.js` and `migrations/` exist |
| `server/tsconfig.json`, `server/tsconfig.build.json` | The first type-checks `src/` and `test/` without emitting; the second builds `src/` without tests into `dist/` | ESM (`"type": "module"`), `nodenext` imports with `.js` extensions |
| `server/.oxlintrc.json` | Type-aware oxlint with the layering rules: only `src/models`, `src/repositories` and `src/db` import Mongoose; services import neither Mongoose nor Express; only `src/config.ts` and `migrate-mongo-config.js` read `process.env`; `_id` is allowed | `oxlint` and `oxlint-tsgolint` installed |
| `server/migrate-mongo-config.js`, `server/migrations/` | migrate-mongo in ESM mode, against `MONGODB_URI` | Migrations are `.js` files exporting `up` and `down` |
| `server/pnpm-workspace.yaml` | Tells pnpm 11 not to run argon2's fallback build, which its prebuilt binaries make unnecessary | argon2 for password hashing |
| `client/.oxlintrc.json` | Type-aware oxlint with React, jsx-a11y and the TanStack Query plugin | `oxlint`, `oxlint-tsgolint`, `@tanstack/eslint-plugin-query` installed |
| `client/tsconfig.json` | One strict config for `src/` and `vite.config.ts`. It accepts `.tsx` import paths, which the Vite scaffold's `main.tsx` uses | Vite with `@vitejs/plugin-react` |
| `client/knip.json`, `client/.jscpd.json` | Unused files, exports and dependencies; duplicated code over 1% fails | knip 6, jscpd 5 |
| `.prettierrc.json`, `*/.prettierignore`, `*/.gitignore` | Prettier at 100 columns; pnpm's own files and build output stay out of it and of git | |

### Assumptions
- pnpm 11 (`"packageManager": "pnpm@11.28.2"`). pnpm 12 fails inside Claude Code's sandbox: it locks its store under `/tmp/pnpm-store-operation-locks-<uid>` and ignores `TMPDIR`.
- Node 24 LTS in the image and in CI. The Dockerfile enables pnpm through corepack, which Node 25 and later no longer bundle; on Node 26, install corepack or pnpm with npm first.
- The API listens on port 3000 under `/api`, with `GET /api/health`, `GET /api/openapi.json`, `POST /api/auth/signup` (`email`, `password`, `display_name`, answering 201) and `POST /api/auth/login` (answering 204 with an HttpOnly `session` cookie). The generated OpenAPI document declares `sessionAuth` as an `apiKey`-in-`cookie` security scheme named `session`.
- The API reads `NODE_ENV`, `PORT`, `MONGODB_URI`, `CORS_ORIGINS`, `SESSION_SECRET`, `SMTP_URL` and `AUTH_RATE_LIMIT` (logins and signups per 15 minutes per address; the test stack raises it because the tests sign up many accounts from one address). Paid services' keys are set empty.
- `dist/cli/create-admin.js` creates or promotes the admin from `MONGODB_URI`, `ADMIN_EMAIL` and `ADMIN_PASSWORD`.
- The Vite dev server runs on 5173 and proxies `/api` to 3000.

## Setup for a new project
Run these after the bootstrap lays the pack down.

1. `server/package.json`: `"type": "module"`, `"packageManager": "pnpm@11.28.2"`, `"engines": {"node": ">=24"}`, and the scripts `build` (`tsc -p tsconfig.build.json`), `start` (`node dist/main.js`), `typecheck` (`tsc --noEmit`), `lint` (`oxlint`), `test` (`vitest run`), `format` (`prettier --check .`), `migrate` (`migrate-mongo up`). Then, in `server/`:
   ```sh
   pnpm add express mongoose zod helmet cors express-rate-limit cookie pino pino-http argon2 migrate-mongo \
     @asteasolutions/zod-to-openapi
   pnpm add -D typescript @types/node@24 @types/express @types/cors oxlint oxlint-tsgolint prettier vitest yaml
   ```
2. Scaffold the client with `pnpm create vite <scratch dir> --template react-ts` and copy it into `client/` without overwriting the pack's files (the scaffold brings its own `.oxlintrc.json`; keep the pack's). Replace its tsconfig files with the pack's `client/tsconfig.json`, change `build` to `tsc --noEmit && vite build` (the scaffold's `tsc -b` leaves a `tsconfig.tsbuildinfo` behind), add `server: { proxy: { "/api": "http://localhost:3000" } }` to `vite.config.ts`, set the same `packageManager`, and add the `typecheck`, `lint`, `test` and `format` scripts. Then, in `client/`:
   ```sh
   pnpm add @tanstack/react-query axios react-hook-form @hookform/resolvers zod
   pnpm add -D oxlint oxlint-tsgolint @tanstack/eslint-plugin-query prettier vitest knip jscpd
   ```
3. Write the server's `src/config.ts`, `src/main.ts`, `src/cli/create-admin.ts`, a first migration for the indexes, and `GET /api/openapi.json` (built with `@asteasolutions/zod-to-openapi` from the route Zod schemas, declaring `sessionAuth` as its `apiKey`-in-`cookie` scheme), following `~/.claude/rules/node-express.md` and `mongodb.md`.
4. Replace the example journeys, matrix and Schemathesis roles with the app's own, in the client's call order.
5. `lefthook install`.

## Checks
- In each package: `pnpm exec oxlint`, `pnpm exec tsc --noEmit`, `pnpm exec prettier --check .`, `pnpm test`; in `client/` also `pnpm exec vite build`, `pnpm exec knip` and `pnpm exec jscpd .`. oxlint prints nothing when it passes, so read its exit code.
- HTTP tests: `docker compose -f compose.test.yml run --build --rm journeys`. It builds the images, starts the stack, runs the role matrix, then the journeys, and fails if either fails. When the matrix fails, compose only names it: read `docker compose -f compose.test.yml logs role-matrix`. Stop the stack with `docker compose -f compose.test.yml down -v`.
- Schemathesis: `docker compose -f compose.test.yml run --rm schemathesis-admin`. Record accepted failures once with `--baseline`; the list only shrinks.
- Workflow: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12` (the image includes shellcheck).

The tests run in the API container's network instead of from the host for three reasons. Claude Code's sandbox runs any command with `&&` or a pipe inside the sandbox, where the Docker socket is closed and localhost is reachable only through its proxy, so only a single `docker ...` command can start the stack and test it. The host needs no Hurl or Node. And the API is `localhost` there too, so curl sends the Secure session cookie back over plain HTTP.

## Patterns the type-aware lint expects
`no-unsafe-type-assertion`, `no-floating-promises` and `no-misused-promises` are on in both packages. The rules in `~/.claude/rules/` say this in one line each; `server/test/role-matrix.ts` shows the first two.
- Parse, don't cast: `schema.parse(req.body)`, `configSchema.safeParse(process.env)`, `schema.parse(parseYaml(text))`. Narrow a caught `unknown` with `instanceof` or `in` checks. `as const` is fine.
- Mongoose types queries and `.lean()` from the schema, so a cast on a result is flagged as unnecessary or unsafe. When a projection drops fields, type the query with its generic: `.select("role").lean<Pick<User, "role"> & { _id: Types.ObjectId }>()`, with `type User = InferSchemaType<typeof userSchema>`.
- Tests run on Vitest and await each async assertion: `await expect(verifyPassword(hash, "wrong")).resolves.toBe(false)`. `node:test`'s `test()` returns a promise, so every call trips `no-floating-promises`.
- Controllers are plain functions or arrow-function properties. A method passed to the router trips `unbound-method`.
- `import { create } from "axios"`: `axios.create(...)` trips `import/no-named-as-default-member`.
- Startup awaits at the top level of `main.ts`. A signal handler stays synchronous and handles its promise: `process.once("SIGTERM", () => { shutdown().then(() => process.exit(0), (error: unknown) => { logger.error({ err: error }); process.exit(1); }); });`
- Express 5 handlers may be `async`: rejections reach the error middleware, and the types allow it.
- In JSX, a handler can't return a promise: `onSubmit={(event) => void handleSubmit(submit)(event)}`, where `submit` calls `mutate`. `onSuccess: () => queryClient.invalidateQueries(...)` returns the promise, so the mutation waits for it.

## How it was checked
A throwaway app under `$TMPDIR` was built from this pack by the setup steps above: a notes API (Express 5.2.1, Mongoose 9.10.3, Zod 4.6.5, argon2id sessions in an HttpOnly cookie, roles `user` and `admin`, every query scoped to the owner) and a React 19.3 + Vite 8.3 client (TanStack Query 5.104, react-hook-form 7.89, axios 1.20). Tooling: TypeScript 7.0.2 in the server and 6.0.3 in the client (create-vite 9.2.1 pins 6.0, inside the TanStack lint plugin's peer range), oxlint 1.86.0 with oxlint-tsgolint 7.0.2003, Vitest 5.0.3, pnpm 11.28.2, Node 24.21.0 in the image, MongoDB 8.0.32, Hurl 8.0.1, lefthook 2.1.15, Betterleaks 1.9.0, actionlint 1.7.12, Docker Compose 5.5.1. Every version and action SHA was checked against npm, Docker Hub and the GitHub releases.

Passed:
- oxlint, `tsc --noEmit` and Prettier in both packages; unit tests (9 server, 4 client); the client's production build.
- `docker compose -f compose.test.yml up --build --wait`: the replica set came up primary, `migrate` and `seed-admin` exited 0, and the API turned healthy.
- Hurl from the host: 35 requests in 3 files. Then the journeys command: 28 role-matrix cells and the same 35 requests in the Hurl image. No 5xx among the 132 requests the API logged, and cookies are redacted in its logs.
- lefthook: one commit passed every job that ran, and `lefthook run commit-msg` rejected a non-conventional subject.
- The workflow passes actionlint 1.7.12; each action SHA is its release tag's commit, and the Betterleaks checksum matches its release.
- On SIGTERM the API logs the shutdown and exits 0. `down -v` removed the stack.
- Lint probes: without the `_id` allowance, `doc._id` fails `no-underscore-dangle`; `JSON.parse(text) as T` fails `no-unsafe-type-assertion`; an unawaited `expect(...).rejects` and a `node:test` `test()` call fail `no-floating-promises`.

Not checked:
- The workflow never ran on GitHub.
- The kit's own journeys gate with this command in a real session: scratch projects are exempt from the gate.
- lefthook's `.env*` and secret blocks: the kit's own guards stopped those test commands before lefthook ran. lefthook rejected both separately.
- Transactions (the replica set was up and primary, but nothing used one), a migration's `down`, sending mail, the 429 path, the client in a browser, Docker Desktop on macOS, and pnpm 12 inside the sandbox.
- Schemathesis and the client's knip/jscpd against a real app: the pack ships no application source, only the configs and the compose services. `docker compose config` and actionlint 1.7.12 validated their syntax; running them against a live server and a real client isn't covered here.
