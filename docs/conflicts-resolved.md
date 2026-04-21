# Conflicts Resolved

> Why this doc exists: a future contributor reading
> [`docs/_archive/pgbackrest-ui-plan.md`](_archive/pgbackrest-ui-plan.md)
> and [`docs/_archive/log-collector.md`](_archive/log-collector.md) will
> ask "why no Loki? where's Celery? what happened to mTLS?". This page
> records the locked answers from
> [`PLAN.md` §1](../PLAN.md#1-conflict-resolution-vs-original-specs) so
> we don't have to relitigate them every time.

The two source specs proposed two parallel agent stacks plus a separate
log pipeline. We collapsed them to favor **operational simplicity** and
**minimum moving parts**. The default mode of operation is "one Postgres,
one manager, one agent per host, no other infrastructure".

If you want to reopen one of these decisions, do it in `PLAN.md`
itself — don't fork this doc.

## Summary table

| Topic               | pgBackRest Nexus said | Log Collector said    | PCT v1 (locked)                                 |
| ------------------- | --------------------- | --------------------- | ----------------------------------------------- |
| Agent runtime       | Python FastAPI        | Vector.dev            | One Python FastAPI agent does **both** jobs     |
| Log aggregator      | (n/a)                 | Grafana Loki          | None — logs land in Postgres `logs.events`      |
| Task queue          | Celery + Redis        | (n/a)                 | None — `pct.jobs` table + agent long-poll        |
| Transport security  | mTLS + JWT            | (n/a)                 | HTTPS + per-agent bearer (hashed); mTLS = v2    |
| Backend language    | Python                | "Python or Go"        | Python only, end-to-end                         |
| Frontend            | React + Vite          | React (dark mode)     | React + Vite + Tailwind + hand-rolled shadcn    |
| Metadata DB         | "Central Postgres"    | (n/a)                 | Same Postgres as logs, separate schemas         |
| Restore / PITR UI   | Phase 4 deliverable   | (n/a)                 | **Blocked in v1** at API + agent layers         |
| RBAC                | Viewer / Admin        | (n/a)                 | Viewer / Admin (kept). Granular roles = v2      |
| "Type cluster name" | Required for restore  | (n/a)                 | Punted with restore itself; v2 path only        |

## The decisions, with rationale

### One agent runtime, not two

The Nexus spec wanted a Python FastAPI agent for backups; the Log
Collector spec wanted Vector.dev for logs. Running both on every DB
host means two service units, two configuration languages (Python
config vs Vector TOML), two failure modes, two upgrade cycles, and
two security postures.

**Resolution:** one `pct-agent` Python process per host that does both.
Tailers + parsers + shipper + job runner are a few hundred lines each
(see [`agent/pct_agent/`](../agent/pct_agent/)) and reusing the same
`httpx` client and bearer token is much simpler than bridging Vector
to a custom token-auth Sink.

We accept that this means we have a tighter ceiling on log throughput
than Vector. See **No Loki** below for the v2 escape hatch.

### No Loki / Vector — logs go to Postgres

The Log Collector spec proposed Vector → Loki. We dropped both.

**Resolution:** logs land in `logs.events`, partitioned monthly by
`ts_utc`, with a configurable retention (default 14 days, see
`PCT_LOG_RETENTION_DAYS`).
Indexes:

- B-tree on `(agent_id, ts_utc)` for the per-agent timeline.
- GIN on `parsed` for free-text search via `parsed->>'message'`.

Why:

- For a fleet of 10–20 clusters, log volume fits comfortably in
  Postgres for two weeks. We measured and it's not close.
- One DB to back up, one DB to harden, one query language for the UI.
- The Logs page already needs metadata-style joins (e.g. "show
  Patroni events from agents in cluster X"). Loki would force us to
  do those joins client-side.

**v2 path** (in [`hardening.md`](hardening.md)): if we cross
~few k events/sec sustained, we revisit Loki or ClickHouse, but only
then.

### No Celery, no Redis — `pct.jobs` + long-poll

The Nexus spec said Celery + Redis is "essential for handling
long-running restores across 10+ nodes". For v1 we don't have
restores, so the rationale evaporates.

**Resolution:**

- A `pct.jobs` table is the queue.
- Agents long-poll `GET /api/v1/agents/jobs/next` (~25s) and the
  manager claims atomically with
  `SELECT ... FOR UPDATE SKIP LOCKED`.
- APScheduler in the manager handles the periodic side
  (partitions, retention, alerts, forecast).

This is sufficient for the v1 fan-out (one job per agent at a time;
backups serialize naturally per host). When we add restore in v2 we
will revisit, not before.

### HTTPS + bearer, not mTLS, in v1

mTLS is a perfectly fine answer, but it requires a CA, an enrollment
process for the cert, agent cert renewal, manager cert pinning, and a
clear "what happens if the agent cert expires" story. None of that is
useful product surface in a v1 prototype.

**Resolution:**

- Bootstrap with a shared `PCT_ENROLLMENT_TOKEN`.
- `POST /api/v1/agents/register` issues a per-agent bearer token,
  stores only its hash, and returns the plaintext to the agent once.
- All subsequent calls use `Authorization: Bearer <token>`.
- Run behind TLS termination at the network edge.

Token rotation = re-register the agent. See
[`troubleshooting.md`](troubleshooting.md) for the runbook.

mTLS is documented as the v2 path in [`hardening.md`](hardening.md).

### Same Postgres for metadata + logs

Two schemas, one cluster:

- `pct.*` — clusters, agents, snapshots, jobs, alerts, forecast, users.
- `logs.*` — partitioned event stream and derived role transitions.

This means one connection pool, one `pg_dump` for backup, one
migration tool (`alembic`).
If logs grow to dominate the database, splitting `logs` to a separate
PG cluster is a one-config-line change (different SQLAlchemy URL for
the log routes) and does not require an architectural rewrite.

### React + Vite, served by FastAPI

We did not pick Next.js or anything that requires SSR / its own Node
runtime. The UI is fully client-side; production builds are static
files served by the manager's FastAPI process from
`PCT_WEB_DIST_DIR`. One container, one port, no proxy.

### shadcn-style primitives, hand-rolled (no `shadcn-ui` CLI)

The CLI scaffolds files into your project that depend on a specific
copy of Radix and a specific Tailwind config. We chose to write the
half-dozen primitives we need by hand (`Card`, `Dialog`, `Badge`, …)
to keep the dependency surface small. The look-and-feel is the same;
you just won't find a `components/ui/...` tree generated by the CLI.

### "Type cluster name" modal — deferred with restore

The Nexus spec required a typed-confirmation modal for any
**Restore** or **Delete**. Both of those operations are blocked in
v1, so the modal is deferred too.

When restore lands in v2, the modal lands with it. It is documented
preemptively in [`safety-and-rbac.md`](safety-and-rbac.md) so the
contract is clear.

### RBAC stays simple: viewer + admin

A real role tree (`backup-operator`, `restore-approver`, …) is only
useful once you have destructive ops to gate. We have backup, check,
and stanza-create; admin/viewer is enough.

The `require_admin` dependency lives in
[`manager/pct_manager/security.py`](../manager/pct_manager/security.py)
and is applied to:

- `POST /api/v1/jobs`
- `POST /api/v1/alerts/{id}/ack`

Everything else in the UI surface is `viewer`.

## What we kept verbatim from the source specs

Not everything was changed. The pieces below survived intact and the
implementation reflects the original wording:

- **The Pulse** — 5-minute auto-refresh — TanStack Query
  `staleTime: 5 * 60 * 1000` on the dashboard hooks.
- **The Surgeon** — multi-node UTC-synced log explorer — the
  `/logs` page.
- **Storage Runway** — linear regression on
  `pgbackrest_info.payload->repo->size` — `alerter/forecast.py`.
- **WAL archive lag > 60 s warn / > 5 min crit** as a default trigger —
  `alerter/rules.py::rule_wal_lag` (originally specced at 15 min;
  tightened so demo failure injection fires in ~2 minutes).
- **Role & Stability Analytics** — Patroni / etcd role transitions
  rendered as a Recharts Gantt — `pages/Cluster.tsx`.
- **Dark mode default** — Tailwind `class` strategy with `dark` on
  `<html>`.

## Where to argue with this doc

Don't argue with this doc directly. The decisions above are
*derived* from [`PLAN.md` §1](../PLAN.md#1-conflict-resolution-vs-original-specs).
If you want to change one, edit `PLAN.md` first and update this file
to match.
