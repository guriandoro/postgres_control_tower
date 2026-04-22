# How to install and register a `pct-agent`

This guide gets a single agent running on a PostgreSQL host and
reporting to an existing manager.
For multi-host conventions, log paths and adding a new source, see
[`log-sources.md`](log-sources.md).
For what the agent does once it's running, see
[`architecture.md`](architecture.md).

## Prerequisites

- Python 3.12 or newer on the DB host.
- `pgbackrest` on `PATH` (if you want backup data + log tailing).
- `journalctl` on `PATH` (if you want OS / OOM Killer events).
- A PostgreSQL `libpq` DSN the agent can use to read
  `pg_stat_archiver` (read-only login is enough — typically the
  `postgres` superuser via Unix socket).
- Outbound HTTPS reachability to the manager URL.
- The current manager **enrollment token** (`PCT_ENROLLMENT_TOKEN`).
- A planned cluster name (one already in PCT, or a new one — the
  agent will create it on first registration).

You do **not** need any inbound port open on the DB host.
Agents always initiate outbound calls.

## 1. Install the package

The agent is a single Python package, `pct-agent`, which provides a
console script of the same name.

For the local development checkout in this repo:

```bash
cd /opt
git clone https://github.com/yourorg/postgres_control_tower.git
cd postgres_control_tower
python3 -m venv /opt/pct-agent.venv
/opt/pct-agent.venv/bin/pip install -e ./agent
```

For a production install you would publish a wheel, then:

```bash
python3 -m venv /opt/pct-agent.venv
/opt/pct-agent.venv/bin/pip install pct-agent
```

Verify the binary is on the venv `PATH`:

```bash
/opt/pct-agent.venv/bin/pct-agent --version
```

## 2. Create directories and the config file

The agent expects two paths to exist and be writable by the user it
runs as (typically a dedicated `pct-agent` system user):

```bash
sudo useradd --system --home /var/lib/pct-agent --shell /usr/sbin/nologin pct-agent
sudo mkdir -p /etc/pct-agent /var/lib/pct-agent /var/lib/pct-agent/spool
sudo chown -R pct-agent:pct-agent /var/lib/pct-agent
sudo chmod 750 /var/lib/pct-agent
```

Drop a `/etc/pct-agent/config.yaml` (also chowned to the agent user):

```yaml
# /etc/pct-agent/config.yaml
manager_url: https://pct.internal
heartbeat_interval: 30

pg_dsn: "postgresql:///postgres?host=/var/run/postgresql"
wal_interval: 30

pgbackrest_bin: pgbackrest
pgbackrest_stanza: ""        # empty = all stanzas
pgbackrest_interval: 60

# Patroni REST API on this node. Empty disables the patroni collector;
# leave it empty on standalone hosts. On Patroni nodes, point this at
# the local node's REST endpoint (default port 8008):
patroni_rest_url: ""         # e.g. http://patroni-1:8008
patroni_interval: 30

pg_log_paths: /var/log/postgresql/postgresql-16-main.log
pgbackrest_log_paths: /var/log/pgbackrest/main-backup.log,/var/log/pgbackrest/main-archive-push.log
patroni_log_paths: ""        # leave empty on standalone hosts
etcd_log_paths: ""           # only on Patroni nodes; point at etcd's
                             # log file (or a shared volume in compose)

# OS source: journalctl is the production path; the /proc-based sampler
# below also runs and ships under source='os' so the UI is never empty.
# Set to 0 to disable on hosts where you only want journalctl events.
host_metrics_interval: 60

shipper_batch_size: 200
shipper_flush_interval: 5.0
spool_dir: /var/lib/pct-agent/spool

runner_long_poll_seconds: 25
runner_job_timeout_seconds: 21600
runner_stdout_tail_chars: 16000
```

Per-source path conventions are in [`log-sources.md`](log-sources.md).
Anything in this file can also be passed as an env var prefixed with
`PCT_AGENT_` (see `.env.example` at the repo root).

## 3. Register with the manager (one shot)

`pct-agent register` calls `POST /api/v1/agents/register` once,
receives a per-agent bearer token, and persists it to
`/var/lib/pct-agent/state.json` (chmod `0600`).

```bash
sudo -u pct-agent /opt/pct-agent.venv/bin/pct-agent register \
    --manager-url https://pct.internal \
    --enrollment-token "$(cat /etc/pct-agent/enrollment.token)" \
    --cluster-name order-db-prod \
    --cluster-kind standalone \
    --hostname "$(hostname --fqdn)"
```

You can also set `PCT_AGENT_ENROLLMENT_TOKEN`,
`PCT_AGENT_CLUSTER_NAME`, etc. and skip the flags.

**Expected output:**

```text
Registering with https://pct.internal/api/v1/agents/register as host=db01.prod cluster=order-db-prod...
Registered. agent_id=7; token persisted to /var/lib/pct-agent/state.json
```

The token is now stored. Re-running `register` will issue a fresh
token and overwrite the file (this is how you rotate — see
[`troubleshooting.md`](troubleshooting.md)).

## 4. Run the agent

For a quick smoke test you can run in the foreground:

```bash
sudo -u pct-agent /opt/pct-agent.venv/bin/pct-agent run
```

You should see, in order:

1. Heartbeat loop logs (every 30s).
2. WAL collector logs (if `pg_dsn` is set).
3. pgBackRest collector logs (every 60s).
4. Patroni collector logs (only if `patroni_rest_url` is set;
   otherwise a single "disabled" line and silence afterwards).
5. Tailers attaching to each configured log file.
6. Job runner attaching ("Job runner started. long_poll=25s …").

For a real install, drop a systemd unit:

```ini
# /etc/systemd/system/pct-agent.service
[Unit]
Description=Postgres Control Tower agent
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=pct-agent
Group=pct-agent
EnvironmentFile=-/etc/pct-agent/env
ExecStart=/opt/pct-agent.venv/bin/pct-agent run
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/pct-agent
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Reload, enable, start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pct-agent
sudo systemctl status pct-agent
```

## 5. Verify in the UI

Open the manager UI and:

1. Go to **Dashboard**. Within ~30s, the agent count goes up by one
   and the cluster shows `last_seen_at` within the last minute.
2. Go to **Cluster → order-db-prod**. Within ~60s the pgBackRest
   panel populates from `pgbackrest --output=json info`.
3. Go to **Logs**. Your hostname appears in the agent filter once
   the first batch of log lines is shipped (depends on log volume —
   usually within a minute).

## Troubleshooting

If something is off, the order of operations is usually:

1. Is the agent process running? `systemctl status pct-agent`
2. Can it reach the manager? `curl -fsS https://pct.internal/healthz`
3. Did registration succeed? `cat /var/lib/pct-agent/state.json`
   (this file should contain `agent_id` and `agent_token`).
4. Is the bearer token valid? Heartbeat 401s mean the token was
   rotated server-side; just re-run `pct-agent register`.

For each of the standard failure modes — clock skew, missing
journalctl perms, state file owned by the wrong user, agent showing
`unknown` role — the runbook lives in
[`troubleshooting.md`](troubleshooting.md).

## Related

- [`log-sources.md`](log-sources.md) — paths, journald units, and how
  to add a new source.
- [`architecture.md`](architecture.md) — what each collector does.
- [`safety-and-rbac.md`](safety-and-rbac.md) — what jobs the agent
  will and will not run.
- [`PLAN.md` §6](../PLAN.md#6-components--responsibilities) — the
  authoritative agent component list.
