# Safety and RBAC

This page is the contract for **what v1 will and will not do** with
your PostgreSQL clusters.
The short version: PCT in v1 only runs **non-destructive** pgBackRest
operations.
Restore, `stanza-delete`, and config push are blocked **at the API
layer and at the agent layer** (defense in depth) and are explicitly
deferred to v2.

If you've ever had a "click the wrong cluster, restore over prod"
incident, this is the page that says "we already thought about it".

## v1 capability matrix

| Operation                       | UI button? | API route                       | Allowed in v1 | Notes                                                                  |
| ------------------------------- | ---------- | ------------------------------- | ------------- | ---------------------------------------------------------------------- |
| `pgbackrest backup --type=full` | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "backup_full"`. Admin-gated.                                    |
| `pgbackrest backup --type=diff` | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "backup_diff"`. Admin-gated.                                    |
| `pgbackrest backup --type=incr` | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "backup_incr"`. Admin-gated.                                    |
| `pgbackrest check`              | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "check"`. Admin-gated. Read-only against the repo.              |
| `pgbackrest stanza-create`      | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "stanza_create"`. Admin-gated. Idempotent on existing stanzas.  |
| `pt-stalk` PostgreSQL collect   | Yes        | `POST /api/v1/jobs`             | ✅            | `kind: "pt_stalk_collect"`. Admin-gated. Read-only diagnostic snapshot — only runs `SELECT`s and OS samplers. |
| Recurring backup schedule       | Yes        | `POST /api/v1/schedules`        | ✅            | Cron-driven; only the three `backup_*` kinds. Admin-gated.             |
| `pgbackrest restore`            | **No**     | n/a                             | ❌ blocked    | Type literal rejects the value; agent runner allowlist also rejects.   |
| `pgbackrest stanza-delete`      | **No**     | n/a                             | ❌ blocked    | Same — both layers refuse.                                             |
| Config push (`pgbackrest.conf`) | **No**     | n/a                             | ❌ blocked    | No code path exists. Documented as v2 in [`hardening.md`](hardening.md).|
| `systemctl stop postgresql`     | **No**     | n/a                             | ❌ blocked    | Agent never invokes systemctl. Even restore (when added) will require an explicit "stop PG first" toggle. |
| Acknowledge an alert            | Yes        | `POST /api/v1/alerts/{id}/ack`  | ✅            | Admin-gated. Acks silence notifications, do not resolve the alert.     |
| Read everything                 | Yes        | `GET /api/v1/...`               | ✅            | `viewer` role.                                                         |

The full job-kind allowlist lives in two places by design:

- [`manager/pct_manager/schemas.py`](../manager/pct_manager/schemas.py) — `JOB_KINDS`
- [`agent/pct_agent/runner.py`](../agent/pct_agent/runner.py) — `ALLOWED_KINDS`

Both must be edited together.
A future restore-enabling change touches **both files plus this doc plus
`PLAN.md`**.

## RBAC model

There are exactly two roles in v1, mapped to the `pct.users.role`
column.

### `viewer`

Can do everything that doesn't change cluster state:

- See the Dashboard, Cluster pages, Logs, Jobs, Alerts.
- Read pgBackRest snapshots, WAL health, role transitions.
- Read storage runway forecasts.

Cannot:

- Submit a job.
- Acknowledge an alert.

### `admin`

Everything `viewer` can do, plus:

- `POST /api/v1/jobs` — submit a backup / check / stanza-create.
- `POST /api/v1/schedules`, `PATCH/DELETE /api/v1/schedules/{id}` —
  manage recurring backup schedules. Listing them is `viewer`-only.
- `POST /api/v1/alerts/{id}/ack` — silence an open alert.

Admin-gating is implemented by the
[`require_admin`](../manager/pct_manager/security.py) FastAPI
dependency.
The UI also hides admin-only buttons for `viewer` users, but the
server is the source of truth.

There is no group concept, no per-cluster RBAC, no "approver"
role. Those land in [`hardening.md`](hardening.md) under v2.

## Why no restore in v1?

A restore that picks the wrong cluster is the kind of mistake that
ends careers.
For v1 we wanted to ship a useful, low-blast-radius product that
doesn't carry that tail risk.

Concretely, doing restore *correctly* requires:

1. A **typed-confirmation modal** ("Type the cluster name to
   confirm"). The original Nexus spec already required this; we
   chose to defer it together with the operation it protects.
2. A second admin to **approve** the destructive job (a real RBAC
   role beyond `admin`).
3. A clear **"PG is stopped now, you cannot read from it"** state
   on the cluster page so an oblivious viewer doesn't think the
   database is down.
4. A **PITR slider** that turns a calendar pick into a
   `--type=time --target=...` flag *with timezone discipline* (see
   [`troubleshooting.md`](troubleshooting.md) on UTC).
5. A **rollback story** if the restore corrupts the data dir.

None of those exist in v1. Doing 1 of 5 is worse than doing 0 of 5.

## The v2 confirmation modal (preview)

When destructive ops land, the UI gate will look like this:

```text
+------------------------------------------------------------+
| Restore cluster: order-db-prod                             |
|                                                            |
| You are about to restore from backup taken at:             |
|     2026-04-21T03:00:00Z (UTC)                             |
|                                                            |
| The PostgreSQL service will be stopped first and the data  |
| directory will be overwritten. This is destructive.        |
|                                                            |
| Type the cluster name to confirm:                          |
| [_____________________]                                    |
|                                                            |
| Approver (admin email): [____________________________]     |
|                                                            |
| [ Cancel ]                              [ Restore ]        |
+------------------------------------------------------------+
```

Locked invariants this modal must enforce:

- The typed string is matched **case-sensitive**.
- The button stays disabled until the typed string equals the
  cluster name **and** an approver email is filled.
- The approver email must belong to a **different** admin user.
- The chosen restore target is rendered in **UTC** with the local
  TZ offset shown alongside, never local-only. This avoids the
  classic "I picked 03:00 my time but the cluster runs in UTC"
  outage.
- The action is recorded in the (v2) audit log with both
  requester and approver IDs.

None of this exists yet. It is documented here so the future
implementer cannot legitimately ship the modal without these
properties.

## Defense in depth — what protects what

```mermaid
flowchart LR
    UI[Web UI] -->|Hides admin-only buttons for viewers| API
    API[Manager API] -->|require_admin dep<br/>Pydantic Literal[JobKind]| DB[(pct.jobs)]
    DB -->|Job claim| Agent
    Agent[pct-agent runner] -->|ALLOWED_KINDS frozenset<br/>refuses unknown kinds| pgBR[pgbackrest]
