---
paths:
  - "**/app/**/*.ts"
  - "**/app/**/*.tsx"
  - "**/next.config.*"
  - "**/proxy.ts"
  - "**/middleware.ts"
  - "**/supabase/**"
---
# Next.js (App Router) / Supabase

- Server Components by default; `'use client'` only on interactive leaves.
- Modules that touch secrets, the database or service keys start with `import 'server-only'`.
- Server Actions and route handlers are public endpoints: validate input with Zod, re-check auth and ownership, return minimal data. Never mutate during render.
- Pass minimal DTOs to Client Components. Treat `params` and `searchParams` as untrusted input.
- `NEXT_PUBLIC_` variables are public and frozen at build time, so only non-secrets go there.
- Pair `'use cache'` with `cacheLife`. Never put per-user data or responses that set auth cookies in a shared cache.
- Server code verifies users with `getClaims()`, never `getSession()`. Only `proxy.ts` refreshes tokens.
- Row Level Security is on for every exposed table. Policies target `authenticated`, use `(select auth.uid())`, index the policy columns, and never trust `user_metadata`.
