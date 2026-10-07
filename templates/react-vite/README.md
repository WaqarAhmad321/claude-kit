# React + Vite pack

A React single-page app built with Vite and pnpm, talking to a separate API. The pack holds the quality gates and an example Playwright suite with one signed-in state per role; the app's own code comes from the setup steps below.

## Files
| File | What it does | What it assumes |
|---|---|---|
| `kit.json` | Journeys gate: `pnpm exec playwright test`, stale when the source, e2e tests, Vite or Playwright config or dependencies change. Review always for auth, permissions, payments, billing, security and the shared API client | The backend's test stack is up, and `.env` holds the `E2E_*` logins |
| `.oxlintrc.json` | Type-aware oxlint with the react (including the hooks rules), typescript, import, jsx-a11y, unicorn and oxc plugins. `max-lines` 250, `max-lines-per-function` 60 (off in tests), `complexity` 10, `no-console`, unused disable directives reported, and no `axios` import in `.tsx` files, `components/` or `hooks/`. `axios.create` is allowed and CSS side-effect imports pass | `oxlint` and `oxlint-tsgolint` installed |
| `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json` | Vite's split: `src/` with `vite/client` types; the Vite and Playwright configs and `e2e/` with Node types. Strict, with `noUncheckedIndexedAccess`, unused locals and parameters, fallthrough and override checks | TypeScript 6 (create-vite's) or later |
| `knip.json` | Unused files, exports and dependencies; the Playwright setup file is an entry | knip 6 |
| `.jscpd.json` | Duplicated code over 1% fails | jscpd 5 |
| `.prettierrc.json`, `.prettierignore` | Prettier at 100 columns with Tailwind class sorting; Markdown is left to markdownlint | Tailwind 4 with `src/index.css` importing it |
| `playwright.config.ts`, `e2e/*` | Starts Vite on 4173, signs each role in once through the login form (`auth.setup.ts`) and saves its storage state to `playwright/.auth/<role>.json`; `notes.spec.ts` shows a signed-out case and per-role cases | A `/login` page with Email and Password labels and a "Sign in" button; logins in `E2E_*` variables |
| `.lefthook/react-vite.yml` | Pre-commit: oxlint and Prettier on staged files, skipped until `node_modules` exists | The common `lefthook.yml` extends it |
| `.github/workflows/ci.yml` | oxlint, `tsc -b`, Prettier, knip, jscpd, unit tests and the build; the e2e job when the repository variable `E2E_API_URL` is set, with the logins as secrets; Betterleaks over the history | Actions pinned by SHA; a `test` script |
| `gitignore.additions`, `.env.example` | Build output and Playwright's auth state and reports stay out of git; the variables the app and the e2e suite read | |
| `CLAUDE.md.template` | The stack section of CLAUDE.md: layout, data flow, commands | |

## Setup for a new project
1. Scaffold with `pnpm create vite@9.2.1 <scratch dir> --template react-ts` and copy it in without overwriting the pack's files.
2. Add the dependencies:
   ```sh
   pnpm add @tanstack/react-query axios react-hook-form @hookform/resolvers zod
   pnpm add -D oxlint oxlint-tsgolint prettier prettier-plugin-tailwindcss tailwindcss @tailwindcss/vite knip jscpd \
     vitest @playwright/test @types/node
   pnpm exec playwright install chromium
   ```
3. Scripts in `package.json`: `typecheck` (`tsc -b`), `lint` (`oxlint`), `format` (`prettier --check .`), `test` (`vitest run --passWithNoTests`), `e2e` (`playwright test`).
4. Add Tailwind (`@tailwindcss/vite` in `vite.config.ts`, `@import "tailwindcss";` in `src/index.css`) and shadcn.
5. Replace the example specs with the app's own flows, in the order a user goes through them, one `describe` per role.
6. `lefthook install`, then record the tsc baseline with the kit's `kit_baseline.py`.

## Checks
`pnpm exec oxlint` (prints nothing when it passes), `pnpm exec tsc -b`, `pnpm exec prettier --check .`, `pnpm exec knip`, `pnpm exec jscpd .`, `pnpm test`, `pnpm exec vite build`, and `pnpm exec playwright test` against the backend's test stack.

## How it was checked
On a scratch Vite app with TypeScript 6.0.2, Vite 8.3.2, React 19.3, oxlint 1.86.0 with oxlint-tsgolint 7.0.2003, Prettier 3.9.9 with prettier-plugin-tailwindcss 0.8.1 and Tailwind 4.3.3, knip 6.39.0, jscpd 5.4.0 and Playwright 1.63.0: `tsc -b` passed both projects; oxlint reported an axios import in a component but not in a feature's api module, a console call, an index key, an unused disable directive, a 284-line file, a 65-line function, complexity 13 and a floating promise; Prettier sorted Tailwind classes; knip and jscpd passed; Playwright listed the setup and per-role tests, and stopped with a clear message when a login variable was empty. actionlint 1.7.12 passed the workflow.

Not checked: the e2e suite against a running app and API, the workflow on GitHub, and TypeScript 7.