```

If any single layer is bypassed (a hand-rolled curl, a buggy
manager build, an attacker with a stolen agent token), the next
layer still refuses. Specifically:

- A user with a `viewer` JWT calling `POST /api/v1/jobs` directly
  gets `403`. The `require_admin` dep doesn't care about UI state.
- A `JobCreateRequest` with `kind: "restore"` fails Pydantic
  validation with `422`; the literal rejects it.
- If a hostile manager somehow inserts a `restore` row into
  `pct.jobs`, the agent runner sees `kind not in ALLOWED_KINDS`
  and reports `exit_code=126` without ever touching pgBackRest.

## pt-stalk diagnostics — why it's safe

`pt_stalk_collect` is the only non-pgBackRest kind in the v1 allowlist.
It exists to capture a point-in-time diagnostic bundle (queries via
`pg_gather`'s `gather.sql`, plus OS samplers like `vmstat`/`iostat`/`ps`)
the operator can later attach to a support ticket.

It is allowed because:

- **Read-only against the DB.** `pg_gather`'s queries only `SELECT` from
  catalogs and stats views. The agent runs pt-stalk in `--no-stalk
  --collect` mode — no monitoring loop, no triggers, just one snapshot.
- **No write to the cluster filesystem.** Output lands in the agent's
  own `/var/lib/pct-agent/pt-stalk/` (created by the entrypoint), never
  in `PGDATA` or the pgBackRest repo.
- **Bounded runtime.** Hard-capped server-side at
  `PCT_AGENT_PT_STALK_MAX_RUNTIME_SECONDS` (default 30 minutes); the
  per-job `run_time_seconds` is further capped at 1..3600.
- **Bounded blast radius for the upload.** The artifact endpoint
  enforces `PCT_MAX_ARTIFACT_BYTES` (default 200 MiB) and only stores
  files under `<artifacts_dir>/<job_id>/`. Filenames are restricted to
  `[A-Za-z0-9._-]{1,200}` so a malicious agent can't write outside its
  own directory.
- **Same `require_admin` gate** as every other job-submission route.

If you ever need to disable it, drop `"pt_stalk_collect"` from
`JOB_KINDS` in `manager/pct_manager/schemas.py` and `ALLOWED_KINDS` in
`agent/pct_agent/runner.py`. Both layers refuse, in line with the rest
of the matrix above.

## Recurring backup schedules

Backup schedules (`pct.backup_schedules`) are admin-gated cron
expressions, evaluated in UTC. The manager's APScheduler tick fires
due rows by inserting a `pct.jobs` entry exactly like the UI does, so
the same allowlist and routing apply. Two extra invariants:

- The schedule allowlist is **narrower** than the job allowlist —
  only `backup_full | backup_diff | backup_incr` (see
  `BACKUP_SCHEDULE_KINDS` in `manager/pct_manager/schemas.py`).
  `check` and `stanza_create` stay one-off because there's no good
  reason to repeat them on a calendar.
- Disabling and re-enabling a schedule recomputes `next_run_at` from
  "now" so a paused schedule cannot fire its missed runs in a burst
  the moment it's re-enabled.

`requested_by` on scheduler-issued jobs is `null`; the audit trail
points back via `pct.backup_schedules.last_job_id` and
`schedule.created_by`.

## Auditing

v1 has no audit log table. It has:

- `pct.jobs.requested_by` — admin user ID for every operator-submitted
  job. `null` for jobs created by the schedule tick (look at
  `pct.backup_schedules.created_by` instead).
- `pct.backup_schedules.created_by` — admin user ID per schedule.
- `pct.alerts.acknowledged_by` — admin user ID for every ack.
- The manager logs every `require_admin`-gated request via
  uvicorn access logs.

A real, queryable audit table is a v2 deliverable; see
[`hardening.md`](hardening.md).
