# Postgres Control Tower

Centralized control plane for fleets of PostgreSQL clusters managed with
[pgBackRest](https://pgbackrest.org/), with a unified multi-source log
collector (PostgreSQL, pgBackRest, Patroni, etcd, OS/journald) layered on top.

> **Status:** early prototype. See [PLAN.md](PLAN.md) for the unified
> implementation plan and current phase. Original specs are archived in
> [docs/_archive/](docs/_archive/).

## Repo layout

```
manager/   FastAPI manager app (API, scheduler, ingest, alerter)
agent/     Lightweight Python agent that runs on each DB host
web/       React UI (added in P5)
docs/      Documentation (added in P8)
deploy/    Dockerfiles + docker-compose demo (added in P9)
PLAN.md    Authoritative implementation plan
```

## Try it (Docker Compose demo)

The fastest way to see PCT end-to-end. Requires Docker Desktop or
Docker Engine + Compose v2. First build is ~5 minutes.

```bash
./deploy/scripts/bootstrap.sh
open http://localhost:8080      # admin password printed at the end
```

This brings up the manager, a control-plane Postgres, one **standalone**
PG cluster, one two-node **Patroni** cluster (with etcd), and a `pct-agent`
sidecar against each data-plane Postgres. It also queues a first
`stanza_create` + `backup_full` so the UI has data on first load.

Drive failure scenarios (WAL archiving break, Patroni failover, backup
failure, clock drift, blocked `restore` attempt) with:

```bash
./deploy/scripts/demo-failures.sh wal_lag
```

Stop the stack with `./deploy/scripts/teardown.sh` (keeps data) or wipe
with `./deploy/scripts/reset.sh`. See [deploy/README.md](deploy/README.md)
for the full topology, layout, and `docker compose` knobs.

## Quick start (development, no Docker)

Requires Python 3.12+ and a reachable PostgreSQL 16 instance.

```bash
# 1. Manager
cd manager
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp ../.env.example ../.env   # edit values, especially PCT_DATABASE_URL
alembic upgrade head
uvicorn pct_manager.main:app --reload --port 8080

# 2. Agent (separate shell)
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e .
pct-agent --help
```

## Documentation

Production deployment, agent setup, log sources, the safety contract,
the API reference, hardening notes, and a symptom-first troubleshooting
guide all live under [docs/](docs/) — start at [docs/README.md](docs/README.md).
