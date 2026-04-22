# API reference

This page is a hand-written companion to the FastAPI-generated
OpenAPI document at:

- **Manager runtime:** `GET /openapi.json` and the interactive UI at
  `GET /docs` (Swagger) / `GET /redoc`.

The schemas are the source of truth and live in
[`manager/pct_manager/schemas.py`](../manager/pct_manager/schemas.py).
What follows is the practical operator-facing tour with curl examples.

All routes are prefixed with `/api/v1`.

## Conventions

- Times are **ISO-8601 UTC** in both directions.
  Strings ending in `Z` or with an explicit `+00:00` offset are accepted;
  no timezone abbreviation games.
- Authorization is `Authorization: Bearer <token>`.
  Two distinct token types — see [Auth](#auth).
- Pagination is **simple `limit` + `since`**, not cursors.
  v1 fleet sizes don't need cursors.
- Errors follow FastAPI's standard:
  `{"detail": "..."}` for 4xx, `{"detail": [...validation errors...]}` for 422.

## Quick map

| Group       | Route                                              | Method | Auth                |
| ----------- | -------------------------------------------------- | ------ | ------------------- |
| Auth        | `/auth/login`                                      | POST   | public              |
| Auth        | `/auth/me`                                         | GET    | UI JWT              |
| Clusters    | `/clusters`                                        | GET    | UI JWT (viewer)     |
| Clusters    | `/clusters/{cluster_id}`                           | GET    | UI JWT (viewer)     |
| Clusters    | `/clusters/{cluster_id}/storage_forecast`          | GET    | UI JWT (viewer)     |
| Clusters    | `/clusters/{cluster_id}/wal_health`                | GET    | UI JWT (viewer)     |
| Logs read   | `/logs/events`                                     | GET    | UI JWT (viewer)     |
| Logs read   | `/logs/events/{event_id}`                          | GET    | UI JWT (viewer)     |
| Logs read   | `/logs/role_transitions`                           | GET    | UI JWT (viewer)     |
| Jobs read   | `/jobs`, `/jobs/{job_id}`                          | GET    | UI JWT (viewer)     |
| Jobs write  | `/jobs`                                            | POST   | UI JWT (admin)      |
| Alerts      | `/alerts`, `/alerts/summary`                       | GET    | UI JWT (viewer)     |
| Alerts      | `/alerts/{alert_id}/ack`                           | POST   | UI JWT (admin)      |
| Agent in    | `/agents/register`                                 | POST   | enrollment token    |
| Agent in    | `/agents/heartbeat`                                | POST   | agent bearer        |
| Agent in    | `/agents/pgbackrest_info`                          | POST   | agent bearer        |
| Agent in    | `/agents/wal_health`                               | POST   | agent bearer        |
| Agent in    | `/agents/patroni_state`                            | POST   | agent bearer        |
| Agent jobs  | `/agents/jobs/next`                                | GET    | agent bearer        |
| Agent jobs  | `/agents/jobs/{job_id}/result`                     | POST   | agent bearer        |
| Logs ingest | `/logs/ingest`                                     | POST   | agent bearer        |
| Health      | `/healthz`                                         | GET    | public              |

## Auth

Two distinct credential surfaces, intentionally separate. See
[`architecture.md`](architecture.md#authentication--transport) for the
threat model.

### UI JWT (`viewer` / `admin`)

```bash
curl -fsS -X POST https://pct.internal/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"admin"}'
```

```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer"
}
```

Use the token everywhere else:

```bash
TOKEN="eyJhbGci..."
curl -fsS https://pct.internal/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

### Agent bearer (per-agent)

Issued exactly once at registration. Stored hashed server-side.
Re-running `pct-agent register` rotates it.

```bash
curl -fsS -X POST https://pct.internal/api/v1/agents/register \
  -H 'Content-Type: application/json' \
  -d '{
    "enrollment_token": "change-me-enrollment-token",
    "cluster_name": "order-db-prod",
    "cluster_kind": "standalone",
    "hostname": "db01.prod"
  }'
```

```json
{ "agent_id": 7, "agent_token": "rb_...", "cluster_id": 3 }
```

The `agent_token` is the bearer for every subsequent agent → manager
call.

## Clusters

### List clusters

`GET /api/v1/clusters?limit=100`

Returns a **summary** view (cluster row + counts), not full
per-agent state.

```json
[
  {
    "id": 3,
    "name": "order-db-prod",
    "kind": "standalone",
    "created_at": "2026-04-01T00:00:00+00:00",
    "agent_count": 1,
    "last_seen_at": "2026-04-21T14:51:00+00:00"
  }
]
```

### Cluster detail

`GET /api/v1/clusters/{cluster_id}`

Returns the cluster + every agent in it, each with its **latest**
`wal_health` and `pgbackrest_info` payload embedded.

```json
{
  "id": 3,
  "name": "order-db-prod",
  "kind": "standalone",
  "created_at": "2026-04-01T00:00:00+00:00",
  "agents": [
    {
      "id": 7,
      "cluster_id": 3,
      "hostname": "db01.prod",
      "role": "primary",
      "last_seen_at": "2026-04-21T14:51:00+00:00",
      "version": "0.1.0",
      "clock_skew_ms": 14,
      "created_at": "2026-04-01T00:05:00+00:00",
      "latest_wal_health": {
        "captured_at": "2026-04-21T14:50:30+00:00",
        "last_archived_wal": "0000000100000000000000A1",
        "archive_lag_seconds": 7,
        "gap_detected": false
      },
      "latest_pgbackrest_info": {
        "captured_at": "2026-04-21T14:50:00+00:00",
        "payload": [{ "name": "main", "backup": [/* ... */] }]
      },
      "latest_patroni_state": null
    }
  ]
}
```

For Patroni clusters, each agent additionally embeds a
`latest_patroni_state` block populated by the agent's
[Patroni REST collector](#agent-ingest-patroni-state):

```json
{
  "captured_at": "2026-04-22T16:51:00+00:00",
  "member_name": "patroni-1",
  "patroni_role": "replica",
  "state": "streaming",
  "timeline": 2,
  "lag_bytes": 0,
  "leader_member": "patroni-2",
  "members": [
    { "name": "patroni-1", "role": "replica", "state": "streaming",
      "host": "patroni-1", "port": 5432, "timeline": 2, "lag": 0 },
    { "name": "patroni-2", "role": "leader", "state": "running",
      "host": "patroni-2", "port": 5432, "timeline": 2 }
  ]
}
```

`patroni_role` is one of `leader` | `replica` | `sync_standby` |
`standby_leader` | `unknown` — richer than the cluster-wide
`agents.role` (which stays `primary | replica | unknown`). The manager
collapses Patroni roles down to `agents.role` on every ingest:
`leader` and `standby_leader` map to `primary`; `replica` and
`sync_standby` map to `replica`.

### Storage runway forecast

`GET /api/v1/clusters/{cluster_id}/storage_forecast`

Returns the latest forecast row for the cluster, or `null` if not
computed yet.

```json
{
  "cluster_id": 3,
  "captured_at": "2026-04-21T14:30:00+00:00",
  "sample_count": 168,
  "daily_growth_bytes": 524288000,
  "current_bytes": 805306368000,
  "target_bytes": 1099511627776,
  "days_to_target": 56.1
}
```

`days_to_target` is `null` when growth is non-positive or no
`PCT_FORECAST_TARGET_BYTES` is configured.

### WAL archive lag history

`GET /api/v1/clusters/{cluster_id}/wal_health`

Per-agent timeseries of WAL archival samples for the cluster's
sparkline. Each agent in the cluster contributes its own series
(empty `samples` when nothing was captured in the window — keeps the
chart legend stable across renders).

Query parameters:

- `since_minutes` (default `60`, max `1440`) — look-back window.
- `max_per_agent` (default `300`, max `2000`) — hard cap on samples
  per agent. Collector ticks every 30s, so the default keeps roughly
  2.5h of resolution if the look-back is widened.

```json
{
  "cluster_id": 2,
  "since_minutes": 60,
  "series": [
    {
      "agent_id": 2,
      "hostname": "patroni-1",
      "role": "primary",
      "samples": [
        {
          "captured_at": "2026-04-21T22:00:00+00:00",
          "last_archived_wal": "0000000100000000000000A1",
          "archive_lag_seconds": 4,
          "gap_detected": false
        }
      ]
    }
  ]
}
```

## Agent ingest: Patroni state

`POST /api/v1/agents/patroni_state`

Posted by `collectors/patroni.py` after polling the local node's
Patroni REST endpoint (`GET /cluster`). Shipped only when
`PCT_AGENT_PATRONI_REST_URL` is set on the agent.

```json
{
  "captured_at": "2026-04-22T16:51:00+00:00",
  "member_name": "patroni-1",
  "patroni_role": "replica",
  "state": "streaming",
  "timeline": 2,
  "lag_bytes": 0,
  "leader_member": "patroni-2",
  "members": [
    { "name": "patroni-1", "role": "replica", "state": "streaming",
      "host": "patroni-1", "port": 5432, "timeline": 2, "lag": 0 },
    { "name": "patroni-2", "role": "leader", "state": "running",
      "host": "patroni-2", "port": 5432, "timeline": 2 }
  ]
}
```

Side effects on the manager:

- A row is appended to `pct.patroni_state` (the latest row per agent
  drives the cluster dashboard's Patroni panel).
- `pct.agents.role` is updated to the collapsed
  `primary | replica | unknown` mapping (see [Cluster detail](#cluster-detail)
  for the table).

This makes Patroni the **stronger** signal for `agents.role`: it
overrides whatever `pg_is_in_recovery()` most recently set via the WAL
collector. That matters during partitions where a former leader still
answers "false" to `pg_is_in_recovery()` even though Patroni has
already elected someone else.

## Logs

### Ingest (agent → manager)

`POST /api/v1/logs/ingest`

Body: a `LogBatchIngest` (max 5000 records / call).

```json
{
  "records": [
    {
      "ts_utc": "2026-04-21T12:34:56.123+00:00",
      "source": "patroni",
      "severity": "info",
      "raw": "2026-04-21 12:34:56,123 INFO: promoted self to leader by acquiring session lock",
      "parsed": {
        "message": "promoted self to leader by acquiring session lock",
        "level": "INFO",
        "role_transition": { "from": "replica", "to": "primary" }
      }
    }
  ]
}
```

```json
{ "ok": true, "accepted": 1, "role_transitions": 1 }
```

Reserved keys in `parsed` (see `LogRecordIn` docstring):

- `role_transition` — promoted to `logs.role_transitions`.
- `message` — the human-readable bit, used by free-text search.

### Query events

`GET /api/v1/logs/events`

Filters (all optional):

| Param         | Type                 | Notes                                   |
| ------------- | -------------------- | --------------------------------------- |
| `cluster_id`  | int                  | Restrict to agents in cluster.          |
| `agent_id`    | int                  | Restrict to a single agent.             |
| `source`      | comma-separated list | e.g. `postgres,patroni`.                 |
| `severity`    | comma-separated list | e.g. `error,critical`.                   |
| `since`       | ISO-8601 UTC         | Inclusive lower bound on `ts_utc`.       |
| `until`       | ISO-8601 UTC         | Exclusive upper bound on `ts_utc`.       |
| `q`           | string               | Free-text on `parsed->>'message'`.       |
| `limit`       | int (default 200)    | Max page size, capped server-side.       |

```bash
curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  'https://pct.internal/api/v1/logs/events?cluster_id=3&severity=error,critical&since=2026-04-21T00:00:00Z&limit=50'
```

### Single event

`GET /api/v1/logs/events/{event_id}` — returns the full
`LogEventOut` (including `raw` and `parsed`).

### Role transitions

`GET /api/v1/logs/role_transitions?cluster_id=3&since=2026-04-14T00:00:00Z`

Powers the Cluster page's leader Gantt. Same `cluster_id` /
`agent_id` / `since` filters as `events`.

## Jobs (Safe Ops)

See [`safety-and-rbac.md`](safety-and-rbac.md) for what is and isn't
allowed.

### Submit a job (admin)

`POST /api/v1/jobs`

```json
{
  "kind": "backup_full",
  "cluster_id": 3,
  "params": { "stanza": "main" }
}
```

- Provide either `agent_id` or `cluster_id`. Both is fine; mismatch is
  a 400.
- `kind` must be one of `backup_full`, `backup_diff`, `backup_incr`,
  `check`, `stanza_create`. Anything else is `422`. **`restore` and
  `stanza_delete` are blocked here _and_ in the agent runner.**
- `params.stanza` overrides the agent's default
  `PCT_AGENT_PGBACKREST_STANZA`.
- `params.extra_args` (list of strings) is appended verbatim after
  the kind-derived flags. Use sparingly.

Response (`201 Created`): a full `JobOut`.

### List / detail

`GET /api/v1/jobs` accepts `cluster_id`, `agent_id`,
`status` (`pending`/`running`/`succeeded`/`failed`), `since`, `limit`.

`GET /api/v1/jobs/{job_id}` returns the row, including the
`stdout_tail` (last ~16KB) once the job has finished.

The full pgBackRest stream is in `logs.events` (the pgBackRest log
tailer captures it independently of the job runner).

### Agent: claim next (long-poll)

`GET /api/v1/agents/jobs/next?wait=25`

- The manager picks the oldest `pending` job for the calling agent
  using `SELECT ... FOR UPDATE SKIP LOCKED` and atomically flips it
  to `running`.
- `200 OK` body: `JobClaim` `{ "id": 42, "kind": "backup_full",
  "params": {...} }`.
- `204 No Content` if there's nothing to do — the agent should
  long-poll again.
- The manager caps `wait` server-side; pass a generous client-side
  HTTP timeout (e.g. `wait + 10s`).

### Agent: report result

`POST /api/v1/agents/jobs/{job_id}/result`

```json
{
  "exit_code": 0,
  "stdout_tail": "...last ~16KB of merged stdout+stderr...",
  "succeeded": true
}
```

Response: `200 OK` with the updated `JobOut`.

`409 Conflict` means the manager doesn't think the job is still
`running` (e.g. it was reset out-of-band). The runner logs and moves
on; nothing to retry.

## Alerts

### List

`GET /api/v1/alerts`

Filters (all optional): `status` (`open` / `acknowledged` /
`resolved`), `kind`, `cluster_id`, `limit`.

```json
[
  {
    "id": 12,
    "kind": "wal_lag",
    "severity": "warning",
    "cluster_id": 3,
    "dedup_key": "agent:7",
    "opened_at": "2026-04-21T14:00:00+00:00",
    "resolved_at": null,
    "acknowledged_at": null,
    "acknowledged_by": null,
    "last_notified_at": "2026-04-21T14:00:05+00:00",
    "payload": { "archive_lag_seconds": 920, "agent_id": 7 }
  }
]
```

### Summary

`GET /api/v1/alerts/summary`

Returns `{ "open": N, "critical": N, "acknowledged": N }`.
The Dashboard's "Open alerts" card uses this.

### Acknowledge (admin)

`POST /api/v1/alerts/{alert_id}/ack`

Silences notifications for that alert until it is resolved-and-reopens
on a fresh occurrence.

```json
{ "id": 12, "acknowledged_at": "2026-04-21T14:30:00+00:00" }
```

Acking does **not** mark the alert resolved — see
[`architecture.md`](architecture.md#database-schema) for the
deduplication rules.

## Agent ingest (heartbeat / pgBR / WAL)

These three endpoints are the steady-state telemetry path. All
require the agent bearer token.

### Heartbeat

`POST /api/v1/agents/heartbeat`

```json
{
  "agent_time_utc": "2026-04-21T14:51:00.123+00:00",
  "version": "0.1.0",
  "role": "primary"
}
```

Manager subtracts `agent_time_utc` from its own `now()` to derive
`clock_skew_ms` (positive = agent is behind manager). Stored on
`pct.agents` and exposed in the cluster detail. Returned to the agent
so it can self-diagnose:

```json
{ "server_time_utc": "2026-04-21T14:51:00.137+00:00", "clock_skew_ms": 14 }
```

### pgBackRest snapshot

`POST /api/v1/agents/pgbackrest_info`

```json
{
  "captured_at": "2026-04-21T14:50:00+00:00",
  "payload": [/* verbatim output of: pgbackrest --output=json info */]
}
```

`payload` is stored as JSONB without inner-shape validation, so a
pgBackRest version bump cannot break ingest. The Cluster page's
retention timeline reads `payload[*].backup[*]`.

### WAL health probe

`POST /api/v1/agents/wal_health`

```json
{
  "captured_at": "2026-04-21T14:50:30+00:00",
  "last_archived_wal": "0000000100000000000000A1",
  "archive_lag_seconds": 7,
  "gap_detected": false,
  "role": "primary"
}
```

Inserts into `pct.wal_health`. The alerter uses
`archive_lag_seconds` for the WAL-lag rule (default thresholds: warning
at 60s, critical at 5 minutes — tuned for the demo so a broken
`archive_command` opens an alert in ~2 minutes; see
[`PLAN.md` §6](../PLAN.md#6-components--responsibilities) and
[`alerter/rules.py`](../manager/pct_manager/alerter/rules.py)).

## Health

`GET /healthz` returns `{ "ok": true }` and is unauthenticated. Use
it as your container / load-balancer health check.

## OpenAPI

The full machine-readable spec is at:

```bash
curl -fsS https://pct.internal/openapi.json | jq '.paths | keys'
```

Generated client libraries (Go / TypeScript / etc.) should be
produced from that file rather than from this doc, which is for
humans.

## Related

- [`architecture.md`](architecture.md) — request/response sequence
  diagrams.
- [`safety-and-rbac.md`](safety-and-rbac.md) — what `POST /jobs` will
  refuse.
- [`agent-setup.md`](agent-setup.md) — how to obtain an agent
  bearer.
- [`troubleshooting.md`](troubleshooting.md) — common 401/403 modes.
