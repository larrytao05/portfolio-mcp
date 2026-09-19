# Portfolio Dashboard frontend

This directory is a React application written in TypeScript. It is the browser
interface for saved portfolio records: account holdings, activity, quotes,
portfolio overview data, and the guarded fake-order workflow.

If you are new to TypeScript, begin with `src/main.tsx`, then `src/App.tsx`,
then `src/api/client.ts`. That order follows the path a request takes through
the UI.

## Run it

Start the API from the repository root in one terminal:

```sh
uv run uvicorn api_main:app --reload
```

Then start the frontend here in another terminal:

```sh
npm install
npm run dev
```

Vite prints a local browser URL. Requests beginning with `/api` are proxied to
`http://127.0.0.1:8000` by `vite.config.ts`, so frontend code can use relative
paths such as `/api/accounts`.

## File map

- `src/main.tsx` — starts React and installs shared providers.
- `src/App.tsx` — page composition, UI components, queries, mutations, and
  user interactions.
- `src/api/client.ts` — TypeScript definitions for API responses and functions
  that make HTTP requests.
- `src/styles.css` — all application styling, layout, and responsive rules.
- `src/App.test.tsx` — component tests with Vitest and Testing Library.
- `vite.config.ts` — Vite, React, test, and local API-proxy configuration.
- `tsconfig*.json` — TypeScript compiler configuration.
- `eslint.config.js` — JavaScript, TypeScript, and React lint rules.

## The browser data flow

```text
main.tsx
  -> QueryClientProvider
  -> App.tsx
  -> api/client.ts
  -> fetch('/api/...')
  -> FastAPI backend
```

`main.tsx` calls React's `createRoot` and renders `<App />`. It wraps that app
in TanStack React Query's `QueryClientProvider`. This provider gives components
a shared request cache.

`App.tsx` asks for data with `useQuery`. A query has useful states such as
`isPending`, `isError`, and `data`, so the UI can render loading, failed, empty,
and successful states honestly.

```tsx
const accounts = useQuery({
  queryKey: ["accounts"],
  queryFn: getAccounts,
});
```

The `queryKey` names the cached resource. The `queryFn` is the function that
actually fetches it. After a write-like action such as a portfolio refresh,
`useMutation` runs the action and invalidates affected query keys. React Query
then refetches current data.

## TypeScript essentials used here

TypeScript is JavaScript with compile-time descriptions of values. The types do
not run in the browser; they help the editor and compiler catch mistakes before
the app is built.

```ts
type Account = {
  id: string;
  label: string;
  currency: string;
};
```

This says that an `Account` must have those fields and that each is a string.
If code writes `account.curreny`, TypeScript can flag it before runtime.

Common syntax in this codebase:

- `string | null` means a value is either text or intentionally unavailable.
  Financial fields use this so unknown data is never silently shown as zero.
- `field?: string` means the field may be absent.
- `Thing[]` means an array of `Thing` values.
- `Promise<Thing>` means an asynchronous operation that eventually produces a
  `Thing`, such as a network request.
- `.tsx` files can contain JSX markup like `<section>...</section>`; `.ts`
  files cannot.

Component props are also typed. This component says it needs a title and a
complete `AllocationGroup`:

```tsx
function AllocationCard({ title, group }: {
  title: string;
  group: AllocationGroup;
}) {
  return <h3>{title}</h3>;
}
```

## The API boundary

Keep network details in `src/api/client.ts`, not inside visual components. A
new backend-backed UI feature normally follows this sequence:

1. Add the FastAPI endpoint and test it on the backend.
2. Add a response type to `api/client.ts`.
3. Add a fetch function such as `getOverview()`.
4. Call it from `App.tsx` through `useQuery` or `useMutation`.
5. Render loading, error, empty, and ready states.
6. Add a component test.
7. Add or adjust CSS.

Types describe what the frontend expects, but they do not validate JSON at
runtime. Backend tests and a stable API contract still matter.

The overview UI intentionally renders server-provided totals, allocations, and
coverage metadata. It should not independently aggregate holdings or reconcile
financial percentages in React.

## Styling and accessibility

`src/styles.css` owns the visual system. Components use semantic HTML such as
headings, tables, labels, buttons, and `aria-*` attributes; CSS then controls
layout and the narrow-screen experience. When changing a section, check both
desktop and narrow layouts, keyboard focus order, and non-color status cues.

## Tests and checks

```sh
npm run typecheck  # TypeScript compiler checks without emitting files
npm test           # Vitest component tests
npm run lint       # ESLint rules for TS and React
npm run build      # Typecheck plus production Vite build
```

Run all four before handing off a frontend change.
