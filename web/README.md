# `web/` — Postgres Control Tower UI

Vite + React 18 + TypeScript + TailwindCSS + TanStack Query + Recharts.
Dark mode is the default. shadcn-style primitives are hand-rolled in
`src/components/ui/` (we don't run the shadcn CLI; the surface is small).

## Prerequisites

- Node.js 20+
- A running manager on `http://localhost:8080` (or set
  `PCT_MANAGER_URL` before `npm run dev`).

## Dev

```bash
npm install
npm run dev
```

The Vite dev server listens on **5173** and proxies `/api` to the manager
URL above so the browser sees a same-origin app and there are no CORS
preflights to debug.

## Build

```bash
npm run build
```

Outputs static files to `web/dist/`. Point the manager at that directory
via `PCT_WEB_DIST_DIR=/abs/path/to/web/dist` and it will serve the SPA at
`/`, with history-mode fallback to `index.html`.

## Layout

```
src/
  api/          Typed fetch client + TanStack Query key factories
  auth/         Auth context — token in localStorage, auto-logout on 401
  charts/       Recharts wrappers (WAL sparkline, retention "safety window")
  components/   Layout + reusable UI primitives (Button, Card, Badge, ...)
  hooks/        useQuery wrappers — pages never call useQuery inline
  pages/        Login, Dashboard, Cluster, Logs (with RCA hints)
  rca/          Hardcoded root-cause-analysis rules (see PLAN §6)
```

## Conventions

See `.cursor/rules/20-frontend-react.mdc` — the short version: no Next.js,
no extra chart libraries, no global state libs, no inline `fetch`, all
times in **UTC** in the UI because the agent already normalizes at ingest.
