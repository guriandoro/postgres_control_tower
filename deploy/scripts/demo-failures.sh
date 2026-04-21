#!/usr/bin/env bash
# Trigger one of four failure scenarios in the demo so the operator can
# watch PCT detect, alert, and (where applicable) heal.
#
# Usage:
#   ./deploy/scripts/demo-failures.sh wal_lag
#   ./deploy/scripts/demo-failures.sh failover
#   ./deploy/scripts/demo-failures.sh backup_fail
#   ./deploy/scripts/demo-failures.sh clock_drift
#   ./deploy/scripts/demo-failures.sh restore_attempt
#   ./deploy/scripts/demo-failures.sh restore   (alias)
#
# Each scenario prints what to look at in the UI after it runs.

set -euo pipefail
cd "$(dirname "$0")/../compose"
SCN="${1:-help}"

# ---------- shared helpers ----------

API="http://localhost:8080"
ENV_FILE="$(pwd)/.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
fi
ADMIN_EMAIL="${PCT_BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${PCT_BOOTSTRAP_ADMIN_PASSWORD:-admin}"

login_token() {
    curl -fsS -X POST "$API/api/v1/auth/login" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "username=$ADMIN_EMAIL" \
        --data-urlencode "password=$ADMIN_PASSWORD" \
        | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}

# ---------- scenarios ----------

case "$SCN" in
    wal_lag)
        echo "[demo:wal_lag] Breaking archive_command on pg-standalone..."
        # Point archive_command at /bin/false so every WAL segment fails.
        # archive_lag_seconds will climb past the 15m alert threshold.
        docker compose exec -T pg-standalone \
            psql -U postgres -c "ALTER SYSTEM SET archive_command = '/bin/false';"
        docker compose exec -T pg-standalone \
            psql -U postgres -c "SELECT pg_reload_conf();"
        # Force several WAL switches so something needs to be archived now.
        docker compose exec -T pg-standalone \
            psql -U postgres -c "SELECT pg_switch_wal();"
        cat <<EOF

  Wait ~15 minutes (or shorten WAL_LAG_THRESHOLD_SECONDS in
  manager/pct_manager/alerter/rules.py for a quicker demo) and watch:

    UI -> Alerts:        new 'wal_lag' alert appears
    UI -> Cluster page:  WAL lag chart climbs

  To recover:
    docker compose exec pg-standalone \\
      psql -U postgres -c "ALTER SYSTEM RESET archive_command;"
    docker compose exec pg-standalone \\
      psql -U postgres -c "SELECT pg_reload_conf();"
EOF
        ;;

    failover)
        echo "[demo:failover] Stopping patroni-1 to force failover..."
        docker compose stop patroni-1
        cat <<EOF

  Patroni's leader race will promote patroni-2 within ~30s.
  Watch:
    UI -> Cluster ha-demo:  primary swaps from patroni-1 -> patroni-2
    UI -> Logs:             role transition rows ('promoted' / 'demoted')

  Restart the old node when you're ready:
    docker compose start patroni-1
EOF
        ;;

    backup_fail)
        echo "[demo:backup_fail] Queuing a backup against a broken stanza..."
        TOKEN=$(login_token)
        if [ -z "$TOKEN" ]; then echo "Login failed"; exit 1; fi
        # Make pgbackrest fail by removing repo write access for one moment;
        # easier: queue a job for an invalid stanza name. The job runner
        # forwards stdin and the agent reports exit_code != 0.
        STANDALONE_ID=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API/api/v1/clusters" \
            | python3 -c "import json,sys
for c in json.load(sys.stdin):
    if c['name']=='standalone': print(c['id']); break")
        curl -fsS -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -X POST "$API/api/v1/jobs" \
            -d "$(printf '{"cluster_id":%d,"kind":"backup_full","params":{"stanza":"does-not-exist"}}' "$STANDALONE_ID")" \
            >/dev/null
        cat <<EOF

  Watch:
    UI -> Jobs:    new 'backup_full' job, fails within ~30s
    UI -> Alerts:  'backup_failed' alert opens after the next eval pass
EOF
        ;;

    clock_drift)
        echo "[demo:clock_drift] Setting agent container clocks 10s in the past..."
        # The agent reports clock_skew_ms on each heartbeat; manager logs
        # the delta vs its own NOW(). Setting host clock backwards inside
        # one container reproduces clock_drift > 2000ms quickly.
        for svc in pct-agent-standalone pct-agent-patroni-1; do
            docker compose exec -T --user root "$svc" \
                date -u -s "@$(($(date -u +%s) - 10))" || true
        done
        cat <<EOF

  Watch:
    UI -> Alerts:  'clock_drift' alert opens after next eval (~60s)

  Note: the agent container's clock will eventually re-sync via the host;
  the alert will resolve on its own.
EOF
        ;;

    restore|restore_attempt)
        echo "[demo:restore_attempt] Trying to queue a 'restore' job..."
        TOKEN=$(login_token)
        if [ -z "$TOKEN" ]; then echo "Login failed"; exit 1; fi
        STANDALONE_ID=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API/api/v1/clusters" \
            | python3 -c "import json,sys
for c in json.load(sys.stdin):
    if c['name']=='standalone': print(c['id']); break")
        # Manager allowlist rejects anything not in JOB_KINDS — this MUST
        # 422. The point of the scenario is to demonstrate it.
        echo
        echo "Manager response (expecting HTTP 422):"
        curl -isS -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -X POST "$API/api/v1/jobs" \
            -d "$(printf '{"cluster_id":%d,"kind":"restore","params":{}}' "$STANDALONE_ID")" \
            | sed -n '1,15p'
        cat <<EOF

  -> The manager schema rejects 'restore' before it ever reaches an agent.
     Even if the manager were compromised, agent/pct_agent/runner.py
     keeps its own ALLOWED_KINDS allowlist (defense in depth).
EOF
        ;;

    help|*)
        cat <<EOF
Usage: $0 <scenario>

Scenarios:
  wal_lag          Break archive_command on pg-standalone -> wal_lag alert
  failover         Stop patroni-1 -> Patroni promotes patroni-2
  backup_fail      Queue a backup with a bad stanza -> backup_failed alert
  clock_drift     Skew an agent's clock by 10s -> clock_drift alert
  restore          Try to queue a 'restore' job -> manager rejects (422)
EOF
        exit 1
        ;;
esac
