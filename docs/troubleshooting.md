# Troubleshooting

A symptom-first runbook for the most common failures. Each section
follows the same shape:

1. What the operator sees in the UI / logs.
2. Most likely cause.
3. The actual fix, with the commands.

If your problem isn't here, check the manager logs first
(`journalctl -u pct-manager -e`), then the agent logs
(`journalctl -u pct-agent -e`), then the agent's local diagnostic
endpoint at `http://127.0.0.1:8081/healthz`.

## Symptom: the agent never appears in the UI

**You see**

- The cluster you registered against does not show the new agent
  count, even after a minute.
- `pct-agent register` printed `Registered. agent_id=...` but the
  Cluster page still says 0 agents.

**Most likely cause**

`pct-agent register` succeeded (it created the row) but the **run**
process that sends heartbeats is not running, or its bearer token is
no longer valid (e.g. you re-registered, then started an old `run`).

**Fix**

```bash
sudo systemctl status pct-agent
sudo journalctl -u pct-agent -e --no-pager | tail -50
```

Look for `401 Unauthorized` on heartbeats. If you see them:

```bash
sudo systemctl stop pct-agent
sudo -u pct-agent /opt/pct-agent.venv/bin/pct-agent register \
    --manager-url https://pct.internal \
    --enrollment-token "$(cat /etc/pct-agent/enrollment.token)" \
    --cluster-name <name> --cluster-kind <kind> \
    --hostname "$(hostname --fqdn)"
sudo systemctl start pct-agent
```

If you see no heartbeat attempts at all, check `pct-agent run`
crashed at startup with a config error (typically a malformed
`/etc/pct-agent/config.yaml`).

## Symptom: agent shows up but `last_seen_at` is stale

**You see**

- The Dashboard shows the agent in red ("stale").
- Cluster page shows `last_seen_at` more than 2 minutes old.

**Most likely cause**

Network reachability to the manager dropped, or the manager DB is
overloaded so heartbeats time out.

**Fix**

On the agent host:

```bash
curl -fsS https://pct.internal/healthz
```

- Connection refused / timeout → fix the network. The agent will
  catch up via its on-disk spool once reachability returns
  (`/var/lib/pct-agent/spool` should grow during the outage).
- 5xx → check manager logs: it's likely a DB problem, not an agent
  problem.
- 200 → the agent process is the issue. `systemctl restart
  pct-agent` and tail logs.

## Symptom: clock drift alert fires constantly

**You see**

A `clock_drift` alert with `payload.clock_skew_ms > 2000` for one or
more agents.

**Most likely cause**

The agent host doesn't have NTP running, or the host clock has been
manually adjusted backwards.

**Fix**

```bash
timedatectl status
# Want: "System clock synchronized: yes" and an active NTP service.
sudo systemctl enable --now systemd-timesyncd      # or chronyd
```

Force a manual resync if needed:

```bash
sudo systemctl restart systemd-timesyncd
sudo timedatectl set-ntp true
```

The alert will resolve on the next evaluation cycle once skew goes
back under 2000 ms (the threshold is hardcoded in
`alerter/rules.py::rule_clock_drift` — don't tune it per-host, fix
the host).

Why we care: every `LogRecord` is timestamped at the agent. If the
agent clock is off by 30s, the Logs UI's multi-host chronological
view is wrong by 30s. The Surgeon view is the whole reason this
project exists, and it's only useful if `ts_utc` is true UTC.

## Symptom: `journalctl` lines are missing from the OS source

**You see**

OOM Killer events and I/O errors are in `journalctl` on the host
but never show up in the Logs UI, source = `os`.

**Most likely cause**

The `pct-agent` system user does not have permission to read the
journal.

**Fix**

```bash
sudo usermod -aG systemd-journal pct-agent
sudo systemctl restart pct-agent
```

Verify:

```bash
sudo -u pct-agent journalctl -n 5 --no-pager
```

If that prints recent lines, the agent will too.

