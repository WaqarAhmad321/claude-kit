# Next.js + Supabase pack

A Next.js App Router app on Supabase (Postgres, auth, storage), with pnpm. The pack holds the quality gates, the Supabase checks and an example Playwright suite with one signed-in state per role; the app's own code comes from the setup steps below.

## Files
| File | What it does | What it assumes |
|---|---|---|
| `kit.json` | Journeys gate: `pnpm exec playwright test`, stale when the source, e2e tests, migrations, seed, Next or Playwright config or dependencies change. Review always for auth, permissions, payments, billing, security, migrations, `src/proxy.ts`, the Supabase clients, route handlers and Server Actions | Local Supabase running, and `.env` holding its URL and key and the `E2E_*` logins |
| `.oxlintrc.json` | The React pack's rules plus the nextjs plugin: type-aware, `max-lines` 250, `max-lines-per-function` 60 (off in tests), `complexity` 10, `no-console`, unused disable directives reported. `createClient` from `@supabase/supabase-js` is blocked outside `src/lib/supabase/`; type imports pass. `server-only`, `client-only` and CSS side-effect imports pass | `oxlint` and `oxlint-tsgolint` installed |
| `tsconfig.json` | create-next-app's config with `@/*` pointing at `src/`, plus `noUncheckedIndexedAccess`, unused locals and parameters, fallthrough and override checks | TypeScript 5.9 (create-next-app's) |
| `knip.json`, `.jscpd.json` | Unused files, exports and dependencies; duplicated code over 1% fails | knip 6, jscpd 5 |
| `.prettierrc.json`, `.prettierignore` | Prettier at 100 columns with Tailwind class sorting; Markdown is left to markdownlint | Tailwind 4 imported in `src/app/globals.css` |
| `playwright.config.ts`, `e2e/*` | Starts `next dev` on 3100, signs each role in through the login form and saves its storage state; `notes.spec.ts` shows a signed-out case and per-role cases | A `/login` page with Email and Password labels and a "Sign in" button; logins created by `supabase/seed.sql` |
| `.lefthook/nextjs.yml` | Pre-commit: oxlint and Prettier on staged files, skipped until `node_modules` exists | The common `lefthook.yml` extends it |
| `.github/workflows/ci.yml` | oxlint, `tsc --noEmit`, Prettier, knip, jscpd, unit tests and `next build`; `supabase db lint` and the security advisors on a local database with the migrations applied; the e2e job against local Supabase when the repository variable `E2E_ENABLED` is `true`; Betterleaks over the history | Actions pinned by SHA; the Supabase CLI by version; a `test` script |
| `gitignore.additions`, `.env.example` | Build output, Playwright state and Supabase's local folders stay out of git; the variables the app and the e2e suite read | |
| `CLAUDE.md.template` | The stack section of CLAUDE.md: rendering, the Supabase clients, auth, RLS and the commands | |

## Supabase notes
- **Clients.** `src/lib/supabase/server.ts` builds a client with `createServerClient` from `@supabase/ssr` and the request's cookies, in a module that starts with `import "server-only"`. `client.ts` uses `createBrowserClient`. Nothing else creates clients, so every query runs as the signed-in user and RLS applies.
- **Auth.** Server code checks the user with `supabase.auth.getClaims()`, which verifies the JWT's signature. Never trust `getSession()` on the server: it reads the cookie without verifying it. Only `src/proxy.ts` refreshes the session. Never read roles from `user_metadata`, which the user can change; keep them in a table or in `app_metadata`.
- **Row Level Security.** Every table in an exposed schema has RLS on and explicit policies. Policies target `authenticated`, call `(select auth.uid())` so it runs once per query, and the columns they filter on are indexed. A table without a policy returns nothing, which is the safe failure.
- **Keys.** `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` is public by design. `SUPABASE_SECRET_KEY` bypasses RLS, so it's read only in server-only modules for admin tasks, and never gets the `NEXT_PUBLIC_` prefix.
- **Migrations.** Schema changes are SQL files from `supabase migration new <name>` or `supabase db diff`, applied locally with `supabase db reset`. The dashboard is never the source of truth.
- **Advisors in CI.** `supabase db advisors --local --type security --fail-on warn` runs Supabase's security lints (RLS disabled on an exposed table, policies that reference `user_metadata`, security-definer views, mutable function search paths) against the migrated local database. Run `--type performance` too, for unindexed foreign keys and per-row `auth.uid()` calls.

## Setup for a new project
1. Scaffold: `pnpm create next-app@16.3.8 <scratch dir> --ts --app --src-dir --tailwind --no-linter --import-alias "@/*" --use-pnpm --disable-git --no-agents-md`, then copy it in without overwriting the pack's files.
2. Add the dependencies:
   ```sh
   pnpm add @supabase/ssr @supabase/supabase-js zod server-only
   pnpm add -D supabase oxlint oxlint-tsgolint prettier prettier-plugin-tailwindcss knip jscpd vitest @playwright/test
   pnpm exec playwright install chromium
   ```
3. Scripts in `package.json`: `typecheck` (`tsc --noEmit`), `lint` (`oxlint`), `format` (`prettier --check .`), `test` (`vitest run --passWithNoTests`), `e2e` (`playwright test`).
4. `pnpm exec supabase init`, then write `src/lib/supabase/{server,client,proxy}.ts` and `src/proxy.ts` from Supabase's Next.js guide, and a `supabase/seed.sql` that creates the e2e logins.
5. Replace the example specs with the app's own flows, one `describe` per role.
6. `lefthook install`, then record the tsc baseline with the kit's `kit_baseline.py`.

## How it was checked
On a scratch Next 16.3.8 app with TypeScript 5.9.3, `@supabase/ssr` 0.12.7, `@supabase/supabase-js` 2.117.2, oxlint 1.86.0 with oxlint-tsgolint 7.0.2003, knip 6.39.0 and Playwright 1.63.0: `tsc --noEmit` passed a layout, a page calling `getClaims()` through a server client, and the e2e files; oxlint blocked `createClient` from `@supabase/supabase-js` in a component while passing the type import; knip passed; Playwright listed the setup and per-role tests. Supabase CLI 2.119.0 lists `db lint` and `db advisors` with the flags the workflow uses. actionlint 1.7.12 passed the workflow.

Not checked: `next build`, the e2e suite against a running app, the database job and the env mapping from `supabase status -o env` (no local Supabase stack ran), and the workflow on GitHub.
