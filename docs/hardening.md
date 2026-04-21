# Hardening (v2 path)

This page is **explanatory, not how-to**. It catalogs the v2-grade
security and operational features intentionally **not** in v1, with
the design we expect to follow when we build them.

If you want to use one of these today, you can't — but the contract
is recorded here so the future implementer ships the right thing.

The complementary "what v1 will and will not do" surface is in
[`safety-and-rbac.md`](safety-and-rbac.md).
The "why we deferred this" answers are in
[`conflicts-resolved.md`](conflicts-resolved.md) and
[`PLAN.md` §10](../PLAN.md#10-out-of-scope-for-v1-recorded-for-future-work).

## What "v2" means here

v1 is a useful, low-blast-radius prototype for a fleet of 10–20
clusters operated by a small trusted team behind a TLS-terminating
reverse proxy.

v2 is the same product hardened for:

- **Less-trusted networks** (BYO-CA mTLS instead of bearer tokens).
- **Bigger / multi-team operators** (real RBAC, audit log).
- **Destructive operations** (restore, stanza-delete, config push).
- **Compliance audits** (who did what, when, with what approval).

Nothing in this doc changes the v1 user surface; it lives until the
day we open a "v2-foundations" phase in `PLAN.md`.

## 1. Mutual TLS for agent ↔ manager

**Today (v1):** HTTPS at the edge + per-agent bearer token, hashed at
rest. See [`architecture.md`](architecture.md#authentication--transport).

**v2 design:**

- Provision a small, internal CA (`step-ca`, `cfssl`, Vault PKI —
  any of them).
- The manager presents a server cert from this CA.
- Each agent gets a client cert at registration time (the
  `enrollment_token` flow stays — it's how the agent proves it's
  allowed to ask for a cert in the first place).
- The manager validates the client cert chain on every request and
  matches the SAN against `pct.agents.hostname`.
- The bearer token stays as a second factor (defense in depth — a
  stolen private key alone isn't enough; you also need the per-agent
  bearer).

What the implementer must own beyond "make TLS work":

- **Renewal.** A cron / systemd timer on each agent that re-issues
  before expiry. Track the cert NotAfter in `pct.agents` so the UI
  can warn 14 days before expiry.
- **Revocation.** A "revoke agent" admin action that flips a server
  side flag *and* publishes a CRL the manager checks on every
  request. (Just CRL — no OCSP, the fleet is small enough that a
  10s-cached CRL works.)
- **Bootstrap.** The very first agent on a fresh manager still uses
  the enrollment token over plain TLS to bootstrap; document that as
  the only allowed exception.

## 2. RBAC beyond viewer/admin

**Today (v1):** two roles, `viewer` and `admin`. See
[`safety-and-rbac.md`](safety-and-rbac.md#rbac-model).

**v2 roles**, in least-to-most-privilege order:

| Role               | Can read | Can submit backup | Can ack alerts | Can submit restore | Can approve restore | Can manage users |
| ------------------ | -------- | ----------------- | -------------- | ------------------ | ------------------- | ---------------- |
| `viewer`           | ✅       | ❌                | ❌             | ❌                 | ❌                  | ❌               |
| `oncall`           | ✅       | ✅                | ✅             | ❌                 | ❌                  | ❌               |
| `restore-operator` | ✅       | ✅                | ✅             | ✅ (queues)        | ❌                  | ❌               |
| `restore-approver` | ✅       | ✅                | ✅             | ❌                 | ✅                  | ❌               |
| `admin`            | ✅       | ✅                | ✅             | ✅                 | ✅                  | ✅               |

Implementation notes:

- Add a `pct.user_roles(user_id, cluster_id, role)` table for
  **per-cluster** RBAC. `cluster_id NULL` = fleet-wide.
- The `require_admin` dep splits into `require_role("oncall")`,
  `require_role("restore-operator", cluster_id=...)`, etc.
- A restore job moves through `pending_approval → pending → running`.
  An approver from the same cluster (and **different user**) must
  flip it to `pending` before any agent will see it via
  `/agents/jobs/next`.

## 3. Audit log

**Today (v1):** access logs from uvicorn + `requested_by` /
`acknowledged_by` columns on `pct.jobs` / `pct.alerts`. That is not
an audit log.

**v2 audit table:**

```sql
CREATE TABLE pct.audit_events (
    id          bigserial PRIMARY KEY,
    ts_utc      timestamptz NOT NULL DEFAULT now(),
    actor_id    int REFERENCES pct.users(id) ON DELETE SET NULL,
    actor_email text NOT NULL,                       -- denormalized: survives user delete
    action      text NOT NULL,                       -- e.g. 'job.create', 'alert.ack', 'agent.revoke'
    target_kind text,                                -- 'job' | 'agent' | 'cluster' | 'user'
    target_id   text,                                -- string for portability
    request_ip  inet,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb   -- before/after delta where relevant
);
CREATE INDEX ix_audit_actor_ts ON pct.audit_events(actor_id, ts_utc);
CREATE INDEX ix_audit_action_ts ON pct.audit_events(action, ts_utc);
```

What writes a row:

- Every `POST` / `DELETE` to `/api/v1/*` performed by a UI user.
- Every admin role grant / revoke.
- Every restore approval.
- Every agent revocation.

What does NOT write a row:

- Agent ingest (heartbeat, log batches, snapshots) — too noisy and
  not security-relevant. Use access logs.

The audit log is **append-only at the application layer** (no
update/delete endpoint) and ships off-host via the standard
`pg_dump` schedule. A second copy to an immutable bucket (S3 with
Object Lock or equivalent) covers the "the manager DB was tampered
with" scenario.

## 4. Secret management

**Today (v1):** `PCT_JWT_SECRET`, `PCT_ENROLLMENT_TOKEN`, SMTP
credentials, and the bootstrap admin password all come from
environment variables (likely a `.env` file).

**v2 path:**

- Pull all of the above from a real secret store (Vault, AWS Secrets
  Manager, GCP Secret Manager, sealed-secrets in k8s).
- Add a tiny wrapper at startup that resolves the env var
  `PCT_JWT_SECRET=secret://pct/jwt-secret` to the actual value.
- **Rotate without downtime.** Specifically:
  - `PCT_JWT_SECRET` → support **two** active secrets so old
    sessions remain valid for a grace window after rotation.
  - `PCT_ENROLLMENT_TOKEN` → make it a list; old + new both
    accepted during rollout.
- Stop logging the bootstrap admin password ever. Today it's only in
  startup logs in an obvious "rotate me" mode; v2 should require an
  out-of-band reset link instead.

## 5. Destructive ops UX

The full plan for restore / stanza-delete / config push is in
[`safety-and-rbac.md`](safety-and-rbac.md#the-v2-confirmation-modal-preview).
The hardening-relevant points:

- **Two-person rule.** Approver must be a different admin from the
  requester (RBAC + audit table enforce; UI displays the requirement).
- **Cooldown.** No new restore for the same cluster within 1 hour
  of a successful one. Prevents click-spam mid-incident.
- **PITR target rendering** in UTC, with the local TZ offset shown
  alongside in dim text. Never pick a target in local time without
  showing the UTC equivalent.

## 6. Manager DB co-tenancy

v1 keeps metadata + logs in the same Postgres for operational
simplicity (one backup, one migration tool). At fleet sizes where
log volume dominates the metadata, split:

- A **metadata** Postgres (`pct.*` only, small, easy to back up).
- A **logs** Postgres (`logs.*` only, larger, sharded by month if
  needed).

The split is one config change in the manager (separate SQLAlchemy
URL for `routes/logs.py` and the partition jobs) plus a one-time
data migration. Documented as v2 to avoid tempting v1 operators to
do it prematurely.

## 7. Replace Postgres logs with Loki / ClickHouse

If `logs.events` is regularly above ~few k events/sec sustained,
revisit:

- **Loki** if you want chronological tail-search and don't need
  joins. The Logs page would lose its per-cluster JOIN behavior and
  fall back to label filtering.
- **ClickHouse** if you want to keep SQL. Schema maps cleanly:
  `(ts_utc, agent_id, source, severity, raw, parsed JSON)`.
  Subsecond queries over months of data.

Either way, the **manager API surface stays the same** — only
`routes/logs.py` swaps its backing store. Agents do not change.

## 8. Notifier expansion

`alerter/notifiers.py` already exposes a `Notifier` base class. v2
notifier targets:

- **PagerDuty** (Events v2 API).
- **Microsoft Teams** (Office connector or Workflow webhook).
- **Opsgenie** (Alert API).

Each gets:

- A new file in `manager/pct_manager/alerter/`.
- A `PCT_PAGERDUTY_*` / `PCT_TEAMS_*` config block.
- A row in [`troubleshooting.md`](troubleshooting.md) for "notifier X
  silent — how to test".

The dispatcher already loops over all configured notifiers; nothing
in the rules engine changes.

## 9. Eat your own dog food

Once mTLS is in place, run `pct-agent` on the manager Postgres host
itself and back it up with pgBackRest. The manager UI then displays
its own meta-cluster, and storage runway / WAL lag alerts apply
recursively.

This requires v2 mTLS so a compromised agent on the manager host
doesn't escalate to compromising the manager API surface.

## What we will deliberately not add

A few feature requests that have come up multiple times and that we
are choosing to *not* do, even in v2, unless the use case becomes
overwhelming:

- **Agent → agent communication.** Stays out. The hub-and-spoke
  model is the security boundary.
- **Custom plugin scripts on agents.** Stays out. The allowlist of
  CLI invocations is a feature, not a limitation.
- **Per-user theme / preference storage server-side.** UI
  preferences live in the browser.
- **A query language** beyond the existing simple filters on
  `/logs/events`. If we need more, swap the backing store
  (item 7), don't reinvent SQL.

## Related

- [`safety-and-rbac.md`](safety-and-rbac.md) — v1 RBAC + the v2
  confirmation-modal contract.
- [`conflicts-resolved.md`](conflicts-resolved.md) — why specific v1
  decisions look the way they do.
- [`PLAN.md` §10](../PLAN.md#10-out-of-scope-for-v1-recorded-for-future-work) — the canonical v2 wishlist.