If `journalctl` isn't installed at all (minimal container), the OS
collector falls back to tailing `PCT_AGENT_OS_LOG_PATHS`. Set that
to e.g. `/var/log/messages` if your image uses old-school syslog.
See [`log-sources.md`](log-sources.md#5-os--journald) for the full
matrix.

## Symptom: `state.json` exists but the agent is unauthorized

**You see**

- `pct-agent run` logs `401 Unauthorized` on every heartbeat.
- `cat /var/lib/pct-agent/state.json` looks well-formed and recent.

**Most likely cause**

Someone re-ran `pct-agent register` from a different machine using
the same hostname, which rotated the token server-side and orphaned
this agent.

**Fix**

Just register again on this host:

```bash
sudo systemctl stop pct-agent
sudo -u pct-agent /opt/pct-agent.venv/bin/pct-agent register \
    --manager-url https://pct.internal \
    --enrollment-token "$(cat /etc/pct-agent/enrollment.token)" \
    --cluster-name <same name> --cluster-kind <same kind> \
    --hostname "$(hostname --fqdn)"
sudo systemctl start pct-agent
```

The manager keys agents on `(cluster_id, hostname)` and updates the
`token_hash` on re-registration, so this is the supported way to
rotate.

If you actually wanted **two** agents on what registers as the same
hostname (e.g. two containers that both report `db01.prod`), give
each a distinct `--hostname`.

## Symptom: a backup job is "running" forever

**You see**

`pct.jobs.status = 'running'`, `started_at` was hours ago, but the
agent's process tree shows no `pgbackrest` running.

**Most likely cause**

The agent died (or was restarted) mid-job. v1 does not
auto-reclaim — once a job is `running`, only the original agent will
report on it.

**Fix**

There is no UI gesture for this in v1 (it's a hardening item — see
[`hardening.md`](hardening.md)). Mark it failed in SQL:

```sql
UPDATE pct.jobs
SET status = 'failed',
    finished_at = now(),
    exit_code = 137,
    stdout_tail = 'manually marked failed: agent restarted mid-job'
WHERE id = <job_id> AND status = 'running';
```

Then re-submit the job from the UI. The pgBackRest log tailer will
have captured whatever progress did happen in `logs.events`, so you
have the forensics.

## Symptom: a job is rejected with `kind not in agent allowlist`

**You see**

`pct.jobs.exit_code = 126`, `stdout_tail` says "Agent refused to
run job kind=... not in agent allowlist (...)".

**Most likely cause**

Someone added a new job kind on the manager side
(`schemas.JOB_KINDS`) but did not mirror it in
`agent/pct_agent/runner.py::ALLOWED_KINDS`.

**Fix**

This is a deployment sync bug. Edit both files together (and update
[`safety-and-rbac.md`](safety-and-rbac.md)) and redeploy the agent.

The allowlist mismatch is not a security failure — it's defense in
depth catching a configuration drift. Don't "fix" it by removing the
agent-side check.

If the kind was `restore` or `stanza_delete` and you didn't expect
to see it, treat it as a possible compromise of the manager image
and audit accordingly. v1 explicitly does not support those.

## Symptom: alerts work but no notification ever arrives

**You see**

- `pct.alerts` rows have `last_notified_at` set.
- No Slack message; no email.

**Most likely cause**

The notifier swallows its own errors so a Slack outage doesn't take
down the rule engine. The error is in the manager logs, not the UI.

**Fix**

```bash
sudo journalctl -u pct-manager -e --no-pager | grep -iE 'slack|smtp|notifier'
```

Common findings:

- `403` from Slack → wrong webhook URL or webhook revoked.
- `connection refused` on SMTP → wrong port, or local firewall
  blocking egress on 587.
- `SMTP authentication failed` → app password not enabled on the
  sending account (Gmail, etc.).

To force a re-notify of an existing open alert (e.g. you fixed
SMTP and want to confirm), bump the renotify window in the env and
restart the manager:

```bash
PCT_ALERT_RENOTIFY_SECONDS=60
sudo systemctl restart pct-manager
```

(Restore the value once you've confirmed delivery.)

## Symptom: log retention is eating disk faster than expected

**You see**

`logs.events_*` partitions on the manager DB total well above the
size you sized for in [`deployment.md`](deployment.md).

**Most likely cause**

A specific Postgres has `log_min_messages = debug1` (or a tight loop
in an application is flooding the log).

**Fix**

Find the loud agent:

```sql
SELECT agent_id, source, count(*)
FROM logs.events
WHERE ts_utc > now() - interval '1 hour'
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 20;
```

If one agent / one source dominates, fix it at the source (`ALTER
SYSTEM SET log_min_messages = 'warning'; SELECT
pg_reload_conf();`). Do not lower retention as the first reaction —
that punishes every cluster for one cluster's noise.

If you really do need more headroom now, lower
`PCT_LOG_RETENTION_DAYS` and restart; the next nightly pass will
drop expired partitions cleanly.

## Symptom: storage runway forecast is missing or wildly wrong

**You see**

The Cluster page's "Storage runway" card is empty or shows
"days_to_target = 0.5" for a cluster that's clearly fine.

**Most likely cause**

- Empty card: fewer than two `pgbackrest_info` snapshots exist for
  this cluster. The forecast needs samples — give it at least two
  cycles (~2 minutes) after the agent attaches.
- Wildly low number: a recent backup expire shrunk the repo and the
  short window saw a fake "we'll hit zero" trend, OR a pending
  backup just doubled the repo size and the forecast extrapolated
  it.

**Fix**

Both clear up naturally as the rolling window
(`PCT_FORECAST_WINDOW_DAYS=7` by default) accumulates more samples.
If you want to see the underlying numbers:

```sql
SELECT captured_at,
       (payload->0->'repo'->>'size')::bigint AS bytes
FROM pct.pgbackrest_info
WHERE agent_id IN (SELECT id FROM pct.agents WHERE cluster_id = <id>)
ORDER BY captured_at DESC LIMIT 50;
```

The forecast code is in
[`alerter/forecast.py`](../manager/pct_manager/alerter/forecast.py); if the
output looks wrong consistently, that's where to look.

## Symptom: UI shows blank pages after a deploy

**You see**

- Login works.
- After login, the Dashboard / Cluster / Logs pages render an empty
  shell.

**Most likely cause**

Browser is holding a cached `index.html` that points at hashed asset
filenames from the previous deploy.

**Fix**

Hard refresh (`Cmd-Shift-R` / `Ctrl-Shift-R`). If that fixes it,
configure your reverse proxy to send `Cache-Control: no-store` for
`/index.html` (the Vite assets under `/assets/` are content-hashed
and safe to cache for a year).

Caddy:

```caddy
@spa_index path /index.html /
header @spa_index Cache-Control "no-store"
```

## Symptom: PITR target picked from the UI runs an hour off

**You see**

Restore (when v2 lands) ran to a target one hour off from what you
clicked in the calendar.

**Most likely cause**

The calendar widget is using local time but the cluster's
`postgresql.conf` has `timezone = 'UTC'` (or vice versa).

**Fix**

This is the entire reason [`safety-and-rbac.md`](safety-and-rbac.md#the-v2-confirmation-modal-preview)
mandates rendering the target in UTC with the local TZ offset shown
inline. Until restore actually exists, we can only document this
preemptively — when it lands, the modal must enforce UTC.

If your team needs to do a PITR right now in v1, run `pgbackrest
restore` directly on the host, set `recovery_target_time` in
**UTC** explicitly with the `Z` suffix, and verify the cluster came
up at the expected LSN before pointing applications at it.

## Related

- [`agent-setup.md`](agent-setup.md) — install / register an agent.
- [`deployment.md`](deployment.md) — production checklist that
  prevents most of these issues.
- [`log-sources.md`](log-sources.md) — per-source paths and journald
  permission requirements.
- [`safety-and-rbac.md`](safety-and-rbac.md) — what jobs are
  expected to be refused.
