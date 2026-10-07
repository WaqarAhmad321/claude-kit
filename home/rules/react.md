---
paths:
  - "**/*.tsx"
  - "**/*.jsx"
  - "src/**/*.ts"
  - "client/src/**/*.ts"
---
# React / TypeScript

- tsconfig has `strict`, `noUncheckedIndexedAccess`, `noUnusedLocals` and `noUnusedParameters`. No `any`, `@ts-ignore` or non-null `!`: use `unknown` and narrow.
- Parse every boundary with Zod: API responses, env, URL params, storage. Never cast network data with `as`.
- Data flows component → hook → `features/<feature>/api` (fetcher plus `queryOptions`) → one shared axios instance. Components never import axios or build URLs.
- Features don't import from each other. Shared code lives in shared folders; dependencies flow shared → features → app.
- Server state lives only in TanStack Query. Query keys come from a per-feature key factory and include every input of the query function. Hooks are thin wrappers around exported `queryOptions`.
- No data fetching or derived state in `useEffect`. Compute during render, respond to events in handlers, reset state with a `key`.
- Every data view handles data, error, pending and empty states explicitly, and keeps showing stale data while refetching.
- API errors are typed from problem+json in one axios interceptor; a global query error handler shows toasts; error boundaries catch render failures.
- Forms use react-hook-form with `zodResolver` and complete `defaultValues`. Server field errors map to inputs with `setError`.
- A JSX handler can't return a promise. Submit handlers call `mutate`, not `mutateAsync`, and are wired as `onSubmit={(event) => void handleSubmit(submit)(event)}`; `void` only a promise that can't reject. `onSuccess` returns `invalidateQueries(...)` so the mutation waits for it.
- Render stays pure, props and state stay immutable, hooks stay at the top level. No redundant or prop-mirrored state.
- List keys are stable IDs, never array indexes.
- Every input has a label; errors are text linked with `aria-describedby`; interactive elements are `<button>` or `<a>`; dialogs have a title, trap focus, close on Esc and return focus.
- Use the design tokens and existing UI primitives (shadcn). Don't hardcode palette colours or re-implement primitives.
- Web auth tokens live in HttpOnly Secure cookies, never localStorage. Render user content as text and sanitize before any raw-HTML sink.
