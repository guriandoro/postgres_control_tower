# Postgres Control Tower — Documentation

This directory is the working documentation set for **Postgres Control
Tower** (PCT).
The authoritative product plan is [`PLAN.md`](../PLAN.md) at the repo root;
these documents elaborate on it and stay current with the code in `manager/`,
`agent/`, and `web/`.

## Audience

Everything here is written for two audiences in parallel:

- **DBAs / SREs** standing up a manager + agent fleet against an existing
  PostgreSQL estate.
- **Future contributors** trying to understand a design decision or extend
  a subsystem.

If a section is interesting to only one of those audiences, it is marked
with a "**For operators:**" or "**For contributors:**" prefix.

## Reading order

If you've never seen this project before, read in this order:

1. [`architecture.md`](architecture.md) — what the pieces are and how they
   talk to each other. Start here.
2. [`conflicts-resolved.md`](conflicts-resolved.md) — the "why no Loki?" /
   "why no Celery?" decisions baked into the architecture. Helpful before
   you propose changing anything large.
3. [`agent-setup.md`](agent-setup.md) — how to get a single agent reporting
   to the manager.
4. [`log-sources.md`](log-sources.md) — the five log sources, file
   locations, and how to add a new one.
5. [`safety-and-rbac.md`](safety-and-rbac.md) — what the v1 manager will
   and will not do (especially around backups + restore).
6. [`api.md`](api.md) — the HTTP surface, with examples.
7. [`deployment.md`](deployment.md) — running it for real (TLS,
   sizing, retention, manager-DB backups).
8. [`hardening.md`](hardening.md) — the v2 path: mTLS, RBAC roles,
   audit logging, secret management.
9. [`troubleshooting.md`](troubleshooting.md) — common operational
   failures and how to recognize them.

## Index

| Doc                                             | Purpose                                           |
| ----------------------------------------------- | ------------------------------------------------- |
| [`architecture.md`](architecture.md)            | Components, data flow, API surface, schema.       |
| [`deployment.md`](deployment.md)                | Production-ish deploy + sizing + DB backup.       |
| [`agent-setup.md`](agent-setup.md)              | Install, register, configure log sources.         |
| [`log-sources.md`](log-sources.md)              | Per-source paths/units, parser format examples.   |
| [`safety-and-rbac.md`](safety-and-rbac.md)      | v1 capability matrix; v2 confirmation modal.      |
| [`api.md`](api.md)                              | HTTP API reference with curl examples.            |
| [`hardening.md`](hardening.md)                  | v2 path — mTLS, RBAC, audit, secrets.             |
| [`troubleshooting.md`](troubleshooting.md)      | Clock skew, perms, agent token rotation, etc.     |
| [`conflicts-resolved.md`](conflicts-resolved.md)| Decisions vs. the original two source specs.      |
| [`_archive/`](_archive/)                        | Original source specs, kept verbatim for history. |

## Conventions

These docs follow the
[`30-docs.mdc`](../.cursor/rules/30-docs.mdc) cursor rule:

- One sentence per line in long-form prose so diffs stay readable.
- Inline file paths, env vars, and identifiers wrapped in backticks
  (`PCT_DATABASE_URL`, `pct.jobs`, `manager/pct_manager/main.py`).
- Mermaid diagrams are inline; nothing depends on external rendering.
- "v2" / "future work" features are clearly flagged so they aren't
  mistaken for things you can use today.
- When echoing a locked decision, we cite the
  [`PLAN.md`](../PLAN.md) section so future contributors can find the
  source of truth.
